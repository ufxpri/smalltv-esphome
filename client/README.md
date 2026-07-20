# client — PC-side tools for the SmallTV

Two layers live here, and they do different jobs:

- **`smalltv/`** — a tiny, dependency-free REST client for the device's own
  entities (page, backlight, sensors). Wraps `web_server`, so anything it does
  also works from `curl`.
- **Stream sources** — everything the ESP8266 can't draw itself. The PC renders
  a 240×240 image and pushes only the changed tiles to the device's framebuffer
  server. This is where the rich screens live.

The device holds a lean local page set (Clock, Weather) so it still shows
something with the PC off; while a stream client is connected the device blits
exactly what the PC sends, and falls back to the local page when it stops.

## Everyday use: the control panel

```sh
python control_panel.py            # local web UI at http://localhost:8787
```

Switch sources, set brightness, pick stickers, edit the ticker list and device
address, and watch a live monitor (mirror of the screen, dirty-patch heatmap,
fps / heap / RSSI). `smalltv_widget.py` is a tray app that just keeps this
server running and gives you a menu-bar entry to open it.

## Stream sources

| source | what it shows | needs |
|---|---|---|
| `smalltv_stream.py` | CPU-load furnace (also the streaming library) | `psutil` |
| `stream_stocks.py` | candlesticks + MA/Bollinger + volume + RSI, cycling tickers | — |
| `stream_sectors.py` | S&P sector heatmap (11 SPDR ETFs + SPY) | — |
| `stream_gif.py` | animated GIF slideshow | — |
| `stream_video.py` | video playback | `ffmpeg` on PATH |

All need `pip install pillow numpy psutil`. Drive them through `stream.py`,
which enforces one source at a time (the device accepts a single stream client):

```sh
python stream.py stocks AAPL MSFT     # cycles tickers, 15s each
python stream.py sectors
python stream.py video clip.mp4
python stream.py off                  # -> device falls back to its local clock
python stream.py status
```

Add `--host <ip>` to target a specific device; sources also honour the
`SMALLTV_HOST` env var, which is how the panel points them at the right one.

## Library

```python
from smalltv import SmallTV
tv = SmallTV("192.168.219.112")      # device IP (or hostname)

tv.set_mode("Clock")                  # local page; must be in the flashed build
tv.backlight(0.5)                     # 0.0 – 1.0
tv.get_sensor("free_heap")            # watch this — heap is the tight resource

tv.set_text("some_entity", "hello")   # generic: any entity the firmware exposes
```

`set_mode` only accepts pages actually flashed (see `tools/build.py`).

## REST cheat-sheet (no Python needed)
```sh
curl -X POST 'http://<ip>/select/mode/set?option=Clock'
curl -X POST 'http://<ip>/light/backlight/turn_on?brightness=128'
curl        'http://<ip>/sensor/free_heap'
```
