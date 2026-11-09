/* LA TIENDITA BAD · tienda de objetos diaria. Spanish twin of BAD ROYALE's shop short.
 * Data: window.SHOP (shop_pick.py, es-419), voice timings: window.VO. Beats follow the voice by role:
 * hook, mejor oferta (+ how much you save), regreso OG (+ days since last seen), último día, código.
 */
(function () {
  "use strict";
  const { k, lerp, E, win, mk, T, svg } = window.FX;
  const S = window.STYLE2;
  const D = window.SHOP, VO = window.VO, P = VO.phrases;
  const R = (role) => { const i = D.roles.indexOf(role); return i >= 0 ? P[i] : null; };
  const RI = (role) => D.roles.indexOf(role);
  const IMG = (id) => `../../tiendabad/shop/${D.date}/img/${id}.png`;
  const INK = "#0B0D1A", YEL = "#FFD21E", RED = "#FF4A3D", BLUE = "#0E2F9E";
  const DUR = Math.round((P[P.length - 1].t1 + 0.75) * 10) / 10;
  const b1 = R("deal_name").t0 - 0.12, b2 = R("og_name").t0 - 0.12, b3 = R("last") ? R("last").t0 - 0.12 : R("ask").t0 - 0.1, b4 = R("ask").t0 - 0.1;
  const RAR = { common: ["#C2C2C2", "#5E5E5E"], uncommon: ["#8BE33B", "#1C6B10"], rare: ["#5BD0FF", "#1A5BB5"], epic: ["#D27BFF", "#5B1F9C"], legendary: ["#F7AE52", "#8A3B12"],
    gaminglegends: ["#7B6BFF", "#1E1557"], icon: ["#5CF2F3", "#0F6B7A"], marvel: ["#F0473C", "#5A0F0B"], dc: ["#5A84FF", "#14205E"], starwars: ["#3D3D3D", "#0A0A0A"], slurp: ["#3FE3D2", "#0B5E7A"], dark: ["#FF4FD8", "#3A0A4F"], frozen: ["#C9E9FF", "#4C7FB0"], lava: ["#FF8A2B", "#6E1A06"], shadow: ["#6E6E6E", "#141414"] };
  const rar = (o) => RAR[o.rarityKey] || RAR.epic;
  const vb = (size) => `<img src="../../tiendabad/shop/${D.date}/img/vbuck.png" style="display:inline-block;width:${size}px;height:${size}px;vertical-align:-${size * 0.14}px">`;
  // Spanish number style: no separator below 10 000, a thin space above (the voice script uses the same)
  const num = (n) => (n >= 10000 ? n.toLocaleString("en-US").replace(/,/g, " ") : String(n));
  const word = (role, re) => { const i = RI(role); const w = VO.words.find((x) => x.p === i && re.test(x.w)); return w ? w.t0 : (R(role) ? R(role).t0 + 0.4 : 1e9); };

  // a shop tile: rarity gradient, featured art, name bar, optional price (same tile as BAD ROYALE)
  function tile(o, w, h, price = true) {
    const [a, b] = rar(o);
    const el = document.createElement("div");
    el.style.cssText = `position:absolute;left:0;top:0;width:${w}px;height:${h}px;border-radius:${w * 0.05}px;overflow:hidden;background:radial-gradient(circle at 50% 30%,${a},${b} 80%);border:${Math.max(4, w * 0.018)}px solid ${INK};box-shadow:0 ${w * 0.03}px 0 rgba(0,0,0,.45);transform-origin:50% 60%`;
    el.innerHTML = `<div style="position:absolute;inset:0;background:linear-gradient(120deg,rgba(255,255,255,.18) 0 18%,transparent 18%)"></div>
      <img src="${IMG(o.id)}" style="position:absolute;left:${-w * 0.2}px;top:${-h * 0.02}px;width:${w * 1.4}px;height:auto">
      <div style="position:absolute;left:0;right:0;bottom:0;padding:${w * 0.05}px ${w * 0.06}px ${w * 0.045}px;background:linear-gradient(transparent,rgba(5,8,22,.92) 30%)">
        <div style="font-family:'Barlow Condensed';font-weight:900;font-size:${Math.min(w * 0.13, (w * 0.86) / (o.name.length * 0.43))}px;line-height:.95;text-transform:uppercase;color:#fff;-webkit-text-stroke:${w * 0.012}px ${INK};paint-order:stroke fill;white-space:nowrap">${o.name}</div>
        ${price && o.price ? `<div style="margin-top:${w * 0.02}px;font-family:'Barlow Condensed';font-weight:900;font-size:${w * 0.11}px;color:#fff">${vb(w * 0.11)} ${num(o.price)}</div>` : ""}
      </div>`;
    el.dataset.text = o.name.toUpperCase();
    return el;
  }
  const add = (parent, el, css) => { el.style.cssText += ";" + css; parent.appendChild(el); return el; };
  const chip = (stage, text, color = YEL, x = 60, y = 250, size = 50) => mk(stage, `left:${x}px;top:${y}px;padding:8px 22px 4px;background:${color};border:5px solid ${INK};border-radius:10px;transform:skewX(-8deg);box-shadow:0 6px 0 ${INK};white-space:nowrap`,
    `<span style="display:inline-block;font-family:'Barlow Condensed';font-weight:900;font-size:${size}px;line-height:1;color:${INK};text-transform:uppercase">${text}</span>`, text.toUpperCase());

  // NEW: the saving as a sticker burst ("-63%" / "AHORRAS 6500")
  function burst(stage) {
    const pts = []; for (let i = 0; i < 28; i++) { const a = (i / 28) * Math.PI * 2, r = i % 2 ? 132 : 158; pts.push(`${(170 + Math.cos(a) * r).toFixed(1)},${(170 + Math.sin(a) * r).toFixed(1)}`); }
    return mk(stage, `left:0;top:0;width:340px;height:340px`, svg(340, 340, `<polygon points="${pts.join(" ")}" fill="${INK}" transform="translate(0,10)"/><polygon points="${pts.join(" ")}" fill="${RED}" stroke="${INK}" stroke-width="10" stroke-linejoin="round"/>`) +
      `<div style="position:absolute;left:0;top:96px;width:340px;text-align:center;font-family:'Barlow Condensed';font-weight:900;font-size:112px;line-height:.9;color:#fff;-webkit-text-stroke:9px ${INK};paint-order:stroke fill">-${D.deal.pct}%</div>
       <div style="position:absolute;left:0;top:200px;width:340px;text-align:center;font-family:'Barlow Condensed';font-weight:900;font-size:38px;line-height:1;color:${YEL};-webkit-text-stroke:6px ${INK};paint-order:stroke fill">AHORRAS ${num(D.deal.saving)}</div>`,
      `-${D.deal.pct}% / AHORRAS ${num(D.deal.saving)}`);
  }
  // brand art (brand/tiendabad/brand, exported from the logo files): the awning, the logo's own BAD, the watermark
  const BR = "../../tiendabad/brand";
  // the CÓDIGO BAD card dressed as the shop: the brand awning over a turquoise counter, plus the 14-day reminder
  const codeCardES = function (stage, y = 520) {
    return mk(stage, `left:140px;width:800px;top:${y - 150}px;text-align:center`,
      `<img src="${BR}/awning.svg" style="position:relative;z-index:2;display:block;width:800px;height:auto">
       <div style="position:relative;z-index:1;margin:-34px 36px 0;padding:50px 0 24px;border-radius:0 0 24px 24px;background:linear-gradient(180deg,#36D3E2,#13B5C6 55%,#0B8796);border:6px solid ${INK};border-top:0;box-shadow:0 12px 0 rgba(0,0,0,.4)">
         <div style="display:inline-block;background:${YEL};border:4px solid ${INK};border-radius:8px;transform:skewX(-7deg);padding:0 16px;font-family:'Barlow Condensed';font-weight:900;font-size:40px;color:${INK}">CÓDIGO</div>
         <img src="${BR}/bad.svg" style="display:block;margin:8px auto 0;height:176px">
         <div style="font:800 31px Montserrat;color:#fff;margin-top:12px;-webkit-text-stroke:7px ${INK};paint-order:stroke fill">Tienda de objetos → Apoya a un creador</div>
         <div style="display:inline-block;margin-top:12px;padding:6px 18px;border-radius:30px;background:${INK};font:800 26px Montserrat;color:${YEL}">Dura 14 días · ¡renuévalo!</div>
         <div style="font:800 26px Montserrat;color:#fff;margin-top:8px;-webkit-text-stroke:6px ${INK};paint-order:stroke fill">#EpicPartner</div>
       </div>`,
      "CÓDIGO / BAD / TIENDA DE OBJETOS → APOYA A UN CREADOR / DURA 14 DÍAS · ¡RENUÉVALO! / #EPICPARTNER");
  }
  function thanksES(stage, y = 1010) {
    return mk(stage, `left:170px;width:740px;top:${y}px;text-align:center`,
      `<div style="display:inline-block;padding:8px 26px 4px;background:${YEL};border:5px solid ${INK};border-radius:10px;transform:skewX(-7deg);box-shadow:0 6px 0 ${INK};font-family:'Barlow Condensed';font-weight:900;font-size:48px;line-height:1;color:${INK}">¡GRACIAS POR VER!</div>
       <div style="font:800 30px Montserrat;color:#fff;margin-top:12px;-webkit-text-stroke:5px ${INK};paint-order:stroke fill">Síguenos para más contenido diario</div>`,
      "¡GRACIAS POR VER! / SÍGUENOS PARA MÁS CONTENIDO DIARIO");
  }
  // channel watermark (mini awning + LA TIENDITA BAD), opposite the #EpicPartner chip
  const bug = (stage) => mk(stage, `right:34px;top:136px;width:228px`, `<img src="${BR}/watermark.svg" style="display:block;width:228px;height:auto">`, "LA TIENDITA BAD");

  function build(stage) {
    mk(stage, `inset:0;background:linear-gradient(160deg,#1D2E8F,#14156B 45%,#2A0F5E)`);
    const bands = mk(stage, `left:-600px;top:0;width:2400px;height:1920px;background:repeating-linear-gradient(115deg,rgba(255,255,255,.05) 0 90px,transparent 90px 260px)`);

    // hook: the three tiles of the three beats, each with its label, under the date
    const hook = mk(stage, `inset:0`);
    const hookRecs = [D.deal.outfits[0], D.og.outfits[D.og.outfits.length - 1], D.last.outfits[0] || D.deal.outfits[1]];
    const hookTiles = hookRecs.map((o, i) => add(hook, tile(o, 290, 420, false), `left:${50 + i * 300}px;top:${520 + (i === 1 ? -30 : 0)}px`));
    const hookTags = [["OFERTA", YEL], ["OG", "#5BD0FF"], ["ÚLTIMO DÍA", RED]].map(([t, c], i) => chip(hook, t, c, 0, 0, 34));
    const title = S.title(stage, ["Tienda de hoy", D.dateLabel], { y: 236, size: 78 });

    // beat 1: mejor oferta
    const g1 = mk(stage, `inset:0`);
    chip(g1, "1 · Mejor oferta");
    const dealTiles = D.deal.outfits.map((o, i) => add(g1, tile(o, 205, 300, false), `left:${68 + i * 215}px;top:${380 + (i % 2) * 30}px`));
    const priceNew = mk(g1, `left:70px;top:800px;font-family:'Barlow Condensed';font-weight:900;font-size:150px;line-height:1;color:${YEL};-webkit-text-stroke:12px ${INK};paint-order:stroke fill;text-shadow:0 8px 0 ${INK}`, `${vb(120)} ${num(D.deal.final)}`, num(D.deal.final));
    const priceOld = mk(g1, `left:520px;top:838px;font-family:'Barlow Condensed';font-weight:900;font-size:84px;line-height:1;color:#fff;-webkit-text-stroke:9px ${INK};paint-order:stroke fill`, num(D.deal.regular), num(D.deal.regular));
    const strike = mk(g1, `left:505px;top:874px;width:${D.deal.regular >= 10000 ? 262 : 212}px;height:16px;background:#FF2D2D;border:4px solid ${INK};border-radius:8px;transform:rotate(-8deg);transform-origin:0 50%`);
    const dealName = mk(g1, `left:70px;top:1004px;width:860px;font:800 34px Montserrat;color:#fff;text-transform:uppercase`, `${D.deal.name} · se va el ${D.dealOutLabel}`, `${D.deal.name} · SE VA EL ${D.dealOutLabel}`.toUpperCase());
    const sv = burst(g1);

    // beat 2: regreso OG
    const g2 = mk(stage, `inset:0`);
    chip(g2, "2 · Regreso OG", "#5BD0FF");
    const ogTiles = D.og.outfits.map((o, i, a) => add(g2, tile(o, 380, 500), `left:${a.length === 2 ? 90 + i * 420 : 330}px;top:360px`));
    const stamp = mk(g2, `left:110px;top:890px;width:780px;text-align:center;padding:10px 0 4px;background:${YEL};border:8px solid ${INK};transform:rotate(-6deg);box-shadow:0 10px 0 ${INK}`,
      `<span style="font-family:'Barlow Condensed';font-weight:900;font-size:66px;line-height:1;color:${INK};white-space:nowrap">CAPÍTULO ${D.og.chapter} · TEMPORADA ${D.og.season}</span>`, `CAPÍTULO ${D.og.chapter} · TEMPORADA ${D.og.season}`);
    const gapNote = D.og.gapDays >= 30 ? S.scrawl(g2, `¡${D.og.gapDays} días sin salir!`, 66, YEL, -5) : null;

    // beat 3: último día
    const g3 = mk(stage, `inset:0`);
    chip(g3, "3 · Último día", RED);
    const lastTiles = D.last.outfits.slice(0, 6).map((o, i) => add(g3, tile(o, 260, 330), `left:${80 + (i % 3) * 280}px;top:${350 + Math.floor(i / 3) * 350}px`));
    const more = D.last.count > 6 ? mk(g3, `left:700px;top:1045px;padding:6px 20px 2px;background:#fff;border:5px solid ${INK};border-radius:30px;font-family:'Barlow Condensed';font-weight:900;font-size:54px;color:${INK}`, `+${D.last.count - 6} más`, `+${D.last.count - 6} MÁS`) : null;
    const resetNote = S.scrawl(g3, `se van mañana, ${D.resetLabel}`, 52, YEL, -5);

    const code = codeCardES(stage, 500);
    const thanks = thanksES(stage, 1010);
    const dim = mk(stage, `inset:0;background:rgba(5,8,25,.55)`);
    stage.appendChild(code); stage.appendChild(thanks);
    const caps = S.captions(stage, VO.words, { y: 1150 });
    S.disclosure(stage);
    bug(stage);
    const fin = S.finish(stage);

    const swap = (el, t, tIn, tOut) => {
      const pin = E.out5(k(t, tIn, tIn + 0.28)), pout = E.in(k(t, tOut - 0.22, tOut));
      const x = (1 - pin) * 1100 - pout * 1100, blur = (1 - pin) * 14 + pout * 14;
      el.style.transform = `translateX(${x}px)`;
      el.style.opacity = Math.max(0, 1 - Math.abs(x) / 15);
      el.style.filter = blur > 0.3 ? `blur(${blur.toFixed(1)}px)` : "none";
      el.style.visibility = t >= tIn - 0.01 && t < tOut ? "visible" : "hidden";
    };
    const tp = R("deal_price").t0, tOld = word("deal_price", /^vez$/i) + 0.12, tSave = R("saving").t0, tEra = R("og_era").t0, tGap = word("og_era", /^salía/i);

    return function renderAt(t) {
      bands.style.transform = `translateX(${(t * 40) % 260}px)`;
      // hook
      swap(hook, t, -1, b1);
      hookTiles.forEach((el, i) => T(el, { y: lerp(80, 0, E.out(k(t, i * 0.07, 0.3 + i * 0.07))), s: lerp(0.85, 1, E.back(k(t, i * 0.07, 0.3 + i * 0.07))), a: k(t, i * 0.07, 0.12 + i * 0.07), r: [-3, 0, 3][i] + Math.sin(t * 2 + i) * 0.6 }));
      hookTags.forEach((el, i) => { const t0 = 0.55 + i * 0.12; T(el, { x: 70 + i * 300 + (i === 2 ? -20 : 0), y: 490 + (i === 1 ? -30 : 0), s: E.back(k(t, t0, t0 + 0.25)), a: t >= t0 ? 1 : 0, r: [-4, 2, 4][i] }); });
      T(title, { a: t < b1 ? 1 : 0, s: 0.94 + 0.06 * E.out(k(t, 0, 0.3)) });
      // deal
      swap(g1, t, b1, b2);
      dealTiles.forEach((el, i) => T(el, { r: (i - 1.5) * 2, y: lerp(60, 0, E.out(k(t, b1 + i * 0.07, b1 + 0.3 + i * 0.07))) }));
      T(priceNew, { s: E.back(k(t, tp - 0.05, tp + 0.25)), a: t >= tp - 0.05 ? 1 : 0 });
      T(priceOld, { a: t >= tOld ? 1 : 0, s: E.back(k(t, tOld, tOld + 0.2)) });
      strike.style.transform = `rotate(-8deg) scaleX(${E.out(k(t, tOld + 0.45, tOld + 0.7))})`;
      T(dealName, { a: k(t, tp + 0.3, tp + 0.6) });
      T(sv, { x: 746, y: 700, r: -12 + Math.sin(t * 3) * 1.5, s: 0.72 * (t >= tSave ? E.back(k(t, tSave, tSave + 0.3)) : 0) * (1 + 0.03 * Math.sin(t * 9)), a: t >= tSave ? 1 : 0 });
      // OG
      swap(g2, t, b2, b3);
      ogTiles.forEach((el, i) => T(el, { r: i ? 4 : -4, s: E.back(k(t, b2 + i * 0.12, b2 + 0.35 + i * 0.12)) }));
      T(stamp, { a: t >= tEra ? 1 : 0, s: t < tEra ? 0 : 1 + 0.22 * (1 - E.out(k(t, tEra, tEra + 0.22))), r: -6 });
      if (gapNote) gapNote.place(150, 1068, t, tGap, t >= tGap && t < b3 ? 1 : 0);
      // last day
      swap(g3, t, b3, b4);
      lastTiles.forEach((el, i) => T(el, { s: E.back(k(t, b3 + i * 0.06, b3 + 0.3 + i * 0.06)) }));
      if (more) T(more, { s: E.back(k(t, b3 + 0.5, b3 + 0.75)), a: t >= b3 + 0.5 ? 1 : 0 });
      resetNote.place(80, 1062, t, b3 + 0.7, t >= b3 + 0.7 && t < b4 ? 1 : 0);
      // code
      const codeOn = win(t, b4, DUR + 1, 0.12, 0.1);
      T(dim, { a: codeOn });
      T(code, { a: codeOn, s: 0.86 + 0.14 * E.back(k(t, b4, b4 + 0.3)) });
      const thP = R("thanks"), thanksOn = thP ? win(t, thP.t0 - 0.05, DUR + 1, 0.12, 0.1) : 0;
      T(thanks, { a: thanksOn, s: 0.9 + 0.1 * E.back(k(t, thP.t0 - 0.05, thP.t0 + 0.3)) });
      caps(t);
      fin(t);
    };
  }

  const tSave = R("saving").t0, tEra = R("og_era").t0;
  const cues = [[0.03, "pop"], [0.12, "flip"], [0.2, "flip"], [0.28, "flip"], [0.55, "pop"], [0.67, "pop"], [0.79, "pop"], [b1, "whoosh"], [R("deal_price").t0, "pop"], [tSave, "locked"], [b2, "whoosh"], [tEra, "locked"],
    ...(D.og.gapDays >= 30 ? [[word("og_era", /^salía/i), "pop"]] : []), [b3, "whoosh"], ...[0, 1, 2, 3, 4, 5].map((i) => [b3 + 0.05 + i * 0.06, "flip"]), [b4, "notify"], ...(R("thanks") ? [[R("thanks").t0 - 0.03, "chime"]] : [])];
  const og = D.og.outfits.map((o) => o.name).join(" y ");
  window.SCENE = {
    duration: DUR, build, cues,
    caption: `#EpicPartner Tienda de objetos de Fortnite, ${D.dateLong}: el ${D.deal.name} cuesta ${num(D.deal.final)} V-Bucks en vez de ${num(D.deal.regular)} (ahorras ${num(D.deal.saving)}), ${og} ${D.og.outfits.length > 1 ? "están" : "está"} en la tienda (Capítulo ${D.og.chapter}, Temporada ${D.og.season}${D.og.gapDays >= 30 ? `, ${D.og.gapDays} días sin salir` : ""}) y ${D.last.count} skins se van mañana a las ${D.resetLabel} (${D.resetZone}).\n¿Vas a comprar hoy? Usa el código BAD en la tienda de objetos.\n#Fortnite #TiendaFortnite #FortniteTienda`,
    claims: [
      { text: `${D.deal.name}: ${D.deal.final} V-Bucks, regular ${D.deal.regular}`, source: "shop.json", match: [D.deal.name, `"finalPrice": ${D.deal.final}`, `"regularPrice": ${D.deal.regular}`] },
      ...D.og.outfits.map((o) => ({ text: `${o.name}: Capítulo ${D.og.chapter}, Temporada ${D.og.season}`, source: "history.json", match: [o.name, `"chapter": ${D.og.chapter}`, `"season": ${D.og.season}`] })),
      { text: `${D.og.gapDays} days since last in the shop`, source: "history.json", match: [`"gapDays": ${D.og.gapDays}`] },
      { text: `${D.last.count} outfits leave at the next reset`, source: "plan.json", match: [`"count": ${D.last.count}`] },
    ],
  };
})();
