"""SmallTV — tiny, dependency-free client for a SmallTV-Ultra running this
ESPHome firmware. Talks to the device's web_server REST API (port 80).

    from smalltv import SmallTV
    tv = SmallTV("192.168.219.112")
    tv.set_mode("Clock")
    tv.lines(title="Hi", l1="from PC")     # PC Info page
    tv.stock(ticker="AAPL", price="192.34", change="+1.2%")  # Stocks page
    tv.backlight(0.5)

Every setter maps to one REST call, so anything here also works from curl:
    curl -X POST 'http://<ip>/select/mode/set?option=Clock'
"""
import json
import urllib.parse
import urllib.request


class SmallTV:
    def __init__(self, host, timeout=4):
        if not host.startswith("http"):
            host = "http://" + host
        self.base = host.rstrip("/")
        self.timeout = timeout

    # ---- transport ----
    def _req(self, method, path, params=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read().decode("utf-8", "replace")

    def _get_json(self, path):
        try:
            return json.loads(self._req("GET", path))
        except Exception:
            return {}

    # ---- generic entity control (works for any entity this firmware exposes) ----
    def set_text(self, entity_id, value):
        self._req("POST", f"/text/{entity_id}/set", {"value": value})

    def get_text(self, entity_id):
        return self._get_json(f"/text/{entity_id}").get("value")

    def set_select(self, entity_id, option):
        self._req("POST", f"/select/{entity_id}/set", {"option": option})

    def get_select(self, entity_id):
        return self._get_json(f"/select/{entity_id}").get("value")

    def switch(self, entity_id, on=True):
        self._req("POST", f"/switch/{entity_id}/turn_{'on' if on else 'off'}")

    # ---- high level ----
    def set_mode(self, mode):
        """Switch the active page: Clock / Stocks / PC Info / Off (must be built in)."""
        self.set_select("mode", mode)

    def get_mode(self):
        return self.get_select("mode")

    def backlight(self, pct):
        pct = max(0.0, min(1.0, float(pct)))
        self._req("POST", "/light/backlight/turn_on", {"brightness": int(pct * 255)})

    def is_alive(self):
        try:
            self._req("GET", "/")
            return True
        except Exception:
            return False

    # ---- PC Info page ----
    def lines(self, title=None, l1=None, l2=None, l3=None, switch=True):
        if switch:
            self.set_mode("PC Info")
        for eid, val in (("title", title), ("line1", l1), ("line2", l2), ("line3", l3)):
            if val is not None:
                self.set_text(eid, val)

    def message(self, title, sub=""):
        self.lines(title=title, l1=sub, l2="", l3="")

    # ---- Stocks page ----
    def stock(self, price=None, change=None, ticker=None):
        if ticker is not None:
            self.set_text("ticker", ticker)
        if price is not None:
            self.set_text("stock_price", str(price))
        if change is not None:
            self.set_text("stock_change", str(change))

    def get_ticker(self):
        return self.get_text("ticker")
