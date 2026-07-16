#!/usr/bin/env python3
"""PC-rendered framebuffer streamer for the SmallTV (hybrid streaming mode).

Renders a rich 240x240 image on the Mac (real fonts, Korean, soft glow — things
the ESP8266 can't draw) and streams only the CHANGED tiles to the device's TCP
framebuffer server (port 6789). The device blits them; when this stops, the
device falls back to its local clock page.

    pip install pillow numpy psutil
    python smalltv_stream.py [device_ip]

Protocol per tile: [u16 x, y, w, h  big-endian][w*h*2 bytes RGB565 big-endian].
A zero-size header (0,0,0,0) is a heartbeat that keeps the stream "active".
"""
import math
import socket
import struct
import sys
import time

import numpy as np
import psutil
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HOST = sys.argv[1] if len(sys.argv) > 1 else "smalltv-ultra.local"
PORT = 6789
W = H = 240
SS = 2                       # supersample factor for anti-aliasing
TW, TH = 40, 24              # tile grid (960 px/tile <= device max 1024)
FPS_MIN, FPS_MAX = 2, 12     # dynamic frame rate: calm when idle, frantic when busy
CLAUDE = (217, 119, 87)      # Anthropic coral

_FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"


def font(sz):
    try:
        return ImageFont.truetype(_FONT, sz * SS)
    except Exception:
        return ImageFont.load_default()


# fire palette lookup: intensity 0..255 -> (r,g,b), stoked hotter with `load`
def fire_lut(load):
    x = np.linspace(0, 1, 256)
    # base -> tip color stops shift toward white as load rises
    hot = min(1.0, 0.35 + load)
    r = np.clip(x * 3.2, 0, 1)
    g = np.clip((x - 0.18) * 2.4 * hot, 0, 1)
    b = np.clip((x - 0.72) * 3.5 * hot, 0, 1)
    lut = np.stack([r, g, b], axis=1) * 255
    return lut.astype(np.uint8)


def status_for(cpu):
    if cpu < 8:
        return "대기 중", (120, 140, 155)
    if cpu < 35:
        return "예열 중", (255, 176, 0)
    if cpu < 65:
        return "열일 중", (255, 138, 0)
    if cpu < 88:
        return "과열!!", (255, 90, 42)
    return "멜트다운!!", (255, 60, 40)


