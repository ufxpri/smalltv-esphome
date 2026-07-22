#!/usr/bin/env python3
"""Live candlestick chart on the SmallTV over the framebuffer stream.

The PC-rendered replacement for the old on-device Stocks page. Three stacked
panels: price (candles + MA5/MA20 + Bollinger bands), volume, and RSI(14).

    python stream_stocks.py [TICKER ...] [--host IP] [--rotate SECS]

Pass several tickers to cycle them; each shows for --rotate seconds (default
15). Tickers are anything Yahoo knows: AAPL, MSFT, 005930.KS, BTC-USD ...
"""
import os
import sys
import time

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import marketdata as md                                   # noqa: E402
from smalltv_stream import CLAUDE, PORT, SS, H, Streamer, W, font, resolve_host  # noqa: E402

REFETCH_SECS = 6.0        # Yahoo refresh; the chart itself barely moves between ticks
ROTATE_SECS = 15.0        # per ticker, when several are given
FPS = 2.0                 # unchanged frames cost ~nothing (tile diff), but keep the
                          # stream's intra-refresh sweep healing dropped tiles

BG = (0, 0, 0)
UP, DOWN = (0, 227, 107), (255, 64, 96)
MUTED, TEXT = (154, 167, 180), (255, 255, 255)
MA5_C, MA20_C, BB_C = (46, 134, 255), (255, 149, 0), (80, 96, 110)
RSI_C, SEP = (192, 96, 224), (27, 34, 43)

# panel geometry, in final 240x240 units
X0, CW = 6, 228
PY, PH = 50, 110          # price panel
VY, VH = 168, 18          # volume
RY, RH = 194, 38          # RSI

BADGES = {"REGULAR": ("LIVE", (0, 162, 75)), "PRE": ("PRE", (229, 148, 0)),
          "POST": ("AFTER", (59, 107, 229)), "CLOSED": ("CLOSED", (90, 100, 114))}

# Readable header labels for the tickers the control panel offers as presets.
# Everything else falls back to Yahoo's shortName (Quote.name), then the symbol.
NAMES = {"^KS11": "코스피", "005930.KS": "삼성전자", "000660.KS": "SK하이닉스",
         "^IXIC": "나스닥", "^GSPC": "S&P 500", "BTC-USD": "비트코인"}


def _s(v):
    return int(round(v * SS))


def _clip(d, text, f, max_w):
    """Trim `text` (final-px width budget `max_w`) with an ellipsis so a long name
    never runs into the price on the other side of the header."""
    if d.textlength(text, font=f) / SS <= max_w:
        return text
    while text and d.textlength(text + "…", font=f) / SS > max_w:
        text = text[:-1]
    return text + "…" if text else text


def _price_extent(candles, extras, wick_room=0.6):
    """The price window the chart scales to.

    Bodies set the range; wicks may only push it `wick_room` of a body-span
    further. Thin pre/post-market candles routinely carry a stray print — a wick
    several percent off a flat body — and scaling to it squashes the real action
    into a few pixels. Such a wick is still drawn, clipped to the panel edge, so
    the spike stays visible without costing the rest of the chart.
    """
    bodies = [v for o, _, _, c in candles for v in (o, c)]
    blo, bhi = min(bodies), max(bodies)
    span = max(bhi - blo, 1e-9)
    floor, ceil_ = blo - span * wick_room, bhi + span * wick_room
    # Out-of-range wicks are dropped from the scale, not clamped into it: a
    # clamped one would still be the extreme and would leave the panel's whole
    # bottom third empty.
    lo = min([l for _, _, l, _ in candles if l >= floor] or [blo])
    hi = max([h for _, h, _, _ in candles if h <= ceil_] or [bhi])
    for v in extras:                      # overlays must stay on-panel
        lo, hi = min(lo, v), max(hi, v)
    pad = (hi - lo) * 0.04
    return lo - pad, hi + pad


def _fit(box, lo, hi):
    """price -> y within a panel (higher price = lower y), clamped to the panel."""
    y0, height = box
    span = max(hi - lo, 1e-9)

    def y(v):
        return _s(y0 + min(max((hi - v) / span, 0.0), 1.0) * height)
    return y


