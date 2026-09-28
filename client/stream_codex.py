#!/usr/bin/env python3
"""Codex usage on the SmallTV: the burn monitor in Codex colors.

The same screen as `stream_claude` — burn histogram over the fixed
reset-to-reset window, ghost bars for the projection, RUNOUT racing RESET IN, a
weekly segment bar — rendered from a cool teal palette with a pixel-art terminal
in the mascot box instead of the Claude creature. The layout lives in
`burnscreen.Screen`; this file supplies only the palette, the terminal, how
the header reads, and the feed.

    python stream_codex.py [--host IP]

Data: `codexusage` — chatgpt.com's Codex usage API, authenticated by the
session cookie saved in the panel, so the screen shows the account's usage no
matter which machine is actually running Codex. With no cookie saved it falls
back to the local session logs, and then the badge reads IDLE + how long since
Codex last logged anything, rather than a trend that feed can't actually see.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import burnscreen as bs                                     # noqa: E402
import codexusage as cx                                     # noqa: E402
import logs                                                 # noqa: E402
from burnscreen import GBOX, box, hhmm                      # noqa: E402

REFETCH_SECS = 10.0       # poll cadence (fetch runs off-thread so it never stutters)
STALE_AFTER = 6           # consecutive fetch errors (~1 min) before the screen says so

# Palette: the same roles as the Claude screen, swung from warm coral-on-brown
# to cool teal-on-slate. OK is a lime rather than a sage so the "safe" verdict
# stays legible next to the teal usage bars instead of blending into them.
THEME = bs.Theme(
    title="codex",
    bg=(8, 10, 12),
    fg=(228, 238, 240),       # #E4EEF0  just-now bar / values
    use=(26, 170, 134),       # #1AAA86  session usage
    ok=(150, 205, 110),       # #96CD6E  conclusion (safe)
    warn=(226, 168, 58),      # #E2A83A  pace exceeded
    crit=(212, 70, 66),       # #D44642  limit reached
    gray=(104, 118, 122),     # muted labels
    track=(46, 58, 62),       # window track
    dim=(24, 32, 35),
)

# terminal mascot colors
TM_SHELL = (14, 20, 22)     # the little screen inside the frame
TM_BAR = (26, 36, 39)       # its title bar

# The "code" the terminal types, as (indent, [(cells, role), ...]) per line.
# Roles map to theme colors below; glyphs are 2px blocks on a 3px pitch, which
# is as much as 58x44 can carry and still read as text from across a room.
TERM_SCRIPT = [
    (0, [(1, "p"), (4, "k"), (5, "t")]),
    (2, [(3, "k"), (6, "t"), (2, "d")]),
    (2, [(5, "t"), (4, "k")]),
    (0, [(1, "p"), (3, "k"), (6, "t")]),
    (2, [(4, "k"), (3, "t"), (5, "d")]),
    (2, [(7, "t"), (2, "k")]),
]
TERM_CPS = 6.0        # cells typed per second — also the screen's bandwidth
                      # dial, since a still frame costs nothing to push
TERM_PAUSE = 12       # cells' worth of dwell before the script loops
TERM_ROWS = 4         # visible lines
CELL, GLYPH, ROWH = 3.0, 2.0, 8.0


def _term_roles():
    return {"p": THEME.fg, "k": THEME.use, "t": THEME.gray, "d": THEME.track}


def draw_terminal(d, t):
    """A pixel-art terminal that types itself out under a blinking block cursor.

    Codex's mascot is its prompt, so the box runs a tiny shell: a framed screen
    with a title bar, four rows of block "code" that fill in a cell at a time,
    and a cursor riding the end of the line being written. It scrolls once the
    script runs past four lines and loops after a short dwell.
    """
    x0, y0, x1, y1 = GBOX
    box(d, x0, y0, x1, y1, THEME.use)                   # frame
    box(d, x0 + 1, y0 + 1, x1 - 1, y1 - 1, TM_SHELL)    # screen
    box(d, x0 + 1, y0 + 1, x1 - 1, y0 + 7, TM_BAR)      # title bar
    for i, col in enumerate((THEME.crit, THEME.warn, THEME.ok)):
        cx = x0 + 5 + i * 5
        box(d, cx, y0 + 3, cx + 1.5, y0 + 4.5, col)

    total = sum(sum(n for n, _ in toks) for _, toks in TERM_SCRIPT)
    typed = (t * TERM_CPS) % (total + TERM_PAUSE)
    roles = _term_roles()
    # Which line is being written, and how far into it.
    cur, done = 0, 0
    for i, (_, toks) in enumerate(TERM_SCRIPT):
        n = sum(k for k, _ in toks)
        if typed < done + n:
            cur = i
            break
        done += n
        cur = i + 1
    cur = min(cur, len(TERM_SCRIPT) - 1)
    in_line = max(0.0, typed - done)

    lx, ty = x0 + 3, y0 + 11
    end_x, end_y = lx, ty
    for row in range(TERM_ROWS):
        idx = cur - (TERM_ROWS - 1) + row
        if idx < 0 or idx >= len(TERM_SCRIPT):
            continue
        indent, toks = TERM_SCRIPT[idx]
        budget = in_line if idx == cur else 1e9
        x = lx + indent * CELL
        y = ty + row * ROWH
        for n, role in toks:
            for c in range(n):
                if budget <= 0:
                    break
                box(d, x, y, x + GLYPH, y + 1.5, roles[role])
                x += CELL
                budget -= 1
            x += CELL * 0.6          # word gap
            if budget <= 0:
                break
        if idx == cur:
            end_x, end_y = x, y

    if (t * 2.0) % 1.0 < 0.6:       # block cursor riding the line being written
        box(d, end_x, end_y - 1, end_x + GLYPH, end_y + 3, THEME.fg)


def _age(secs):
    """How long since Codex last logged anything, in the coarsest unit that is
    still informative: minutes for a break, h:mm for a day, days beyond that."""
    if secs < 90 * 60:
        return f"{int(secs // 60)}m"
    if secs < 48 * 3600:
        return hhmm(secs / 3600.0)
    return f"{int(secs // 86400)}d"


def badge(m, stale=False):
    """The top-right readout. Only a fresh reading licenses a trend: on the log
    fallback with Codex idle there is no new data to have a trend about, so say
    IDLE and for how long rather than reprinting the last delta as if it were
    happening now."""
    if stale:   # fetches have been failing for a while — the numbers are old
        return "! STALE", THEME.warn
    if not m.live:
        return f"IDLE {_age(m.age_s)}", THEME.gray
    arrow = "▼" if m.easing else "▲"
    trend = "EASING" if m.easing else "RISING"
    return f"Δ+{round(m.delta)} {arrow} {trend}", THEME.ok if m.easing else THEME.warn


def sub(m):
    """The word next to LIMIT 100%: which limit this is. A scoped limit's name is
    a full model id ("GPT-5.3-Codex-Spark") and the header has room for about
    nine characters, so keep the tail — that is the part that distinguishes it."""
    return m.limit_name.split("-")[-1] if m.limit_name else m.plan


def notice(feed):
    """The two dead ends this feed has: a cookie that needs re-saving, and no
    readable source at all. Either way, say it rather than hold an old frame."""
    if feed.auth:
        return "! SESSION EXPIRED", "re-save the cookie in the panel", ":8787 -> codex"
    if feed.nodata and feed.model is None:
        return "! NO CODEX DATA", "save a session cookie in the panel", ":8787 -> codex"
    return None


_SCREEN = None


def screen():
    """This module's Screen, built on first use — see stream_claude.screen()."""
    global _SCREEN
    if _SCREEN is None:
        _SCREEN = bs.Screen(
            theme=THEME, mascot=draw_terminal, badge=badge, notice=notice, sub=sub,
            waiting="reading...",
            feed=bs.Feed("codex", cx.burn_model, REFETCH_SECS, auth=(cx.AuthError,),
                         nodata=(cx.NoDataError,), stale_after=STALE_AFTER,
                         note=lambda m: f"src={m.source} age={_age(m.age_s)}"
                                        f"{' rolled' if m.rolled else ''}"),
            gif=bs.GifPick("codex_gif"))
    return _SCREEN


def main():
    logs.setup("codex")
    bs.run([screen()], bs.parse_host(sys.argv[1:]), label="codex")


if __name__ == "__main__":
    main()
