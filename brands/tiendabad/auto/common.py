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
# owner, 2026-11-17: from Nov 10 on, every TikTok video got 0-1 views while the same videos got 116-1,485 views on Instagram
# Reels (the account had gone to 5-6 near-identical promo videos a day). TikTok pauses through Nov 19 (CDMX); from Nov 20 it
# gets 2 a day: the shop video and the 1:00 PM short. The pirate extras and the 8:30 PM evergreen never go to TikTok.
TIKTOK_FROM = datetime.date(2026, 11, 20)
TIKTOK_RULE = "TikTok is paused through Nov 19 CDMX; from Nov 20 it gets only the shop video and the 1:00 PM short (owner, 2026-11-17)"
# "Free skins + use a code" is the classic Fortnite scam TikTok moderates hardest, and the account's first TikTok video (Nov 9:
# "¿Toda la tienda de Fortnite GRATIS?… Usa el código BAD") was the last one that got any views. A video that says something
# is free, in its TikTok caption or out loud, skips TikTok (Instagram, Facebook, YouTube and Threads still get it).
TIKTOK_RISKY = re.compile(r"\b(gratis|gratuit[oa]s?|free|sin gastar|cero pavos|0 pavos|regal[oa]s?|regalamos)\b", re.I)
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


def made_cover(folder, src_mp4, t, out_jpg):
    """The designed cover from thumbs.py (owner, 2026-11-18: covers that are a frame of the video "look so ai right now
    and not engaging at all"). Only when no layout fits does it fall back to the frame at t, and it says so loudly:
    the run's summary names it and the playbook asks for a layout before the next video of that kind."""
    import thumbs
    try:
        thumbs.cover(folder, out_jpg)
        print(f"cover: designed ({os.path.relpath(out_jpg, BRAND)}); look at it before scheduling", file=sys.stderr)
        return "designed"
    except SystemExit as e:
        print(f"WARNING: no designed cover ({e}); this post uses the video frame at {t} s", file=sys.stderr)
        cover(src_mp4, t, out_jpg)
        return "frame"


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


def tiktok_ok(when, kind, words=""):
    """TIKTOK_RULE and TIKTOK_RISKY. kind: "shop" (shop_daily.py) or "short" (make_short.py, where only the 1:00 PM slot
    goes to TikTok); words: the TikTok caption, title and everything the voice says."""
    local, m = when.astimezone(MX), TIKTOK_RISKY.search(words)
    why = (f"the video or its caption says '{m.group(0)}' (free + code reads as a scam on TikTok)" if m
           else None if local.date() >= TIKTOK_FROM and (kind == "shop" or (local.hour, local.minute) == (13, 0)) else TIKTOK_RULE)
    if why:
        print(f"no TikTok post: {why}", file=sys.stderr)
    return not why


def post_calls(when, video, cover_url, c, networks=None, long=False, kind="short", spoken=""):
    """One ready createScheduledPost call per network. Covers go everywhere Metricool takes them: TikTok, Instagram
    Reels, Facebook Reels and YouTube, Shorts included (owner, 2026-11-19: on YouTube the thumbnails "still look very bad",
    because the Shorts never got a cover and YouTube picked a frame; Metricool accepts one for Shorts and YouTube shows it
    where the channel has Shorts thumbnails). Threads has no cover.
    TikTok only where TIKTOK_RULE and TIKTOK_RISKY allow it, even when --networks names it; spoken = the voice's lines."""
    iso, local = when.isoformat(timespec="seconds"), when.strftime("%Y-%m-%dT%H:%M:%S")
    base = {"autoPublish": True, "draft": False, "descendants": [], "firstCommentText": "", "hasNotReadNotes": False, "mediaAltText": [],
            "media": [video], "publicationDate": {"dateTime": local, "timezone": TZNAME}, "shortener": False, "smartLinkData": {"ids": []}}
    thumb = {"videoThumbnailUrl": cover_url} if cover_url else {}
    nets = {
        "tiktok": {**base, **thumb, "providers": [{"network": "tiktok"}], "text": c["tiktok"],
                   "tiktokData": {"title": c["tiktokTitle"], "disableComment": False, "disableDuet": False, "disableStitch": False, "privacyOption": "PUBLIC_TO_EVERYONE",
                                  "commercialContentThirdParty": False, "commercialContentOwnBrand": False, "autoAddMusic": False, "isAigc": False}},
        "instagram": {**base, **thumb, "providers": [{"network": "instagram"}], "text": c["instagram"],
                      "instagramData": {"type": "REEL", "showReelOnFeed": True, "isAiGenerated": False}},
        "facebook": {**base, **thumb, "providers": [{"network": "facebook"}], "text": c["facebook"],
                     "facebookData": {"type": "REEL", "title": c["facebookTitle"]}},
        "youtube": {**base, **thumb, "providers": [{"network": "youtube"}], "text": c["youtube"],
                    "youtubeData": {"title": c["youtubeTitle"], "type": "video" if long else "short", "privacy": "public", "tags": c["tags"], "category": "GAMING",
                                    "madeForKids": False, "isAiGeneratedContent": False}},
        "threads": {**base, "providers": [{"network": "threads"}], "text": c.get("threads") or c["facebook"], "threadsData": {}},
    }
    if long:
        nets = {"youtube": {**nets["youtube"], **thumb}}
    want = list(networks or (["youtube"] if long else NETWORKS))
    if "tiktok" in want and not tiktok_ok(when, kind, " ".join([c.get("tiktok", ""), c.get("tiktokTitle", ""), spoken])):
        want.remove("tiktok")
    return [{"network": k, "blogId": BLOG, "date": iso, "info": v,
             "match": {"tiktok": c["tiktokTitle"], "youtube": c["youtubeTitle"]}.get(k, v["text"][:60])} for k, v in nets.items() if k in want]


def write_posts(path, slug, when, calls):
    # owner, 2026-11-11: no post is marked as a paid partnership (or promotional content); #EpicPartner on screen and in
    # every caption is the disclosure
    for c in calls:
        td = c["info"].get("tiktokData") or {}
        if td.get("commercialContentThirdParty") or td.get("commercialContentOwnBrand"):
            raise SystemExit(f"STOP: the {c['network']} call is marked as a paid partnership / commercial content; it must not be.")
    json.dump({"slug": slug, "blogId": BLOG, "post_at": when.isoformat(timespec="minutes"), "calls": calls}, open(path, "w"), indent=1, ensure_ascii=False)


def mx_label(when):
    return when.astimezone(MX).strftime("%Y-%m-%d %-I:%M %p CDMX")


def now_mx():
    return datetime.datetime.now(MX)
