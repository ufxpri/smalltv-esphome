# Writing a page

A **page** is one screen the device can show. Each lives in `pages/<id>/page.yaml`.
The build tool (`tools/build.py`) composes any subset of pages you choose into one
firmware, checks it fits in RAM/Flash, and uploads it. Pages are independent, so
different people can own different pages.

## `page.yaml` format
```yaml
name: My Page          # shown as the `mode` option (what the PC/HA selects)
description: One line.  # shown in `build.py list`
author: your-name

# Optional ESPHome config this page needs (sensors, http_request, globals,
# page-specific fonts, template text/number the PC pushes to, ...). Merged into
# the build. MUST NOT use !secret or !include. Leave "" if you only need what
# core.yaml already provides (fonts font_big/med/small, sntp_time, backlight).
requires: |
  text:
    - platform: template
      name: "my_value"
      id: my_value
      optimistic: true
      mode: text
      max_length: 40        # ESPHome caps text at 255
      initial_value: ""

# Drawing code. `it` is the display buffer (240x240). Runs while this page is
# active. A cyan accent bar at the top (y 0..6) is already drawn for you.
render: |
  it.printf(120, 120, id(font_med), Color(0xFFFFFF), TextAlign::CENTER,
            "%s", id(my_value).state.c_str());
```

## Rules (from RULES.md — they matter, ESP8266 is single-core)
- **Keep `render` light.** It runs ~30× per refresh (once per display fragment).
  Prefer a few draw calls; avoid heavy loops or string parsing in `render`.
- **Parse pushed data ONCE, not in `render`.** If the PC pushes a blob (like the
  Stocks chart), parse it in a `text: on_value:` lambda into `globals`, and have
  `render` only read those globals. See `pages/stocks/page.yaml`.
- **Don't write flash on every update.** For values the PC updates frequently, set
  `restore_value: false` (default) on the template entity — otherwise you wear out
  the flash.
- **Namespace your ids** (`stock_price`, not `price`) so pages don't collide when
  combined.

## Getting data onto a page
1. **PC push (recommended, easiest):** the PC/browser sets a `text`/`number`
   entity over REST; your `render` reads it. See the `client/` library. Great for
   anything the ESP8266 can't fetch itself (HTTPS APIs, heavy JSON).
2. **On-device fetch:** add `http_request:` + an `interval:`/automation in
   `requires` to pull data directly. Works for simple HTTP; HTTPS is RAM-heavy on
   ESP8266, so prefer PC push for big/secure APIs.

## Build, budget, ship
```sh
python tools/build.py list                     # see pages + measured cost
python tools/build.py measure my_page          # record this page's RAM/Flash cost
python tools/build.py budget clock my_page      # fast fit estimate from cache
python tools/build.py compile clock my_page     # real compile + exact usage
python tools/build.py upload  clock my_page --device <ip>
```
`upload` refuses to flash if RAM is over the safe limit. Not every page fits at
once — that's the point of the budget check.
