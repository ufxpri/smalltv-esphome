# SmallTV Widget — tray / menu-bar app (macOS + Windows)

A tiny background app that lives in the **Windows system tray** / **macOS menu
bar** and drives your SmallTV-Ultra. Click the icon to switch pages, set
brightness, start/stop the live data bridges, or open Settings. It can start
itself at login and remembers everything in a per-user config file.

It reuses the dependency-free `smalltv` client — the widget is just a friendly
front-end over the same REST calls.

## What it does

- **Icon** turns green when the device is reachable, grey when it isn't; the
  tooltip shows the IP and which bridges are running.
- **Page** submenu — switch the active screen (Clock / Stocks / PC Info /
  Weather / Off), reflecting the device's current mode.
- **Brightness** submenu — 20 – 100 %.
- **Stocks bridge** — fetches live 5-minute candles from Yahoo Finance for the
  configured ticker and pushes them to the Stocks page.
- **PC stats bridge** — pushes live CPU / RAM to the PC Info page (needs
  `psutil`).
- **Settings…** — a small window for device IP (with a Test button), ticker,
  refresh intervals, which bridges auto-start, and start-at-login.
- **Start at login** — per-user, no admin/root:
  registry `Run` key (Windows) / LaunchAgent (macOS) / XDG autostart (Linux).

Config is stored at:
- Windows `%APPDATA%\SmallTVWidget\config.json`
- macOS `~/Library/Application Support/SmallTVWidget/config.json`
- Linux `~/.config/SmallTVWidget/config.json`

## Run from source

```sh
cd client
pip install -r requirements-widget.txt
python smalltv_widget.py
```

## Build a standalone app (.exe / .app)

```sh
cd client
# Windows
build\build_win.bat
# macOS / Linux
./build/build_mac.sh
```

Output lands in `client/dist/`:
- Windows → `SmallTVWidget.exe` (double-click; no console window)
- macOS → `SmallTVWidget.app` (a menu-bar agent — no Dock icon). First launch:
  right-click → **Open** to get past Gatekeeper.

To auto-start, just tick **Start at login** in the widget — it registers the
built executable, so it keeps working after reboot.

## Notes

- The Settings window runs as a separate process (`--settings`) so it never
  clashes with the tray's GUI thread on macOS.
- Only one thing should push to a given page at a time — if you already run
  `examples/stock_bridge.py` from a terminal, stop it before starting the
  widget's Stocks bridge (or vice-versa).
