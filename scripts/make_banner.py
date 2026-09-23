"""The StreakPros YouTube channel banner.

  make_banner.py OUT_DIR [--chromium PATH]

Writes youtube-banner.jpg (2560x1440, what YouTube asks for; well under
its 6MB limit) and youtube-banner-safe-areas.jpg, the same image marked
up with how much of it each device shows.

YouTube crops one image three ways: a TV shows all of it, a desktop a
2560x423 strip through the middle, a phone only the centre 1546x423.
So everything that has to be read -- the mark, the name, what the
channel is for, the address -- sits inside that centre box, and the
rest is backdrop that can be lost without losing anything.
"""
import argparse
import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_thumbnails import font_css  # noqa: E402

W, H = 2560, 1440
SAFE = (507, 508, 1546, 423)          # x, y, w, h: shown on every device


def logo_svg():
    src = open(os.path.join(os.path.dirname(HERE), "webapp.py"), encoding="utf-8").read()
    return re.search(r'^LOGO_SVG = """(.*?)"""', src, re.S | re.M).group(1)


def page(fonts):
    x, y, w, h = SAFE
    # Yard lines across the whole canvas: a field, without a stock photo.
    lines = "".join(
        f'<div class="yl" style="left:{i * 128}px"></div>' for i in range(21))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
{fonts}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ width:{W}px; height:{H}px; overflow:hidden; background:#0b0d12; }}
.bg {{ position:absolute; inset:0;
  background:
    radial-gradient(1100px 520px at 50% 50%, rgba(242,84,45,.20), transparent 70%),
    radial-gradient(1500px 900px at 50% 50%, #13284a 0%, #0d1830 45%, #0a0c12 100%); }}
.yl {{ position:absolute; top:0; bottom:0; width:3px; background:rgba(255,255,255,.045); }}
.stripe {{ position:absolute; left:0; right:0; height:6px;
  background:linear-gradient(90deg, transparent, #F2542D 20%, #FFC53D 50%, #F2542D 80%, transparent); }}
.safe {{ position:absolute; left:{x}px; top:{y}px; width:{w}px; height:{h}px;
  display:flex; align-items:center; justify-content:center; gap:56px; padding:0 40px; }}
.mark svg {{ width:220px; height:220px; display:block;
  filter:drop-shadow(0 18px 40px rgba(0,0,0,.55)); }}
.words {{ display:flex; flex-direction:column; gap:12px; }}
.name {{ font-family:'Anton', sans-serif; font-size:138px; line-height:.95;
  letter-spacing:2px; color:#fff; }}
.name em {{ font-style:normal; color:#F2542D; }}
.tag {{ font-family:'Source Sans 3', sans-serif; font-weight:700; font-size:40px; white-space:nowrap;
  color:#dfe6f1; letter-spacing:.5px; }}
.tag b {{ color:#FFC53D; font-weight:900; }}
.row {{ display:flex; align-items:center; gap:22px; margin-top:8px; }}
.pill {{ font-family:'Source Sans 3', sans-serif; font-weight:900; font-size:34px;
  color:#10233F; background:#FFC53D; border-radius:40px; padding:8px 30px; }}
.daily {{ font-family:'Source Sans 3', sans-serif; font-weight:700; font-size:30px;
  color:#fff; border:3px solid rgba(255,255,255,.35); border-radius:40px; padding:6px 26px; }}
</style></head><body>
<div class="bg"></div>{lines}
<div class="stripe" style="top:{y - 40}px"></div>
<div class="stripe" style="top:{y + h + 34}px"></div>
<div class="safe">
  <div class="mark">{logo_svg()}</div>
  <div class="words">
    <div class="name">STREAK<em>PROS</em></div>
    <div class="tag">Live scores <b>·</b> Prop streaks <b>·</b> Dynasty rankings <b>·</b> Trade calculator</div>
    <div class="row"><span class="pill">streakpros.com</span><span class="daily">New videos every day</span></div>
  </div>
</div>
</body></html>"""


async def shoot(html, out, chromium):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(**({"executable_path": chromium} if chromium else {}))
        pg = await b.new_page(viewport={"width": W, "height": H})
        await pg.set_content(html, wait_until="load")
        await pg.evaluate("document.fonts.ready")
        box = await pg.evaluate("""() => { const r = document.querySelector('.words').getBoundingClientRect();
            const m = document.querySelector('.mark').getBoundingClientRect();
            return [Math.min(r.left, m.left), Math.min(r.top, m.top), Math.max(r.right, m.right), Math.max(r.bottom, m.bottom)]; }""")
        x, y, w, h = SAFE
        inside = box[0] >= x and box[1] >= y and box[2] <= x + w and box[3] <= y + h
        print(f"content {[round(v) for v in box]} inside phone-safe box: {inside}")
        assert inside, "text runs outside the area every device shows"
        await pg.screenshot(path=out, type="jpeg", quality=93)
        await b.close()


def mark_up(src, out):
    """The banner with the TV, desktop and phone crops drawn on it."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(src).convert("RGB")
    d = ImageDraw.Draw(im, "RGBA")
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
    except OSError:
        f = ImageFont.load_default()
    x, y, w, h = SAFE
    d.rectangle((0, y, W, y + h), outline=(80, 200, 255, 255), width=6)
    d.text((24, y - 48), "DESKTOP 2560x423", fill=(80, 200, 255), font=f)
    d.rectangle((x, y, x + w, y + h), outline=(120, 255, 120, 255), width=6)
    d.text((x + 12, y + h + 14), "PHONE + ALL DEVICES 1546x423", fill=(120, 255, 120), font=f)
    d.text((24, 24), "TV 2560x1440 (whole image)", fill=(255, 255, 255), font=f)
    im.save(out, quality=88)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out")
    ap.add_argument("--chromium", default="")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    fonts = font_css(os.path.join(a.out, ".fonts"))
    banner = os.path.join(a.out, "youtube-banner.jpg")
    asyncio.run(shoot(page(fonts), banner, a.chromium))
    mark_up(banner, os.path.join(a.out, "youtube-banner-safe-areas.jpg"))
    mb = os.path.getsize(banner) / 1e6
    assert mb < 6, f"{mb:.1f}MB is over YouTube's 6MB limit"
    print(f"wrote {banner} ({W}x{H}, {mb:.2f}MB)")


if __name__ == "__main__":
    main()
