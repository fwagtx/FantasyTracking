"""Record the live site as video, for posting.

Why this exists as a script and a workflow rather than something run by
hand: the sandbox this repo is developed in cannot reach streakpros.com
at all, and a phone screen-recording of a laptop is not a thing anyone
wants to watch. A GitHub Actions runner has real internet, a real
Chromium and ffmpeg, so it can drive the actual production site and hand
back an .mp4.

Each scene is one idea and runs twenty to thirty seconds -- long enough
to show a number changing, short enough to survive a feed. Trim the head
and tail to taste; the clip is deliberately a little generous at both
ends so there is something to cut into.

Two shapes:

  wide  1280x720  -- X, Reddit, YouTube, an embed on the site itself.
                     The page's own .wrap is 1060px, so this leaves a
                     natural gutter rather than a stretched layout.
  tall   540x960  -- TikTok, Reels, Shorts. Under the 760px breakpoint,
                     so this records the real phone layout, tab bar and
                     all. Doubled to 1080x1920 on the way out, which is
                     a clean 2x rather than a soft resample.

Signing in is optional and off unless PROMO_EMAIL and PROMO_PASSWORD are
set. Signed out, the paid panels record as their locked state, which is
honest but makes for a dull clip -- so for feature videos, put a real
account in the repository secrets and leave it at that. Never put a
password anywhere else.

Usage:
    python3 scripts/promo_capture.py --base https://streakpros.com \\
        --scene streaks --shape tall --out ./promo
"""
import argparse
import asyncio
import html
import os
import sys

from promo_overlay import OVERLAY_JS, TOP_ROW_JS


# A pointer, drawn. Playwright's recorder captures the page, not the
# cursor, so without this a click looks like the page changing on its
# own -- which is exactly the thing a demo has to show on purpose.
CURSOR_JS = """
(() => {
  function install() {
    if (document.getElementById('__promo_cursor')) return;
    const d = document.createElement('div');
    d.id = '__promo_cursor';
    d.style.cssText = [
      'position:fixed', 'z-index:2147483647', 'left:-60px', 'top:-60px',
      'width:20px', 'height:20px', 'margin:-10px 0 0 -10px',
      // A ring, not a disc. A filled dot sits on top of whatever it is
      // pressing, and on a pill the size of "L5" that hides the very
      // label the clip is there to show changing.
      'border-radius:50%', 'background:rgba(255,255,255,.16)',
      'border:2px solid rgba(255,255,255,.95)', 'box-sizing:border-box',
      'box-shadow:0 0 0 1px rgba(0,0,0,.45), 0 2px 12px rgba(0,0,0,.55)',
      'pointer-events:none', 'transition:left .28s ease, top .28s ease, transform .12s ease'
    ].join(';');
    document.body.appendChild(d);
  }
  window.__promoMove = (x, y) => {
    install();
    const d = document.getElementById('__promo_cursor');
    d.style.left = x + 'px'; d.style.top = y + 'px';
  };
  window.__promoTap = () => {
    const d = document.getElementById('__promo_cursor');
    if (!d) return;
    d.style.transform = 'scale(.55)';
    setTimeout(() => { d.style.transform = 'scale(1)'; }, 150);
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install);
  } else {
    install();
  }
})();
"""

# Scrolling by wheel events is jerky on video: the recorder samples
# faster than the wheel fires, so the page lurches. Driving it from
# requestAnimationFrame with an ease gives a steady pan instead.
GLIDE_JS = """
([y, ms]) => new Promise(done => {
  const start = window.scrollY, dist = y - start, t0 = performance.now();
  if (Math.abs(dist) < 2 || ms <= 0) { window.scrollTo(0, y); return done(); }
  function step(now) {
    const p = Math.min(1, (now - t0) / ms);
    const e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
    window.scrollTo(0, start + dist * e);
    if (p < 1) requestAnimationFrame(step); else done();
  }
  requestAnimationFrame(step);
})
"""

SHAPES = {
    "wide": {"width": 1280, "height": 720},
    "tall": {"width": 540, "height": 960},
}


