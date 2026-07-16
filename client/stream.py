#!/usr/bin/env python3
"""Switch what the SmallTV streams. Only one source runs at a time (the device
accepts a single stream client); `off` stops streaming so the device falls back
to its local clock page.

    python stream.py furnace          # CPU-load furnace
    python stream.py stickers         # OGQ sticker slideshow
    python stream.py video <file>     # play a video file
    python stream.py off              # stop streaming (-> local clock)
    python stream.py status           # show what's running
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, os.pardir, ".venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable
LOGDIR = os.path.expanduser("~/Library/Logs/SmallTVWidget")

SOURCES = {
    "furnace": "smalltv_stream.py",
    "stickers": "stream_gif.py",
    "video": "stream_video.py",
}
ALL = list(SOURCES.values())


def stop_all():
    for s in ALL:
        subprocess.run(["pkill", "-f", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def running():
    out = subprocess.run(["pgrep", "-fl", "|".join(ALL)], capture_output=True, text=True).stdout
    hits = [s for s in ALL if s in out]
    return hits


def start(source, extra):
    os.makedirs(LOGDIR, exist_ok=True)
    script = os.path.join(HERE, SOURCES[source])
    log = open(os.path.join(LOGDIR, f"{source}.log"), "a")
    env = {**os.environ, "SMALLTV_TELEMETRY": "1"}   # emit live monitor data for the panel
    subprocess.Popen([PY, script, *extra], stdout=log, stderr=subprocess.STDOUT,
                     start_new_session=True, cwd=HERE, env=env)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    extra = sys.argv[2:]

    if cmd == "status":
        r = running()
        print("streaming:", ", ".join(r) if r else "(none — device shows local clock)")
        return
    if cmd in ("off", "stop"):
        stop_all()
        print("stopped — device falls back to its local clock in a few seconds")
        return
    if cmd in SOURCES:
        if cmd == "video" and not extra:
            sys.exit("usage: python stream.py video <file>")
        stop_all()
        # video expects the file first; furnace/stickers take an optional host
        start(cmd, extra)
        print(f"now streaming: {cmd}")
        return
    sys.exit(f"unknown source '{cmd}'. options: {', '.join(SOURCES)}, off, status")


if __name__ == "__main__":
    main()
