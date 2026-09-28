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

Global settings (device / brightness / colour depth) sit at the top; below them
the source picker swaps in the settings pane for whichever source you select.
Nothing takes effect while you edit — picking a source only selects it, and the
change is applied when you press **저장** (globals) or **전송** (source).

## Stream sources

| source | what it shows | needs |
|---|---|---|
| `smalltv_stream.py` | CPU-load furnace (also the streaming library) | `psutil` |
| `stream_stocks.py` | candlesticks + MA/Bollinger + volume + RSI, cycling tickers | — |
| `stream_sectors.py` | S&P sector heatmap (11 SPDR ETFs + SPY) | — |
| `stream_claude.py` | Claude usage burn monitor | claude.ai session key |
| `stream_codex.py` | the same screen for Codex, in teal | chatgpt.com session cookie |
| `stream_usage.py` | both usage screens, alternating | whichever keys you have |
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

### The two usage screens

<p align="center">
  <img src="../docs/usage-claude.png" width="300" alt="Claude usage screen">
  <img src="../docs/usage-codex.png" width="300" alt="Codex usage screen">
</p>

`claude` and `codex` are one screen — the burn monitor in `burnscreen.py`: a
histogram of the 5-hour window filling up, ghost bars projecting where it lands,
and RUNOUT racing RESET IN underneath.

The shape of that is deliberately small. A screen is a `burnscreen.Screen`:

```python
Screen(theme, mascot, feed, gif, badge, notice=None, sub=None, waiting="loading...")
```

and `Screen.frame(t)` is the only way anything is drawn — standalone or in the
rotation, so the two can't diverge. `burnscreen.Feed` polls off the render
thread and sorts failures into the three states a screen can show (live model /
key to re-save / nothing to read). `burnscreen.run()` is the stream loop. The
verdict a screen colours itself by is `burnmodel.BurnModel.state`, defined once
on the base class; a feed subclasses it for its own extra fields and may
override only `live`. Layout goes in `burnscreen`, the curve in `burnmodel` —
neither belongs in a source file.

The feeds differ, and that is the only behavioural difference between them:

- **`claudeusage`** calls the claude.ai limits API. It needs the `sessionKey`
  cookie, saved from the panel's Claude pane (kept 0600 outside the repo), and
  records the utilization it sees into its own history file so past bars can't
  change retroactively.
- **`codexusage`** calls chatgpt.com's Codex usage API — `wham` is the
  backend's name for Codex, so it is `GET /backend-api/wham/usage`, carrying a
  bearer token minted from the `__Secure-next-auth.session-token` cookie via
  `/api/auth/session`. `primary_window` is the 5h limit, `secondary_window` the
  weekly one. Because it reads the *account*, it works no matter which machine
  runs Codex.
- With no cookie saved, `codexusage` falls back to the rate-limit snapshots
  Codex logs on this machine (`~/.codex/sessions/**/rollout-*.jsonl`). Those are
  dated, so they are their own history — but they only exist where Codex runs
  and only while it runs, so the badge then reads `IDLE <age>` instead of a
  trend. The fallback is chosen only when there is *no* cookie: once one is
  saved, an API failure is an error, never a silent slide back to stale numbers.

Both record what they observe into a per-window history file so past bars are
frozen observations; that bookkeeping and the projection live in `burnmodel.py`.

`stream_usage.py` shows the two in turn. The device takes a single stream
client, so this can't be two processes: it asks each module for its `Screen` and
hands the list to `run()`, which holds the one connection. A
screen that can only show a notice (expired key, feed that never answered) is
skipped while the other is healthy; if neither can draw, it rotates through both,
because then the notices are the information. Interval: 패널, or `--rotate`.

Both cookies are `HttpOnly`, so getting one by hand means DevTools → Application
→ Cookies → copy → paste into the panel. `extension/` is a small Chrome
extension that does that errand in one click for both providers, and badges the
toolbar when a session is about to expire — see `extension/README.md`.

## Logs

Every process logs through `logs.py` — one rotating file per process in the
platform log dir (`~/Library/Logs/SmallTVWidget` on macOS), capped at 4 MB each.

```sh
python stream.py logs               # tail whatever is streaming now
python stream.py logs panel -n 200  # a specific process
```

The panel has a viewer (the 로그 card) and a 상세도 switch in 전역 설정;
`SMALLTV_LOG_LEVEL=DEBUG` overrides it for one run.

Three rules the format exists to enforce, each of them a measured problem from
before it existed:

- **Bounded.** `claude.log` had reached 29 MB / 644,678 lines with nothing to
  truncate it. `RotatingFileHandler`, 1 MB × 3 backups.
- **Dated and levelled.** Not one of those lines had a timestamp. Now every line
  is `MM-DD HH:MM:SS L name: message`. Routine per-poll numbers are DEBUG;
  INFO is reserved for changes (a feed's verdict flipping, a connect, a source
  switch); a failing fetch is WARNING and a persistent one ERROR.
- **No repeat storms.** 92,150 of those lines were a single stuck retry.
  `logs.Collapse` writes the first, suppresses the rest, and re-emits once every
  five minutes carrying the tally (`... (x51 in 300s)`).

Each spawned source also gets a raw `<name>.err.log`: only for failures that
never reach Python logging, like the interpreter dying before startup (that is
how the reaped-`_MEI` bug showed itself). It is capped at 256 KB.

## Library

```python
from smalltv import SmallTV
tv = SmallTV("smalltv-ultra.local")  # device IP or mDNS hostname

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
