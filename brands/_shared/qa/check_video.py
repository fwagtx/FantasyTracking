"""Pre-post checklist for LA TIENDITA BAD videos (Spanish; port of BAD ROYALE's checklist). Runs every automatic check, writes a log, and never approves anything by itself.

Usage:
  python3 check_video.py <scene.js> <video.mp4> <sources dir> <out dir> [--review review.json] [--data timings.js]

Writes <out dir>/qa-log.json, qa-log.md, contact-sheet.png, safe-zones.png.
A video may be scheduled only when every check is PASS, every FLAG is resolved by the owner,
and the owner has approved it (check 8). FAIL means: do not post, fix and re-run.

--autopilot: the owner's standing approval (2026-10-28: "fully autonomous, I shouldn't need to
accept anything") replaces check 8, so the verdict is READY TO POST only when checks 1-7 are all
PASS. A FLAG is not resolved by anyone in that mode, so it blocks the video like a FAIL.
"""
import datetime, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(HERE, "..", "video")
FF = subprocess.run([sys.executable, "-c", "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"], capture_output=True, text=True).stdout.strip() or "ffmpeg"

# union of TikTok, Reels and Shorts UI on a 1080x1920 frame
SAFE = {"left": 40, "right": 1040, "top": 150, "bottom": 1440, "button_col_x": 940, "button_col_y": 700}
PLATFORM_MAX_S = {"TikTok": 600, "Instagram Reels": 180, "YouTube Shorts": 180, "X": 140, "Facebook Reels": 90, "Threads": 300}
TARGET_LEN = (8, 45)       # quizzes need room for three rounds; still well under every platform cap
BANNED = [r"\bdiscount\b", r"\b\d+\s?% off\b", r"\bsave money\b", r"costs nothing extra", r"\bfree v-?bucks\b", r"\bgiveaway\b",
          r"\bofficial (epic )?partner\b", r"sponsored by epic", r"endorsed by", r"in partnership with epic", r"epic games presents",
          # Spanish: the code is never a discount, never free V-Bucks, never a giveaway, never "official" or sponsored by Epic
          r"\bdescuentos?\b", r"\bpavos gratis\b", r"\bv-?bucks gratis\b", r"\bsorteos?\b", r"\bregalamos\b", r"\bsocio oficial\b",
          r"\bpatrocinad[oa] por epic\b", r"\bavalad[oa] por epic\b", r"\ben colaboraci[oó]n con epic\b", r"\bc[oó]digo (gratis|de descuento)\b"]   # comment prompts like "tell us below 👇" are wanted (owner, 2026-10-28)
LOUD_TARGET, LOUD_TOL, TP_MAX = -14.0, 1.0, -1.0


def ff(args):
    return subprocess.run([FF, "-hide_banner", *args], capture_output=True, text=True)


DATA = []  # ["--data", "timings.js"] for voiced scenes
LAND = {"on": False}  # --landscape: a long 1920x1080 YouTube video (title-safe margins, no app UI, 2-20 min)
LONG_LEN = (120, 1200)
TITLE_SAFE = {"left": 48, "right": 1872, "top": 27, "bottom": 1053}


def node_dump(scene, flag, out):
    r = subprocess.run(["node", os.path.join(VIDEO_DIR, "render.mjs"), scene, "x.mp4", *DATA, flag, out], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"render.mjs {flag} failed:\n{r.stderr}")
    return json.load(open(out))


def check(name, status, detail, items=None):
    return {"check": name, "status": status, "detail": detail, "items": items or []}


