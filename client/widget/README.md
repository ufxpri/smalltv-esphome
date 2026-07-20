# SmallTV Widget — tray / menu-bar app (macOS + Windows)

A tiny background app in the **Windows system tray** / **macOS menu bar** whose
only job is to keep the **control panel server** running and give you a one-click
way to open it. The panel (`client/control_panel.py`) is the actual UI.

## Why the split

An earlier version drew its own settings window in tkinter. It had to be
launched as a *separate process* so it wouldn't fight the tray's GUI main thread
on macOS — and even then the rich parts (the live screen mirror, the patch
heatmap, sticker thumbnails) were painful to build twice.

So the UI moved to a local web page, which renders identically on both OSes, and
the tray kept the jobs a web page can't do: run at login, stay resident, and be
the thing you click. It's the same shape Docker Desktop, Syncthing and Tailscale
use.

## What it does

- **Icon** — green when the device is reachable, grey when it isn't. The tooltip
  reads `server stopped`, `server up · device unreachable (<ip>)`, or
  `server up · <source>`.
- **Open control panel** — starts the server if needed, then opens the browser.
  (Also the default action on click.)
- **Server running** — a checkbox to start/stop the panel. Stopping it also
  stops whatever it was streaming, since nothing would supervise those processes
  afterwards.
- **Start at login** — per-user, no admin/root: registry `Run` key (Windows) /
  LaunchAgent (macOS) / XDG autostart (Linux). At login the panel starts
  silently (`--no-browser`) rather than popping a tab every boot.

Everything else — sources, brightness, tickers, stickers, colour depth, the
device address, the live monitor — lives in the panel.

The widget reads its status from the panel's `/status` rather than polling the
device itself, so only one process talks to a board with ~23 KB of free heap.

Config (shared with the panel, which is where it's edited) is stored at:
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

To auto-start, tick **Start at login** in the widget — it registers the built
executable, so it keeps working after reboot.
