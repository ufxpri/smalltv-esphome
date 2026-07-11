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

## Repo layout
- `smalltv-ultra.yaml` — the ESPHome config (single source of truth).
- `secrets.yaml` — Wi-Fi/OTA/AP credentials (**git-ignored**; copy from `secrets.yaml.example`).
- `components/st7789v/` — local patched copy of lhartmann's fractional-framebuffer
  ST7789 driver (streams 240×240 in fragments so it fits ESP8266 RAM).
- `RULES.md` — **the firmware dev constitution + recovery net. Read before editing.**
- `CAPABILITIES.md` — what this hardware can/can't do and where the limits are.
- `firmware-v1.bin` — network-only recovery image (git-ignored; rebuildable). Serial-flash to un-brick.

## Build & deploy workflow
`esphome` is not on PATH — always use `python -m esphome`.
```sh
python -m esphome compile smalltv-ultra.yaml            # ALWAYS compile first
python -m esphome upload  smalltv-ultra.yaml --device <device-ip>
```
- `esphome upload` alone does NOT recompile — it re-sends the last binary. Compile first.
- Device IP is DHCP and changes (hostname `smalltv-ultra`). Find it by sweeping the
  subnet for `http://<ip>/text_sensor/esphome_version`, or check the router client list.
- After deploy, watch for ~60 s: if `http://<ip>/sensor/uptime` climbs past 60 s, it's
  stable (not in a reboot loop).

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
