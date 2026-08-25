#!/usr/bin/env python3
"""Claude usage on the SmallTV: a terminal-style burn monitor.

A burn histogram of cumulative session usage over the fixed reset-to-reset
window: 30-min bins, real clock times along the x-axis, and the live usage %
riding the y-axis at its own height. Solid bars are measured; the bins ahead
are dashed ghost bars at the projected level, warming toward red as they near
the limit, so the bin that reaches it is the one that runs you out. Below: the
two deadlines racing each other — RUNOUT and RESET IN — plus the hand-drawn Claude
mascot (or a panel-picked GIF) and a weekly segment bar. Usage gains fire
micro-burst particle pops over the NOW bar.

    python stream_claude.py [--host IP]

Data: live claude.ai limits API (utilization + reset); the curve is the
utilization history this source records itself, so past bars never change
retroactively. See claudeusage.burn_model.
"""
import datetime as dt
import json
import math
import os
import random
import sys
import threading
import time

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claudeusage as cu                                    # noqa: E402
import config as cfg_mod                                    # noqa: E402
import stream as stream_mod                                 # noqa: E402
from smalltv_stream import PORT, SS, H, Streamer, TELEM_DIR, W, resolve_host  # noqa: E402

REFETCH_SECS = 10.0       # poll cadence (fetch runs off-thread so it never stutters)
FPS = 10.0                # smooth GIF / mascot / burst playback
STALE_AFTER = 6           # consecutive fetch errors (~1 min) before the screen says so

# palette per DISPLAY SPEC v1
BG = (11, 10, 10)
CREAM = (242, 236, 225)     # #F2ECE1  just-now bar / values
CLAY = (217, 119, 87)       # #D97757  session usage
GREEN = (143, 174, 134)     # #8FAE86  conclusion (safe)
AMBER = (224, 163, 63)      # #E0A33F  pace exceeded
RED = (207, 75, 58)         # #CF4B3A  limit reached
GRAY = (120, 112, 100)      # muted labels
TRACKGRAY = (74, 66, 58)    # #4A423A  window track
DIM = (42, 38, 34)

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


def _s(v):
    return int(round(v * SS))


def _lerp(a, b, f):
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))


def _hmm(hours):
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
        return _hmm(hours)
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
        d.line([_s(x0 + dx * a), _s(y0 + dy * a), _s(x0 + dx * b), _s(y0 + dy * b)],
               fill=color, width=_s(w))
        t += dash + gap


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


_gif_state = {"ts": 0.0, "name": None, "gif": None}


def load_gif():
    """The GIF the panel picked (config `claude_gif`), or None. The config is
    re-read at most every 2 s, and the GIF is only re-decoded when the name
    changes — so calling this every render frame is cheap and picks up panel
    changes within a couple seconds."""
    now = time.time()
    if now - _gif_state["ts"] < 2.0:
        return _gif_state["gif"]
    _gif_state["ts"] = now
    name = (cfg_mod.load().get("claude_gif") or "").strip()
    if name != _gif_state["name"]:
        _gif_state["name"] = name
        path = os.path.join(stream_mod.gif_dir(), name) if name else ""
        gif = None
        if path and os.path.exists(path):
            try:
                gif = Gif(path)
            except Exception:
                gif = None
        _gif_state["gif"] = gif
    return _gif_state["gif"]


# chart + layout geometry (final 240x240 units). X0 leaves a left margin for
# the y-axis, which carries the used-% readout at its own height.
X0, X1 = 33, 230
TOP, BASE = 46, 150
GBOX = (172, 164, 230, 208)   # gif / mascot box (x0,y0,x1,y1) — right-side accent

# hand-drawn Claude mascot (the default box content): a coral blob that bobs,
# blinks, and has a twinkling star antenna. Drawn directly — no GIF file.
MC_D = (168, 86, 62)      # shaded belly / feet
MC_L = (233, 150, 118)    # cheeks
MC_EYE = (34, 26, 22)


