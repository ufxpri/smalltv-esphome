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
import io
import json
import math
import os
import socket
import struct
import sys
import time

import numpy as np
import psutil
from PIL import Image, ImageDraw, ImageFilter, ImageFont

TELEM_DIR = "/tmp/smalltv"


class Telemetry:
    """Writes a live mirror frame + patch grid + stats for the control panel.
    Throttled to ~5 Hz; enabled via the SMALLTV_TELEMETRY env var."""

    def __init__(self, source):
        os.makedirs(TELEM_DIR, exist_ok=True)
        self.source = source
        self.last_write = 0.0
        self._recent = []                      # (time, bytes) over a short window

    def record(self, cur, changed_grid, blits, nbytes):
        now = time.time()
        self._recent.append((now, nbytes))
        self._recent = [(t, b) for t, b in self._recent if now - t < 2.0]
        if now - self.last_write < 0.2:
            return
        self.last_write = now
        rt = [t for t, _ in self._recent]
        fps = (len(rt) - 1) / (rt[-1] - rt[0]) if len(rt) > 1 and rt[-1] > rt[0] else 0.0
        span = max(now - self._recent[0][0], 0.01)
        kbps = sum(b for _, b in self._recent) / span / 1024.0

        a = cur.astype(np.uint32)              # RGB565 -> RGB888 for the preview
        rgb = np.stack([((a >> 11) & 0x1F) << 3, ((a >> 5) & 0x3F) << 2, (a & 0x1F) << 3], 2).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(rgb).save(buf, "JPEG", quality=70)
        self._atomic(TELEM_DIR + "/frame.jpg", buf.getvalue(), "wb")

        gh, gw = changed_grid.shape
        stat = {"source": self.source, "fps": round(fps, 1), "blits": blits,
                "kbps": round(kbps), "gw": gw, "gh": gh,
                "grid": changed_grid.astype(np.uint8).flatten().tolist(), "ts": now}
        self._atomic(TELEM_DIR + "/stat.json", json.dumps(stat), "w")

    @staticmethod
    def _atomic(path, data, mode):
        tmp = path + ".tmp"
        with open(tmp, mode) as f:
            f.write(data)
        os.replace(tmp, path)

HOST = sys.argv[1] if len(sys.argv) > 1 else "smalltv-ultra.local"
PORT = 6789
W = H = 240
SS = 2                       # supersample factor for anti-aliasing
TW, TH = 12, 12              # tile grid — small patches fit motion tightly (measured
                             # ~1.7x more fps than 40x24, still under device per-patch overhead)
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


def render(cpu, t):
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


def to565(img, dither=False):
    a = np.asarray(img)
    if dither and a.shape[:2] == _BT.shape:                 # dither 888 -> 565 (kills gradient banding)
        d = (_BT[:, :, None] - 0.5) * np.array([8.0, 4.0, 8.0], np.float32)  # step: R/B 5-bit, G 6-bit
        a = np.clip(a.astype(np.float32) + d, 0, 255)
    a = a.astype(np.uint16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)   # (H,W) uint16


# RGB565 palette for the dirty-patch debug overlay (cycles per patch)
DBG_PALETTE = [0xF800, 0x07E0, 0x001F, 0xFFE0, 0xF81F, 0x07FF, 0xFFFF, 0xFD20]

# Intra-refresh: also resend this many patches per frame in raster order, cycling
# through the whole screen. Heals static tiles that were dropped on connect / in
# the initial burst (they'd otherwise never be resent). ~full sweep every
# (tiles / REFRESH_K) frames.
REFRESH_K = 12

