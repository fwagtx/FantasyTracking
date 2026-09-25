"""The phone every vertical promo clip plays on: an iPhone 15 Pro.

Drawn after the real thing -- iPhone 15 Pro proportions (a 393 x 852 pt
display), the Dynamic Island, a thin black glass border inside a
titanium band, the side buttons -- and showing the site the way a phone
really does: the iOS status bar on top, Safari's bottom address bar
("streakpros.com") underneath, the home indicator below that.

Two looks, picked per clip:

  brand    black iPhone on the StreakPros navy-and-flame backdrop
  ambient  natural-titanium iPhone on a blurred, darkened copy of the
           clip itself, so the backdrop takes the page's own colours

The phone is kept inside the part of a Reel / TikTok / Short that the
app leaves clear: below its top tabs, above the caption, and left of the
like/comment/share column.

The status bar and Safari's bar are painted every frame from the page's
own colour (its gutter, top and bottom), and the clock, icons and
address are black on a light page and white on a dark one -- as iOS
does -- so a clip that cuts from a white page to a navy title card
stays right throughout.

  promo_phone.py --draw [--chromium PATH]   redraw the layers (committed PNGs)
"""
import argparse
import asyncio
import os
import subprocess
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "promo_assets")

CANVAS = (1080, 1920)
# The screen, in canvas pixels, and its parts. 598 px for 393 pt.
SX, SY, SW, SH = 229, 168, 598, 1296
K = SW / 393
R_SCREEN = round(55 * K)
STATUS_H = 82
CONTENT = (SX, SY + STATUS_H, SW, 1064)          # the clip itself, 9:16
SAFARI_Y = CONTENT[1] + CONTENT[3]
SAFARI_H = SY + SH - SAFARI_Y                     # 150
EDGE_SRC = 10     # first row of the 1080x1920 clip under its progress sliver
EDGE = round(EDGE_SRC * CONTENT[3] / 1920)

LOOKS = {"brand": ("brand", "black"), "ambient": ("blur", "titanium")}
LAYER = {
    "bg-brand": "phone15-bg-brand.png",
    "body-black": "phone15-body-black.png",
    "body-titanium": "phone15-body-titanium.png",
    "top": "phone15-top.png",
    "ui": "phone15-ui.png",
    "mask": "phone15-screen-mask.png",
}


# --- drawing the layers ---------------------------------------------------

def _css(k):
    bezel, band = 7.5 * k, 4.2 * k
    return bezel, band, SW + 2 * (bezel + band), SH + 2 * (bezel + band)


def _page(body, fonts=""):
    return (f"<!doctype html><html><head><meta charset=utf-8><style>{fonts}"
            "*{margin:0;padding:0;box-sizing:border-box}"
            f"body{{width:{CANVAS[0]}px;height:{CANVAS[1]}px;overflow:hidden;background:transparent;"
            "font-family:'Source Sans 3',-apple-system,sans-serif}"
            f"</style></head><body>{body}</body></html>")


def bg_brand():
    lines = "".join(f'<div style="position:absolute;top:0;bottom:0;left:{i * 108}px;width:2px;'
                    f'background:rgba(255,255,255,.035)"></div>' for i in range(11))
    return _page('<div style="position:absolute;inset:0;background:'
                 'radial-gradient(900px 700px at 50% 38%,rgba(242,84,45,.35),transparent 70%),'
                 'radial-gradient(1400px 1200px at 50% 45%,#16305a 0%,#0d1a33 50%,#070b14 100%)"></div>'
                 + lines)


