"""Market data for the SmallTV stream sources (Yahoo Finance, no API key).

Pure data — no rendering, no device calls. `stream_stocks.py` and
`stream_sectors.py` both draw from here.

Everything is returned at full float precision. The device pages that these
sources replaced had to quantize each value to a base64url level 0..63 and pack
it into a 255-char text field, because that was the only channel ESPHome gave
them; rendering on the PC means that whole encoding layer is gone.
"""
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

N = 48          # candles shown across the width
_UA = {"User-Agent": "Mozilla/5.0"}

# 11 SPDR sector ETFs + SPY, with the labels the heatmap shows.
SECTORS = [("XLK", "Tech"), ("XLF", "Fin"), ("XLV", "Health"), ("XLY", "ConsD"),
           ("XLC", "Comm"), ("XLI", "Indu"), ("XLP", "Staple"), ("XLE", "Enrgy"),
           ("XLU", "Util"), ("XLRE", "RealE"), ("XLB", "Matl"), ("SPY", "SPY")]


@dataclass
class Quote:
    """One symbol's intraday state."""
    symbol: str
    candles: list          # [(open, high, low, close)], oldest first
    vols: list             # per-candle volume, aligned to `candles`
    rsi: list              # per-candle RSI(14); None where lookback is short
    price: float
    prev_close: float
    pct: float             # % change vs prev close
    session: str           # REGULAR / PRE / POST / CLOSED
    high_pct: float | None  # day high as % move vs prev close
    low_pct: float | None

    @property
    def closes(self):
        return [c[3] for c in self.candles]


def _get(url):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def market_session(meta):
    """REGULAR / PRE / POST / CLOSED from Yahoo's currentTradingPeriod windows
    (epoch UTC) compared against the current time."""
    ctp = meta.get("currentTradingPeriod") or {}
    now = time.time()

    def in_window(key):
        p = ctp.get(key) or {}
        s, e = p.get("start"), p.get("end")
        return s is not None and e is not None and s <= now < e

    for key in ("regular", "pre", "post"):
        if in_window(key):
            return key.upper()
    return "CLOSED"


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


def sma(values, period):
    """Simple moving average aligned to `values`; None until enough lookback."""
    out = [None] * len(values)
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def bollinger(values, period=20, k=2.0):
    """(mid, upper, lower) bands aligned to `values`; None until enough lookback.

    Population stddev over the same window as `mid`, matching the convention the
    device page used.
    """
    mid = sma(values, period)
    up, lo = [None] * len(values), [None] * len(values)
    for i, m in enumerate(mid):
        if m is None:
            continue
        window = values[i - period + 1:i + 1]
        var = sum((v - m) ** 2 for v in window) / period
        sd = var ** 0.5
        up[i], lo[i] = m + k * sd, m - k * sd
    return mid, up, lo


def fetch_quote(symbol, n=N):
    """Intraday 5-minute candles + session state for one symbol."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?interval=5m&range=1d&includePrePost=true")
    res = _get(url)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    vol_in = q.get("volume") or [None] * len(q["close"])
    rows = [(o, h, l, c, v or 0)
            for o, h, l, c, v in zip(q["open"], q["high"], q["low"], q["close"], vol_in)
            if None not in (o, h, l, c)]
    # RSI needs lookback, so compute over the full series, then take the tail.
    rsi_full = compute_rsi([r[3] for r in rows], 14)
    rows, rsi = rows[-n:], rsi_full[-n:]

    meta = res["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
    candles = [(o, h, l, c) for o, h, l, c, _ in rows]
    high = meta.get("regularMarketDayHigh")
    low = meta.get("regularMarketDayLow")
    if high is None and candles:
        high = max(c[1] for c in candles)
    if low is None and candles:
        low = min(c[2] for c in candles)

    def as_pct(v):
        return (v - prev) / prev * 100 if (v is not None and prev) else None

    return Quote(symbol=symbol, candles=candles, vols=[v for *_, v in rows], rsi=rsi,
                 price=price, prev_close=prev, pct=as_pct(price) or 0.0,
                 session=market_session(meta), high_pct=as_pct(high), low_pct=as_pct(low))


def fetch_pct(symbol):
    """Daily % change for a symbol, from the 1d chart meta (no candles needed)."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?interval=1d&range=1d")
    meta = _get(url)["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
    return (price - prev) / prev * 100 if prev else 0.0
