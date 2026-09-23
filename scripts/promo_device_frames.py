"""Draw the phone and laptop frames promo_device.py puts clips into.

Each is a full-canvas PNG: backdrop, device and shadow, with a
transparent hole exactly where the clip goes (promo_device.PHONE /
LAPTOP). Drawn as SVG and rendered by Chromium, so the edges are
anti-aliased the same way a browser draws them.

  promo_device_frames.py [--chromium PATH]

Writes scripts/promo_assets/phone.png and laptop.png. Only needed when
the geometry or the look changes; the PNGs are committed.
"""
import argparse
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from promo_device import ASSETS, LAPTOP, PHONE  # noqa: E402

BG = "#141417"
BG_LIFT = "#26262c"


def backdrop():
    return f"""
    <defs>
      <radialGradient id="lift" cx="50%" cy="45%" r="70%">
        <stop offset="0" stop-color="{BG_LIFT}"/><stop offset="1" stop-color="{BG}"/>
      </radialGradient>
      <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="28"/>
      </filter>
    </defs>"""


def status_icons(right, mid, k):
    """Signal, Wi-Fi and battery, right-aligned at x=right, centred on
    y=mid, drawn at iPhone proportions (k scales them with the phone)."""
    import math
    u = 1.15 * k                              # one icon unit, in px
    parts = []
    # Battery: outline, charge, and the nub on the right.
    bw, bh = 25 * u, 12 * u
    bx = right - bw - 2.5 * u
    by = mid - bh / 2
    parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="{3.8 * u:.1f}" '
                 f'fill="none" stroke="#fff" stroke-opacity=".4" stroke-width="{1.1 * u:.1f}"/>')
    parts.append(f'<rect x="{bx + 2 * u:.1f}" y="{by + 2 * u:.1f}" width="{bw - 4 * u:.1f}" '
                 f'height="{bh - 4 * u:.1f}" rx="{2 * u:.1f}" fill="#fff"/>')
    parts.append(f'<path d="M{bx + bw + 1 * u:.1f} {mid - 2 * u:.1f} a{2 * u:.1f} {2 * u:.1f} 0 0 1 0 {4 * u:.1f} z" '
                 f'fill="#fff" fill-opacity=".45"/>')
    # Wi-Fi: a dot and two arcs fanning up from it, 90 degrees wide.
    wx = bx - 7 * u - 8.5 * u                 # centre of the fan
    wb = mid + 5.5 * u                        # its base
    parts.append(f'<path d="M{wx:.1f} {wb:.1f} l{-3.2 * u:.1f} {-3.2 * u:.1f} '
                 f'a{4.5 * u:.1f} {4.5 * u:.1f} 0 0 1 {6.4 * u:.1f} 0 z" fill="#fff"/>')
    for r in (7.8 * u, 11.4 * u):
        dx, dy = r * math.sin(math.pi / 4), r * math.cos(math.pi / 4)
        parts.append(f'<path d="M{wx - dx:.1f} {wb - dy:.1f} A{r:.1f} {r:.1f} 0 0 1 {wx + dx:.1f} {wb - dy:.1f}" '
                     f'fill="none" stroke="#fff" stroke-width="{2.3 * u:.1f}" stroke-linecap="round"/>')
    # Signal: four bars, rising.
    sx = wx - 8.5 * u - 6 * u - 4 * 4.6 * u
    for i in range(4):
        hgt = (4.5 + 2.6 * i) * u
        parts.append(f'<rect x="{sx + i * 4.6 * u:.1f}" y="{mid + 5.5 * u - hgt:.1f}" width="{3.2 * u:.1f}" '
                     f'height="{hgt:.1f}" rx="{0.9 * u:.1f}" fill="#fff"/>')
    return "\n      ".join(parts)