def body(style):
    """The phone with its screen left transparent (punched out after)."""
    bezel, band, bw, bh = _css(K)
    left, top = SX - bezel - band, SY - bezel - band
    r_out = R_SCREEN + bezel + band
    if style == "titanium":
        metal = ("linear-gradient(90deg,#8d8a86 0%,#d2cfca 1.2%,#6f6c68 2.6%,#a3a09b 50%,"
                 "#6f6c68 97.4%,#d2cfca 98.8%,#8d8a86 100%)")
        btn = "linear-gradient(90deg,#77746f,#bdbab5 50%,#77746f)"
    else:
        metal = ("linear-gradient(90deg,#3a3a3d 0%,#7a7a80 1.2%,#2a2a2d 2.6%,#3d3d41 50%,"
                 "#2a2a2d 97.4%,#7a7a80 98.8%,#3a3a3d 100%)")
        btn = "linear-gradient(90deg,#2c2c2f,#5c5c62 50%,#2c2c2f)"
    b = lambda side, y, h: (  # noqa: E731
        f'<div style="position:absolute;{side}:{-2.6 * K}px;top:{y * K}px;width:{4.2 * K}px;'
        f'height:{h * K}px;border-radius:2px;background:{btn};box-shadow:0 1px 2px rgba(0,0,0,.5)"></div>')
    return _page(f"""
<div style="position:absolute;left:{left}px;top:{top}px;width:{bw}px;height:{bh}px">
  {b("left", 118, 30)}{b("left", 178, 58)}{b("left", 248, 58)}{b("right", 200, 92)}
  <div style="position:absolute;inset:0;border-radius:{r_out}px;background:{metal};
       box-shadow:0 60px 120px -20px rgba(0,0,0,.7),0 30px 50px -25px rgba(0,0,0,.55),inset 0 0 0 1px rgba(255,255,255,.18)">
    <div style="position:absolute;inset:0;border-radius:{r_out}px;background:linear-gradient(180deg,
         rgba(255,255,255,.28) 0,rgba(255,255,255,0) 1.4%,rgba(0,0,0,0) 98.4%,rgba(0,0,0,.35) 100%)"></div>
    <div style="position:absolute;inset:{band}px;border-radius:{R_SCREEN + bezel}px;background:#020203;
         box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.07),inset 0 0 0 3px #000">
      <div style="position:absolute;inset:{bezel}px;border-radius:{R_SCREEN}px;background:#ff00ff"></div>
    </div>
  </div>
</div>""")


def top():
    """Over the page: the Dynamic Island, a faint glass glare, and the
    tinted pill of Safari's address field."""
    return _page(f"""
<div style="position:absolute;left:{SX}px;top:{SY}px;width:{SW}px;height:{SH}px;border-radius:{R_SCREEN}px;overflow:hidden">
  <div style="position:absolute;inset:0;background:linear-gradient(118deg,rgba(255,255,255,.09) 0%,
       rgba(255,255,255,.025) 28%,rgba(255,255,255,0) 42%)"></div>
  <div style="position:absolute;left:{16 * K}px;right:{16 * K}px;top:{SAFARI_Y - SY + 10 * K}px;height:{46 * K}px;
       border-radius:{13 * K}px;background:rgba(118,118,128,.20)"></div>
</div>
<div style="position:absolute;left:{SX + SW / 2 - 63 * K}px;top:{SY + 11 * K}px;width:{126 * K}px;height:{37 * K}px;
     border-radius:40px;background:#000">
  <div style="position:absolute;right:13%;top:32%;width:{12 * K}px;height:{12 * K}px;border-radius:50%;
       background:radial-gradient(circle at 40% 40%,#1c2340,#05060a 70%)"></div>
</div>""")


