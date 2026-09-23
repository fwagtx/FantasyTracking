"""Plan a week of StreakPros promo posts, and pick what actually goes out.

Three posts a day, each an Instagram Reel, a TikTok and a YouTube Short
of the same vertical clip:

  10:00 CT  a feature (montage, streaks, trade calculator, ...)
  13:00 CT  a slice of one (QB rankings, the playoff picture, ...)
  18:00 CT  one NFL team's page, or a starter's profile from one

Each list is walked in order, week after week, so nothing repeats
within a week and the team slot takes 64 days to come round again.
Every week is recorded fresh, so even a repeat shows that week's data.

  plan   write plan.json for the week starting --start (a Wednesday):
         the 21 posts plus spares in each slot, and the scene list to
         record.
  final  given plan.json and the QA report for the recorded clips,
         print the posts to schedule -- any clip that failed QA is
         swapped for a spare from the same slot that passed.

Captions, titles and hashtags live in promo_catalog.json. Every post
goes to the StreakPros brand and nowhere else.
"""
import argparse
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from promo_capture import TEAMS  # noqa: E402

REPO = "fwagtx/FantasyTracking"
BRAND = "7065085"                      # StreakPros in Metricool, only ever this one
TZ = ZoneInfo("America/Chicago")
ANCHOR = dt.date(2026, 10, 7)          # first automated week; before it was scheduled by hand
SLOTS = [("10:00", "feature"), ("13:00", "variant"), ("18:00", "team")]
SPARES = 3                             # per slot, recorded in case a clip fails QA


def slugify(s):
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-").replace("--", "-")


def catalog():
    with open(os.path.join(HERE, "promo_catalog.json"), encoding="utf-8") as f:
        c = json.load(f)
    teams = []
    for abbr, full in TEAMS.items():
        city, nick = full.rsplit(" ", 1)
        tag = "#" + "".join(ch for ch in nick.lower() if ch.isalnum())
        teams.append(dict(
            scene=f"t_{abbr.lower()}", slug=f"{slugify(full)}-team-page",
            caption=f"The {nick}, all on one page. 🏈\n\nResults, the full roster with dynasty values, "
                    f"and the depth chart — every NFL team gets one.\n\nstreakpros.com",
            yt=f"{full}: Results, Roster & Dynasty Values #shorts",
            tt=f"The {nick}, all on one page",
            tags=[full.lower(), nick.lower(), "nfl", "dynasty fantasy football", "depth chart"],
            hashtags=f"#streakpros {tag} #nfl #fantasyfootball #nflteams"))
        teams.append(dict(
            scene=f"p_{abbr.lower()}", slug=f"{slugify(nick)}-player-profile",
            caption=f"Any {nick} player, in full. 🔎\n\nStats, dynasty value, injury status and exactly "
                    f"where he sits on the depth chart.\n\nstreakpros.com",
            yt=f"{full} Player Profile: Stats, Value, Depth Chart #shorts",
            tt="Any player, in full",
            tags=[full.lower(), "nfl player stats", "dynasty value", "depth chart", "fantasy football"],
            hashtags=f"#streakpros {tag} #nfl #fantasyfootball #dynastyfantasyfootball"))
    # Alternate team pages and player profiles, and never put the same
    # team on two days running: all 32 team pages, then all 32 profiles
    # starting halfway down the list.
    t = [x for x in teams if x["scene"].startswith("t_")]
    p = [x for x in teams if x["scene"].startswith("p_")]
    p = p[16:] + p[:16]
    order = [x for pair in zip(t, p) for x in pair]
    return {"feature": c["features"], "variant": c["variants"], "team": order}


def media_url(tag, slug):
    return f"https://github.com/{REPO}/releases/download/{tag}/{slug}-vertical.mp4"


def cover_url(tag, slug):
    return f"https://github.com/{REPO}/releases/download/{tag}/{slug}-cover.jpg"


