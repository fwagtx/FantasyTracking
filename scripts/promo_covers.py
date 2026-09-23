"""Cover images for promo posts, one per clip.

Each cover is a real frame from its own clip -- the page the video is
about, so the cover never promises something the video does not show --
dimmed, with a short hook over it in the same type as the clips' title
cards: a kicker, then a headline of four words or fewer with the payoff
in amber.

  promo_covers.py CLIPS_DIR OUT_DIR

For every <slug>-vertical.mp4 writes <slug>-cover.jpg (1080x1920, for
Reels, TikTok and Shorts); for every <slug>-desktop.mp4 writes
<slug>-cover-wide.jpg (1280x720, for YouTube). JPEG, because that is
what the platforms take for a custom cover.

The hook text lives in HOOKS below, keyed by scene; team and player
clips get theirs from the team name.
"""
import asyncio
import base64
import glob
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_thumbnails import CSS, font_css  # noqa: E402
from promo_capture import TEAMS  # noqa: E402
from promo_plan import catalog  # noqa: E402

# scene: (kicker, headline, payoff) -- headline + payoff read as one line of thought.
HOOKS = {
    "montage": ("Fantasy football", "Every number", "that matters"),
    "tour": ("Full walkthrough", "Every tool.", "One free site."),
    "streaks": ("Prop hit rates", "Who keeps", "hitting?"),
    "tradecalc": ("Trade calculator", "Is your trade", "fair?"),
    "newsfeed": ("Injury report", "Who's hurt", "right now?"),
    "performances": ("Performances", "Every game", "rated"),
    "rankings": ("Dynasty rankings", "Who moved", "this week?"),
    "sbc": ("Start / Bench / Cut", "You", "decide"),
    "gameday": ("Game detail", "Inside", "every game"),
    "player": ("Player profiles", "Any player", "in full"),
    "standings": ("Standings", "Who's actually", "good?"),
    "scores": ("Live scores", "Every game", "live"),
    "team": ("Team pages", "Every team", "one page"),
    "leaguemanager": ("League sync", "All your leagues", "free"),
    "v_perf_qb": ("Quarterbacks", "Best QB games", "rated"),
    "v_perf_rb": ("Running backs", "Best RB games", "rated"),
    "v_perf_wr": ("Wide receivers", "Best WR games", "rated"),
    "v_perf_te": ("Tight ends", "Best TE games", "rated"),
    "v_perf_lb": ("IDP", "Defense gets", "rated too"),
    "v_rank_qb": ("Dynasty rankings", "Every QB", "ranked"),
    "v_rank_rb": ("Dynasty rankings", "Every RB", "ranked"),
    "v_rank_wr": ("Dynasty rankings", "Every WR", "ranked"),
    "v_rank_te": ("Dynasty rankings", "Every TE", "ranked"),
    "v_st_power": ("Power rankings", "Ranked by", "results"),
    "v_st_playoffs": ("Playoff picture", "Who's in", "right now?"),
    "v_st_draft": ("Draft order", "Who picks", "first?"),
    "v_st_nfc": ("NFC standings", "The NFC", "right now"),
    "v_st_nfc_playoffs": ("NFC playoffs", "Who's in", "the NFC?"),
    "v_trade_sf": ("Superflex", "2-QB trade", "values"),
    "v_feed_moves": ("Roster moves", "Who just", "signed?"),
}
for _abbr, _full in TEAMS.items():
    _city, _nick = _full.rsplit(" ", 1)
    HOOKS[f"t_{_abbr.lower()}"] = (_city, f"The {_nick}", "on one page")
    HOOKS[f"p_{_abbr.lower()}"] = (_full, f"Any {_nick} player", "in full")


def slug_to_scene():
    m = {e["slug"]: e["scene"] for v in catalog().values() for e in v}
    m["streakpros-full-walkthrough"] = "tour"
    return m


def best_frame(path):
    """The sharpest frame from the middle of the clip -- the page itself,
    not a title card (which is flat colour and scores near zero)."""
    c = cv2.VideoCapture(path)
    n = int(c.get(7))
    best, score = None, -1.0
    for k in range(12):
        c.set(1, int(n * (0.2 + 0.6 * k / 11)))
        ok, f = c.read()
        if not ok:
            continue
        g = cv2.cvtColor(cv2.resize(f, (270, 480) if f.shape[0] > f.shape[1] else (480, 270)),
                         cv2.COLOR_BGR2GRAY)
        v = cv2.Laplacian(g, cv2.CV_64F).var()
        if v > score:
            best, score = f, v
    return best


