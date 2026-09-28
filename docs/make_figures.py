#!/usr/bin/env python3
"""Regenerate the README figures from the real renderer.

    python docs/make_figures.py

Right now that is `docs/tiles.svg`, the partial-update figure. It is built by
rendering two consecutive frames, diffing them the way `Streamer.push565` does,
and drawing whatever comes out — so the tile pattern and every byte count in the
figure are measurements, not an illustration of how it might work.
"""
import base64
import io
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "client"))

import burnscreen as bs                              # noqa: E402
import logs                                          # noqa: E402
import stream_claude as claude                       # noqa: E402
from smalltv_stream import TH, TW, H, W              # noqa: E402

GW, GH = W // TW, H // TH          # 20 x 20 tiles of 12 x 12 px
TILE_BYTES = TW * TH * 2           # RGB565


def model(util, delta):
    """A burn model at a given utilization — enough fields for one render."""
    import datetime as dt
    base = dt.datetime(2026, 9, 28, 13, 10, tzinfo=dt.timezone.utc)

    class M:
        pass
    m = M()
    for k, v in dict(
        state="ok", util=util, cum_pct=[4, 11, 19, 28, util, 0, 0, 0, 0, 0],
        now_bin=4, slope=9.0, elapsed_h=2.3, hits_in_h=7.0, weekly=41.0,
        delta=delta, easing=False, bin_min=30.0,
        start_dt=base, now_dt=base + dt.timedelta(hours=2.3),
        end_dt=base + dt.timedelta(hours=5),
    ).items():
        setattr(m, k, v)
    return m


def changed_tiles(a, b):
    """The grid cells that differ — the same 12x12 comparison the streamer makes."""
    da, db = np.asarray(a, dtype=np.int16), np.asarray(b, dtype=np.int16)
    diff = np.abs(da - db).sum(axis=2)
    cells = diff.reshape(GH, TH, GW, TW).sum(axis=(1, 3))
    return sorted(zip(*np.nonzero(cells)))          # [(row, col), ...]


def merged_runs(cells):
    """Horizontally adjacent tiles in a row go out as one wider blit, which is
    what `push565` does to cut per-patch overhead. Returns [(row, col0, span)]."""
    runs, by_row = [], {}
    for r, c in cells:
        by_row.setdefault(r, []).append(c)
    for r, cols in sorted(by_row.items()):
        cols.sort()
        start = prev = cols[0]
        for c in cols[1:]:
            if c != prev + 1:
                runs.append((r, start, prev - start + 1))
                start = c
            prev = c
        runs.append((r, start, prev - start + 1))
    return runs


