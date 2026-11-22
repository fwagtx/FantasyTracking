"""LA TIENDITA BAD: make an "Adivina la temporada" short from three outfits, straight from Epic's cosmetics data (es-419).

    python3 brands/tiendabad/auto/new_guess.py <folder> auto
    python3 brands/tiendabad/auto/new_guess.py <folder> <id> <id> <id>

"auto" picks three outfits from today's live shop whose ids are not in the ledger's "ids", that no daily shop video of
the past week named as its "Regreso OG" (it said their season), and whose season was not an answer in the last two
quizzes: the oldest, one from the middle and the newest (outfits from the newest season in the shop are left out, they
are too easy), from different chapters where possible, asked in the order middle, oldest, newest.
Or name exactly three outfit ids yourself.
Writes <folder>/: sources/<id>.json + sources/manifest.json (the facts), img/<id>.png (the art), scene.js (TPL.guess),
vo.json and post.json, and prints the ids for the ledger entry. Then build and ship with make_short.py, passing
--sources <folder>/sources. Outfits whose chapter or season isn't a plain number are refused (the voice can't say them
cleanly).
"""
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://fortnite-api.com/v2/cosmetics/br"
SHOP = "https://fortnite-api.com/v2/shop?language=es-419"
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.json")


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "latienditabad-autopilot"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def era(cid):
    """(chapter, season) Epic gives for an outfit, or None when it isn't a plain number or it isn't an outfit."""
    d = json.loads(get(f"{API}/{urllib.parse.quote(cid)}?language=es-419"))["data"]
    intro = d.get("introduction") or {}
    ch, se = str(intro.get("chapter", "")), str(intro.get("season", ""))
    return (int(ch), int(se)) if d["type"]["value"] == "outfit" and ch.isdigit() and se.isdigit() else None


def said_lately(days=7):
    """Outfits whose chapter and season a daily shop video of the past week said out loud (its "Regreso OG" part).
    2026-11-14: the auto pick chose Detective, the shop video's OG that same day, so the quiz answer was already given."""
    shop, today, out = os.path.join(os.path.dirname(LEDGER), "..", "shop"), datetime.date.today(), set()
    for k in range(-1, days + 1):
        p = os.path.join(shop, (today - datetime.timedelta(days=k)).isoformat(), "plan.json")
        if os.path.exists(p):
            out |= {o["id"] for o in json.load(open(p))["og"]["outfits"]}
    return out


