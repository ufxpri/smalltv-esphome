"""Live 5-minute candlestick bridge for the Stocks page.

Reads the ticker the user typed in the device web UI, fetches 5-minute candles
from Yahoo Finance (no API key), normalizes them to pixel offsets, and pushes
them to the device as `chart_data` (plus price + % change) every few seconds.

    python stock_bridge.py [device_ip] [interval_seconds]

Type a symbol (AAPL, MSFT, 005930.KS, BTC-USD ...) in the device web UI's
`ticker` field, or set it here: SmallTV(ip).set_text("ticker", "TSLA")
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smalltv import SmallTV

DEVICE = sys.argv[1] if len(sys.argv) > 1 else "192.168.219.112"
INTERVAL = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0

N = 48          # candles shown across the width
# 4 base64url chars per candle (o,h,l,c), each a level 0..63 (0 = top/high).
ALPH = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def fetch_5m(symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?interval=5m&range=1d&includePrePost=true")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        res = json.load(r)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    vol_in = q.get("volume") or [None] * len(q["close"])
    rows = [(o, h, l, c, v or 0)
            for o, h, l, c, v in zip(q["open"], q["high"], q["low"], q["close"], vol_in)
            if None not in (o, h, l, c)]
    rsi_full = compute_rsi([r[3] for r in rows], 14)
    rows, rsi = rows[-N:], rsi_full[-N:]
    candles = [(o, h, l, c) for o, h, l, c, v in rows]
    vols = [v for *_, v in rows]
    meta = res["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
    pct = (price - prev) / prev * 100 if prev else 0.0
    high = meta.get("regularMarketDayHigh")
    low = meta.get("regularMarketDayLow")
    if high is None and candles:
        high = max(c[1] for c in candles)
    if low is None and candles:
        low = min(c[2] for c in candles)
    high_pct = (high - prev) / prev * 100 if (high is not None and prev) else None
    low_pct = (low - prev) / prev * 100 if (low is not None and prev) else None
    return candles, price, pct, market_session(meta), vols, rsi, high_pct, low_pct


def compute_rsi(closes, period=14):
    """Wilder's RSI aligned to `closes`; None for the first `period` points."""
    rsi = [None] * len(closes)
    if len(closes) <= period:
        return rsi

    def val(ag, al):
        return 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)

    gains = sum(max(closes[i] - closes[i - 1], 0.0) for i in range(1, period + 1))
    losses = sum(max(closes[i - 1] - closes[i], 0.0) for i in range(1, period + 1))
    avg_g, avg_l = gains / period, losses / period
    rsi[period] = val(avg_g, avg_l)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
        rsi[i] = val(avg_g, avg_l)
    return rsi


def encode_levels(values):
    """One base64url char per value, normalized to the series max (0..63)."""
    m = max(values) if values else 0
    if m <= 0:
        return ALPH[0] * len(values)
    return "".join(ALPH[max(0, min(63, round(v / m * 63)))] for v in values)


def encode_rsi(rsi):
    """One char per point: RSI 0..100 -> level 0..63; '.' where undefined."""
    return "".join("." if r is None else ALPH[max(0, min(63, round(r / 100 * 63)))]
                   for r in rsi)


def market_session(meta):
    """REGULAR / PRE / POST / CLOSED from Yahoo's currentTradingPeriod windows."""
    ctp = meta.get("currentTradingPeriod") or {}
    now = time.time()

    def in_window(key):
        p = ctp.get(key) or {}
        s, e = p.get("start"), p.get("end")
        return s is not None and e is not None and s <= now < e

    if in_window("regular"):
        return "REGULAR"
    if in_window("pre"):
        return "PRE"
    if in_window("post"):
        return "POST"
    return "CLOSED"


def encode(candles):
    hi = max(c[1] for c in candles)
    lo = min(c[2] for c in candles)
    rng = (hi - lo) or 1e-9

    def e(v):  # value -> level 0..63 (0 = high/top) -> base64url char
        lvl = max(0, min(63, round((hi - v) / rng * 63)))
        return ALPH[lvl]

    return "".join(e(o) + e(h) + e(l) + e(c) for o, h, l, c in candles)


def main():
    tv = SmallTV(DEVICE)
    tv.set_mode("Stocks")
    print(f"5m candle bridge -> {DEVICE} (mode=Stocks). "
          f"Type a ticker at http://{DEVICE}/ , Ctrl+C to stop.")
    while True:
        symbol = (tv.get_ticker() or "AAPL").strip().upper()
        try:
            candles, price, pct, session, vols, rsi, high_pct, low_pct = fetch_5m(symbol)
            tv.set_text("market_state", session)
            if candles:
                tv.set_text("chart_data", encode(candles))
                tv.set_text("vol_data", encode_levels(vols))
                tv.set_text("rsi_data", encode_rsi(rsi))
                if high_pct is not None:
                    tv.set_text("stock_high", f"{high_pct:+.1f}%")
                if low_pct is not None:
                    tv.set_text("stock_low", f"{low_pct:+.1f}%")
                tv.stock(price=f"{price:,.2f}", change=f"{pct:+.2f}%")
                print(f"  {symbol:10} {price:>12,.2f}  {pct:+.2f}%  [{session}]  ({len(candles)} candles)")
            else:
                print(f"  {symbol:10} no candle data  [{session}]")
        except Exception as e:
            tv.stock(price="--", change="")
            print(f"  {symbol:10} error: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
