"""Build, check and ship one long LA TIENDITA BAD YouTube video (1920x1080, 30 fps), Spanish port of BAD ROYALE's.

A long video is a folder (brands/tiendabad/video/long/<YYYY-MM-DD>-<slug>/) holding:
    long.json   {"title", "caption", "claims", "segments": [...]} (segment types: brands/tiendabad/long/scene.js;
                segments with a "chapter" become YouTube chapters)
    vo.json     {"phrases": [...]}; voice settings always come from brands/tiendabad/voice.json
    post.json   {"youtubeTitle", "youtube" (description; {chapters} is replaced with the chapter list), "tags", "thumb" (a thumbs.py spec: layout ytmap or ytskins, image paths relative to brands/tiendabad)}
    sources/    the files the claims are checked against, with manifest.json

    python3 brands/tiendabad/auto/make_long.py build DIR      voice, render, review sheet (DIR/qa/review-sheet.png)
    python3 brands/tiendabad/auto/make_long.py ship DIR --at "YYYY-MM-DD HH:MM"
                                                               checklist (landscape, autopilot), upload copy, thumbnail,
                                                               hosted copies, DIR/posts.json (one YouTube call, CDMX time)
    python3 brands/tiendabad/auto/make_long.py done DIR       delete the hosted copies once it is scheduled
"""
import datetime
import glob
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BANNED, BLOG, BRAND, MX, QA, STORE, TB, TZNAME, VIDEO, VOICE, ffmpeg, mx_label, run, write_posts  # noqa: E402

SCENE = os.path.join(TB, "long", "scene.js")


