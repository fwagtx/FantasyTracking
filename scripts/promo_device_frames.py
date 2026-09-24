"""Draw the phone and laptop frames promo_device.py puts clips into.

Each is a full-canvas PNG: backdrop, device and shadow, with a
transparent hole exactly where the clip goes (promo_device.PHONE /
LAPTOP). Drawn as SVG and rendered by Chromium, so the edges are
anti-aliased the same way a browser draws them.

  promo_device_frames.py [--chromium PATH]

Writes scripts/promo_assets/phone.png, phone-ui-ink.png, phone-ui-white.png
and laptop.png. Only needed when
the geometry or the look changes; the PNGs are committed.
"""
import argparse
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from promo_device import ASSETS, LAPTOP, PHONE  # noqa: E402
from make_thumbnails import font_css  # noqa: E402

# The status bar's clock is set in Source Sans 3, the nearest to Apple's
# SF of the faces the promo already uses. Inlined, because the headless
# browser cannot fetch fonts itself (see make_thumbnails.font_css).
FONTS = font_css(os.path.join(os.environ.get("TMPDIR", "/tmp"), "promo-fonts"))

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


def desk():
    """A dark matte surface, like the one the reference iPhone lies on:
    near-black, lit a little from above, with a fine paper grain."""
    return """
    <defs>
      <radialGradient id="deskLight" cx="50%" cy="38%" r="75%">
        <stop offset="0" stop-color="#3a3a3d"/><stop offset=".55" stop-color="#2a2a2c"/>
        <stop offset="1" stop-color="#18181a"/>
      </radialGradient>
      <filter id="grain" x="0" y="0" width="100%" height="100%">
        <feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" seed="7"/>
        <feColorMatrix type="saturate" values="0"/>
        <feComponentTransfer><feFuncA type="linear" slope=".09"/></feComponentTransfer>
      </filter>
      <filter id="soft" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="26"/></filter>
      <filter id="tight" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="5"/></filter>
    </defs>"""


def status_icons(right, mid, k, ink="#fff"):
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
                 f'fill="none" stroke="{ink}" stroke-opacity=".4" stroke-width="{1.1 * u:.1f}"/>')
    parts.append(f'<rect x="{bx + 2 * u:.1f}" y="{by + 2 * u:.1f}" width="{bw - 4 * u:.1f}" '
                 f'height="{bh - 4 * u:.1f}" rx="{2 * u:.1f}" fill="{ink}"/>')
    parts.append(f'<path d="M{bx + bw + 1 * u:.1f} {mid - 2 * u:.1f} a{2 * u:.1f} {2 * u:.1f} 0 0 1 0 {4 * u:.1f} z" '
                 f'fill="{ink}" fill-opacity=".45"/>')
    # Wi-Fi: a dot and two arcs fanning up from it, 90 degrees wide.
    wx = bx - 7 * u - 8.5 * u                 # centre of the fan
    wb = mid + 5.5 * u                        # its base
    parts.append(f'<path d="M{wx:.1f} {wb:.1f} l{-3.2 * u:.1f} {-3.2 * u:.1f} '
                 f'a{4.5 * u:.1f} {4.5 * u:.1f} 0 0 1 {6.4 * u:.1f} 0 z" fill="{ink}"/>')
    for r in (7.8 * u, 11.4 * u):
        dx, dy = r * math.sin(math.pi / 4), r * math.cos(math.pi / 4)
        parts.append(f'<path d="M{wx - dx:.1f} {wb - dy:.1f} A{r:.1f} {r:.1f} 0 0 1 {wx + dx:.1f} {wb - dy:.1f}" '
                     f'fill="none" stroke="{ink}" stroke-width="{2.3 * u:.1f}" stroke-linecap="round"/>')
    # Signal: four bars, rising.
    sx = wx - 8.5 * u - 6 * u - 4 * 4.6 * u
    for i in range(4):
        hgt = (4.5 + 2.6 * i) * u
        parts.append(f'<rect x="{sx + i * 4.6 * u:.1f}" y="{mid + 5.5 * u - hgt:.1f}" width="{3.2 * u:.1f}" '
                     f'height="{hgt:.1f}" rx="{0.9 * u:.1f}" fill="{ink}"/>')
    return "\n      ".join(parts)


