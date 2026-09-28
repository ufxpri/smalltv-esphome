"""The burn-monitor screen: one layout, one palette slot, many data sources.

`stream_claude` and `stream_codex` are the same screen — a burn histogram of
cumulative usage over the fixed reset-to-reset window, the two deadlines racing
below it, a mascot box and a weekly segment bar. Only three things differ per
source: the **palette** (a `Theme`), the **mascot** drawn in the box, and the
**data** (a model with the fields `render` reads). Everything here is shared, so
a change to the layout lands on both screens at once.

What a source provides:
  * a `Theme` — nine colors plus the header title and the spark palette.
  * a model shaped like `claudeusage.BurnModel` / `codexusage.BurnModel`.
  * a `mascot(d, t)` callable that fills GBOX when no GIF is picked.
  * the header badge (its trend/stale readout), since only the source knows what
    "no fresh data" means for its own feed.

Keep it light: `Streamer.push` re-renders nothing, but the device's fractional
framebuffer means every extra millisecond here is paid at FPS.
"""
import datetime as dt
import json
import logging
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

import config as cfg_mod
import stream as stream_mod
from smalltv_stream import PORT, SS, H, TELEM_DIR, W, Streamer, resolve_host

# (regular, bold) mono font per platform — first pair whose regular exists wins.
# A None bold means the regular is a .ttc whose index 1 is the bold face (Menlo).
_MONO_CANDIDATES = [
    ("/System/Library/Fonts/Menlo.ttc", None),                       # macOS
    ("C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/consolab.ttf"),  # Windows
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"),    # Linux
]
MONO, MONO_BOLD = next(((r, b) for r, b in _MONO_CANDIDATES if os.path.exists(r)),
                       (None, None))
_fc = {}


def font(px, bold=False):
    key = (px, bold)
    if key not in _fc:
        try:
            if MONO_BOLD:
                _fc[key] = ImageFont.truetype(MONO_BOLD if bold else MONO, int(px * SS))
            else:
                _fc[key] = ImageFont.truetype(MONO, int(px * SS), index=1 if bold else 0)
        except Exception:
            # last resort: PIL's bitmap font, at least at the right size
            _fc[key] = ImageFont.load_default(int(px * SS))
    return _fc[key]


def px(v):
    """A canvas coordinate at the supersampled scale. Public: the mascots each
    source draws are on this grid too."""
    return int(round(v * SS))


def _lerp(a, b, f):
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))


def hhmm(hours):
    """Hours as h:mm, or --:-- for "never". Public: sources format their own
    ages and countdowns with it."""
    if hours is None or hours == math.inf:
        return "--:--"
    m = int(round(hours * 60))
    return f"{m // 60}:{m % 60:02d}"


IMMINENT_H = 10 / 60.0    # under ten minutes a countdown switches to mm:ss


def _countdown(hours):
    """h:mm, or mm:ss once under IMMINENT_H — the seconds are the part still
    moving by then. Callers recolor it, which is also what tells the two units
    apart (both render as N:NN)."""
    if hours >= IMMINENT_H:
        return hhmm(hours)
    s = max(0, int(hours * 3600))
    return f"{s // 60}:{s % 60:02d}"


def _dash(d, x0, y0, x1, y1, color, w=1, dash=5, gap=4):
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 1:
        return
    dx, dy = (x1 - x0) / L, (y1 - y0) / L
    t = 0.0
    while t < L:
        a, b = t, min(t + dash, L)
        d.line([px(x0 + dx * a), px(y0 + dy * a), px(x0 + dx * b), px(y0 + dy * b)],
               fill=color, width=px(w))
        t += dash + gap


# ---- palette ----

