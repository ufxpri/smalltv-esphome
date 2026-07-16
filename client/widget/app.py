"""Tray / menu-bar app: the SmallTV Widget.

Quick actions live in the icon menu (switch page, brightness, start/stop the
live bridges, start-at-login, quit); the fuller Settings window opens in a
separate process. A background poller keeps the icon's colour/tooltip in sync
with the device.
"""
import subprocess
import sys
import threading
import time

import pystray

from smalltv import SmallTV

from . import assets
from . import autostart
from . import config as cfg_mod
from .bridges import PCStatsBridge, SectorBridge, StockBridge
from .launch import self_command

BRIGHTNESS_STEPS = [0.2, 0.4, 0.6, 0.8, 1.0]


class Manager:
    def __init__(self):
        self.cfg = cfg_mod.load()
        self.icon = None
        self.online = False
        self.mode = None                # last known device mode
        self.stock = StockBridge(self)
        self.pcstats = PCStatsBridge(self)
        self.sectors = SectorBridge(self)
        self.rotation_on = bool(self.cfg["rotation"]["enabled"])
        self._rot_stop = threading.Event()
        self._rot_thread = None
        self._lock = threading.Lock()

    # ---- utils ----
    def log(self, msg):
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    def tv(self):
        return SmallTV(self.cfg["device_ip"], timeout=4)

    def reload_config(self):
        self.cfg = cfg_mod.load()
        # reconcile live rotation state with the (possibly edited) config
        want = bool(self.cfg["rotation"]["enabled"])
        if want != self.rotation_on:
            self.rotation_on = want
            if want:
                self.start_rotation()
            else:
                self._rot_stop.set()
        if self.icon:
            self.icon.update_menu()

    # ---- device actions ----
    def set_mode(self, mode):
        def worker():
            try:
                self.tv().set_mode(mode)
                self.mode = mode
                self.log(f"mode -> {mode}")
            except Exception as e:
                self.log(f"mode error: {e}")
            self._refresh_icon()
        threading.Thread(target=worker, daemon=True).start()

    def set_brightness(self, pct):
        def worker():
            try:
                self.tv().backlight(pct)
                self.cfg["brightness"] = pct
                cfg_mod.save(self.cfg)
                self.log(f"brightness -> {int(pct * 100)}%")
            except Exception as e:
                self.log(f"brightness error: {e}")
            self._refresh_icon()
        threading.Thread(target=worker, daemon=True).start()

    # ---- bridges ----
    def toggle_stock(self):
        if self.stock.running:
            self.stock.stop()
            self.log("stocks bridge stopped")
        else:
            self.stock = StockBridge(self)
            self.stock.start()
            self.log("stocks bridge started")
        self._refresh_icon()

    def toggle_pcstats(self):
        if self.pcstats.running:
            self.pcstats.stop()
            self.log("pc stats bridge stopped")
        else:
            self.pcstats = PCStatsBridge(self)
            self.pcstats.start()
            self.log("pc stats bridge started")
        self._refresh_icon()

    def toggle_sectors(self):
        if self.sectors.running:
            self.sectors.stop()
            self.log("sectors bridge stopped")
        else:
            self.sectors = SectorBridge(self)
            self.sectors.start()
            self.log("sectors bridge started")
        self._refresh_icon()

    # ---- rotation ----
    def start_rotation(self):
        if self._rot_thread and self._rot_thread.is_alive():
            return
        self._rot_stop.clear()
        self._rot_thread = threading.Thread(target=self._rotation_loop, daemon=True)
        self._rot_thread.start()

    def _rotation_loop(self):
        i = 0
        while not self._rot_stop.is_set():
            pages = [p for p in (self.cfg["rotation"]["pages"] or self.cfg["modes"])
                     if p and p != "Off"]
            if pages:
                page = pages[i % len(pages)]
                try:
                    self.tv().set_mode(page)
                    self.mode = page
                    self.log(f"rotate -> {page}")
                except Exception as e:
                    self.log(f"rotation error: {e}")
                i += 1
                self._refresh_icon()
            self._rot_stop.wait(max(2.0, float(self.cfg["rotation"]["interval"])))

    def toggle_rotation(self):
        self.rotation_on = not self.rotation_on
        self.cfg["rotation"]["enabled"] = self.rotation_on
        cfg_mod.save(self.cfg)
        if self.rotation_on:
            self.start_rotation()
        else:
            self._rot_stop.set()
        self.log(f"rotation -> {self.rotation_on}")
        if self.icon:
            self.icon.update_menu()

    # ---- login item ----
    def toggle_login(self):
        new = not autostart.is_enabled()
        try:
            autostart.set_enabled(new)
            self.cfg["start_at_login"] = new
            cfg_mod.save(self.cfg)
            self.log(f"start at login -> {new}")
        except Exception as e:
            self.log(f"login item error: {e}")
        if self.icon:
            self.icon.update_menu()

    # ---- settings window (separate process) ----
    def open_settings(self):
        def worker():
            try:
                subprocess.run(self_command("--settings"))
            except Exception as e:
                self.log(f"settings error: {e}")
            self.reload_config()
        threading.Thread(target=worker, daemon=True).start()

    # ---- status poller ----
    def _refresh_icon(self):
        if not self.icon:
            return
        self.icon.icon = assets.make_icon(self.online)
        bits = [self.cfg["device_ip"], "online" if self.online else "offline"]
        if self.stock.running:
            bits.append("stocks")
        if self.pcstats.running:
            bits.append("pc")
        if self.sectors.running:
            bits.append("sectors")
        if self.rotation_on:
            bits.append("rotating")
        self.icon.title = "SmallTV — " + " · ".join(bits)
        self.icon.update_menu()

    def poll_loop(self):
        while True:
            try:
                self.mode = self.tv().get_mode()
                self.online = self.mode is not None
            except Exception:
                self.online = False
            self._refresh_icon()
            time.sleep(5)

    # ---- quit ----
    def quit(self):
        self._rot_stop.set()
        self.stock.stop()
        self.pcstats.stop()
        self.sectors.stop()
        if self.icon:
            self.icon.stop()


