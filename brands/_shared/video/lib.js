/* Shared animation helpers for BAD channel videos. */
(function (root) {
  "use strict";
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const k = (t, a, b) => clamp((t - a) / (b - a));
  const lerp = (a, b, x) => a + (b - a) * x;
  const E = {
    out: (x) => 1 - Math.pow(1 - x, 3),
    out5: (x) => 1 - Math.pow(1 - x, 5),
    in: (x) => x * x * x,
    inOut: (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
    back: (x) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); },
  };
  const pop = (t, t0, d = 0.3) => E.back(k(t, t0, t0 + d));
  const win = (t, a, b, fi = 0.15, fo = 0.15) => Math.min(k(t, a, a + fi), 1 - k(t, b - fo, b));
  function mk(parent, css, html = "", text) {
    const d = document.createElement("div");
    d.style.cssText = "position:absolute;" + css;
    d.innerHTML = html;
    if (text) d.dataset.text = text;
    parent.appendChild(d);
    return d;
  }
  function T(e, o = {}) {
    const { x = 0, y = 0, s = 1, sx = 1, sy = 1, r = 0, a = 1 } = o;
    e.style.transform = `translate(${x}px,${y}px) rotate(${r}deg) scale(${s * sx},${s * sy})`;
    e.style.opacity = a;
    e.style.visibility = a <= 0.001 ? "hidden" : "visible";
  }
  const svg = (w, h, inner, css = "") => `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg" style="display:block;overflow:visible;${css}">${inner}</svg>`;
  // in-game style text: caps, heavy outline behind the fill, short hard shadow, slight lean
  function fn(text, size, o = {}) {
    const { fill = "#fff", stroke = "#0A0A14", sw = Math.max(2, size * 0.075), skew = -6, weight = 900, lh = 0.92, track = 0.5, shadow = true } = o;
    return `<span style="display:inline-block;font-family:'Barlow Condensed';font-weight:${weight};font-size:${size}px;line-height:${lh};letter-spacing:${track}px;text-transform:uppercase;color:${fill};-webkit-text-stroke:${sw}px ${stroke};paint-order:stroke fill;${shadow ? `text-shadow:0 ${Math.round(size * 0.07)}px 0 rgba(0,0,0,.4);` : ""}transform:skewX(${skew}deg);white-space:nowrap">${text}</span>`;
  }
  root.FX = { clamp, k, lerp, E, pop, win, mk, T, svg, fn };
})(window);
