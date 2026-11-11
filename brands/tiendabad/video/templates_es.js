/* LA TIENDITA BAD short templates (Spanish port of BAD ROYALE's). A short's scene.js is just a config:  window.SCENE = TPL.guess({...})
 * Every template: hook on screen from frame 1, word-by-word captions, #EpicPartner on screen, the code card on the
 * last voice line. Beats are tied to the voice: "p" is a phrase index in vo.json (P[p].t0 = when it starts).
 *   TPL.guess({ title, rounds: [{id, name, img, rarity, chapter, season}] })      Guess the Season
 *   TPL.map({ title, map, shots, pins, toasts })                                  a place / Easter egg on the map
 *   TPL.news({ kicker, tile, quote: [{p, text}], points: [{p, label, text}] })    an in-game news tile explained
 *   TPL.drop({ title, cards, name, contents, price, when, tone })                 a shop listing: new today / leaving tonight
 *   TPL.guide({ kicker, title, facts: [{p, label, text}] })                        a feature explained: each fact lands on its voice line
 * Each takes caption and claims too (see make_short.py and the pre-post checklist).
 */
(function (root) {
  "use strict";
  const { k, lerp, E, win, mk, T } = root.FX;
  const S = root.STYLE2;
  const INK = "#0B0D1A", YEL = "#FFD21E", RED = "#FF2D2D";
  const RAR = { common: ["#C2C2C2", "#5E5E5E"], uncommon: ["#8BE33B", "#1C6B10"], rare: ["#5BD0FF", "#1A5BB5"], epic: ["#D27BFF", "#5B1F9C"], legendary: ["#F7AE52", "#8A3B12"],
    gaminglegends: ["#7B6BFF", "#1E1557"], icon: ["#5CF2F3", "#0F6B7A"], marvel: ["#F0473C", "#5A0F0B"], dc: ["#5A84FF", "#14205E"], starwars: ["#3D3D3D", "#0A0A0A"], dark: ["#FF4FD8", "#3A0A4F"], shadow: ["#6E6E6E", "#141414"] };
  // official map: game coordinates -> pixels on the 2048 map image (fitted to the POI data, error < 1 px)
  const mapPx = (w) => ({ x: 0.0066081 * w.x + 976.46, y: 0.0066058 * w.y + 1016.67 });
  // the code line is the phrase that says the code; whatever follows it (the sign-off) plays over the code card
  const codeIndex = (P) => { for (let i = P.length - 1; i >= 0; i--) if (/\b(code|código)\b[^.?!]*\bbad\b/i.test(P[i].text)) return i; return P.length - 1; };
  const timing = () => { const VO = root.VO, P = VO.phrases, codeI = codeIndex(P); return { VO, P, DUR: Math.round((P[P.length - 1].t1 + 0.7) * 10) / 10, codeI, codeP: P[codeI], thanksP: P[codeI + 1] }; };
  const signoff = (e, t, thanksP, DUR) => T(e.thanks, { a: thanksP ? win(t, thanksP.t0 - 0.05, DUR + 1, 0.12, 0.1) : 0, s: thanksP ? 0.9 + 0.1 * E.back(k(t, thanksP.t0 - 0.05, thanksP.t0 + 0.3)) : 1 });
  const wall = (stage) => {
    mk(stage, `inset:0;background:linear-gradient(160deg,#1D2E8F,#14156B 45%,#2A0F5E)`);
    return mk(stage, `left:-600px;top:0;width:2400px;height:1920px;background:repeating-linear-gradient(115deg,rgba(255,255,255,.05) 0 90px,transparent 90px 260px)`);
  };
  const chip = (stage, text, color = YEL, top = 250) => mk(stage, `left:60px;top:${top}px;padding:8px 22px 4px;background:${color};border:5px solid ${INK};border-radius:10px;transform:skewX(-8deg);box-shadow:0 6px 0 ${INK}`,
    `<span style="display:inline-block;font-family:'Barlow Condensed';font-weight:900;font-size:50px;line-height:1;color:${INK};text-transform:uppercase">${text}</span>`, text.toUpperCase());
  function tile(stage, o, w, h) {
    const [a, b] = RAR[o.rarity] || RAR.epic;
    const el = mk(stage, `left:0;top:0;width:${w}px;height:${h}px;border-radius:${w * 0.05}px;overflow:hidden;background:radial-gradient(circle at 50% 30%,${a},${b} 80%);border:${Math.max(5, w * 0.018)}px solid ${INK};box-shadow:0 ${w * 0.03}px 0 rgba(0,0,0,.45);transform-origin:50% 60%`,
      `<div style="position:absolute;inset:0;background:linear-gradient(120deg,rgba(255,255,255,.18) 0 18%,transparent 18%)"></div>
       <img src="${o.img}" style="position:absolute;left:${-w * 0.2}px;top:${-h * 0.02}px;width:${w * 1.4}px;height:auto">
       <div style="position:absolute;left:0;right:0;bottom:0;padding:${w * 0.05}px ${w * 0.06}px ${w * 0.045}px;background:linear-gradient(transparent,rgba(5,8,22,.92) 30%)">
         <div style="font-family:'Barlow Condensed';font-weight:900;font-size:${Math.min(w * 0.14, (w * 0.86) / (o.name.length * 0.43))}px;line-height:.95;text-transform:uppercase;color:#fff;-webkit-text-stroke:${w * 0.012}px ${INK};paint-order:stroke fill;white-space:nowrap">${o.name}</div>
       </div>`, o.name.toUpperCase());
    return el;
  }
  const BR = "../../tiendabad/brand";
  const codeES = (stage, y = 520) => {
    return mk(stage, `left:170px;width:740px;top:${y - 140}px;text-align:center`,
      `<img src="${BR}/awning.svg" style="position:relative;z-index:2;display:block;width:740px;height:auto">
       <div style="position:relative;z-index:1;margin:-32px 32px 0;padding:48px 0 24px;border-radius:0 0 24px 24px;background:linear-gradient(180deg,#36D3E2,#13B5C6 55%,#0B8796);border:6px solid ${INK};border-top:0;box-shadow:0 12px 0 rgba(0,0,0,.4)">
         <div style="display:inline-block;background:${YEL};border:4px solid ${INK};border-radius:8px;transform:skewX(-7deg);padding:0 16px;font-family:'Barlow Condensed';font-weight:900;font-size:40px;color:${INK}">CÓDIGO</div>
         <img src="${BR}/bad.svg" style="display:block;margin:8px auto 0;height:176px">
         <div style="font:800 31px Montserrat;color:#fff;margin-top:12px;-webkit-text-stroke:7px ${INK};paint-order:stroke fill">Tienda de objetos → Apoya a un creador</div>
         <div style="display:inline-block;margin-top:12px;padding:6px 18px;border-radius:30px;background:${INK};font:800 26px Montserrat;color:${YEL}">Dura 14 días · ¡renuévalo!</div>
         <div style="font:800 26px Montserrat;color:#fff;margin-top:8px;-webkit-text-stroke:6px ${INK};paint-order:stroke fill">#EpicPartner</div>
       </div>`,
      "CÓDIGO / BAD / TIENDA DE OBJETOS → APOYA A UN CREADOR / DURA 14 DÍAS · ¡RENUÉVALO! / #EPICPARTNER");
  };
  const thanksES = (stage, y) => mk(stage, `left:170px;width:740px;top:${y}px;text-align:center`,
    `<div style="display:inline-block;padding:8px 26px 4px;background:${YEL};border:5px solid ${INK};border-radius:10px;transform:skewX(-7deg);box-shadow:0 6px 0 ${INK};font-family:'Barlow Condensed';font-weight:900;font-size:48px;line-height:1;color:${INK}">¡GRACIAS POR VER!</div>
     <div style="font:800 30px Montserrat;color:#fff;margin-top:12px;-webkit-text-stroke:5px ${INK};paint-order:stroke fill">Síguenos para más contenido diario</div>`, "¡GRACIAS POR VER! / SÍGUENOS PARA MÁS CONTENIDO DIARIO");
  const bugES = (stage) => mk(stage, `right:44px;top:154px;width:200px`, `<img src="${BR}/watermark.svg" style="display:block;width:200px;height:auto">`, "LA TIENDITA BAD");
  const end = (stage, VO) => ({ code: codeES(stage, 500), thanks: thanksES(stage, 1010), caps: S.captions(stage, VO.words, { y: 1150 }), disc: S.disclosure(stage), bug: bugES(stage), fin: S.finish(stage) });

  // ---------------- Guess the Season ----------------
  // voice: [hook, name1, answer1, name2, answer2, ..., "How many did you get?", code line]
  function guess(cfg) {
    const { VO, P, DUR, codeI, codeP, thanksP } = timing();
    const R = cfg.rounds, nameP = (i) => P[1 + 2 * i], ansP = (i) => P[2 + 2 * i];
    const outro = P[codeI - 1];
    function build(stage) {
      const bands = wall(stage);
      const hook = mk(stage, `inset:0`);
      const hookTiles = R.map((o) => tile(hook, o, 250, 360));
      const qs = R.map((_, i) => mk(hook, `left:0;top:0;width:250px;text-align:center;font-family:'Barlow Condensed';font-weight:900;font-size:150px;line-height:1;color:${YEL};-webkit-text-stroke:12px ${INK};paint-order:stroke fill`, "?", "?"));
      const title = S.title(stage, cfg.title || ["Adivina la temporada", "3 skins · sin trampa"], { y: 236, size: 66 });
      const rounds = R.map((o, i) => {
        const g = mk(stage, `inset:0`);
        const card = tile(g, o, 500, 680);
        const counter = chip(g, `${i + 1} / ${R.length}`, "#fff");
        const ring = mk(g, `left:810px;top:300px;width:170px;height:170px;border-radius:50%;background:${INK};border:8px solid ${YEL};display:flex;align-items:center;justify-content:center;font-family:'Barlow Condensed';font-weight:900;font-size:110px;color:#fff`, "3", "CUENTA");
        const stamp = mk(g, `left:210px;top:1012px;width:660px;text-align:center;padding:10px 0 4px;background:${YEL};border:8px solid ${INK};transform:rotate(-5deg);box-shadow:0 10px 0 ${INK}`,
          `<span style="font-family:'Barlow Condensed';font-weight:900;font-size:56px;line-height:1;color:${INK};white-space:nowrap">CAPÍTULO ${o.chapter} · TEMPORADA ${o.season}</span>`, `CAPÍTULO ${o.chapter} · TEMPORADA ${o.season}`);
        const ask = mk(g, `left:170px;top:1018px;width:740px;text-align:center;font-family:Montserrat;font-weight:900;font-size:64px;color:#fff;-webkit-text-stroke:9px ${INK};paint-order:stroke fill`, "¿QUÉ TEMPORADA?", "¿QUÉ TEMPORADA?");
        return { g, card, counter, ring, stamp, ask };
      });
      const score = S.title(stage, ["¿Cuántas", "acertaste?"], { y: 330, size: 96 });
      const e = end(stage, VO);
      const dim = mk(stage, `inset:0;background:rgba(5,8,25,.55)`);
      stage.appendChild(e.code); stage.appendChild(e.thanks);
      return function renderAt(t) {
        bands.style.transform = `translateX(${(t * 40) % 260}px)`;
        const r0 = nameP(0).t0 - 0.12;
        const hookOn = t < r0 ? 1 : 1 - k(t, r0, r0 + 0.2);
        T(hook, { a: hookOn });
        hookTiles.forEach((el, i) => T(el, { x: 85 + i * 275, y: 560 + (i === 1 ? -40 : 0), r: (i - 1) * 6, s: E.back(k(t, 0.02 + i * 0.06, 0.3 + i * 0.06)) }));
        qs.forEach((q, i) => T(q, { x: 85 + i * 275, y: 630 + (i === 1 ? -40 : 0), s: E.back(k(t, 0.25 + i * 0.08, 0.5 + i * 0.08)), a: hookOn }));
        T(title, { a: hookOn, s: 1 - 0.06 * k(t, 0, r0) });
        rounds.forEach((r, i) => {
          const tIn = nameP(i).t0 - 0.12, tOut = i < R.length - 1 ? nameP(i + 1).t0 - 0.12 : outro.t0 - 0.1;
          const on = t >= tIn && t < tOut + 0.25;
          const pin = E.out5(k(t, tIn, tIn + 0.28)), pout = E.in(k(t, tOut - 0.05, tOut + 0.2));
          const gx = (1 - pin) * 900 - pout * 900;
          T(r.g, { a: on ? Math.max(0, 1 - Math.abs(gx) / 15) : 0, x: gx });   // visible only within 15 px of rest: never over the app UI
          T(r.card, { x: 290, y: 300, r: -2 + Math.sin(t * 1.3) * 1.2, s: 0.94 + 0.06 * E.back(k(t, tIn, tIn + 0.35)) });
          // countdown in the silence between the name and the answer
          const g0 = nameP(i).t1, g1 = ansP(i).t0, left = Math.max(1, Math.ceil(3 * (1 - k(t, g0, g1))));
          r.ring.textContent = String(left);
          T(r.ring, { a: t >= g0 - 0.1 && t < g1 ? 1 : 0, s: 1 + 0.12 * (1 - k((t - g0) % ((g1 - g0) / 3 || 1), 0, 0.2)) });
          const revealed = t >= ansP(i).t0 - 0.05;
          T(r.ask, { a: revealed ? 0 : 1 });
          T(r.stamp, { a: revealed ? 1 : 0, s: revealed ? 1.15 - 0.15 * E.out(k(t, ansP(i).t0 - 0.05, ansP(i).t0 + 0.18)) : 1.15, r: -5 });
        });
        const sc = win(t, outro.t0 - 0.1, codeP.t0 - 0.05, 0.15, 0.1);
        T(score, { a: sc, s: 0.9 + 0.1 * E.back(k(t, outro.t0 - 0.1, outro.t0 + 0.25)) });
        const codeOn = win(t, codeP.t0 - 0.05, DUR + 1, 0.12, 0.1);
        T(dim, { a: codeOn }); T(e.code, { a: codeOn, s: 0.86 + 0.14 * E.back(k(t, codeP.t0 - 0.05, codeP.t0 + 0.3)) }); signoff(e, t, thanksP, DUR);
        e.caps(t); e.fin(t, R.map((_, i) => ansP(i).t0 - 0.03));
      };
    }
    const cues = [[0.03, "pop"], [0.1, "flip"], [0.16, "flip"], [0.22, "flip"]];
    R.forEach((_, i) => cues.push([nameP(i).t0 - 0.12, "whoosh"], [nameP(i).t1 + 0.05, "tick"], [ansP(i).t0 - 0.03, "reveal"]));
    cues.push([codeP.t0 - 0.05, "notify"]);
    if (thanksP) cues.push([thanksP.t0 - 0.03, "chime"]);
    return { duration: DUR, build, cues, caption: cfg.caption, claims: cfg.claims };
  }

  // ---------------- a spot on the map ----------------
  // shots: [{p, dt?, x, y, z, ease?}] camera keyframes (map pixels); pins: [{x, y, p, label, color?}]; toasts: [{p, label, text}]
  function map(cfg) {
    const { VO, P, DUR, codeP, thanksP } = timing();
    const at = (s) => (s.p === "end" ? DUR : P[s.p].t0 + (s.dt || 0));
    // the camera holds still and only moves in a short whip before each shot, so no frame is a slow blur
    const kf = [];
    cfg.shots.forEach((s, i) => {
      const t = i === 0 ? 0 : at(s);
      if (i > 0) { const [pt, pp, pz] = kf[kf.length - 1]; kf.push([Math.max(pt + 0.01, t - (s.move || 0.6)), pp, pz]); }
      kf.push([t, { x: s.x, y: s.y }, s.z, s.ease || "inOut"]);
    });
    function build(stage) {
      mk(stage, `inset:0;background:#084687`);
      const cam = S.mapCam(stage, `../../tiendabad/sources/${cfg.map === "blank" ? "map-blank" : "map-labels"}.png`, kf);
      const shade = mk(stage, `inset:0;background:rgba(5,10,28,.35)`);
      const pins = cfg.pins.map((p, i) => ({ ...p, ring: S.marker(stage, p.r || 70, p.r || 70, p.color || RED, i * 1.9 + 0.7), lab: p.label ? S.scrawl(stage, p.label, p.size || 66, YEL, -7) : null,
        pulse: mk(stage, `left:0;top:0;width:200px;height:200px;border-radius:50%;border:8px solid ${YEL}`) }));
      const dim = mk(stage, `inset:0;background:rgba(5,10,28,.72)`);
      const toasts = (cfg.toasts || []).map((o, i) => ({ ...o, el: S.toast(stage, o.label, o.text, 520 + i * 190, o.color || YEL) }));
      const title = S.title(stage, cfg.title, { y: 236, size: cfg.titleSize || 60 });
      const e = end(stage, VO);
      return function renderAt(t) {
        const { toScreen } = cam.draw(t);
        pins.forEach((p) => {
          const f = toScreen(p), t0 = P[p.p].t0 + (p.dt || 0.2), pr = (t % 1.1) / 1.1;
          T(p.pulse, { x: f.x - 100, y: f.y - 100, s: 0.4 + pr * 0.9, a: t < t0 && t >= P[p.p].t0 - 0.6 ? 1 - pr : 0 });
          p.ring.place(f.x, f.y, t, t0, t >= t0 ? 1 : 0, 1);
          if (p.lab) p.lab.place(f.x + (p.lx ?? 40), f.y + (p.ly ?? 80), t, t0 + 0.15, t >= t0 + 0.15 ? 1 : 0);
        });
        const tOn = toasts.length ? win(t, P[toasts[0].p].t0 - 0.05, codeP.t0, 0.08, 0.12) : 0;
        const codeOn = win(t, codeP.t0 - 0.05, DUR + 1, 0.12, 0.1);
        T(dim, { a: Math.max(tOn, codeOn) });
        toasts.forEach((o) => { const x = (1 - E.out(k(t, P[o.p].t0 - 0.05, P[o.p].t0 + 0.22))) * -700; T(o.el, { a: t >= P[o.p].t0 - 0.05 ? tOn * Math.max(0, 1 - Math.abs(x) / 60) : 0, x }); });
        T(e.code, { a: codeOn, s: 0.86 + 0.14 * E.back(k(t, codeP.t0 - 0.05, codeP.t0 + 0.3)) }); signoff(e, t, thanksP, DUR);
        T(title, { a: 1 - Math.max(tOn, codeOn), s: 0.96 + 0.04 * E.out(k(t, 0, 0.3)) });
        e.caps(t); e.fin(t);
      };
    }
    const cues = [[0.03, "ping"], ...cfg.pins.map((p) => [P[p.p].t0 + (p.dt || 0.2), "marker"]), ...cfg.shots.slice(1).filter((s) => s.p !== "end" && s.whoosh).map((s) => [at(s) - 0.1, "whoosh"]),
      ...(cfg.toasts || []).map((o) => [P[o.p].t0, "notify"]), [codeP.t0 - 0.05, "pop"]];
    if (thanksP) cues.push([thanksP.t0 - 0.03, "chime"]);
    return { duration: DUR, build, cues, caption: cfg.caption, claims: cfg.claims };
  }

  // ---------------- an in-game news tile, explained ----------------
  // quote: lines of the tile's own text, each shown from phrase p; points: takeaways as in-game toasts
  function news(cfg) {
    const { VO, P, DUR, codeP, thanksP } = timing();
    function build(stage) {
      const bands = wall(stage);
      const panel = mk(stage, `left:50px;top:330px;width:860px;border-radius:18px;overflow:hidden;border:6px solid ${INK};box-shadow:0 14px 0 rgba(0,0,0,.4);background:linear-gradient(180deg,#0E1B5C,#0A1240)`,
        `<div style="display:flex;align-items:center;gap:14px;padding:16px 26px;background:${YEL};border-bottom:6px solid ${INK}">
           <span style="font-family:'Barlow Condensed';font-weight:900;font-size:44px;color:${INK}">${cfg.kicker || "NOTICIAS DEL JUEGO"}</span></div>
         <div style="padding:26px 34px 34px">
           <div style="font-family:Montserrat;font-weight:900;font-size:${cfg.tileSize || 62}px;line-height:1.02;text-transform:uppercase;color:#fff;-webkit-text-stroke:3px ${INK};paint-order:stroke fill">${cfg.tile}</div>
           <div class="q" style="margin-top:22px;min-height:120px"></div></div>`, `${cfg.kicker || "NOTICIAS DEL JUEGO"} / ${cfg.tile}`.toUpperCase());
      const q = panel.querySelector(".q");
      const lines = cfg.quote.map((l) => { const d = document.createElement("div"); d.style.cssText = `font:700 40px/1.25 Montserrat;color:#C9D6FF;margin-top:10px;opacity:0`; d.textContent = `“${l.text}”`; d.dataset.text = l.text.toUpperCase(); q.appendChild(d); return { ...l, d }; });
      const hook = cfg.hook ? S.title(stage, cfg.hook, { y: 950, size: 78 }) : null;
      const hookEnd = P[cfg.quote[0].p].t0 - 0.1;
      const dim = mk(stage, `inset:0;background:rgba(5,8,25,.75)`);
      const points = (cfg.points || []).map((o, i) => ({ ...o, el: S.toast(stage, o.label, o.text, 480 + i * 190, o.color || YEL) }));
      const e = end(stage, VO);
      return function renderAt(t) {
        bands.style.transform = `translateX(${(t * 40) % 260}px)`;
        T(panel, { y: 0, s: 0.94 + 0.06 * E.out(k(t, 0, 0.3)), r: -0.6 + Math.sin(t * 0.8) * 0.3 });
        if (hook) T(hook, { a: 1 - k(t, hookEnd, hookEnd + 0.2), s: 0.88 + 0.12 * E.out(k(t, 0, 0.3)) });
        lines.forEach((l) => { const on = t >= P[l.p].t0 - 0.05; l.d.style.opacity = on ? 1 : 0; l.d.style.color = on && t < P[l.p].t1 + 0.1 ? "#fff" : "#C9D6FF"; });
        const pOn = points.length ? win(t, P[points[0].p].t0 - 0.05, codeP.t0, 0.08, 0.12) : 0;
        const codeOn = win(t, codeP.t0 - 0.05, DUR + 1, 0.12, 0.1);
        T(dim, { a: Math.max(pOn, codeOn) });
        points.forEach((o) => { const x = (1 - E.out(k(t, P[o.p].t0 - 0.05, P[o.p].t0 + 0.22))) * -700; T(o.el, { a: t >= P[o.p].t0 - 0.05 ? pOn * Math.max(0, 1 - Math.abs(x) / 60) : 0, x }); });
        T(e.code, { a: codeOn, s: 0.86 + 0.14 * E.back(k(t, codeP.t0 - 0.05, codeP.t0 + 0.3)) }); signoff(e, t, thanksP, DUR);
        e.caps(t); e.fin(t);
      };
    }
    const cues = [[0.03, "pop"], ...cfg.quote.map((l) => [P[l.p].t0, "tick"]), ...(cfg.points || []).map((o) => [P[o.p].t0, "notify"]), [codeP.t0 - 0.05, "pop"]];
    if (thanksP) cues.push([thanksP.t0 - 0.03, "chime"]);
    return { duration: DUR, build, cues, caption: cfg.caption, claims: cfg.claims };
  }

  // ---------------- a feature explained: kicker, headline, 3-4 facts that land on their voice lines ----------------
  // cfg: { kicker, title: [l1, l2], titleSize?, facts: [{p, label, text, color?}] (max 4) }
  function guide(cfg) {
    const { VO, P, DUR, codeP, thanksP } = timing();
    const F = (cfg.facts || []).slice(0, 4), gap = F.length > 3 ? 150 : 190, top = F.length > 3 ? 500 : 560;
    function build(stage) {
      // the real map behind everything (owner: "make sure you show the actual map as well"), a slow push-in, shaded so the text reads
      mk(stage, `inset:0;background:#084687`);
      const cam = S.mapCam(stage, `../../tiendabad/sources/${cfg.map === "blank" ? "map-blank" : "map-labels"}.png`, [[0, { x: 1010, y: 1040 }, 1.1], [DUR, { x: 1010, y: 990 }, 1.22]]);
      mk(stage, `inset:0;background:linear-gradient(180deg,rgba(5,8,25,.8) 0 28%,rgba(5,8,25,.5) 58%,rgba(5,8,25,.6))`);
      const kick = chip(stage, cfg.kicker || "TIENDA PIRATA", cfg.kickerColor || YEL, 236);
      const title = S.title(stage, cfg.title, { y: 322, size: cfg.titleSize || 66 });
      const facts = F.map((o, i) => { const el = S.toast(stage, o.label, o.text, top + i * gap, o.color || YEL); el.style.width = "770px"; return { ...o, el }; });
      // a gold token below the captions: the thing everyone is hunting, drawn here (no Epic art)
      const coinText = cfg.coin || ["TIENDA", "PIRATA"];
      const coin = mk(stage, `left:60px;top:1232px;width:190px;height:190px;border-radius:50%;background:radial-gradient(circle at 40% 35%,#FFE98A,#F2B21B 55%,#B06A00);border:10px solid ${INK};box-shadow:0 14px 0 rgba(0,0,0,.4),inset 0 0 0 14px rgba(255,255,255,.28)`,
        `<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:'Barlow Condensed';font-weight:900;color:${INK};text-transform:uppercase;line-height:.95;text-align:center">
           <div style="font-size:${coinText[0].length > 11 ? 24 : 29}px">${coinText[0]}</div><div style="font-size:${coinText[1].length > 8 ? 34 : 46}px">${coinText[1]}</div></div>`, coinText.join(" / ").toUpperCase());
      const dim = mk(stage, `inset:0;background:rgba(5,8,25,.75)`);
      const e = end(stage, VO);
      return function renderAt(t) {
        cam.draw(t);
        // everything clears away when the code card comes in, so nothing peeks out around the taller shop card
        const codeOn = win(t, codeP.t0 - 0.05, DUR + 1, 0.12, 0.1), keep = 1 - codeOn;
        const kx = 1 - E.out(k(t, 0, 0.3)); T(kick, { x: -60 * kx, a: (1 - kx) * keep });
        T(title, { a: keep, s: 0.9 + 0.1 * E.out(k(t, 0, 0.35)) });
        T(coin, { y: Math.sin(t * 1.6) * 6, r: Math.sin(t * 0.9) * 4, s: 0.7 + 0.3 * E.out(k(t, 0.15, 0.6)), a: keep });
        facts.forEach((o) => {
          const t0 = P[o.p].t0 - 0.05, u = 1 - E.out(k(t, t0, t0 + 0.25)), live = t >= t0 && t < P[o.p].t1 + 0.15;
          T(o.el, { a: (t >= t0 ? 1 - u : 0) * keep, x: -70 * u, s: live ? 1.02 : 1 });
        });
        T(dim, { a: codeOn });
        T(e.code, { a: codeOn, s: 0.86 + 0.14 * E.back(k(t, codeP.t0 - 0.05, codeP.t0 + 0.3)) }); signoff(e, t, thanksP, DUR);
        e.caps(t); e.fin(t);
      };
    }
    const cues = [[0.03, "pop"], ...F.map((o) => [P[o.p].t0 - 0.03, "notify"]), [codeP.t0 - 0.05, "pop"]];
    if (thanksP) cues.push([thanksP.t0 - 0.03, "chime"]);
    return { duration: DUR, build, cues, caption: cfg.caption, claims: cfg.claims };
  }

  // ---------------- a shop listing right now: new today, or leaving tonight ----------------
  // voice: [hook, name, (contents), price, when, question, code line]; each info beat swaps the line under the cards
  // cfg: { title, cards: [{name, img, rarity}] (1-3), name, contents?, price: {final, regular?}, when, tone: "new"|"last" }
  function drop(cfg) {
    const { VO, P, DUR, codeI, codeP, thanksP } = timing();
    const askP = P[codeI - 1];
    const hasList = !!cfg.contents;
    const nameP = P[1], listP = hasList ? P[2] : null, priceP = P[hasList ? 3 : 2], whenP = P[hasList ? 4 : 3];
    const C = cfg.cards.slice(0, 3), n = C.length;
    // cards as big as fits: the right one ends left of the app button column (x 940) once it is below y 700
    const lay = n === 1 ? { w: 440, h: 600, xs: [300], y: 360 } : n === 2 ? { w: 360, h: 500, xs: [130, 520], y: 380 } : { w: 270, h: 390, xs: [70, 355, 640], y: 400 };
    const infoY = Math.min(1000, lay.y + lay.h + 80);
    const accent = cfg.tone === "last" ? RED : YEL;
    function build(stage) {
      const bands = wall(stage);
      const title = S.title(stage, cfg.title, { y: 236, size: 64 });
      const cards = C.map((o) => tile(stage, o, lay.w, lay.h));
      // info lines wrap inside x 140-900 and pop in at most 6%, so even mid-pop they stay left of the button column (x 940)
      const line = (html, label) => mk(stage, `left:140px;top:${infoY}px;width:760px;text-align:center`, html, label);
      const nameSize = cfg.name.length <= 18 ? 92 : cfg.name.length <= 26 ? 80 : cfg.name.length <= 36 ? 66 : 56;
      const listSize = cfg.contents && cfg.contents.length <= 40 ? 50 : cfg.contents && cfg.contents.length <= 70 ? 44 : 38;
      const fit = (s, max) => Math.min(max, Math.floor(1300 / Math.max(8, s.length)));
      const name = line(`<div style="font-family:'Barlow Condensed';font-weight:900;font-size:${nameSize}px;line-height:1;text-transform:uppercase;color:#fff;-webkit-text-stroke:10px ${INK};paint-order:stroke fill">${cfg.name}</div>`, cfg.name.toUpperCase());
      const list = hasList ? line(`<div style="font-family:Montserrat;font-weight:900;font-size:${listSize}px;line-height:1.12;color:#fff;-webkit-text-stroke:8px ${INK};paint-order:stroke fill">${cfg.contents}</div>`, cfg.contents.toUpperCase()) : null;
      const num = (n) => (n >= 10000 ? n.toLocaleString("en-US").replace(/,/g, " ") : String(n));
      const priceTxt = `${num(cfg.price.final)} PAVOS`;
      const was = cfg.price.regular && cfg.price.regular > cfg.price.final ? `<span style="font-size:44px;color:#5A5A5A;text-decoration:line-through;margin-left:18px">${num(cfg.price.regular)}</span>` : "";
      const price = mk(stage, `left:210px;top:${infoY}px;width:660px;text-align:center;padding:10px 0 4px;background:${YEL};border:8px solid ${INK};transform:rotate(-4deg);box-shadow:0 10px 0 ${INK}`,
        `<span style="font-family:'Barlow Condensed';font-weight:900;font-size:76px;line-height:1;color:${INK}">${priceTxt}</span>${was}`, priceTxt);
      const when = mk(stage, `left:190px;top:${infoY}px;width:700px;text-align:center;padding:12px 0 6px;background:${accent};border:8px solid ${INK};transform:rotate(3deg);box-shadow:0 10px 0 ${INK}`,
        `<span style="font-family:'Barlow Condensed';font-weight:900;font-size:${fit(cfg.when, 70)}px;line-height:1;color:${cfg.tone === "last" ? "#fff" : INK}">${cfg.when.toUpperCase()}</span>`, cfg.when.toUpperCase());
      const ask = S.title(stage, cfg.ask || ["¿Lo vas a", "comprar?"], { y: 330, size: 96 });
      const e = end(stage, VO);
      const dim = mk(stage, `inset:0;background:rgba(5,8,25,.62)`);
      stage.appendChild(ask); stage.appendChild(e.code); stage.appendChild(e.thanks);
      // each info line shows from its phrase until the next one starts
      const beats = [[name, nameP], [list, listP], [price, priceP], [when, whenP]].filter((b) => b[0]);
      return function renderAt(t) {
        bands.style.transform = `translateX(${(t * 40) % 260}px)`;
        // the top title clears away for the closing question and the code card, so they never sit on top of it
        const endOn = Math.max(win(t, askP.t0 - 0.08, codeP.t0 - 0.05, 0.12, 0.1), win(t, codeP.t0 - 0.05, DUR + 1, 0.12, 0.1));
        T(title, { s: 0.92 + 0.08 * E.out(k(t, 0, 0.3)), a: 1 - endOn });
        cards.forEach((el, i) => {
          const t0 = 0.03 + i * 0.07, mid = (n - 1) / 2;
          T(el, { x: lay.xs[i], y: lay.y + (n === 3 && i === 1 ? -30 : 0) + Math.sin(t * 1.1 + i) * 6, r: (i - mid) * 5 + Math.sin(t * 0.9 + i * 2) * 1, s: E.back(k(t, t0, t0 + 0.3)) });
        });
        beats.forEach(([el, ph], i) => {
          const t0 = ph.t0 - 0.06, t1 = i < beats.length - 1 ? beats[i + 1][1].t0 - 0.06 : askP.t0 - 0.08;
          const on = t >= t0 && t < t1;
          T(el, { a: on ? 1 : 0, s: on ? 1.06 - 0.06 * E.out(k(t, t0, t0 + 0.2)) : 1, r: el === price ? -4 : el === when ? 3 : 0 });
        });
        const askOn = win(t, askP.t0 - 0.08, codeP.t0 - 0.05, 0.12, 0.1), codeOn = win(t, codeP.t0 - 0.05, DUR + 1, 0.12, 0.1);
        T(dim, { a: Math.max(askOn, codeOn) });
        T(ask, { a: askOn, s: 0.9 + 0.1 * E.back(k(t, askP.t0 - 0.08, askP.t0 + 0.25)) });
        T(e.code, { a: codeOn, s: 0.86 + 0.14 * E.back(k(t, codeP.t0 - 0.05, codeP.t0 + 0.3)) }); signoff(e, t, thanksP, DUR);
        e.caps(t); e.fin(t, [priceP.t0 - 0.06, whenP.t0 - 0.06]);
      };
    }
    const cues = [[0.03, "pop"], ...C.map((_, i) => [0.05 + i * 0.07, "flip"]), [nameP.t0 - 0.06, "whoosh"], [priceP.t0 - 0.06, "reveal"], [whenP.t0 - 0.06, cfg.tone === "last" ? "tick" : "marker"],
      [askP.t0 - 0.08, "pop"], [codeP.t0 - 0.05, "notify"]];
    if (thanksP) cues.push([thanksP.t0 - 0.03, "chime"]);
    return { duration: DUR, build, cues, caption: cfg.caption, claims: cfg.claims };
  }

  // ---------------- Top 5 countdown: "#5 … y el #1 es…" ----------------
  // A creator-style countdown: the code sticker top left, a big rank top right with its label, one art card per entry,
  // a "?" box under the card that turns into the answer on its voice line, caption lines and emoji stickers that pop on
  // the voice, a dark "y el #1 es…" beat with the #1 in silhouette, a recap grid, then the creator code typed in.
  //   hook:   {lines: ["Tienda Pirata:", "las fechas ⏰"], tag: "no te las pierdas 🏴‍☠️", tag2: {p, text}}
  //   items:  countdown order (#5 first): {rank, label, img, kind: "skin"|"map", rarity?, p (the "Número cinco" line),
  //           cap: {p, text}, box: {hidden, count?: [{p, dt, text}], reveal: {p, dt?, text}}, extras?: [{p, dt?, text}],
  //           sticker?: {p, dt?, text}, recap: "#5 · 1 nov", flag?: true}
  //   tease:  {p, title: "y el #1 es… 🥁", text: "el mejor 👀"}      recap: {p, title: "¿cuándo usarías el tuyo? 👇"}
  // The voice ends like every template: the code line, then the sign-off.
  function top(cfg) {
    const { VO, P, DUR, codeP, thanksP } = timing();
    const I = cfg.items, n = I.length, last = n - 1;
    const at = (o) => P[o.p].t0 + (o.dt || 0) - 0.06;
    const tIn = I.map((it) => P[it.p].t0 - 0.15);
    const teaseT = cfg.tease ? P[cfg.tease.p].t0 - 0.15 : null;
    const recapT = P[cfg.recap.p].t0 - 0.15;
    const tOut = I.map((_, i) => (i === last ? recapT : i === last - 1 && teaseT != null ? teaseT : tIn[i + 1]));
    const codeT = codeP.t0 - 0.1, thanksT = thanksP ? thanksP.t0 - 0.05 : DUR + 1;
    const typed = [0.85, 1.05, 1.25].map((d) => codeT + d), clickT = codeT + 2.15, okT = clickT + 0.22;
    const CX = 480, CARD = { x: 150, y: 540, w: 660, h: 770 };
    const colors = (it) => it.colors || RAR[it.rarity] || RAR.epic;
    const fit = (text, maxW, base, em = 0.6) => Math.min(base, Math.floor(maxW / (em * [...text].length)));
    // a centred pill: the wrapper is wide and invisible, the pill inside carries the text box for the checks
    function pill(parent, text, top, o = {}) {
      const { size = 44, bg = "#fff", fg = INK, cx = CX, w = 900, weight = 800, shadow = true } = o;
      const wr = mk(parent, `left:${cx - w / 2}px;width:${w}px;top:${top}px;text-align:center;transform-origin:50% 50%`);
      const p = document.createElement("div");
      p.style.cssText = `display:inline-block;padding:${size * 0.1}px ${size * 0.4}px ${size * 0.16}px;border-radius:${size * 0.3}px;background:${bg};color:${fg};font-family:Montserrat;font-weight:${weight};font-size:${size}px;line-height:1.12;white-space:nowrap;${shadow ? `box-shadow:0 ${size * 0.12}px ${size * 0.35}px rgba(0,0,0,.4)` : ""}`;
      p.textContent = text; p.dataset.text = text.toUpperCase(); wr.appendChild(p);
      return wr;
    }
    // white words with a dark edge, for the small caption lines over the card
    function cline(parent, text, top) {
      const wr = mk(parent, `left:${CX - 420}px;width:840px;top:${top}px;text-align:center`);
      const p = document.createElement("div");
      p.style.cssText = `display:inline-block;font-family:Montserrat;font-weight:800;font-size:${fit(text, 640, 35, 0.56)}px;line-height:1.1;color:#fff;-webkit-text-stroke:7px rgba(16,6,30,.92);paint-order:stroke fill;white-space:nowrap`;
      p.textContent = text; p.dataset.text = text.toUpperCase(); wr.appendChild(p);
      return wr;
    }
    function cardEl(parent, it, o = {}) {
      const { w = CARD.w, h = CARD.h, silhouette = false, label = null } = o;
      const [a, b] = colors(it);
      const el = mk(parent, `left:0;top:0;width:${w}px;height:${h}px;border-radius:${w * 0.045}px;overflow:hidden;background:radial-gradient(circle at 50% 36%,${a},${b} 88%);box-shadow:0 ${w * 0.04}px ${w * 0.09}px rgba(0,0,0,.5),inset 0 0 0 ${Math.max(2, w * 0.004)}px rgba(255,255,255,.16);transform-origin:50% 55%`);
      el.innerHTML = it.kind === "map"
        ? `<div style="position:absolute;inset:0;background:linear-gradient(180deg,#5FC9F5,#1C8FD6 30%,#0E6FB8)"></div><img src="${it.img}" style="position:absolute;left:${-w * 0.07}px;top:${(h - w * 1.14) / 2 + h * 0.03}px;width:${w * 1.14}px;height:auto">`
        : `<div style="position:absolute;inset:0;background:linear-gradient(120deg,rgba(255,255,255,.14) 0 22%,transparent 22%)"></div><img src="${it.img}" style="position:absolute;left:50%;top:${h * 0.03}px;height:${h * 1.0}px;width:auto;transform:translateX(-50%);${silhouette ? "filter:brightness(0) drop-shadow(0 0 18px rgba(255,255,255,.18))" : ""}">`;
      el.innerHTML += `<div style="position:absolute;left:0;right:0;bottom:0;height:40%;background:linear-gradient(transparent,rgba(12,3,26,.78))"></div>`;
      if (it.flag && !silhouette) el.innerHTML += `<div style="position:absolute;right:${w * 0.035}px;top:${w * 0.035}px;width:${w * 0.15}px;height:${w * 0.15}px;border-radius:${w * 0.03}px;background:#17171f;display:flex;align-items:center;justify-content:center;font-size:${w * 0.095}px;transform:rotate(9deg);box-shadow:0 6px 14px rgba(0,0,0,.45)">${it.flag === true ? "🏴‍☠️" : it.flag}</div>`;
      if (label) {
        const tag = document.createElement("div");
        tag.style.cssText = `position:absolute;left:0;right:0;bottom:${w * 0.04}px;text-align:center`;
        tag.innerHTML = `<span style="display:inline-block;padding:3px 12px 5px;border-radius:9px;background:rgba(12,10,20,.9);color:#fff;font:800 ${fit(label, w * 0.86, 23, 0.6)}px Montserrat;white-space:nowrap">${label}</span>`;
        tag.firstChild.dataset.text = label.toUpperCase();
        el.appendChild(tag);
      }
      return el;
    }
    // blurred copy of the card art behind everything (small and blurred, then scaled up: cheap and smooth)
    function backdrop(it, dark = 0.34) {
      const [a, b] = colors(it);
      const el = mk(stage0, `inset:0;overflow:hidden;background:radial-gradient(circle at 50% 40%,${a},${b} 90%)`);
      el.innerHTML = `<img src="${it.img}" style="position:absolute;left:${540 - 135}px;top:${960 - 240}px;width:270px;height:480px;object-fit:${it.kind === "map" ? "cover" : "contain"};filter:blur(11px) saturate(1.2);transform:translateY(130px) scale(4.7)">
        <div style="position:absolute;inset:0;background:rgba(14,6,30,${dark})"></div>`;
      return el;
    }
    let stage0;
    function build(stage) {
      stage0 = stage;
      const bgs = I.map((it) => backdrop(it));
      const darkBg = mk(stage, `inset:0;background:radial-gradient(circle at 50% 42%,#2b2b36,#0d0d12 75%)`);
      // ---- hook: title pills, tag, the five cards (top: #2 #1 #3, bottom: #4 #5) ----
      const hook = mk(stage, `inset:0`);
      const hookLines = cfg.hook.lines.map((l, i) => pill(hook, l, 520 + i * 78, { size: fit(l, 740, 52, 0.58) }));   // 740: room for the pop's overshoot
      const tagY = 520 + cfg.hook.lines.length * 78 + 8;
      const tag1 = pill(hook, cfg.hook.tag, tagY, { size: 28, bg: "rgba(12,10,20,.92)", fg: "#fff" });
      const tag2 = cfg.hook.tag2 ? pill(hook, cfg.hook.tag2.text, tagY, { size: 28, bg: "rgba(12,10,20,.92)", fg: "#fff" }) : null;
      const slots = [{ r: 2, x: 62, y: 0 }, { r: 1, x: 340, y: 0 }, { r: 3, x: 618, y: 0 }, { r: 4, x: 201, y: 1 }, { r: 5, x: 479, y: 1 }].filter((s) => s.r <= n);
      const gridAt = (y0, parent, labels) => slots.map((s, j) => ({ ...s, el: cardEl(parent, I[n - s.r], { w: 240, h: 290, label: labels ? I[n - s.r].recap : null }), y: y0 + s.y * 316, j }));
      const hookGrid = gridAt(tagY + 66, hook, false);
      // ---- one group per entry ----
      const G = I.map((it, i) => {
        const g = mk(stage, `inset:0`);
        const card = cardEl(g, it);
        const rank = mk(g, `right:62px;top:232px;text-align:right`);
        rank.innerHTML = `<div style="display:inline-block;font-family:Montserrat;font-weight:900;font-style:italic;font-size:178px;line-height:1;letter-spacing:-4px;color:#fff;-webkit-text-stroke:15px ${INK};paint-order:stroke fill;text-shadow:0 10px 0 rgba(0,0,0,.45);padding-right:10px">#${it.rank}</div>`;
        rank.firstChild.dataset.text = `#${it.rank}`;
        const lab = mk(g, `right:62px;top:432px;text-align:right`);
        const ls = fit(it.label, 600, 44, 0.6);
        lab.innerHTML = `<div style="display:inline-block;padding:${ls * 0.08}px ${ls * 0.36}px ${ls * 0.16}px;border-radius:${ls * 0.26}px;background:#fff;color:${INK};font:800 ${ls}px Montserrat;line-height:1.12;white-space:nowrap;box-shadow:0 6px 16px rgba(0,0,0,.35)">${it.label}</div>`;
        lab.firstChild.dataset.text = it.label.toUpperCase();
        const cap = it.cap ? cline(g, it.cap.text, 1222) : null;
        const extras = (it.extras || []).map((x) => ({ ...x, el: cline(g, x.text, 1180) }));
        const boxSize = (s) => fit(s, 560, 78, 0.62);
        const boxes = [{ t: it.cap ? at(it.cap) : tIn[i] + 0.3, text: it.box.hidden }, ...(it.box.count || []).map((c) => ({ t: at(c), text: c.text })), { t: at(it.box.reveal), text: it.box.reveal.text, final: true }]
          .map((b) => ({ ...b, el: pill(g, b.text, 1262, { size: boxSize(b.text), weight: 900 }) }));
        const stk = it.sticker ? pill(g, it.sticker.text, 1068, { size: fit(it.sticker.text, 640, 40, 0.58), bg: "rgba(255,255,255,.95)" }) : null;
        return { g, card, rank, lab, cap, extras, boxes, stk };
      });
      // ---- "y el #1 es…" ----
      const tz = cfg.tease ? (() => {
        const g = mk(stage, `inset:0`);
        const card = cardEl(g, I[last], { silhouette: true });
        const title = mk(g, `right:62px;top:300px;text-align:right`);
        title.innerHTML = `<div style="display:inline-block;font-family:Montserrat;font-weight:900;font-size:${fit(cfg.tease.title, 620, 66, 0.6)}px;line-height:1;color:#fff;-webkit-text-stroke:9px ${INK};paint-order:stroke fill;text-shadow:0 6px 0 rgba(0,0,0,.45);white-space:nowrap">${cfg.tease.title}</div>`;
        title.firstChild.dataset.text = cfg.tease.title.toUpperCase();
        const cap = pill(g, cfg.tease.text, 1100, { size: 34, bg: "rgba(12,10,20,.9)", fg: "#fff" });
        return { g, card, title, cap };
      })() : null;
      // ---- recap ----
      const rc = mk(stage, `inset:0`);
      const rcTitle = pill(rc, cfg.recap.title, 540, { size: fit(cfg.recap.title, 740, 46, 0.58) });
      const rcGrid = gridAt(640, rc, true);
      const rcCode = pill(rc, "usa el código BAD en la tienda de objetos 🛒", 1290, { size: 34, bg: YEL, weight: 900 });
      // ---- the creator code, typed in ----
      const dim = mk(stage, `inset:0;background:rgba(7,6,14,.8)`);
      const panel = mk(stage, `left:70px;top:700px;width:820px;padding:26px 30px 28px;border-radius:14px;background:linear-gradient(180deg,#1c1c26,#121219);border:3px solid rgba(255,255,255,.12);box-shadow:0 20px 50px rgba(0,0,0,.6)`);
      panel.innerHTML = `<div style="font-family:'Barlow Condensed';font-weight:900;font-style:italic;font-size:78px;line-height:1;color:#fff;letter-spacing:1px">APOYA A UN CREADOR</div>
        <div style="height:5px;margin:14px 0 22px;background:${YEL};border-radius:3px"></div>
        <div style="height:74px;border-radius:6px;background:#fff;border:3px solid ${INK};padding:6px 16px;font:900 46px Montserrat;color:${INK};line-height:58px"><span class="typed"></span><span class="caret" style="display:inline-block;width:4px;height:44px;margin-left:3px;vertical-align:-6px;background:${INK}"></span></div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:22px">
          <div class="disc" style="font:800 34px Montserrat;color:#fff">#EpicPartner</div>
          <div class="ok" style="padding:12px 34px;border-radius:10px;background:${YEL};font:900 36px Montserrat;color:${INK};box-shadow:0 6px 0 #B58F00">Aceptar</div></div>`;
      panel.querySelector(".ok").dataset.text = "ACEPTAR";
      panel.firstElementChild.dataset.text = "APOYA A UN CREADOR";
      panel.querySelector(".disc").dataset.text = "#EPICPARTNER";
      const typedEl = panel.querySelector(".typed"), caret = panel.querySelector(".caret"), okBtn = panel.querySelector(".ok");
      const cursor = mk(stage, `left:0;top:0;width:52px;height:64px`, `<svg width="52" height="64" viewBox="0 0 26 32"><path d="M2 2 L2 26 L8.5 20 L13 30 L17 28 L12.6 18.4 L21 18.4 Z" fill="#fff" stroke="${INK}" stroke-width="2.2" stroke-linejoin="round"/></svg>`);
      const done = pill(stage, "✓ APOYANDO A: BAD", 812, { size: 46, bg: YEL, weight: 900 });
      const doneSub = pill(stage, "#EpicPartner", 900, { size: 30, bg: "rgba(12,10,20,.85)", fg: "#fff" });
      const thanks = thanksES(stage, 1010);
      const logo = mk(stage, `left:${CX - 190}px;top:1166px;width:380px`, `<img src="${BR}/watermark.svg" style="display:block;width:380px;height:auto">`, "LA TIENDITA BAD");
      // ---- always on: the code sticker under a little shop awning, and the disclosure ----
      const stick = mk(stage, `left:56px;top:226px;width:300px`);
      stick.innerHTML = `<img src="${BR}/awning.svg" style="position:relative;z-index:2;display:block;width:300px;height:auto;transform:rotate(-4deg);transform-origin:0 100%">
        <div style="position:relative;z-index:1;margin:-14px 0 0 12px;width:272px;padding:12px 0 8px;text-align:center;background:${YEL};border:5px solid ${INK};border-top:0;border-radius:0 0 16px 16px;transform:rotate(-4deg);transform-origin:0 0;box-shadow:0 8px 0 ${INK}">
          <div style="font-family:Montserrat;font-weight:900;font-size:27px;line-height:1;color:${INK}">USA EL CÓDIGO:</div>
          <div style="font-family:Montserrat;font-weight:900;font-size:100px;line-height:.9;letter-spacing:-3px;color:${INK}">BAD</div></div>`;
      stick.dataset.text = "USA EL CÓDIGO: BAD";
      const disc = mk(stage, `left:66px;top:448px`, `<span style="font:800 25px Montserrat;color:#fff;-webkit-text-stroke:5px rgba(10,6,20,.85);paint-order:stroke fill">#EpicPartner</span>`, "#EPICPARTNER");
      const fin = S.finish(stage);
      const vis = (el, a) => { el.style.opacity = a; el.style.visibility = a <= 0.001 ? "hidden" : "visible"; };
      return function renderAt(t) {
        // backgrounds: the hook, the recap and the code use the #1's art; the tease is dark
        const which = t < tIn[0] ? last : t >= recapT ? last : teaseT != null && t >= teaseT && t < tIn[last] ? -1 : I.findIndex((_, i) => t >= tIn[i] && t < tOut[i]);
        bgs.forEach((b, i) => vis(b, i === which ? 1 : 0));
        vis(darkBg, which === -1 ? 1 : 0);
        // hook
        const hookA = t < tIn[0] ? 1 : 1 - k(t, tIn[0], tIn[0] + 0.18);
        vis(hook, hookA);
        hookLines.forEach((h, i) => T(h, { r: i % 2 ? 1.5 : -2, s: E.back(k(t, 0.02 + i * 0.08, 0.3 + i * 0.08)) }));
        const swap = tag2 ? at(cfg.hook.tag2) : 1e9;
        T(tag1, { a: t < swap ? 1 : 0, s: E.back(k(t, 0.2, 0.45)) });
        if (tag2) T(tag2, { a: t >= swap ? 1 : 0, s: E.back(k(t, swap, swap + 0.25)) });
        hookGrid.forEach((c) => T(c.el, { x: c.x, y: c.y + Math.sin(t * 1.3 + c.j) * 5, r: (c.j % 2 ? 3 : -3) + Math.sin(t + c.j * 2) * 1, s: E.back(k(t, 0.12 + c.j * 0.08, 0.42 + c.j * 0.08)) }));
        // entries
        G.forEach((e, i) => {
          const a = tIn[i], b = tOut[i], on = t >= a - 0.02 && t < b + 0.3;
          vis(e.g, on ? 1 : 0);
          if (!on) return;
          const flashIn = i === last && teaseT != null;           // the #1 appears in place of its silhouette
          const pin = flashIn ? 1 : E.out5(k(t, a, a + 0.32)), pout = E.in(k(t, b - 0.04, b + 0.24));
          const x = (1 - pin) * 820 - pout * 900, speed = Math.abs((1 - pin) * 820) > 2 || pout > 0.01;
          T(e.card, { x: CARD.x + x, y: CARD.y + Math.sin(t * 1.1) * 6, r: (1 - pin) * 7 - pout * 7 + Math.sin(t * 0.8) * 0.8, s: flashIn ? 0.92 + 0.08 * E.back(k(t, a, a + 0.35)) : 1 });
          e.card.style.filter = speed ? `blur(${Math.min(6, 2 + (1 - pin + pout) * 6).toFixed(1)}px)` : "none";
          const txtA = t < b - 0.05 ? 1 : 0;
          T(e.rank, { a: txtA * (t >= a + 0.06 ? 1 : 0), s: 0.7 + 0.3 * E.back(k(t, a + 0.06, a + 0.36)) });
          T(e.lab, { a: txtA * (t >= a + 0.16 ? 1 : 0), s: 0.8 + 0.2 * E.back(k(t, a + 0.16, a + 0.42)) });
          if (e.cap) T(e.cap, { a: txtA * (t >= at(I[i].cap) ? 1 : 0), s: 0.85 + 0.15 * E.out(k(t, at(I[i].cap), at(I[i].cap) + 0.2)) });
          const stkT = e.stk ? at(I[i].sticker) : 1e9;
          e.extras.forEach((x, j) => { const t0 = at(x), t1 = j < e.extras.length - 1 ? at(e.extras[j + 1]) : stkT; T(x.el, { a: txtA * (t >= t0 && t < t1 ? 1 : 0), s: 0.85 + 0.15 * E.out(k(t, t0, t0 + 0.2)) }); });
          e.boxes.forEach((bx, j) => { const nx = e.boxes[j + 1]; const show = t >= bx.t && (!nx || t < nx.t); T(bx.el, { a: txtA * (show ? 1 : 0), s: bx.final ? 1.28 - 0.28 * E.back(k(t, bx.t, bx.t + 0.3)) : 1 + 0.1 * (1 - E.out(k(t, bx.t, bx.t + 0.15))) }); });
          if (e.stk) T(e.stk, { a: txtA * (t >= stkT ? 1 : 0), r: -4, s: 0.6 + 0.4 * E.back(k(t, stkT, stkT + 0.3)) });
        });
        // tease
        if (tz) {
          const on = t >= teaseT && t < tIn[last];
          vis(tz.g, on ? 1 : 0);
          if (on) {
            const pin = E.out5(k(t, teaseT, teaseT + 0.32));
            T(tz.card, { x: CARD.x + (1 - pin) * 820, y: CARD.y + Math.sin(t * 1.1) * 6, r: (1 - pin) * 7 });
            tz.card.style.filter = pin < 0.995 ? `blur(${(2 + (1 - pin) * 6).toFixed(1)}px)` : "none";
            T(tz.title, { a: t >= teaseT + 0.1 ? 1 : 0, s: 0.8 + 0.2 * E.back(k(t, teaseT + 0.1, teaseT + 0.4)) });
            T(tz.cap, { a: t >= teaseT + 0.5 ? 1 : 0, s: 0.8 + 0.2 * E.back(k(t, teaseT + 0.5, teaseT + 0.75)) });
          }
        }
        // recap
        const r0 = recapT + 0.2, rcA = win(t, r0, codeT + 0.35, 0.15, 0.2);
        vis(rc, rcA);
        if (rcA > 0) {
          T(rcTitle, { r: -2, s: 0.85 + 0.15 * E.back(k(t, r0, r0 + 0.3)) });
          rcGrid.forEach((c) => T(c.el, { x: c.x, y: c.y + Math.sin(t * 1.3 + c.j) * 5, r: (c.j % 2 ? 3 : -3), s: E.back(k(t, r0 + 0.1 + c.j * 0.08, r0 + 0.4 + c.j * 0.08)) }));
          T(rcCode, { a: t >= codeT - 0.6 ? 1 : 0, s: 0.8 + 0.2 * E.back(k(t, codeT - 0.6, codeT - 0.3)) });
        }
        // creator code: typed, accepted, confirmed
        const cA = t >= codeT + 0.3 ? 1 : 0;
        vis(dim, cA * 0.95);
        const pA = cA * (t < okT ? 1 : 1 - k(t, okT, okT + 0.15));
        T(panel, { a: pA, s: 0.9 + 0.1 * E.back(k(t, codeT + 0.3, codeT + 0.6)) });
        typedEl.textContent = "BAD".slice(0, typed.filter((x) => t >= x).length);
        caret.style.opacity = Math.floor(t * 2.4) % 2 ? 0.15 : 1;
        T(okBtn, { a: t >= typed[2] + 0.15 ? 1 : 0, s: t >= clickT && t < clickT + 0.12 ? 0.92 : 1 });
        const cx0 = 980, cy0 = 1240, cx1 = 790, cy1 = 1000, cp = E.inOut(k(t, typed[2] + 0.3, clickT - 0.05));
        T(cursor, { a: pA * (t >= typed[2] + 0.25 ? 1 : 0), x: lerp(cx0, cx1, cp), y: lerp(cy0, cy1, cp), s: t >= clickT && t < clickT + 0.12 ? 0.9 : 1 });
        T(done, { a: t >= okT ? 1 : 0, s: 0.75 + 0.25 * E.back(k(t, okT, okT + 0.3)) });
        T(doneSub, { a: t >= okT + 0.15 ? 1 : 0, s: 0.8 + 0.2 * E.back(k(t, okT + 0.15, okT + 0.4)) });
        T(thanks, { a: t >= thanksT ? 1 : 0, s: 0.9 + 0.1 * E.back(k(t, thanksT, thanksT + 0.3)) });
        T(logo, { a: t >= okT + 0.3 ? 1 : 0, s: 0.85 + 0.15 * E.back(k(t, okT + 0.3, okT + 0.6)) });
        T(stick, { s: 0.6 + 0.4 * E.back(k(t, 0, 0.3)) });
        T(disc, { a: 1 });
        fin(t, [tIn[last]].filter(() => teaseT != null));
      };
    }
    const cues = [[0.03, "pop"], ...[0.12, 0.2, 0.28, 0.36, 0.44].slice(0, n).map((x) => [x, "flip"])];
    I.forEach((it, i) => {
      cues.push([tIn[i] + 0.02, "whoosh"], [tIn[i] + 0.08, "pop"]);
      (it.box.count || []).forEach((c) => cues.push([at(c), "tick"]));
      cues.push([at(it.box.reveal), "reveal"]);
      if (it.sticker) cues.push([at(it.sticker), "pop"]);
    });
    if (teaseT != null) cues.push([teaseT + 0.02, "sting"]);
    cues.push([recapT + 0.02, "whoosh"]);
    typed.forEach((x) => cues.push([x, "tick"]));
    cues.push([clickT, "pop"], [okT, "notify"]);
    if (thanksP) cues.push([thanksT, "chime"]);
    return { duration: DUR, build, cues, caption: cfg.caption, claims: cfg.claims };
  }

  root.TPL = { guess, map, news, drop, guide, top, mapPx };
})(window);