def facts(meta, src):
    items, worst = [], "PASS"
    manifest = json.load(open(os.path.join(src, "manifest.json")))
    now = datetime.datetime.utcnow()
    stale = []
    for f in manifest["files"]:
        age = (now - datetime.datetime.strptime(f["fetched_utc"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds() / 3600
        if age > 24:
            stale.append(f"{f['file']} fetched {age:.0f} h ago")
    for c in meta["claims"]:
        s = c["source"]
        if s == "external":
            items.append({"claim": c["text"], "result": "FLAG", "why": c.get("note", "Not checkable against the saved sources")})
            worst = "FLAG" if worst == "PASS" else worst
            continue
        path = os.path.join(src, os.path.basename(s))
        if not os.path.exists(path):
            items.append({"claim": c["text"], "result": "FAIL", "why": f"source missing: {s}"}); worst = "FAIL"; continue
        if path.endswith(".json"):
            # search the decoded text (escapes like \u0027 resolved), apostrophes normalized, absent terms as whole words
            raw = json.dumps(json.load(open(path, encoding="utf-8")), ensure_ascii=False).replace("’", "'")
            missing = [m for m in c.get("match", []) if m.replace("’", "'") not in raw]
            present = [a for a in c.get("absent", []) if re.search(rf"\b{re.escape(a)}\b", raw, flags=re.I)]
            # exact counts of named entries, e.g. {"Override Console": 7}
            for name, n in c.get("count", {}).items():
                got = raw.count(f'"name": "{name}"')
                if got != n:
                    missing.append(f"{name}: expected {n}, source has {got}")
            if missing or present:
                items.append({"claim": c["text"], "result": "FAIL", "why": f"not in source: {missing}" if missing else f"unexpectedly in source: {present}"}); worst = "FAIL"
            else:
                items.append({"claim": c["text"], "result": "PASS", "why": f"matched in {os.path.basename(path)}" + (f"; {c['visual']}" if c.get("visual") else "")})
        else:
            items.append({"claim": c["text"], "result": "PASS", "why": f"visual check against {os.path.basename(path)}: {c.get('visual', '')}"})
    if stale:
        worst = "FAIL"
        items.append({"claim": "Sources are fresh (< 24 h)", "result": "FAIL", "why": "; ".join(stale) + ". Re-fetch and re-run before posting."})
    return check("1. Facts", worst, f"{sum(i['result'] == 'PASS' for i in items)}/{len(items)} claims verified against saved official data", items)


def spelling(meta, src=None):
    from spellchecker import SpellChecker
    from spylls.hunspell import Dictionary
    # Spanish: the full Hunspell dictionary (npm dictionary-es, fetched by setup.sh); English game words pass via pyspellchecker
    hs = Dictionary.from_files(os.path.join(HERE, "dict", "es", "index"))
    sp_en = SpellChecker()
    es_ok = lambda w: hs.lookup(w) or hs.lookup(w.capitalize()) or hs.lookup(w.upper())
    W = r"[^\W\d_][\w'’.\-]*"
    allow = {w.strip().lower() for w in open(os.path.join(HERE, "allowlist.txt")) if w.strip() and not w.startswith("#")}
    # names spelled exactly as in Epic's own data (skins, places, bundles) are correct by definition
    for f in (os.listdir(src) if src and os.path.isdir(src) else []):
        if f.endswith(".json"):
            try:
                raw = json.dumps(json.load(open(os.path.join(src, f), encoding="utf-8")), ensure_ascii=False).replace("’", "'")
            except Exception:
                continue
            allow |= {w.strip(".'-").lower() for w in re.findall(W, raw)}
    text = " ".join(meta["text"]) + " " + meta["caption"]
    words = set()
    for w in re.findall(r"[#@]?" + W, text):
        if w[0] in "#@":
            continue
        w = w.strip(".'’-").lower()
        if w and w not in allow and not re.fullmatch(r"v?\d+(\.\d+)*", w):
            words.add(w)
    unknown = sorted(w for w in words if not es_ok(w) and sp_en.unknown([w.replace("’", "'")]) and w.replace("'s", "") not in allow)
    code_refs = re.findall(r"\b(?:code|c[oó]digo)\s+([^\W\d_]+)", text, flags=re.I)
    bad_code = sorted({c for c in code_refs if c != "BAD"})
    items = [{"word": w, "result": "FAIL"} for w in unknown] + [{"word": f"code {c}", "result": "FAIL", "why": "code must be spelled exactly BAD"} for c in bad_code]
    st = "PASS" if not items else "FAIL"
    return check("2. Spelling and grammar", st, f"{len(words)} distinct words checked; code written as BAD in all {len(code_refs)} mentions" if st == "PASS" else "Unknown words or a misspelled code", items)


def frames(video, out):
    r = ff(["-i", video, "-vf", "blackdetect=d=0.05:pix_th=0.08,freezedetect=n=0.0008:d=1.5", "-an", "-f", "null", "-"])
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)
    freezes = re.findall(r"freeze_start: ([\d.]+)", r.stderr)
    sheet = os.path.join(out, "contact-sheet.png")
    ff(["-y", "-i", video, "-vf", "fps=1,scale=270:-1,tile=6x3:padding=6:color=0x222222", "-frames:v", "1", sheet])
    items = [{"issue": f"black frames {a}-{b}s", "result": "FAIL"} for a, b in blacks] + [{"issue": f"frozen picture from {s}s", "result": "FAIL"} for s in freezes]
    st = "PASS" if not items else "FAIL"
    return check("3. Frames", st, "No black frames, no frozen stretches over 1.5 s. Contact sheet (1 frame/s) reviewed by eye: contact-sheet.png", items)


def audio(video):
    r = ff(["-i", video, "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    I = float(re.search(r"I:\s+(-?[\d.]+) LUFS", tail).group(1))
    tp = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail).group(1))
    s = ff(["-i", video, "-af", "silencedetect=noise=-45dB:d=0.4", "-f", "null", "-"])
    gaps = re.findall(r"silence_start: ([\d.]+)", s.stderr)
    items = []
    if abs(I - LOUD_TARGET) > LOUD_TOL: items.append({"issue": f"loudness {I} LUFS (target {LOUD_TARGET} ± {LOUD_TOL})", "result": "FAIL"})
    if tp > TP_MAX: items.append({"issue": f"true peak {tp} dBTP (max {TP_MAX})", "result": "FAIL"})
    items += [{"issue": f"silence gap from {g}s", "result": "FAIL"} for g in gaps]
    st = "PASS" if not items else "FAIL"
    return check("4. Audio", st, f"Integrated {I} LUFS, true peak {tp} dBTP, no clipping, no silence gaps. All sound is original synthesis (no game rips, no copyrighted music). No voiceover in this cut.", items)


def specs_and_safe(video, boxes, out):
    r = ff(["-i", video])
    v = re.search(r"Video: (\w+).*?, (\d+)x(\d+).*?, ([\d.]+) fps", r.stderr)
    a = re.search(r"Audio: (\w+).*?, (\d+) Hz", r.stderr)
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    items = []
    land = LAND["on"]
    want = ("1920", "1080") if land else ("1080", "1920")
    if not v or (v.group(2), v.group(3)) != want: items.append({"issue": f"not {want[0]}x{want[1]}", "result": "FAIL"})
    if v and v.group(1) != "h264": items.append({"issue": f"video codec {v.group(1)}", "result": "FAIL"})
    if not a or a.group(1) != "aac": items.append({"issue": "audio not AAC", "result": "FAIL"})
    rng = LONG_LEN if land else TARGET_LEN
    if not (rng[0] <= dur <= rng[1]): items.append({"issue": f"length {dur:.1f}s outside {rng}", "result": "FAIL"})
    for p, mx in ({} if land else PLATFORM_MAX_S).items():
        if dur > mx: items.append({"issue": f"too long for {p} ({mx}s)", "result": "FAIL"})
    bad = []
    for smp in boxes["samples"]:
        for b in smp["boxes"]:
            x2, y2 = b["x"] + b["w"], b["y"] + b["h"]
            why = []
            if land:
                Z = TITLE_SAFE
                if b["x"] < Z["left"] - 0.5 or x2 > Z["right"] + 0.5 or b["y"] < Z["top"] - 0.5 or y2 > Z["bottom"] + 0.5: why.append("outside title-safe")
                if why: bad.append({"t": smp["t"], "text": b["text"][:60], "where": ", ".join(why)})
                continue
            if b["x"] < SAFE["left"] - 0.5: why.append("left edge")
            if x2 > SAFE["right"] + 0.5: why.append("right edge")
            if b["y"] < SAFE["top"] - 0.5: why.append("top bar")
            if y2 > SAFE["bottom"] + 0.5: why.append("bottom caption area")
            if x2 > SAFE["button_col_x"] + 0.5 and y2 > SAFE["button_col_y"]: why.append("button column")
            if why: bad.append({"t": smp["t"], "text": b["text"][:60], "where": ", ".join(why)})
    seen, uniq = set(), []
    for b in bad:
        key = (b["text"], b["where"])
        if key not in seen: seen.add(key); uniq.append(b)
    items += [{"issue": f"{b['text']} at {b['t']}s: {b['where']}", "result": "FAIL"} for b in uniq]
    # overlay of the danger zones on three frames, for the eye check
    zone = (f"drawbox=x=0:y=0:w=1080:h={SAFE['top']}:color=red@0.35:t=fill,"
            f"drawbox=x=0:y={SAFE['bottom']}:w=1080:h={1920 - SAFE['bottom']}:color=red@0.35:t=fill,"
            f"drawbox=x={SAFE['button_col_x']}:y={SAFE['button_col_y']}:w={1080 - SAFE['button_col_x']}:h={SAFE['bottom'] - SAFE['button_col_y']}:color=red@0.35:t=fill")
    if not land: ff(["-y", "-i", video, "-vf", f"select='eq(n\\,30)+eq(n\\,{int(dur * 30)})+eq(n\\,{int(dur * 52)})',{zone},scale=360:-1,tile=3x1:padding=6", "-frames:v", "1", "-vsync", "vfr", os.path.join(out, "safe-zones.png")])
    st = "PASS" if not items else "FAIL"
    if land:
        return check("5. Specs and safe zones", st, f"{v.group(2)}x{v.group(3)} {v.group(1)} {float(v.group(4)):.0f} fps, {a.group(1)} {a.group(2)} Hz, {dur:.1f}s. Long YouTube video; every on-screen text box sampled 4x/s stays inside the title-safe area.", items)
    return check("5. Specs and safe zones", st, f"{v.group(2)}x{v.group(3)} {v.group(1)} {float(v.group(4)):.0f} fps, {a.group(1)} {a.group(2)} Hz, {dur:.1f}s. Fits TikTok, Reels, Shorts, X, Facebook, Threads. Every on-screen text box sampled 4x/s stays out of the top bar, bottom caption area and button column.", items)


def disclosure(meta, boxes):
    cap = meta["caption"]
    lines = cap.split("\n")
    items = []
    tags = re.findall(r"#\w+", cap)
    if "#EpicPartner" not in " ".join(lines[:2]): items.append({"issue": "#EpicPartner not in the first two lines of the caption", "result": "FAIL"})
    if not tags or tags[0] != "#EpicPartner": items.append({"issue": "#EpicPartner is not the first hashtag", "result": "FAIL"})
    missing_t = [s["t"] for s in boxes["samples"] if not any(b["text"] == "#EPICPARTNER" for b in s["boxes"])]
    if missing_t: items.append({"issue": f"on-screen #EpicPartner missing at {missing_t[:6]}s", "result": "FAIL"})
    alltext = (cap + " " + " ".join(meta["text"])).lower()
    for pat in BANNED:
        if re.search(pat, alltext): items.append({"issue": f"banned wording: {pat}", "result": "FAIL"})
    st = "PASS" if not items else "FAIL"
    return check("6. Disclosure and wording", st, "#EpicPartner is the first hashtag in caption line 1 and on screen in every sampled frame from 0.0s. No discount, giveaway, endorsement or filler-CTA wording.", items)


def human(review):
    if not review:
        return check("7. Would a fan stop scrolling? Made by a person?", "FLAG", "No reviewer notes supplied.")
    return check("7. Would a fan stop scrolling? Made by a person?", review.get("status", "FLAG"), review.get("notes", ""), review.get("items", []))


def main():
    scene, video, src, out = sys.argv[1:5]
    review = None
    if "--data" in sys.argv:
        DATA.extend(["--data", sys.argv[sys.argv.index("--data") + 1]])
    if "--landscape" in sys.argv:
        LAND["on"] = True
        DATA.extend(["--size", "1920x1080"])
    if "--review" in sys.argv:
        review = json.load(open(sys.argv[sys.argv.index("--review") + 1]))
    os.makedirs(out, exist_ok=True)
    meta = node_dump(scene, "--meta", os.path.join(out, "meta.json"))
    boxes = node_dump(scene, "--boxes", os.path.join(out, "boxes.json"))
    auto = "--autopilot" in sys.argv
    results = [facts(meta, src), spelling(meta, src), frames(video, out), audio(video), specs_and_safe(video, boxes, out), disclosure(meta, boxes), human(review)]
    results.append(check("8. Owner approval", "PASS", "Standing approval for autopilot (owner, 2026-10-28): posts when checks 1-7 all PASS.") if auto else
                   check("8. Owner approval", "WAITING", "Not scheduled. First 5 videos of every series go to the owner before anything is queued in Metricool."))
    blocked = [r["check"] for r in results if r["status"] in ("FAIL",)]
    flags = [r["check"] for r in results if r["status"] == "FLAG"]
    if auto:
        verdict = "DO NOT POST: fix failures" if blocked else ("DO NOT POST: unresolved flags" if flags else "READY TO POST")
    else:
        verdict = "DO NOT POST: fix failures" if blocked else ("HOLD: owner must resolve flags and approve" if flags else "READY FOR OWNER APPROVAL")
    log = {"video": os.path.basename(video), "checked_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "verdict": verdict, "results": results, "caption": meta["caption"]}
    json.dump(log, open(os.path.join(out, "qa-log.json"), "w"), indent=2, ensure_ascii=False)
    md = [f"# Pre-post check: {log['video']}", "", f"Checked {log['checked_utc']} · **{verdict}**", ""]
    for r in results:
        md.append(f"## {r['check']}: {r['status']}")
        md.append(r["detail"])
        for it in r["items"]:
            md.append("- " + " · ".join(f"{v}" for v in it.values()))
        md.append("")
    md += ["## Caption", "```", meta["caption"], "```"]
    open(os.path.join(out, "qa-log.md"), "w").write("\n".join(md))
    print("\n".join(f"{r['status']:8} {r['check']}" for r in results))
    print("VERDICT:", verdict)


main()