def _label(d, xy, text, fill, size=11, anchor="ra"):
    """Text over a small opaque backing, so readouts stay legible over the chart."""
    f = font(size)
    box = d.textbbox(xy, text, font=f, anchor=anchor)
    d.rectangle([box[0] - _s(2), box[1] - _s(1), box[2] + _s(2), box[3] + _s(1)], fill=BG)
    d.text(xy, text, font=f, fill=fill, anchor=anchor)


def render(q, idx=0, total=1):
    img = Image.new("RGB", (W * SS, H * SS), BG)
    d = ImageDraw.Draw(img)

    # ---- header: readable name + raw symbol + price, then badge + day swing ----
    price_txt = f"{q.price:,.2f}"
    d.text((_s(234), _s(2)), price_txt, font=font(19), fill=TEXT, anchor="ra")
    d.text((_s(234), _s(30)), f"{q.pct:+.2f}%", font=font(12),
           fill=UP if q.pct >= 0 else DOWN, anchor="ra")

    # The name reads; the symbol identifies. Line 1 is the readable name (big),
    # clipped to whatever the price leaves; the raw symbol drops to line 2 beside
    # the badge — `005930.KS` alone up top was unreadable.
    disp = NAMES.get(q.symbol) or q.name or q.symbol
    dots_w = (total * 7 + 6) if total > 1 else 0
    price_w = d.textlength(price_txt, font=font(19)) / SS
    name_max = 234 - price_w - 10 - dots_w - X0
    disp = _clip(d, disp, font(15), max(name_max, 30))
    d.text((_s(X0), _s(3)), disp, font=font(15), fill=TEXT)
    if total > 1:      # rotation dots trail the name
        nx = X0 + d.textlength(disp, font=font(15)) / SS + 8
        for i in range(total):
            cx = nx + i * 7
            d.ellipse([_s(cx - 2), _s(11), _s(cx + 2), _s(15)],
                      fill=CLAUDE if i == idx else (58, 64, 72))

    x = X0
    d.text((_s(x), _s(33)), q.symbol, font=font(10), fill=MUTED)   # the raw ticker
    x += d.textlength(q.symbol, font=font(10)) / SS + 8
    label, bg = BADGES.get(q.session, ("", None))
    if bg:
        bw = 6 + 7 * len(label)
        d.rounded_rectangle([_s(x), _s(29), _s(x + bw), _s(44)], radius=_s(3), fill=bg)
        d.text((_s(x + bw / 2), _s(31)), label, font=font(10), fill=TEXT, anchor="ma")
        x += bw + 8
    # Day high/low as % moved vs prev close. Tagged H/L and kept out of the price
    # panel: unlabelled they read as just more green percentages next to `pct`.
    for tag, pct in (("H", q.high_pct), ("L", q.low_pct)):
        if pct is None:
            continue
        txt = f"{tag} {pct:+.1f}%"
        d.text((_s(x), _s(32)), txt, font=font(10), fill=UP if pct >= 0 else DOWN)
        x += d.textlength(txt, font=font(10)) / SS + 8

    if not q.candles:
        d.text((_s(120), _s(120)), "waiting for data...", font=font(12),
               fill=(128, 138, 148), anchor="mm")
        return img.resize((W, H), Image.LANCZOS)

    n = len(q.candles)
    closes = q.closes
    ma5 = md.sma(closes, 5)
    mid, bb_up, bb_lo = md.bollinger(closes, 20)
    step = CW / n
    cx = [X0 + i * step + step / 2 for i in range(n)]
    body_w = max(step - 2, 1.0)

    # ---- price panel: candles + overlays, all sharing one scale ----
    lo, hi = _price_extent(q.candles, [v for v in bb_up + bb_lo if v is not None])
    y = _fit((PY, PH), lo, hi)

    for i, (o, h, l, c) in enumerate(q.candles):
        col = UP if c >= o else DOWN
        d.line([(_s(cx[i]), y(h)), (_s(cx[i]), y(l))], fill=col, width=max(SS, 1))
        top, bot = y(max(o, c)), y(min(o, c))
        if bot - top < SS:
            bot = top + SS          # doji: keep a visible 1px body
        d.rectangle([_s(cx[i] - body_w / 2), top, _s(cx[i] + body_w / 2), bot], fill=col)

    def overlay(series, colour, width=1):
        pts = [(_s(cx[i]), y(v)) for i, v in enumerate(series) if v is not None]
        if len(pts) > 1:
            d.line(pts, fill=colour, width=max(width * SS, 1), joint="curve")

    overlay(bb_up, BB_C)
    overlay(bb_lo, BB_C)
    overlay(mid, MA20_C)
    overlay(ma5, MA5_C)

    # ---- volume ----
    d.line([(_s(X0), _s(VY - 3)), (_s(X0 + CW), _s(VY - 3))], fill=SEP, width=SS)
    vmax = max(q.vols) or 1
    for i, (o, _, _, c) in enumerate(q.candles):
        vh = q.vols[i] / vmax * VH
        col = (30, 90, 58) if c >= o else (90, 37, 48)
        d.rectangle([_s(cx[i] - body_w / 2), _s(VY + VH - vh),
                     _s(cx[i] + body_w / 2), _s(VY + VH)], fill=col)

    # ---- RSI: 30/70 guides + the line, current value at the right ----
    d.line([(_s(X0), _s(RY - 3)), (_s(X0 + CW), _s(RY - 3))], fill=SEP, width=SS)
    ry = lambda v: _s(RY + (100 - v) / 100 * RH)                      # noqa: E731
    d.line([(_s(X0), ry(70)), (_s(X0 + CW), ry(70))], fill=(58, 37, 48), width=SS)
    d.line([(_s(X0), ry(30)), (_s(X0 + CW), ry(30))], fill=(34, 57, 46), width=SS)
    pts = [(_s(cx[i]), ry(v)) for i, v in enumerate(q.rsi) if v is not None]
    if len(pts) > 1:
        d.line(pts, fill=RSI_C, width=SS, joint="curve")
    last = next((v for v in reversed(q.rsi) if v is not None), None)
    if last is not None:
        col = (255, 96, 96) if last >= 70 else (64, 192, 128) if last <= 30 else MUTED
        _label(d, (_s(X0 + CW - 2), _s(RY + 1)), f"RSI {last:.0f}", col)

    return img.resize((W, H), Image.LANCZOS)


