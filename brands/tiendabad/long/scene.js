/* LA TIENDITA BAD · long YouTube video (1920x1080), Spanish port of BAD ROYALE's. Data: window.LONG (the episode) and window.VO (voice timings).
 * window.LONG = { title, caption, claims, segments: [...] }; each segment starts with phrase p and runs until the next:
 *   {type:"title", p, kicker, lines:[a, b]}                 opening card over the whole map
 *   {type:"map", p, x, y, z, name, sub, pins:[{x, y, label}]} camera flies to a spot (map pixels), lower-third name card
 *   {type:"quote", p, kicker, title, lines:[{p, text}]}       an in-game news tile, its lines lit as they are read
 *   {type:"items", p, title, items:[{img, name, rarity, price}]} a row of item tiles
 *   {type:"stat", p, big, label}                              one big number
 *   {type:"code", p}                                          the code card (mid-roll and outro)
 * L.map: "blank" uses the map without labels (a season whose label image is too dense), anything else the labeled map.
 * A chapter bar (top left) shows the current segment's "chapter" if it has one.
 */
(function () {
  "use strict";
  const { k, lerp, E, win, mk, T } = window.FX;
  const S = window.STYLE2;
  const L = window.LONG, VO = window.VO, P = VO.phrases;
  const INK = "#0B0D1A", YEL = "#FFD21E", BLUE = "#1A47C8";
  const BR = "../../tiendabad/brand";
  const DUR = Math.round((P[P.length - 1].t1 + 1.2) * 10) / 10;
  const SEG = L.segments.map((s, i, a) => ({ ...s, t0: Math.max(0, P[s.p].t0 - 0.15), t1: i < a.length - 1 ? Math.max(0, P[a[i + 1].p].t0 - 0.15) : DUR + 1 }));
  const RAR = { common: ["#C2C2C2", "#5E5E5E"], uncommon: ["#8BE33B", "#1C6B10"], rare: ["#5BD0FF", "#1A5BB5"], epic: ["#D27BFF", "#5B1F9C"], legendary: ["#F7AE52", "#8A3B12"],
    gaminglegends: ["#7B6BFF", "#1E1557"], icon: ["#5CF2F3", "#0F6B7A"], marvel: ["#F0473C", "#5A0F0B"], dc: ["#5A84FF", "#14205E"], dark: ["#FF4FD8", "#3A0A4F"] };
  const WIDE = { x: 1010, y: 1010, z: 0.5 };

  // camera keyframes: every map segment flies to its spot; everything else holds the last spot, wider
  const kf = [[0, WIDE, 0.48]];
  let last = WIDE;
  SEG.forEach((s) => {
    if (s.type === "map") { kf.push([s.t0, last, last.z, "inOut"], [s.t0 + 1.1, s, s.z, "out5"]); last = s; }
    else if (s.type === "title") { kf.push([s.t0, last, last.z], [s.t0 + 1.2, WIDE, 0.5, "inOut"]); last = WIDE; }
  });
  kf.push([DUR, last, last.z * 1.06]);
  kf.sort((a, b) => a[0] - b[0]);
  for (let i = 1; i < kf.length; i++) if (kf[i][0] <= kf[i - 1][0]) kf[i][0] = kf[i - 1][0] + 0.01;

  const big = (text, size, color = "#fff") => `<div style="font-family:Montserrat;font-weight:900;font-size:${size}px;line-height:1.02;letter-spacing:-1px;text-transform:uppercase;color:${color};-webkit-text-stroke:${size * 0.12}px ${INK};paint-order:stroke fill;text-shadow:0 ${size * 0.07}px 0 rgba(0,0,0,.45)">${text}</div>`;
  function tile(parent, o, w, h) {
    const [a, b] = RAR[o.rarity] || RAR.epic;
    return mk(parent, `left:0;top:0;width:${w}px;height:${h}px;border-radius:${w * 0.05}px;overflow:hidden;background:radial-gradient(circle at 50% 30%,${a},${b} 80%);border:6px solid ${INK};box-shadow:0 10px 0 rgba(0,0,0,.45)`,
      `<img src="${o.img}" style="position:absolute;left:${-w * 0.2}px;top:0;width:${w * 1.4}px;height:auto">
       <div style="position:absolute;left:0;right:0;bottom:0;padding:${w * 0.05}px ${w * 0.06}px;background:linear-gradient(transparent,rgba(5,8,22,.92) 30%)">
         <div style="font-family:'Barlow Condensed';font-weight:900;font-size:${Math.min(w * 0.14, (w * 0.86) / (o.name.length * 0.5))}px;line-height:.95;text-transform:uppercase;color:#fff;white-space:nowrap">${o.name}</div>
         ${o.price ? `<div style="font-family:'Barlow Condensed';font-weight:900;font-size:${w * 0.12}px;color:${YEL}">${o.price >= 10000 ? o.price.toLocaleString("en-US").replace(/,/g, " ") : o.price} pavos</div>` : ""}</div>`, o.name.toUpperCase());
  }

  function build(stage) {
    mk(stage, `inset:0;background:#084687`);
    const cam = S.mapCam(stage, `../../tiendabad/sources/map-${L.map === "blank" ? "blank" : "labels"}.png`, kf, { x: 960, y: 560 });
    const dim = mk(stage, `inset:0;background:rgba(5,10,28,.78)`);
    const els = SEG.map((s, i) => {
      const g = mk(stage, `inset:0`);
      const e = { s, g };
      if (s.type === "title") {
        e.card = mk(g, `left:160px;width:1600px;top:300px;text-align:center`, (s.kicker ? `<div style="display:inline-block;margin-bottom:24px;padding:8px 26px 4px;background:${YEL};border:6px solid ${INK};border-radius:12px;transform:skewX(-8deg);font-family:'Barlow Condensed';font-weight:900;font-size:56px;color:${INK}">${s.kicker}</div>` : "") +
          s.lines.map((l, j) => big(l, j ? 84 : 112, j ? YEL : "#fff")).join(""), [s.kicker || "", ...s.lines].join(" / ").toUpperCase());
      } else if (s.type === "map") {
        e.pins = (s.pins || [{ x: s.x, y: s.y, label: "" }]).map((p, j) => ({ ...p, ring: S.marker(g, 64, 64, "#FF2D2D", i * 1.3 + j), lab: p.label ? S.scrawl(g, p.label, 60, YEL, -6) : null }));
        e.card = mk(g, `left:80px;top:760px;max-width:1100px;padding:18px 34px 16px;border-radius:14px;background:linear-gradient(90deg,rgba(9,22,74,.96),rgba(16,52,160,.9));border:5px solid ${INK};box-shadow:0 10px 0 rgba(0,0,0,.35)`,
          `<div style="font-family:Montserrat;font-weight:900;font-size:58px;line-height:1.05;color:#fff;text-transform:uppercase">${s.name}</div>${s.sub ? `<div style="font:700 32px Montserrat;color:${YEL};margin-top:6px">${s.sub}</div>` : ""}`, `${s.name} / ${s.sub || ""}`.toUpperCase());
      } else if (s.type === "quote") {
        e.card = mk(g, `left:260px;width:1400px;top:150px;border-radius:18px;overflow:hidden;border:6px solid ${INK};box-shadow:0 14px 0 rgba(0,0,0,.4);background:linear-gradient(180deg,#0E1B5C,#0A1240)`,
          `<div style="padding:14px 30px;background:${YEL};border-bottom:6px solid ${INK};font-family:'Barlow Condensed';font-weight:900;font-size:44px;color:${INK}">${s.kicker || "NOTICIAS DEL JUEGO"}</div>
           <div style="padding:26px 40px 36px"><div style="font-family:Montserrat;font-weight:900;font-size:64px;line-height:1.02;text-transform:uppercase;color:#fff">${s.title}</div><div class="q"></div></div>`, `${s.kicker || "NOTICIAS DEL JUEGO"} / ${s.title}`.toUpperCase());
        const q = e.card.querySelector(".q");
        e.lines = s.lines.map((l) => { const d = document.createElement("div"); d.style.cssText = `font:700 42px/1.3 Montserrat;color:#C9D6FF;margin-top:16px;opacity:0`; d.textContent = `“${l.text}”`; d.dataset.text = l.text.toUpperCase(); q.appendChild(d); return { ...l, d }; });
      } else if (s.type === "items") {
        const n = s.items.length, w = Math.min(330, Math.floor(1600 / n) - 30), h = Math.round(w * 1.4);
        e.head = mk(g, `left:200px;right:200px;top:150px;text-align:center`, big(s.title, 54), s.title.toUpperCase());
        e.tiles = s.items.map((o, j) => { const el = tile(g, o, w, h); el.style.left = `${960 - (n * (w + 30) - 30) / 2 + j * (w + 30)}px`; el.style.top = `${300 + (j % 2) * 24}px`; return el; });
      } else if (s.type === "stat") {
        e.card = mk(g, `left:160px;right:160px;top:250px;text-align:center`, big(s.big, 300, YEL) + `<div style="margin-top:10px">${big(s.label, 70)}</div>`, `${s.big} / ${s.label}`.toUpperCase());
      } else if (s.type === "code") {
        e.card = mk(g, `left:590px;width:740px;top:90px;text-align:center`,
          `<img src="${BR}/awning.svg" style="position:relative;z-index:2;display:block;width:740px;height:auto">
           <div style="position:relative;z-index:1;margin:-32px 32px 0;padding:48px 0 24px;border-radius:0 0 24px 24px;background:linear-gradient(180deg,#36D3E2,#13B5C6 55%,#0B8796);border:6px solid ${INK};border-top:0;box-shadow:0 12px 0 rgba(0,0,0,.4)">
             <div style="display:inline-block;background:${YEL};border:4px solid ${INK};border-radius:8px;transform:skewX(-7deg);padding:0 16px;font-family:'Barlow Condensed';font-weight:900;font-size:40px;color:${INK}">CÓDIGO</div>
             <img src="${BR}/bad.svg" style="display:block;margin:8px auto 0;height:176px">
             <div style="font:800 31px Montserrat;color:#fff;margin-top:12px;-webkit-text-stroke:7px ${INK};paint-order:stroke fill">Tienda de objetos → Apoya a un creador</div>
             <div style="display:inline-block;margin-top:12px;padding:6px 18px;border-radius:30px;background:${INK};font:800 26px Montserrat;color:${YEL}">Dura 14 días · ¡renuévalo!</div>
             <div style="font:800 26px Montserrat;color:#fff;margin-top:8px;-webkit-text-stroke:6px ${INK};paint-order:stroke fill">#EpicPartner</div>
           </div>`, "CÓDIGO / BAD / TIENDA DE OBJETOS → APOYA A UN CREADOR / DURA 14 DÍAS · ¡RENUÉVALO! / #EPICPARTNER");
        e.thanks = mk(g, `left:590px;width:740px;top:740px;text-align:center;opacity:0`,
          `<div style="display:inline-block;padding:8px 26px 4px;background:${YEL};border:5px solid ${INK};border-radius:10px;transform:skewX(-7deg);box-shadow:0 6px 0 ${INK};font-family:'Barlow Condensed';font-weight:900;font-size:48px;line-height:1;color:${INK}">¡GRACIAS POR VER!</div>
           <div style="font:800 30px Montserrat;color:#fff;margin-top:12px;-webkit-text-stroke:5px ${INK};paint-order:stroke fill">Síguenos para más contenido diario</div>`, "¡GRACIAS POR VER! / SÍGUENOS PARA MÁS CONTENIDO DIARIO");
      }
      if (s.chapter) e.chap = mk(g, `left:60px;top:60px;padding:8px 22px 4px;background:${YEL};border:5px solid ${INK};border-radius:10px;transform:skewX(-8deg)`,
        `<span style="font-family:'Barlow Condensed';font-weight:900;font-size:44px;color:${INK};text-transform:uppercase">${s.chapter}</span>`, s.chapter.toUpperCase());
      return e;
    });
    // one-line subtitles, bottom centre
    mk(stage, `left:0;right:0;top:880px;height:200px;background:linear-gradient(transparent,rgba(5,8,22,.78) 45%)`);
    const sub = mk(stage, `left:160px;width:1600px;top:950px;text-align:center`);
    const chunks = []; let cur = [];
    VO.words.forEach((w, i) => { cur.push(w); const nx = VO.words[i + 1]; if (!nx || nx.p !== w.p || /[.,?!:]$/.test(w.w) || cur.length >= 6) { chunks.push(cur); cur = []; } });
    const subs = chunks.map((c, i) => { const el = mk(sub, `left:0;right:0;top:0;text-align:center;font-family:Montserrat;font-weight:900;font-size:50px;color:#fff;-webkit-text-stroke:9px ${INK};paint-order:stroke fill;text-transform:uppercase`, c.map((w) => w.w).join(" "), c.map((w) => w.w).join(" ").toUpperCase());
      return { el, t0: c[0].t0 - 0.03, t1: chunks[i + 1] ? chunks[i + 1][0].t0 - 0.03 : c[c.length - 1].t1 + 0.4 }; });
    mk(stage, `left:1600px;top:40px;width:240px`, `<img src="${BR}/watermark.svg" style="display:block;width:240px;height:auto">`, "LA TIENDITA BAD");
    const disc = S.disclosure(stage); disc.style.left = "60px"; disc.style.top = "150px";
    const fin = S.finish(stage);

    return function renderAt(t) {
      const { toScreen } = cam.draw(t);
      let dimA = 0;
      els.forEach((e) => {
        const { s } = e, on = win(t, s.t0, s.t1, 0.25, 0.2);
        T(e.g, { a: on });
        if (on <= 0) return;
        if (s.type === "title") { dimA = Math.max(dimA, 0.45 * on); T(e.card, { s: 0.9 + 0.1 * E.back(k(t, s.t0, s.t0 + 0.5)) }); }
        if (s.type === "map") {
          e.pins.forEach((p, j) => { const f = toScreen(p), t0 = s.t0 + 1.0 + j * 0.5; p.ring.place(f.x, f.y, t, t0, t >= t0 ? 1 : 0, 1); if (p.lab) p.lab.place(f.x + 50, f.y + 60, t, t0 + 0.15, t >= t0 + 0.15 ? 1 : 0); });
          { const x = (1 - E.out5(k(t, s.t0 + 0.6, s.t0 + 1.0))) * -900; T(e.card, { x, a: Math.max(0, 1 - Math.abs(x) / 20) }); }   // only visible once inside the title-safe area
        }
        if (s.type === "quote") { dimA = Math.max(dimA, on); T(e.card, { s: 0.94 + 0.06 * E.back(k(t, s.t0, s.t0 + 0.4)) });
          e.lines.forEach((l) => { const lit = t >= P[l.p].t0 - 0.05; l.d.style.opacity = lit ? 1 : 0.32; l.d.style.color = lit && t < P[l.p].t1 + 0.15 ? "#fff" : "#C9D6FF"; }); }
        if (s.type === "items") { dimA = Math.max(dimA, on); e.tiles.forEach((el, j) => T(el, { s: E.back(k(t, s.t0 + j * 0.08, s.t0 + 0.35 + j * 0.08)), r: Math.sin(t * 0.9 + j) * 1.2 })); }
        if (s.type === "stat") { dimA = Math.max(dimA, on); T(e.card, { s: 0.8 + 0.2 * E.back(k(t, s.t0, s.t0 + 0.4)) }); }
        if (s.type === "code") { dimA = Math.max(dimA, on); T(e.card, { s: 0.86 + 0.14 * E.back(k(t, s.t0, s.t0 + 0.4)) });
          const thP = P[P.length - 1]; T(e.thanks, { a: /gracias/i.test(thP.text) ? k(t, thP.t0 - 0.05, thP.t0 + 0.2) : 0 }); }
      });
      T(dim, { a: dimA * 0.78 });
      subs.forEach((x) => { x.el.style.visibility = t >= x.t0 && t < x.t1 ? "visible" : "hidden"; });
      fin(t);
    };
  }

  const cues = [[0.05, "sting"]];
  SEG.forEach((s) => cues.push([s.t0, { map: "whoosh", title: "sting", quote: "notify", items: "flip", stat: "pop", code: "pop" }[s.type] || "tick"]));
  window.SCENE = { duration: DUR, build, cues, caption: L.caption, claims: L.claims, size: [1920, 1080] };
})();
