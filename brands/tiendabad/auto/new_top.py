"""LA TIENDITA BAD: a "Top 5" countdown short (TPL.top) from today's item shop, straight from Epic's data (es-419).

    python3 brands/tiendabad/auto/new_top.py <folder> regresos   the 5 outfits in today's shop that took longest to come back
    python3 brands/tiendabad/auto/new_top.py <folder> og         the 5 outfits in today's shop that first hit the shop longest ago

Ties stay in (the voice says "¡También…!"), so the five are really the top five. Writes <folder>/:
sources/<id>.json (Epic's cosmetics data with its shop history), sources/shop-entries.json (today's listings),
sources/manifest.json, img/<id>.png, scene.js, vo.json, post.json. Then build and ship with make_short.py,
passing --sources <folder>/sources. Run it after the 6 PM (CDMX) reset: the video is about that day's shop.
"""
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://fortnite-api.com"
LANG = "es-419"
MES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
MES3 = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
NUM = {1: "uno", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco"}


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


def spoken(s):
    return s.replace("$", "S")


def main(folder, mode):
    if mode not in ("regresos", "og"):
        raise SystemExit(__doc__)
    os.makedirs(os.path.join(folder, "sources"), exist_ok=True)
    os.makedirs(os.path.join(folder, "img"), exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    shop_raw = json.loads(get(f"{API}/v2/shop?language={LANG}"))
    shop = shop_raw["data"]
    day = shop["date"][:10]
    today = datetime.date.fromisoformat(day)
    listing = {}
    for e in shop["entries"]:
        for i in e.get("brItems") or []:
            if i["type"]["value"] == "outfit" and i["id"] not in listing:
                listing[i["id"]] = e
    rows = []
    for cid in listing:
        raw = get(f"{API}/v2/cosmetics/br/{urllib.parse.quote(cid)}?language={LANG}&responseFlags=4")
        d = json.loads(raw)["data"]
        seen = sorted({x[:10] for x in (d.get("shopHistory") or [])})
        before = [x for x in seen if x < day]
        intro = d.get("introduction") or {}
        rows.append({"id": cid, "raw": raw, "name": d["name"].strip(), "rarity": d["rarity"]["value"], "set": (d.get("set") or {}).get("value") or cid,
                     "img": d["images"].get("featured") or d["images"]["icon"], "first": seen[0] if seen else None, "last": before[-1] if before else None,
                     "gap": (today - datetime.date.fromisoformat(before[-1])).days if before else None, "apps": len(before),
                     "chapter": str(intro.get("chapter", "")), "season": str(intro.get("season", "")), "out": listing[cid]["outDate"][:10]})
    if mode == "regresos":
        pool = sorted([r for r in rows if r["gap"]], key=lambda r: (-r["gap"], r["apps"], r["name"]))
    else:
        pool = sorted([r for r in rows if r["first"] and r["chapter"].isdigit() and r["season"].isdigit()], key=lambda r: (r["first"], r["name"]))
    pick = pool[:5]
    if len(pick) < 5:
        raise SystemExit(f"STOP: only {len(pick)} usable outfits in today's shop")
    pick.reverse()                                       # countdown order: #5 first
    files = []
    for r in pick:
        open(os.path.join(folder, "sources", f"{r['id']}.json"), "wb").write(r["raw"])
        files.append({"file": f"{r['id']}.json", "source": f"Epic's cosmetics data for {r['name']} with its shop history (es-419)",
                      "url": f"{API}/v2/cosmetics/br/{r['id']}?language={LANG}&responseFlags=4", "fetched_utc": now})
        open(os.path.join(folder, "img", f"{r['id']}.png"), "wb").write(get(r["img"]))
    json.dump({"date": shop["date"], "entries": [listing[r["id"]] for r in pick]}, open(os.path.join(folder, "sources", "shop-entries.json"), "w"), ensure_ascii=False, indent=1)
    files.append({"file": "shop-entries.json", "source": f"Epic's item shop for {day}: the listings of these outfits (es-419)", "url": f"{API}/v2/shop?language={LANG}", "fetched_utc": now})
    json.dump({"via": "fortnite-api.com (mirror of Epic's item shop and cosmetics data, es-419)", "files": files}, open(os.path.join(folder, "sources", "manifest.json"), "w"), indent=1, ensure_ascii=False)

    slug = os.path.basename(folder.rstrip("/"))
    img = lambda r: f"../../tiendabad/video/{os.path.relpath(folder, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'video'))}/img/{r['id']}.png"
    ph = []

    def say(text, pause=0.2, sound=None):
        ph.append({"text": text, "pause": pause, **({"say": sound} if sound else {})})
        return len(ph) - 1

    date_es = lambda s: f"{int(s[8:10])} de {MES[int(s[5:7]) - 1]} de {s[:4]}"
    short = lambda s: f"{int(s[8:10])} {MES3[int(s[5:7]) - 1]} {s[:4]}"
    items, claims = [], []
    if mode == "regresos":
        hook = {"lines": ["la tienda de hoy:", "los regresos más esperados ⏳"], "tag": "¿cuántos días? 👀"}
        say("Las cinco skins de la tienda de hoy que más tardaron en volver.", 0.15)
    else:
        hook = {"lines": ["la tienda de hoy:", "las skins más OG 🧓"], "tag": "¿de qué año? 👀"}
        say("Las cinco skins más OG de la tienda de hoy.", 0.15)
    hook["tag2"] = {"p": 0, "dt": 1.8, "text": "espera al #1 👀"}
    tease = None
    for k, r in enumerate(pick):
        rank = 5 - k
        if rank == 1:
            tease = {"p": say("¿Y el número uno?", 0.6), "title": "y el #1 es… 🥁", "text": "la más esperada 👀" if mode == "regresos" else "la más OG 👀"}
        line = f"¡{r['name']}!" if tease and rank == 1 else f"Número {NUM[rank]}: {r['name']}."
        p = say(line, 0.15, spoken(line) if "$" in line else None)
        prev = pick[k - 1] if k else None
        it = {"rank": rank, "label": r["name"], "img": img(r), "rarity": r["rarity"], "p": p, "recap": "", "flag": "👑" if rank == 1 else None}
        if mode == "regresos":
            c = say("Volvió después de…", 0.5)
            v = say(f"¡También {r['gap']} días!" if prev and prev["gap"] == r["gap"] else f"¡{r['gap']} días!", 0.2)
            x = {"p": say(f"La última vez fue el {date_es(r['last'])}.", 0.15)} if rank == 1 else {"p": v, "dt": 0.35}
            it.update(cap={"p": c, "text": "volvió a la tienda después de"},
                      box={"hidden": "??? días", "count": [{"p": c, "dt": 0.45, "text": f"{max(1, r['gap'] * 3 // 10)} días"}, {"p": c, "dt": 0.9, "text": f"{max(2, r['gap'] * 7 // 10)} días"}],
                           "reveal": {"p": v, "text": f"{r['gap']} días"}},
                      extras=[{**x, "text": f"la última vez: {short(r['last'])}"}], recap=f"#{rank} · {r['gap']} días")
            if r["out"] == day:
                it["sticker"] = {"p": say("¡Y se va hoy!", 0.65), "text": "¡y se va hoy! ⏰"}
            claims.append({"text": f"{r['name']}: volvió a la tienda después de {r['gap']} días (la última vez: {short(r['last'])})", "source": f"{r['id']}.json", "match": [r["name"], f'"{r["last"]}']})
        else:
            years = (today - datetime.date.fromisoformat(r["first"])).days // 365
            c = say("Llegó a la tienda en…", 0.5)
            mes = f"{MES[int(r['first'][5:7]) - 1]} de {r['first'][:4]}"
            v = say(f"¡También en {mes}!" if prev and prev["first"][:7] == r["first"][:7] else f"¡{mes.capitalize()}!", 0.2)
            x = {"p": say(f"Capítulo {r['chapter']}, temporada {r['season']}.", 0.15)} if rank == 1 else {"p": v, "dt": 0.35}
            it.update(cap={"p": c, "text": "llegó a la tienda en"}, box={"hidden": "??? 20??", "reveal": {"p": v, "text": f"{MES3[int(r['first'][5:7]) - 1]} {r['first'][:4]}"}},
                      extras=[{**x, "text": f"Capítulo {r['chapter']}, Temporada {r['season']}"}], recap=f"#{rank} · {r['first'][:4]}")
            if rank == 1 and years >= 2:
                it["sticker"] = {"p": say(f"¡Hace {years} años!", 0.65), "text": f"¡hace {years} años! 😳"}
            claims.append({"text": f"{r['name']}: llegó a la tienda en {MES3[int(r['first'][5:7]) - 1]} {r['first'][:4]}; Capítulo {r['chapter']}, Temporada {r['season']}", "source": f"{r['id']}.json",
                           "match": [r["name"], f'"{r["first"]}', f'"chapter": "{r["chapter"]}"', f'"season": "{r["season"]}"']})
        claims.append({"text": f"{r['name']} está en la tienda de hoy ({day})", "source": "shop-entries.json", "match": [r["name"], f'"date": "{shop["date"]}"']})
        items.append({k2: v2 for k2, v2 in it.items() if v2 is not None})
    recap = {"p": say("¿Cuál te llevas? Dímelo abajo." if mode == "regresos" else "¿Cuál es tu favorita? Dímelo abajo.", 0.2),
             "title": "¿cuál te llevas? 👇" if mode == "regresos" else "¿cuál es tu favorita? 👇"}
    say("Usa el código BAD en la tienda de objetos.", 0.25, "Usa el código bad, en la tienda de objetos.")
    say("¡Gracias por ver! Síguenos para más contenido diario.", 0.3)
    one = pick[-1]
    if mode == "regresos":
        body = (f"Las 5 skins de la tienda de hoy que más tardaron en volver ⏳ La #1 volvió después de {one['gap']} días 😳\n"
                "¿Cuál te llevas? 👇\nUsa el código BAD en la tienda de objetos 💛")
        tags, yt, ytitle, ttitle = "#fortnite #tiendafortnite #skinsfortnite", "#Fortnite #TiendaFortnite #Shorts", "Las skins de Fortnite que MÁS tardaron en volver a la tienda ⏳ #Shorts", "Los regresos más esperados ⏳"
    else:
        body = (f"Las 5 skins más OG de la tienda de hoy 🧓 La #1 llegó a la tienda en {MES[int(one['first'][5:7]) - 1]} de {one['first'][:4]} 😳\n"
                "¿Cuál es tu favorita? 👇\nUsa el código BAD en la tienda de objetos 💛")
        tags, yt, ytitle, ttitle = "#fortnite #fortniteog #tiendafortnite", "#Fortnite #FortniteOG #Shorts", "Las skins MÁS OG de la tienda de Fortnite de hoy 🧓 #Shorts", "Las skins más OG de hoy 🧓"
    cfg = {"hook": hook, "items": items, "tease": tease, "recap": recap, "caption": f"#EpicPartner {body}\n{tags}", "claims": claims}
    open(os.path.join(folder, "scene.js"), "w").write(f"/* LA TIENDITA BAD · Top 5 ({mode}) from the {day} item shop. Facts: sources/. */\n"
                                                      "window.SCENE = TPL.top(" + json.dumps(cfg, indent=1, ensure_ascii=False) + ");\n")
    json.dump({"phrases": ph}, open(os.path.join(folder, "vo.json"), "w"), indent=1, ensure_ascii=False)
    yt_tags = ["fortnite", "tienda fortnite", "tienda de objetos fortnite", "skins fortnite", "fortnite español"]
    json.dump({"tiktok": f"#EpicPartner {body}\n{tags}", "tiktokTitle": ttitle, "instagram": f"#EpicPartner {body}\n{tags}", "facebook": f"#EpicPartner {body}",
               "facebookTitle": ttitle.rstrip(" ⏳🧓"), "youtube": f"#EpicPartner {body}\n{yt}",
               "youtubeTitle": ytitle, "threads": f"#EpicPartner {body}\n" + " ".join(tags.split()[:2]), "tags": yt_tags, "cover_t": 1.4},
              open(os.path.join(folder, "post.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps({"day": day, "mode": mode, "top": [(5 - k, r["name"], r["gap"] if mode == "regresos" else r["first"]) for k, r in enumerate(pick)],
                      "ids": [r["id"] for r in pick]}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(os.path.abspath(sys.argv[1]), sys.argv[2])