def parse_args(argv):
    """-> (tickers, host, rotate_secs). Bare words are tickers; the host is a
    flag because a variable-length ticker list leaves no fixed position for it."""
    tickers, host, rotate = [], None, ROTATE_SECS
    i = 0
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if a == "--host" and nxt:
            host, i = nxt, i + 2
        elif a == "--rotate" and nxt:
            rotate, i = float(nxt), i + 2
        elif a.startswith("-"):
            i += 1
        else:
            tickers.append(a.strip().upper())
            i += 1
    return tickers or ["AAPL"], host, rotate


def main():
    tickers, host, rotate = parse_args(sys.argv[1:])
    print(f"tickers: {', '.join(tickers)}"
          + (f"  (rotating every {rotate:.0f}s)" if len(tickers) > 1 else ""))

    s = Streamer(resolve_host(host), PORT)
    quotes, fetched = {}, {}          # per symbol, so a rotation doesn't refetch the world
    idx, last_rot = 0, time.time()
    while True:
        try:
            s.connect()
            while True:
                frame_start = time.time()
                if len(tickers) > 1 and frame_start - last_rot >= rotate:
                    idx, last_rot = (idx + 1) % len(tickers), frame_start
                sym = tickers[idx]
                if frame_start - fetched.get(sym, 0.0) >= REFETCH_SECS:
                    try:
                        quotes[sym] = md.fetch_quote(sym)
                        q = quotes[sym]
                        print(f"  {sym:10} {q.price:>12,.2f} {q.pct:+.2f}% "
                              f"[{q.session}] ({len(q.candles)} candles)")
                    except Exception as e:                # keep the last good frame up
                        print(f"  {sym:10} fetch error: {e}")
                    fetched[sym] = frame_start
                if sym in quotes:
                    s.push(render(quotes[sym], idx, len(tickers)))
                dt = (1.0 / FPS) - (time.time() - frame_start)
                if dt > 0:
                    time.sleep(dt)
        except OSError as e:
            print(f"\n[stocks] disconnected: {e}; retrying in 3s")
            try:
                if s.sock:
                    s.sock.close()
            except OSError:
                pass
            time.sleep(3)


if __name__ == "__main__":
    main()