def build_menu(m: Manager):
    Item = pystray.MenuItem
    Menu = pystray.Menu

    mode_items = [
        Item(mode,
             (lambda mo: lambda: m.set_mode(mo))(mode),
             checked=(lambda mo: lambda item: m.mode == mo)(mode),
             radio=True)
        for mode in m.cfg["modes"]
    ]

    bright_items = [
        Item(f"{int(p * 100)} %",
             (lambda pp: lambda: m.set_brightness(pp))(p),
             checked=(lambda pp: lambda item: abs(m.cfg["brightness"] - pp) < 0.01)(p),
             radio=True)
        for p in BRIGHTNESS_STEPS
    ]

    return Menu(
        Item(lambda item: m.icon.title if m.icon else "SmallTV", None, enabled=False),
        Menu.SEPARATOR,
        Item("Page", Menu(*mode_items)),
        Item("Brightness", Menu(*bright_items)),
        Menu.SEPARATOR,
        Item("Stocks bridge", lambda: m.toggle_stock(),
             checked=lambda item: m.stock.running),
        Item("PC stats bridge", lambda: m.toggle_pcstats(),
             checked=lambda item: m.pcstats.running),
        Item("Sectors bridge", lambda: m.toggle_sectors(),
             checked=lambda item: m.sectors.running),
        Menu.SEPARATOR,
        Item("Rotate pages", lambda: m.toggle_rotation(),
             checked=lambda item: m.rotation_on),
        Item("Settings…", lambda: m.open_settings()),
        Item("Start at login", lambda: m.toggle_login(),
             checked=lambda item: autostart.is_enabled()),
        Menu.SEPARATOR,
        Item("Quit", lambda: m.quit()),
    )


def main():
    # Settings sub-process mode.
    if "--settings" in sys.argv:
        from . import settings
        settings.run()
        return

    m = Manager()

    # honour saved autostart-on-launch preferences
    if m.cfg["stock"].get("autostart"):
        m.stock = StockBridge(m)
        m.stock.start()
    if m.cfg["pcstats"].get("autostart"):
        m.pcstats = PCStatsBridge(m)
        m.pcstats.start()
    if m.cfg["sectors"].get("autostart"):
        m.sectors = SectorBridge(m)
        m.sectors.start()
    if m.rotation_on:
        m.start_rotation()

    m.icon = pystray.Icon(
        "smalltv_widget",
        icon=assets.make_icon(False),
        title="SmallTV Widget",
        menu=build_menu(m),
    )

    threading.Thread(target=m.poll_loop, daemon=True).start()
    # run() must be on the main thread (required by the macOS backend)
    m.icon.run()


if __name__ == "__main__":
    main()
