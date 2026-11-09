// Renders a scene to MP4 (60 fps by default), with sound from mix.py.
// Usage: node render.mjs <scene.js> <out.mp4> [--size 1920x1080] [--fps 60] [--data timings.js] [--voice vo.wav] [--stills t1,t2,... --stillsDir dir] [--boxes out.json] [--meta out.json]
//   --boxes samples the on-screen text boxes 4 times a second for the safe-zone check.
import { createRequire } from "module";
import { execSync, spawn, spawnSync } from "child_process";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
let playwright;
try { playwright = require("playwright"); }
catch { playwright = require(path.join(execSync("npm root -g").toString().trim(), "playwright")); }

const [sceneArg, outArg] = process.argv.slice(2);
const args = Object.fromEntries(process.argv.slice(4).reduce((a, v, i, arr) => (v.startsWith("--") ? [...a, [v.slice(2), arr[i + 1]]] : a), []));
const FPS = Number(args.fps || 60);
const [W, H] = (args.size || "1080x1920").split("x").map(Number);   // 1080x1920 shorts, 1920x1080 long videos
const FFMPEG = process.env.FFMPEG || execSync(`python3 -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"`).toString().trim();
const scene = path.relative(here, path.resolve(sceneArg));

const browser = await playwright.chromium.launch();
const page = await browser.newPage({ viewport: { width: W, height: H } });
const errors = [];
page.on("pageerror", (e) => { errors.push(e.message); console.error("page error:", e.message); });
const dataQ = args.data ? "&d=" + encodeURIComponent(path.relative(here, path.resolve(args.data))) : "";
await page.goto("file://" + path.join(here, "video.html") + "?s=" + encodeURIComponent(scene) + dataQ + `&w=${W}&h=${H}`);
await page.evaluate(() => window.ready);
const clip = { x: 0, y: 0, width: W, height: H };
const dur = await page.evaluate(() => window.duration);

if (args.stills) {
  const dir = path.resolve(args.stillsDir || ".");
  fs.mkdirSync(dir, { recursive: true });
  for (const t of args.stills.split(",").map(Number)) {
    await page.evaluate((tt) => window.renderAt(tt), t);
    await page.screenshot({ path: path.join(dir, `t${t.toFixed(2).padStart(5, "0")}.png`), clip });
  }
  await browser.close();
  process.exit(errors.length ? 1 : 0);
}
if (args.meta) {
  const meta = await page.evaluate(() => ({ duration: window.SCENE.duration, caption: window.SCENE.caption, claims: window.SCENE.claims, text: window.allText() }));
  fs.writeFileSync(path.resolve(args.meta), JSON.stringify(meta, null, 1));
  await browser.close();
  process.exit(errors.length ? 1 : 0);
}
if (args.boxes) {
  const samples = [];
  for (let t = 0; t <= dur; t += 0.25) {
    await page.evaluate((tt) => window.renderAt(tt), t);
    samples.push({ t, boxes: await page.evaluate(() => window.textBoxes()) });
  }
  const text = await page.evaluate(() => window.allText());
  fs.writeFileSync(path.resolve(args.boxes), JSON.stringify({ duration: dur, samples, text }, null, 1));
  await browser.close();
  process.exit(errors.length ? 1 : 0);
}

const out = path.resolve(outArg);
const silent = out.replace(/\.mp4$/, ".silent.mp4");
const total = Math.round(dur * FPS);
const ff = spawn(FFMPEG, ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-i", "-",
  "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-r", String(FPS), "-movflags", "+faststart", silent], { stdio: ["pipe", "inherit", "inherit"] });
for (let i = 0; i < total; i++) {
  await page.evaluate((tt) => window.renderAt(tt), i / FPS);
  const buf = await page.screenshot({ type: "jpeg", quality: 95, clip });
  if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once("drain", r));
  if (i % (FPS * 4) === 0) console.log(`frame ${i}/${total}`);
}
ff.stdin.end();
await new Promise((r) => ff.on("close", r));
const cues = await page.evaluate(() => window.cues);
await browser.close();
const mix = spawnSync("python3", [path.join(here, "mix.py"), silent, out, String(dur), JSON.stringify(cues), ...(args.voice ? [path.resolve(args.voice)] : [])], { stdio: "inherit", env: { ...process.env, FFMPEG } });
if (mix.status !== 0) process.exit(mix.status);
fs.unlinkSync(silent);
console.log("done", out, errors.length ? `(${errors.length} page errors)` : "");
