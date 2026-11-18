"""LA TIENDITA BAD: designed covers (1080x1920) and YouTube thumbnails (1280x720).

Owner, 2026-11-18: the covers (a frame of the video) "look so ai right now and not engaging at all". So each cover is
made, not grabbed: one big subject from Epic's own art (the skin renders, the official map), two to four huge words, one
hook (a price tag, a hand-drawn circle, a "?"), and no subtitles, emoji or small UI pills. Only the video's own facts.

    python3 brands/tiendabad/auto/thumbs.py <short or shop folder> [out.jpg]    default <folder>/out/cover.jpg
    python3 brands/tiendabad/auto/thumbs.py --spec <spec.json> <out.jpg>        any layout (long videos: post.json "thumb")

Covers keep what matters inside the 3:4 crop the Instagram grid shows (y 240-1680). The layouts are in video/thumb.html.
"""
import json
import math
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND = os.path.dirname(HERE)
VIDEO = os.path.join(BRAND, "video")
SHARED_VIDEO = os.path.join(BRAND, "..", "_shared", "video")
MAP = os.path.join(BRAND, "sources", "map-blank.png")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def clean(s):
    return re.sub(r"\s+", " ", EMOJI.sub("", s)).strip()


def url(p):
    return "file://" + os.path.abspath(p)


def scene(folder):
    s = open(os.path.join(folder, "scene.js")).read()
    m = re.search(r"TPL\.(\w+)\((.*)\);\s*$", s, re.S)
    if not m:
        raise SystemExit(f"STOP: no TPL scene in {folder}/scene.js")
    return m.group(1), json.loads(m.group(2))


def asset(rel):
    """Scene image paths are relative to brands/_shared/video (where video.html renders)."""
    p = os.path.normpath(os.path.join(SHARED_VIDEO, rel))
    if not os.path.exists(p):
        raise SystemExit(f"STOP: missing image {p}")
    return url(p)


def island_centre():
    pois = json.load(open(os.path.join(BRAND, "sources", "map.json")))["data"]["pois"]
    pts = [(0.0066081 * p["location"]["x"] + 976.46, 0.0066058 * p["location"]["y"] + 1016.67) for p in pois if ".POI." in p["id"]]
    return sum(a for a, _ in pts) / len(pts), sum(b for _, b in pts) / len(pts)


def split2(name, limit=13):
    """A long name goes on two lines, cut at the space nearest the middle."""
    if len(name) <= limit or " " not in name:
        return [name]
    cut = min((i for i, ch in enumerate(name) if ch == " "), key=lambda i: abs(i - len(name) / 2))
    return [name[:cut], name[cut + 1:]]


