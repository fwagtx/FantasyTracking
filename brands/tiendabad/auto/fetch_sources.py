"""Refresh the official sources every LA TIENDITA BAD post is checked against (all in Latin American Spanish).

    python3 brands/tiendabad/auto/fetch_sources.py

Writes brands/tiendabad/sources/: map.json (places), map-labels.png / map-blank.png (official map),
news.json (in-game news feed), manifest.json (what was fetched and when; the checklist refuses
sources older than 24 hours). Prints what changed since the last fetch: new or removed news
tiles and places, so queued posts about something that is gone can be pulled.
"""
import datetime
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "sources")
API = "https://fortnite-api.com"
FILES = [
    ("map.json", f"{API}/v1/map?language=es-419", "Official map and named places (fortnite-api.com mirror of Epic's data, es-419)"),
    ("news.json", f"{API}/v2/news/br?language=es-419", "In-game news feed, Battle Royale (es-419)"),
    ("map-labels.png", f"{API}/images/map_es-419.png", "Official map image with Spanish labels"),
    ("map-blank.png", f"{API}/images/map.png", "Official map image without labels"),
]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "latienditabad-autopilot"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def names(path, kind):
    try:
        d = json.load(open(path))["data"]
    except Exception:
        return set()
    if kind == "map":
        return {p["name"] for p in d.get("pois", []) if ".POI." in p.get("id", "")}
    return {m.get("title") for m in (d.get("motds") or [])}


def main():
    os.makedirs(SRC, exist_ok=True)
    before = {"places": names(os.path.join(SRC, "map.json"), "map"), "news": names(os.path.join(SRC, "news.json"), "news")}
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    files = []
    for name, url, what in FILES:
        data = get(url)
        open(os.path.join(SRC, name), "wb").write(data)
        files.append({"file": name, "source": what, "url": url, "fetched_utc": now})
    json.dump({"via": "fortnite-api.com, which mirrors Epic's own data (es-419)", "files": files}, open(os.path.join(SRC, "manifest.json"), "w"), indent=1, ensure_ascii=False)
    after = {"places": names(os.path.join(SRC, "map.json"), "map"), "news": names(os.path.join(SRC, "news.json"), "news")}
    out = {"fetched_utc": now,
           "places": sorted(after["places"]), "news": sorted(after["news"]),
           "places_added": sorted(after["places"] - before["places"]) if before["places"] else [],
           "places_removed": sorted(before["places"] - after["places"]),
           "news_added": sorted(after["news"] - before["news"]) if before["news"] else [],
           "news_removed": sorted(before["news"] - after["news"])}
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