def ui():
    """Everything iOS writes over the page, in white: the shapes only.
    promo_phone colours them black or white per frame."""
    c = "#fff"
    k = K
    sig = "".join(f'<rect x="{i * 4.5}" y="{10 - (3 + i * 2.3)}" width="3" height="{3 + i * 2.3}" rx=".8" fill="{c}"/>'
                  for i in range(4))
    return _page(f"""
<div style="position:absolute;left:{SX}px;top:{SY}px;width:{SW}px;height:{STATUS_H}px">
  <span style="position:absolute;left:{(SW / 2 - 63 * k) / 2 + 10 * k}px;top:50%;transform:translate(-50%,-40%);
        font-weight:700;font-size:{17 * k}px;letter-spacing:.2px;color:{c}">9:41</span>
  <div style="position:absolute;right:{26 * k}px;top:{21 * k}px;display:flex;align-items:center;gap:{6 * k}px">
    <svg width="{17 * k}" height="{11 * k}" viewBox="0 0 17 11">{sig}</svg>
    <svg width="{16 * k}" height="{11.5 * k}" viewBox="0 0 16 11.5"><path d="M8 11.2 5.6 8.6a3.4 3.4 0 014.8 0z" fill="{c}"/>
      <path d="M3.4 6.4a6.5 6.5 0 019.2 0" stroke="{c}" stroke-width="1.9" fill="none" stroke-linecap="round"/>
      <path d="M1 3.9a10 10 0 0114 0" stroke="{c}" stroke-width="1.9" fill="none" stroke-linecap="round"/></svg>
    <svg width="{26 * k}" height="{12 * k}" viewBox="0 0 26 12"><rect x=".6" y=".6" width="22" height="10.8" rx="3.3" fill="none"
      stroke="{c}" stroke-opacity=".4" stroke-width="1.1"/><rect x="2.2" y="2.2" width="18.8" height="7.6" rx="1.9" fill="{c}"/>
      <path d="M24 4.2v3.6a1.9 1.9 0 000-3.6z" fill="{c}" fill-opacity=".45"/></svg>
  </div>
</div>
<div style="position:absolute;left:{SX + 16 * k}px;width:{SW - 32 * k}px;top:{SAFARI_Y + 10 * k}px;height:{46 * k}px;
     display:flex;align-items:center;justify-content:center;font-weight:600;font-size:{16.5 * k}px;color:{c}">
  <span style="position:absolute;left:{14 * k}px;font-size:{14 * k}px;opacity:.6">AA</span>
  <svg width="{11 * k}" height="{13 * k}" viewBox="0 0 11 13" style="margin-right:{6 * k}px"><rect x="1" y="5.5" width="9" height="7"
    rx="1.6" fill="{c}" opacity=".85"/><path d="M3 5.8V4a2.5 2.5 0 015 0v1.8" fill="none" stroke="{c}" stroke-width="1.4" opacity=".85"/></svg>
  streakpros.com
  <svg style="position:absolute;right:{14 * k}px" width="{16 * k}" height="{16 * k}" viewBox="0 0 16 16" fill="none" stroke="{c}"
    stroke-width="1.7" stroke-linecap="round" opacity=".7"><path d="M13.5 8a5.5 5.5 0 11-1.6-3.9"/><path d="M12.2 1.5v3h-3"/></svg>
</div>
<div style="position:absolute;left:{SX + SW / 2 - 67 * k}px;top:{SY + SH - 13 * k}px;width:{134 * k}px;height:{5 * k}px;
     border-radius:3px;background:{c}"></div>""")


