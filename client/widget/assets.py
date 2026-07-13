"""Tray icon images, drawn with Pillow so we ship no binary assets."""
from PIL import Image, ImageDraw

ONLINE = (0, 227, 107)     # green
OFFLINE = (150, 160, 170)  # grey
FRAME = (230, 236, 242)


def make_icon(online: bool = True, size: int = 64) -> Image.Image:
    """A little TV/monitor glyph with a chart line; tinted by connection state."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    accent = ONLINE if online else OFFLINE

    m = size // 8
    # screen body
    d.rounded_rectangle([m, m, size - m, size - m * 2], radius=size // 10,
                        fill=(24, 30, 38, 255), outline=FRAME, width=max(2, size // 24))
    # a rising "chart" polyline inside the screen
    x0, x1 = m + size // 8, size - m - size // 8
    y0, y1 = size - m * 2 - size // 6, m + size // 6
    pts = [
        (x0, y0),
        (x0 + (x1 - x0) * 0.30, y0 - (y0 - y1) * 0.25),
        (x0 + (x1 - x0) * 0.55, y0 - (y0 - y1) * 0.65),
        (x0 + (x1 - x0) * 0.75, y0 - (y0 - y1) * 0.45),
        (x1, y1),
    ]
    d.line(pts, fill=accent, width=max(2, size // 20), joint="curve")
    # stand
    foot = size // 6
    d.rectangle([size // 2 - foot, size - m * 2, size // 2 + foot, size - m * 2 + m // 2],
                fill=FRAME)
    return img
