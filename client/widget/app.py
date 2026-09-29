"""Tray / menu-bar app: the SmallTV Widget.

Supervises the control panel server and nothing else. The panel is the UI —
sources, brightness, stickers, the live monitor, and the device address all live
there, in a browser, which renders the same on macOS and Windows. The tray owns
what a web page can't: running at login, staying resident, and being the thing
you click to open the panel.

(An earlier version drew its own settings window in tkinter. It had to be
launched as a separate process so it wouldn't fight the tray's GUI main thread
on macOS — that fight is why the UI moved to the browser.)
"""
import logging
import sys
import threading
import time
import webbrowser

import pystray

import config as cfg_mod
import logs

from . import assets
from . import autostart
from . import panel


class Manager:
    def __init__(self):
        self.cfg = cfg_mod.load()
        self.icon = None
        self.up = False             # panel server reachable
        self.online = False         # device reachable, per the panel
        self.current = None         # source the panel says is streaming
        self.heap = None            # device free heap in bytes, per the panel

    def log(self, msg):
        """Supervisor events — starts, stops, failures. There are few of them,
        so they all belong at INFO; the widget's log is the one you read when
        the panel did not come up."""
        logging.getLogger("widget").info(msg)

    # ---- panel lifecycle ----
    def start_panel(self):
        def worker():
            try:
                started = panel.start(self.cfg["device_ip"])
                self.log("panel started" if started else "panel already running")
            except Exception as e:
                self.log(f"panel start error: {e}")
            self._poll_once()
        threading.Thread(target=worker, daemon=True).start()

    def stop_panel(self):
        def worker():
            try:
                panel.stop()
                self.log("panel stopped (and any stream with it)")
            except Exception as e:
                self.log(f"panel stop error: {e}")
            self._poll_once()
        threading.Thread(target=worker, daemon=True).start()

    def toggle_panel(self):
        self.stop_panel() if self.up else self.start_panel()

    def open_panel(self):
        def worker():
            if not panel.is_running():
                try:
                    panel.start(self.cfg["device_ip"])
                except Exception as e:
                    self.log(f"panel start error: {e}")
                    return
                for _ in range(20):           # give the server a moment to bind
                    if panel.status():
                        break
                    time.sleep(0.25)
            webbrowser.open(panel.URL)
            self._poll_once()
        threading.Thread(target=worker, daemon=True).start()

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

    # ---- status ----
    def _poll_once(self):
        st = panel.status()
        self.up = st is not None
        self.online = bool(st and st.get("online"))
        self.current = st and st.get("current")
        self.heap = st and st.get("heap")
        self.cfg = cfg_mod.load()          # the panel is where device_ip is edited
        self._refresh_icon()

    def _refresh_icon(self):
        if not self.icon:
            return
        icon_state = assets.state_for(self.online, self.current, self.heap)
        self.icon.icon = assets.make_icon(state=icon_state)
        if not self.up:
            state = "server stopped"
        elif not self.online:
            state = f"server up · device unreachable ({self.cfg['device_ip']})"
        elif icon_state == "low_heap":
            state = f"low heap ({self.heap / 1024:.1f} KB) · {self.current or 'local page'}"
        else:
            state = f"server up · {self.current or 'local page'}"
        self.icon.title = f"SmallTV — {state}"
        self.icon.update_menu()

    def poll_loop(self):
        while True:
            self._poll_once()
            time.sleep(5)

    def quit(self):
        if self.icon:
            self.icon.stop()


def build_menu(m: Manager):
    Item, Menu = pystray.MenuItem, pystray.Menu
    return Menu(
        Item(lambda item: m.icon.title if m.icon else "SmallTV", None, enabled=False),
        Menu.SEPARATOR,
        Item("Open control panel", lambda: m.open_panel(), default=True),
        Item("Server running", lambda: m.toggle_panel(), checked=lambda item: m.up),
        Menu.SEPARATOR,
        Item("Start at login", lambda: m.toggle_login(),
             checked=lambda item: autostart.is_enabled()),
        Menu.SEPARATOR,
        Item("Quit", lambda: m.quit()),
    )


def main():
    logs.setup("widget")
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # cp949 consoles can't encode our logs
    except Exception:
        pass

    m = Manager()
    if m.cfg.get("start_at_login"):
        # Re-register on every launch: the login item stores an absolute path,
        # which goes stale when the build moves (e.g. onefile -> onedir). The
        # config is the intent; the registration is derived, so refresh it.
        try:
            autostart.set_enabled(True)
        except Exception as e:
            m.log(f"login item refresh error: {e}")
    m.start_panel()          # the widget exists to keep the server up

    m.icon = pystray.Icon(
        "smalltv_widget",
        icon=assets.make_icon(state="offline"),
        title="SmallTV Widget",
        menu=build_menu(m),
    )

    threading.Thread(target=m.poll_loop, daemon=True).start()
    # run() must be on the main thread (required by the macOS backend)
    m.icon.run()


if __name__ == "__main__":
    main()
