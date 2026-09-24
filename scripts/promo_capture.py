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
                     Drawn at 1920x1080.
  tall   390x693  -- TikTok, Reels, Shorts. An iPhone's width, so this
                     records the real phone layout, tab bar and all,
                     drawn at 1080x1920 device pixels.

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


# The look a clip is filmed in: the site's own Theme setting (light,
# dark or gray, and a primary colour), set the way a guest's choice is
# kept -- in localStorage, before the page's first script runs, so the
# first frame is already in it. Signed in, the account's saved choice
# wins, so sign_in() saves this one to the account as well. Varied from
# clip to clip by promo_plan, so a week of posts does not look like one
# video three times a day.
THEMES = ("light", "dark", "gray")
ACCENTS = ("amber", "blue", "red", "green", "yellow", "purple", "orange", "pink")
PAPER = {"dark": "#0d0f0d", "light": "#f6f6f3", "gray": "#1b1d1f"}
LOOK = {"theme": "light", "accent": "red"}


def theme_js(theme, accent):
    return ("try{localStorage.setItem('ffc-theme',%r);"
            "localStorage.setItem('ffc-accent',%r)}catch(e){}" % (theme, accent))


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

# Signed-out calls to action that are not gates, only invitations: the
# header's Sign In / Create Account pair on every page, the Join strip on
# Streaks, the "Sign in to draft" button in the Scores draft card. None
# of them locks anything, but every one of them reads as "you need an
# account" in an advert, which is the one thing a clip must not say.
# Hidden in the recording browser only -- the live site is untouched.
# A stylesheet that is in force from the page's first painted frame.
#
# An init script runs before the page's own <html> is parsed, and a
# <style> added to the document at that moment does not survive the
# parse; adding it again at DOMContentLoaded is after first paint. That
# gap was two frames -- long enough for the rankings sign-in card to
# flash on camera. Watching the document and adding the style the
# moment <head> exists closes it: observer callbacks run before the
# browser can paint.
STYLE_NOW_JS = r"""((id, css) => {
    const add = () => {
      if (document.getElementById(id) || !document.head) return;
      const st = document.createElement('style');
      st.id = id;
      st.textContent = css;
      document.head.appendChild(st);
    };
    add();
    const mo = new MutationObserver(add);
    mo.observe(document, {childList: true, subtree: true});
    addEventListener('load', () => { add(); mo.disconnect(); });
  })"""

HIDE_CTA_JS = r"""
(() => {
  // The Start/Bench/Cut vote popup opens itself 350ms after every page
  // load unless this session has already seen it. Set the site's own
  // flag -- exactly what a visitor who dismissed it has -- so it never
  // opens, and hide the overlay as a backstop.
  try { sessionStorage.setItem('vote_shown', '1'); } catch (e) {}
  // Between game days the Scores page's Player Rankings row is a line
  // saying no one has scored yet -- an empty list on camera. It fills in
  // once games kick off; until then the clip simply skips it.
  const css = `
    .nav-auth, .sk-join, .vote-overlay,
    .sc-draft, .sc-sync-banner,
    .sc-group:has(.sc-perf-empty)
    { display:none !important; }`;
  __STYLE_NOW__('__promoHideCta', css);
})();
""".replace("__STYLE_NOW__", STYLE_NOW_JS)

# In the take only. A page whose gate owns its opening view is never
# opened at all (see WALL_JS); a page with a gate further down -- Streaks
# after its free rows, the line-adjust panel on a streak page -- is
# filmed with that gate taken out, so the free content simply ends where
# it ends. It hides a box; it reveals nothing that was locked.
HIDE_GATE_JS = r"""
(() => {
  const css = `.gate-wrap, .sk-gate, #rkGateWrap, #spGate { display:none !important; }`;
  __STYLE_NOW__('__promoHideGate', css);
})();
""".replace("__STYLE_NOW__", STYLE_NOW_JS)

# Where on this page does the first sign-in wall start? Run on the camera-
# off pass, after the page has settled and been scrolled end to end so
# anything revealed on scroll is revealed. Returns the wall's top in
# document coordinates, whether it is visible without scrolling, and
# what it was, for the log.
WALL_JS = r"""
async () => {
  window.scrollTo(0, document.documentElement.scrollHeight);
  await new Promise(r => setTimeout(r, 450));
  window.scrollTo(0, 0);
  await new Promise(r => setTimeout(r, 150));
  const vh = innerHeight;
  const shown = el => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const hits = [];
  for (const g of document.querySelectorAll(
        '.gate-wrap, .gate-card, .gate-blur, #spGate, .sk-gate, #rkGateWrap')) {
    if (shown(g)) hits.push([g, 'gate ' + (g.id || g.className)]);
  }
  const RX = /\b(sign ?in|sign ?up|log ?in|create (a |an |your )?(free )?account|join (for )?free)\b/i;
  for (const el of document.querySelectorAll('a, button')) {
    if (el.closest('header, nav, footer, #__promo_stage')) continue;
    if (!shown(el)) continue;
    const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (t && t.length < 60 && RX.test(t)) hits.push([el, 'prompt "' + t + '"']);
  }
  // Gates that come AFTER free content by design: the rankings and
  // streaks lists show their free rows first, the streak page its bars.
  // The take hides these outright (HIDE_GATE_JS), so they never stop a
  // page being filmed -- only a gate or prompt that IS the page does.
  // Counting them was how desktop Rankings got refused: at 1920 wide its
  // free rows are short enough that the gate sits in the top half.
  const PARTIAL = '#rkGateWrap, .sk-gate, #spGate';
  let wall = null, what = '', hard = null, hardWhat = '';
  for (const [el, label] of hits) {
    const y = el.getBoundingClientRect().top + scrollY;
    if (wall === null || y < wall) { wall = y; what = label; }
    if (!el.closest(PARTIAL) && (hard === null || y < hard)) { hard = y; hardWhat = label; }
  }
  const path = location.pathname;
  const onAuth = path === '/login' || path === '/signup';
  return { wall: onAuth ? 0 : wall, hard: onAuth ? 0 : hard,
           top: onAuth || (hard !== null && hard < vh * 0.5),
           what: onAuth ? 'redirected to ' + path : (hardWhat || what) };
}
"""


SHAPES = {
    # For YouTube and X: laid out at 1280x720, an ordinary laptop
    # window, and drawn at 1920x1080. Laid out at 1920 CSS px the site's
    # 1060px column sat small in the middle of a sea of margin and every
    # word read as zoomed out; at 1280 the column fills the screen and
    # the text is drawn 1.5x.
    "wide": {"width": 1280, "height": 720},
    # Laid out at 390 CSS px wide -- an iPhone's own width, so the page
    # is exactly the layout a phone gets -- and drawn at 1080x1920 for
    # TikTok, Reels and Shorts. (It used to be laid out at 540px, which
    # made every word 28% smaller once the clip sat on the phone.)
    "tall": {"width": 390, "height": 693},
}
# The picture each shape is filmed at, in device pixels.
FILM = {"wide": (1920, 1080), "tall": (1080, 1920)}


class Camera:
    """Films one page from Chromium's own screencast, at full quality.

    Playwright's built-in recorder takes the same screencast but encodes
    it to VP8 capped at 1 Mbit/s -- for a 1080x1920 picture that is what
    turned small text to mush as it moved. Here every frame is kept as
    Chromium delivers it (JPEG at quality 95) with its timestamp, and
    ffmpeg builds a constant-30fps H.264 at near-lossless quality from
    them, holding each frame for exactly as long as it was on screen."""

    def __init__(self, ctx, page, size, frames_dir):
        self.ctx, self.page, self.size, self.dir = ctx, page, size, frames_dir
        self.frames = []            # (timestamp, path)
        self.cdp = None

    async def start(self):
        import base64
        os.makedirs(self.dir, exist_ok=True)
        self.cdp = await self.ctx.new_cdp_session(self.page)

        def on_frame(ev):
            path = os.path.join(self.dir, f"{len(self.frames):06d}.jpg")
            with open(path, "wb") as f:
                f.write(base64.b64decode(ev["data"]))
            self.frames.append((ev["metadata"]["timestamp"], path))
            asyncio.ensure_future(self.cdp.send("Page.screencastFrameAck",
                                                {"sessionId": ev["sessionId"]}))

        self.cdp.on("Page.screencastFrame", on_frame)
        await self.cdp.send("Page.startScreencast", {
            "format": "jpeg", "quality": 95, "everyNthFrame": 1,
            "maxWidth": self.size[0], "maxHeight": self.size[1]})

    async def stop(self):
        import time
        self.stop_time = time.time()
        try:
            await self.cdp.send("Page.stopScreencast")
        except Exception:
            pass

    def write(self, out):
        import shutil
        import subprocess
        if not self.frames:
            return None
        lst = os.path.join(self.dir, "frames.txt")
        end = max(self.frames[-1][0] + 0.5, getattr(self, "stop_time", 0) or 0)
        with open(lst, "w") as f:
            for i, (t, path) in enumerate(self.frames):
                nxt = self.frames[i + 1][0] if i + 1 < len(self.frames) else end
                f.write(f"file '{os.path.abspath(path)}'\nduration {max(nxt - t, 0.001):.6f}\n")
            f.write(f"file '{os.path.abspath(self.frames[-1][1])}'\n")
        w, h = self.size
        subprocess.run([
            "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", lst,
            "-vf", f"scale={w}:{h}:flags=lanczos,fps=30,format=yuv420p",
            "-c:v", "libx264", "-preset", "medium", "-crf", "12",
            "-movflags", "+faststart", out], check=True)
        shutil.rmtree(self.dir, ignore_errors=True)
        return out