def iphone():
    """The iPhone's geometry, in canvas pixels, from promo_device.PHONE.

    Drawn after the one in the reference photo -- an iPhone with the
    notch (X/11/12/13 generation): a black glass front with an even thin
    border, a notch holding the earpiece and camera, a dark metal band
    around the edge, and the side buttons. The screen is the clip plus
    the status bar above it and the home strip below it."""
    x, y, w, h = PHONE["clip"]
    pt = w / 375                              # one iOS point, the screen being 375pt wide
    top, bottom = PHONE["top"], PHONE["bottom"]
    sx, sy, sw, sh = x, y - top, w, top + h + bottom
    bez, band = round(15 * pt), round(4.2 * pt)
    sr = 41 * pt                              # screen corner radius
    fx, fy, fw, fh = sx - bez, sy - bez, sw + 2 * bez, sh + 2 * bez       # the glass front
    ox, oy, ow, oh = fx - band, fy - band, fw + 2 * band, fh + 2 * band   # the metal band
    nw, nd = 162 * pt, 30 * pt                # notch width and depth
    return dict(pt=pt, sx=sx, sy=sy, sw=sw, sh=sh, sr=sr, fx=fx, fy=fy, fw=fw, fh=fh,
                fr=sr + bez, ox=ox, oy=oy, ow=ow, oh=oh, orr=sr + bez + band,
                nx0=sx + (sw - nw) / 2, nx1=sx + (sw + nw) / 2, nd=nd)


def notch_path(g):
    pt, sy, nx0, nx1, nd = g["pt"], g["sy"], g["nx0"], g["nx1"], g["nd"]
    r1, r2 = 6 * pt, 20 * pt                  # shoulder and bottom-corner radii
    t = sy - 2                                # tuck under the top edge of the glass
    return (f"M{nx0 - r1:.1f} {t:.1f} H{nx1 + r1:.1f} "
            f"Q{nx1:.1f} {sy:.1f} {nx1:.1f} {sy + r1:.1f} "
            f"V{sy + nd - r2:.1f} Q{nx1:.1f} {sy + nd:.1f} {nx1 - r2:.1f} {sy + nd:.1f} "
            f"H{nx0 + r2:.1f} Q{nx0:.1f} {sy + nd:.1f} {nx0:.1f} {sy + nd - r2:.1f} "
            f"V{sy + r1:.1f} Q{nx0:.1f} {sy:.1f} {nx0 - r1:.1f} {t:.1f} Z")


