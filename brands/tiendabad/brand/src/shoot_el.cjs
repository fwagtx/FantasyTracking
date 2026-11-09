// node shoot_el.cjs <page.html> <selector> <out.png> [scale]  -> transparent PNG of one element
const { chromium } = require('playwright');
(async () => {
  const [pg, sel, out, scale] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1200, height: 1200 }, deviceScaleFactor: +(scale || 1) });
  await p.goto('file://' + require('path').resolve(pg));
  await p.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map((i) => i.decode().catch(() => {}))); });
  await p.locator(sel).screenshot({ path: out, omitBackground: true });
  await b.close();
})();
