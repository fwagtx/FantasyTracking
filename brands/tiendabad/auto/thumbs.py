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


RARITY = {"common": ("b5b5b5ff", "4a4a4aff", "2c2c2cff"), "uncommon": ("69bb1eff", "175117ff", "0f3a10ff"),
          "rare": ("2cc1ffff", "143977ff", "0c2550ff"), "epic": ("c359ffff", "4b0f81ff", "33095aff"),
          "legendary": ("ea8d23ff", "78371dff", "52240fff"), "mythic": ("ffd94aff", "a8811dff", "6e5410ff"),
          "icon": ("36b7b7ff", "1d5f66ff", "123f44ff"), "marvel": ("d4383aff", "6d1414ff", "4a0c0cff"),
          "dc": ("5475c7ff", "1f2d66ff", "141d44ff"), "starwars": ("2a4a8cff", "081737ff", "050d22ff"),
          "gaminglegends": ("5f53d6ff", "1e1757ff", "140f3bff")}


def fetch(src, dest):
    if not os.path.exists(dest):
        import urllib.request
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0 (thumbs)"})
        data = urllib.request.urlopen(req, timeout=30).read()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(data)
    return dest


def vbuck():
    """The V-buck icon the shop shows (cached; every shop folder keeps a copy from fortnite-api.com)."""
    dest = os.path.join(VIDEO, "cache", "vbuck.png")
    if not os.path.exists(dest):
        import glob
        have = sorted(glob.glob(os.path.join(BRAND, "shop", "20*", "img", "vbuck.png")))
        if have:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, "wb").write(open(have[-1], "rb").read())
        else:
            fetch("https://fortnite-api.com/images/vbuck.png", dest)
    return url(dest)


def tile(entry, folder):
    """A shop listing as the game shows it: its in-game render (newDisplayAsset) and Epic's tile colours."""
    imgs = (entry.get("newDisplayAsset") or {}).get("renderImages") or []
    if not imgs:
        return None
    pick = next((r for r in imgs if r.get("productTag") == "Product.BR"), imgs[0])
    key = pick["image"].rstrip("/").split("/")[-2]
    return url(fetch(pick["image"], os.path.join(folder, "img", f"nda-{key}.png")))


def entry_name(e):
    return (e.get("bundle") or {}).get("name") or next((x.get("name") for k in ("brItems", "cars", "tracks", "instruments") for x in (e.get(k) or [])), "")


def shop_entries(path):
    d = json.load(open(path))
    return d["data"]["entries"] if isinstance(d.get("data"), dict) else d.get("entries", [])


def short_spec(folder):
    if os.path.exists(os.path.join(folder, "plan.json")):           # the daily shop video
        p = json.load(open(os.path.join(folder, "plan.json")))
        es = shop_entries(os.path.join(folder, "shop.json"))
        deal, og, last = p.get("deal") or {}, p.get("og") or {}, p.get("last") or {}
        pick, spec = None, {"kind": "shop", "date": p["dateLabel"], "vbuck": vbuck()}
        if deal.get("name"):
            pick = next((e for e in es if entry_name(e) == deal["name"]), None)
            if pick:
                spec.update(final=deal["final"], regular=deal["regular"], name=deal["name"],
                            tag=f"AHORRAS {deal['regular'] - deal['final']:,}" if deal.get("regular", 0) > deal.get("final", 0) else None)
        for o in (og.get("outfits") or []) + (last.get("outfits") or []):
            if pick:
                break
            pick = next((e for e in es if any(x.get("id") == o["id"] for x in (e.get("brItems") or []))), None)
            if pick:
                spec.update(name=o["name"], final=pick.get("finalPrice"), tag="OG" if o in (og.get("outfits") or []) else "ÚLTIMO DÍA")
        if not pick or not tile(pick, folder):
            raise SystemExit("STOP: no shop listing with an in-game render for the cover")
        spec.update(hero=tile(pick, folder), colors=pick["colors"])
        return spec
    kind, c = scene(folder)
    cx, cy = island_centre()
    if kind == "map":
        name = clean(" ".join(c["title"][1:])).rstrip("?").strip()
        parts = split2(name)
        return {"kind": "map", "map": url(MAP), "x": c["pins"][0]["x"], "y": c["pins"][0]["y"],
                "lines": [l.upper() for l in parts[:-1]] + [parts[-1].upper() + "?"]}
    if kind == "guess":
        r = c["rounds"][0]
        return {"kind": "guess", "hero": asset(r["img"]), "name": r["name"], "rarity": list(RARITY.get(r.get("rarity"), RARITY["epic"]))}
    if kind == "drop":
        src = os.path.join(folder, "sources", "shop-entry.json")
        e = shop_entries(src)[0] if os.path.exists(src) else None
        if not e or not tile(e, folder):
            raise SystemExit("STOP: no saved shop entry with an in-game render (sources/shop-entry.json)")
        when = "A LAS 6 PM · CDMX" if "6 PM" in c.get("when", "") else clean(c.get("when", "")).upper()
        return {"kind": "drop", "hero": tile(e, folder), "colors": e["colors"], "name": c["name"], "final": c["price"]["final"],
                "regular": c["price"]["regular"], "when": when, "vbuck": vbuck()}
    if kind == "top":
        one = min(c["items"], key=lambda x: x["rank"])
        hook = [clean(l) for l in c["hook"]["lines"]]
        src = os.path.join(folder, "sources", "shop-entries.json")
        e = next((x for x in shop_entries(src) if entry_name(x) == one["label"]), None) if os.path.exists(src) else None
        lines = ["LO MÁS CARO", "DE HOY"] if "más caro" in " ".join(hook).lower() else [hook[-1].upper()]
        spec = {"kind": "top", "lines": lines, "name": one["label"], "vbuck": vbuck()}
        if e and tile(e, folder):
            spec.update(hero=tile(e, folder), colors=e["colors"])
        else:
            spec.update(hero=asset(one["img"]), colors={"color1": RARITY["epic"][0], "color3": RARITY["epic"][1], "textBackgroundColor": RARITY["epic"][2]})
        return spec
    if kind == "guide":
        top, low = c["coin"]
        m = re.match(r"(\d+)\s+(.+)", low)
        big, bottom = (m.group(1), m.group(2)) if m else (low, "")
        if not m:
            top, big, bottom = "", top, low
        return {"kind": "guide", "map": url(MAP), "cx": cx, "cy": cy + 120, "kicker": c.get("kicker", ""), "top": top, "big": big,
                "bottom": bottom, "note": "¿YA TE SALIÓ?"}
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