def opt(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def folder():
    d = os.path.abspath(sys.argv[2])
    for f in ("long.json", "vo.json", "post.json", "sources/manifest.json"):
        if not os.path.exists(os.path.join(d, f)):
            raise SystemExit(f"STOP: {f} is missing in {d}")
    return d, os.path.basename(d.rstrip("/"))


def stamp(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def chapters(d):
    L = json.load(open(os.path.join(d, "long.json")))
    P = json.load(open(os.path.join(d, "timings.json")))["phrases"]
    out = [(max(0, P[s["p"]]["t0"] - 0.15), s["chapter"]) for s in L["segments"] if s.get("chapter")]
    if out and out[0][0] > 0:
        out.insert(0, (0.0, "Intro"))
    merged = []                          # YouTube needs chapters of 10 s or more
    for t, name in out:
        # a chapter under 12 s (a short mid-roll code card) gives way to the one after it, so the content chapter stays listed
        # (2026-11-14: "Código BAD" lasted 6 s and swallowed "Kasane Teto")
        while merged and t - merged[-1][0] < 12:
            if len(merged) == 1:
                t = merged[0][0]         # the first chapter has to start at 0:00
            merged.pop()
        merged.append((t, name))
    return "\n".join(f"{stamp(t)} {name}" for t, name in merged)


def build():
    d, slug = folder()
    vo = json.load(open(os.path.join(d, "vo.json")))
    v = json.load(open(VOICE))
    vo.update({k: v[k] for k in ("voice", "speed", "lang", "lead", "mode", "slack") if k in v})
    json.dump(vo, open(os.path.join(d, "vo.json"), "w"), indent=1, ensure_ascii=False)
    run([sys.executable, os.path.join(BRAND, "_shared", "voice", "vo.py"), "vo.json", "vo.wav", "timings.json"], d, quiet=True)
    t = json.load(open(os.path.join(d, "timings.json")))
    L = json.load(open(os.path.join(d, "long.json")))
    open(os.path.join(d, "data.js"), "w").write("window.LONG=" + json.dumps(L, ensure_ascii=False) + ";\nwindow.VO=" + json.dumps(t, ensure_ascii=False) + ";\n")
    rel = lambda p: os.path.relpath(p, VIDEO)
    mp4 = os.path.join(d, f"latienditabad-{slug}.mp4")
    common = ["--size", "1920x1080", "--data", rel(os.path.join(d, "data.js"))]
    run(["node", "render.mjs", rel(SCENE), rel(mp4), "--fps", "30", *common, "--voice", rel(os.path.join(d, "vo.wav"))], VIDEO, quiet=True)
    P = t["phrases"]
    times = [max(0.2, P[s["p"]]["t0"] + 1.4) for s in L["segments"]][:12]
    stills = os.path.join(d, "qa", "stills")
    shutil.rmtree(stills, ignore_errors=True)
    run(["node", "render.mjs", rel(SCENE), "x.mp4", *common, "--stills", ",".join(f"{x:.2f}" for x in times), "--stillsDir", stills], VIDEO, quiet=True)
    files = sorted(glob.glob(os.path.join(stills, "t*.png")))
    inputs = sum([["-i", f] for f in files], [])
    n = len(files)
    graph = "".join(f"[{i}]scale=640:-1[s{i}];" for i in range(n)) + "".join(f"[s{i}]" for i in range(n)) + f"xstack=inputs={n}:layout=" + "|".join(f"{(i % 4) * 640}_{(i // 4) * 360}" for i in range(n)) + ":fill=black"
    run([ffmpeg(), "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, os.path.join(d, "qa", "review-sheet.png")], d, quiet=True)
    print(json.dumps({"video": os.path.relpath(mp4, BRAND), "duration": round(P[-1]["t1"] + 1.2, 1), "review_sheet": os.path.relpath(os.path.join(d, "qa", "review-sheet.png"), BRAND),
                      "chapters": chapters(d).split("\n")}, indent=1, ensure_ascii=False))
    print("NEXT: look at the review sheet, write review.json, then run ship.")


def ship():
    d, slug = folder()
    at = opt("--at")
    if not at:
        raise SystemExit('STOP: --at "YYYY-MM-DD HH:MM" (Mexico City time) is required')
    when = datetime.datetime.strptime(at, "%Y-%m-%d %H:%M").replace(tzinfo=MX)
    if when < datetime.datetime.now(MX) + datetime.timedelta(minutes=15):
        raise SystemExit("STOP: that time is less than 15 minutes away.")
    rv = os.path.join(d, "review.json")
    if not os.path.exists(rv) or json.load(open(rv)).get("status") != "PASS":
        raise SystemExit("STOP: review.json is missing or not PASS.")
    c = json.load(open(os.path.join(d, "post.json")))
    desc = c["youtube"].replace("{chapters}", chapters(d))
    if not desc.startswith("#EpicPartner") or not re.search(r"c[oó]digo BAD", desc) or BANNED.search(desc) or not c.get("youtubeTitle") or len(c["youtubeTitle"]) > 100:
        raise SystemExit("STOP: post.json: description must start with #EpicPartner and say código BAD; title 1-100 characters; no banned wording.")
    mp4 = os.path.join(d, f"latienditabad-{slug}.mp4")
    run([sys.executable, QA, SCENE, mp4, os.path.join(d, "sources"), os.path.join(d, "qa"),
         "--review", rv, "--data", os.path.join(d, "data.js"), "--landscape", "--autopilot"], BRAND)
    verdict = json.load(open(os.path.join(d, "qa", "qa-log.json")))["verdict"]
    if verdict != "READY TO POST":
        raise SystemExit(f"STOP: checklist says {verdict}. See {os.path.relpath(os.path.join(d, 'qa', 'qa-log.md'), BRAND)}.")
    out = os.path.join(d, "out")
    os.makedirs(out, exist_ok=True)
    FF = ffmpeg()
    run([FF, "-y", "-loglevel", "error", "-i", mp4, "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-maxrate", "10M", "-bufsize", "20M",
         "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "copy", "-movflags", "+faststart", os.path.join(out, "video.mp4")], d, quiet=True)
    if not c.get("thumb"):    # owner, 2026-11-18: thumbnails that are a frame of the video "look so ai ... not engaging"
        raise SystemExit('STOP: post.json needs a "thumb" spec (auto/thumbs.py layouts ytmap / ytskins); look at the result before scheduling.')
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import thumbs
    thumbs.render({"w": 1280, "h": 720, **c["thumb"]}, os.path.join(out, "thumb.jpg"))
    check_thumb(os.path.join(out, "thumb.jpg"), os.path.join(d, "qa", "thumb-small.png"), FF)
    video = run([sys.executable, STORE, "put", os.path.join(out, "video.mp4"), f"{slug}/video.mp4"], BRAND, quiet=True).strip()
    thumb = run([sys.executable, STORE, "put", os.path.join(out, "thumb.jpg"), f"{slug}/thumb.jpg"], BRAND, quiet=True).strip()
    iso, local = when.isoformat(timespec="seconds"), when.strftime("%Y-%m-%dT%H:%M:%S")
    info = {"autoPublish": True, "draft": False, "descendants": [], "firstCommentText": "", "hasNotReadNotes": False, "mediaAltText": [], "media": [video],
            "videoThumbnailUrl": thumb, "providers": [{"network": "youtube"}], "publicationDate": {"dateTime": local, "timezone": TZNAME},
            "shortener": False, "smartLinkData": {"ids": []}, "text": desc,
            "youtubeData": {"title": c["youtubeTitle"], "type": "video", "privacy": "public", "tags": c["tags"], "category": "GAMING", "madeForKids": False, "isAiGeneratedContent": False}}
    calls = [{"network": "youtube", "blogId": BLOG, "date": iso, "info": info, "match": c["youtubeTitle"]}]
    write_posts(os.path.join(d, "posts.json"), slug, when, calls)
    print(json.dumps({"verdict": verdict, "post_at": mx_label(when), "posts": os.path.relpath(os.path.join(d, "posts.json"), BRAND)}, indent=1))
    print(f"thumbnail: {os.path.relpath(os.path.join(out, 'thumb.jpg'), BRAND)} (and {os.path.relpath(os.path.join(d, 'qa', 'thumb-small.png'), BRAND)}, "
          "the size YouTube's home feed shows it); look at both before scheduling (rule 11)")
    print("NEXT: schedule it exactly as posts.json says, videoThumbnailUrl included. Never schedule a long video without its thumbnail "
          "(owner, 2026-11-19: 'very important for our channels growth and engagement'; once it is live, Metricool can't change it). "
          f"If Metricool refuses the thumbnail, get a fresh link (python3 brands/_shared/store.py link {slug}/thumb.jpg) and retry once; "
          "if it still refuses, schedule nothing and tell the owner. Then getScheduledPosts: the YouTube post must carry a videoThumbnailUrl; then run done.")


def check_thumb(path, preview, ff):
    """YouTube takes a custom thumbnail as a 1280x720 JPG under 2 MB; the preview is the size the home feed shows it."""
    data = open(path, "rb").read()
    size, i = None, 2
    while i + 9 < len(data) and size is None:
        if data[i] != 0xFF:
            i += 1
        elif data[i + 1] in (0xC0, 0xC1, 0xC2):
            size = (int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big"))
        else:
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    if size != (1280, 720) or len(data) >= 2 * 1024 * 1024:
        raise SystemExit(f"STOP: the thumbnail must be a 1280x720 JPG under 2 MB (got {size}, {len(data)} bytes).")
    os.makedirs(os.path.dirname(preview), exist_ok=True)
    run([ff, "-y", "-loglevel", "error", "-i", path, "-vf", "scale=320:180", preview], os.path.dirname(path), quiet=True)


def done():
    d, slug = os.path.abspath(sys.argv[2]), os.path.basename(sys.argv[2].rstrip("/"))
    for name in ("video.mp4", "thumb.jpg"):
        run([sys.executable, STORE, "rm", f"{slug}/{name}"], BRAND, quiet=True)
    print(f"cleaned up {slug}")


if __name__ == "__main__":
    {"build": build, "ship": ship, "done": done}.get(sys.argv[1] if len(sys.argv) > 2 else "", lambda: sys.exit(__doc__))()
