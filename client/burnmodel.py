"""The burn curve: observed utilization over a fixed window, plus the projection.

Both usage feeds report the same two numbers — "you are N% through this window,
which resets at T" — and nothing about how you got there. The shape of the chart
therefore has to be *observed*: something has to write down what it saw, when.
This module is that bookkeeping, shared by `claudeusage` and `codexusage` so the
two screens can't drift apart in how they read their own history.

Two sample sources feed the same maths:

  * `history_samples()` — the live-API case. Each call appends the current
    utilization to a per-window file, so past bars are frozen observations, not
    a reconstruction. Stretches where nobody was running are filled by linear
    interpolation between the surrounding samples.
  * a caller-supplied list — the case where the feed already carries its own
    dated history (Codex's session logs).

`curve()` then turns samples into bars, a recent slope and the hours until that
slope reaches 100%.
"""
import datetime as dt
import json
import math
import os
from dataclasses import dataclass

HISTORY_MIN_GAP = 25.0     # seconds between kept samples (bounds the file size)

# Burn-rate lookback: start this short so a burst shows up almost at once, and
# only reach further back when utilization has not moved enough to measure.
SLOPE_FLOOR_MIN = 10
SLOPE_STEP_MIN = 5
SLOPE_CAP_MIN = 60


@dataclass
class BurnModel:
    """What a usage screen draws: one window's observed curve and its projection.

    Both feeds produce this. It carries the **verdict** (`state`) as well as the
    numbers, because that verdict is the single thing the view branches on — if
    each feed spelled it out for itself, one of them could be changed and the
    two screens would silently disagree about what "danger" means. Feeds add
    their own fields by subclassing; nothing here may be overridden except
    `live`, which is the one thing only a feed knows.
    """
    util: float              # current utilization %  (0..100)
    reset_h: float           # hours until the window resets
    elapsed_h: float         # hours elapsed in the window
    window_h: float          # window length in hours
    bin_min: float           # bin width in minutes
    cum_pct: list            # cumulative % per bin
    now_bin: int             # index of the current bin
    slope: float             # recent burn slope, % per hour
    hits_in_h: float         # hours until the projection reaches 100% (inf = safe)
    proj_h: float            # elapsed_h + hits_in_h (inf = never before reset)
    delta: float             # last bin's % increment (the burn rate readout)
    easing: bool             # burn decelerating vs the prior bin
    weekly: float            # weekly-window utilization %  (0..100)
    start_dt: dt.datetime    # instant this window last reset (x-axis start)
    now_dt: dt.datetime      # current instant
    end_dt: dt.datetime      # instant of the next reset (x-axis END)

    @property
    def state(self):
        """The screen's single verdict, so model and view can't drift:
        'locked' (used >= limit), 'danger' (projection hits 100% before the
        window resets), or 'ok'."""
        if self.util >= 100.0:
            return "locked"
        if self.proj_h <= self.window_h:
            return "danger"
        return "ok"

    @property
    def safe(self):
        return self.state == "ok"

    @property
    def live(self):
        """The reading is current enough for a trend to mean anything. True for
        a feed that polls an API; a feed whose data arrives in bursts overrides."""
        return True

    @property
    def out_dt(self):
        """Projected instant the limit is hit (None if never / no burn)."""
        if self.hits_in_h == math.inf:
            return None
        return self.now_dt + dt.timedelta(hours=self.hits_in_h)


def build(cls, util, weekly, start_dt, end_dt, window_h, now, samples, nbins, **extra):
    """Assemble a feed's BurnModel subclass from its two numbers and its samples.

    The window arithmetic is identical for every feed, so it lives here rather
    than being re-derived (and re-rounded) per source.
    """
    c = curve(samples, util, start_dt, window_h, now, nbins)
    return cls(util=util, reset_h=max(0.0, (end_dt - now).total_seconds() / 3600.0),
               elapsed_h=(now - start_dt).total_seconds() / 3600.0, window_h=window_h,
               bin_min=c.bin_min, cum_pct=c.cum_pct, now_bin=c.now_bin, slope=c.slope,
               hits_in_h=c.hits_in_h, proj_h=c.proj_h, delta=c.delta, easing=c.easing,
               weekly=weekly, start_dt=start_dt, now_dt=now, end_dt=end_dt, **extra)


@dataclass
class Curve:
    """The drawable part of a burn model."""
    cum_pct: list            # cumulative % per bin
    now_bin: int             # index of the current bin
    bin_min: float           # bin width in minutes
    slope: float             # recent burn slope, % per hour
    hits_in_h: float         # hours until the projection reaches 100% (inf = safe)
    proj_h: float            # elapsed_h + hits_in_h (inf = never before reset)
    delta: float             # last bin's % increment (the burn rate readout)
    easing: bool             # burn decelerating vs the prior bin