def png_uri(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def patch_only(frame, cells):
    """The frame with everything the device already has knocked out — literally
    the pixels that travel.

    Transparent, not black, outside the patches: panel 3 lays this over the frame
    the device already holds, and an opaque background would paint that out — which
    is exactly the repaint the figure is about.
    """
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for r, c in cells:
        box = (c * TW, r * TH, (c + 1) * TW, (r + 1) * TH)
        out.paste(frame.crop(box).convert("RGBA"), box)
    return out


def build():
    logs.setup("figures", console=True)   # a CLI script reports its own failures
    sc = claude.screen()

    # Two consecutive frames of an ordinary second: the numbers are unchanged and
    # only the mascot has advanced. That is the steady state — a frame carrying a
    # new reading touches about four times as many tiles, and is still a fraction
    # of a repaint.
    def frame(util, delta, t):
        m = model(util, delta)
        return bs.render(m, sc.theme, sc.mascot, sc.badge(m, False), sc.feed.fx,
                         gif=None, t=t)

    before = frame(37.0, 3.0, 1.0)
    after = frame(37.0, 3.0, 1.3)
    cells = changed_tiles(before, after)
    runs = merged_runs(cells)

    sent = len(cells) * TILE_BYTES
    full = GW * GH * TILE_BYTES
    print(f"  {len(cells)} tiles changed of {GW * GH} "
          f"({len(runs)} blits after merging) — {sent:,} B vs {full:,} B for a full frame")

    svg = FIG.format(
        before=png_uri(before), after=png_uri(after),
        patches=png_uri(patch_only(after, cells)),
        boxes="\n".join(
            f'      <rect x="{c * TW}" y="{r * TH}" width="{TW}" height="{TH}" class="hit"/>'
            for r, c in cells),
        runboxes="\n".join(
            f'      <rect x="{c0 * TW - 0.5}" y="{r * TH - 0.5}" width="{span * TW + 1}" '
            f'height="{TH + 1}" class="run"/>' for r, c0, span in runs),
        n=len(cells), nruns=len(runs), total=GW * GH,
        sent=f"{sent:,}", full=f"{full:,}",
        pct=f"{100 * sent / full:.1f}",
    )
    path = os.path.join(HERE, "tiles.svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"  wrote {path} ({os.path.getsize(path) / 1024:.0f} KB)")


FIG = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1240 580" width="1240" height="580"
     font-family="ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
     role="img" aria-labelledby="t d">
  <title id="t">Partial screen updates: only changed tiles are sent</title>
  <desc id="d">The renderer diffs each new frame against the one the device already
    holds, in 12 by 12 pixel tiles. Only the tiles that differ are sent over TCP, merged
    into wider blits where they are horizontally adjacent, and the device paints them onto
    its existing framebuffer.</desc>
  <defs>
    <style>
      .panel {{ fill: #15171c; stroke: #2b2f38; stroke-width: 1; }}
      .cap   {{ fill: #6d7787; font-size: 11.5px; letter-spacing: 1.6px; font-weight: 600; }}
      .h     {{ fill: #e8e4dc; font-size: 14.5px; font-weight: 600; }}
      .s     {{ fill: #96a0ad; font-size: 12.5px; }}
      .xs    {{ fill: #737d8b; font-size: 11.5px; }}
      .num   {{ fill: #e8e4dc; font-size: 13px;
                font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
      .accent {{ fill: #d97757; }}
      .grid  {{ stroke: #ffffff; stroke-opacity: .07; stroke-width: .5; fill: none; }}
      .hit   {{ fill: #d97757; fill-opacity: .30; stroke: #d97757; stroke-width: .7; }}
      .run   {{ fill: none; stroke: #f0b49c; stroke-width: 1.2; }}
      .edge  {{ stroke: #5c6773; stroke-width: 1.4; fill: none; }}
    </style>
    <marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
            orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="#5c6773"/>
    </marker>
    <pattern id="g" width="12" height="12" patternUnits="userSpaceOnUse">
      <path d="M12,0 L0,0 L0,12" class="grid"/>
    </pattern>
  </defs>

  <rect width="1240" height="580" fill="#0d0f13"/>
  <text x="40" y="44" class="h" font-size="21">Only what changed goes over the wire</text>
  <text x="40" y="68" class="s">Each frame is compared with the one the device already holds, tile by tile. {n} of {total} tiles differ here — {pct}% of a full repaint.</text>

  <!-- 1. rendered on the PC -->
  <text x="60" y="118" class="cap">1 — RENDERED ON THE PC</text>
  <rect x="58" y="130" width="244" height="244" rx="6" class="panel"/>
  <g transform="translate(60,132)">
    <image href="{after}" x="0" y="0" width="240" height="240" image-rendering="pixelated"/>
    <rect x="0" y="0" width="240" height="240" fill="url(#g)"/>
{boxes}
{runboxes}
  </g>
  <text x="60" y="396" class="xs">Diffed against frame N−1 in 12×12 tiles.</text>
  <text x="60" y="414" class="xs">Filled cells differ; outlines are the {nruns} blits</text>
  <text x="60" y="432" class="xs">they merge into, row by row.</text>

  <path d="M318,252 L392,252" class="edge" marker-end="url(#a)"/>
  <text x="355" y="242" class="xs" text-anchor="middle">diff</text>

  <!-- 2. what is transmitted -->
  <text x="400" y="118" class="cap">2 — SENT OVER TCP :6789</text>
  <rect x="398" y="130" width="244" height="244" rx="6" class="panel"/>
  <g transform="translate(400,132)">
    <rect x="0" y="0" width="240" height="240" fill="#000"/>
    <image href="{patches}" x="0" y="0" width="240" height="240" image-rendering="pixelated"/>
{runboxes}
  </g>
  <text x="400" y="396" class="xs">Nothing else is transmitted. The black here is</text>
  <text x="400" y="414" class="xs">not sent as black — it is simply absent from</text>
  <text x="400" y="432" class="xs">the payload.</text>

  <path d="M658,252 L732,252" class="edge" marker-end="url(#a)"/>
  <text x="695" y="242" class="xs" text-anchor="middle">blit</text>

  <!-- 3. the device -->
  <text x="740" y="118" class="cap">3 — PAINTED BY THE DEVICE</text>
  <rect x="738" y="130" width="244" height="244" rx="6" class="panel"/>
  <g transform="translate(740,132)">
    <image href="{before}" x="0" y="0" width="240" height="240" image-rendering="pixelated"/>
    <image href="{patches}" x="0" y="0" width="240" height="240" image-rendering="pixelated"/>
{runboxes}
  </g>
  <text x="740" y="396" class="xs">A whole screen was already there. The tiles</text>
  <text x="740" y="414" class="xs">land on top of it — outlined here, invisible</text>
  <text x="740" y="432" class="xs">in life. No full-screen repaint, no tearing.</text>

  <!-- the arithmetic -->
  <rect x="1010" y="130" width="192" height="244" rx="6" class="panel"/>
  <text x="1028" y="156" class="cap">THE ARITHMETIC</text>
  <text x="1028" y="186" class="num">12 × 12 px</text>
  <text x="1028" y="203" class="xs">one tile</text>
  <text x="1028" y="228" class="num">288 B</text>
  <text x="1028" y="245" class="xs">RGB565, 2 bytes a pixel</text>
  <text x="1028" y="270" class="num accent">{sent} B</text>
  <text x="1028" y="287" class="xs">this frame — {n} tiles, {pct}%</text>
  <text x="1028" y="312" class="num">{full} B</text>
  <text x="1028" y="329" class="xs">a full 20 × 20 repaint</text>
  <line x1="1028" y1="342" x2="1184" y2="342" stroke="#2b2f38"/>
  <text x="1028" y="362" class="xs">Wi-Fi on an ESP8266 could not</text>
  <text x="1028" y="378" class="xs">carry the full frame at 10 fps.</text>

  <line x1="40" y1="478" x2="1200" y2="478" stroke="#22262e"/>
  <text x="40" y="500" class="xs">An ordinary frame: the numbers have not moved, only the mascot has, so the cost of the update is the size of the mascot. A frame that also carries</text>
  <text x="40" y="518" class="xs">a new reading — a taller bar, a new axis label, a redrawn projection — touches about four times as many tiles, and is still a fraction of a repaint.</text>
  <text x="40" y="540" class="xs">A few extra tiles ride along each frame in raster order, so a patch dropped during the initial burst is healed within one sweep rather than waiting for that region to change again.</text>
  <text x="40" y="562" class="xs">Figure generated by docs/make_figures.py from two real consecutive frames — the tile pattern and every number above are measurements.</text>
</svg>
'''

if __name__ == "__main__":
    build()