class Stage:
    """One recording. Thin wrapper so a scene reads as a storyboard."""

    def __init__(self, page, base):
        self.page = page
        self.base = base.rstrip("/")

    async def visit(self, path, wait_for=None, ms=1400):
        await self.page.goto(self.base + path, wait_until="domcontentloaded")
        if wait_for:
            # A missing selector is not worth failing a whole render
            # over -- the page still records, just without that beat.
            try:
                await self.page.wait_for_selector(wait_for, timeout=12000)
            except Exception:
                print(f"  note: {wait_for} never appeared on {path}", file=sys.stderr)
        await self.hold(ms)

    async def hold(self, ms):
        await self.page.wait_for_timeout(ms)

    async def _eval(self, script, arg=None):
        """Every evaluate runs against a document that a click may have
        just replaced. Losing the context is a missed beat, not a
        failed render."""
        try:
            if arg is None:
                return await self.page.evaluate(script)
            return await self.page.evaluate(script, arg)
        except Exception as e:
            if "Execution context was destroyed" not in str(e):
                print(f"  note: {e}", file=sys.stderr)
            return None

    async def glide(self, to_y, ms=1500):
        await self._eval(GLIDE_JS, [to_y, ms])
        await self.hold(250)

    async def point(self, selector, index=0, settle=420):
        """Move the drawn cursor and the real mouse onto an element."""
        try:
            el = self.page.locator(selector).nth(index)
            await el.scroll_into_view_if_needed(timeout=2500)
            box = await el.bounding_box()
        except Exception:
            print(f"  note: could not find {selector}[{index}]", file=sys.stderr)
            return None
        if not box:
            return None
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        await self._eval("([x, y]) => window.__promoMove(x, y)", [x, y])
        try:
            await self.page.mouse.move(x, y)
        except Exception:
            pass
        await self.hold(settle)
        return el

    async def gated(self):
        """Whether this page is showing a sign-in or plan gate.

        A gated panel still has its buttons in the DOM, behind an
        aria-hidden blur that swallows clicks. Playwright then waits the
        full actionability timeout on every press -- the first live run
        spent forty seconds of a ninety-second clip doing exactly that,
        on a blurred panel, which is the worst footage imaginable.
        """
        found = await self._eval(
            "() => { const g = document.querySelector('.gate-wrap, .gate-card, #spGate');"
            "        return g ? (g.id || g.className) : ''; }")
        if found:
            print(f"  note: page is gated ({found}) -- skipping the locked beats",
                  file=sys.stderr)
        return bool(found)

    async def tap(self, selector, index=0, after=1500):
        el = await self.point(selector, index)
        if el is None:
            return False
        await self._eval("() => window.__promoTap()")
        await self.hold(180)
        clicked = True
        try:
            await el.click(timeout=4000)
        except Exception:
            # A click that starts a navigation can report a timeout
            # while the next document is already loading, so this is
            # not proof that nothing happened.
            print(f"  note: click on {selector}[{index}] did not confirm",
                  file=sys.stderr)
            clicked = False
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        await self.hold(after)
        return clicked


class Film(Stage):
    """A Stage that can also put titles and callouts over the page.

    The methods are deliberately shaped like shot instructions rather
    than DOM calls, because a scene written with them should read as a
    storyboard that someone can argue with.
    """

    async def card(self, ms=2200, **fields):
        safe = {k: (html.escape(str(v)) if k not in ("big",) else v)
                for k, v in fields.items() if v}
        await self._eval("o => window.__promo.card(o)", safe)
        await self.hold(ms)

    async def uncard(self, ms=420):
        await self._eval("() => window.__promo.hideCard()")
        await self.hold(ms)

    async def spotlight(self, selector, tag=None, ms=1500, pad=6):
        ok = await self._eval("a => window.__promo.ring(a[0], a[1], a[2])",
                              [selector, tag, pad])
        if not ok:
            print(f"  note: nothing to spotlight at {selector}", file=sys.stderr)
        await self.hold(ms)
        return bool(ok)

    async def unspotlight(self, ms=360):
        await self._eval("() => window.__promo.hideRing()")
        await self.hold(ms)

    async def mark(self, on=True):
        await self._eval("v => window.__promo.mark(v)", on)

    async def clock(self, ms):
        """Start the progress sliver, given the clip's remaining length."""
        await self._eval("m => window.__promo.progress(m)", ms)

    async def push(self, scale, selector=None, ms=700, hold=900):
        await self._eval("a => window.__promo.zoom(a[0], a[1], a[2])",
                         [scale, selector, ms])
        await self.hold(ms + hold)

    async def pull(self, ms=600, hold=500):
        await self._eval("m => window.__promo.unzoom(m)", ms)
        await self.hold(ms + hold)

    async def headline(self):
        """This week's top row, read off the board it is about to show."""
        return await self._eval(TOP_ROW_JS)


