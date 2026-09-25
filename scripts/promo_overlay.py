"""The overlay layer that turns a screen recording into an edited clip.

Everything here is injected into the live page and drawn by the same
browser that is being recorded, so the motion is real CSS animation
rather than something stitched on afterwards. That buys three things:
the type renders in the site's own fonts, the timing is frame-accurate
because there is no second pass, and one Playwright run produces a
finished video.

The stage is appended to <html>, not <body>, on purpose, so nothing in
the site's own DOM moves. Pushing in on a row is done by the camera
(promo_capture.Camera), on the recorded picture, not by zooming the page.

Layout obeys TikTok's furniture: the app's own buttons sit over the
right edge and its caption sits over the bottom, so nothing that has to
be read goes in the right 12% or the bottom 18%. The lower third is
left deliberately empty -- that is where the text-to-speech sticker
goes when the clip is uploaded.
"""

# Brand values, hard-coded rather than read from the page's theme
# tokens: a reader who has picked the green accent should still get a
# clip in the site's own colours.
NAVY = "#10233F"
FLAME = "#F2542D"
AMBER = "#FFC53D"
CREAM = "#e8e6df"
MUTED = "#9aa0a6"

OVERLAY_JS = """
(() => {
  const NAVY = "%(navy)s", FLAME = "%(flame)s", AMBER = "%(amber)s",
        CREAM = "%(cream)s", MUTED = "%(muted)s";

  function build() {
    if (document.getElementById('__promo_stage')) return;

    const css = document.createElement('style');
    css.textContent = `
      #__promo_stage{ position:fixed; inset:0; z-index:2147483646;
                      pointer-events:none; font-synthesis:none; }
      #__promo_stage *{ box-sizing:border-box; }

      /* A full-bleed card: the hook at the top of the clip and the
         sign-off at the end. Padded clear of the bottom furniture. */
      .__pc{ position:absolute; inset:0; background:${NAVY};
             display:flex; flex-direction:column; align-items:center;
             justify-content:center; gap:calc(14px*var(--pk,1)); padding:0 calc(34px*var(--pk,1)) 16%% calc(34px*var(--pk,1));
             opacity:0; transition:opacity 340ms ease; text-align:center; }
      .__pc.on{ opacity:1; }
      .__pc-kick{ font-family:"Source Sans 3",system-ui,sans-serif;
                  font-weight:700; font-size:calc(14px*var(--pk,1)); letter-spacing:.20em;
                  text-transform:uppercase; color:${FLAME}; }
      .__pc-big{ font-family:"Big Shoulders Display",Impact,sans-serif;
                 font-weight:800; font-size:calc(104px*var(--pk,1)); line-height:.88;
                 color:${CREAM}; letter-spacing:-.01em; }
      .__pc-big em{ font-style:normal; color:${AMBER}; display:block;
                    font-size:calc(56px*var(--pk,1)); line-height:1.05; }
      .__pc-sub{ font-family:"Source Sans 3",system-ui,sans-serif;
                 font-weight:600; font-size:calc(21px*var(--pk,1)); line-height:1.3;
                 color:${MUTED}; max-width:15em; }
      .__pc-url{ font-family:"Big Shoulders Display",Impact,sans-serif;
                 font-weight:800; font-size:calc(46px*var(--pk,1)); color:${CREAM};
                 letter-spacing:.01em; }
      .__pc-free{ font-family:"Source Sans 3",system-ui,sans-serif;
                  font-weight:700; font-size:calc(15px*var(--pk,1)); letter-spacing:.16em;
                  text-transform:uppercase; color:${FLAME}; }
      .__pc-logo{ width:calc(104px*var(--pk,1)); height:calc(104px*var(--pk,1)); border-radius:calc(24px*var(--pk,1)); }

      /* The spotlight: a ring around the thing being talked about, with
         everything else dimmed by an enormous spread shadow. One
         element does both jobs. */
      .__pring{ position:absolute; border:3px solid ${FLAME};
                border-radius:14px; opacity:0;
                box-shadow:0 0 0 9999px rgba(6,9,14,.62);
                transition:opacity 300ms ease, top 420ms cubic-bezier(.4,0,.2,1),
                           left 420ms cubic-bezier(.4,0,.2,1),
                           width 420ms cubic-bezier(.4,0,.2,1),
                           height 420ms cubic-bezier(.4,0,.2,1); }
      .__pring.on{ opacity:1; }

      /* A caption tag pinned beside the ring. */
      .__ptag{ position:absolute; background:${FLAME}; color:#fff;
               font-family:"Source Sans 3",system-ui,sans-serif;
               font-weight:800; font-size:15px; letter-spacing:.01em;
               padding:7px 12px; border-radius:9px; white-space:nowrap;
               opacity:0; transform:translateY(7px);
               transition:opacity 260ms ease, transform 260ms ease;
               box-shadow:0 6px 18px rgba(0,0,0,.45); }
      .__ptag.on{ opacity:1; transform:translateY(0); }

      /* Corner mark, so a re-posted clip still says whose it is. */
      .__pmark{ position:absolute; top:14px; left:14px; display:flex;
                align-items:center; gap:8px; opacity:0;
                transition:opacity 300ms ease; }
      .__pmark.on{ opacity:1; }
      .__pmark img{ width:26px; height:26px; border-radius:7px; }
      .__pmark span{ font-family:"Big Shoulders Display",Impact,sans-serif;
                     font-weight:800; font-size:20px; color:${CREAM};
                     text-shadow:0 2px 8px rgba(0,0,0,.7); }
      .__pmark span b{ color:${FLAME}; font-weight:800; }
      /* Filmed in the light theme, cream on a white header disappears. */
      html[data-theme="light"] .__pmark span{ color:#16181a; text-shadow:none; }

      /* A sliver of progress along the top edge. Cheap, and it tells a
         scroller the clip is nearly over, which keeps them to the end. */
      .__pprog{ position:absolute; top:0; left:0; height:3px; width:0;
                background:${FLAME}; }
    `;
    document.documentElement.appendChild(css);

    const stage = document.createElement('div');
    stage.id = '__promo_stage';
    // The title cards are sized for a 540px-square area. Filmed at a
    // phone's 390px they scale down to match, so a headline that fitted
    // still fits; on a 1280x720 laptop page they scale up with the
    // height, so a card fills the screen instead of sitting small in the
    // middle of it. Everything else (tags, cursor) stays at CSS size.
    stage.style.setProperty('--pk',
      Math.min(window.innerWidth / 540, window.innerHeight / 540));
    stage.innerHTML = `
      <div class="__pprog" id="__pprog"></div>
      <div class="__pring" id="__pring"></div>
      <div class="__ptag" id="__ptag"></div>
      <div class="__pmark" id="__pmark">
        <img src="/icon-192.png" alt="" onerror="this.style.display='none'">
        <span>Streak<b>Pros</b></span>
      </div>
      <div class="__pc" id="__pc"></div>`;
    document.documentElement.appendChild(stage);
  }

  function el(id) { build(); return document.getElementById(id); }

  window.__promo = {
    /* A full-bleed card. `big` may carry a nested <em> for a second,
       smaller line -- the caller escapes its own text. */
    card(o) {
      const c = el('__pc');
      c.innerHTML =
        (o.logo ? '<img class="__pc-logo" src="/icon-192.png" alt="" ' +
                  'onerror="this.style.display=\\'none\\'">' : '') +
        (o.kicker ? `<div class="__pc-kick">${o.kicker}</div>` : '') +
        (o.big ? `<div class="__pc-big">${o.big}</div>` : '') +
        (o.sub ? `<div class="__pc-sub">${o.sub}</div>` : '') +
        (o.url ? `<div class="__pc-url">${o.url}</div>` : '') +
        (o.free ? `<div class="__pc-free">${o.free}</div>` : '');
      c.classList.add('on');
    },
    hideCard() { el('__pc').classList.remove('on'); },

    /* Ring an element, by selector, with an optional tag beside it.
       Coordinates come from the live box so the ring tracks whatever
       the page actually laid out. */
    ring(sel, tag, pad) {
      const t = document.querySelector(sel);
      const r = el('__pring'), g = el('__ptag');
      if (!t) { r.classList.remove('on'); g.classList.remove('on'); return false; }
      const b = t.getBoundingClientRect();
      pad = pad == null ? 6 : pad;
      r.style.top = (b.top - pad) + 'px';
      r.style.left = (b.left - pad) + 'px';
      r.style.width = (b.width + pad * 2) + 'px';
      r.style.height = (b.height + pad * 2) + 'px';
      r.classList.add('on');
      if (tag) {
        g.textContent = tag;
        // Below the ring normally, above it when that would fall into
        // the bottom furniture.
        const below = b.bottom + pad + 10;
        const tall = window.innerHeight * 0.80;
        g.style.top = (below > tall ? b.top - pad - 34 : below) + 'px';
        g.style.left = Math.max(14, Math.min(b.left, window.innerWidth - 220)) + 'px';
        g.classList.add('on');
      } else {
        g.classList.remove('on');
      }
      return true;
    },
    hideRing() { el('__pring').classList.remove('on'); el('__ptag').classList.remove('on'); },

    mark(on) { el('__pmark').classList.toggle('on', !!on); },

    /* Drive the progress sliver over the clip's whole length. */
    progress(ms) {
      const p = el('__pprog');
      p.style.transition = `width ${ms}ms linear`;
      requestAnimationFrame(() => { p.style.width = '100%%'; });
    },

    /* Push in on a point of the page. body, not <html>, so the stage
       above stays at its own scale. */
    zoom(scale, sel, ms) {
      const b = document.body;
      b.style.transition = `zoom ${ms || 700}ms cubic-bezier(.4,0,.2,1)`;
      if (sel) {
        const t = document.querySelector(sel);
        if (t) {
          const r = t.getBoundingClientRect();
          const want = r.top + r.height / 2 - window.innerHeight / (2 * scale);
          window.scrollTo(0, Math.max(0, window.scrollY + want));
        }
      }
      b.style.zoom = scale;
    },
    /* Bring a point of the page to the middle of the screen, and say
       where it sits (as a fraction of the viewport), so the camera can
       push in on it. The page itself is never scaled: a CSS zoom
       reflows a phone-width layout, and names came out as "Chris Bos...". */
    aim(sel) {
      const t = sel && document.querySelector(sel);
      if (!t) return {x: 0.5, y: 0.45};
      let r = t.getBoundingClientRect();
      const want = r.top + r.height / 2 - window.innerHeight * 0.45;
      window.scrollTo(0, Math.max(0, window.scrollY + want));
      r = t.getBoundingClientRect();
      return {x: (r.left + r.width / 2) / window.innerWidth,
              y: (r.top + r.height / 2) / window.innerHeight};
    },
    unzoom(ms) {
      const b = document.body;
      b.style.transition = `zoom ${ms || 600}ms cubic-bezier(.4,0,.2,1)`;
      b.style.zoom = 1;
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
""" % {"navy": NAVY, "flame": FLAME, "amber": AMBER, "cream": CREAM, "muted": MUTED}


