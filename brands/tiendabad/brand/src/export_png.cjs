// node export_png.cjs <jobs.json>   jobs: [[svg, out.png, width], ...]  -> transparent PNGs (SVG inlined, no file loads)
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const b = await chromium.launch();
  for (const [svg, out, W] of jobs) {
    let src = fs.readFileSync(svg, 'utf8');
    const vb = src.match(/viewBox="([^"]+)"/)[1].split(/\s+/).map(Number);
    const H = Math.round(W * vb[3] / vb[2]);
    src = src.replace('<svg ', `<svg width="${W}" height="${H}" style="display:block" `);
    const p = await b.newPage({ viewport: { width: W, height: H } });
    await p.setContent(`<html><body style="margin:0;background:transparent">${src}</body></html>`);
    await p.screenshot({ path: out, omitBackground: true });
    await p.close();
    console.log(out.split('/').pop(), W + 'x' + H);
  }
  await b.close();
})();
