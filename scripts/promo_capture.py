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
import os
import sys


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

    async def tap(self, selector, index=0, after=1500):
        el = await self.point(selector, index)
        if el is None:
            return False
        await self._eval("() => window.__promoTap()")
        await self.hold(180)
        clicked = True
        try:
            await el.click(timeout=6000)
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
    # And the thing nobody else does: move the line yourself.
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


SCENES = {
    "streaks": scene_streaks,
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
        page = await ctx.new_page()
        stage = Stage(page, base)

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