def _box(d, x0, y0, x1, y1, fill):
    d.rectangle([_s(x0), _s(y0), _s(x1), _s(y1)], fill=fill)


def _ghost_box(d, x0, y0, x1, y1, color):
    """An unfilled bar outlined in dashes — a projection, not a measurement.
    The baseline is left open; the chart's own axis already draws it."""
    _dash(d, x0, y0, x1, y0, color, 1, 3, 3)
    for x in (x0, x1):
        _dash(d, x, y0, x, y1, color, 1, 3, 3)


def _proj_color(pct):
    """Projected bars warm from the usual clay toward amber and then red as they
    approach the limit, so a run that ends badly looks wrong before you read it."""
    if pct <= 50:
        return CLAY
    if pct <= 85:
        return _lerp(CLAY, AMBER, (pct - 50) / 35.0)
    return _lerp(AMBER, RED, min(1.0, (pct - 85) / 15.0))


def draw_mascot(d, t):
    """Angular pixel-art Claude creature: a hard-edged coral box with square eyes,
    blocky legs, and a twinkling diamond spark antenna. Bobs and blinks."""
    cx = (GBOX[0] + GBOX[2]) / 2
    ph = t * 2.2
    cy = (GBOX[1] + GBOX[3]) / 2 + 1 + round(math.sin(ph) * 2)   # stepped bob (pixel feel)
    bw, bh = 20, 11
    top, bot = cy - bh, cy + bh

    for lx in (-13, 0, 13):                       # blocky legs (behind body)
        _box(d, cx + lx - 3, bot - 1, cx + lx + 3, bot + 6, MC_D)
    _box(d, cx - bw, top, cx + bw, bot, CLAY)     # body (hard corners)
    _box(d, cx - bw, cy + 4, cx + bw, bot, MC_D)  # belly shade band
    _box(d, cx - bw, top, cx + bw, top + 2, MC_L)  # top highlight edge

    blink = (t % 3.0) < 0.14
    for ex in (-8, 8):                            # square eyes
        if blink:
            _box(d, cx + ex - 4, cy - 3, cx + ex + 4, cy - 1, MC_EYE)
        else:
            _box(d, cx + ex - 4, cy - 6, cx + ex + 4, cy + 1, MC_EYE)
            _box(d, cx + ex, cy - 5, cx + ex + 2, cy - 3, CREAM)   # glint

    tip = top - 9                                 # wick + diamond spark
    _box(d, cx - 1, top - 6, cx + 1, top, MC_D)
    tw = 2 + 1.6 * abs(math.sin(ph * 2))
    d.polygon([(_s(cx), _s(tip - tw * 1.8)), (_s(cx + tw), _s(tip)),
               (_s(cx), _s(tip + tw * 1.8)), (_s(cx - tw), _s(tip))], fill=CREAM)
    _box(d, cx - 1, tip - 1, cx + 1, tip + 1, AMBER)


class BurstFX:
    """Sparks over the NOW bar for as long as usage is climbing — an activity
    light, not a firework. Each poll reports how much was gained, and that sets
    an emission rate that holds until the next poll replaces it. Because the
    reading is stamped and expires after TTL, "no more polls" means the sparks
    stop on their own: a failed fetch, a wedged fetcher, or a genuinely idle
    model all read as calm without anyone having to switch them off.

    So a 1% gain — the smallest the API can express, since it reports whole
    percent — is a full poll interval of visible sparks rather than a blink,
    and heavier use shows up as a denser stream over the same span.

    Thread boundary: set_burn() is called from the fetcher thread, particles()
    only from the render loop. Each writes its own attributes and the reads are
    single-slot, so no lock is needed — keep it that way if you add methods.
    """
    LIFE = 0.6            # seconds a spark flies
    GRAVITY = 300.0       # px/s^2 for the parabolic arcs
    TTL = REFETCH_SECS * 1.3    # a reading outlives its poll a little, so back-to-back
                                # busy polls emit seamlessly instead of flickering
    BASE_RATE = 2.0       # sparks/s at the faintest measurable burn
    RATE_PER_PCT = 1.5    # extra sparks/s per point gained
    MAX_RATE = 40.0
    MANUAL_DELTA = 12     # the panel's test button pretends this much was gained
    COLORS = [(255, 255, 255), (242, 236, 225), (217, 119, 87), (224, 163, 63)]

    def __init__(self, signal_path):
        self._rate = 0.0        # sparks/s from the newest reading
        self._stamp = 0.0       # when that reading was taken (staleness clock)
        self._parts = []        # sparks in flight: (spawn, vx, vy, size, color)
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
        """[(vx, vy, size, color, age), ...] in flight right now."""
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
                                random.choice([1.5, 2.0, 2.5]),
                                random.choice(self.COLORS)))
        self._parts = [p for p in self._parts if now - p[0] <= self.LIFE]
        return [(vx, vy, sz, col, now - t0) for t0, vx, vy, sz, col in self._parts]

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


