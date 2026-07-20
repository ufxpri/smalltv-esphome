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
- **RAM**: free heap is the tightest constraint — and TIGHTER than it looks. With
  the full page set (clock+stocks+sectors+worker) + web + api it's only **~7.6 KB**
  free; a lean **clock-only** build frees ~15 KB. Watch it live via the `Free Heap`
  sensor (`/sensor/free_heap`). The top warning bar goes **red under 6 KB** = OOM risk.
- **Display**: ST7789V 240×240, SPI. Needs **inversion ON (INVON)**.
- **Pinmap**: SPI CLK `GPIO14`, MOSI `GPIO13`; CS `GPIO15`, DC `GPIO0`, RST `GPIO2`;
  backlight PWM `GPIO5` (inverted).
- **Serial header**: left edge of board; no DTR/RTS auto-reset. UART adapter = CH340.

## Architecture — lean local pages + PC-rendered streaming
Two ways to put something on the screen, and the split is deliberate:
- **Local pages** run on the ESP8266. Kept to a **minimum (clock, weather)** so the
  device shows something useful with the PC off, and so heap stays fat enough for
  streaming + OTA. Every flashed page costs RAM *permanently* — an inactive page's
  `requires:` globals sit in `.bss` forever (Stocks alone was 2.5 KB of `int[64]`
  chart buffers, a third of the free heap it had, even while showing the clock).
- **PC stream sources** render a 240×240 image on the PC and push changed tiles to
  the driver's TCP server. Cost to the device is **fixed, not per-source**, so this
  is where anything rich or data-hungry belongs. Stocks/sectors/furnace live here.

**Adding a screen? Default to a PC source.** Only add a local page if it must work
with the PC off. There is no way to unload a page's memory at runtime — ESPHome has
no component teardown, so `mode` switching is *not* isolation.

## Repo layout
- `core.yaml` — shared base (wifi/ota/api/web/safe_mode, display plumbing, fonts,
  backlight, wifi-gated refresh interval, `free_heap` sensor). No `display:` or
  `mode` select — those are GENERATED. Don't add page-specific stuff here.
- `pages/<id>/page.yaml` — one local screen each (metadata + `requires:` deps +
  `render:` drawing code). See `pages/PAGE_SCHEMA.md`. Current: **clock, weather**.
- `tools/build.py` — composes selected pages + core, **checks the RAM/Flash budget**,
  compiles, uploads. The single entry point for building.
- `client/` — the PC side, cross-platform (Mac/Windows). See `client/README.md`.
  - `smalltv/` — dependency-free REST lib for the device's own entities.
  - `smalltv_stream.py` — the streaming library (`Streamer`, `resolve_host`, fonts)
    and the furnace source; `stream_stocks/sectors/gif/video.py` — other sources;
    `marketdata.py` — shared Yahoo fetch layer.
  - `stream.py` — runs exactly one source at a time (the device takes one client).
  - `control_panel.py` — **the UI**: local web page (`:8787`) for sources, brightness,
    tickers, device address, and a live monitor. Also the settings page.
  - `widget/` + `smalltv_widget.py` — tray app that only supervises the panel
    (start/stop, status, open, start-at-login); `build/` packages it to .exe/.app.
  - `config.py` — shared config for the widget + panel.
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
- `safe_mode: boot_is_good_after: 10min` — any **software** reboot loop eventually boots
  a minimal Wi-Fi+OTA-only mode → re-flash over the air, **no serial needed**.
  ⚠️ The safe_mode counter lives in RTC, which is **wiped on power loss** — so
  *unplugging/replugging (cold boot) does NOT accumulate it*. Only crashes/soft-resets
  (power kept on) count. Don't try to trigger safe_mode by power-cycling.
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

**Heap OOM → OTA-brick (2026-07). Adding a TCP framebuffer server (WiFiServer + a
few KB of buffers) on top of the full page set pushed free heap under 6 KB. At that
point the device was alive on Wi-Fi but too heap-starved to serve web (port 80 open
but no response) or complete an OTA (it errored ~19% every time — not enough heap for
the receive buffer). Lessons:**
- **Budget heap before adding anything that allocates** (sockets, big buffers, pages).
  Check `/sensor/free_heap`, not just compile-time `RAM %`. Streaming needs a lean
  build (clock-only) to leave headroom; it OOMs on the full page set.
- **Recovering an OTA-brick without serial:** power-cycling did NOT help (safe_mode
  counter is RTC, wiped on power loss — see Recovery net). What worked: **retry OTA in
  a loop.** Each failed attempt crashed the starved device (a *soft* reboot), and after
  a few crashes an upload landed in the brief post-reboot window (ports flapping to
  `refused`) before heap was re-consumed. Do NOT flood connections to force it — that's
  a DoS and gets blocked; plain `esphome upload` retries are the tool.

**PC-rendered streaming (hybrid).** For rich visuals the ESP8266 can't draw (real
fonts, Korean, glow, smooth animation), render on the PC and stream only changed
RGB565 tiles to `st7789v`'s TCP server (`stream_port`, `client/smalltv_stream.py`).
The device stays a normal local-page display until a client connects, then blits the
stream; on disconnect it falls back to the local page. Keep the flashed page set lean
when streaming is enabled.

**Resolved 2026-07-17 — the page set was cut to clock+weather and stocks/sectors/worker
were re-implemented as PC sources.** Measured on the lean build: **free heap ~23 KB with
the stream server running** (vs ~7.6 KB on the full page set, which is what bricked the
OTA). The old fear "streaming needs clock-only" was really "streaming needs a lean page
set" — there is now comfortable margin. `tools/build.py` still sets `stream_port: 6789`
unconditionally, which is safe at this page count but would be dangerous again if the
local set grew back; the RAM% gate cannot see it (the server's cost is runtime heap, not
static RAM), so heap is the number to watch.