MISSED = []
DIVERTED = []
# True during the camera-off pass. Nothing that pass sees is a missed
# beat or a diversion in the finished clip, so it records neither.
DRY = False


def diverted(what):
    """A page that turned out to be behind a gate, so the scene went
    somewhere else instead.

    Not a failure -- the clip is still clean, which is the whole point.
    But it must be said out loud, because a diversion means the feature
    the clip was named after never appeared in it."""
    if DRY:
        return
    DIVERTED.append(what)
    print(f"  note: {what}", file=sys.stderr)


def miss(what):
    """A beat that did not land.

    These used to be notes on stderr and nothing more, which is how two
    spotlights aimed at invented classes (.sc-card, .pf-row) recorded
    clean for weeks: the run stayed green and the clip just quietly had
    nothing highlighted. Now they are counted, and the run ends red."""
    if DRY:
        return
    MISSED.append(what)
    print(f"  note: {what}", file=sys.stderr)


class Stage:
    """One recording. Thin wrapper so a scene reads as a storyboard."""

    def __init__(self, page, base, walls=None, taps=None, dry=False, scout=None,
                 signed_in=False):
        self.page = page
        # Whether this recording has an account behind it. League pages
        # show nothing worth filming without one, and finding that out by
        # opening them is filming them.
        self.signed_in = signed_in
        self.base = base.rstrip("/")
        # What the camera-off pass learned. walls maps a page (path and
        # query) to {"wall": y or None, "top": bool, "what": str}: where
        # the first sign-in prompt starts, and whether it is on screen
        # the moment the page lands. taps maps (page, selector, index) to
        # the page that tap opened.
        self.walls = walls if walls is not None else {}
        self.taps = taps if taps is not None else {}
        self.dry = dry
        # A camera-off page kept open during the take, for the rare page
        # the dry pass never reached because a branch went another way.
        self.scout = scout
        # Set when the last visit was refused, so the has() checks that
        # follow it answer at once instead of waiting on a page that was
        # never opened.
        self.skipped = False

    def _key(self, url):
        base = self.base
        return url[len(base):] if url.startswith(base) else url

    def here(self):
        return self._key(self.page.url)

    async def _survey(self, page):
        """Measure the sign-in wall on whatever `page` is showing."""
        try:
            await page.wait_for_timeout(600)
            fact = await page.evaluate(WALL_JS)
        except Exception as e:
            fact = {"wall": None, "top": False, "what": f"unmeasured ({e})"}
        key = self._key(page.url)
        self.walls[key] = fact
        return key, fact

    async def _known(self, path):
        """The wall fact for `path`, scouting it off camera if the dry
        pass never went there."""
        if path in self.walls:
            return self.walls[path]
        if self.scout is not None:
            try:
                await self.scout.goto(self.base + path, wait_until="domcontentloaded",
                                      timeout=30000)
                key, fact = await self._survey(self.scout)
                self.walls[path] = fact
                return fact
            except Exception:
                pass
        return None

    async def visit(self, path, wait_for=None, ms=1400):
        """Open a page -- unless it opens on a sign-in prompt.

        The camera rolls from the moment the browser starts, so a page
        cannot be checked for a gate by opening it: by then it is in
        the clip. Every scene runs once first with the camera off, which
        records where each page's wall is, and this consults that record
        before navigating. A page that lands on a prompt is never opened
        on camera at all; the scene's own fallback takes over.

        Returns True when the page was opened."""
        if not self.dry:
            fact = await self._known(path)
            if fact and fact.get("top"):
                diverted(f"{path} opens on a sign-in wall ({fact.get('what')}) -- not filmed")
                self.skipped = True
                return False
        self.skipped = False
        await self.page.goto(self.base + path, wait_until="domcontentloaded")
        if wait_for:
            # A missing selector is not worth failing a whole render
            # over -- the page still records, just without that beat.
            try:
                await self.page.wait_for_selector(wait_for, timeout=12000)
            except Exception:
                miss(f"{wait_for} never appeared on {path}")
        await self.hold(ms)
        if self.dry:
            key, fact = await self._survey(self.page)
            self.walls[path] = fact
        return True

    async def hold(self, ms):
        # The camera-off pass only needs pages to settle, not to be
        # watched, so it does not sit through the pacing.
        await self.page.wait_for_timeout(min(ms, 250) if self.dry else ms)

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
        # Never scroll a wall into view. Streaks, for one, puts its
        # sign-in card partway down the list, and a pan that ends on it
        # films exactly what the clip is meant to avoid.
        fact = self.walls.get(self.here())
        if fact and fact.get("hard") is not None:
            vh = (self.page.viewport_size or {}).get("height", 720)
            ceiling = max(0, int(fact["hard"] - vh - 24))
            to_y = min(to_y, ceiling)
        await self._eval(GLIDE_JS, [to_y, ms])
        await self.hold(250)

    async def point(self, selector, index=0, settle=420):
        """Move the drawn cursor and the real mouse onto an element."""
        try:
            el = self.page.locator(selector).nth(index)
            await el.scroll_into_view_if_needed(timeout=2500)
            box = await el.bounding_box()
        except Exception:
            miss(f"could not find {selector}[{index}]")
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

    async def open_visit(self, path, wait_for=None, ms=1400):
        """Land on a page, but never film a gate.

        The sign-up and upgrade cards are the single worst thing that
        can be in an advert for the thing they are covering up: the clip
        is named after a feature and then shows a box asking you to make
        an account. So if the page is gated, the scene does not film it
        at all -- it diverts to an open page, or gives up on the beat.

        Returns True when the real page is open and worth filming."""
        return await self.visit(path, wait_for=wait_for, ms=ms)

    async def has(self, selector, timeout=2500):
        """Is this actually on the page? Asked before a card claims it is.

        A montage that narrates "playoff odds, title odds, luck" over a
        sign-in gate is worse than one that never mentions them."""
        if self.skipped:
            return False
        try:
            await self.page.wait_for_selector(selector, timeout=timeout, state="attached")
            return True
        except Exception:
            return False

    async def gated(self):
        """Whether this page is showing a sign-in or plan gate.

        A gated panel still has its buttons in the DOM, behind an
        aria-hidden blur that swallows clicks. Playwright then waits the
        full actionability timeout on every press -- the first live run
        spent forty seconds of a ninety-second clip doing exactly that,
        on a blurred panel, which is the worst footage imaginable.
        """
        if not self.dry:
            # The take hides gates (HIDE_GATE_JS), so looking would always
            # say no -- and the scene would then reach for buttons inside
            # a panel that is not there. Answer from the camera-off pass.
            fact = self.walls.get(self.here())
            return bool(fact and fact.get("wall") is not None)
        found = await self._eval(
            "() => {"
            "  for (const g of document.querySelectorAll('.gate-wrap, .gate-card, #spGate')) {"
            "    const cs = getComputedStyle(g);"
            "    const shown = g.offsetParent !== null && cs.display !== 'none'"
            "                  && cs.visibility !== 'hidden' && g.getClientRects().length;"
            "    if (shown) return g.id || g.className;"
            "  }"
            "  return '';"
            "}")
        return bool(found)

    async def tap(self, selector, index=0, after=1500, force=False):
        # A tap that opens a locked page is refused before the cursor
        # even moves, using where the same tap led on the camera-off
        # pass.
        before = self.here()
        if not self.dry:
            dest = self.taps.get((before, selector, index))
            fact = self.walls.get(dest) if dest else None
            if fact and fact.get("top"):
                diverted(f"tap on {selector} opens {dest}, which is walled -- not filmed")
                return False
        el = await self.point(selector, index)
        if el is None:
            return False
        await self._eval("() => window.__promoTap()")
        await self.hold(180)
        clicked = True
        try:
            # force: skip Playwright's wait for the element to be
            # "stable". The game page re-renders on its live poll, and
            # that wait held the tap for its full four seconds -- on
            # camera, over the kneel-down feed it was meant to leave.
            await el.click(timeout=4000, force=force)
        except Exception:
            # A click that starts a navigation can report a timeout
            # while the next document is already loading, so this is
            # not proof that nothing happened. But the streak rows have
            # failed to register outright on several takes, after which
            # the scene went on pressing buttons on a page it never
            # reached. If the target is a link and we have not moved,
            # follow the link: the cursor has already pointed and
            # tapped, so the footage reads exactly the same.
            clicked = False
            try:
                await self.page.wait_for_timeout(600)
                href = await el.evaluate(
                    "e => (e.closest('a[href]') || {}).href || ''")
            except Exception:
                href = ""
            if href and self._key(self.page.url) == before:
                try:
                    await self.page.goto(href, wait_until="domcontentloaded")
                    clicked = True
                except Exception:
                    pass
            if not clicked:
                print(f"  note: click on {selector}[{index}] did not confirm",
                      file=sys.stderr)
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        await self.hold(after)
        if self.dry and self.here() != before:
            key, _ = await self._survey(self.page)
            self.taps[(before, selector, index)] = key
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
            miss(f"nothing to spotlight at {selector}")
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
    """The signature page: who has cleared a line, and how often.

    Two things this used to do, and why it no longer does them. It tapped
    the window pills (L5, L20), which to a signed-out viewer empties the
    list -- seven seconds of filter buttons over nothing. And it tapped
    into one player, whose page currently shows the line as 0.5 and the
    hit rate as 0% in red, contradicting the 10/10 the list just showed
    (a site bug, reported separately). So it stays on the list, which is
    the part that makes the point anyway.
    """
    await s.mark(True)
    await s.clock(14000)
    await s.visit("/streaks", wait_for=".sk-item", ms=1000)
    await s.card(kicker="Streaks", big="WHO<em>keeps hitting</em>",
                 sub="Every prop, every position", ms=1700)
    await s.uncard(300)
    await s.spotlight(".sk-item", "Hit rate, not a hunch", ms=1900)
    await s.unspotlight()
    # Without the plan the board is three rows, and the rest of a phone
    # screen under them is empty. Push in so the rows are the picture.
    await s.push(1.35, ".sk-list", ms=700, hold=2600)
    await s.pull(ms=500, hold=300)
    await s.card(logo=True, url="streakpros.com", ms=2200)