def load_history(path, start_dt):
    """(canonical_start, samples) for this window — the stored window_start wins
    while it matches (±120s), pinning the bin grid: an API's `resets_at` can
    jitter by a second per call, and letting that shift start_dt would wobble
    every interpolated bar. A larger mismatch means a new window: fresh start,
    no samples."""
    try:
        with open(path) as f:
            d = json.load(f)
        ws = dt.datetime.fromisoformat(d["window_start"])
        if abs((ws - start_dt).total_seconds()) > 120:
            return start_dt, []
        return ws, [(dt.datetime.fromisoformat(t), float(u)) for t, u in d["samples"]]
    except Exception:
        return start_dt, []


def save_history(path, start_dt, samples):
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"window_start": start_dt.isoformat(),
                   "samples": [[t.isoformat(), round(u, 2)] for t, u in samples]}, f)
    os.replace(tmp, path)


def history_samples(path, end_dt, window_h, now, util):
    """Record this reading and return (canonical_start, samples-through-now).

    A reading is committed at most every HISTORY_MIN_GAP, and the tip's
    timestamp is never rewritten: moving it forward would restart the gap on
    every poll, so the history could never grow past one sample and every past
    bar would be redrawn as a straight line from the window start to the live
    utilization.
    """
    start_dt, samples = load_history(path, end_dt - dt.timedelta(hours=window_h))
    hist = [s for s in samples if start_dt <= s[0] <= now]
    if not hist or (now - hist[-1][0]).total_seconds() >= HISTORY_MIN_GAP:
        hist.append((now, util))
        save_history(path, start_dt, hist)
    return start_dt, (hist if hist[-1][0] == now else hist + [(now, util)])


def curve(samples, util, start_dt, window_h, now, nbins):
    """Bars, slope and projection from dated (time, utilization) observations.

    `samples` must be sorted and lie inside the window; `util` is the live
    reading, which always wins for the current bar.
    """
    elapsed_h = (now - start_dt).total_seconds() / 3600.0

    def util_at(t):
        """Observed util at instant t: 0 at the window start, linear between
        samples, flat at the last sample after it."""
        if t <= start_dt:
            return 0.0
        pt, pu = start_dt, 0.0
        for st, su in samples:
            if st >= t:
                span = (st - pt).total_seconds()
                f = (t - pt).total_seconds() / span if span > 0 else 1.0
                return pu + (su - pu) * f
            pt, pu = st, su
        return pu

    bin_min = window_h * 60.0 / nbins
    now_bin = min(nbins - 1, max(0, int(elapsed_h * 60 // bin_min)))
    cum_pct = [util_at(min(start_dt + dt.timedelta(minutes=(k + 1) * bin_min), now))
               for k in range(nbins)]
    cum_pct[now_bin] = util                      # the NOW bar is the live reading

    # Recent burn rate, read over the shortest lookback that can actually see a
    # change. Both feeds report utilization in whole percent, so a short fixed
    # window resolves rates only in coarse steps and reads as "no burn" most of
    # the time; growing the window until a full step appears keeps the response
    # near the floor while you are burning hard and only reaches further back
    # when the signal is too small to measure. Projected to the limit from there.
    slope = 0.0
    for lb_min in range(SLOPE_FLOOR_MIN, SLOPE_CAP_MIN + 1, SLOPE_STEP_MIN):
        anchor_t = max(start_dt, now - dt.timedelta(minutes=lb_min))
        anchor_hrs = (now - anchor_t).total_seconds() / 3600.0
        if anchor_hrs <= 0.01:
            continue
        moved = util - util_at(anchor_t)
        slope = max(0.0, moved / anchor_hrs)
        if moved >= 1.0 or anchor_t == start_dt:
            break
    hits_in = (100.0 - util) / slope if slope > 1e-6 else math.inf
    proj = elapsed_h + hits_in if hits_in != math.inf else math.inf
    binh = bin_min / 60.0
    d1 = util - util_at(now - dt.timedelta(hours=binh))
    d0 = util_at(now - dt.timedelta(hours=binh)) - util_at(now - dt.timedelta(hours=2 * binh))
    return Curve(cum_pct=cum_pct, now_bin=now_bin, bin_min=bin_min, slope=slope,
                 hits_in_h=hits_in, proj_h=proj, delta=d1, easing=d1 <= d0)