def payload(entry, tag, day, time):
    local = dt.datetime.combine(day, dt.time.fromisoformat(time), TZ)
    text = entry["caption"].rstrip() + "\n\n" + entry["hashtags"]
    assert entry["hashtags"].startswith("#streakpros ")
    return {
        "date": local.isoformat(),
        "info": {
            "autoPublish": True, "draft": False, "descendants": [], "firstCommentText": "",
            "hasNotReadNotes": False, "media": [media_url(tag, entry["slug"])], "mediaAltText": [],
            "videoThumbnailUrl": cover_url(tag, entry["slug"]),
            "providers": [{"network": "instagram"}, {"network": "tiktok"}, {"network": "youtube"}],
            "publicationDate": {"dateTime": f"{day.isoformat()}T{time}:00", "timezone": "America/Chicago"},
            "shortener": False, "smartLinkData": {"ids": []}, "text": text,
            "youtubeData": {"title": entry["yt"], "type": "short", "privacy": "public", "tags": entry["tags"],
                            "category": "SPORTS", "madeForKids": False, "isAiGeneratedContent": False},
            "instagramData": {"type": "REEL", "showReelOnFeed": True, "isAiGenerated": False},
            "tiktokData": {"privacyOption": "PUBLIC_TO_EVERYONE", "commercialContentOwnBrand": True,
                           "commercialContentThirdParty": False, "title": entry["tt"], "autoAddMusic": False,
                           "isAigc": False, "disableComment": False, "disableDuet": False,
                           "disableStitch": False},
        },
    }


def next_wednesday(today):
    return today + dt.timedelta(days=(2 - today.weekday()) % 7 or 7)


def plan(start):
    assert start.weekday() == 2, f"{start} is not a Wednesday"
    week = (start - ANCHOR).days // 7
    cat = catalog()
    tag = f"promo-week-{start.isoformat()}"
    posts, spares = [], {}
    for d in range(7):
        day = start + dt.timedelta(days=d)
        for time, slot in SLOTS:
            items = cat[slot]
            e = items[(week * 7 + d) % len(items)]
            posts.append({"slot": slot, "day": day.isoformat(), "time": time, **e})
    for time, slot in SLOTS:
        items = cat[slot]
        used = {p["scene"] for p in posts if p["slot"] == slot}
        k, out = week * 7 + 7, []
        while len(out) < SPARES and k < week * 7 + 7 + len(items):
            e = items[k % len(items)]
            if e["scene"] not in used:
                out.append(e)
            k += 1
        spares[slot] = out
    scenes = sorted({p["scene"] for p in posts} | {e["scene"] for v in spares.values() for e in v})
    return {"week_start": start.isoformat(), "tag": tag, "brand": BRAND,
            "posts": posts, "spares": spares, "scenes": scenes}


def final(pl, qa):
    """The posts to schedule: failed clips swapped for passing spares."""
    ok = lambda slug: qa.get(f"{slug}-vertical.mp4", {}).get("ok", False)
    free = {slot: [e for e in v if ok(e["slug"])] for slot, v in pl["spares"].items()}
    out, notes = [], []
    for p in pl["posts"]:
        e = p
        if not ok(p["slug"]):
            if not free[p["slot"]]:
                notes.append(f"{p['day']} {p['time']}: {p['slug']} failed QA and no spare passed -- slot left empty")
                continue
            e = free[p["slot"]].pop(0)
            reasons = "; ".join(qa.get(f"{p['slug']}-vertical.mp4", {}).get("reasons", ["not recorded"]))
            notes.append(f"{p['day']} {p['time']}: {p['slug']} failed QA ({reasons}) -- using {e['slug']}")
        out.append({"day": p["day"], "time": p["time"], "slug": e["slug"],
                    **payload(e, pl["tag"], dt.date.fromisoformat(p["day"]), p["time"])})
    return out, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plan")
    a.add_argument("--start", help="week start, a Wednesday (default: the next one)")
    a.add_argument("--out", default="plan.json")
    b = sub.add_parser("final")
    b.add_argument("--plan", default="plan.json")
    b.add_argument("--qa", default="qa.json")
    b.add_argument("--out", default="final.json")
    args = ap.parse_args()
    if args.cmd == "plan":
        start = dt.date.fromisoformat(args.start) if args.start else next_wednesday(dt.datetime.now(TZ).date())
        if start < ANCHOR:
            print(f"week of {start} is before {ANCHOR}, which was scheduled by hand -- nothing to plan")
            with open(args.out, "w") as f:
                json.dump({"skip": True}, f)
            return
        pl = plan(start)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(pl, f, ensure_ascii=False, indent=1)
        print(f"{pl['tag']}: {len(pl['posts'])} posts, {len(pl['scenes'])} scenes to record")
        print(",".join(pl["scenes"]))
    else:
        pl = json.load(open(args.plan, encoding="utf-8"))
        qa = json.load(open(args.qa, encoding="utf-8"))
        out, notes = final(pl, qa)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"tag": pl["tag"], "brand": pl["brand"], "posts": out, "notes": notes}, f,
                      ensure_ascii=False, indent=1)
        print(f"{len(out)} of {len(pl['posts'])} posts ready")
        for n in notes:
            print("  " + n)


if __name__ == "__main__":
    main()
