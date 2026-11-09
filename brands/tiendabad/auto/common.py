"""Shared settings and helpers for the LA TIENDITA BAD autopilot (Spanish twin of BAD ROYALE's)."""
import datetime
import json
import os
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

BRAND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))       # brands/
TB = os.path.join(BRAND, "tiendabad")
SHOP = os.path.join(TB, "shop")
VIDEO = os.path.join(BRAND, "_shared", "video")
STORE = os.path.join(BRAND, "_shared", "store.py")
QA = os.path.join(BRAND, "_shared", "qa", "check_video.py")
VOICE = os.path.join(TB, "voice.json")
BLOG = "7065085"            # Metricool brand LA TIENDITA BAD (the old StreakPros brand). Never any other.
MX = ZoneInfo("America/Mexico_City")
TZNAME = "America/Mexico_City"
NETWORKS = ["tiktok", "instagram", "facebook", "youtube", "threads"]
BANNED = re.compile(r"\b(discount|discounted|% ?off|save money|free v-?bucks|giveaway|sponsored by epic|descuentos?|pavos gratis|v-?bucks gratis|"
                    r"sorteos?|regalamos|patrocinad[oa] por epic|avalad[oa] por epic|c[oó]digo (gratis|de descuento))\b", re.I)


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(cmd, cwd, quiet=False):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write((r.stdout or "")[-3000:] + (r.stderr or "")[-3000:])
        raise SystemExit(f"FAILED: {' '.join(cmd)}")
    if not quiet:
        sys.stdout.write(r.stdout)
    return r.stdout


def review_sheet(stills_dir, out_png, width=360):
    import glob
    files = sorted(glob.glob(os.path.join(stills_dir, "t*.png")))
    inputs = sum([["-i", f] for f in files], [])
    graph = "".join(f"[{i}]scale={width}:-1[s{i}];" for i in range(len(files))) + "".join(f"[s{i}]" for i in range(len(files))) + f"hstack={len(files)}"
    run([ffmpeg(), "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, out_png], stills_dir, quiet=True)


def upload_copy(src_mp4, out_mp4):
    """The copy that gets uploaded: H.264 high, CRF 20, 60 fps, AAC as rendered, fast start."""
    run([ffmpeg(), "-y", "-loglevel", "error", "-i", src_mp4, "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-maxrate", "14M", "-bufsize", "28M",
         "-pix_fmt", "yuv420p", "-r", "60", "-c:a", "copy", "-movflags", "+faststart", out_mp4], os.path.dirname(out_mp4), quiet=True)


def cover(src_mp4, t, out_jpg):
    run([ffmpeg(), "-y", "-loglevel", "error", "-ss", str(t), "-i", src_mp4, "-frames:v", "1", "-q:v", "2", out_jpg], os.path.dirname(out_jpg), quiet=True)


def caption_problems(c):
    """Every caption starts with #EpicPartner and says código BAD; no banned wording; platform limits."""
    bad = []
    for k in ("tiktok", "instagram", "facebook", "youtube", "threads"):
        txt = c.get(k) or (c.get("facebook") if k == "threads" else "") or ""
        if not txt.startswith("#EpicPartner"):
            bad.append(f"{k}: must start with #EpicPartner")
        if not re.search(r"c[oó]digo BAD", txt):
            bad.append(f"{k}: must say código BAD")
        if BANNED.search(txt):
            bad.append(f"{k}: banned wording ({BANNED.search(txt).group(0)})")
        if k == "threads" and len(txt) > 500:
            bad.append("threads: over 500 characters")
    if len(re.findall(r"#\w+", c.get("instagram", ""))) > 5:
        bad.append("instagram: more than 5 hashtags")
    if not (c.get("youtubeTitle") or "").strip() or len(c["youtubeTitle"]) > 100:
        bad.append("youtubeTitle: empty or over 100 characters")
    for k in ("tiktokTitle", "facebookTitle"):
        if not (c.get(k) or "").strip():
            bad.append(f"{k}: empty")
    if not isinstance(c.get("tags"), list) or not c["tags"]:
        bad.append("tags: empty")
    return bad


def post_calls(when, video, cover_url, c, networks=None, long=False):
    """One ready createScheduledPost call per network. Covers go only where Metricool takes them
    (TikTok, Instagram Reels, Facebook Reels); YouTube gets one only for long videos on a verified channel."""
    iso, local = when.isoformat(timespec="seconds"), when.strftime("%Y-%m-%dT%H:%M:%S")
    base = {"autoPublish": True, "draft": False, "descendants": [], "firstCommentText": "", "hasNotReadNotes": False, "mediaAltText": [],
            "media": [video], "publicationDate": {"dateTime": local, "timezone": TZNAME}, "shortener": False, "smartLinkData": {"ids": []}}
    thumb = {"videoThumbnailUrl": cover_url} if cover_url else {}
    nets = {
        "tiktok": {**base, **thumb, "providers": [{"network": "tiktok"}], "text": c["tiktok"],
                   "tiktokData": {"title": c["tiktokTitle"], "disableComment": False, "disableDuet": False, "disableStitch": False, "privacyOption": "PUBLIC_TO_EVERYONE",
                                  "commercialContentThirdParty": True, "commercialContentOwnBrand": False, "autoAddMusic": False, "isAigc": False}},
        "instagram": {**base, **thumb, "providers": [{"network": "instagram"}], "text": c["instagram"],
                      "instagramData": {"type": "REEL", "showReelOnFeed": True, "isAiGenerated": False}},
        "facebook": {**base, **thumb, "providers": [{"network": "facebook"}], "text": c["facebook"],
                     "facebookData": {"type": "REEL", "title": c["facebookTitle"]}},
        "youtube": {**base, "providers": [{"network": "youtube"}], "text": c["youtube"],
                    "youtubeData": {"title": c["youtubeTitle"], "type": "video" if long else "short", "privacy": "public", "tags": c["tags"], "category": "GAMING",
                                    "madeForKids": False, "isAiGeneratedContent": False}},
        "threads": {**base, "providers": [{"network": "threads"}], "text": c.get("threads") or c["facebook"], "threadsData": {}},
    }
    if long:
        nets = {"youtube": {**nets["youtube"], **thumb}}
    want = networks or (["youtube"] if long else NETWORKS)
    return [{"network": k, "blogId": BLOG, "date": iso, "info": v,
             "match": {"tiktok": c["tiktokTitle"], "youtube": c["youtubeTitle"]}.get(k, v["text"][:60])} for k, v in nets.items() if k in want]


def write_posts(path, slug, when, calls):
    json.dump({"slug": slug, "blogId": BLOG, "post_at": when.isoformat(timespec="minutes"), "calls": calls}, open(path, "w"), indent=1, ensure_ascii=False)


def mx_label(when):
    return when.astimezone(MX).strftime("%Y-%m-%d %-I:%M %p CDMX")


def now_mx():
    return datetime.datetime.now(MX)