@dataclass
class Theme:
    """A screen's nine colors, its header word, and its spark palette.

    The names are roles, not hues, so the drawing code never has to know which
    source it is rendering: FG is "a measured value", USE is "usage", OK/WARN/CRIT
    are the three verdicts, TRACK/DIM are furniture.
    """
    title: str            # header word ("burn", "codex")
    bg: tuple
    fg: tuple             # just-now bar / big values
    use: tuple            # usage bars, the screen's accent
    ok: tuple             # conclusion: safe
    warn: tuple           # conclusion: pace exceeded
    crit: tuple           # conclusion: limit reached
    gray: tuple           # muted labels
    track: tuple          # window track / axes
    dim: tuple            # empty weekly blocks
    sparks: tuple = ()    # burst colors; defaults to (white, fg, use, warn)

    def __post_init__(self):
        if not self.sparks:
            self.sparks = ((255, 255, 255), self.fg, self.use, self.warn)

    def proj(self, pct):
        """Projected bars warm from the usage accent toward WARN and then CRIT as
        they approach the limit, so a run that ends badly looks wrong before you
        read it."""
        if pct <= 50:
            return self.use
        if pct <= 85:
            return _lerp(self.use, self.warn, (pct - 50) / 35.0)
        return _lerp(self.warn, self.crit, min(1.0, (pct - 85) / 15.0))


# ---- optional looping GIF in the lower-right box ----

class Gif:
    """A GIF decoded once into RGB frames, advanced by wall-clock time."""
    def __init__(self, path):
        self.frames, self.durs, self.total = [], [], 0.0
        im = Image.open(path)
        try:
            while True:
                self.frames.append(im.convert("RGB").copy())
                self.durs.append(max(im.info.get("duration", 100) / 1000.0, 0.03))
                im.seek(im.tell() + 1)
        except EOFError:
            pass
        self.total = sum(self.durs) or 1.0

    def frame(self, t):
        t %= self.total
        for fr, du in zip(self.frames, self.durs):
            if t < du:
                return fr
            t -= du
        return self.frames[-1]


@dataclass
class GifPick:
    """The GIF the panel picked for one screen, watched by config key.

    The config is re-read at most every 2 s and the GIF is only re-decoded when
    the name changes — so calling `get()` every render frame is cheap and still
    picks up a panel change within a couple of seconds.
    """
    key: str                          # config key ("claude_gif" / "codex_gif")
    _ts: float = 0.0
    _name: str = field(default=None)
    _gif: Gif = None

    def get(self):
        now = time.time()
        if now - self._ts < 2.0:
            return self._gif
        self._ts = now
        name = (cfg_mod.load().get(self.key) or "").strip()
        if name != self._name:
            self._name = name
            path = os.path.join(stream_mod.gif_dir(), name) if name else ""
            gif = None
            if path and os.path.exists(path):
                try:
                    gif = Gif(path)
                except Exception:
                    gif = None
            self._gif = gif
        return self._gif


# chart + layout geometry (final 240x240 units). X0 leaves a left margin for
# the y-axis, which carries the used-% readout at its own height.
X0, X1 = 33, 230
TOP, BASE = 46, 150
GBOX = (172, 164, 230, 208)   # gif / mascot box (x0,y0,x1,y1) — right-side accent


def box(d, x0, y0, x1, y1, fill):
    d.rectangle([px(x0), px(y0), px(x1), px(y1)], fill=fill)


def _ghost_box(d, x0, y0, x1, y1, color):
    """An unfilled bar outlined in dashes — a projection, not a measurement.
    The baseline is left open; the chart's own axis already draws it."""
    _dash(d, x0, y0, x1, y0, color, 1, 3, 3)
    for x in (x0, x1):
        _dash(d, x, y0, x, y1, color, 1, 3, 3)


# ---- burst particles ----