FX = BurstFX(os.path.join(TELEM_DIR, "burst.json"))


def draw_particles(d, ox, oy, particles):
    """Draw flying particles from (ox, oy) on parabolic arcs."""
    for vx, vy, sz, col, age in particles:
        fade = max(0.0, 1.0 - age / BurstFX.LIFE)
        bright = 0.5 + 0.5 * fade
        x = ox + vx * age
        y = oy + vy * age + 0.5 * BurstFX.GRAVITY * age * age
        if y > H + 14 or x < -14 or x > W + 14:
            continue
        c = _lerp(col, BG, 1.0 - bright)
        d.rectangle([_s(x - sz), _s(y - sz), _s(x + sz), _s(y + sz)], fill=c)


def render(m, gif=None, t=0.0, stale=False):
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    # ---- state: the model's single verdict mapped to colors ----
    locked, danger = m.state == "locked", m.state == "danger"
    accent = RED if locked else (AMBER if danger else CLAY)
    concl = RED if locked else (AMBER if danger else GREEN)

    def clk(x):     # clock times are rounded to the minute, never truncated
        return (x.astimezone() + dt.timedelta(seconds=30)).strftime("%H:%M")

    # ---- header ----
    d.text((_s(10), _s(6)), "burn", font=font(15, True), fill=CREAM)
    d.text((_s(55), _s(8)), "▶", font=font(11), fill=accent)
    d.text((_s(73), _s(6)), f"{cu.BURN_BIN_MIN}min", font=font(15, True), fill=CREAM)
    if stale:   # fetches have been failing for a while — the numbers are old
        d.text((_s(230), _s(8)), "! STALE", font=font(11, True), fill=AMBER, anchor="ra")
    else:
        arrow = "▼" if m.easing else "▲"
        trend = "EASING" if m.easing else "RISING"
        d.text((_s(230), _s(8)), f"Δ+{round(m.delta)} {arrow} {trend}",
               font=font(11), fill=GREEN if m.easing else AMBER, anchor="ra")
    d.text((_s(10), _s(28)), "LIMIT 100%", font=font(11), fill=accent)
    d.text((_s(230), _s(28)), f"RESET ▸{clk(m.end_dt)}", font=font(11), fill=GRAY, anchor="ra")

    # limit reference line (top of chart) + window track (baseline / x-axis)
    d.line([_s(X0), _s(TOP), _s(X1), _s(TOP)], fill=accent, width=_s(2))
    d.line([_s(X0), _s(BASE), _s(X1), _s(BASE)], fill=TRACKGRAY, width=_s(1))

    # ---- bars ----
    bw = (X1 - X0) / cu.BURN_NBINS

    def y(p):
        return BASE - (p / 100.0) * (BASE - TOP)

    # ---- y axis: the used % rides it at its own height (0 and 100 yield to it) ----
    d.line([_s(X0 - 2), _s(TOP), _s(X0 - 2), _s(BASE)], fill=TRACKGRAY, width=_s(1))
    ylev = y(m.util)
    for lvl, lab in ((TOP, "100"), (BASE, "0")):
        if abs(ylev - lvl) > 9:
            d.text((_s(X0 - 6), _s(lvl)), lab, font=font(8), fill=GRAY, anchor="rm")
    d.line([_s(X0 - 5), _s(ylev), _s(X0 - 2), _s(ylev)], fill=accent, width=_s(1))
    d.text((_s(X0 - 7), _s(ylev)), f"{round(m.util)}%", font=font(10, True),
           fill=accent, anchor="rm")

    binh = cu.BURN_BIN_MIN / 60.0
    for k in range(cu.BURN_NBINS):
        cx = X0 + bw * k + bw / 2
        x0, x1 = cx - bw * 0.36, cx + bw * 0.36
        if k <= m.now_bin:                                 # measured: a solid bar
            col = CREAM if k == m.now_bin else accent
            d.rectangle([_s(x0), _s(y(m.cum_pct[k])), _s(x1), _s(BASE)], fill=col)
        else:                                              # projected: a ghost bar
            p = min(100.0, m.util + max(0.0, m.slope) * ((k + 1) * binh - m.elapsed_h))
            _ghost_box(d, x0, y(p), x1, BASE, _proj_color(p))

    nb = m.now_bin
    cxn = X0 + bw * nb + bw / 2

    # ---- x axis: real clock times, window-reset -> next-reset. The NOW clock is
    # centred on its bin, so the fixed end labels yield to it when it drifts near.
    if cxn > X0 + 44:
        d.text((_s(X0), _s(155)), clk(m.start_dt), font=font(10), fill=GRAY)
    if cxn < X1 - 44:      # the exact reset clock also lives in the header
        d.text((_s(X1), _s(155)), clk(m.end_dt), font=font(10), fill=GRAY, anchor="ra")
    d.text((_s(cxn), _s(155)), clk(m.now_dt), font=font(10, True), fill=CREAM, anchor="ma")

    # ---- the two deadlines, side by side: whichever lands first is what happens.
    # RESET is the one you can't argue with, so it gets the big type; RUNOUT is
    # the projection racing it, colored by which of the two wins.
    runout = "0:00" if locked else _hmm(m.hits_in_h)
    d.text((_s(10), _s(165)), "RUNOUT", font=font(9), fill=GRAY)
    d.text((_s(8), _s(179)), runout, font=font(20, True), fill=concl)
    # The reset is an instant, so count down to it from the wall clock rather than
    # from the model's snapshot — otherwise the seconds would jump a poll at a time.
    left_h = max(0.0, (m.end_dt - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600.0)
    d.text((_s(80), _s(165)), "RESET IN", font=font(9), fill=GRAY)
    d.text((_s(78), _s(174)), _countdown(left_h), font=font(30, True),
           fill=GREEN if left_h < IMMINENT_H else CREAM)

    # ---- gif / info box ----
    bx0, by0, bx1, by1 = GBOX
    if gif is not None:            # an explicitly-picked GIF overrides the mascot
        fr = gif.frame(t)
        fr = fr.resize((_s(bx1 - bx0), _s(by1 - by0)), Image.LANCZOS)
        img.paste(fr, (_s(bx0), _s(by0)))
    else:                          # default: the hand-drawn Claude mascot
        draw_mascot(d, t)

    # ---- weekly segment bar ----
    d.text((_s(10), _s(215)), "WK", font=font(11), fill=GRAY)
    wx0, wx1, nblk = 34, 206, 26
    blkw = (wx1 - wx0) / nblk
    fill_n = int(round(m.weekly / 100.0 * nblk))
    for i in range(nblk):
        bx = wx0 + i * blkw
        d.rectangle([_s(bx), _s(216), _s(bx + blkw * 0.7), _s(224)],
                    fill=CREAM if i < fill_n else DIM)
    d.text((_s(230), _s(215)), f"{round(m.weekly)}%", font=font(11, True),
           fill=CREAM, anchor="ra")

    # ---- micro-burst particles flying on top ----
    draw_particles(d, cxn, y(m.cum_pct[nb]), FX.particles(time.time()))
    return img.resize((W, H), Image.LANCZOS)


def render_wait(msg):
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)
    d.text((_s(10), _s(6)), "burn", font=font(15, True), fill=CREAM)
    d.text((_s(120), _s(120)), msg, font=font(11), fill=GRAY, anchor="mm")
    return img.resize((W, H), Image.LANCZOS)