def page(frame_b64, hook, tall, fonts):
    kicker, big, em = hook
    # Vertical: the headline sits a little above centre -- inside the
    # 3:4 crop a profile grid shows, and clear of the caption TikTok lays
    # over the bottom quarter. The brand goes under the headline rather
    # than in a corner, where it landed on the site's own logo.
    if tall:
        k, b, s, pad, lift = 40, 176, 40, 84, 300
    else:
        k, b, s, pad, lift = 26, 118, 28, 60, 0
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{fonts}</style><style>{CSS}
.bg{{position:absolute;inset:0;background:url(data:image/jpeg;base64,{frame_b64}) center/cover;
     filter:saturate(1.05) brightness(.40);}}
.shade{{position:absolute;inset:0;background:radial-gradient(90% 55% at 50% 45%,rgba(13,15,13,.55) 0%,
       rgba(13,15,13,.15) 100%),linear-gradient(180deg,rgba(13,15,13,.35) 0%,rgba(13,15,13,0) 30%,
       rgba(13,15,13,0) 65%,rgba(13,15,13,.9) 100%);}}
.big{{text-shadow:0 6px 40px rgba(0,0,0,.7);}}
.sig{{display:flex;align-items:center;gap:.7em;font-weight:900;letter-spacing:.06em;color:#e8e6df;
      background:rgba(13,15,13,.72);border:2px solid rgba(185,122,31,.55);border-radius:999px;
      padding:.45em 1.1em;margin-top:.4em}}
.sig b{{color:#b97a1f}} .sig i{{font-style:normal;font-weight:700;color:#a8ada4;letter-spacing:.02em}}
</style></head><body><div class="card">
  <div class="bg"></div><div class="shade"></div>
  <div class="inner" style="padding:{pad}px;padding-bottom:{pad + lift}px;gap:{pad * 0.4:.0f}px">
    <div class="kicker" style="font-size:{k}px">{kicker}</div>
    <div class="rule" style="width:{k * 3.4:.0f}px;height:{max(5, k // 5)}px"></div>
    <div class="big" style="font-size:{b}px">{big}<br><em>{em}</em></div>
    <div class="sig" style="font-size:{s}px"><span class="dot"></span>STREAK<b>PROS</b><i>&middot; streakpros.com</i></div>
  </div>
</div></body></html>"""


async def render(clips_dir, out_dir):
    from playwright.async_api import async_playwright
    os.makedirs(out_dir, exist_ok=True)
    fonts = font_css(os.path.join(out_dir, ".fontcache"))
    scenes = slug_to_scene()
    jobs = []
    for f in sorted(glob.glob(os.path.join(clips_dir, "*.mp4"))):
        base = os.path.basename(f)[:-4]
        slug, kind = base.rsplit("-", 1)
        scene = scenes.get(slug)
        if kind not in ("vertical", "desktop") or scene not in HOOKS:
            print(f"  skip {base}: no hook for it")
            continue
        tall = kind == "vertical"
        jobs.append((f, HOOKS[scene], tall,
                     os.path.join(out_dir, f"{slug}-cover{'' if tall else '-wide'}.jpg")))
    exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=exe if os.path.exists(exe) else None)
        for clip, hook, tall, out in jobs:
            w, h = (1080, 1920) if tall else (1280, 720)
            frame = best_frame(clip)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
            p = await browser.new_page(viewport={"width": w, "height": h})
            tmp = os.path.join(out_dir, "_cover.html")
            with open(tmp, "w") as fh:
                fh.write(page(base64.b64encode(buf.tobytes()).decode(), hook, tall, fonts))
            await p.goto("file://" + os.path.abspath(tmp), wait_until="networkidle")
            loaded = await p.evaluate("""async () => {
                await document.fonts.load('400 120px Anton');
                await document.fonts.ready;
                return document.fonts.check('400 120px Anton'); }""")
            if not loaded:
                raise SystemExit("Anton did not load -- refusing to write covers in a fallback face")
            png = await p.screenshot(type="png")
            await p.close()
            img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
            cv2.imwrite(out, img, [cv2.IMWRITE_JPEG_QUALITY, 90])
            print(f"  {os.path.basename(out)}  {w}x{h}")
        await browser.close()
    tmp = os.path.join(out_dir, "_cover.html")
    if os.path.exists(tmp):
        os.remove(tmp)


if __name__ == "__main__":
    asyncio.run(render(sys.argv[1], sys.argv[2]))