# The id of the most recent finished game of the previous week, read
# from the site's own scoreboard API (or null).
FINISHED_GAME_JS = """async () => {
  try {
    const now = await (await fetch('/api/scoreboard')).json();
    for (let back = 1; back <= 2; back++) {
      const w = (now.week || 1) - back;
      if (w < 1) break;
      const r = await (await fetch('/api/scoreboard?season=' + now.season + '&week=' + w +
                                   '&seasontype=' + (now.season_type || 2))).json();
      const done = (r.games || []).filter(g => g.status === 'final')
                     .sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
      if (done.length) return done[0].id;
    }
  } catch (e) {}
  return null;
}"""


async def scene_scores(s):
    """Live game day: the board, then one game opened up."""
    await s.visit("/scores", wait_for=".wrap", ms=2000)
    await s.glide(380, 1700)
    await s.hold(900)
    await s.glide(760, 1500)
    await s.hold(900)
    await s.glide(0, 1000)
    # Open a game that has something in it: a live one, else one that has
    # finished today. On a day whose games have not kicked off yet (a
    # Thursday morning), the first card is a pregame page whose panels
    # all say "appears once the game kicks off" -- so open the latest
    # finished game from last week instead.
    if await s.has("a.sc-game-card.live", timeout=800):
        await s.tap("a.sc-game-card.live", index=0, after=1600)
    elif await s.has("a.sc-game-card.done", timeout=800):
        await s.tap("a.sc-game-card.done", index=0, after=1600)
    else:
        gid = await s.page.evaluate(FINISHED_GAME_JS)
        if gid:
            await s.visit(f"/game?id={gid}", wait_for=".gd-tab", ms=1300)
        else:
            await s.tap("a[href^='/game']", index=0, after=1600)
    # Not the feed: a finished game's feed is its kneel-downs.
    await s.tap(".gd-tab[data-panel='game']", force=True, after=1000)
    await s.glide(340, 1600)
    await s.hold(2200)


async def scene_matchups(s):
    """Every starter graded, with the sentence that explains the grade.

    Matchups is members-only, so to a guest this page is nothing but a
    gate. Rather than film the gate -- an advert for a feature, showing
    a box that hides it -- the clip falls back to Performances, which is
    the same idea (a number on a player) and open to everyone.
    """
    await s.mark(True)
    await s.clock(16000)
    if not await s.open_visit("/matchups", wait_for=".wrap", ms=1400):
        await s.visit("/performances?scope=season", ms=900)
        await s.card(kicker="Performances", big="EVERY<em>game, rated</em>",
                     sub="Against what the position normally does", ms=1700)
        await s.uncard(300)
        # The board is data-dependent: between slates it can come back
        # empty for a moment. Ringing a row that is not there would be a
        # missed beat over an empty list, so check before reaching.
        if await s.has(".pl-row-item", timeout=8000):
            await s.spotlight(".pl-row-item", ms=1700)
            await s.unspotlight()
        else:
            diverted("the performances board was empty -- no row to ring")
        await s.glide(420, 1200)
        await s.hold(1200)
        await s.card(logo=True, url="streakpros.com", ms=2200)
        return
    await s.card(kicker="Matchups", big="START<em>or sit?</em>",
                 sub="Every starter, graded", ms=1700)
    await s.uncard(300)
    await s.spotlight(".mu-grade", "The grade, and why", ms=1700)
    await s.unspotlight()
    await s.glide(680, 1400)
    await s.hold(1400)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def rankings_board(s, ms=900):
    """Open Rankings on a board that is full for a signed-out viewer.

    Signed out, the default list shows only its top tier -- four
    players -- and puts everything below behind a sign-in box. The take
    hides that box, which left four rows over an empty page. Sorting by
    trend fills the list but leads with rank-220 depth players. The card
    grid is open to everyone, in rank order, stars first, each with a
    photo and its movement badge -- the better picture on both counts.
    """
    return await s.visit("/rankings?view=grid", wait_for=".rk-card", ms=ms)