def phone_svg():
    cw, ch = PHONE["canvas"]
    g = iphone()
    pt = g["pt"]
    btn = lambda side, top_pt, len_pt: (  # noqa: E731
        f'<rect x="{(g["ox"] - 2.6 * pt) if side == "l" else (g["ox"] + g["ow"] - 1.4 * pt):.1f}" '
        f'y="{g["oy"] + top_pt * pt:.1f}" width="{4 * pt:.1f}" height="{len_pt * pt:.1f}" '
        f'rx="{1.6 * pt:.1f}" fill="url(#btn)"/>')
    cx = (g["nx0"] + g["nx1"]) / 2
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{cw}" height="{ch}">
    {desk()}
    <defs>
      <mask id="hole">
        <rect width="{cw}" height="{ch}" fill="#fff"/>
        <rect x="{g['sx']}" y="{g['sy']}" width="{g['sw']}" height="{g['sh']}" rx="{g['sr']:.1f}" fill="#000"/>
        <path d="{notch_path(g)}" fill="#fff"/>
      </mask>
      <linearGradient id="band" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#6e6e73"/><stop offset=".04" stop-color="#2c2c2f"/>
        <stop offset=".5" stop-color="#1e1e21"/><stop offset=".96" stop-color="#2c2c2f"/>
        <stop offset="1" stop-color="#77777c"/>
      </linearGradient>
      <linearGradient id="bandTop" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#fff" stop-opacity=".22"/><stop offset=".03" stop-color="#fff" stop-opacity="0"/>
        <stop offset=".97" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".35"/>
      </linearGradient>
      <linearGradient id="btn" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#4a4a4f"/><stop offset=".5" stop-color="#2a2a2d"/><stop offset="1" stop-color="#4a4a4f"/>
      </linearGradient>
      <linearGradient id="gloss" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#fff" stop-opacity=".07"/><stop offset=".35" stop-color="#fff" stop-opacity="0"/>
      </linearGradient>
      <radialGradient id="lens" cx="40%" cy="35%" r="60%">
        <stop offset="0" stop-color="#3b4a78"/><stop offset=".45" stop-color="#101426"/><stop offset="1" stop-color="#050608"/>
      </radialGradient>
    </defs>
    <g mask="url(#hole)">
      <rect width="{cw}" height="{ch}" fill="url(#deskLight)"/>
      <rect width="{cw}" height="{ch}" filter="url(#grain)"/>
      <!-- shadow: a wide soft one and a tight contact one -->
      <rect x="{g['ox'] + 10}" y="{g['oy'] + 34}" width="{g['ow']}" height="{g['oh']}" rx="{g['orr']:.1f}"
            fill="#000" opacity=".55" filter="url(#soft)"/>
      <rect x="{g['ox'] + 2}" y="{g['oy'] + 6}" width="{g['ow']}" height="{g['oh']}" rx="{g['orr']:.1f}"
            fill="#000" opacity=".6" filter="url(#tight)"/>
      {btn("l", 92, 18)}{btn("l", 150, 38)}{btn("l", 200, 38)}{btn("r", 160, 62)}
      <rect x="{g['ox']:.1f}" y="{g['oy']:.1f}" width="{g['ow']:.1f}" height="{g['oh']:.1f}" rx="{g['orr']:.1f}" fill="url(#band)"/>
      <rect x="{g['ox']:.1f}" y="{g['oy']:.1f}" width="{g['ow']:.1f}" height="{g['oh']:.1f}" rx="{g['orr']:.1f}" fill="url(#bandTop)"/>
      <rect x="{g['fx'] - .8 * pt:.1f}" y="{g['fy'] - .8 * pt:.1f}" width="{g['fw'] + 1.6 * pt:.1f}" height="{g['fh'] + 1.6 * pt:.1f}"
            rx="{g['fr'] + .8 * pt:.1f}" fill="#0c0c0d"/>
      <rect x="{g['fx']:.1f}" y="{g['fy']:.1f}" width="{g['fw']:.1f}" height="{g['fh']:.1f}" rx="{g['fr']:.1f}" fill="#030304"/>
      <rect x="{g['fx']:.1f}" y="{g['fy']:.1f}" width="{g['fw']:.1f}" height="{g['fh']:.1f}" rx="{g['fr']:.1f}" fill="url(#gloss)"/>
      <!-- the notch: earpiece grille and front camera -->
      <rect x="{cx - 26 * pt:.1f}" y="{g['sy'] + 6 * pt:.1f}" width="{52 * pt:.1f}" height="{5.5 * pt:.1f}"
            rx="{2.75 * pt:.1f}" fill="#1b1b1e" stroke="#2b2b2f" stroke-width="{.6 * pt:.1f}"/>
      <circle cx="{cx + 43 * pt:.1f}" cy="{g['sy'] + 8.75 * pt:.1f}" r="{5 * pt:.1f}" fill="url(#lens)"/>
      <circle cx="{cx + 41.8 * pt:.1f}" cy="{g['sy'] + 7.6 * pt:.1f}" r="{1.1 * pt:.1f}" fill="#8fa4ff" opacity=".55"/>
    </g>
    </svg>"""


def phone_ui_svg(ink):
    """What iOS draws over the page: the time, signal/Wi-Fi/battery either
    side of the notch, and the home indicator. Black on a light page,
    white on a dark one -- promo_device picks which per clip."""
    cw, ch = PHONE["canvas"]
    g = iphone()
    pt = g["pt"]
    mid = g["sy"] + 16 * pt
    left_c = (g["sx"] + g["nx0"]) / 2 + 6 * pt
    right_edge = g["sx"] + g["sw"] - 22 * pt
    hw, hh = 134 * pt, 5 * pt
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{cw}" height="{ch}">
      <text x="{left_c:.1f}" y="{mid:.1f}" dominant-baseline="central" text-anchor="middle"
            font-family="'Source Sans 3',-apple-system,'Helvetica Neue',Arial,sans-serif"
            font-size="{16.5 * pt:.1f}" font-weight="700" letter-spacing=".2" fill="{ink}">9:41</text>
      {status_icons(right_edge, mid, pt * .62, ink)}
      <rect x="{g['sx'] + (g['sw'] - hw) / 2:.1f}" y="{g['sy'] + g['sh'] - 8 * pt - hh:.1f}"
            width="{hw:.1f}" height="{hh:.1f}" rx="{hh / 2:.1f}" fill="{ink}"/>
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
            f"<html><head><style>{FONTS}</style></head>"
            f"<body style='margin:0;background:transparent'>{svg}</body></html>")
        await pg.evaluate("document.fonts.ready")
        await pg.screenshot(path=out, omit_background=True)
        await b.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chromium", default="")
    a = ap.parse_args()
    os.makedirs(ASSETS, exist_ok=True)
    jobs = [(phone_svg(), PHONE["frame"], PHONE["canvas"]),
            (phone_ui_svg("#0b0b0c"), PHONE["ui"]["ink"], PHONE["canvas"]),
            (phone_ui_svg("#ffffff"), PHONE["ui"]["white"], PHONE["canvas"]),
            (laptop_svg(), LAPTOP["frame"], LAPTOP["canvas"])]
    for svg, name, size in jobs:
        out = os.path.join(ASSETS, name)
        asyncio.run(render(svg, size, out, a.chromium))
        print("wrote", out)


if __name__ == "__main__":
    main()
