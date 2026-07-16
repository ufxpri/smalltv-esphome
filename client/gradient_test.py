"""Colour-resolution sweep: show a smooth full-screen gradient and step the
number of colour levels/channel DOWN over time, labelled on screen. Call out
the level where you first see banding — anything finer than that is wasted
colour space we can drop to save bandwidth.

    python gradient_test.py [device_ip] [--dither]
"""
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from smalltv_stream import Streamer, to565   # noqa

HOST = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "smalltv-ultra.local"
DITHER = "--dither" in sys.argv
W = H = 240
HOLD = 3.0                         # seconds per level
# transport is RGB565, so 32 levels (R/B 5-bit) is the finest the device can show
LEVELS = [32, 24, 20, 16, 13, 11, 9, 8, 7, 6, 5, 4, 3, 2]

BAYER = np.array([[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
                  [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
                  [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
                  [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]]) / 64.0
BT = np.tile(BAYER, (H // 8, W // 8))[:, :, None]


def base_gradient():
    a, b = np.array([8, 10, 34]), np.array([255, 150, 60])   # navy -> warm orange
    t = np.linspace(0, 1, H)[:, None, None]
    return np.repeat(a + (b - a) * t, W, axis=1)             # (H,W,3) float


def quantize(base, levels):
    step = 255.0 / (levels - 1)
    x = base + (BT - 0.5) * step if DITHER else base
    q = np.clip(np.round(x / step), 0, levels - 1) * step
    return q.astype(np.uint8)


def to_frame(rgb, levels):
    im = Image.fromarray(rgb)
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 30)
    except Exception:
        f = ImageFont.load_default()
    d.rectangle([0, 0, W, 40], fill=(0, 0, 0))
    d.text((10, 5), f"levels/ch: {levels}", font=f, fill=(255, 255, 255))
    return to565(im)


def main():
    base = base_gradient()
    frames = [(lv, to_frame(quantize(base, lv), lv)) for lv in LEVELS]
    s = Streamer(HOST, 6789)
    s.refresh_k = 0
    s.connect()
    print(f"sweeping levels {LEVELS}  (dither={DITHER}), {HOLD}s each — say when banding appears", flush=True)
    while True:
        for lv, f in frames:
            print(f"▶ levels/ch = {lv}", flush=True)
            s.prev = None
            t0 = time.time()
            while time.time() - t0 < HOLD:
                s.push565(f)
                time.sleep(0.5)


if __name__ == "__main__":
    main()