def screen_mask(scale=4):
    """The screen's rounded rectangle, anti-aliased (1 inside)."""
    w, h = CANVAS
    big = np.zeros((h * scale, w * scale), np.uint8)
    x0, y0, x1, y1 = SX * scale, SY * scale, (SX + SW) * scale, (SY + SH) * scale
    r = R_SCREEN * scale
    cv2.rectangle(big, (x0 + r, y0), (x1 - r, y1), 255, -1)
    cv2.rectangle(big, (x0, y0 + r), (x1, y1 - r), 255, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(big, (cx, cy), r, 255, -1, lineType=cv2.LINE_AA)
    return cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255


async def _render(pages, chromium):
    from playwright.async_api import async_playwright
    sys.path.insert(0, HERE)
    from make_thumbnails import font_css
    fonts = font_css(os.path.join(os.environ.get("TMPDIR", "/tmp"), "promo-fonts"))
    async with async_playwright() as p:
        b = await p.chromium.launch(**({"executable_path": chromium} if chromium else {}))
        pg = await b.new_page(viewport={"width": CANVAS[0], "height": CANVAS[1]})
        for name, html in pages:
            await pg.set_content(html.replace("<style>", f"<style>{fonts}", 1), wait_until="load")
            await pg.evaluate("document.fonts.ready")
            path = os.path.join(ASSETS, LAYER[name])
            await pg.screenshot(path=path, omit_background=not name.startswith("bg"))
            if name.startswith("body"):
                im = cv2.imread(path, cv2.IMREAD_UNCHANGED).astype(np.float32)
                im[..., 3] *= 1 - screen_mask()
                cv2.imwrite(path, im.clip(0, 255).astype(np.uint8))
            print("wrote", path)
        await b.close()


def draw(chromium=""):
    os.makedirs(ASSETS, exist_ok=True)
    m = screen_mask()[SY:SY + SH, SX:SX + SW]
    cv2.imwrite(os.path.join(ASSETS, LAYER["mask"]), (m * 255).round().astype(np.uint8))
    asyncio.run(_render([("bg-brand", bg_brand()), ("body-black", body("black")),
                         ("body-titanium", body("titanium")), ("top", top()), ("ui", ui())], chromium))


# --- putting a clip on it ---------------------------------------------------

def compose(clip, out, look="brand"):
    """clip (1080x1920) -> out: the clip playing on the phone, 1080x1920."""
    bg, finish = LOOKS[look]
    cw, ch = CANVAS
    x, y, w, h = CONTENT
    a = lambda n: os.path.join(ASSETS, LAYER[n])  # noqa: E731
    inputs = ["-i", clip]
    loop = ["-loop", "1", "-framerate", "30", "-i"]
    if bg == "brand":
        inputs += loop + [a("bg-brand")]
    inputs += loop + [a(f"body-{finish}")] + loop + [a("top")] + loop + [a("ui")] + loop + [a("mask")]
    n = 1 if bg != "brand" else 2
    ib, it, iu, im = n, n + 1, n + 2, n + 3
    lum = "format=gray,lut=y='if(gt(val,150),0,255)',format=rgba"
    g = f"[0:v]fps=30,setsar=1,split=2[c0][c1];"
    if bg == "brand":
        g += f"[1:v]format=rgba[bg];[c1]nullsink;"
    else:
        # The clip itself, filling the frame, blurred and dimmed.
        g += (f"[c1]scale={cw}:{ch}:flags=bicubic,boxblur=luma_radius=48:luma_power=3:chroma_radius=48:chroma_power=3,"
              f"eq=brightness=-0.16:saturation=1.25,vignette=PI/4,format=rgba[bg];")
    g += (f"[c0]scale={w}:{h}:flags=lanczos,split=3[s][t0][b0];"
          f"[t0]crop=4:2:2:{EDGE},scale=1:1:flags=area,scale={w}:{STATUS_H},split=2[t1][t2];"
          f"[b0]crop=4:2:2:{h - 2},scale=1:1:flags=area,scale={w}:{SAFARI_H},split=2[b1][b2];"
          # The screen as one piece -- status bar, page, Safari bar --
          # cut to the display's rounded corners, then set in the phone.
          f"color=c=black:s={w}x{SH}:r=30[scr0];"
          f"[scr0][t1]overlay=0:0:shortest=1[scr1];"
          f"[scr1][b1]overlay=0:{SAFARI_Y - SY}:shortest=1[scr2];"
          f"[scr2][s]overlay=0:{y - SY}:shortest=1,format=rgba[scr3];"
          f"[{im}:v]format=gray[msk];[scr3][msk]alphamerge[scr];"
          f"[bg][scr]overlay={x}:{SY}:shortest=1[v3];"
          f"[v3][{ib}:v]overlay=0:0:shortest=1[v4];"
          f"[v4][{it}:v]overlay=0:0:shortest=1[v5];"
          f"[{iu}:v]format=rgba,split=2[u0][u1];"
          f"[u0]crop={w}:{STATUS_H}:{x}:{SY},alphaextract[ua];"
          f"[u1]crop={w}:{SAFARI_H}:{x}:{SAFARI_Y},alphaextract[ub];"
          f"[t2]{lum}[tc];[b2]{lum}[bc];[tc][ua]alphamerge[ti];[bc][ub]alphamerge[bi];"
          f"[v5][ti]overlay={x}:{SY}:shortest=1[v6];"
          f"[v6][bi]overlay={x}:{SAFARI_Y}:shortest=1,format=yuv420p[v]")
    tmp = out + ".part.mp4"
    c = cv2.VideoCapture(clip)
    secs = c.get(cv2.CAP_PROP_FRAME_COUNT) / (c.get(cv2.CAP_PROP_FPS) or 30)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *inputs,
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-filter_complex", g, "-map", "[v]", "-map", f"{im + 1}:a",
                    "-c:v", "libx264", "-profile:v", "high", "-level", "4.2", "-preset", "slow", "-crf", "14",
                    "-c:a", "aac", "-b:a", "128k", "-t", f"{secs:.3f}", "-movflags", "+faststart", tmp], check=True)
    os.replace(tmp, out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draw", action="store_true")
    ap.add_argument("--chromium", default="")
    a = ap.parse_args()
    if a.draw:
        draw(a.chromium)


if __name__ == "__main__":
    main()
