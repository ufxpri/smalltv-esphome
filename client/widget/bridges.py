"""Background data-push bridges for the SmallTV pages.

Each bridge is a daemon thread with a stop event. It reads its live settings
from a shared config accessor every tick, so editing settings in the UI takes
effect on the next loop without a restart.
"""
import json
import threading
import time
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- Stocks ----
N = 48          # candles shown across the width
ALPH = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def fetch_5m(symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?interval=5m&range=1d&includePrePost=true")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        res = json.load(r)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    candles = [c for c in zip(q["open"], q["high"], q["low"], q["close"])
               if None not in c][-N:]
    meta = res["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
    pct = (price - prev) / prev * 100 if prev else 0.0
    return candles, price, pct, market_session(meta)


def market_session(meta):
    """REGULAR / PRE / POST / CLOSED, from Yahoo's currentTradingPeriod windows
    (epoch UTC) compared against the current time."""
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

    def e(v):
        lvl = max(0, min(63, round((hi - v) / rng * 63)))
        return ALPH[lvl]

    return "".join(e(o) + e(h) + e(l) + e(c) for o, h, l, c in candles)


# --------------------------------------------------------------- runtime ----
class Bridge(threading.Thread):
    """Base worker: loops tick() until stopped, honouring a live interval."""
    label = "bridge"

    def __init__(self, manager):
        super().__init__(daemon=True)
        self.m = manager
        self._stop = threading.Event()
        self.last_status = ""

    @property
    def running(self) -> bool:
        return self.is_alive() and not self._stop.is_set()

    def stop(self):
        self._stop.set()

    def _interval(self) -> float:
        return 5.0

    def tick(self, tv):
        raise NotImplementedError

    def run(self):
        from smalltv import SmallTV
        while not self._stop.is_set():
            try:
                tv = SmallTV(self.m.cfg["device_ip"])
                self.tick(tv)
            except Exception as e:                       # keep the thread alive
                self.last_status = f"error: {e}"
                self.m.log(f"[{self.label}] {self.last_status}")
            self._stop.wait(max(1.0, float(self._interval())))


class StockBridge(Bridge):
    label = "stocks"

    def _interval(self):
        return self.m.cfg["stock"]["interval"]

    def run(self):
        # push the configured ticker once so the device shows it immediately
        try:
            from smalltv import SmallTV
            tv = SmallTV(self.m.cfg["device_ip"])
            tv.set_mode("Stocks")
            want = (self.m.cfg["stock"].get("ticker") or "").strip().upper()
            if want:
                tv.set_text("ticker", want)
        except Exception:
            pass
        super().run()

    def tick(self, tv):
        symbol = (tv.get_ticker() or self.m.cfg["stock"].get("ticker") or "AAPL").strip().upper()
        candles, price, pct, session = fetch_5m(symbol)
        tv.set_text("market_state", session)
        if candles:
            tv.set_text("chart_data", encode(candles))
            tv.stock(price=f"{price:,.2f}", change=f"{pct:+.2f}%")
            self.last_status = f"{symbol} {price:,.2f} {pct:+.2f}% [{session}]"
        else:
            tv.stock(price="--", change="")
            self.last_status = f"{symbol} no data [{session}]"
        self.m.log(f"[stocks] {self.last_status}")


class PCStatsBridge(Bridge):
    label = "pcstats"

    def _interval(self):
        return self.m.cfg["pcstats"]["interval"]

    def run(self):
        try:
            import psutil  # noqa: F401
        except ImportError:
            self.last_status = "psutil not installed"
            self.m.log("[pcstats] psutil not installed — run: pip install psutil")
            return
        try:
            from smalltv import SmallTV
            SmallTV(self.m.cfg["device_ip"]).set_mode("PC Info")
        except Exception:
            pass
        super().run()

    def tick(self, tv):
        import psutil
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent
        tv.lines(
            title=self.m.cfg["pcstats"].get("title", "PC Monitor"),
            l1=f"CPU  {cpu:4.0f} %",
            l2=f"RAM  {mem:4.0f} %",
            l3=time.strftime("%H:%M:%S"),
            switch=False,
        )
        self.last_status = f"CPU {cpu:.0f}%  RAM {mem:.0f}%"