class BurstFX:
    """Sparks over the NOW bar for as long as usage is climbing — an activity
    light, not a firework. Each poll reports how much was gained, and that sets
    an emission rate that holds until the next poll replaces it. Because the
    reading is stamped and expires after TTL, "no more polls" means the sparks
    stop on their own: a failed fetch, a wedged fetcher, or a genuinely idle
    model all read as calm without anyone having to switch them off.

    So a 1% gain — the smallest either feed can express, since both report whole
    percent — is a full poll interval of visible sparks rather than a blink, and
    heavier use shows up as a denser stream over the same span.

    Thread boundary: set_burn() is called from the fetcher thread, particles()
    only from the render loop. Each writes its own attributes and the reads are
    single-slot, so no lock is needed — keep it that way if you add methods.
    """
    LIFE = 0.6            # seconds a spark flies
    GRAVITY = 300.0       # px/s^2 for the parabolic arcs
    BASE_RATE = 2.0       # sparks/s at the faintest measurable burn
    RATE_PER_PCT = 1.5    # extra sparks/s per point gained
    MAX_RATE = 40.0
    MANUAL_DELTA = 12     # the panel's test button pretends this much was gained

    def __init__(self, signal_path, ttl):
        self.TTL = ttl          # a reading outlives its poll a little, so back-to-back
                                # busy polls emit seamlessly instead of flickering
        self._rate = 0.0        # sparks/s from the newest reading
        self._stamp = 0.0       # when that reading was taken (staleness clock)
        self._parts = []        # sparks in flight: (spawn, vx, vy, size, color index)
        self._pending = 0.0     # fractional spark carried between frames
        self._last = 0.0
        self._signal_path = signal_path
        self._sig_n = self._read_signal()   # baseline, so a press fires at once
        self._sig_ts = 0.0

    def set_burn(self, delta, now=None):
        """Report the utilization gained since the previous poll. Any rise at all
        emits; no rise (or a window reset, which drops util) goes quiet."""
        self._rate = (min(self.BASE_RATE + self.RATE_PER_PCT * delta, self.MAX_RATE)
                      if delta > 0 else 0.0)
        self._stamp = time.time() if now is None else now

    def particles(self, now):
        """[(vx, vy, size, color index, age), ...] in flight right now."""
        self._poll_signal(now)
        rate = self._rate if now - self._stamp < self.TTL else 0.0
        # Clamp the step so a stalled render (or a fetch hiccup) can't dump a
        # whole backlog of sparks into one frame.
        step = min(now - self._last, 0.25) if self._last else 0.0
        self._last = now
        self._pending += rate * step
        while self._pending >= 1.0:
            self._pending -= 1.0
            ang = math.radians(random.uniform(-175, -5))       # upward (y grows down)
            spd = random.uniform(50, 140)
            self._parts.append((now, math.cos(ang) * spd, math.sin(ang) * spd,
                                random.choice([1.5, 2.0, 2.5]), random.randrange(4)))
        self._parts = [p for p in self._parts if now - p[0] <= self.LIFE]
        return [(vx, vy, sz, ci, now - t0) for t0, vx, vy, sz, ci in self._parts]

    def _read_signal(self):
        try:
            with open(self._signal_path) as f:
                return int(json.load(f).get("n", 0))
        except Exception:
            return 0

    def _poll_signal(self, now):
        if now - self._sig_ts < 0.15:
            return
        self._sig_ts = now
        n = self._read_signal()
        if n != self._sig_n:
            self._sig_n = n
            self.set_burn(self.MANUAL_DELTA, now)


def burst_fx(ttl):
    """The shared burst channel: the panel's 💥 button bumps one counter, and only
    one usage source runs at a time, so both screens read the same file."""
    return BurstFX(os.path.join(TELEM_DIR, "burst.json"), ttl)


# ---- the feed behind a screen ----

