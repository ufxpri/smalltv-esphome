# SmallTV-Ultra — ESPHome custom firmware

Custom [ESPHome](https://esphome.io) firmware for a **GeekMagic SmallTV-Ultra**
(third-party *robotcity* ESP8266 board, ST7789V 240×240 display), replacing the locked
stock firmware with a fully controllable, self-updating (OTA) info display.

> ⚠️ **Private repo recommended.** `secrets.yaml` and `*.bin` are git-ignored because
> compiled firmware bakes in your Wi-Fi/OTA passwords. Never commit them.

## Features
- Clock/date display on the 240×240 ST7789 panel (Home-Assistant-ready).
- Web UI + REST control on port 80, native ESPHome API, wireless OTA updates.
- **Self-healing**: display refresh is gated on Wi-Fi so heavy drawing can never
  starve the radio; `safe_mode` recovers any reboot loop over the air (no serial).

## Quick start
```sh
cp secrets.yaml.example secrets.yaml     # then edit with your Wi-Fi/OTA passwords
python -m esphome compile smalltv-ultra.yaml
python -m esphome upload  smalltv-ultra.yaml --device <device-ip>
```

## Docs
- **[CLAUDE.md](CLAUDE.md)** — project guide: hardware, workflow, rules, recovery.
- **[RULES.md](RULES.md)** — firmware dev rules + recovery/prevention (read before editing).
- **[CAPABILITIES.md](CAPABILITIES.md)** — what this ESP8266 can/can't do, and the limits.

## Hardware
ESP8266EX · 4 MB flash · ST7789V 240×240 (INVON) · SPI CLK14/MOSI13, CS15/DC0/RST2,
backlight PWM5 (inverted). First flash was over the stock unauthenticated `/update`
web uploader; updates are OTA thereafter. Last-resort serial recovery via the board's
UART header (CH340) — see CLAUDE.md.