# Pull the headline row's real numbers off the board, so the hook card
# writes itself from this week's data instead of from a fixed string.
TOP_ROW_JS = """
() => {
  const a = document.querySelector('.sk-item');
  if (!a) return null;
  const txt = s => { const e = a.querySelector(s); return e ? e.textContent.trim() : ''; };
  const nameEl = a.querySelector('.sk-name');
  let name = '';
  if (nameEl) {
    for (const n of nameEl.childNodes) {
      if (n.nodeType === 3) name += n.textContent;
    }
  }
  // The prop's short label is a bare text node sitting after the line,
  // so it has to be read off the child list rather than by selector.
  let prop = '';
  const propEl = a.querySelector('.sk-prop');
  if (propEl) {
    let seenLine = false;
    for (const n of propEl.childNodes) {
      if (n.nodeType === 1 && n.classList.contains('ln')) { seenLine = true; continue; }
      if (seenLine && n.nodeType === 3) prop += n.textContent;
      if (seenLine && n.nodeType === 1) break;
    }
  }
  return {
    name: name.trim(), team: txt('.sk-name .tm'),
    side: txt('.sk-prop .o'), line: txt('.sk-prop .ln'),
    prop: prop.trim().replace(/\\s+/g, ' '),
    rate: txt('.rate').replace(/\\s+/g, ' '),
    streak: txt('.streak'), edge: txt('.sk-edge .val')
  };
}
"""
