# DESIGN.md — visual design guide for SmallTV-Ultra pages

How to make pages that look good **and** stay within the hardware's tight
performance envelope. Read alongside `pages/PAGE_SCHEMA.md` (structure) and
`RULES.md` (the golden performance rules). This file is about *pixels*.

## The canvas
- **240 × 240 px, square** ST7789V LCD (not round). Panel needs INVON.
- Origin `(0,0)` = top-left. Center = `(120,120)`. X → right, Y → down.
- Color is `Color(0xRRGGBB)` (RGB565 on the wire — very subtle gradients band).

### Safe area
```
 y 0 ┌───────────────────────────┐  ← 0–6 px: reserved health-warning bar
     │        (reserved)         │     (hidden when healthy; red/amber/blue
 y 8 ├───────────────────────────┤      on low-heap / slow-render / weak-wifi)
     │                           │
     │        content           │  ← put everything here
     │                           │
y232 │                           │
     └───────────────────────────┘
```
- **Start real content at y ≥ 8.** The top 6 px belongs to the dispatcher's
  health bar and can light up at any time — don't put anything you care about
  under it.
- **PC stream sources own the full 240×240.** While a stream client is connected
  the local page render is suspended, so the dispatcher never draws its health
  bar — a source can use y 0–7. The flip side: that warning is invisible while
  streaming, so watch `/sensor/free_heap` (the panel's monitor shows it).
- Keep a **~8 px margin** on the left/right/bottom edges. Match the gutter of the
  screen you're editing; don't mix within one.

## Type
Three shared fonts live in `core.yaml` (Roboto). Use these unless you have a
strong reason to add a page-specific font (which costs RAM/Flash — see budget):

| id           | face           | size | use for                          |
|--------------|----------------|------|----------------------------------|
| `font_big`   | Roboto Medium  | 60   | the one hero number (clock, price)|
| `font_med`   | Roboto         | 22   | titles, primary values           |
| `font_small` | Roboto         | 14   | labels, secondary text, badges   |

- **One hero element per screen.** A single `font_big` focal point reads far
  better on a 240 px panel than several medium ones competing.
- Align deliberately with `TextAlign::` — `CENTER`, `TOP_LEFT`, `TOP_RIGHT`,
  `BASELINE_*`. Right-align numeric columns so digits line up.
- `it.printf`/`it.strftime` render live values; keep format strings short.

## Color palette
A small, consistent palette. Reuse these — don't invent a new blue per page.

### Neutrals (structure & text)
| role            | hex        | notes                              |
|-----------------|------------|------------------------------------|
| Background      | `0x000000` | always; the panel is OLED-like black|
| Text primary    | `0xFFFFFF` | main readouts                      |
| Text secondary  | `0x9AA7B4` | labels, ticker, muted info         |
| Text disabled   | `0x808A94` | "waiting…", placeholders           |
| Divider / rule  | `0x2A3542` | hairlines under a title            |
| Track / trough  | `0x203040` | the unfilled part of a bar/gauge   |

### Accent & brand
| role            | hex        | seen in            |
|-----------------|------------|--------------------|
| Cyan (accent)   | `0x00E5FF` | headings, weather  |
| Amber (accent)  | `0xFFB000` | dates, titles      |

### Semantic (meaning — keep these consistent everywhere)
| meaning              | hex        | used for                         |
|----------------------|------------|----------------------------------|
| Up / good / positive | `0x00E36B` | gains, "LIVE", healthy           |
| Down / bad / negative| `0xFF4060` | losses, errors, low-heap warning |
| Caution              | `0xFFA000` | slow-render warning, pre-market  |
| Info / neutral state | `0x2E86FF` | weak-wifi warning, after-hours   |
| Muted / off / closed | `0x5A6472` | disabled, "CLOSED"               |

**Rule:** green means up/good, red means down/bad — never swap them, and don't
reuse the semantic reds/greens for decoration.

## Layout patterns (reuse these)
**Header + divider** (`client/stream_sectors.py`): a title at the top-left, a
`0x2A3542` hairline a few px below it, then content. Clean way to label a screen.
```cpp
it.printf(14, 14, id(font_med), Color(0xFFB000), TextAlign::TOP_LEFT, "%s", title);
it.line(14, 46, 226, 46, Color(0x2A3542));
```

**Corner metadata** (`client/stream_stocks.py`): primary label top-left, primary value top-right,
delta under the value, a small status badge on the opposite corner. Corners read
well because the eye isn't hunting a centered block.

**Hero + progress** (Clock): one big centered number, a secondary line under it,
and a thin progress bar with a leading dot near the bottom edge (`y ≈ 198`).

**Pill badge** (`client/stream_stocks.py` session badge): a `filled_rectangle` behind centered
`font_small` text. Fixed width per label (don't measure text in the lambda):
```cpp
it.filled_rectangle(8, 36, bw, 18, bg);           // bg = a semantic color
it.printf(8 + bw/2, 45, id(font_small), Color(0xFFFFFF), TextAlign::CENTER, "%s", lbl);
```

**Bars / gauges** — draw the track first, then the fill on top:
```cpp
it.filled_rectangle(20, 198, 200, 4, Color(0x203040));   // track
it.filled_rectangle(20, 198,   w, 4, Color(0x00FF7F));   // fill (w = value)
```

## Drawing primitives (what `it.` gives you — all vector, all cheap)
`printf` / `strftime` · `line` · `rectangle` / `filled_rectangle` ·
`circle` / `filled_circle` · `triangle` / `filled_triangle` · `fill` (whole
frame — the dispatcher already does this once; don't call it again per page).

## Performance-aware design (this is what keeps it pretty *and* stable)
The fractional framebuffer **re-runs the page render lambda ~30× per frame**
(once per horizontal strip). So visual cost multiplies. Design accordingly:

- **Vectors only.** No bitmaps/images, no per-pixel loops, no gradients built
  by looping over rows. Shapes + text + a few fills is the whole toolbox.
- **Precompute; don't parse in the render.** Do heavy work (decode, math,
  string building) once in an `on_value:`/`interval:` and stash the result in a
  `global`; the render just reads it.
- **Keep the lambda short.** It runs 30× — every `printf` and loop iteration is
  paid 30 times. Aim to keep a single render pass well under ~50 ms (the golden
  rule); watch the amber health bar as a live "too slow" signal.
- **Bound your loops.** A chart caps at N bars; a list caps at a few rows. Never
  iterate an unbounded data structure in the render.
- If a design genuinely needs more headroom, the cheapest lever is lowering the
  display's `fragmentation` (more RAM per strip, fewer lambda re-runs) — not
  fancier drawing. See `RULES.md`.

## Checklist for a new / redesigned page
- [ ] Content starts at y ≥ 8; edges have a consistent margin.
- [ ] Exactly one hero element; sizes chosen from the 3 shared fonts.
- [ ] Colors come from this palette; green/red used only with their meaning.
- [ ] Numbers right-aligned; labels muted (`0x9AA7B4`).
- [ ] No bitmaps, no per-pixel loops, no unbounded loops in the render.
- [ ] Heavy computation moved to `on_value`/`interval` + a `global`.
- [ ] Tested at a slow interval first; amber health bar never latches on.