def render(cpu, ram, t):
    """Return a 240x240 RGB PIL image of the furnace at the given CPU load."""
    load = cpu / 100.0
    w, h = W * SS, H * SS
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)

    # background: subtle warm vertical gradient
    top = np.array([10, 8, 14]); bot = np.array([26, 14, 10])
    grad = (top[None, :] + (bot - top)[None, :] * (np.arange(h)[:, None] / h)).astype(np.uint8)
    img.paste(Image.fromarray(np.repeat(grad[:, None, :], w, axis=1)), (0, 0))
    d = ImageDraw.Draw(img)

    # header
    d.text((14 * SS, 8 * SS), "CLAUDE CODE", font=font(13), fill=(231, 216, 201))
    st, sc = status_for(cpu)
    d.text((226 * SS, 8 * SS), st, font=font(15), fill=sc, anchor="ra")

    # furnace body (metallic vertical gradient inside a rounded rect)
    fx0, fy0, fx1, fy1 = 62 * SS, 50 * SS, 178 * SS, 188 * SS
    body = np.linspace(58, 26, fy1 - fy0).astype(np.uint8)
    bodyimg = np.stack([body * 0 + 42, body * 0 + 33, body * 0 + 28], axis=1)
    bodyimg = np.repeat(bodyimg[:, None, :], fx1 - fx0, axis=1)
    shade = (body[:, None] / 58.0)
    bodyimg = (bodyimg * (0.6 + 0.4 * shade[:, :, None])).astype(np.uint8)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([fx0, fy0, fx1, fy1], radius=14 * SS, fill=255)
    img.paste(Image.fromarray(bodyimg), (fx0, fy0), mask.crop((fx0, fy0, fx1, fy1)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([fx0, fy0, fx1, fy1], radius=14 * SS, outline=(14, 10, 8), width=2 * SS)
    # rivets + door rim
    for rx, ry in [(72, 60), (168, 60), (72, 178), (168, 178)]:
        d.ellipse([rx * SS - 2 * SS, ry * SS - 2 * SS, rx * SS + 2 * SS, ry * SS + 2 * SS], fill=CLAUDE)
    dr = [80 * SS, 66 * SS, 160 * SS, 176 * SS]
    d.rounded_rectangle(dr, radius=8 * SS, fill=(8, 5, 4), outline=CLAUDE, width=2 * SS)

    # ---- fire: draw a bright mask, blur it, colorize with the fire LUT ----
    base_y = 172
    max_h = 8 + load * 96
    flame = Image.new("L", (w, h), 0)
    fd = ImageDraw.Draw(flame)
    flicker = 2.5 + load * 9.0        # flames flicker slowly when idle, fast when busy
    for i in range(6):
        fx = (90 + i * 12)
        ph = t * flicker + i * 1.7
        fh = max_h * (0.6 + 0.4 * math.sin(ph))
        sway = 4 * math.sin(ph * 0.7)
        tipx = (fx + sway) * SS
        poly = [(fx - 7) * SS, base_y * SS,
                (fx + 7) * SS, base_y * SS,
                tipx, (base_y - fh) * SS]
        fd.polygon(poly, fill=200)
        fd.polygon([(fx - 3) * SS, base_y * SS, (fx + 3) * SS, base_y * SS,
                    tipx, (base_y - fh * 0.6) * SS], fill=255)
    flame = flame.filter(ImageFilter.GaussianBlur(radius=3 * SS))
    # clip fire to the door interior
    doormask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(doormask).rounded_rectangle(dr, radius=8 * SS, fill=255)
    fa = np.asarray(flame, dtype=np.uint8) & np.asarray(doormask, dtype=np.uint8)
    lut = fire_lut(load)
    fire_rgb = lut[fa]                          # (h,w,3)
    # additive composite over the door
    base = np.asarray(img, dtype=np.uint16)
    out = np.clip(base + fire_rgb, 0, 255).astype(np.uint8)
    img = Image.fromarray(out)
    d = ImageDraw.Draw(img)

    # readouts
    d.text((120 * SS, 196 * SS), f"CPU {cpu:.0f}%", font=font(20), fill=(235, 224, 210), anchor="ma")
    # load bar
    bx0, bx1, by = 22 * SS, 218 * SS, 226 * SS
    d.rounded_rectangle([bx0, by, bx1, by + 6 * SS], radius=3 * SS, fill=(36, 26, 20))
    bw = bx0 + (bx1 - bx0) * load
    if bw > bx0 + 4 * SS:
        d.rounded_rectangle([bx0, by, bw, by + 6 * SS], radius=3 * SS, fill=sc)

    if SS != 1:
        img = img.resize((W, H), Image.LANCZOS)
    return img


def to565(img):
    a = np.asarray(img, dtype=np.uint16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)   # (H,W) uint16


class Streamer:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.sock = None
        self.prev = None
        self.last_send = 0.0

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=6)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.prev = None
        print(f"connected to {self.host}:{self.port}")

    def _send(self, data):
        self.sock.sendall(data)
        self.last_send = time.time()

    def push(self, img):
        cur = to565(img)
        sent = 0
        buf = bytearray()
        for ty in range(0, H, TH):
            for tx in range(0, W, TW):
                tile = cur[ty:ty + TH, tx:tx + TW]
                if self.prev is not None and np.array_equal(tile, self.prev[ty:ty + TH, tx:tx + TW]):
                    continue
                buf += struct.pack(">HHHH", tx, ty, TW, TH)
                buf += tile.astype(">u2").tobytes()
                sent += 1
        if buf:
            self._send(bytes(buf))
        elif time.time() - self.last_send > 1.5:
            self._send(struct.pack(">HHHH", 0, 0, 0, 0))   # heartbeat
        self.prev = cur
        return sent


def main():
    s = Streamer(HOST, PORT)
    psutil.cpu_percent()
    while True:
        try:
            s.connect()
            t0 = time.time()
            while True:
                frame_start = time.time()
                t = frame_start - t0
                cpu = psutil.cpu_percent()
                ram = psutil.virtual_memory().percent
                img = render(cpu, ram, t)
                n = s.push(img)
                # dynamic fps: scale with CPU load (idle -> FPS_MIN, busy -> FPS_MAX)
                fps = FPS_MIN + (cpu / 100.0) * (FPS_MAX - FPS_MIN)
                print(f"cpu {cpu:5.1f}%  fps {fps:4.1f}  tiles {n:2d}   ", end="\r", flush=True)
                dt = (1.0 / fps) - (time.time() - frame_start)
                if dt > 0:
                    time.sleep(dt)
        except (OSError, socket.error) as e:
            print(f"\n[stream] disconnected: {e}; retrying in 3s")
            try:
                if s.sock:
                    s.sock.close()
            except Exception:
                pass
            time.sleep(3)


if __name__ == "__main__":
    main()
