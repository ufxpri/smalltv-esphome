#!/usr/bin/env python3
"""Both usage screens on one device, taking turns.

The device accepts a single stream client, so "show Claude and Codex" can't be
two processes — it has to be one that holds the connection and swaps what it
draws. That is all this source is: it asks each module for its `Screen` and
hands the list to `burnscreen.run`, which owns the rotation. Each screen is
still drawn by its own `Screen.frame()`, the same one the standalone sources
use, so the rotating version cannot drift from them.

    python stream_usage.py [--host IP] [--rotate SECONDS]

A screen with nothing to draw — an expired key, a feed that has never answered —
is skipped while the other one is healthy, so one broken key doesn't cost you
half the time on a notice. If neither can draw, the rotation continues through
both, because then the notices *are* the information.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import burnscreen as bs                                     # noqa: E402
import config as cfg_mod                                    # noqa: E402
import logs                                                 # noqa: E402
import stream_claude as claude                              # noqa: E402
import stream_codex as codex                                # noqa: E402

MODULES = (claude, codex)


def parse_rotate(argv):
    """`--rotate SECONDS`, else the panel's saved interval."""
    for i, a in enumerate(argv):
        if a == "--rotate" and i + 1 < len(argv):
            try:
                return max(bs.MIN_ROTATE, float(argv[i + 1]))
            except ValueError:
                break
    return max(bs.MIN_ROTATE, cfg_mod.load().get("usage_rotate") or 20.0)


def main():
    logs.setup("usage")
    bs.run([m.screen() for m in MODULES], bs.parse_host(sys.argv[1:]),
           rotate=parse_rotate(sys.argv[1:]), label="usage")


if __name__ == "__main__":
    main()
