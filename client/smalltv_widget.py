#!/usr/bin/env python3
"""Entry point for the SmallTV tray/menu-bar widget.

Run from source:   python smalltv_widget.py

The widget supervises the control panel server; the panel is where the UI lives.

It also dispatches `--run <script.py> [args...]`, which is how a *frozen* build
starts the panel and the stream sources: a PyInstaller bundle ships no
interpreter, so those processes have to be this same executable re-entering
itself. From source they are launched as plain scripts and this path is unused.
See stream.command().

This file lives next to the ``smalltv``/``widget`` packages and the ``config``,
``stream``, ``control_panel`` and ``stream_*`` modules so they all import
cleanly, both from source and when frozen.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Modules the --run dispatcher can enter. Importing them here (rather than by
# name at runtime) is also what tells PyInstaller to bundle them.
RUNNABLE = (
    "control_panel.py",
    "smalltv_stream.py",
    "stream_stocks.py",
    "stream_sectors.py",
    "stream_gif.py",
    "stream_video.py",
)


def _run(script, argv):
    if script not in RUNNABLE:
        sys.exit(f"--run: unknown script '{script}'. known: {', '.join(RUNNABLE)}")
    # Make argv look exactly like a direct `python <script> ...` invocation:
    # these modules read sys.argv at import time.
    sys.argv = [script, *argv]
    mod = __import__(script[:-3])
    mod.main()


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--run":
        _run(sys.argv[2], sys.argv[3:])
        return
    from widget.app import main as widget_main
    widget_main()


if __name__ == "__main__":
    main()
