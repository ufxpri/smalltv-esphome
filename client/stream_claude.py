#!/usr/bin/env python3
"""Claude usage on the SmallTV: a terminal-style burn monitor.

A burn histogram of cumulative session usage (20-min bins) with a dashed
projection of the current burn rate toward the 100% limit — so you can see
whether you'll hit the limit before the session resets. Below it: a big
"HITS LIMIT IN" countdown, an optional looping GIF, and a weekly segment bar.

    python stream_claude.py [--host IP]

Data: live claude.ai limits API (utilization + reset) joined with the intra-
window token distribution from ~/.claude/usage.db. See claudeusage.burn_model.
"""
import datetime as dt
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
from smalltv_stream import PORT, SS, H, Streamer, W, resolve_host  # noqa: E402

REFETCH_SECS = 10.0       # poll cadence (fetch runs off-thread so it never stutters)
FPS = 10.0                # smooth GIF / mascot / burst playback

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
DIMBAR = (52, 46, 40)
PROJ = (205, 200, 185)

MONO = "/System/Library/Fonts/Menlo.ttc"
_fc = {}


def font(px, bold=False):
    key = (px, bold)
    if key not in _fc:
        try:
            _fc[key] = ImageFont.truetype(MONO, int(px * SS), index=1 if bold else 0)
        except Exception:
            _fc[key] = ImageFont.load_default()
    return _fc[key]


def _s(v):
    return int(round(v * SS))


def _hmm(hours):
    if hours is None or hours == math.inf:
        return "--:--"
    m = int(round(hours * 60))
    return f"{m // 60}:{m % 60:02d}"


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


# chart + layout geometry (final 240x240 units)
X0, X1 = 10, 230
TOP, BASE = 46, 150
GBOX = (172, 164, 230, 208)   # gif / mascot box (x0,y0,x1,y1) — right-side accent

# hand-drawn Claude mascot (the default box content): a coral blob that bobs,
# blinks, and has a twinkling star antenna. Drawn directly — no GIF file.
MC_D = (168, 86, 62)      # shaded belly / feet
MC_L = (233, 150, 118)    # cheeks
MC_EYE = (34, 26, 22)


def _box(d, x0, y0, x1, y1, fill):
    d.rectangle([_s(x0), _s(y0), _s(x1), _s(y1)], fill=fill)


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


# ---- pixel-explosion FX: a burst above the bar that just rose (spec §6) ----
# 4 age stages, cooling from ignition-white to residual-dark; one burst at a time
# (a fresh trigger drops the previous one), emitted upward so it never hits the gif.
BURST_LIFE = 0.62
BURST_STAGES = [(0.09, (242, 236, 225)), (0.18, (217, 119, 87)),
                (0.36, (141, 107, 88)), (0.62, (51, 44, 39))]
_burst = {"spawn": -9.0, "parts": []}


def trigger_burst(delta):
    """Spawn a burst sized by the usage jump Δ (in % points). Δ<=~0 does nothing."""
    if delta <= 0.3:
        return
    mag = 3 if delta >= 8 else (2 if delta >= 3 else 1)     # small / medium / large
    npart = {1: 8, 2: 14, 3: 22}[mag]
    base = {1: 2.3, 2: 3.6, 3: 5.2}[mag]
    parts = []
    for _ in range(npart):
        ang = math.radians(random.uniform(205, 335))        # upward fan (y grows down)
        spd = random.uniform(20, 46) * (0.7 + 0.15 * mag)
        parts.append((math.cos(ang) * spd, math.sin(ang) * spd,
                      base * random.uniform(0.7, 1.3)))
    _burst.update(spawn=time.time(), parts=parts)


def draw_burst(d, ox, oy, now):
    """Draw the active burst (if any) originating at bar-top (ox, oy)."""
    age = now - _burst["spawn"]
    if age > BURST_LIFE or not _burst["parts"]:
        return
    col = next(c for lim, c in BURST_STAGES if age <= lim)
    for vx, vy, sz in _burst["parts"]:
        px = ox + vx * age
        py = oy + vy * age + 34 * age * age                  # a little gravity
        r = max(sz * (1.0 - 0.35 * age / BURST_LIFE), 0.6)
        d.rectangle([_s(px - r), _s(py - r), _s(px + r), _s(py + r)], fill=col)