def short_spec(folder):
    if os.path.exists(os.path.join(folder, "plan.json")):           # the daily shop video
        p = json.load(open(os.path.join(folder, "plan.json")))
        img = lambda o: url(os.path.join(folder, "img", o["id"] + ".png"))
        deal, og, last = p.get("deal") or {}, p.get("og") or {}, p.get("last") or {}
        heroes = [o for o in deal.get("outfits", [])] + [o for o in og.get("outfits", [])] + [o for o in last.get("outfits", [])]
        if not heroes:
            raise SystemExit("STOP: the shop plan names no outfit for the cover")
        spec = {"kind": "shop", "hero": img(heroes[0]), "date": p["dateLabel"]}
        back = (og.get("outfits") or []) if deal.get("outfits") else (last.get("outfits") or [])[1:]
        if back and back[0]["id"] != heroes[0]["id"]:
            spec["back"] = img(back[0])
        if deal.get("final") and deal.get("regular"):
            spec.update(final=deal["final"], regular=deal["regular"], note=deal["name"])
        return spec
    kind, c = scene(folder)
    cx, cy = island_centre()
    if kind == "map":
        name = clean(" ".join(c["title"][1:])).rstrip("?").strip()
        return {"kind": "map", "map": url(MAP), "x": c["pins"][0]["x"], "y": c["pins"][0]["y"],
                "lines": ["¿DÓNDE ESTÁ"] + [l.upper() for l in split2(name)[:-1]] + [split2(name)[-1].upper() + "?"]}
    if kind == "guess":
        return {"kind": "guess", "imgs": [asset(r["img"]) for r in c["rounds"][:3]], "note": "solo los OG aciertan las 3"}
    if kind == "drop":
        cards = c["cards"]
        hero = next((x for x in cards if "/Character_" in x["img"] or "/CID_" in x["img"]), cards[0])
        pirate = "pirata" in " ".join(c["title"]).lower()
        return {"kind": "drop", "hero": asset(hero["img"]), "final": c["price"]["final"], "regular": c["price"]["regular"],
                "note": "¿y con la Tienda Pirata?\n¡0 pavos!" if pirate else clean(c.get("when", ""))}
    if kind == "top":
        one = min(c["items"], key=lambda x: x["rank"])
        hook = [clean(l) for l in c["hook"]["lines"]]
        priced = all("pavos" in json.dumps(x.get("box", {}), ensure_ascii=False) for x in c["items"])
        if "más caro" in " ".join(hook).lower():
            lines, note = ["LO MÁS CARO", "DE HOY"], "lo nuevo de hoy"
        else:
            lines, note = [hook[-1].upper()], clean(hook[0])
        return {"kind": "top", "hero": asset(one["img"]), "lines": lines, "tag": "¿???? PAVOS?" if priced else "#1", "note": note}
    if kind == "guide":
        top, low = c["coin"]
        m = re.match(r"(\d+)\s+(.+)", low)
        big, bottom = (m.group(1), m.group(2)) if m else (low, "")
        if not m:
            top, big, bottom = "", top, low
        return {"kind": "guide", "map": url(MAP), "cx": cx, "cy": cy + 120, "kicker": c.get("kicker", ""), "top": top, "big": big,
                "bottom": bottom, "note": "¿ya te salió?"}
    if kind == "news":
        return {"kind": "news", "map": url(MAP), "cx": cx, "cy": cy, "kicker": clean(c.get("kicker", "NOTICIAS")),
                "lines": [clean(l) for l in c["hook"]], "note": clean(c.get("tile", ""))}
    raise SystemExit(f"STOP: no cover layout for TPL.{kind}")


def resolve(v):
    """Image paths in a hand-written spec (a long video's post.json "thumb") are relative to brands/tiendabad."""
    if isinstance(v, list):
        return [resolve(x) for x in v]
    if isinstance(v, str) and not v.startswith("file://") and re.search(r"\.(png|jpe?g|webp)$", v, re.I):
        p = v if os.path.isabs(v) else os.path.join(BRAND, v)
        if not os.path.exists(p):
            raise SystemExit(f"STOP: missing image {p}")
        return url(p)
    return v


def render(spec, out):
    spec = {"w": 1080, "h": 1920, **{k: resolve(v) for k, v in spec.items()}}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = os.path.abspath(out) + ".spec.json"
    json.dump(spec, open(tmp, "w"), ensure_ascii=False)
    r = subprocess.run(["node", os.path.join(VIDEO, "thumb.mjs"), tmp, os.path.abspath(out)], capture_output=True, text=True)
    os.remove(tmp)
    if r.returncode:
        raise SystemExit((r.stderr or r.stdout).strip() or "STOP: thumbnail render failed")
    return out


def cover(folder, out=None):
    return render(short_spec(folder), out or os.path.join(folder, "out", "cover.jpg"))


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == "--spec":
        print(render(json.load(open(a[1])), a[2]))
    elif len(a) in (1, 2) and not a[0].startswith("--"):
        print(cover(os.path.abspath(a[0]), a[1] if len(a) == 2 else None))
    else:
        raise SystemExit(__doc__)
