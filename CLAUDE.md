# CLAUDE.md — SmallTV-Ultra ESPHome custom firmware

Guidance for Claude Code (and humans) working in this repo. Read this first.

## What this is
A **GeekMagic SmallTV-Ultra** — actually a third-party **robotcity** ESP8266 board
(PCB marked `robotcity@foxmail.com 20250911`) that shipped running GeekMagic's stock
firmware (clock/weather only). It has been **converted to ESPHome** so the owner has
full control + self-hosted OTA. Current firmware = a Wi-Fi-safe clock; the goal is a
rich, self-updating info display.

## Hardware facts (verified)
- **MCU**: ESP8266EX, single core 80/160 MHz, no FPU. MAC `48:3f:da:03:08:67`.
- **Flash**: 4 MB physical (built with `esp01_1m` 1 MB layout).
- **RAM**: ~40 KB free heap after Wi-Fi/web/API. This is the tightest constraint.
- **Display**: ST7789V 240×240, SPI. Needs **inversion ON (INVON)**.
- **Pinmap**: SPI CLK `GPIO14`, MOSI `GPIO13`; CS `GPIO15`, DC `GPIO0`, RST `GPIO2`;
  backlight PWM `GPIO5` (inverted).
- **Serial header**: left edge of board; no DTR/RTS auto-reset. UART adapter = CH340.

## Repo layout (page-based)
- `core.yaml` — shared base (wifi/ota/api/web/safe_mode, display plumbing, fonts,
  backlight, wifi-gated refresh interval). No `display:` or `mode` select — those
  are GENERATED. Don't add page-specific stuff here.
- `pages/<id>/page.yaml` — one screen each (metadata + `requires:` deps + `render:`
  drawing code). See `pages/PAGE_SCHEMA.md`. Current: clock, stocks, pcinfo, weather.
- `tools/build.py` — composes selected pages + core, **checks the RAM/Flash budget**,
  compiles, uploads. The single entry point for building.
- `client/` — dependency-free Python lib (`smalltv`) to drive the device from a PC,
  plus `examples/` (stock_bridge, pc_stats). REST-based; works from curl too.
  `client/widget/` — cross-platform (Mac/Windows) tray/menu-bar app over the same
  REST calls (page switch, brightness, live bridges, start-at-login, settings);
  `smalltv_widget.py` is its entry point, `build/` packages it into a .exe/.app.
- `components/st7789v/` — local patched fractional-framebuffer ST7789 driver (INVON).
- `costs.json` — measured per-page RAM/Flash cost (for `build.py budget`).
- `secrets.yaml` — creds (**git-ignored**; copy from `secrets.yaml.example`).
- `RULES.md` — **dev constitution + recovery net. Read before editing.**
- `DESIGN.md` — **visual design guide** (canvas, palette, fonts, layout, perf-aware drawing). Read before styling a page.
- `CAPABILITIES.md` — hardware limits. `firmware-v1.bin` — serial recovery image (git-ignored).

## Build & deploy workflow — use tools/build.py
`esphome` is not on PATH — the tool calls `python -m esphome` for you.
```sh
python tools/build.py list                        # pages + measured cost
python tools/build.py budget clock stocks         # fast fit estimate (from costs.json)
python tools/build.py compile clock stocks        # real compile + exact RAM/Flash
python tools/build.py upload  clock stocks --device <device-ip>   # refuses if over RAM limit
python tools/build.py measure <page>              # record a page's cost into costs.json
```
- Not every page fits at once (ESP8266 RAM). The budget check is the whole point:
  `upload` won't flash a set that blows the RAM limit.
- Device IP is DHCP and changes (hostname `smalltv-ultra`). Find it by sweeping the
  subnet for `http://<ip>/text_sensor/esphome_version`, or the router client list.
- After deploy, watch ~60 s: if `http://<ip>/sensor/uptime` climbs past 60 s it's
  stable (not in a reboot loop).
- The generator writes `generated.build.yaml` (git-ignored). Don't edit it by hand.

## GOLDEN RULES (full detail in RULES.md — do not skip)
1. **Never block the main loop** — no single `lambda`/`interval`/`on_*` op over ~50 ms.
   No `delay()`, long loops, or repeated full-screen fills.
2. **Gate anything CPU-heavy on `wifi.connected`** (display refresh, animation, game
   logic). This is why the device now self-heals instead of locking up.
3. **Never remove the lifelines**: `wifi`, `ota`, `safe_mode`, `api`, `web_server`,
   `captive_portal`.
4. **Keep display lambdas light** — the fractional framebuffer re-runs the lambda once
   per fragment (~30×), so heavy drawing multiplies.
5. **Test new display code at a slow interval (5–10 s) first**, then tighten.
6. **Watch RAM** — check compile `RAM %`; OOM = crash.

## Recovery net (already baked in — why we rarely need serial)
- `safe_mode: boot_is_good_after: 10min` — any reboot loop eventually boots a minimal
  Wi-Fi+OTA-only mode → re-flash over the air, **no serial needed**.
- `wifi.connected` gating — prevents the silent Wi-Fi-starvation deadlock outright.
- `api: reboot_timeout: 0s` — no spurious reboots when Home Assistant isn't connected.
- Fallback AP `SmallTV-Ultra Fallback` — reachable at `192.168.4.1` if Wi-Fi fails.

### Last-resort serial recovery
1. IO0(GPIO0)→GND, replug USB-C (download mode).
2. `python -m esptool --port COM4 --before no-reset --after no-reset write-flash --flash-mode keep --flash-size keep 0x0 firmware-v1.bin`
3. Remove IO0 jumper, reboot.

## History / hard-won lessons
The device once looked "bricked": a router reboot dropped Wi-Fi, and a heavy display
lambda (3 s per refresh) starved the single core so Wi-Fi could never re-associate —
online but unreachable. Fixed by serial-flashing the network-only image, then adding
`wifi.connected` gating + `safe_mode` hardening. Don't reintroduce blocking display code.