class Feed:
    """One usage feed, polled off the render thread.

    A fetch is a call to an account API behind Cloudflare and takes a second or
    two; doing it in the frame loop would stutter the animation. So each feed
    owns a thread that keeps `model` current and sorts whatever went wrong into
    the states a screen knows how to draw: a live model, a key that needs
    re-saving, no data at all, or numbers going stale under repeated failures.

    It also owns the screen's burst channel, because "how much did usage move
    since the last poll" is only knowable here.

    Thread boundary: `poll()` runs on the feed thread and writes; readers on the
    render thread only read. Every write is a single slot, so no lock — keep it
    that way.
    """

    def __init__(self, name, fetch, interval, auth=(), nodata=(), stale_after=6, note=None):
        self.name, self._fetch, self.interval = name, fetch, interval
        self.log = logging.getLogger(name)
        self._was = None               # last state reported at INFO, to log changes only
        self._auth_exc, self._nodata_exc = auth, nodata
        self.stale_after = stale_after
        self._note = note              # optional extra fields for the log line
        self.fx = burst_fx(interval * 1.3)
        self.model = None
        self.err = None
        self.err_n = 0
        self.auth = False              # the key was rejected; won't heal by itself
        self.nodata = False            # nothing to read at all
        self._prev_util = None

    @property
    def stale(self):
        """Fetches have been failing long enough that the numbers are old."""
        return self.err_n >= self.stale_after

    @property
    def ready(self):
        """There is a model to draw and no dead end to report — i.e. this screen
        is worth showing. A rotation uses this to skip a feed that can't answer."""
        return self.model is not None and not self.auth and not self.nodata

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name=f"feed-{self.name}").start()
        return self

    def _loop(self):
        while True:
            self.poll()
            time.sleep(self.interval)

    def poll(self):
        """One fetch. The numbers go to DEBUG because they arrive every ten
        seconds forever; INFO is reserved for the handful of moments the log is
        actually consulted about — what changed, and what broke."""
        try:
            m = self._fetch()
            if self._prev_util is not None:      # every poll reports, gain or not
                self.fx.set_burn(m.util - self._prev_util)
            self._prev_util = m.util
            self.model, self.err = m, None
            self.err_n, self.auth, self.nodata = 0, False, False
            extra = f" {self._note(m)}" if self._note else ""
            self.log.debug("util=%.0f%% now_bin=%d hits_in=%s wk=%.0f%%%s",
                           m.util, m.now_bin, hhmm(m.hits_in_h), m.weekly, extra)
            self._report(m.state, "%s — util %.0f%%, weekly %.0f%%, runout %s",
                         m.state.upper(), m.util, m.weekly, hhmm(m.hits_in_h))
        except self._auth_exc as e:
            # The key won't come back on its own — flag it so the screen flips to
            # the renewal notice instead of quietly showing stale numbers. The
            # secret is re-read every poll, so a re-saved key is picked up here
            # without a restart.
            self.auth, self.err = True, str(e)
            self._was = None
            self.log.warning("key rejected: %s", self.err)
        except self._nodata_exc as e:
            self.nodata, self.err = True, str(e)
            self._was = None
            self.log.warning("no data: %s", self.err)
        except Exception as e:
            self.err_n += 1
            self.err = str(e).split("\n")[0][:60]
            self._was = None
            log = self.log.error if self.stale else self.log.warning
            log("fetch failed (%dx): %s", self.err_n, self.err)

    def _report(self, state, fmt, *args):
        """Log at INFO only when the verdict changes — a screen that sits at 'ok'
        for five hours should cost the log one line, not eighteen hundred."""
        if state != self._was:
            self._was = state
            self.log.info(fmt, *args)


def draw_particles(d, th, ox, oy, particles):
    """Draw flying particles from (ox, oy) on parabolic arcs."""
    for vx, vy, sz, ci, age in particles:
        fade = max(0.0, 1.0 - age / BurstFX.LIFE)
        bright = 0.5 + 0.5 * fade
        x = ox + vx * age
        y = oy + vy * age + 0.5 * BurstFX.GRAVITY * age * age
        if y > H + 14 or x < -14 or x > W + 14:
            continue
        c = _lerp(th.sparks[ci % len(th.sparks)], th.bg, 1.0 - bright)
        d.rectangle([px(x - sz), px(y - sz), px(x + sz), px(y + sz)], fill=c)


