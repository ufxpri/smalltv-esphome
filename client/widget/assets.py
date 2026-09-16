"""Tray icon images, drawn with Pillow so we ship no binary assets.

The glyph is the SmallTV itself — a 3:4 portrait body with a 1:1 screen butted
under the top bezel and a status LED on the chin. That silhouette is the point:
tray neighbours are nearly all square or round line glyphs, so a filled *tall*
body is what makes this icon findable at 16 px.

`icon.svg` next to this file is the same drawing in vector form and is the
source of truth for the numbers below — both live on a 64-unit grid, so every
coordinate here matches it one to one. Change one, change the other.

State is carried by colour only, never by shape, so the icon never "jumps" in
the tray when the device goes away.
"""
from PIL import Image, ImageDraw

# --- geometry, on the 64-unit grid of icon.svg ---
BODY = (11, 4, 53, 60)        # 42 x 56 = 3:4
SCREEN = (16, 9, 48, 41)      # 32 x 32 = 1:1, 5u bezel left/right/top
LED = (32, 51, 3)             # cx, cy, r — on the chin
BODY_R, SCREEN_R, RIM_W = 10, 5.5, 2.5
WARN_H, WARN_R = 7, 3.5       # the device's own top warning bar, in miniature

# --- palette, straight from DESIGN.md ---
INK = (15, 20, 27, 255)       # 0x0F141B  body
RIM = (230, 236, 242, 255)    # 0xE6ECF2  bezel highlight
RIM_OFF = (107, 118, 132, 255)
CYAN = (0, 229, 255, 255)     # 0x00E5FF  screen, online
AMBER = (255, 176, 0, 255)    # 0xFFB000  streaming
GREEN = (0, 227, 107, 255)    # 0x00E36B  healthy LED
RED = (255, 64, 96, 255)      # 0xFF4060  low heap
DARK = (57, 67, 79, 255)      # a screen that isn't lit
MUTED = (90, 100, 114, 255)   # 0x5A6472  dead LED

SCREENS = {"online": CYAN, "streaming": AMBER}
LEDS = {"online": GREEN, "streaming": AMBER, "low_heap": RED}

SS = 4  # supersample factor — ImageDraw has no antialiasing, so draw big and shrink


def make_icon(online: bool = True, size: int = 64, state: str = None) -> Image.Image:
    """The SmallTV glyph for one state: online / streaming / offline / low_heap.

    `online` stays the first argument for callers that only know that much.
    """
    state = state or ("online" if online else "offline")
    s = size * SS
    k = s / 64.0
    box = lambda c: [v * k for v in c]

    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle(box(BODY), radius=BODY_R * k, fill=INK,
                        outline=RIM_OFF if state == "offline" else RIM,
                        width=max(1, round(RIM_W * k)))
    d.rounded_rectangle(box(SCREEN), radius=SCREEN_R * k,
                        fill=SCREENS.get(state, DARK))
    if state == "low_heap":
        # mirror the red health bar the device paints across its own top edge
        x0, y0, x1, _ = SCREEN
        d.rounded_rectangle(box((x0, y0, x1, y0 + WARN_H)), radius=WARN_R * k, fill=RED)
    cx, cy, r = LED
    d.ellipse(box((cx - r, cy - r, cx + r, cy + r)), fill=LEDS.get(state, MUTED))

    return img.resize((size, size), Image.LANCZOS)


# --- build-time app icons ---------------------------------------------------
# The .ico / .icns the packaged app wears are GENERATED, never committed: this
# repo ships no binary assets, and a stale checked-in icon is exactly the kind
# of thing that silently survives a rebuild. build/smalltv_widget.spec calls
# this and hands the result to PyInstaller.

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def write_app_icon(out_dir: str) -> str:
    """Render the app icon for this platform into `out_dir`; return its path.

    Each size is drawn at its own resolution rather than downscaled from one
    master, so the 16 px frame keeps its bezel instead of smearing into grey.
    """
    import os
    import sys

    os.makedirs(out_dir, exist_ok=True)
    if sys.platform == "darwin":
        path = os.path.join(out_dir, "SmallTVWidget.icns")
        make_icon(size=1024).save(path)
        return path

    path = os.path.join(out_dir, "SmallTVWidget.ico")
    frames = [make_icon(size=n) for n in ICO_SIZES]
    frames[-1].save(path, format="ICO", sizes=[(n, n) for n in ICO_SIZES],
                    append_images=frames[:-1])
    return path