def pick_auto():
    used = {i for t in json.load(open(LEDGER))["topics"] for i in t.get("ids", [])} | said_lately()
    ids = []
    for e in json.loads(get(SHOP))["data"]["entries"]:
        for i in e.get("brItems") or []:
            if i["type"]["value"] == "outfit" and i["id"] not in ids and i["id"] not in used:
                ids.append(i["id"])
    eras = {cid: e for cid in ids if (e := era(cid))}
    if not eras:
        raise SystemExit("STOP: no usable outfits in today's shop")
    newest = max(eras.values())
    quiz = [t for t in json.load(open(LEDGER))["topics"] if t.get("ids")]
    # no answer of the last two quizzes again (2026-11-14: Mega Man X came up right after Zero, same collab and season)
    last = {e for t in sorted(quiz, key=lambda t: t["date"])[-2:] for cid in t["ids"] if (e := era(cid))}
    pool = sorted((e, cid) for cid, e in eras.items() if e != newest and e not in last)
    if len(pool) < 3:
        pool = sorted((e, cid) for cid, e in eras.items() if e != newest)
    if len(pool) < 3:
        raise SystemExit(f"STOP: only {len(pool)} usable outfits in today's shop")
    old, new = pool[0], pool[-1]
    middle = [p for p in pool[1:-1] if p[0][0] not in (old[0][0], new[0][0])] or pool[1:-1]
    return [middle[len(middle) // 2][1], old[1], new[1]]


def main(folder, ids):
    os.makedirs(os.path.join(folder, "sources"), exist_ok=True)
    os.makedirs(os.path.join(folder, "img"), exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rounds, files = [], []
    for cid in ids:
        url = f"{API}/{urllib.parse.quote(cid)}?language=es-419"
        raw = get(url)
        d = json.loads(raw)["data"]
        if d["type"]["value"] != "outfit":
            raise SystemExit(f"STOP: {cid} is not an outfit")
        intro = d.get("introduction") or {}
        ch, se = str(intro.get("chapter", "")), str(intro.get("season", ""))
        if not (ch.isdigit() and se.isdigit()):
            raise SystemExit(f"STOP: {d['name']}: no usable season ({intro!r})")
        open(os.path.join(folder, "sources", f"{d['id']}.json"), "wb").write(raw)
        files.append({"file": f"{d['id']}.json", "source": f"Epic cosmetics data for {d['name']} (es-419)", "url": url, "fetched_utc": now})
        art = d["images"].get("featured") or d["images"]["icon"]
        open(os.path.join(folder, "img", f"{d['id']}.png"), "wb").write(get(art))
        rounds.append({"id": d["id"], "name": d["name"].strip(), "rarity": d["rarity"]["value"], "chapter": int(ch), "season": int(se), "intro": intro.get("text", "")})
    json.dump({"via": "fortnite-api.com (mirror of Epic's cosmetics data, es-419)", "files": files}, open(os.path.join(folder, "sources", "manifest.json"), "w"), indent=1, ensure_ascii=False)
    slug = os.path.basename(folder.rstrip("/"))
    caption = ("#EpicPartner Adivina la temporada 👀 Solo los verdaderos OG aciertan las 3\n¿Cuántas acertaste? 👇\n"
               "Usa el código BAD en la tienda de objetos 💛\n#fortnite #fortniteog #adivinalatemporada")
    cfg = {"title": ["Adivina la temporada", f"{len(rounds)} skins · sin trampa"],
           "rounds": [{**{k: r[k] for k in ("id", "name", "rarity", "chapter", "season")}, "img": f"../../tiendabad/video/auto/{slug}/img/{r['id']}.png"} for r in rounds],
           "caption": caption,
           "claims": [{"text": f"{r['name']}: Capítulo {r['chapter']}, Temporada {r['season']}", "source": f"{r['id']}.json",
                       "match": [r["name"], f'"chapter": "{r["chapter"]}"', f'"season": "{r["season"]}"']} for r in rounds]}
    open(os.path.join(folder, "scene.js"), "w").write("/* LA TIENDITA BAD · Adivina la temporada. Facts: Epic's cosmetics data in sources/. */\n"
                                                      "window.SCENE = TPL.guess(" + json.dumps(cfg, indent=1, ensure_ascii=False) + ");\n")
    phrases = [{"text": "¡Adivina la temporada! Solo los verdaderos OG aciertan las tres.", "pause": 0.25}]
    for r in rounds:
        phrases += [{"text": f"{r['name']}.", "pause": 1.7}, {"text": f"¡Capítulo {r['chapter']}, Temporada {r['season']}!", "pause": 0.4}]
    phrases += [{"text": "¿Cuántas acertaste? Dímelo en los comentarios.", "pause": 0.2},
                {"text": "Usa el código BAD en la tienda de objetos. Le ayuda muchísimo al canal.", "say": "Usa el código bad, en la tienda de objetos. Le ayuda muchísimo al canal.", "pause": 0.25},
                {"text": "¡Gracias por ver! Síguenos para más contenido diario.", "pause": 0.3}]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from say import say_names                    # the respellings the shop video and pirate shorts use too (auto/say.py)
    for ph in phrases:
        s = say_names(ph.get("say", ph["text"]))
        if s != ph["text"]:
            ph["say"] = s
    json.dump({"phrases": phrases}, open(os.path.join(folder, "vo.json"), "w"), indent=1, ensure_ascii=False)
    body = "Adivina la temporada 👀 Solo los verdaderos OG aciertan las 3\n¿Cuántas acertaste? 👇\nUsa el código BAD en la tienda de objetos 💛"
    json.dump({"tiktok": f"#EpicPartner {body}\n#fortnite #fortniteog #adivinalatemporada", "tiktokTitle": "Adivina la temporada 👀",
               "instagram": f"#EpicPartner {body}\n#fortnite #fortniteog #adivinalatemporada", "facebook": f"#EpicPartner {body}",
               "facebookTitle": "Adivina la temporada", "youtube": f"#EpicPartner {body}\n#Fortnite #FortniteOG #Shorts",
               "youtubeTitle": "Adivina la TEMPORADA de estas skins de Fortnite 👀 #Shorts", "threads": f"#EpicPartner {body}\n#fortnite #fortniteog",
               "tags": ["fortnite", "fortnite og", "adivina la temporada", "quiz fortnite", "skins fortnite", "fortnite español"], "cover_t": 0.6},
              open(os.path.join(folder, "post.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps({"ids": [r["id"] for r in rounds], "rounds": [(r["name"], r["chapter"], r["season"]) for r in rounds]}, ensure_ascii=False))


if __name__ == "__main__":
    args = sys.argv[2:]
    if args == ["auto"]:
        args = pick_auto()
    if len(sys.argv) < 3 or len(args) != 3:     # the voice and caption say "las tres"
        raise SystemExit(__doc__)
    main(os.path.abspath(sys.argv[1]), args)