# ---- the screen ----

def render(m, th, mascot, badge, fx, gif=None, t=0.0, sub=""):
    """One frame of the burn monitor.

    `badge` is the (text, color) readout in the top-right corner — the source's
    own verdict on how fresh its feed is. `sub` is an optional word next to
    "LIMIT 100%" (a plan or scoped-limit name).
    """
    img = Image.new("RGB", (W * SS, H * SS), th.bg)
    d = ImageDraw.Draw(img)

    # ---- state: the model's single verdict mapped to colors ----
    locked, danger = m.state == "locked", m.state == "danger"
    accent = th.crit if locked else (th.warn if danger else th.use)
    concl = th.crit if locked else (th.warn if danger else th.ok)
    nbins = len(m.cum_pct)

    def clk(x):     # clock times are rounded to the minute, never truncated
        return (x.astimezone() + dt.timedelta(seconds=30)).strftime("%H:%M")

    # ---- header ----
    d.text((px(10), px(6)), th.title, font=font(15, True), fill=th.fg)
    tw = 10 + 9.0 * len(th.title) + 9       # mono advance at 15px, plus a gap
    d.text((px(tw), px(8)), "▶", font=font(11), fill=accent)
    d.text((px(tw + 18), px(6)), f"{round(m.bin_min)}min", font=font(15, True), fill=th.fg)
    d.text((px(230), px(8)), badge[0], font=font(11, badge[0].startswith("!")),
           fill=badge[1], anchor="ra")
    d.text((px(10), px(28)), "LIMIT 100%", font=font(11), fill=accent)
    if sub:
        d.text((px(80), px(28)), sub[:9].upper(), font=font(11), fill=th.gray)
    d.text((px(230), px(28)), f"RESET ▸{clk(m.end_dt)}", font=font(11), fill=th.gray,
           anchor="ra")

    # limit reference line (top of chart) + window track (baseline / x-axis)
    d.line([px(X0), px(TOP), px(X1), px(TOP)], fill=accent, width=px(2))
    d.line([px(X0), px(BASE), px(X1), px(BASE)], fill=th.track, width=px(1))

    # ---- bars ----
    bw = (X1 - X0) / nbins

    def y(p):
        return BASE - (p / 100.0) * (BASE - TOP)

    # ---- y axis: the used % rides it at its own height (0 and 100 yield to it) ----
    d.line([px(X0 - 2), px(TOP), px(X0 - 2), px(BASE)], fill=th.track, width=px(1))
    ylev = y(m.util)
    for lvl, lab in ((TOP, "100"), (BASE, "0")):
        if abs(ylev - lvl) > 9:
            d.text((px(X0 - 6), px(lvl)), lab, font=font(8), fill=th.gray, anchor="rm")
    d.line([px(X0 - 5), px(ylev), px(X0 - 2), px(ylev)], fill=accent, width=px(1))
    d.text((px(X0 - 7), px(ylev)), f"{round(m.util)}%", font=font(10, True),
           fill=accent, anchor="rm")

    binh = m.bin_min / 60.0
    for k in range(nbins):
        cx = X0 + bw * k + bw / 2
        x0, x1 = cx - bw * 0.36, cx + bw * 0.36
        if k <= m.now_bin:                                 # measured: a solid bar
            col = th.fg if k == m.now_bin else accent
            d.rectangle([px(x0), px(y(m.cum_pct[k])), px(x1), px(BASE)], fill=col)
        else:                                              # projected: a ghost bar
            p = min(100.0, m.util + max(0.0, m.slope) * ((k + 1) * binh - m.elapsed_h))
            _ghost_box(d, x0, y(p), x1, BASE, th.proj(p))

    nb = m.now_bin
    cxn = X0 + bw * nb + bw / 2

    # ---- x axis: real clock times, window-reset -> next-reset. The NOW clock is
    # centred on its bin, so the fixed end labels yield to it when it drifts near.
    if cxn > X0 + 44:
        d.text((px(X0), px(155)), clk(m.start_dt), font=font(10), fill=th.gray)
    if cxn < X1 - 44:      # the exact reset clock also lives in the header
        d.text((px(X1), px(155)), clk(m.end_dt), font=font(10), fill=th.gray, anchor="ra")
    # Centred on its bin, but clamped inside the axis: in the window's last bin
    # the label would otherwise hang off the right edge and get clipped.
    d.text((px(min(max(cxn, X0 + 16), X1 - 16)), px(155)), clk(m.now_dt),
           font=font(10, True), fill=th.fg, anchor="ma")

    # ---- the two deadlines, side by side: whichever lands first is what happens.
    # RESET is the one you can't argue with, so it gets the big type; RUNOUT is
    # the projection racing it, colored by which of the two wins.
    runout = "0:00" if locked else hhmm(m.hits_in_h)
    d.text((px(10), px(165)), "RUNOUT", font=font(9), fill=th.gray)
    d.text((px(8), px(179)), runout, font=font(20, True), fill=concl)
    # The reset is an instant, so count down to it from the wall clock rather than
    # from the model's snapshot — otherwise the seconds would jump a poll at a time.
    left_h = max(0.0, (m.end_dt - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600.0)
    d.text((px(80), px(165)), "RESET IN", font=font(9), fill=th.gray)
    d.text((px(78), px(174)), _countdown(left_h), font=font(30, True),
           fill=th.ok if left_h < IMMINENT_H else th.fg)

    # ---- gif / info box ----
    bx0, by0, bx1, by1 = GBOX
    if gif is not None:            # an explicitly-picked GIF overrides the mascot
        fr = gif.frame(t)
        fr = fr.resize((px(bx1 - bx0), px(by1 - by0)), Image.LANCZOS)
        img.paste(fr, (px(bx0), px(by0)))
    else:                          # default: the source's own hand-drawn mascot
        mascot(d, t)

    # ---- weekly segment bar ----
    d.text((px(10), px(215)), "WK", font=font(11), fill=th.gray)
    wx0, wx1, nblk = 34, 206, 26
    blkw = (wx1 - wx0) / nblk
    fill_n = int(round(m.weekly / 100.0 * nblk))
    for i in range(nblk):
        bx = wx0 + i * blkw
        d.rectangle([px(bx), px(216), px(bx + blkw * 0.7), px(224)],
                    fill=th.fg if i < fill_n else th.dim)
    d.text((px(230), px(215)), f"{round(m.weekly)}%", font=font(11, True),
           fill=th.fg, anchor="ra")

    # ---- micro-burst particles flying on top ----
    draw_particles(d, th, cxn, y(m.cum_pct[nb]), fx.particles(time.time()))
    return img.resize((W, H), Image.LANCZOS)


def render_wait(th, msg):
    img = Image.new("RGB", (W * SS, H * SS), th.bg)
    d = ImageDraw.Draw(img)
    d.text((px(10), px(6)), th.title, font=font(15, True), fill=th.fg)
    d.text((px(120), px(120)), msg, font=font(11), fill=th.gray, anchor="mm")
    return img.resize((W, H), Image.LANCZOS)


def render_notice(th, head, *lines):
    """A dead end the numbers can't describe — a rejected key, a missing log.
    Stale data would be a lie, so say it outright and point at the fix.
    (Mono font: no Hangul, keep these English.)"""
    img = Image.new("RGB", (W * SS, H * SS), th.bg)
    d = ImageDraw.Draw(img)
    d.text((px(10), px(6)), th.title, font=font(15, True), fill=th.fg)
    d.text((px(120), px(104)), head, font=font(14, True), fill=th.warn, anchor="mm")
    for i, ln in enumerate(lines):
        d.text((px(120), px(126 + i * 16)), ln, font=font(10), fill=th.gray, anchor="mm")
    return img.resize((W, H), Image.LANCZOS)


# ---- a screen, and the loop that drives one or more of them ----

FPS = 10.0                # smooth GIF / mascot / burst playback
MIN_ROTATE = 5.0          # below this the countdowns are unreadable


@dataclass
class Screen:
    """One usage screen: a palette, a mascot, a feed, and how it talks about itself.

    This is the contract a new screen implements — there is nothing else to
    provide, and nothing here knows which provider it is showing. `stream_usage`
    rotates a list of these, so a screen cannot behave differently alone than it
    does in the rotation: there is only one `frame()`.
    """
    theme: Theme
    mascot: object                # mascot(draw, t) -> fills GBOX when no GIF is picked
    feed: Feed
    gif: GifPick
    badge: object                 # badge(model, stale) -> (text, color)
    notice: object = None         # notice(feed) -> (head, *lines) when there is no model
    sub: object = None            # sub(model) -> the word beside LIMIT 100%
    waiting: str = "loading..."   # shown until the feed's first answer

    @property
    def ready(self):
        """There is a model to draw and no dead end to report."""
        return self.feed.ready

    def frame(self, t):
        """This screen right now — the burn monitor, or whatever its feed can
        only say in words."""
        words = self.notice(self.feed) if self.notice else None
        if words:
            return render_notice(self.theme, *words)
        m = self.feed.model
        if m is None:
            return render_wait(self.theme, self.feed.err or self.waiting)
        return render(m, self.theme, self.mascot, self.badge(m, self.feed.stale),
                      self.feed.fx, gif=self.gif.get(), t=t,
                      sub=self.sub(m) if self.sub else "")


def parse_host(argv):
    """`--host IP` out of a source's argv; None means SMALLTV_HOST / the default."""
    for i, a in enumerate(argv):
        if a == "--host" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def next_screen(screens, cur, switched, now, rotate):
    """(index, switched_at) for this frame.

    Two rules: a healthy screen keeps its full turn, and a screen that can only
    show a notice yields immediately to one that can show numbers.
    """
    pool = [i for i, sc in enumerate(screens) if sc.ready]
    if not pool:                       # nothing to draw anywhere: rotate anyway
        pool = list(range(len(screens)))
    elif cur not in pool:              # ours went dark while another is fine
        return pool[0], now
    if now - switched < rotate:
        return cur, switched
    if len(pool) == 1:                 # only one worth showing — stay on it
        return pool[0], now
    return next((i for i in pool if i > cur), pool[0]), now


def run(screens, host=None, rotate=None, label="usage"):
    """Hold the device's one stream client and draw `screens`, rotating if asked.

    Everything about being a stream source lives here — the connect/retry loop,
    the frame pacing, and the fact that a notice screen has nothing to animate
    so it can be pushed twice a second instead of ten times.
    """
    log = logging.getLogger(label)
    s = Streamer(resolve_host(host), PORT)
    for sc in screens:
        sc.feed.start()
    if len(screens) > 1:
        log.info("rotating %s every %.0fs",
                 " / ".join(sc.theme.title for sc in screens), rotate)
    t0 = time.time()
    cur, switched = 0, t0
    while True:
        try:
            s.connect()
            while True:
                frame_start = time.time()
                if len(screens) > 1:
                    was = cur
                    cur, switched = next_screen(screens, cur, switched, frame_start, rotate)
                    if cur != was:
                        log.debug("showing %s", screens[cur].theme.title)
                sc = screens[cur]
                s.push(sc.frame(frame_start - t0))
                if not sc.ready:            # a notice screen; nothing animates
                    time.sleep(0.5)
                    continue
                sleep_for = (1.0 / FPS) - (time.time() - frame_start)
                if sleep_for > 0:
                    time.sleep(sleep_for)
        except OSError as e:
            log.warning("disconnected: %s; retrying in 3s", e)
            try:
                if s.sock:
                    s.sock.close()
            except OSError:
                pass
            time.sleep(3)
