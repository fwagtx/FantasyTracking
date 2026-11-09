// node guide.cjs <guide.html> <out.png> <out.pdf>
const { chromium } = require('playwright');
(async () => {
  const [pg, png, pdf] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 1200 } });
  await p.goto('file://' + require('path').resolve(pg));
  await p.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map((i) => i.decode().catch(() => {}))); });
  const h = await p.evaluate(() => document.documentElement.scrollHeight);
  await p.screenshot({ path: png, fullPage: true });
  await p.pdf({ path: pdf, width: '1600px', height: h + 'px', printBackground: true, pageRanges: '1' });
  console.log('height', h);
  await b.close();
})();
