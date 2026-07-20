#!/usr/bin/env python3
"""S&P 500 sector heatmap on the SmallTV over the framebuffer stream.

The PC-rendered replacement for the on-device Sectors page: a 3x4 grid of the
11 SPDR sector ETFs plus SPY, each tile tinted by its daily % change.

    python stream_sectors.py [device_ip]
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import marketdata as md                                   # noqa: E402
from smalltv_stream import PORT, SS, H, Streamer, W, font, resolve_host  # noqa: E402

REFETCH_SECS = 60.0       # daily % changes; no reason to hammer Yahoo
FPS = 1.0

BG = (0, 0, 0)
UP, DOWN = (0, 227, 107), (255, 64, 96)          # DESIGN.md semantic up/down
TEXT, MUTED = (255, 255, 255), (154, 167, 180)
DIVIDER, FLAT = (42, 53, 66), (32, 38, 46)
CAP = 2.0                 # |%| at which a tile reaches full saturation

# grid: 3 cols x 4 rows inside an 8px gutter, below the title rule
GX, GY, GAP = 8, 32, 4
TW, TH = 72, 47


def _s(v):
    return int(round(v * SS))


def _tint(pct):
    """Dark base -> semantic colour, saturating at CAP. Sign picks the hue."""
    t = min(abs(pct) / CAP, 1.0)
    target = UP if pct >= 0 else DOWN
    k = 0.14 + 0.72 * t
    return tuple(int(round(FLAT[i] + (target[i] - FLAT[i]) * k)) for i in range(3))


def render(pcts):
    """pcts: {symbol: daily % change}; missing symbols render as a flat tile."""
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    d.text((_s(GX), _s(5)), "S&P SECTORS", font=font(14), fill=TEXT)
    # Breadth, not SPY: SPY already has its own tile, and how many sectors are
    # green is the one thing the grid can't be read at a glance for.
    sectors = [pcts[s] for s, _ in md.SECTORS if s != "SPY" and s in pcts]
    if sectors:
        up = sum(1 for v in sectors if v >= 0)
        x = 232
        for n, col in ((len(sectors) - up, DOWN), (up, UP)):
            txt = f"{n}▼" if col is DOWN else f"{n}▲"
            d.text((_s(x), _s(7)), txt, font=font(12), fill=col, anchor="ra")
            x -= d.textlength(txt, font=font(12)) / SS + 7
    d.line([(_s(GX), _s(26)), (_s(232), _s(26))], fill=DIVIDER, width=SS)

    for i, (sym, label) in enumerate(md.SECTORS):
        x = GX + (i % 3) * (TW + GAP)
        y = GY + (i // 3) * (TH + GAP)
        pct = pcts.get(sym)
        fill = FLAT if pct is None else _tint(pct)
        d.rounded_rectangle([_s(x), _s(y), _s(x + TW), _s(y + TH)],
                            radius=_s(6), fill=fill)
        cx = x + TW / 2
        d.text((_s(cx), _s(y + 8)), label, font=font(11), fill=TEXT, anchor="ma")
        txt = "--" if pct is None else f"{pct:+.2f}%"
        d.text((_s(cx), _s(y + 24)), txt, font=font(13),
               fill=TEXT if pct is not None else MUTED, anchor="ma")

    return img.resize((W, H), Image.LANCZOS)


def fetch_all():
    """Daily % for every sector, fetched in parallel. Missing on error."""
    syms = [s for s, _ in md.SECTORS]
    out = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for sym, res in zip(syms, ex.map(_safe_pct, syms)):
            if res is not None:
                out[sym] = res
    return out


def _safe_pct(sym):
    try:
        return md.fetch_pct(sym)
    except Exception as e:
        print(f"  {sym:6} error: {e}")
        return None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    s = Streamer(resolve_host(args[0] if args else None), PORT)
    pcts, fetched = {}, 0.0
    while True:
        try:
            s.connect()
            while True:
                frame_start = time.time()
                if frame_start - fetched >= REFETCH_SECS:
                    new = fetch_all()
                    if new:                       # keep the last good frame on a total failure
                        pcts = new
                        print(f"  {len(pcts)}/{len(md.SECTORS)} sectors  "
                              f"SPY {pcts.get('SPY', float('nan')):+.2f}%")
                    fetched = frame_start
                if pcts:
                    s.push(render(pcts))
                dt = (1.0 / FPS) - (time.time() - frame_start)
                if dt > 0:
                    time.sleep(dt)
        except OSError as e:
            print(f"\n[sectors] disconnected: {e}; retrying in 3s")
            try:
                if s.sock:
                    s.sock.close()
            except OSError:
                pass
            time.sleep(3)


if __name__ == "__main__":
    main()
