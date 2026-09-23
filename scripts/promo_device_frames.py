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


def phone_svg():
    cw, ch = PHONE["canvas"]
    x, y, w, h = PHONE["clip"]
    sb = 64                      # status bar above the clip
    bez = 20                     # bezel
    sr, br = 70, 90              # screen and body corner radius
    sx, sy, sh = x, y - sb, h + sb
    bx, by, bw, bh = sx - bez, sy - bez, w + 2 * bez, sh + 2 * bez
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
      <rect x="{bx + 14}" y="{by + 36}" width="{bw}" height="{bh}" rx="{br}" fill="#000" opacity=".55" filter="url(#shadow)"/>
      <rect x="{bx - 5}" y="{by + 260}" width="8" height="70" rx="3" fill="#3a3a40"/>
      <rect x="{bx - 5}" y="{by + 360}" width="8" height="120" rx="3" fill="#3a3a40"/>
      <rect x="{bx - 5}" y="{by + 500}" width="8" height="120" rx="3" fill="#3a3a40"/>
      <rect x="{bx + bw - 3}" y="{by + 400}" width="8" height="180" rx="3" fill="#3a3a40"/>
      <rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="{br}" fill="url(#rim)"/>
      <rect x="{bx + 5}" y="{by + 5}" width="{bw - 10}" height="{bh - 10}" rx="{br - 5}" fill="#050506"/>
    </g>
    <g clip-path="url(#screen)">
      <rect x="{sx}" y="{sy}" width="{w}" height="{sb}" fill="#0b0b0d"/>
      <rect x="{sx + w / 2 - 70}" y="{sy + 14}" width="140" height="38" rx="19" fill="#000"/>
      <text x="{sx + 74}" y="{sy + 44}" font-family="-apple-system,Helvetica,Arial,sans-serif"
            font-size="28" font-weight="600" fill="#fff">9:41</text>
      <g fill="#fff" transform="translate({sx + w - 170},{sy + 22})">
        <rect x="0" y="14" width="5" height="8" rx="1"/><rect x="8" y="10" width="5" height="12" rx="1"/>
        <rect x="16" y="6" width="5" height="16" rx="1"/><rect x="24" y="2" width="5" height="20" rx="1"/>
        <path d="M44 20 l7 -7 a10 10 0 0 0 -14 0 z M37 11 a16 16 0 0 1 22 0 l3 -3 a20 20 0 0 0 -28 0 z"/>
        <rect x="72" y="3" width="40" height="19" rx="5" fill="none" stroke="#fff" stroke-width="2" opacity=".6"/>
        <rect x="75" y="6" width="30" height="13" rx="3"/>
        <rect x="114" y="9" width="3" height="7" rx="1" opacity=".6"/>
      </g>
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