# --- the scenes ---------------------------------------------------------
#
# One feature each. Keep them boring to read and obvious to watch: land,
# let the page settle, pan once, press the thing that makes a number
# move, hold on the result.

async def scene_streaks(s):
    """The signature page: who has cleared a line, and how often."""
    await s.visit("/streaks", wait_for=".sk-item", ms=1800)
    await s.glide(420, 1700)
    await s.hold(700)
    # The window pills are the point -- the same player looks different
    # over ten games than over five.
    await s.tap("#skWindows button", index=2, after=1500)
    await s.tap("#skWindows button", index=3, after=1500)
    await s.glide(0, 800)
    # Into one player, where the bars get big enough to read.
    await s.tap(".sk-item", index=0, after=2200)
    await s.glide(300, 1400)
    await s.hold(800)
    # And the thing nobody else does: move the line yourself -- when
    # the viewer is allowed to. Signed out that panel is blurred, so
    # hold on the game-by-game bars instead, which are the part that
    # reads on a phone anyway.
    if await s.gated():
        await s.glide(560, 1500)
        await s.hold(2400)
        return
    for _ in range(3):
        await s.tap("#spPlus", after=700)
    await s.hold(1200)
    for _ in range(2):
        await s.tap("#spMinus", after=700)
    await s.hold(1600)


async def scene_scores(s):
    """Live game day: the board, then one game opened up."""
    await s.visit("/scores", wait_for=".wrap", ms=2000)
    await s.glide(380, 1700)
    await s.hold(900)
    await s.glide(760, 1500)
    await s.hold(900)
    await s.glide(0, 1000)
    await s.tap("a[href^='/game']", index=0, after=2600)
    await s.glide(340, 1600)
    await s.hold(2200)


async def scene_matchups(s):
    """Every starter graded, with the sentence that explains the grade."""
    await s.visit("/matchups", wait_for=".wrap", ms=2000)
    await s.glide(340, 1700)
    await s.hold(1200)
    await s.glide(680, 1500)
    await s.hold(1400)
    await s.glide(1020, 1500)
    await s.hold(1800)


async def scene_rankings(s):
    """Dynasty values, and which way they moved this week."""
    await s.visit("/rankings", wait_for=".wrap", ms=2400)
    await s.glide(360, 1900)
    await s.hold(1500)
    await s.glide(720, 1700)
    await s.hold(2200)


async def scene_tour(s):
    """One longer walkthrough, for a site embed or a pinned post."""
    await scene_streaks(s)
    await scene_scores(s)
    await scene_matchups(s)


async def scene_streaks_story(s):
    """The Streaks clip, cut as a piece rather than recorded as a demo.

    Hook, proof, payoff, sign-off. The hook is built from the board's
    own top row, so every week's run writes its own title card instead
    of repeating a fixed line -- and the run log prints those numbers,
    which is the caption to type into the app on upload.
    """
    await s.visit("/streaks", wait_for=".sk-item", ms=700)
    top = await s.headline() or {}
    if top.get("name"):
        print(f"  CAPTION: {top.get('name')} ({top.get('team')}) "
              f"{top.get('side')}{top.get('line')} {top.get('prop')} "
              f"-- {top.get('streak') or top.get('rate')}", file=sys.stderr)

    # 0:00 the hook. A number first, a name second: the number is what
    # stops a thumb, and there is about a second and a half to do it.
    streak = top.get("streak") or top.get("rate") or "EVERY STREAK"
    who = top.get("name") or "Every starter"
    line = (f"{top.get('side','')}{top.get('line','')} {top.get('prop','')}").strip()
    await s.card(kicker="NFL streaks, week by week",
                 big=streak.upper().replace(" ", "<em>", 1) + ("</em>" if " " in streak else ""),
                 sub=f"{who} — {line}" if line else who,
                 ms=2100)
    await s.clock(21000)
    await s.uncard()
    await s.mark(True)

    # 0:02 the proof: that row, on the real board, ringed.
    await s.spotlight(".sk-item", tag="Every game, against the line", ms=1900)
    await s.push(1.35, ".sk-item", ms=650, hold=1100)
    await s.unspotlight()
    await s.pull()

    # 0:07 it is not one player -- the whole board reads this way.
    await s.glide(430, 1500)
    await s.hold(900)
    await s.tap("#skWindows button", index=2, after=1500)
    await s.spotlight("#skWindows", tag="Last 5 games, not the season", ms=1700)
    await s.unspotlight()

    # 0:12 the payoff: one player, game by game.
    await s.glide(0, 700)
    await s.tap(".sk-item", index=0, after=2100)
    await s.glide(300, 1200)
    if await s.gated():
        await s.glide(560, 1200)
        await s.hold(1500)
    else:
        for _ in range(3):
            await s.tap("#spPlus", after=650)
        await s.hold(1300)

    # 0:21 the sign-off.
    await s.card(logo=True, url="streakpros.com", free="Free to use", ms=2600)



