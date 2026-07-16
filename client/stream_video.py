#!/usr/bin/env python3
"""Play a video file on the SmallTV over the framebuffer stream.

ffmpeg decodes + scales to 240x240; frames play at real-time speed with
frame-dropping (the stream only sustains a few fps for full-screen change).
Pass `--debug` to outline each transmitted patch in cycling colours.

    python stream_video.py <file> [device_ip] [loops] [--debug]
"""
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from smalltv_stream import Streamer   # noqa

W = H = 240
EXTRACT_FPS = 15


def decode_frames(path):
    """Decode the whole clip to a (n, H, W) uint16 RGB565 array via ffmpeg."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", path,
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={EXTRACT_FPS}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        sys.exit("ffmpeg failed: " + p.stderr.decode()[-400:])
    fsize = W * H * 3
    raw = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(raw) // fsize
    raw = raw[: n * fsize].reshape(n, H, W, 3).astype(np.uint16)
    r, g, b = raw[..., 0], raw[..., 1], raw[..., 2]
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    debug = "--debug" in sys.argv
    path = args[0]
    host = args[1] if len(args) > 1 else "smalltv-ultra.local"
    loops = int(args[2]) if len(args) > 2 else 3

    print("decoding video ...", flush=True)
    frames = decode_frames(path)
    n = len(frames)
    print(f"{n} frames @ {EXTRACT_FPS}fps ({n / EXTRACT_FPS:.1f}s)", flush=True)

    s = Streamer(host, 6789, debug=debug)
    s.connect()
    for loop in range(loops):
        t0 = time.time()
        shown, last = 0, -1
        while True:
            idx = int((time.time() - t0) * EXTRACT_FPS)
            if idx >= n:
                break
            if idx != last:
                s.push565(frames[idx])   # blocks on the pipe -> drops frames to stay real-time
                shown += 1
                last = idx
        el = time.time() - t0
        print(f"loop {loop + 1}/{loops}: {shown}/{n} frames  -> {shown / el:.1f} fps", flush=True)
    print("done")


if __name__ == "__main__":
    main()
