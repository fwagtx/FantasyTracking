"""Render click thumbnails for YouTube and TikTok.

A thumbnail has about a third of a second to earn the tap, so each one
here is built around a single curiosity gap and almost no words. The
rules it follows, which are the ones that actually move click-through:

- Four words or fewer at the top size. Anything longer is read as a
  paragraph and skipped.
- One idea per image. A thumbnail that says two things says neither.
- The hook is a QUESTION or a NUMBER, because both leave something
  unresolved that only the video closes.
- Colour does the arguing: green for good, red for bad, the brand amber
  for the thing being sold.
- Vertical covers keep the middle third clear. TikTok puts the caption
  over the bottom and the profile furniture over the top, so anything
  important there is covered on the real feed.

Nothing here claims a number the site cannot show. An advert that
promises a stat the product does not have is a refund request later.
"""
import asyncio, os, sys
from playwright.async_api import async_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "thumbs"

# The site's own dark palette, so a thumbnail and the clip it fronts
# look like the same product.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")
FONT_URL = ("https://fonts.googleapis.com/css2?family=Anton"
            "&family=Source+Sans+3:wght@400;600;700;900&display=block")


def font_css(cache_dir):
    """Google Fonts CSS with every file inlined as a data: URI.

    The headless browser here cannot reach the font CDN even though the
    shell can, so a <link> to Google Fonts silently renders in a
    fallback sans at the display face's metrics -- which is subtle
    enough to ship and wrong enough to matter, since the whole design
    rests on a condensed headline. Fetching the files out here and
    inlining them removes the browser's network from the question.
    """
    import base64, re as _re, subprocess
    os.makedirs(cache_dir, exist_ok=True)
    cached = os.path.join(cache_dir, "fonts.css")
    if os.path.exists(cached) and os.path.getsize(cached) > 20000:
        return open(cached).read()

    def get(url, binary=False):
        out = subprocess.run(["curl", "-sSfL", "-H", f"User-Agent: {UA}", url],
                             capture_output=True, check=True)
        return out.stdout if binary else out.stdout.decode()

    css = get(FONT_URL)
    for url in sorted(set(_re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", css))):
        kind = "woff2" if url.endswith(".woff2") else "ttf"
        mime = "font/woff2" if kind == "woff2" else "font/ttf"
        b64 = base64.b64encode(get(url, binary=True)).decode()
        css = css.replace(url, f"data:{mime};base64,{b64}")
    open(cached, "w").write(css)
    return css


CSS = """
*{ margin:0; padding:0; box-sizing:border-box; }
/* The card is height:100%, so the root elements need a height to be a
   percentage OF. Without this the card collapses and leaves a dead band
   at the bottom of every shot. */
html,body{ height:100%; }
body{ background:#0d0f0d; color:#e8e6df; font-family:"Source Sans 3",system-ui,sans-serif;
      -webkit-font-smoothing:antialiased; overflow:hidden; }
.card{ position:relative; width:100%; height:100%; overflow:hidden;
       background:radial-gradient(120% 90% at 50% 0%, #1c201c 0%, #0d0f0d 62%); }
/* A faint pitch of stripes, so the flat background is not flat. */
.card::before{ content:""; position:absolute; inset:0;
  background:repeating-linear-gradient(90deg, rgba(255,255,255,0.028) 0 2px, transparent 2px 86px); }
.glow{ position:absolute; border-radius:50%; filter:blur(90px); opacity:.55; }
.inner{ position:relative; height:100%; display:flex; flex-direction:column;
        justify-content:center; align-items:center; text-align:center; }
.kicker{ font-weight:900; letter-spacing:.24em; text-transform:uppercase;
         color:#b97a1f; }
.big{ font-family:Anton, Impact, sans-serif; line-height:.86; letter-spacing:-.015em;
      text-transform:uppercase; text-wrap:balance; }
.big em{ font-style:normal; color:#b97a1f; }
.big .g{ color:#1fae5a; } .big .r{ color:#e2534a; }
.sub{ color:#a8ada4; font-weight:700; }
.brand{ position:absolute; display:flex; align-items:center; gap:.5em;
        font-weight:900; letter-spacing:.06em; }
.brand b{ color:#b97a1f; }
.dot{ width:.62em; height:.62em; border-radius:50%; background:#b97a1f;
      box-shadow:0 0 0 .22em rgba(185,122,31,.22); }
.pill{ display:inline-flex; align-items:center; gap:.45em; border-radius:999px;
       font-weight:900; letter-spacing:.04em; text-transform:uppercase; }
.pill.good{ background:rgba(31,174,90,.18); color:#4fcb86; border:2px solid rgba(31,174,90,.5); }
.pill.bad{ background:rgba(226,83,74,.18); color:#f0736a; border:2px solid rgba(226,83,74,.5); }
.pill.amb{ background:rgba(185,122,31,.20); color:#e0a542; border:2px solid rgba(185,122,31,.55); }
/* The number hooks. Huge, and allowed to bleed. */
.num{ font-family:Anton, Impact, sans-serif; line-height:.8; color:#1fae5a;
      text-shadow:0 0 70px rgba(31,174,90,.45); }
.num.amber{ color:#b97a1f; text-shadow:0 0 70px rgba(185,122,31,.5); }
.rule{ height:6px; width:90px; background:#b97a1f; border-radius:99px; }
.bars{ display:flex; align-items:flex-end; gap:8px; }
.bars i{ display:block; width:26px; border-radius:5px 5px 2px 2px; background:#1fae5a; }
.bars i.m{ background:rgba(255,255,255,.14); }
.vs{ display:flex; align-items:center; justify-content:center; width:100%; }
.side{ flex:1; }
.side .t{ font-family:Anton,Impact,sans-serif; text-transform:uppercase; line-height:.9; }
.bal{ height:26px; border-radius:99px; overflow:hidden; display:flex; width:78%;
      border:2px solid rgba(255,255,255,.14); }
.bal u{ display:block; height:100%; text-decoration:none; }
"""

# Each thumbnail: (name, kicker, big-html, sub, extra-html, glow css)
CARDS = [
    ("streaks-12", "Hit rates",
     'HE HAS HIT IT<br><span class="g">12 STRAIGHT</span>',
     "Find every player like him &mdash; free",
     '<div class="bars" data-bars></div>',
     "#1fae5a"),

    ("trade-fair", "Trade calculator",
     'IS THIS<br>TRADE <em>FAIR?</em>',
     "Priced on real market value",
     '<div class="bal"><u style="width:46%;background:#e2534a"></u>'
     '<u style="width:54%;background:#1fae5a"></u></div>',
     "#b97a1f"),

    ("start-sit", "Start or sit",
     'START HIM?<br><span class="r">OR SIT HIM?</span>',
     "Every starter graded, and why",
     '<div class="pill amb" data-pill>A+ &nbsp;&bull;&nbsp; D&minus;</div>',
     "#e2534a"),

    ("rankings-moved", "Dynasty rankings",
     'WHO <em>MOVED</em><br>THIS WEEK?',
     "Value changes most sites hide",
     '<div class="pill good" data-pill>&#9650; RISING</div>',
     "#b97a1f"),

    ("everything-free", "StreakPros",
     'EVERY NUMBER<br>THAT <em>MATTERS</em>',
     "Live scores &middot; Rankings &middot; Trades &middot; Streaks",
     '<div class="pill amb" data-pill>100% Free</div>',
     "#b97a1f"),

    ("suggested", "Suggested trades",
     'NAME HIM.<br>WE BUILD <em>THE OFFER.</em>',
     "Priced to your league&rsquo;s settings",
     '<div class="pill good" data-pill>Superflex ready</div>',
     "#1fae5a"),

    ("waivers", "Waiver wire",
     'PICK HIM UP<br><em>BEFORE</em> THEY DO',
     "Ranked for your roster, not a top 50",
     '<div class="pill good" data-pill>This week</div>',
     "#1fae5a"),

    ("rated-10", "Performances",
     'EVERY GAME<br>RATED <span class="g">OUT OF 10</span>',
     "Not points. A rating.",
     '<div class="num" data-num>9.4</div>',
     "#1fae5a"),
]


FONTS = ""


def html(card, shape):
    name, kicker, big, sub, extra, glow = card
    tall = shape == "tall"
    # Type scale per shape. The vertical cover is read at arm's length
    # on a phone, so it goes proportionally bigger, not smaller.
    if tall:
        k, b, s, pad, brand = 34, 152, 36, 80, 32
        gw, gh, gx, gy = 900, 900, -180, -260
        # Centred, then lifted by padding the foot. TikTok lays its
        # caption over the bottom quarter and its buttons up the right
        # edge, so a cover that centres honestly is half covered on the
        # real feed -- but one pinned to the top leaves a dead half,
        # which is how the first pass came out.
        foot = 520
    else:
        k, b, s, pad, brand = 26, 122, 30, 64, 26
        gw, gh, gx, gy = 780, 700, -140, -200
        foot = 0

    bars = ""
    if "data-bars" in extra:
        hs = [34, 52, 44, 66, 58, 74, 62, 86, 78, 96]
        bars = "".join(f'<i style="height:{h * (1.5 if tall else 1.0):.0f}px"></i>' for h in hs)
        extra = extra.replace('<div class="bars" data-bars></div>',
                              f'<div class="bars">{bars}</div>')
    extra = extra.replace('data-pill', f'style="font-size:{s * 0.92:.0f}px;padding:{s*0.5:.0f}px {s*1.1:.0f}px"')
    # 1.5x ran the figure off the bottom edge of the landscape frame.
    extra = extra.replace('data-num', f'style="font-size:{b * (1.35 if tall else 1.05):.0f}px"')

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{FONTS}</style><style>{CSS}</style></head>
<body><div class="card">
  <div class="glow" style="width:{gw}px;height:{gh}px;left:{gx}px;top:{gy}px;background:{glow}"></div>
  <div class="glow" style="width:{gw*0.8:.0f}px;height:{gh*0.8:.0f}px;right:{gx}px;bottom:{gy}px;background:#b97a1f;opacity:.28"></div>
  <div class="inner" style="padding:{pad}px;padding-bottom:{pad + foot}px;gap:{pad*0.42:.0f}px">
    <div class="kicker" style="font-size:{k}px">{kicker}</div>
    <div class="rule" style="width:{k*3.4:.0f}px;height:{max(5, k//5)}px"></div>
    <div class="big" style="font-size:{b}px">{big}</div>
    <div class="sub" style="font-size:{s}px">{sub}</div>
    {extra}
  </div>
  <div class="brand" style="font-size:{brand}px;left:{pad}px;bottom:{pad}px">
    <span class="dot"></span>STREAK<b>PROS</b>
  </div>
</div></body></html>"""


SHAPES = {"yt": (1280, 720, "wide"), "tt": (1080, 1920, "tall")}


async def main():
    global FONTS
    os.makedirs(OUT, exist_ok=True)
    FONTS = font_css(os.path.join(OUT, ".fontcache"))
    async with async_playwright() as pw:
        # The image pinned here is older than the playwright package
        # expects, so point at the one that is actually installed
        # rather than downloading a second copy.
        exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        browser = await pw.chromium.launch(
            executable_path=exe if os.path.exists(exe) else None)
        for key, (w, h, shape) in SHAPES.items():
            page = await browser.new_page(viewport={"width": w, "height": h},
                                          device_scale_factor=1)
            for card in CARDS:
                # Written to a file and opened, not set_content: a
                # document created by set_content lives on about:blank,
                # and a cross-origin webfont requested from there does
                # not resolve. The whole first batch rendered in a
                # fallback sans at Anton's metrics because of it.
                tmp = os.path.join(OUT, "_frame.html")
                with open(tmp, "w") as fh:
                    fh.write(html(card, shape))
                await page.goto("file://" + os.path.abspath(tmp),
                                wait_until="networkidle")
                # Webfonts must be in before the shot. document.fonts.ready
                # alone is not enough -- it resolves before a face that no
                # laid-out text has requested yet has loaded, and the first
                # render then falls back to a plain sans at Anton's metrics,
                # which is how the first batch came out wrong.
                loaded = await page.evaluate("""async () => {
                    await Promise.all([
                        document.fonts.load('400 120px Anton'),
                        document.fonts.load('900 40px "Source Sans 3"'),
                    ]);
                    await document.fonts.ready;
                    return document.fonts.check('400 120px Anton');
                }""")
                if not loaded:
                    print("  WARNING: Anton did not load -- headline face is a fallback",
                          file=sys.stderr)
                await page.wait_for_timeout(250)
                path = os.path.join(OUT, f"{card[0]}-{key}.png")
                await page.screenshot(path=path)
                print(f"  {os.path.basename(path):<28} {w}x{h}")
            await page.close()
        tmp = os.path.join(OUT, "_frame.html")
        if os.path.exists(tmp):
            os.remove(tmp)
        await browser.close()

asyncio.run(main())