def render_auth():
    """The session key was rejected — stale data would be a lie, so say it
    outright and point at the fix. (Mono font: no Hangul, keep it English.)"""
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)
    d.text((_s(10), _s(6)), "burn", font=font(15, True), fill=CREAM)
    d.text((_s(120), _s(104)), "! SESSION EXPIRED", font=font(14, True),
           fill=AMBER, anchor="mm")
    d.text((_s(120), _s(126)), "re-save the key in the panel", font=font(10),
           fill=GRAY, anchor="mm")
    d.text((_s(120), _s(142)), ":8787 → usage", font=font(10),
           fill=GRAY, anchor="mm")
    return img.resize((W, H), Image.LANCZOS)


def parse_args(argv):
    host = None
    i = 0
    while i < len(argv):
        if argv[i] == "--host" and i + 1 < len(argv):
            host, i = argv[i + 1], i + 2
        else:
            i += 1
    return host


# Shared latest model, produced off the render thread so a ~1s fetch never
# stutters the animation. A usage increase between polls fires a burst.
_data = {"model": None, "err": None, "prev_util": None, "err_n": 0, "auth": False}


def _fetcher():
    while True:
        try:
            m = cu.burn_model()
            if _data["prev_util"] is not None:      # every poll reports, gain or not
                FX.set_burn(m.util - _data["prev_util"])
            _data["prev_util"] = m.util
            _data["model"], _data["err"] = m, None
            _data["err_n"], _data["auth"] = 0, False
            print(f"  util={m.util:.0f}% now_bin={m.now_bin} "
                  f"hits_in={_hmm(m.hits_in_h)} wk={m.weekly:.0f}%", flush=True)
        except cu.AuthError as e:
            # the key won't come back on its own — flag it so the screen flips
            # to the renewal notice instead of quietly showing stale numbers.
            # load_secret() re-reads the file each poll, so a re-saved key is
            # picked up here without a restart.
            _data["auth"], _data["err"] = True, str(e)
            print(f"  auth error: {_data['err']}", flush=True)
        except Exception as e:
            _data["err_n"] += 1
            _data["err"] = str(e).split("\n")[0][:40]
            print(f"  fetch error ({_data['err_n']}x): {_data['err']}", flush=True)
        time.sleep(REFETCH_SECS)


def main():
    host = parse_args(sys.argv[1:])
    s = Streamer(resolve_host(host), PORT)
    threading.Thread(target=_fetcher, daemon=True).start()
    t0 = time.time()
    while True:
        try:
            s.connect()
            while True:
                frame_start = time.time()
                model = _data["model"]
                if _data["auth"]:
                    s.push(render_auth())
                    time.sleep(0.5)
                    continue
                if model is None:
                    s.push(render_wait(_data["err"] or "loading..."))
                    time.sleep(0.5)
                    continue
                s.push(render(model, load_gif(), frame_start - t0,
                              stale=_data["err_n"] >= STALE_AFTER))
                sleep_for = (1.0 / FPS) - (time.time() - frame_start)
                if sleep_for > 0:
                    time.sleep(sleep_for)
        except OSError as e:
            print(f"\n[claude] disconnected: {e}; retrying in 3s")
            try:
                if s.sock:
                    s.sock.close()
            except OSError:
                pass
            time.sleep(3)


if __name__ == "__main__":
    main()
