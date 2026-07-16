#!/usr/bin/env python3
"""Cycle a folder of animated GIFs on the SmallTV over the framebuffer stream.

Each GIF's frames are composited onto white (stickers use transparency), then
streamed with the tile-diff pipeline at real-time speed (frame-dropping when the
pipe can't keep up). Each sticker shows for DISPLAY_SECS, looping, then the next.

    python stream_gif.py [gif_dir] [device_ip]
"""
import glob
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from smalltv_stream import Streamer, to565   # noqa

DEFAULT_GIF_DIR = __file__.rsplit("/", 1)[0] + "/gifs"
W = H = 240
DISPLAY_SECS = 4.0
BG = (0, 0, 0)          # composite transparent stickers onto black


def load_gif(path):
    """Return [(rgb565 frame, duration_s)] with transparency flattened onto BG."""
    im = Image.open(path)
    out = []
    for i in range(getattr(im, "n_frames", 1)):
        im.seek(i)
        dur = (im.info.get("duration") or 80) / 1000.0
        rgba = im.convert("RGBA").resize((W, H))
        canvas = Image.new("RGB", (W, H), BG)
        canvas.paste(rgba, (0, 0), rgba)
        out.append((to565(canvas), max(dur, 0.02)))
    return out


def play(s, frames):
    total = sum(d for _, d in frames)
    cum, acc = [], 0.0
    for _, d in frames:
        acc += d
        cum.append(acc)
    s.prev = None                       # clean full paint on sticker switch
    t0, last = time.time(), -1
    while time.time() - t0 < DISPLAY_SECS:
        tt = (time.time() - t0) % total
        idx = min(sum(1 for c in cum if c <= tt), len(frames) - 1)
        if idx != last:
            s.push565(frames[idx][0])
            last = idx
        else:
            time.sleep(0.005)


def main():
    argv = sys.argv[1:]
    pick = None
    if "--pick" in argv:
        j = argv.index("--pick")
        pick = argv[j + 1] if j + 1 < len(argv) else None
        del argv[j:j + 2]
    gif_dir = argv[0] if argv else DEFAULT_GIF_DIR
    host = argv[1] if len(argv) > 1 else "smalltv-ultra.local"

    paths = sorted(glob.glob(gif_dir + "/*.gif"))
    if pick:
        paths = [p for p in paths if pick in p.rsplit("/", 1)[-1]]
    if not paths:
        sys.exit(f"no GIFs in {gif_dir}" + (f" matching '{pick}'" if pick else ""))
    print(f"loading {len(paths)} GIFs ...", flush=True)
    gifs = [(p.rsplit("/", 1)[-1], load_gif(p)) for p in paths]

    s = Streamer(host, 6789)
    s.refresh_k = 4          # gifs already repaint most tiles; keep sweep light for heap headroom
    s.connect()
    i = 0
    while True:
        name, frames = gifs[i % len(gifs)]
        print(f"▶ {name} ({len(frames)} frames)", flush=True)
        play(s, frames)
        i += 1


if __name__ == "__main__":
    main()
