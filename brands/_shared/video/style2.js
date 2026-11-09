/* Editor-style kit: the things a person editing a Fortnite short in CapCut actually does.
 *  - captions that pop word by word, synced to the voiceover (active word in yellow)
 *  - a map camera with quick zooms, motion blur on fast moves, and a little handheld drift
 *  - hand-drawn marker circles, arrows and scrawled labels (imperfect on purpose)
 *  - film grain, a screenshot flash, in-game style toasts, a short code card
 * No persistent lower thirds, tickers or broadcast furniture.
 */
(function (root) {
  "use strict";
  const { clamp, k, lerp, E, mk, T, svg } = root.FX;
  const INK = "#0B0D1A", YEL = "#FFD21E", BLUE = "#0E2F9E";

  // deterministic smooth noise for drift
  const nz = (t, f, s) => Math.sin(t * f + s) * 0.6 + Math.sin(t * f * 2.3 + s * 1.7) * 0.3 + Math.sin(t * f * 5.1 + s * 0.3) * 0.1;

  // ---------- map camera ----------
  // kf: [[t, {x,y}, zoom, ease?]] in map pixels; F = on-screen anchor
  function mapCam(stage, src, kf, F = { x: 540, y: 820 }, size = 2048) {
    const wrap = mk(stage, `left:0;top:0;width:${size}px;height:${size}px;transform-origin:0 0;will-change:transform`, `<img src="${src}" style="width:${size}px;height:${size}px">`);
    const at = (t) => {
      let i = 0; while (i < kf.length - 2 && t > kf[i + 1][0]) i++;
      const [t0, a, z0] = kf[i], [t1, b, z1, ease] = kf[i + 1];
      const p = (E[ease || "inOut"])(k(t, t0, t1));
      return { x: lerp(a.x, b.x, p), y: lerp(a.y, b.y, p), z: Math.exp(lerp(Math.log(z0), Math.log(z1), p)) };
    };
    function draw(t) {
      const c = at(t), c2 = at(t + 1 / 60);
      const speed = Math.hypot((c2.x - c.x) * c.z, (c2.y - c.y) * c.z) + Math.abs(Math.log(c2.z / c.z)) * 900;
      const dx = nz(t, 0.9, 1) * 6, dy = nz(t, 0.8, 4) * 6, rot = nz(t, 0.5, 7) * 0.35;
      wrap.style.transform = `translate(${F.x - c.x * c.z + dx}px,${F.y - c.y * c.z + dy}px) rotate(${rot}deg) scale(${c.z})`;
      wrap.style.filter = speed > 6 ? `blur(${Math.min(7, (speed - 6) * 0.22).toFixed(2)}px)` : "none";
      return { c, toScreen: (pt) => ({ x: F.x + (pt.x - c.x) * c.z + dx, y: F.y + (pt.y - c.y) * c.z + dy }) };
    }
    // optional second map image in the same frame (e.g. the labeled map), faded in by the scene
    const overlay = (src2) => { const im = document.createElement("img"); im.src = src2; im.style.cssText = `position:absolute;left:0;top:0;width:${size}px;height:${size}px;opacity:0`; wrap.appendChild(im); return im; };
    return { draw, at, overlay };
  }

  // ---------- word-by-word captions ----------
  // groups words into 1-3 word chunks, breaking at phrase ends and punctuation
  function captions(stage, words, o = {}) {
    const { y = 1150, size = 74, max = 3 } = o;
    const chunks = [];
    let cur = [];
    words.forEach((w, i) => {
      cur.push(w);
      const next = words[i + 1];
      const end = !next || next.p !== w.p || /[.,?!]$/.test(w.w) || cur.length >= max;
      if (end) { chunks.push(cur); cur = []; }
    });
    const box = mk(stage, `left:60px;width:880px;top:${y}px;text-align:center;line-height:1.08`);
    const els = chunks.map((ch) => {
      const line = mk(box, `left:0;right:0;top:0;text-align:center`, "", ch.map((w) => w.w).join(" ").toUpperCase());
      line.innerHTML = ch.map((w) => `<span style="display:inline-block;margin:0 ${size * 0.12}px;font-family:Montserrat;font-weight:900;font-size:${size}px;letter-spacing:-.5px;text-transform:uppercase;color:#fff;-webkit-text-stroke:${size * 0.16}px ${INK};paint-order:stroke fill;text-shadow:0 ${size * 0.07}px 0 rgba(0,0,0,.45);transform-origin:50% 80%">${w.w.replace(/[,]$/, "")}</span>`).join(" ");
      return { line, ch, spans: [...line.children] };
    });
    // each chunk stays up until the next one starts (never two at once)
    els.forEach((e, i) => { const nx = els[i + 1]; e.t0 = e.ch[0].t0 - 0.04; e.t1 = Math.min(e.ch[e.ch.length - 1].t1 + 0.35, nx ? nx.ch[0].t0 - 0.04 : 1e9); });
    return function draw(t) {
      els.forEach(({ line, ch, spans, t0, t1 }) => {
        const on = t >= t0 && t < t1;
        line.style.visibility = on ? "visible" : "hidden";
        if (!on) return;
        spans.forEach((sp, i) => {
          const w = ch[i], said = t >= w.t0 - 0.03;
          const pop = E.back(k(t, w.t0 - 0.03, w.t0 + 0.09));
          sp.style.opacity = said ? 1 : 0;
          sp.style.color = t >= w.t0 - 0.03 && t < w.t1 + 0.02 ? YEL : "#fff";
          sp.style.transform = `scale(${said ? lerp(0.6, 1, pop) : 0.6})`;
        });
      });
    };
  }

  // ---------- hand-drawn marker circle / arrow / label ----------
  function scribble(rx, ry, seed) {
    // a slightly lopsided loop that overshoots its start, like a real marker circle
    let d = "", n = 40;
    for (let i = 0; i <= n + 6; i++) {
      const a = (i / n) * Math.PI * 2 - 0.6;
      const wob = 1 + Math.sin(i * 0.9 + seed) * 0.05 + (i > n ? 0.08 : 0);
      const x = Math.cos(a) * rx * wob, y = Math.sin(a) * ry * wob * (1 + 0.04 * Math.sin(seed));
      d += (i ? "L" : "M") + (x + rx * 1.2).toFixed(1) + " " + (y + ry * 1.2).toFixed(1) + " ";
    }
    return d;
  }
  function marker(stage, rx, ry, color = "#FF2D2D", seed = 1) {
    const w = rx * 2.4, h = ry * 2.4;
    const g = mk(stage, `left:0;top:0;width:${w}px;height:${h}px`, svg(w, h, `<path d="${scribble(rx, ry, seed)}" fill="none" stroke="${color}" stroke-width="11" stroke-linecap="round" stroke-linejoin="round" style="filter:drop-shadow(0 3px 0 rgba(0,0,0,.35))"/>`));
    const path = g.querySelector("path"); const L = path.getTotalLength();
    path.style.strokeDasharray = L;
    return { place(x, y, t, t0, a = 1, s = 1) { T(g, { x: x - w / 2, y: y - h / 2, a, s }); path.style.strokeDashoffset = L * (1 - E.out(k(t, t0, t0 + 0.32))); } };
  }
  function scrawl(stage, text, size = 60, color = "#fff", rot = -6) {
    const g = mk(stage, `left:0;top:0;white-space:nowrap;font-family:'Permanent Marker';font-size:${size}px;line-height:1;color:${color};-webkit-text-stroke:${size * 0.12}px ${INK};paint-order:stroke fill;text-shadow:0 4px 0 rgba(0,0,0,.4);transform-origin:0 50%`, text, text.toUpperCase());
    return {
      place(x, y, t, t0, a = 1) {
        T(g, { x, y, r: rot, a: a * (t >= t0 ? 1 : 0), s: E.back(k(t, t0, t0 + 0.25)) });
        // a label pinned to the map hides once the camera carries it out of the app-safe area
        if (a > 0 && t >= t0) { const r = g.getBoundingClientRect(); if (!inSafe(r)) T(g, { x, y, r: rot, a: 0 }); }
      },
    };
  }
  // union of TikTok / Reels / Shorts UI on a 1080x1920 frame (same numbers the checklist uses)
  const SAFE = { left: 40, right: 1040, top: 150, bottom: 1440, colX: 940, colY: 700 };
  const inSafe = (r) => r.left >= SAFE.left && r.right <= SAFE.right && r.top >= SAFE.top && r.bottom <= SAFE.bottom && !(r.right > SAFE.colX && r.bottom > SAFE.colY);
  function arrow(stage, x1, y1, x2, y2, color = "#fff") {
    const mx = (x1 + x2) / 2 + (y2 - y1) * 0.18, my = (y1 + y2) / 2 - (x2 - x1) * 0.18;
    const ang = Math.atan2(y2 - my, x2 - mx), hl = 36;
    const g = mk(stage, `left:0;top:0;width:1080px;height:1920px`, svg(1080, 1920,
      `<path d="M${x1} ${y1} Q${mx} ${my} ${x2} ${y2}" fill="none" stroke="${INK}" stroke-width="20" stroke-linecap="round"/><path d="M${x1} ${y1} Q${mx} ${my} ${x2} ${y2}" fill="none" stroke="${color}" stroke-width="11" stroke-linecap="round"/>
       <path d="M${x2 - hl * Math.cos(ang - 0.5)} ${y2 - hl * Math.sin(ang - 0.5)} L${x2} ${y2} L${x2 - hl * Math.cos(ang + 0.5)} ${y2 - hl * Math.sin(ang + 0.5)}" fill="none" stroke="${INK}" stroke-width="20" stroke-linecap="round" stroke-linejoin="round"/>
       <path d="M${x2 - hl * Math.cos(ang - 0.5)} ${y2 - hl * Math.sin(ang - 0.5)} L${x2} ${y2} L${x2 - hl * Math.cos(ang + 0.5)} ${y2 - hl * Math.sin(ang + 0.5)}" fill="none" stroke="${color}" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/>`));
    const paths = [...g.querySelectorAll("path")];
    paths.forEach((p) => { const L = p.getTotalLength(); p.style.strokeDasharray = L; p.dataset.L = L; });
    return { draw(t, t0, a = 1) { T(g, { a }); paths.forEach((p, i) => { const s = i < 2 ? k(t, t0, t0 + 0.28) : k(t, t0 + 0.24, t0 + 0.34); p.style.strokeDashoffset = p.dataset.L * (1 - E.out(s)); }); } };
  }

  // ---------- top title, the way editors put a hook line at the top ----------
  function title(stage, lines, o = {}) {
    const { y = 270, size = 84 } = o;
    return mk(stage, `left:60px;width:880px;top:${y}px;text-align:center`, lines.map((l, i) =>
      `<div style="font-family:Montserrat;font-weight:900;font-size:${size}px;line-height:1.02;letter-spacing:-1px;text-transform:uppercase;color:${i ? YEL : "#fff"};-webkit-text-stroke:${size * 0.15}px ${INK};paint-order:stroke fill;text-shadow:0 ${size * 0.07}px 0 rgba(0,0,0,.45)">${l}</div>`).join(""), lines.join(" / ").toUpperCase());
  }

  // ---------- grain, vignette, flash ----------
  function finish(stage) {
    const c = document.createElement("canvas"); c.width = c.height = 256;
    const g = c.getContext("2d"), im = g.createImageData(256, 256);
    let s = 7; const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
    for (let i = 0; i < im.data.length; i += 4) { const v = rnd() * 255; im.data[i] = im.data[i + 1] = im.data[i + 2] = v; im.data[i + 3] = 255; }
    g.putImageData(im, 0, 0);
    const vig = mk(stage, `inset:0;background:radial-gradient(ellipse 80% 65% at 50% 45%,transparent 55%,rgba(0,0,0,.42));pointer-events:none`);
    const grain = mk(stage, `left:-128px;top:-128px;width:1336px;height:2176px;background:url(${c.toDataURL()});opacity:.07;mix-blend-mode:overlay;pointer-events:none`);
    const flash = mk(stage, `inset:0;background:#fff;pointer-events:none`);
    return function draw(t, flashes = []) {
      const f = Math.floor(t * 24);
      grain.style.transform = `translate(${(f * 37) % 128}px,${(f * 71) % 128}px)`;
      T(vig, { a: 1 });
      const fl = flashes.reduce((m, t0) => Math.max(m, t >= t0 ? 1 - k(t, t0, t0 + 0.28) : 0), 0);
      T(flash, { a: fl * 0.85 });
    };
  }

  // ---------- in-game style toast (like a quest / notification popup) ----------
  function toast(stage, label, text, y = 520, accent = YEL) {
    return mk(stage, `left:140px;width:800px;top:${y}px;display:flex;align-items:center;gap:22px;padding:20px 28px;border-radius:12px;background:linear-gradient(90deg,rgba(9,22,74,.96),rgba(16,52,160,.92));border:4px solid ${INK};box-shadow:0 8px 0 rgba(0,0,0,.35)`,
      `<div style="width:66px;height:66px;flex:none;border-radius:50%;background:${accent};border:4px solid ${INK};display:flex;align-items:center;justify-content:center;font-family:Montserrat;font-weight:900;font-size:40px;color:${INK}">!</div>
       <div><div style="font:800 24px Montserrat;letter-spacing:2px;color:${accent}">${label}</div><div style="font-family:Montserrat;font-weight:900;font-size:44px;line-height:1.05;color:#fff;text-transform:uppercase">${text}</div></div>`, `${label} / ${text}`.toUpperCase());
  }

  // ---------- the code moment: short, on brand ----------
  function codeCard(stage, y = 560) {
    return mk(stage, `left:190px;width:700px;top:${y}px;text-align:center;padding:26px 0 24px;border-radius:22px;background:linear-gradient(180deg,#1A63E6,${BLUE});border:6px solid ${INK};box-shadow:0 12px 0 rgba(0,0,0,.4)`,
      `<div style="display:inline-block;background:${YEL};border:4px solid ${INK};border-radius:8px;transform:skewX(-7deg);padding:0 16px;font-family:'Barlow Condensed';font-weight:900;font-size:40px;color:${BLUE}">CODE</div>
       <div style="font-family:'Barlow Condensed';font-weight:900;font-size:190px;line-height:.95;color:${YEL};-webkit-text-stroke:16px ${INK};paint-order:stroke fill;text-shadow:0 10px 0 #B06A00,0 16px 0 ${INK};transform:skewX(-7deg)">BAD</div>
       <div style="font:700 30px Montserrat;color:#fff;margin-top:14px">Item Shop → Support-A-Creator</div>
       <div style="font:800 26px Montserrat;color:${YEL};margin-top:6px">#EpicPartner</div>`,
      "CODE / BAD / ITEM SHOP → SUPPORT-A-CREATOR / #EPICPARTNER");
  }

  // sign-off under the code card, shown while the last line ("Thank you for watching! Follow for more daily content.") plays
  function thanksStrip(stage, y = 960) {
    return mk(stage, `left:190px;width:700px;top:${y}px;text-align:center`,
      `<div style="display:inline-block;padding:8px 26px 4px;background:${YEL};border:5px solid ${INK};border-radius:10px;transform:skewX(-7deg);box-shadow:0 6px 0 ${INK};font-family:'Barlow Condensed';font-weight:900;font-size:48px;line-height:1;color:${INK}">THANK YOU FOR WATCHING!</div>
       <div style="font:800 30px Montserrat;color:#fff;margin-top:12px;-webkit-text-stroke:5px ${INK};paint-order:stroke fill">Follow for more daily content</div>`,
      "THANK YOU FOR WATCHING! / FOLLOW FOR MORE DAILY CONTENT");
  }

  // required disclosure, on screen from frame 1; small, like a creator would place it
  const disclosure = (stage) => mk(stage, `left:44px;top:166px;padding:6px 16px;border-radius:24px;background:rgba(11,13,26,.72);border:2px solid rgba(255,255,255,.85)`, `<span style="font:800 24px Montserrat;color:#fff">#EpicPartner</span>`, "#EPICPARTNER");

  root.STYLE2 = { mapCam, captions, marker, scrawl, arrow, title, finish, toast, codeCard, disclosure, nz, thanksStrip };
})(window);
