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

The screen itself is `burnscreen.Screen` — this file supplies only what is
Claude's: the palette, the mascot, how the header reads, and the feed.
`stream_codex.py` supplies its own; `stream_usage.py` rotates both. Layout
changes belong in burnscreen, not here.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import burnscreen as bs                                     # noqa: E402
import claudeusage as cu                                    # noqa: E402
import logs                                                 # noqa: E402
from burnscreen import GBOX, box, px                        # noqa: E402

REFETCH_SECS = 10.0       # poll cadence (fetch runs off-thread so it never stutters)
STALE_AFTER = 6           # consecutive fetch errors (~1 min) before the screen says so

# palette per DISPLAY SPEC v1
THEME = bs.Theme(
    title="burn",
    bg=(11, 10, 10),
    fg=(242, 236, 225),       # #F2ECE1  just-now bar / values
    use=(217, 119, 87),       # #D97757  session usage
    ok=(143, 174, 134),       # #8FAE86  conclusion (safe)
    warn=(224, 163, 63),      # #E0A33F  pace exceeded
    crit=(207, 75, 58),       # #CF4B3A  limit reached
    gray=(120, 112, 100),     # muted labels
    track=(74, 66, 58),       # #4A423A  window track
    dim=(42, 38, 34),
)

# hand-drawn Claude mascot (the default box content): a coral blob that bobs,
# blinks, and has a twinkling star antenna. Drawn directly — no GIF file.
MC_D = (168, 86, 62)      # shaded belly / feet
MC_L = (233, 150, 118)    # cheeks
MC_EYE = (34, 26, 22)


def draw_mascot(d, t):
    """Angular pixel-art Claude creature: a hard-edged coral box with square eyes,
    blocky legs, and a twinkling diamond spark antenna. Bobs and blinks."""
    cx = (GBOX[0] + GBOX[2]) / 2
    ph = t * 2.2
    cy = (GBOX[1] + GBOX[3]) / 2 + 1 + round(math.sin(ph) * 2)   # stepped bob (pixel feel)
    bw, bh = 20, 11
    top, bot = cy - bh, cy + bh

    for lx in (-13, 0, 13):                       # blocky legs (behind body)
        box(d, cx + lx - 3, bot - 1, cx + lx + 3, bot + 6, MC_D)
    box(d, cx - bw, top, cx + bw, bot, THEME.use)     # body (hard corners)
    box(d, cx - bw, cy + 4, cx + bw, bot, MC_D)       # belly shade band
    box(d, cx - bw, top, cx + bw, top + 2, MC_L)      # top highlight edge

    blink = (t % 3.0) < 0.14
    for ex in (-8, 8):                            # square eyes
        if blink:
            box(d, cx + ex - 4, cy - 3, cx + ex + 4, cy - 1, MC_EYE)
        else:
            box(d, cx + ex - 4, cy - 6, cx + ex + 4, cy + 1, MC_EYE)
            box(d, cx + ex, cy - 5, cx + ex + 2, cy - 3, THEME.fg)   # glint

    tip = top - 9                                 # wick + diamond spark
    box(d, cx - 1, top - 6, cx + 1, top, MC_D)
    tw = 2 + 1.6 * abs(math.sin(ph * 2))
    d.polygon([(px(cx), px(tip - tw * 1.8)), (px(cx + tw), px(tip)),
               (px(cx), px(tip + tw * 1.8)), (px(cx - tw), px(tip))],
              fill=THEME.fg)
    box(d, cx - 1, tip - 1, cx + 1, tip + 1, THEME.warn)


def badge(m, stale):
    """The top-right readout: how the feed is doing, or how fast usage is moving."""
    if stale:   # fetches have been failing for a while — the numbers are old
        return "! STALE", THEME.warn
    arrow = "▼" if m.easing else "▲"
    trend = "EASING" if m.easing else "RISING"
    return f"Δ+{round(m.delta)} {arrow} {trend}", THEME.ok if m.easing else THEME.warn


def notice(feed):
    """The one dead end this feed has: a key that will not come back on its own.
    Stale numbers would be a lie, so say it and point at the fix."""
    if feed.auth:
        return "! SESSION EXPIRED", "re-save the key in the panel", ":8787 -> claude"
    return None


_SCREEN = None


def screen():
    """This module's Screen, built on first use.

    Lazily, because `stream_usage` imports both screen modules and a Screen owns
    a Feed and a burst channel — constructing those at import time would spend
    them on whichever screen isn't being shown.
    """
    global _SCREEN
    if _SCREEN is None:
        _SCREEN = bs.Screen(
            theme=THEME, mascot=draw_mascot, badge=badge, notice=notice,
            feed=bs.Feed("claude", cu.burn_model, REFETCH_SECS,
                         auth=(cu.AuthError,), stale_after=STALE_AFTER),
            gif=bs.GifPick("claude_gif"))
    return _SCREEN


def main():
    logs.setup("claude")
    bs.run([screen()], bs.parse_host(sys.argv[1:]), label="claude")


if __name__ == "__main__":
    main()