def phone_svg():
    cw, ch = PHONE["canvas"]
    x, y, w, h = PHONE["clip"]
    k = w / 820                  # everything is drawn relative to the screen width
    sb = round(64 * k)           # status bar above the clip
    bez = round(20 * k)          # bezel
    sr, br = 70 * k, 90 * k      # screen and body corner radius
    sx, sy, sh = x, y - sb, h + sb
    bx, by, bw, bh = sx - bez, sy - bez, w + 2 * bez, sh + 2 * bez
    btn = lambda top, hgt, side: (  # noqa: E731
        f'<rect x="{bx - 5 * k if side == "l" else bx + bw - 3 * k:.1f}" y="{by + top * k:.1f}" '
        f'width="{8 * k:.1f}" height="{hgt * k:.1f}" rx="{3 * k:.1f}" fill="#3a3a40"/>')
    mid = sy + sb * 0.56
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{cw}" height="{ch}">
    {backdrop()}
    <defs>
      <mask id="hole">
        <rect width="{cw}" height="{ch}" fill="#fff"/>
        <rect x="{sx}" y="{sy}" width="{w}" height="{sh}" rx="{sr}" fill="#000"/>
      </mask>
      <clipPath id="screen"><rect x="{sx}" y="{sy}" width="{w}" height="{sh}" rx="{sr}"/></clipPath>
      <linearGradient id="rim" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#5a5a62"/><stop offset=".5" stop-color="#2a2a2f"/>
        <stop offset="1" stop-color="#4a4a52"/>
      </linearGradient>
    </defs>
    <g mask="url(#hole)">
      <rect width="{cw}" height="{ch}" fill="url(#lift)"/>
      <rect x="{bx + 14 * k}" y="{by + 36 * k}" width="{bw}" height="{bh}" rx="{br}" fill="#000" opacity=".55" filter="url(#shadow)"/>
      {btn(260, 70, "l")}{btn(360, 120, "l")}{btn(500, 120, "l")}{btn(400, 180, "r")}
      <rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="{br}" fill="url(#rim)"/>
      <rect x="{bx + 5 * k}" y="{by + 5 * k}" width="{bw - 10 * k}" height="{bh - 10 * k}" rx="{br - 5 * k}" fill="#050506"/>
    </g>
    <g clip-path="url(#screen)">
      <rect x="{sx}" y="{sy}" width="{w}" height="{sb}" fill="#0b0b0d"/>
      <rect x="{sx + w / 2 - 62 * k}" y="{sy + 12 * k}" width="{124 * k}" height="{36 * k}" rx="{18 * k}" fill="#000"/>
      <text x="{sx + 104 * k}" y="{mid}" dominant-baseline="central" text-anchor="middle"
            font-family="-apple-system,'SF Pro Text','Helvetica Neue',Arial,sans-serif"
            font-size="{27 * k:.1f}" font-weight="600" fill="#fff">9:41</text>
      {status_icons(sx + w - 56 * k, mid, k)}
    </g>
    </svg>"""


def laptop_svg():
    cw, ch = LAPTOP["canvas"]
    x, y, w, h = LAPTOP["clip"]
    side, top, bottom = 22, 30, 34
    lx, ly, lw, lh = x - side, y - top, w + 2 * side, h + top + bottom
    base_y = ly + lh
    bw = lw + 240
    bx = (cw - bw) / 2
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{cw}" height="{ch}">
    {backdrop()}
    <defs>
      <mask id="hole">
        <rect width="{cw}" height="{ch}" fill="#fff"/>
        <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="#000"/>
      </mask>
      <linearGradient id="deck" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#6a6a72"/><stop offset=".45" stop-color="#3c3c43"/>
        <stop offset="1" stop-color="#1c1c20"/>
      </linearGradient>
    </defs>
    <g mask="url(#hole)">
      <rect width="{cw}" height="{ch}" fill="url(#lift)"/>
      <ellipse cx="{cw / 2}" cy="{base_y + 40}" rx="{bw / 2}" ry="40" fill="#000" opacity=".6" filter="url(#shadow)"/>
      <rect x="{lx - 3}" y="{ly - 3}" width="{lw + 6}" height="{lh + 3}" rx="28" fill="#4a4a52"/>
      <rect x="{lx}" y="{ly}" width="{lw}" height="{lh}" rx="26" fill="#050506"/>
      <circle cx="{cw / 2}" cy="{ly + top / 2}" r="5" fill="#1f2a36"/>
      <path d="M{bx + 20} {base_y} H{bx + bw - 20} Q{bx + bw} {base_y} {bx + bw} {base_y + 12}
               V{base_y + 20} Q{bx + bw} {base_y + 34} {bx + bw - 30} {base_y + 34}
               H{bx + 30} Q{bx} {base_y + 34} {bx} {base_y + 20} V{base_y + 12}
               Q{bx} {base_y} {bx + 20} {base_y} Z" fill="url(#deck)"/>
      <rect x="{cw / 2 - 110}" y="{base_y}" width="220" height="12" rx="6" fill="#2a2a30"/>
    </g>
    </svg>"""


async def render(svg, size, out, chromium):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        kw = {"executable_path": chromium} if chromium else {}
        b = await p.chromium.launch(**kw)
        pg = await b.new_page(viewport={"width": size[0], "height": size[1]})
        await pg.set_content(
            f"<html><body style='margin:0;background:transparent'>{svg}</body></html>")
        await pg.screenshot(path=out, omit_background=True)
        await b.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chromium", default="")
    a = ap.parse_args()
    os.makedirs(ASSETS, exist_ok=True)
    for svg, dev in ((phone_svg(), PHONE), (laptop_svg(), LAPTOP)):
        out = os.path.join(ASSETS, dev["frame"])
        asyncio.run(render(svg, dev["canvas"], out, a.chromium))
        print("wrote", out)


if __name__ == "__main__":
    main()