def render(m, gif=None, t=0.0):
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    # ---- state (one comparison: runs_out vs reset_left) ----
    locked = m.util >= 100.0
    danger = (not locked) and (m.proj_h <= m.window_h)     # hits the limit before reset
    accent = RED if locked else (AMBER if danger else CLAY)
    concl = RED if locked else (AMBER if danger else GREEN)

    # ---- header ----
    d.text((_s(10), _s(6)), "burn", font=font(15, True), fill=CREAM)
    d.text((_s(55), _s(8)), "▶", font=font(11), fill=accent)
    d.text((_s(73), _s(6)), f"{cu.BURN_BIN_MIN}min", font=font(15, True), fill=CREAM)
    arrow = "▼" if m.easing else "▲"
    trend = "EASING" if m.easing else "RISING"
    d.text((_s(230), _s(8)), f"Δ+{round(m.delta)} {arrow} {trend}",
           font=font(11), fill=GREEN if m.easing else AMBER, anchor="ra")
    d.text((_s(10), _s(28)), "LIMIT 100%", font=font(11), fill=accent)
    d.text((_s(230), _s(28)), f"RESET {_hmm(m.reset_h)}", font=font(11), fill=GRAY, anchor="ra")

    # limit reference line (top of chart) + window track (baseline / x-axis)
    d.line([_s(X0), _s(TOP), _s(X1), _s(TOP)], fill=accent, width=_s(2))
    d.line([_s(X0), _s(BASE), _s(X1), _s(BASE)], fill=TRACKGRAY, width=_s(1))

    # ---- bars ----
    bw = (X1 - X0) / cu.BURN_NBINS

    def y(p):
        return BASE - (p / 100.0) * (BASE - TOP)

    for k in range(cu.BURN_NBINS):
        cx = X0 + bw * k + bw / 2
        x0, x1 = cx - bw * 0.36, cx + bw * 0.36
        if k <= m.now_bin:
            col = CREAM if k == m.now_bin else accent
            d.rectangle([_s(x0), _s(y(m.cum_pct[k])), _s(x1), _s(BASE)], fill=col)
        else:
            d.rectangle([_s(x0), _s(BASE - 3), _s(x1), _s(BASE)], fill=DIMBAR)

    # ---- projection + OUT cursor ----
    nb = m.now_bin
    cxn = X0 + bw * nb + bw / 2
    yn = y(m.cum_pct[nb])
    out_x = None
    if m.slope > 1e-6:
        dpb = m.slope * (cu.BURN_BIN_MIN / 60.0)          # % per bin
        x100 = cxn + ((100.0 - m.cum_pct[nb]) / dpb) * bw
        if x100 <= X1:                                     # limit hit within this window
            _dash(d, cxn, yn, x100, TOP, PROJ)
            out_x = x100
        else:                                              # hit lands after END (off-chart) -> safe
            ey = yn - (X1 - cxn) / (x100 - cxn) * (yn - TOP)
            _dash(d, cxn, yn, X1, ey, PROJ)
    else:
        _dash(d, cxn, yn, X1, yn, (120, 116, 108))
    if out_x is not None:                                  # dashed cursor where it crosses 100%
        for yy in range(TOP, BASE, 6):
            d.line([_s(out_x), _s(yy), _s(out_x), _s(yy + 3)], fill=AMBER, width=_s(1))

    # ---- axis: real clock times (rounded to the minute), window-reset -> next-reset ----
    def clk(x):
        return (x.astimezone() + dt.timedelta(seconds=30)).strftime("%H:%M")
    if cxn > X0 + 44:      # else the centered NOW clock would collide with the start
        d.text((_s(X0), _s(155)), clk(m.start_dt), font=font(10), fill=GRAY)
    d.text((_s(cxn), _s(155)), clk(m.now_dt), font=font(10, True), fill=CREAM, anchor="ma")
    d.text((_s(X1), _s(155)), clk(m.end_dt), font=font(10), fill=GRAY, anchor="ra")

    # ---- pixel explosion above the bar that just rose ----
    draw_burst(d, cxn, y(m.cum_pct[nb]), time.time())

    # ---- conclusion: two balanced stats — USED % (left) and RUNOUT (right) ----
    if locked:
        r_label, r_val = "LOCKED", _hmm(m.reset_h)
    elif m.hits_in_h == math.inf:
        r_label, r_val = "RUNOUT", "SAFE"
    else:
        r_label, r_val = "RUNOUT", _hmm(m.hits_in_h)
    d.text((_s(10), _s(165)), "USED", font=font(9), fill=GRAY)
    d.text((_s(96), _s(165)), r_label, font=font(9), fill=GRAY)
    d.text((_s(8), _s(175)), f"{round(m.util)}%", font=font(26, True), fill=accent)
    d.text((_s(94), _s(175)), r_val, font=font(26, True), fill=concl)

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
    return img.resize((W, H), Image.LANCZOS)


def render_wait(msg):
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)
    d.text((_s(10), _s(6)), "burn", font=font(15, True), fill=CREAM)
    d.text((_s(120), _s(120)), msg, font=font(11), fill=GRAY, anchor="mm")
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
_data = {"model": None, "err": None, "prev_util": None}


def _fetcher():
    while True:
        try:
            m = cu.burn_model()
            if _data["prev_util"] is not None and m.util > _data["prev_util"]:
                trigger_burst(m.util - _data["prev_util"])   # usage grew -> spark
            _data["prev_util"] = m.util
            _data["model"], _data["err"] = m, None
            print(f"  util={m.util:.0f}% now_bin={m.now_bin} "
                  f"hits_in={_hmm(m.hits_in_h)} wk={m.weekly:.0f}%")
        except Exception as e:
            _data["err"] = str(e).split("\n")[0][:40]
            print(f"  fetch error: {_data['err']}")
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
                if model is None:
                    s.push(render_wait(_data["err"] or "loading..."))
                    time.sleep(0.5)
                    continue
                s.push(render(model, load_gif(), frame_start - t0))
                dt = (1.0 / FPS) - (time.time() - frame_start)
                if dt > 0:
                    time.sleep(dt)
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