async def scene_rankings(s):
    """Dynasty values, and which way they moved this week."""
    await s.mark(True)
    await s.clock(16000)
    await rankings_board(s)
    await s.card(kicker="Dynasty rankings", big="WHAT<em>everyone is worth</em>",
                 sub="And which way it moved", ms=1700)
    await s.uncard(300)
    if await s.has(".rk-card-move"):
        await s.spotlight(".rk-card-move", "Who moved, and how far", ms=1700)
        await s.unspotlight()
    await s.glide(520, 1400)
    await s.hold(1400)
    await s.card(logo=True, url="streakpros.com", ms=2200)


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
    await s.spotlight(".sc-game-card", "Every game, every score", ms=1600)
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
    await rankings_board(s)
    await s.card(kicker="Dynasty rankings", big="WHAT<em>everyone is worth</em>", ms=1400)
    await s.uncard(300)
    if await s.has(".rk-card-move"):
        await s.spotlight(".rk-card-move", "Who moved, and how far", ms=1500)
        await s.unspotlight()
    await s.glide(360, 1000)

    # 0:22 -- the league tools, when there is a signed-in league to
    # show them with. Set PROMO_EMAIL and PROMO_PASSWORD to record this
    # half; without them the two beats below stand in, and they are
    # real features rather than filler.
    # Signed out, League Manager is an empty lookup box, and checking for
    # that by opening it put four seconds of it in the montage.
    if s.signed_in and await s.visit("/league-manager", ms=1100) and await s.has(".lg-stats"):
        await s.card(kicker="Your leagues", big="ALL<em>of them, ranked</em>",
                     sub="Best team to worst, with the maths", ms=1600)
        await s.uncard(300)
        await s.spotlight(".lg-stats", "Playoff odds. Title odds. Luck.", ms=1900)
        await s.unspotlight()
        await s.glide(520, 1100)
        await s.hold(600)

        await s.visit("/suggested-trades", wait_for=".sg-tabs", ms=1100)
        await s.card(kicker="Suggested trades", big="NAME<em>who you want</em>",
                     sub="We work out what it takes", ms=1700)
        await s.uncard(300)
        if await s.has(".sg-pk"):
            await s.spotlight(".sg-pk", "Priced for YOUR league's settings", ms=1800)
            await s.unspotlight()
        await s.glide(480, 1000)
        await s.hold(700)
    else:
        print("  note: signed out -- recording the public half of the montage",
              file=sys.stderr)
        await s.visit("/performances?scope=season", wait_for=".pl-row-item", ms=1100)
        await s.card(kicker="Every performance", big="EVERY<em>game, rated</em>",
                     sub="Every player, every week", ms=1600)
        await s.uncard(300)
        await s.glide(380, 1000)
        # .pl-row-item, not .pf-grid: the list page and the player
        # detail page do not share a prefix, and .pf-* belongs to the
        # detail page.
        await s.spotlight(".pl-row-item", "Not points. A rating.", ms=1700)
        await s.unspotlight()

        await s.visit("/standings", wait_for=".st-row", ms=1100)
        await s.card(kicker="Standings", big="WHO<em>is actually good</em>", ms=1500)
        await s.uncard(300)
        await s.glide(420, 1000)
        await s.hold(900)

    # 0:38 -- the sign-off.
    await s.card(logo=True, big="STREAK<em>PROS</em>", url="streakpros.com",
                 free="Free to use", ms=3200)




# --- one feature per clip ----------------------------------------------
#
# Each of these is about fifteen seconds and shows exactly one thing.
# Same grammar every time so a run of them cuts together: land on the
# page, name it with a card, ring the one element that makes the point,
# move once, stop. Nothing here explains -- if a beat needs a sentence
# to make sense it does not belong in a fifteen second clip.


async def scene_performances(s):
    """Every scored game, rated against its position."""
    await s.mark(True)
    await s.clock(17000)
    await s.visit("/performances?scope=season", wait_for=".pl-row-item", ms=900)
    await s.card(kicker="Performances", big="EVERY<em>game, rated</em>",
                 sub="Against what the position normally does", ms=1600)
    await s.uncard(300)
    await s.spotlight(".pl-row-item", "Not points. A rating.", ms=1700)
    await s.unspotlight()
    await s.point(".pl-filters")
    await s.glide(420, 1100)
    # Into one performance, where the rating is broken apart.
    await s.tap("a[href^='/performance']", index=0, after=2000)
    await s.glide(300, 1200)
    await s.hold(1400)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_standings(s):
    """Who is actually good, not who got lucky."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/standings", wait_for=".st-row", ms=900)
    await s.card(kicker="Standings", big="WHO<em>is actually good</em>", ms=1500)
    await s.uncard(300)
    await s.spotlight(".st-row", "Every team, ranked", ms=1600)
    await s.unspotlight()
    await s.point(".st-tabs")
    await s.glide(420, 1200)
    await s.spotlight(".st-legend", ms=1400)
    await s.unspotlight()
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_team(s):
    """One page per team: results, news, every player."""
    await s.mark(True)
    await s.clock(17000)
    await s.visit("/standings", wait_for=".st-row", ms=800)
    await s.tap("a[href^='/team']", index=0, after=1900)
    await s.card(kicker="Team pages", big="ONE<em>page per team</em>",
                 sub="Results, news, the whole roster", ms=1700)
    await s.uncard(300)
    await s.spotlight(".tm-panel", ms=1600)
    await s.unspotlight()
    await s.glide(520, 1300)
    await s.hold(1500)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_gameday(s):
    """Inside a single game: field, drives, box score."""
    await s.mark(True)
    await s.clock(18000)
    await s.visit("/scores", wait_for=".sc-day-tabs", ms=900)
    await s.tap("a[href^='/game']", index=0, after=1400)
    # The feed of a finished game is its last plays -- a column of
    # kneel-downs. The box score is the part that sells it, so a finished
    # game goes straight there; a live one shows the field first.
    # The page has loaded by now; a finished game simply has no field,
    # and waiting the default 2.5s for one filmed the kneel-downs.
    if await s.has(".gd-field", timeout=300):
        await s.card(kicker="Game detail", big="INSIDE<em>every game</em>",
                     sub="Live drives, box score, odds", ms=1700)
        await s.uncard(300)
        await s.spotlight(".gd-field", "Where the ball is", ms=1600)
        await s.unspotlight()
        await s.tap(".gd-tab[data-panel='game']", force=True, after=1300)
    else:
        await s.tap(".gd-tab[data-panel='game']", force=True, after=900)
        await s.card(kicker="Game detail", big="INSIDE<em>every game</em>",
                     sub="Live drives, box score, odds", ms=1700)
        await s.uncard(300)
    await s.glide(420, 1300)
    await s.hold(1300)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_player(s):
    """A player's whole profile, depth chart included.

    Reached through a team's roster, not the injury feed: the feed's top
    entry is whoever was designated last, which on the first take was a
    rookie DT on PUP with no rank and no value -- the emptiest profile on
    the site. A roster's first name is its starting quarterback.
    """
    await s.mark(True)
    await s.clock(16000)
    await s.visit("/team?abbr=KC", wait_for=".tm-head", ms=700)
    await s.tap(".tm-tab[data-panel='players']", after=900)
    await s.tap(".tm-player-name", index=0, after=1800)
    await s.card(kicker="Player profiles", big="EVERY<em>player, in full</em>",
                 sub="Stats, value, depth chart", ms=1700)
    await s.uncard(300)
    await s.spotlight(".player-hero", ms=1500)
    await s.unspotlight()
    await s.glide(520, 1300)
    await s.hold(1400)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_tradecalc(s):
    """Two sides, one bar, an honest answer."""
    await s.mark(True)
    await s.clock(18000)
    await s.visit("/trade-calculator", wait_for=".quick-add-grid", ms=900)
    await s.card(kicker="Trade calculator", big="IS IT<em>fair?</em>",
                 sub="Priced on real market value", ms=1700)
    await s.uncard(300)
    # A real two-sided offer: two picks going out, one coming back. Both
    # sides need something on them or the balance bar never appears --
    # which is how the first cut ringed an empty space.
    await s.tap(".quick-add-tile[onclick^='quickAddClick(1']", index=0, after=800)
    await s.tap(".quick-add-tile[onclick^='quickAddClick(1']", index=4, after=800)
    await s.tap(".quick-add-tile[onclick^='quickAddClick(2']", index=1, after=1000)
    await s.point(".balance-bar-wrap")
    await s.spotlight(".balance-bar-wrap", "Who wins it, instantly", ms=2000)
    await s.unspotlight()
    await s.hold(700)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_newsfeed(s):
    """Injuries, moves and birthdays, all in one list."""
    await s.mark(True)
    await s.clock(17000)
    await s.visit("/injuries", wait_for=".fd-row", ms=900)
    await s.card(kicker="Injury feed", big="WHO<em>is hurt</em>",
                 sub="Updated all day", ms=1600)
    await s.uncard(300)
    await s.spotlight(".fd-row", ms=1600)
    await s.unspotlight()
    await s.glide(430, 1200)
    await s.hold(900)
    await s.visit("/moves", wait_for=".fd-row", ms=700)
    await s.card(kicker="Roster moves", big="WHO<em>just signed</em>", ms=1400)
    await s.uncard(300)
    # The moves list gets its own time on screen; on the first cut it
    # was covered by its card and then the sign-off within a second.
    await s.glide(420, 1200)
    await s.hold(1000)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_sbc(s):
    """Start, bench, cut -- the vote that feeds the rankings."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/start-bench-cut", wait_for=".vote-cards", ms=1000)
    await s.card(kicker="Start / Bench / Cut", big="YOU<em>set the market</em>",
                 sub="Every vote moves the rankings", ms=1800)
    await s.uncard(300)
    await s.spotlight(".vote-cards", ms=1700)
    await s.unspotlight()
    await s.point(".vote-btns")
    await s.hold(1400)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_ratingdraft(s):
    """The day's board, when one is open."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/draft", ms=1200)
    if not await s.has(".dr-picks"):
        print("  note: no rating draft open -- showing the scores board instead",
              file=sys.stderr)
        await s.visit("/scores", wait_for=".sc-day-tabs", ms=900)
        await s.card(kicker="Rating draft", big="EVERY<em>game day</em>",
                     sub="Opens the morning after each slate", ms=1900)
        await s.uncard(300)
        await s.glide(380, 1200)
        await s.hold(1400)
        await s.card(logo=True, url="streakpros.com", ms=2200)
        return
    await s.card(kicker="Rating draft", big="PICK<em>the day's best</em>",
                 sub="A new board after every slate", ms=1800)
    await s.uncard(300)
    await s.spotlight(".dr-picks", ms=1700)
    await s.unspotlight()
    await s.glide(380, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_leaguemanager(s):
    """Your leagues, ranked, with the odds behind the ranking."""
    await s.mark(True)
    await s.clock(17000)
    if not (s.signed_in and await s.visit("/league-manager", ms=1200)
            and await s.has(".lg-stats")):
        # Signed out this page is a sign-in prompt, so the pitch goes
        # over a page that actually has data on it rather than over an
        # empty panel.
        diverted("no synced league -- putting the league pitch over the rankings")
        await rankings_board(s)
        await s.card(kicker="League manager", big="ALL<em>your leagues</em>",
                     sub="Sync every one, free", ms=2000)
        await s.uncard(300)
        await s.glide(400, 1200)
        await s.hold(1300)
        await s.card(logo=True, url="streakpros.com", free="Free to use", ms=2400)
        return
    await s.card(kicker="League manager", big="ALL<em>of them, ranked</em>",
                 sub="Best team to worst, with the maths", ms=1800)
    await s.uncard(300)
    await s.spotlight(".lg-stats", "Playoff odds. Title odds. Luck.", ms=1900)
    await s.unspotlight()
    await s.glide(520, 1300)
    await s.hold(1400)
    await s.card(logo=True, url="streakpros.com", free="Free to use", ms=2200)


async def scene_suggested(s):
    """Name who you want; it works out what it takes.

    Signed out this page is one panel saying "sign in and sync a
    league" -- not a gate element, just a plain panel, so the gate
    check does not catch it. Filming it would be twelve seconds of an
    advert for a feature, showing a box telling you to make an account.
    So the clip proves the real thing is on screen first, and otherwise
    shows the trade calculator, which is the same idea and open to
    everyone.
    """
    await s.mark(True)
    await s.clock(17000)
    await s.visit("/suggested-trades", wait_for=".sg-tabs", ms=1000)
    if not await s.has(".sg-setup") and not await s.has(".sg-row"):
        diverted("/suggested-trades needs a synced league -- showing the calculator")
        await s.visit("/trade-calculator", wait_for=".quick-add-grid", ms=900)
        await s.card(kicker="Trade calculator", big="IS IT<em>fair?</em>",
                     sub="Real market value, both sides", ms=1700)
        await s.uncard(300)
        await s.tap(".quick-add-tile", index=0, after=900)
        await s.tap(".quick-add-tile", index=1, after=900)
        await s.spotlight(".balance-bar-wrap", "The answer, instantly", ms=1800)
        await s.unspotlight()
        await s.hold(800)
        await s.card(logo=True, url="streakpros.com", ms=2200)
        return
    await s.card(kicker="Suggested trades", big="NAME<em>who you want</em>",
                 sub="We work out what it takes", ms=1800)
    await s.uncard(300)
    await s.spotlight(".sg-setup", "Your league, your settings", ms=1700)
    await s.unspotlight()
    if await s.has(".sg-row"):
        await s.spotlight(".sg-row", "Priced for YOUR league", ms=1800)
        await s.unspotlight()
    await s.glide(460, 1200)
    await s.hold(1000)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_waivers(s):
    """Who to pick up, for the league you actually play in."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/waivers", ms=1200)
    if not await s.has(".wv-row"):
        print("  note: signed out -- no waiver board, showing rankings instead",
              file=sys.stderr)
        await rankings_board(s)
        await s.card(kicker="Waiver targets", big="WHO<em>to pick up</em>",
                     sub="Sync a league to see yours", ms=2000)
        await s.uncard(300)
        await s.glide(360, 1200)
        await s.hold(1300)
        await s.card(logo=True, url="streakpros.com", ms=2200)
        return
    await s.card(kicker="Waiver targets", big="WHO<em>to pick up</em>",
                 sub="Ranked for your roster", ms=1800)
    await s.uncard(300)
    await s.spotlight(".wv-row", ms=1700)
    await s.unspotlight()
    await s.glide(400, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)



