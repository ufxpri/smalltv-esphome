"""SmallTV — tiny, dependency-free client for a SmallTV-Ultra running this
ESPHome firmware. Talks to the device's web_server REST API (port 80).

    from smalltv import SmallTV
    tv = SmallTV("192.168.219.112")
    tv.set_mode("Clock")
    tv.backlight(0.5)
    tv.get_sensor("free_heap")

Every setter maps to one REST call, so anything here also works from curl:
    curl -X POST 'http://<ip>/select/mode/set?option=Clock'

Scope: the device's own entities only. Rich screens are rendered on the PC and
pushed over the framebuffer stream instead — see client/stream.py.
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

    def get_sensor(self, entity_id):
        """e.g. get_sensor("free_heap") / ("uptime") / ("wifi_signal")."""
        return self._get_json(f"/sensor/{entity_id}").get("value")

    # ---- high level ----
    def set_mode(self, mode):
        """Switch the active local page. Only pages built into the running
        firmware are valid — currently Clock / Weather / Off."""
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
