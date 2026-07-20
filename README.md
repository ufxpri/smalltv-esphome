# SmallTV-Ultra — ESPHome custom firmware

Custom [ESPHome](https://esphome.io) firmware for a **GeekMagic SmallTV-Ultra**
(third-party *robotcity* ESP8266 board, ST7789V 240×240 display), replacing the locked
stock firmware with a fully controllable, self-updating (OTA) info display.

> ⚠️ **Private repo recommended.** `secrets.yaml` and `*.bin` are git-ignored because
> compiled firmware bakes in your Wi-Fi/OTA passwords. Never commit them.

## Features
- **Hybrid display**: a lean set of **local pages** (clock, weather) runs on the
  device so it works with the PC off — while anything rich is **rendered on the PC**
  and streamed to the device as changed tiles. Local pages cost the ESP8266's scarce
  RAM permanently; PC sources cost it nothing per-source, so that's where the good
  stuff lives.
- **PC sources**: live candlestick charts (MA/Bollinger/volume/RSI, cycling tickers),
  S&P sector heatmap, a CPU-load furnace, GIF slideshows, video playback.
- **Control panel** (`client/control_panel.py`) — a local web UI to switch sources,
  set brightness, and watch a live monitor (screen mirror, patch heatmap, fps/heap).
  A cross-platform tray app keeps it running.
- **Budget-checked builds**: `tools/build.py` composes any page subset into one
  firmware and **checks it fits in RAM/Flash before uploading**.
- Web UI + REST + native ESPHome API + wireless OTA.
- **Self-healing**: display refresh is gated on Wi-Fi so heavy drawing can never
  starve the radio; `safe_mode` recovers any reboot loop over the air (no serial).

## Quick start
```sh
cp secrets.yaml.example secrets.yaml     # then edit with your Wi-Fi/OTA passwords
python tools/build.py list               # see available local pages
python tools/build.py upload clock weather --device <device-ip>

pip install pillow numpy psutil
python client/control_panel.py           # web UI -> pick a source
```

## Docs
- **[CLAUDE.md](CLAUDE.md)** — project guide: hardware, build workflow, rules, recovery.
- **[pages/PAGE_SCHEMA.md](pages/PAGE_SCHEMA.md)** — how to write a local page (read
  the "local page vs PC source" call in CLAUDE.md first — usually you want a source).
- **[client/README.md](client/README.md)** — the PC tools: stream sources, control
  panel, tray widget, REST library.
- **[RULES.md](RULES.md)** — firmware dev rules + recovery/prevention (read before editing).
- **[CAPABILITIES.md](CAPABILITIES.md)** — what this ESP8266 can/can't do, and the limits.

## Hardware
ESP8266EX · 4 MB flash · ST7789V 240×240 (INVON) · SPI CLK14/MOSI13, CS15/DC0/RST2,
backlight PWM5 (inverted). First flash was over the stock unauthenticated `/update`
web uploader; updates are OTA thereafter. Last-resort serial recovery via the board's
UART header (CH340) — see CLAUDE.md.