# --- variants: one slice of one feature each ------------------------------
#
# Three posts a day cannot repeat a video, so these are distinct slices of
# the same features: a position, a team, a view. Written out one by one
# rather than generated at runtime, so the page-by-page selector sweep can
# read every beat of every one.

async def scene_v_perf_qb(s):
    """The best quarterback games of the season, rated."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/performances?scope=season&position=QB", wait_for=".pl-row-item", ms=900)
    await s.card(kicker="Quarterbacks", big="BEST<em>QB games this year</em>",
                 sub="Rated against every other QB", ms=1700)
    await s.uncard(300)
    await s.spotlight(".pl-row-item", "The top QB game of the season", ms=1800)
    await s.unspotlight()
    await s.tap("a[href^='/performance']", index=0, after=1600)
    await s.glide(320, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_perf_rb(s):
    """The best running back games of the season."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/performances?scope=season&position=RB", wait_for=".pl-row-item", ms=900)
    await s.card(kicker="Running backs", big="BEST<em>RB games this year</em>",
                 sub="Rated against every other RB", ms=1700)
    await s.uncard(300)
    await s.spotlight(".pl-row-item", "Not points. A rating.", ms=1800)
    await s.unspotlight()
    await s.tap("a[href^='/performance']", index=0, after=1600)
    await s.glide(320, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_perf_wr(s):
    """The best wide receiver games of the season."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/performances?scope=season&position=WR", wait_for=".pl-row-item", ms=900)
    await s.card(kicker="Wide receivers", big="BEST<em>WR games this year</em>",
                 sub="Rated against every other WR", ms=1700)
    await s.uncard(300)
    await s.spotlight(".pl-row-item", "Not points. A rating.", ms=1800)
    await s.unspotlight()
    await s.tap("a[href^='/performance']", index=0, after=1600)
    await s.glide(320, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_perf_te(s):
    """The best tight end games of the season."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/performances?scope=season&position=TE", wait_for=".pl-row-item", ms=900)
    await s.card(kicker="Tight ends", big="BEST<em>TE games this year</em>",
                 sub="Rated against every other TE", ms=1700)
    await s.uncard(300)
    await s.spotlight(".pl-row-item", "Not points. A rating.", ms=1800)
    await s.unspotlight()
    await s.tap("a[href^='/performance']", index=0, after=1600)
    await s.glide(320, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_perf_lb(s):
    """The best linebacker games -- IDP gets rated too."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/performances?scope=season&position=LB", wait_for=".pl-row-item", ms=900)
    await s.card(kicker="Defense too", big="BEST<em>defensive games</em>",
                 sub="IDP players rated the same way", ms=1700)
    await s.uncard(300)
    await s.spotlight(".pl-row-item", "Linebackers, rated", ms=1800)
    await s.unspotlight()
    await s.tap("a[href^='/performance']", index=0, after=1600)
    await s.glide(320, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_perf_worst(s):
    """The lowest-rated games of the season. Engagement bait, honestly labelled."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/performances?scope=season&order=lowest", wait_for=".pl-row-item", ms=900)
    await s.card(kicker="Performances", big="WORST<em>games this year</em>",
                 sub="Every dud, rated", ms=1700)
    await s.uncard(300)
    await s.spotlight(".pl-row-item", "Rock bottom", ms=1800)
    await s.unspotlight()
    await s.glide(520, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_rank_qb(s):
    """Dynasty quarterback rankings with weekly movement."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/rankings?pos=QB", wait_for=".rk-page", ms=900)
    await s.card(kicker="Dynasty rankings", big="EVERY<em>QB, ranked</em>",
                 sub="With this week's movement", ms=1700)
    await s.uncard(300)
    await s.spotlight(".rk-move", "Who moved, and how far", ms=1800)
    await s.unspotlight()
    await s.glide(360, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_rank_rb(s):
    """Dynasty running back rankings with weekly movement."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/rankings?pos=RB", wait_for=".rk-page", ms=900)
    await s.card(kicker="Dynasty rankings", big="EVERY<em>RB, ranked</em>",
                 sub="With this week's movement", ms=1700)
    await s.uncard(300)
    await s.spotlight(".rk-move", "Who moved, and how far", ms=1800)
    await s.unspotlight()
    await s.glide(360, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_rank_wr(s):
    """Dynasty wide receiver rankings with weekly movement."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/rankings?pos=WR", wait_for=".rk-page", ms=900)
    await s.card(kicker="Dynasty rankings", big="EVERY<em>WR, ranked</em>",
                 sub="With this week's movement", ms=1700)
    await s.uncard(300)
    await s.spotlight(".rk-move", "Who moved, and how far", ms=1800)
    await s.unspotlight()
    await s.glide(360, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_rank_te(s):
    """Dynasty tight end rankings with weekly movement."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/rankings?pos=TE", wait_for=".rk-page", ms=900)
    await s.card(kicker="Dynasty rankings", big="EVERY<em>TE, ranked</em>",
                 sub="With this week's movement", ms=1700)
    await s.uncard(300)
    await s.spotlight(".rk-move", "Who moved, and how far", ms=1800)
    await s.unspotlight()
    await s.glide(360, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_team_kc(s):
    """Chiefs team page: results, then the roster with values."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/team?abbr=KC", wait_for=".tm-head", ms=900)
    await s.card(kicker="Kansas City", big="THE CHIEFS<em>on one page</em>",
                 sub="Results, roster, dynasty values", ms=1700)
    await s.uncard(300)
    await s.spotlight(".tm-panel", ms=1800)
    await s.unspotlight()
    await s.tap(".tm-tab[data-panel='players']", index=0, after=1600)
    await s.glide(480, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_team_phi(s):
    """Eagles team page."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/team?abbr=PHI", wait_for=".tm-head", ms=900)
    await s.card(kicker="Philadelphia", big="THE EAGLES<em>on one page</em>",
                 sub="Results, roster, dynasty values", ms=1700)
    await s.uncard(300)
    await s.spotlight(".tm-panel", ms=1800)
    await s.unspotlight()
    await s.tap(".tm-tab[data-panel='players']", index=0, after=1600)
    await s.glide(480, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_team_det(s):
    """Lions team page."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/team?abbr=DET", wait_for=".tm-head", ms=900)
    await s.card(kicker="Detroit", big="THE LIONS<em>on one page</em>",
                 sub="Results, roster, dynasty values", ms=1700)
    await s.uncard(300)
    await s.spotlight(".tm-panel", ms=1800)
    await s.unspotlight()
    await s.tap(".tm-tab[data-panel='players']", index=0, after=1600)
    await s.glide(480, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_team_buf(s):
    """Bills team page."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/team?abbr=BUF", wait_for=".tm-head", ms=900)
    await s.card(kicker="Buffalo", big="THE BILLS<em>on one page</em>",
                 sub="Results, roster, dynasty values", ms=1700)
    await s.uncard(300)
    await s.spotlight(".tm-panel", ms=1800)
    await s.unspotlight()
    await s.tap(".tm-tab[data-panel='players']", index=0, after=1600)
    await s.glide(480, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_team_dal(s):
    """Cowboys team page."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/team?abbr=DAL", wait_for=".tm-head", ms=900)
    await s.card(kicker="Dallas", big="THE COWBOYS<em>on one page</em>",
                 sub="Results, roster, dynasty values", ms=1700)
    await s.uncard(300)
    await s.spotlight(".tm-panel", ms=1800)
    await s.unspotlight()
    await s.tap(".tm-tab[data-panel='players']", index=0, after=1600)
    await s.glide(480, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_st_power(s):
    """Power rankings computed from results."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/standings?view=rankings", wait_for=".st-row", ms=900)
    await s.card(kicker="Power rankings", big="RANKED<em>by results</em>",
                 sub="Not by opinions", ms=1700)
    await s.uncard(300)
    await s.spotlight(".st-row", "Every team, one list", ms=1800)
    await s.unspotlight()
    await s.glide(420, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_st_playoffs(s):
    """The playoff picture as it stands."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/standings?view=playoffs", wait_for=".st-row", ms=900)
    await s.card(kicker="Playoff picture", big="WHO'S IN<em>right now</em>",
                 sub="Seeds, byes and the bubble", ms=1700)
    await s.uncard(300)
    await s.spotlight(".st-row", "The one seed", ms=1800)
    await s.unspotlight()
    await s.glide(420, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_st_draft(s):
    """The live draft order."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/standings?view=draft", wait_for=".st-row", ms=900)
    await s.card(kicker="Draft order", big="WHO PICKS<em>first?</em>",
                 sub="The draft order, live", ms=1700)
    await s.uncard(300)
    await s.spotlight(".st-row", "On the clock", ms=1800)
    await s.unspotlight()
    await s.glide(420, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_st_nfc(s):
    """NFC standings."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/standings?view=nfc", wait_for=".st-row", ms=900)
    await s.card(kicker="NFC standings", big="THE NFC<em>right now</em>",
                 sub="Every division, every record", ms=1700)
    await s.uncard(300)
    await s.spotlight(".st-row", "Division leader", ms=1800)
    await s.unspotlight()
    await s.glide(420, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_trade_sf(s):
    """The calculator in superflex, where quarterbacks carry more value."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/trade-calculator?format=superflex", wait_for=".quick-add-grid", ms=900)
    await s.card(kicker="Superflex", big="SUPERFLEX<em>values, built in</em>",
                 sub="Priced for 2-QB leagues", ms=1700)
    await s.uncard(300)
    await s.tap(".quick-add-tile[onclick^='quickAddClick(1']", index=0, after=900)
    await s.tap(".quick-add-tile[onclick^='quickAddClick(2']", index=2, after=900)
    await s.point(".balance-bar-wrap")
    await s.spotlight(".balance-bar-wrap", "Who wins it", ms=1800)
    await s.unspotlight()
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_trade_redraft(s):
    """The calculator in redraft mode."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/trade-calculator?mode=redraft", wait_for=".quick-add-grid", ms=900)
    await s.card(kicker="Redraft", big="NOT<em>a dynasty league?</em>",
                 sub="Redraft values too", ms=1700)
    await s.uncard(300)
    await s.tap(".quick-add-tile[onclick^='quickAddClick(1']", index=0, after=900)
    await s.tap(".quick-add-tile[onclick^='quickAddClick(2']", index=1, after=900)
    await s.point(".balance-bar-wrap")
    await s.spotlight(".balance-bar-wrap", "Who wins it", ms=1800)
    await s.unspotlight()
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_feed_moves(s):
    """Every signing, release and trade."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/moves", wait_for=".fd-row", ms=900)
    await s.card(kicker="Roster moves", big="WHO<em>just signed</em>",
                 sub="Every transaction, as it happens", ms=1700)
    await s.uncard(300)
    await s.spotlight(".fd-row", ms=1800)
    await s.unspotlight()
    await s.glide(440, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_feed_bdays(s):
    """Player birthdays -- the lighter one."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/birthdays", wait_for=".fd-row", ms=900)
    await s.card(kicker="Birthdays", big="WHO'S<em>a year older</em>",
                 sub="Yes, we track that too", ms=1700)
    await s.uncard(300)
    await s.spotlight(".fd-row", ms=1800)
    await s.unspotlight()
    await s.glide(440, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_player_cin(s):
    """A Bengals starter's full profile."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/team?abbr=CIN", wait_for=".tm-head", ms=900)
    await s.tap(".tm-tab[data-panel='players']", index=0, after=1500)
    await s.tap(".tm-player-name", index=0, after=1500)
    await s.card(kicker="Player profiles", big="EVERY<em>player, in full</em>",
                 sub="Stats, value, depth chart", ms=1700)
    await s.uncard(300)
    await s.spotlight(".player-hero", ms=1500)
    await s.unspotlight()
    await s.glide(520, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_player_bal(s):
    """A Ravens starter's full profile."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/team?abbr=BAL", wait_for=".tm-head", ms=900)
    await s.tap(".tm-tab[data-panel='players']", index=0, after=1500)
    await s.tap(".tm-player-name", index=0, after=1500)
    await s.card(kicker="Player profiles", big="EVERY<em>player, in full</em>",
                 sub="Stats, value, depth chart", ms=1700)
    await s.uncard(300)
    await s.spotlight(".player-hero", ms=1500)
    await s.unspotlight()
    await s.glide(520, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_player_sf(s):
    """A 49ers starter's full profile."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/team?abbr=SF", wait_for=".tm-head", ms=900)
    await s.tap(".tm-tab[data-panel='players']", index=0, after=1500)
    await s.tap(".tm-player-name", index=0, after=1500)
    await s.card(kicker="Player profiles", big="EVERY<em>player, in full</em>",
                 sub="Stats, value, depth chart", ms=1700)
    await s.uncard(300)
    await s.spotlight(".player-hero", ms=1500)
    await s.unspotlight()
    await s.glide(520, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)

async def scene_v_team_min(s):
    """Vikings team page. Replaces the redraft calculator, whose mode has
    no quick-add picks to build a trade from."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/team?abbr=MIN", wait_for=".tm-head", ms=900)
    await s.card(kicker="Minnesota", big="THE VIKINGS<em>on one page</em>",
                 sub="Results, roster, dynasty values", ms=1700)
    await s.uncard(300)
    await s.spotlight(".tm-panel", ms=1800)
    await s.unspotlight()
    await s.tap(".tm-tab[data-panel='players']", index=0, after=1600)
    await s.glide(480, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_v_team_gb(s):
    """Packers team page. Replaces the worst-games board, which is a list
    of backups rated 0.0 -- accurate, and not worth a post."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/team?abbr=GB", wait_for=".tm-head", ms=900)
    await s.card(kicker="Green Bay", big="THE PACKERS<em>on one page</em>",
                 sub="Results, roster, dynasty values", ms=1700)
    await s.uncard(300)
    await s.spotlight(".tm-panel", ms=1800)
    await s.unspotlight()
    await s.tap(".tm-tab[data-panel='players']", index=0, after=1600)
    await s.glide(480, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_v_st_nfc_playoffs(s):
    """NFC playoff picture. Replaces birthdays, which is empty on any day
    nobody has one."""
    await s.mark(True)
    await s.clock(15000)
    await s.visit("/standings?view=playoffs&conf=NFC", wait_for=".st-row", ms=900)
    await s.card(kicker="NFC playoff picture", big="WHO'S IN<em>in the NFC</em>",
                 sub="Seeds, byes and the bubble", ms=1700)
    await s.uncard(300)
    await s.spotlight(".st-row", "The one seed", ms=1800)
    await s.unspotlight()
    await s.glide(420, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)


# One feature per clip, for the pages the other scenes only pass through
# (or not at all). Each is reached the way a visitor reaches it -- from
# the board it belongs to -- so the clip never opens on a hand-picked id
# that could be stale by the time it records.

async def open_one_performance(s):
    """From the season board into its top performance."""
    await s.visit("/performances?scope=season", wait_for=".pl-row-item", ms=700)
    await s.tap("a[href^='/performance?']", index=0, after=1900)


async def scene_f_perf_detail(s):
    """One game, taken apart: the score, what made it, every play."""
    await s.mark(True)
    await s.clock(18000)
    await open_one_performance(s)
    await s.card(kicker="Performance breakdown", big="ONE GAME<em>taken apart</em>",
                 sub="The score, and exactly what made it", ms=1700)
    await s.uncard(300)
    if await s.has(".pf-scores", timeout=800):
        await s.spotlight(".pf-scores", "Rated against the position", ms=1600)
        await s.unspotlight()
    if await s.has("#pfQtrs", timeout=800):
        await s.point("#pfQtrs")
        await s.spotlight("#pfQtrs", "Quarter by quarter", ms=1700)
        await s.unspotlight()
    if await s.has("#pfFeed", timeout=800):
        await s.point("#pfFeed")
        await s.spotlight(".pf-feed-row", "Every play he was in", ms=1600)
        await s.unspotlight()
    await s.hold(900)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_f_play(s):
    """Any play, on its own page: the situation, the field, the swing."""
    await s.mark(True)
    await s.clock(17000)
    await open_one_performance(s)
    await s.point("#pfFeed")
    await s.card(kicker="Play by play", big="EVERY PLAY<em>on its own page</em>",
                 sub="Down, distance, field and the swing", ms=1700)
    await s.uncard(300)
    await s.tap(".pf-feed-row[data-href]", index=0, after=1900, force=True)
    if await s.has(".pd-field", timeout=1500):
        await s.spotlight(".pd-field", "Where it happened", ms=1700)
        await s.unspotlight()
    await s.glide(360, 1200)
    await s.hold(1200)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_f_kdst(s):
    """Kickers and defences, scored and ranked like everyone else."""
    await s.mark(True)
    await s.clock(16000)
    await s.visit("/rankings?pos=K", wait_for=".rk-page", ms=900)
    await s.card(kicker="Kickers & D/ST", big="EVERY KICKER<em>and defense</em>",
                 sub="Scored and ranked, week by week", ms=1700)
    await s.uncard(300)
    await s.glide(300, 1100)
    await s.hold(900)
    await s.visit("/rankings?pos=DEF", wait_for=".rk-page", ms=900)
    await s.glide(300, 1100)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)


async def scene_f_streak_player(s):
    """One player, every prop, every game against the line."""
    await s.mark(True)
    await s.clock(17000)
    await s.visit("/streaks", wait_for=".sk-item", ms=700)
    await s.tap("a.sk-item", index=0, after=1900)
    await s.card(kicker="Player streaks", big="ONE PLAYER<em>every prop</em>",
                 sub="Every game, against the line", ms=1700)
    await s.uncard(300)
    if await s.has("#spPlot", timeout=1200):
        await s.spotlight("#spPlot", "Every game, hit or miss", ms=1800)
        await s.unspotlight()
    if await s.has(".sp-facts", timeout=600):
        await s.spotlight(".sp-facts", ms=1400)
        await s.unspotlight()
    await s.glide(380, 1200)
    await s.hold(1100)
    await s.card(logo=True, url="streakpros.com", ms=2200)


# Members-only pages. Signed out, their scenes go around the gate to a
# public page -- fine for a demo, wrong for a clip named after the
# feature -- so for these a diversion fails the recording instead.
MEMBERS_ONLY = {"matchups", "waivers", "suggested"}

SCENES = {
    "f_perf_detail": scene_f_perf_detail,
    "f_play": scene_f_play,
    "f_kdst": scene_f_kdst,
    "f_streak_player": scene_f_streak_player,
    "montage": scene_montage,
    "v_team_min": scene_v_team_min,
    "v_team_gb": scene_v_team_gb,
    "v_st_nfc_playoffs": scene_v_st_nfc_playoffs,
    "v_perf_qb": scene_v_perf_qb,
    "v_perf_rb": scene_v_perf_rb,
    "v_perf_wr": scene_v_perf_wr,
    "v_perf_te": scene_v_perf_te,
    "v_perf_lb": scene_v_perf_lb,
    "v_perf_worst": scene_v_perf_worst,
    "v_rank_qb": scene_v_rank_qb,
    "v_rank_rb": scene_v_rank_rb,
    "v_rank_wr": scene_v_rank_wr,
    "v_rank_te": scene_v_rank_te,
    "v_team_kc": scene_v_team_kc,
    "v_team_phi": scene_v_team_phi,
    "v_team_det": scene_v_team_det,
    "v_team_buf": scene_v_team_buf,
    "v_team_dal": scene_v_team_dal,
    "v_st_power": scene_v_st_power,
    "v_st_playoffs": scene_v_st_playoffs,
    "v_st_draft": scene_v_st_draft,
    "v_st_nfc": scene_v_st_nfc,
    "v_trade_sf": scene_v_trade_sf,
    "v_trade_redraft": scene_v_trade_redraft,
    "v_feed_moves": scene_v_feed_moves,
    "v_feed_bdays": scene_v_feed_bdays,
    "v_player_cin": scene_v_player_cin,
    "v_player_bal": scene_v_player_bal,
    "v_player_sf": scene_v_player_sf,
    "performances": scene_performances,
    "standings": scene_standings,
    "team": scene_team,
    "gameday": scene_gameday,
    "player": scene_player,
    "tradecalc": scene_tradecalc,
    "newsfeed": scene_newsfeed,
    "sbc": scene_sbc,
    "ratingdraft": scene_ratingdraft,
    "leaguemanager": scene_leaguemanager,
    "suggested": scene_suggested,
    "waivers": scene_waivers,
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
        return
    # Signed in, the account's saved theme beats the browser's, so save
    # this clip's look to the (dedicated promo) account too.
    try:
        await stage.page.evaluate(
            """(look) => fetch('/api/theme', {method: 'POST', credentials: 'same-origin',
                 headers: {'Content-Type': 'application/json'}, body: JSON.stringify(look)})""",
            LOOK)
    except Exception as e:
        print(f"  note: could not save the theme to the account ({e})", file=sys.stderr)


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
        # The page is laid out at `size` CSS px and drawn at FILM device
        # px: 390px wide drawn 1080 wide is a device scale of ~2.77, set
        # on the browser itself as well as the context -- on the context
        # alone the capture comes out at CSS size, padded with gray.
        dpr = FILM[shape][0] / size["width"]
        if dpr != 1:
            launch["args"].append(f"--force-device-scale-factor={dpr:.5f}")
        chrome = os.environ.get("PROMO_CHROME")
        if chrome:
            launch["executable_path"] = chrome
        browser = await pw.chromium.launch(**launch)

        # --- the camera-off pass -----------------------------------------
        # Run the whole scene once with no recording, measuring where every
        # page it opens puts its first sign-in prompt and where every tap
        # leads. The take then knows, before it opens anything, which
        # pages would put a wall on screen -- it cannot find out by
        # looking, because looking is filming. This also warms every page
        # the take will open, so a cold instance is never the opening shot.
        global DRY
        walls, taps = {}, {}
        scout_ctx = await browser.new_context(viewport=size, device_scale_factor=1)
        await scout_ctx.add_init_script(theme_js(LOOK["theme"], LOOK["accent"]))
        await scout_ctx.add_init_script(HIDE_CTA_JS)
        await scout_ctx.add_init_script(CURSOR_JS)
        await scout_ctx.add_init_script(OVERLAY_JS)
        dry_page = await scout_ctx.new_page()
        dry = Film(dry_page, base, walls=walls, taps=taps, dry=True,
                   signed_in=bool(email and password))
        if email and password:
            await sign_in(dry, email, password)
        DRY = True
        try:
            await SCENES[scene](dry)
        except Exception as e:
            print(f"  note: camera-off pass stopped early ({e})", file=sys.stderr)
        finally:
            DRY = False
        walled = sorted(k for k, v in walls.items() if v.get("top"))
        if walled:
            print(f"  walled on landing: {', '.join(walled)}", file=sys.stderr)

        # --- the take ----------------------------------------------------
        # Signed in on the camera-off pass, cookies carried across, so
        # the login form is never in the take.
        # Drawn at full device resolution (see dpr above), so every glyph
        # is rendered sharp rather than drawn small and stretched later.
        ctx = await browser.new_context(
            storage_state=await scout_ctx.storage_state(),
            viewport=size,
            device_scale_factor=dpr,
        )
        await ctx.add_init_script(theme_js(LOOK["theme"], LOOK["accent"]))
        await ctx.add_init_script(HIDE_CTA_JS)
        await ctx.add_init_script(HIDE_GATE_JS)
        await ctx.add_init_script(CURSOR_JS)
        await ctx.add_init_script(OVERLAY_JS)
        page = await ctx.new_page()
        camera = Camera(ctx, page, FILM[shape], os.path.join(out_dir, f".frames-{scene}-{shape}"))
        await camera.start()
        # Recording starts on about:blank, which is white. A scene that
        # opens on a title card drew it over that -- the first frame of
        # the montage was a white screen. Start on the page colour of the
        # theme being filmed, so nothing flashes on the way in.
        paper = PAPER[LOOK["theme"]].replace("#", "%23")
        await page.goto(f"data:text/html,<html style='background:{paper}'>"
                        f"<body style='margin:0;background:{paper}'></body></html>")
        stage = Film(page, base, walls=walls, taps=taps, scout=dry_page,
                     signed_in=bool(email and password))

        try:
            await SCENES[scene](stage)
        except Exception as e:
            # Keep whatever was recorded up to the failure. A short clip
            # is still a clip; a crashed job is nothing.
            print(f"  scene stopped early: {e}", file=sys.stderr)

        await camera.stop()
        await scout_ctx.close()
        await ctx.close()
        await browser.close()

    final = camera.write(os.path.join(out_dir, f"{scene}-{shape}.mp4"))
    if not final:
        raise SystemExit("no video was recorded")
    print(final)
    return final


# Every team, and a starter from every team, as its own clip -- the
# weekly promo run rotates through these so three posts a day do not
# repeat a video for weeks. Same beats as the hand-written team and
# player clips above, one factory each instead of 64 copies.
TEAMS = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}


def team_scene(abbr):
    city, nick = TEAMS[abbr].rsplit(" ", 1)

    async def scene(s):
        await s.mark(True)
        await s.clock(15000)
        await s.visit(f"/team?abbr={abbr}", wait_for=".tm-head", ms=900)
        await s.card(kicker=city, big=f"THE {nick.upper()}<em>on one page</em>",
                     sub="Results, roster, dynasty values", ms=1700)
        await s.uncard(300)
        await s.spotlight(".tm-panel", ms=1800)
        await s.unspotlight()
        await s.tap(".tm-tab[data-panel='players']", index=0, after=1600)
        await s.glide(480, 1200)
        await s.hold(1100)
        await s.card(logo=True, url="streakpros.com", ms=2200)
    scene.__doc__ = f"{TEAMS[abbr]} team page."
    return scene


def player_scene(abbr):
    async def scene(s):
        await s.mark(True)
        await s.clock(15000)
        await s.visit(f"/team?abbr={abbr}", wait_for=".tm-head", ms=900)
        await s.tap(".tm-tab[data-panel='players']", index=0, after=1500)
        await s.tap(".tm-player-name", index=0, after=1500)
        await s.card(kicker="Player profiles", big="EVERY<em>player, in full</em>",
                     sub="Stats, value, depth chart", ms=1700)
        await s.uncard(300)
        await s.spotlight(".player-hero", ms=1500)
        await s.unspotlight()
        await s.glide(520, 1200)
        await s.hold(1100)
        await s.card(logo=True, url="streakpros.com", ms=2200)
    scene.__doc__ = f"A {TEAMS[abbr]} starter's full profile."
    return scene


for _abbr in TEAMS:
    SCENES[f"t_{_abbr.lower()}"] = team_scene(_abbr)
    SCENES[f"p_{_abbr.lower()}"] = player_scene(_abbr)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=os.environ.get("SITE_HOST", "https://streakpros.com"))
    ap.add_argument("--scene", default="streaks", choices=sorted(SCENES))
    ap.add_argument("--shape", default="wide", choices=sorted(SHAPES))
    ap.add_argument("--out", default="promo")
    ap.add_argument("--theme", default=os.environ.get("PROMO_THEME") or LOOK["theme"], choices=THEMES)
    ap.add_argument("--accent", default=os.environ.get("PROMO_ACCENT") or LOOK["accent"], choices=ACCENTS)
    a = ap.parse_args()
    LOOK.update(theme=a.theme, accent=a.accent)
    asyncio.run(record(a.base, a.scene, a.shape, a.out,
                       os.environ.get("PROMO_EMAIL"),
                       os.environ.get("PROMO_PASSWORD")))
    # The clip is on disk by now either way -- a missed beat is worth
    # seeing, not worth throwing the footage away for. Exit 2 says
    # "recorded, but look at it"; a crash is still 1.
    if DIVERTED:
        print(f"\n{len(DIVERTED)} beat(s) went around a gate:", file=sys.stderr)
        for d in DIVERTED:
            print(f"  - {d}", file=sys.stderr)
        print("  the clip is clean, but it does not show that feature.",
              file=sys.stderr)
        if a.scene in MEMBERS_ONLY:
            MISSED.append(f"{a.scene} is members-only and was not filmed signed in")
    if MISSED:
        print(f"\n{len(MISSED)} beat(s) did not land:", file=sys.stderr)
        for m in MISSED:
            print(f"  - {m}", file=sys.stderr)
        raise SystemExit(2)
    print("every beat landed" + (" (no gate was filmed)" if DIVERTED else ""))


if __name__ == "__main__":
    main()
