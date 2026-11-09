"""Build, check and ship one LA TIENDITA BAD short (Spanish), the same way BAD ROYALE ships its shorts.

A short is a folder (brands/tiendabad/video/auto/<YYYY-MM-DD>-<slug>/) holding:
    scene.js    the animation: window.SCENE = TPL.<template>({...}) (templates: brands/tiendabad/video/templates_es.js)
    vo.json     {"phrases": [{"text": ..., "pause": ..., "say"?: ...}, ...]}; the voice settings always come from
                brands/tiendabad/voice.json, whatever this says
    post.json   tiktok, tiktokTitle, instagram, facebook, facebookTitle, youtube, youtubeTitle, threads?, tags,
                cover_t (the second of the video used as its cover)

    python3 brands/tiendabad/auto/make_short.py build DIR
        Records the voice, renders the video and a review sheet (DIR/qa/review-sheet.png).
        Then a reviewer looks at it and writes DIR/review.json ({"status": "PASS"|"FAIL", "notes"}).
    python3 brands/tiendabad/auto/make_short.py ship DIR --at "YYYY-MM-DD HH:MM" [--sources SRC] [--networks a,b]
        Pre-post checklist in autopilot mode (must say READY TO POST), caption rules, upload copy, cover,
        upload for Metricool, and DIR/posts.json with one createScheduledPost call per network at that
        Mexico City time.
    python3 brands/tiendabad/auto/make_short.py done DIR
        Deletes the uploaded copies once every post is scheduled.

SRC is the folder of source files the claims are checked against (default DIR/sources, then brands/tiendabad/sources).
"""
import datetime
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BRAND, MX, QA, STORE, TB, VIDEO, VOICE, caption_problems, cover, mx_label, post_calls, review_sheet, run, upload_copy, write_posts  # noqa: E402


def opt(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def folder():
    d = os.path.abspath(sys.argv[2])
    for f in ("scene.js", "vo.json", "post.json"):
        if not os.path.exists(os.path.join(d, f)):
            raise SystemExit(f"STOP: {f} is missing in {d}")
    return d, os.path.basename(d.rstrip("/"))


def build():
    d, slug = folder()
    vo = json.load(open(os.path.join(d, "vo.json")))
    v = json.load(open(VOICE))
    vo.update({k: v[k] for k in ("voice", "speed", "lang", "lead", "mode", "slack") if k in v})     # the agreed voice, always
    json.dump(vo, open(os.path.join(d, "vo.json"), "w"), indent=1, ensure_ascii=False)
    run([sys.executable, os.path.join(BRAND, "_shared", "voice", "vo.py"), "vo.json", "vo.wav", "timings.json"], d, quiet=True)
    t = json.load(open(os.path.join(d, "timings.json")))
    open(os.path.join(d, "timings.js"), "w").write("window.VO=" + json.dumps(t, ensure_ascii=False) + ";\n")
    rel = lambda p: os.path.relpath(p, VIDEO)
    mp4 = os.path.join(d, f"latienditabad-{slug}.mp4")
    run(["node", "render.mjs", rel(os.path.join(d, "scene.js")), rel(mp4), "--data", rel(os.path.join(d, "timings.js")), "--voice", rel(os.path.join(d, "vo.wav"))], VIDEO, quiet=True)
    P = t["phrases"]
    times = sorted({0.5} | {round(p["t0"] + 0.45, 2) for p in P})[:8]
    stills = os.path.join(d, "qa", "stills")
    shutil.rmtree(stills, ignore_errors=True)
    run(["node", "render.mjs", rel(os.path.join(d, "scene.js")), "x.mp4", "--data", rel(os.path.join(d, "timings.js")),
         "--stills", ",".join(f"{x:.2f}" for x in times), "--stillsDir", stills], VIDEO, quiet=True)
    review_sheet(stills, os.path.join(d, "qa", "review-sheet.png"))
    print(json.dumps({"video": os.path.relpath(mp4, BRAND), "duration": round(P[-1]["t1"] + 0.75, 1),
                      "review_sheet": os.path.relpath(os.path.join(d, "qa", "review-sheet.png"), BRAND), "voice": [p["text"] for p in P]}, indent=1, ensure_ascii=False))
    print("NEXT: look at the review sheet (and a few more stills if anything looks off), write review.json, then run ship.")


def ship():
    d, slug = folder()
    at = opt("--at")
    if not at:
        raise SystemExit('STOP: --at "YYYY-MM-DD HH:MM" (Mexico City time) is required')
    when = datetime.datetime.strptime(at, "%Y-%m-%d %H:%M").replace(tzinfo=MX)
    if when < datetime.datetime.now(MX) + datetime.timedelta(minutes=15):
        raise SystemExit("STOP: that time is less than 15 minutes away; pick the next slot or skip.")
    rv = os.path.join(d, "review.json")
    if not os.path.exists(rv) or json.load(open(rv)).get("status") != "PASS":
        raise SystemExit("STOP: review.json is missing or not PASS.")
    c = json.load(open(os.path.join(d, "post.json")))
    bad = caption_problems(c)
    if bad:
        raise SystemExit("STOP: post.json: " + "; ".join(bad))
    src = opt("--sources") or (os.path.join(d, "sources") if os.path.isdir(os.path.join(d, "sources")) else os.path.join(TB, "sources"))
    mp4 = os.path.join(d, f"latienditabad-{slug}.mp4")
    run([sys.executable, QA, os.path.join(d, "scene.js"), mp4, os.path.abspath(src), os.path.join(d, "qa"),
         "--review", rv, "--data", os.path.join(d, "timings.js"), "--autopilot"], BRAND)
    verdict = json.load(open(os.path.join(d, "qa", "qa-log.json")))["verdict"]
    if verdict != "READY TO POST":
        raise SystemExit(f"STOP: checklist says {verdict}. See {os.path.relpath(os.path.join(d, 'qa', 'qa-log.md'), BRAND)}.")
    out = os.path.join(d, "out")
    os.makedirs(out, exist_ok=True)
    upload_copy(mp4, os.path.join(out, "video.mp4"))
    cover(mp4, c.get("cover_t", 0.5), os.path.join(out, "cover.jpg"))
    video = run([sys.executable, STORE, "put", os.path.join(out, "video.mp4"), f"{slug}/video.mp4"], BRAND, quiet=True).strip()
    cov = run([sys.executable, STORE, "put", os.path.join(out, "cover.jpg"), f"{slug}/cover.jpg"], BRAND, quiet=True).strip()
    nets = opt("--networks")
    calls = post_calls(when, video, cov, c, nets.split(",") if nets else None)
    write_posts(os.path.join(d, "posts.json"), slug, when, calls)
    print(json.dumps({"verdict": verdict, "post_at": mx_label(when), "posts": os.path.relpath(os.path.join(d, "posts.json"), BRAND),
                      "networks": [x["network"] for x in calls]}, indent=1))
    print("NEXT: call createScheduledPost once per entry in posts.json calls (blogId, date, info exactly as given), then run done.")


def done():
    d, slug = os.path.abspath(sys.argv[2]), os.path.basename(sys.argv[2].rstrip("/"))
    for name in ("video.mp4", "cover.jpg"):
        run([sys.executable, STORE, "rm", f"{slug}/{name}"], BRAND, quiet=True)
    print(f"cleaned up {slug}")


if __name__ == "__main__":
    {"build": build, "ship": ship, "done": done}.get(sys.argv[1] if len(sys.argv) > 2 else "", lambda: sys.exit(__doc__))()
