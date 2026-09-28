<h1 align="center">
  <img src="client/widget/icon.svg" width="38" alt=""><br>
  SmallTV-Ultra
</h1>

<p align="center">
  <b>Custom ESPHome firmware for the GeekMagic SmallTV-Ultra — and a 240×240 desk display<br>
  that shows how much of your Claude Code and OpenAI Codex usage limit is left.</b>
</p>

<p align="center">
  <img alt="ESPHome custom firmware" src="https://img.shields.io/badge/ESPHome-custom%20firmware-222?style=flat-square">
  <img alt="ESP8266EX" src="https://img.shields.io/badge/MCU-ESP8266EX-5b6672?style=flat-square">
  <img alt="ST7789V 240x240" src="https://img.shields.io/badge/display-ST7789V%20240%C3%97240-5b6672?style=flat-square">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/client-Python%203.10%2B-5b6672?style=flat-square">
  <img alt="macOS and Windows" src="https://img.shields.io/badge/client-macOS%20%C2%B7%20Windows-5b6672?style=flat-square">
</p>

<p align="center">
  <img src="docs/hero.jpg" width="840"
       alt="A SmallTV-Ultra on a desk showing the Claude usage burn monitor: 77% of the five-hour window used, with the projection reaching the limit in seven minutes.">
</p>

<p align="center">
  <sub><b>77% of the five-hour window gone; the projection says seven minutes left.</b>
  Glanceable from across the desk — which is the entire point.</sub>
</p>

The stock GeekMagic firmware gives you a clock, the weather, and no say in the matter.
This replaces it with [ESPHome](https://esphome.io), then adds the part that makes the
little screen worth having: a **PC-side renderer** that draws a 240×240 frame and streams
only the changed tiles to the device over TCP.

That split is deliberate. The ESP8266 has about 12 KB of free heap, so it keeps a lean set
of **local pages** and still shows something useful with the PC off — while anything rich
(real fonts, Korean text, smooth animation, live API data) is drawn on the PC, where there
is memory to do it. Adding a screen costs the device nothing.

The screens it was built for are **usage monitors for Claude Code and Codex**. Not a
percentage: the shape of the five-hour window filling up, and an honest projection of when
it runs out.

## Screens

| | |
|:--:|:--|
| <img src="docs/usage-claude.png" width="200" alt="Claude Code usage burn monitor"> | **Claude Code usage** — a burn histogram of the five-hour rate-limit window. Solid bars are measured, dashed bars are the projection, warming toward red as they approach the limit. Below, two deadlines race each other: **RUNOUT** (when the current burn rate reaches 100%) against **RESET IN**. The weekly limit runs along the bottom. Reads the claude.ai limits API. |
| <img src="docs/usage-codex.png" width="200" alt="OpenAI Codex usage burn monitor"> | **Codex usage** — the same screen in teal, with a pixel-art terminal in the mascot box. Reads ChatGPT's own Codex usage API, so it reports your *account*: the machine running Codex does not have to be this one. A third source alternates the two on one connection. |
| <img src="docs/src-stocks.png" width="200" alt="Candlestick chart with moving averages, Bollinger bands, volume and RSI"> | **Stocks** — candlesticks with moving averages, Bollinger bands, volume and RSI, cycling through a ticker list you set in the panel. Yahoo symbols, so KOSPI and crypto work too (`005930.KS`, `BTC-USD`). |
| <img src="docs/src-sectors.png" width="200" alt="S&P sector heatmap"> | **S&P sectors** — the eleven SPDR sector ETFs plus SPY as a heatmap, with market breadth in the header. One glance tells you whether it was a broad day or one sector carrying it. |
| <img src="docs/src-furnace.png" width="200" alt="CPU load rendered as a furnace fire"> | **CPU furnace** — your machine's load as a fire that grows and whitens as it climbs. Useless, and the first thing anyone asks about. |

Also: GIF slideshows, video playback (needs `ffmpeg`), and the device's own local pages —
clock and weather — which run on the ESP8266 itself.

## How it works

<p align="center">
  <img src="docs/architecture.svg" width="980"
       alt="Architecture diagram: session cookies authenticate the PC host against the Claude and Codex usage APIs; the host builds an observed burn curve, renders a 240x240 frame, and streams changed 12-pixel tiles over TCP to the ESP8266, which falls back to its own local pages when no client is connected.">
</p>

- **The curve is observed, not reconstructed.** Both APIs report one number — "you are N%
  through this window" — and nothing about how you got there. Every reading is appended to
  a per-window history file, so a past bar is a recorded fact and is never redrawn.
- **Only changed tiles go over the wire.** Each frame is diffed into 12×12 cells against
  what the device already has; in practice that runs at about 9.5 fps and 40–60 KB/s.
- **A stream client owns the screen while it is connected.** Disconnect it and the device
  is back on its own clock a few seconds later — no PC, still a working display.

Both usage screens need a session cookie, and both are `HttpOnly`. A small **Chrome
extension** ([`client/extension/`](client/extension/)) reads them and posts them to the
local control panel, which verifies each one against its provider before saving it `0600`
outside the repo. Its permissions cover two sites and loopback, and nothing else.

## Quick start

**Firmware** — flash once, then everything is over the air:

```sh
cp secrets.yaml.example secrets.yaml     # your Wi-Fi + OTA passwords
python tools/build.py list               # available local pages
python tools/build.py upload clock weather --device <device-ip>
```

`tools/build.py` composes any subset of local pages into one firmware and **checks it fits
in RAM and flash before uploading**. On a 1 MB ESP8266 that check is the difference between
a display and a brick.

**PC side** — the control panel is the UI:

```sh
pip install pillow numpy psutil curl_cffi
python client/control_panel.py           # http://localhost:8787
```

Pick a source and press 전송. For the usage screens, save a session cookie first — the
extension does it in one click, or paste it into the panel. `client/smalltv_widget.py` is a
tray app that keeps the panel running and starts it at login.

## Hardware

ESP8266EX · 4 MB flash · ST7789V 240×240 with inversion on · SPI CLK 14 / MOSI 13, CS 15 /
DC 0 / RST 2, backlight PWM 5 (inverted). The unit sells as a GeekMagic SmallTV-Ultra;
inside it is a third-party *robotcity* board.

The first flash went in over the stock firmware's unauthenticated `/update` uploader — no
soldering, no disassembly. Everything after that is OTA, with `safe_mode` to recover a
reboot loop without touching the device. Last-resort serial recovery uses the board's UART
header (CH340); see [CLAUDE.md](CLAUDE.md).

> ⚠️ `secrets.yaml` and `*.bin` are git-ignored on purpose — compiled firmware bakes in
> your Wi-Fi and OTA passwords. Never commit them.

## Documentation

| | |
|---|---|
| **[CLAUDE.md](CLAUDE.md)** | Project guide: hardware facts, build workflow, the rules, and the recovery net. Start here. |
| **[client/README.md](client/README.md)** | The PC side: stream sources, burn-monitor internals, control panel, tray widget, logging, REST library. |
| **[client/extension/README.md](client/extension/README.md)** | The Chrome extension, and exactly how far its permissions reach. |
| **[pages/PAGE_SCHEMA.md](pages/PAGE_SCHEMA.md)** | Writing a local page — read the "local page vs PC source" call in CLAUDE.md first. Usually you want a source. |
| **[RULES.md](RULES.md)** | Firmware development rules and prevention. Read before editing. |
| **[CAPABILITIES.md](CAPABILITIES.md)** | What this ESP8266 can and cannot do, with the measured limits. |
