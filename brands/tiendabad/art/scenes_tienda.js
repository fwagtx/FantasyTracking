// TIENDA BAD channel art: same family as BAD ROYALE (yellow tag, big yellow BAD, a plate), in Spanish.
const TB = { INK: '#0B0D1A', YEL: '#FFD21E', RED: '#E8322A', RED2: '#A9141B' };
TB.lockup = (parent, x, y, s = 1) => {
  const root = E.div({ left: x + 'px', top: y + 'px', width: '760px', height: '820px', transform: `scale(${s})`, transformOrigin: '0 0' }, parent);
  const txt = (t, size, fill, sw, extra = '') => `<span style="position:absolute;left:0;top:0;white-space:nowrap;font:italic 900 ${size}px/1 'Barlow Condensed';letter-spacing:2px;color:${TB.INK};-webkit-text-stroke:${sw}px ${TB.INK};transform:translate(${sw * .35}px,${sw * .55}px)">${t}</span>
     <span style="position:absolute;left:0;top:0;white-space:nowrap;font:italic 900 ${size}px/1 'Barlow Condensed';letter-spacing:2px;color:${TB.INK};-webkit-text-stroke:${sw}px ${TB.INK}">${t}</span>
     <span style="position:relative;white-space:nowrap;font:italic 900 ${size}px/1 'Barlow Condensed';letter-spacing:2px;${fill};${extra}">${t}</span>`;
  // reads top-down as the channel name: TIENDA plate, then the big yellow BAD (which is also the code)
  const plate = E.div({ left: '110px', top: '30px', width: '540px', height: '200px', background: `linear-gradient(180deg, ${TB.RED} 0%, ${TB.RED2} 100%)`, border: `14px solid ${TB.INK}`, borderRadius: '30px', transform: 'rotate(-3deg)', boxShadow: `0 14px 0 ${TB.INK}`,
    display: 'flex', alignItems: 'center', justifyContent: 'center' }, root);
  E.h('div', { style: { position: 'relative' }, html: txt('TIENDA', 128, 'color:#fff', 20) }, plate);
  const bad = E.div({ left: '70px', top: '262px', height: '420px', transform: 'rotate(-3deg)' }, root, txt('BAD', 440, 'background:linear-gradient(180deg,#FFF3A8 0%,#FFF3A8 44%,#FFD21E 46%,#FFC400 100%);-webkit-background-clip:text;color:transparent', 48));
  return root;
};
SCENES.tienda_pfp = {
  dur: 1,
  async setup(st, W, H) {
    E.div({ inset: 0, background: 'radial-gradient(90% 90% at 50% 30%, #3B8DFF 0%, #1D5FE6 45%, #1238A8 100%)' }, st);
    E.div({ left: '-200px', top: '-100px', width: '600px', height: '1400px', background: 'rgba(255,255,255,.12)', transform: 'rotate(24deg)' }, st);
    TB.lockup(st, 162, 150, 1);
  }, update() {}
};
SCENES.tienda_banner = {
  dur: 1,
  async setup(st, W, H) {   // 2560x1440 YouTube banner; the 1546x423 middle is what every device shows
    E.div({ inset: 0, background: 'linear-gradient(160deg,#1D2E8F,#14156B 45%,#2A0F5E)' }, st);
    E.div({ left: '-600px', top: 0, width: '3800px', height: '1440px', background: 'repeating-linear-gradient(115deg,rgba(255,255,255,.05) 0 90px,transparent 90px 260px)' }, st);
    TB.lockup(st, 560, 512, .5);
    E.div({ left: '1000px', top: '560px', font: "italic 900 120px/1 'Barlow Condensed'", color: '#fff', WebkitTextStroke: `16px ${TB.INK}`, paintOrder: 'stroke fill', textShadow: `0 10px 0 ${TB.INK}`, whiteSpace: 'nowrap' }, st, 'LA TIENDA DE FORTNITE');
    E.div({ left: '1004px', top: '700px', font: "italic 900 80px/1 'Barlow Condensed'", color: TB.YEL, WebkitTextStroke: `12px ${TB.INK}`, paintOrder: 'stroke fill', whiteSpace: 'nowrap' }, st, 'CADA DÍA, EN ESPAÑOL');
    E.div({ left: '1006px', top: '812px', padding: '8px 22px', borderRadius: '30px', background: 'rgba(11,13,26,.8)', border: '3px solid rgba(255,255,255,.85)', font: "800 34px Barlow", color: '#fff', whiteSpace: 'nowrap' }, st, 'Código BAD · #EpicPartner · Hecho por fans, sin afiliación con Epic');
  }, update() {}
};