# Manual colour depth / dithering, live-controlled via this file by the panel.
MODE_FILE = TELEM_DIR + "/mode.json"
_BAYER = np.array([[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
                   [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
                   [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
                   [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]]) / 64.0
_BT = np.tile(_BAYER, (H // 8, W // 8))


def encode_tile(tile565, x, y, bits, dither):
    """Bytes for one tile: RGB565 (2 B/px) or dithered RGB332 (1 B/px)."""
    if bits != 8:
        return tile565.astype(">u2").tobytes()
    h, w = tile565.shape
    r = ((tile565 >> 11) & 0x1F).astype(np.float32) / 31.0
    g = ((tile565 >> 5) & 0x3F).astype(np.float32) / 63.0
    b = (tile565 & 0x1F).astype(np.float32) / 31.0
    if dither:
        d = _BT[y:y + h, x:x + w]
        r = r + (d - 0.5) / 7.0
        g = g + (d - 0.5) / 7.0
        b = b + (d - 0.5) / 3.0
    r3 = np.clip(np.round(r * 7), 0, 7).astype(np.uint8)
    g3 = np.clip(np.round(g * 7), 0, 7).astype(np.uint8)
    b2 = np.clip(np.round(b * 3), 0, 3).astype(np.uint8)
    return ((r3 << 5) | (g3 << 2) | b2).astype(np.uint8).tobytes()


class Streamer:
    def __init__(self, host, port, tw=TW, th=TH, debug=False):
        self.host, self.port = host, port
        self.tw, self.th = tw, th
        self.debug = debug
        self.color_bits = 16          # 16 = RGB565, 8 = RGB332 (manual, via MODE_FILE)
        self.dither = False
        self._mode_ts = 0.0
        self._dbg = 0
        self.refresh_k = REFRESH_K
        self.refresh_cursor = 0
        self.sock = None
        self.prev = None
        self.last_send = 0.0
        self.telem = Telemetry(os.path.basename(sys.argv[0])) if os.environ.get("SMALLTV_TELEMETRY") else None

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=6)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.prev = None
        print(f"connected to {self.host}:{self.port}")

    def _read_mode(self):
        now = time.time()
        if now - self._mode_ts < 0.5:
            return
        self._mode_ts = now
        try:
            with open(MODE_FILE) as f:
                m = json.load(f)
            new_bits = 8 if int(m.get("bits", 16)) == 8 else 16
            new_dither = bool(m.get("dither", False))
            if new_bits != self.color_bits or new_dither != self.dither:
                self.color_bits, self.dither = new_bits, new_dither
                self.prev = None       # repaint fully in the new format
        except Exception:
            pass

    def _send(self, data):
        self.sock.sendall(data)
        self.last_send = time.time()

    def push(self, img):
        return self.push565(to565(img, self.dither))

    def push565(self, cur):
        """Send changed patches (horizontally-merged into wider blits), plus a few
        'stale' patches per frame in raster order so any tile dropped on connect /
        in the initial burst is healed within one sweep."""
        self._read_mode()
        tw, th = self.tw, self.th
        prev = self.prev
        gw = (W + tw - 1) // tw
        gh = (H + th - 1) // th

        # decide which grid cells to send this frame
        if prev is None:
            send = np.ones((gh, gw), dtype=bool)                 # first frame: full paint
            changed = send
        else:
            send = np.zeros((gh, gw), dtype=bool)
            for gy in range(gh):
                ty, hh = gy * th, min(th, H - gy * th)
                for gx in range(gw):
                    tx, ww = gx * tw, min(tw, W - gx * tw)
                    if not np.array_equal(cur[ty:ty + hh, tx:tx + ww], prev[ty:ty + hh, tx:tx + ww]):
                        send[gy, gx] = True
            changed = send.copy()                                # motion grid (before sweep)
            # intra-refresh: also resend K patches in raster order, cycling
            total = gh * gw
            for _ in range(min(self.refresh_k, total)):
                gy, gx = divmod(self.refresh_cursor, gw)
                send[gy, gx] = True
                self.refresh_cursor = (self.refresh_cursor + 1) % total

        # emit: merge horizontally-adjacent send cells into one blit (<=1024 px)
        sent = 0
        buf = bytearray()
        max_px = 1024
        for gy in range(gh):
            ty, hh = gy * th, min(th, H - gy * th)
            max_run_w = max(tw, max_px // hh)
            gx = 0
            while gx < gw:
                if not send[gy, gx]:
                    gx += 1
                    continue
                run_x = gx * tw
                run_w = min(tw, W - run_x)
                gx += 1
                while gx < gw and send[gy, gx] and run_w + min(tw, W - gx * tw) <= max_run_w:
                    run_w += min(tw, W - gx * tw)
                    gx += 1
                rect = cur[ty:ty + hh, run_x:run_x + run_w]
                if self.debug:
                    rect = rect.copy()
                    c = DBG_PALETTE[self._dbg % len(DBG_PALETTE)]
                    self._dbg += 1
                    rect[0, :] = c; rect[-1, :] = c; rect[:, 0] = c; rect[:, -1] = c
                wfield = run_w | 0x8000 if self.color_bits == 8 else run_w
                buf += struct.pack(">HHHH", run_x, ty, wfield, hh)
                buf += encode_tile(rect, run_x, ty, self.color_bits, self.dither)
                sent += 1
        if buf:
            self._send(bytes(buf))
        elif time.time() - self.last_send > 1.5:
            self._send(struct.pack(">HHHH", 0, 0, 0, 0))   # heartbeat (only if refresh_k=0)
        if self.telem:
            self.telem.record(cur, changed, sent, len(buf))
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
                n = s.push(render(cpu, t))
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
