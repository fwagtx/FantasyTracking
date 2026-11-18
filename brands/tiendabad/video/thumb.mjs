// Renders one cover or thumbnail from a spec (auto/thumbs.py writes it): node thumb.mjs <spec.json> <out.jpg|out.png>
import { createRequire } from "module";
import { execSync } from "child_process";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
let playwright;
try { playwright = require("playwright"); }
catch { playwright = require(path.join(execSync("npm root -g").toString().trim(), "playwright")); }

const [specPath, outPath] = process.argv.slice(2);
const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
const browser = await playwright.chromium.launch();
const page = await browser.newPage({ viewport: { width: spec.w, height: spec.h }, deviceScaleFactor: 1 });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("requestfailed", (r) => errors.push("failed to load " + r.url()));
await page.addInitScript(`window.SPEC = ${JSON.stringify(spec)};`);
await page.goto("file://" + path.join(here, "thumb.html"));
try { await page.evaluate(() => window.ready); } catch (e) { errors.push(e.message); }
const png = outPath.endsWith(".png");
await page.screenshot({ path: outPath, type: png ? "png" : "jpeg", ...(png ? {} : { quality: 92 }), clip: { x: 0, y: 0, width: spec.w, height: spec.h } });
await browser.close();
if (errors.length) { console.error("STOP: " + errors.join("\n")); process.exit(1); }