async def scene_montage(s):
    """The whole site in about forty seconds: every feature worth
    showing, cut fast, with a card naming each one.

    Built as a montage rather than a tour because the two are not the
    same film. A tour walks a page at a time and assumes somebody is
    already interested; a montage assumes three seconds to earn the
    next three, so every beat opens on the thing itself and the card
    lands over it rather than before it.

    Ordered by what a stranger can judge instantly. Live scores first,
    because everyone understands a scoreboard. Streaks second, because
    it is the number nobody else shows. The league tools last, because
    they only mean anything once you believe the data underneath them.
    """
    await s.mark(True)
    await s.clock(42000)

    # 0:00 -- open cold on the hook, no page behind it.
    await s.card(kicker="Fantasy football",
                 big="EVERY<em>number that matters</em>",
                 sub="One site. Free.", ms=2200)
    await s.uncard()

    # 0:02 -- live scores. The universally legible one.
    await s.visit("/scores", wait_for=".sc-day-tabs", ms=1100)
    await s.card(kicker="Live scores", big="EVERY<em>game, live</em>", ms=1500)
    await s.uncard(300)
    await s.point(".sc-day-tab.active")
    await s.glide(420, 1100)
    await s.spotlight(".sc-card, .sc-game", "Your players, in every game", ms=1600)
    await s.unspotlight()

    # 0:09 -- streaks. The thing nobody else has.
    await s.visit("/streaks", wait_for=".sk-item", ms=1000)
    await s.card(kicker="Streaks", big="WHO<em>keeps hitting</em>",
                 sub="Every prop, every position", ms=1600)
    await s.uncard(300)
    await s.spotlight(".sk-item", "Hit rate, not a hunch", ms=1700)
    await s.unspotlight()
    await s.glide(540, 1100)
    await s.hold(700)

    # 0:16 -- rankings, with the movement column.
    await s.visit("/rankings", wait_for=".rk-page", ms=1000)
    await s.card(kicker="Dynasty rankings", big="WHAT<em>everyone is worth</em>", ms=1400)
    await s.uncard(300)
    await s.glide(360, 1000)
    await s.spotlight(".rk-move", "Who moved, and how far", ms=1500)
    await s.unspotlight()

    # 0:22 -- the league tools. This is where it stops being a website
    # and starts being yours.
    await s.visit("/league-manager", wait_for=".panel", ms=1200)
    await s.card(kicker="Your leagues", big="ALL<em>of them, ranked</em>",
                 sub="Best team to worst, with the maths", ms=1600)
    await s.uncard(300)
    await s.spotlight(".lg-stats", "Playoff odds. Title odds. Luck.", ms=1900)
    await s.unspotlight()
    await s.glide(520, 1100)
    await s.hold(600)

    # 0:30 -- suggested trades, the newest and most distinctive thing.
    await s.visit("/suggested-trades", wait_for=".sg-tabs", ms=1100)
    await s.card(kicker="Suggested trades", big="NAME<em>who you want</em>",
                 sub="We work out what it takes", ms=1700)
    await s.uncard(300)
    await s.spotlight(".sg-pk, .panel", "Priced for YOUR league's settings", ms=1800)
    await s.unspotlight()
    await s.glide(480, 1000)
    await s.hold(700)

    # 0:38 -- the sign-off.
    await s.card(logo=True, big="STREAK<em>PROS</em>", url="streakpros.com",
                 free="Free to use", ms=3200)


