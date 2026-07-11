# SmallTV-Ultra — ESPHome custom firmware

Custom [ESPHome](https://esphome.io) firmware for a **GeekMagic SmallTV-Ultra**
(third-party *robotcity* ESP8266 board, ST7789V 240×240 display), replacing the locked
stock firmware with a fully controllable, self-updating (OTA) info display.

> ⚠️ **Private repo recommended.** `secrets.yaml` and `*.bin` are git-ignored because
> compiled firmware bakes in your Wi-Fi/OTA passwords. Never commit them.

## Features
- **Page ecosystem**: each screen (clock, stocks, pcinfo, weather, …) is an
  independent `pages/<id>/page.yaml`. `tools/build.py` composes any subset into one
  firmware and **checks it fits in RAM/Flash before uploading** (the ESP8266 can't
  hold them all). Switch the active page at runtime via the `mode` selector.
- **Live 5-minute candlestick stock chart** — type a ticker in the browser, a PC
  bridge streams candles to the device.
- **PC client library** (`client/`) to push info / switch pages from a PC over REST.
- Web UI + REST + native ESPHome API + wireless OTA.
- **Self-healing**: display refresh is gated on Wi-Fi so heavy drawing can never
  starve the radio; `safe_mode` recovers any reboot loop over the air (no serial).

## Quick start
```sh
cp secrets.yaml.example secrets.yaml     # then edit with your Wi-Fi/OTA passwords
python tools/build.py list               # see available pages
python tools/build.py upload clock stocks pcinfo --device <device-ip>
# then, for the live stock chart:
python client/examples/stock_bridge.py <device-ip>
```

## Docs
- **[CLAUDE.md](CLAUDE.md)** — project guide: hardware, build workflow, rules, recovery.
- **[pages/PAGE_SCHEMA.md](pages/PAGE_SCHEMA.md)** — how to write/contribute a page.
- **[client/README.md](client/README.md)** — the PC client library + examples.
- **[RULES.md](RULES.md)** — firmware dev rules + recovery/prevention (read before editing).
- **[CAPABILITIES.md](CAPABILITIES.md)** — what this ESP8266 can/can't do, and the limits.

## Hardware
ESP8266EX · 4 MB flash · ST7789V 240×240 (INVON) · SPI CLK14/MOSI13, CS15/DC0/RST2,
backlight PWM5 (inverted). First flash was over the stock unauthenticated `/update`
web uploader; updates are OTA thereafter. Last-resort serial recovery via the board's
UART header (CH340) — see CLAUDE.md.