# Where each scene lands first, so the cold load can be taken before
# the camera is rolling.
SCENE_FIRST_PATH = {"streaks": "/streaks", "streaks_story": "/streaks",
                    "scores": "/scores",
                    "matchups": "/matchups", "rankings": "/rankings",
                    "tour": "/streaks", "montage": "/scores"}

SCENES = {
    "montage": scene_montage,
    "streaks": scene_streaks,
    "streaks_story": scene_streaks_story,
    "scores": scene_scores,
    "matchups": scene_matchups,
    "rankings": scene_rankings,
    "tour": scene_tour,
}


async def sign_in(stage, email, password):
    """Optional. Without it the paid panels record as their locked card."""
    await stage.page.goto(stage.base + "/login", wait_until="domcontentloaded")
    try:
        await stage.page.fill("input[name='email']", email)
        await stage.page.fill("input[name='password']", password)
        await stage.page.click("button[type='submit']")
        await stage.page.wait_for_load_state("domcontentloaded")
        await stage.hold(1200)
    except Exception as e:
        print(f"  note: sign-in did not complete ({e}) -- recording signed out",
              file=sys.stderr)


async def record(base, scene, shape, out_dir, email=None, password=None):
    from playwright.async_api import async_playwright

    size = SHAPES[shape]
    os.makedirs(out_dir, exist_ok=True)

    async with async_playwright() as pw:
        # On a runner, `playwright install chromium` has put the right
        # binary where Playwright looks. The development sandbox ships
        # its own Chromium at a fixed path instead, so allow an override
        # rather than downloading a second copy of the same browser.
        launch = {"args": ["--hide-scrollbars"]}
        chrome = os.environ.get("PROMO_CHROME")
        if chrome:
            launch["executable_path"] = chrome
        browser = await pw.chromium.launch(**launch)
        ctx = await browser.new_context(
            viewport=size,
            record_video_dir=out_dir,
            record_video_size=size,
            # The recorder captures whatever the page draws, so the
            # site's own view transitions end up in the video for free.
            device_scale_factor=1,
        )
        await ctx.add_init_script(CURSOR_JS)
        await ctx.add_init_script(OVERLAY_JS)
        page = await ctx.new_page()
        stage = Film(page, base)

        # Recording starts the moment the context does, so a cold
        # instance waking up is the opening shot of the clip. Pull that
        # first load through a throwaway context instead, and let the
        # real one land on a warm origin.
        warm = SCENE_FIRST_PATH.get(scene)
        if warm:
            try:
                scout = await browser.new_context()
                sp = await scout.new_page()
                await sp.goto(base.rstrip("/") + warm,
                              wait_until="domcontentloaded", timeout=45000)
                await scout.close()
            except Exception as e:
                print(f"  note: warm-up skipped ({e})", file=sys.stderr)

        if email and password:
            await sign_in(stage, email, password)

        try:
            await SCENES[scene](stage)
        except Exception as e:
            # Keep whatever was recorded up to the failure. A short clip
            # is still a clip; a crashed job is nothing.
            print(f"  scene stopped early: {e}", file=sys.stderr)

        await ctx.close()
        await browser.close()

    # Playwright names its recording with a random hash. Clips already
    # renamed by an earlier scene in the same run are skipped, so a run
    # that records several scenes into one directory keeps them all.
    done = {f"{sc}-{sh}.webm" for sc in SCENES for sh in SHAPES}
    videos = sorted(
        (os.path.join(out_dir, f) for f in os.listdir(out_dir)
         if f.endswith(".webm") and f not in done),
        key=os.path.getmtime,
    )
    if not videos:
        raise SystemExit("no video was recorded")
    final = os.path.join(out_dir, f"{scene}-{shape}.webm")
    os.replace(videos[-1], final)
    print(final)
    return final


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=os.environ.get("SITE_HOST", "https://streakpros.com"))
    ap.add_argument("--scene", default="streaks", choices=sorted(SCENES))
    ap.add_argument("--shape", default="wide", choices=sorted(SHAPES))
    ap.add_argument("--out", default="promo")
    a = ap.parse_args()
    asyncio.run(record(a.base, a.scene, a.shape, a.out,
                       os.environ.get("PROMO_EMAIL"),
                       os.environ.get("PROMO_PASSWORD")))


if __name__ == "__main__":
    main()
