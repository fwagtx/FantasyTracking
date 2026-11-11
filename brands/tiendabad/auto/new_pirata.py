"""LA TIENDITA BAD: the daily Tienda Pirata trio (owner, 2026-11-11: "make about 3 extra videos for today and everyday
until it gets removed"). Facts come from Epic's live item shop (es-419) and from that day's press sources folder
(video/auto/pirata-sources-<D>/: the articles quoting Epic, re-fetched live with Firecrawl, passages verbatim).

    python3 brands/tiendabad/auto/new_pirata.py <folder> sevan  <pirata-sources-dir>   what leaves today's shop at 6 PM (TPL.drop)
    python3 brands/tiendabad/auto/new_pirata.py <folder> caros  <pirata-sources-dir>   the 5 most expensive listings today (TPL.top)
    python3 brands/tiendabad/auto/new_pirata.py <folder> cuenta <pirata-sources-dir>   "quedan N días" + the dato del día (TPL.guide)

sevan and caros are about the shop that is live now, so they must post before the 6 PM (CDMX) reset. Each writes
<folder>/: sources/ (the files its claims check, with manifest.json), img/ (shop modes), scene.js, vo.json, post.json, and
prints a one-line topic for the ledger. Then build and ship with make_short.py, passing --sources <folder>/sources.
The hunt ends December 5 (VGC citing Epic): after that day this script refuses to run.
"""
import datetime
import json
import os
import shutil
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

API = "https://fortnite-api.com/v2/shop?language=es-419"
MX = ZoneInfo("America/Mexico_City")
END = datetime.date(2026, 12, 5)
START = datetime.date(2026, 11, 11)            # first day of the daily trio
MES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
NUM = {1: "uno", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco"}
TREASURE_HOURS = [datetime.date(2026, 11, 14), datetime.date(2026, 11, 21), datetime.date(2026, 11, 28), datetime.date(2026, 12, 5)]
TH_ROW = {datetime.date(2026, 11, 14): "November 14", datetime.date(2026, 11, 21): "November 21", datetime.date(2026, 11, 28): "November 28", datetime.date(2026, 12, 5): "December 5"}
TYPES = {"outfit": ("skin", "skins"), "backpack": ("mochila", "mochilas"), "pickaxe": ("pico", "picos"), "glider": ("planeador", "planeadores"),
         "emote": ("gesto", "gestos"), "wrap": ("envoltorio", "envoltorios"), "shoe": ("calzado", "calzados"), "sidekick": ("sidekick", "sidekicks"),
         "contrail": ("estela", "estelas"), "loadingscreen": ("pantalla de carga", "pantallas de carga"), "spray": ("grafiti", "grafitis"), "emoji": ("emoticono", "emoticonos")}
CODE = ("Usa el código BAD en la tienda de objetos.", "Usa el código bad, en la tienda de objetos.")
THANKS = "¡Gracias por ver! Síguenos para más contenido diario."
FREE = {"text": "Con la Tienda Pirata, todo lo que cuesta pavos es gratis por 14 días", "source": "vgc-odds-confirmed.json",
        "match": ["every single item in the Fortnite Shop which costs V-Bucks", "becomes entirely free to that player for the next 14 days"]}

# the dato del día, one per day in this order (Fridays and Saturdays get the Treasure Hours instead); each fact is used once
POOL = [
    ("th_next", None, None),   # this Saturday's Treasure Hours (built below)
    ("DOS FICHAS", "Una para 2026 y otra para 2027", ["Si te sale, recibes dos fichas: una para 2026 y otra para todo 2027."],
     {"source": "vgc-odds-confirmed.json", "match": ["Players who find it receive two tokens", "between January 1 and December 31"]}),
    ("EN ESCUADRÓN", "Si uno la encuentra, la reciben todos", ["Si alguien de tu escuadrón la encuentra, ¡la reciben todos!"],
     {"source": "vgc-guide.json", "match": ["if anyone in your Squad finds the Pirate Shop Token in a chest, every member of the Squad gets it"]}),
    ("CUALQUIER COFRE", "Normales, raros, enterrados…", ["Sirve cualquier cofre: normales, raros, enterrados… ¡todos!"],
     {"source": "vgc-guide.json", "match": ["This can be any type of chest", "Normal chests", "Rare chests", "Buried Chests"]}),
    ("IGUAL PARA TODOS", "Tu skin no cambia nada", ["La probabilidad es igual para todos: tu skin no cambia nada."],
     {"source": "vgc-guide.json", "match": ["everyone has an equal chance of finding a Pirate Shop Token", "the skin you choose has no effect on your odds"]}),
    ("NO SE RECOGE", "Te sale un mensaje en pantalla", ["El premio no sale del cofre: te aparece un mensaje en la pantalla."],
     {"source": "vgc-guide.json", "match": ["you won’t have to pick it up", "you’ll just get a message on the screen"]}),
    ("MODOS", "Battle Royale y Cero Construcción", ["Cuenta en Battle Royale y en Cero Construcción."],
     {"source": "vgc-guide.json", "match": ["open chests during Battle Royale and Zero Build modes"]}),
    ("MÁS RARA AÚN", "Skin dorada: 1 en 1 562 500", ["La skin dorada de la semana es todavía más rara: 1 de cada 1 562 500 cofres."],
     {"source": "vgc-odds-confirmed.json", "match": ["Weekly Gold Outfit – 1 in 1,562,500"]},
     ["La skin dorada de la semana es todavía más rara: uno de cada un millón quinientos sesenta y dos mil quinientos cofres."]),
    ("NO INCLUYE", "Pase, dinero real ni regalos", ["No incluye niveles del pase, lotes de dinero real ni regalos, y solo un sidekick."],
     {"source": "vgc-guide.json", "match": ["you can’t claim Battle Pass levels", "you can’t get bundles that cost real money", "you can’t gift items to other players", "you can only claim one free sidekick"]}),
    ("NO HA CAMBIADO", "Misma probabilidad desde el inicio", ["Epic dice que la probabilidad no ha cambiado desde que empezó la temporada."],
     {"source": "vice-treasure-hours.json", "match": ["the normal chest drop rates have stayed the same since Fortnite Treasure Hunters launched"]}),
    ("HASTA ×2", "Una vez subió al doble", ["En unas Horas del Tesoro, la probabilidad llegó a subir al doble."],
     {"source": "vgc-odds-confirmed.json", "match": ["during a recent Treasure Hour, Pirate Shop Access was boosted to 2x"]}),
    ("14 DÍAS", "Todo lo que cuesta pavos, gratis", ["Cuando la activas, todo lo que cuesta pavos es gratis para ti por 14 días."],
     {"source": "vgc-odds-confirmed.json", "match": ["becomes entirely free to that player for the next 14 days"]}),
    ("ACTÍVALA", "La de 2026, antes del 15 de diciembre", ["La ficha de 2026 la puedes activar hasta el 15 de diciembre."],
     {"source": "vgc-odds-confirmed.json", "match": ["activated at any point in 2026 between November 1 and December 15"]}),
    ("POR COFRE", "Un 0.00073 % por cofre", ["Eso es más o menos un 0.00073 por ciento por cofre."],
     {"source": "vice-treasure-hours.json", "match": ["That works out to about 0.00073% per chest"]},
     ["Eso es más o menos un cero punto cero cero cero siete tres por ciento por cofre."]),
    ("PREMIO SUPREMO", "La skin dorada o la Tienda Pirata", ["Los premios supremos son solo dos: la skin dorada de la semana o la Tienda Pirata."],
     {"source": "vgc-guide.json", "match": ["And if it’s Supreme, you’ll get one of two things", "or the Pirate Shop Token"]}),
    ("SIN AMIGOS", "Usa rellenar escuadrón", ["¿No tienes con quién jugar? Elige rellenar escuadrón."],
     {"source": "vgc-guide.json", "match": ["just choose the Fill option to add random players to your squad of four"]}),
    ("TRES NIVELES", "Estándar, mayor y supremo", ["Los premios del tesoro vienen en tres niveles: estándar, mayor y supremo."],
     {"source": "vgc-guide.json", "match": ["Treasure Hunt prizes are split into three tiers – Standard, Major and Supreme"]}),
]


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
    """How a shop name is said: $ is S, and all-caps words are read as words, not letters."""
    return " ".join(w.capitalize() if w.isupper() and len(w) > 2 else w for w in s.replace("$", "S").split(" "))


def name_of(e):
    return ((e.get("bundle") or {}).get("name") or ((e.get("brItems") or [{}])[0].get("name")) or ((e.get("cars") or [{}])[0].get("name"))
            or ((e.get("tracks") or [{}])[0].get("title")) or ((e.get("instruments") or [{}])[0].get("name")) or "").strip()


def art_of(e):
    if (e.get("bundle") or {}).get("image"):
        return e["bundle"]["image"]
    for k in ("brItems", "cars", "instruments", "legoKits"):
        for i in e.get(k) or []:
            im = i.get("images") or {}
            if im.get("featured") or im.get("icon") or im.get("large"):
                return im.get("featured") or im.get("icon") or im.get("large")
    for r in (e.get("newDisplayAsset") or {}).get("renderImages") or []:
        return r.get("image")
    return None


def rarity_of(e):
    items = e.get("brItems") or []
    outfits = [i for i in items if i["type"]["value"] == "outfit"]
    return ((outfits or items or [{}])[0].get("rarity") or {}).get("value", "epic")


def copy_sources(folder, src_dir, names, extra_files):
    """The pirate passages this video's claims use, copied with their fetch time, plus the shop files written here."""
    man = json.load(open(os.path.join(src_dir, "manifest.json")))
    files = [f for f in man["files"] if f["file"] in names]
    if len(files) != len(set(names)):
        raise SystemExit(f"STOP: {src_dir} is missing one of {sorted(set(names))}")
    for f in files:
        shutil.copy(os.path.join(src_dir, f["file"]), os.path.join(folder, "sources", f["file"]))
    json.dump({"via": man["via"] + " Plus Epic's item shop (fortnite-api.com mirror, es-419) where used.", "files": files + extra_files},
              open(os.path.join(folder, "sources", "manifest.json"), "w"), indent=1, ensure_ascii=False)


def today_mx():
    return datetime.datetime.now(MX).date()


def shop_now():
    raw = get(API)
    shop = json.loads(raw)["data"]
    return shop, shop["date"][:10]


def write(folder, kind, cfg, phrases, post, topic):
    for ph in phrases:
        if "$" in ph["text"] and "say" not in ph:
            ph["say"] = ph["text"].replace("$", "S")
    open(os.path.join(folder, "scene.js"), "w").write(f"/* LA TIENDITA BAD · Tienda Pirata diaria ({kind}). Facts: sources/. */\n"
                                                      f"window.SCENE = TPL.{ {'sevan': 'drop', 'caros': 'top', 'cuenta': 'guide'}[kind] }(" + json.dumps(cfg, indent=1, ensure_ascii=False) + ");\n")
    json.dump({"phrases": phrases}, open(os.path.join(folder, "vo.json"), "w"), indent=1, ensure_ascii=False)
    json.dump(post, open(os.path.join(folder, "post.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps({"kind": kind, "topic": topic}, ensure_ascii=False))


def posts(body, tags3, yt_tags_line, ytitle, ttitle, ftitle, tags):
    return {"tiktok": f"#EpicPartner {body}\n{tags3}", "tiktokTitle": ttitle, "instagram": f"#EpicPartner {body}\n{tags3}", "facebook": f"#EpicPartner {body}",
            "facebookTitle": ftitle, "youtube": f"#EpicPartner {body}\n{yt_tags_line}", "youtubeTitle": ytitle,
            "threads": f"#EpicPartner {body}\n" + " ".join(tags3.split()[:2]), "tags": tags, "cover_t": 1.2}


def img_rel(folder, name):
    video = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "video")
    return f"../../tiendabad/video/{os.path.relpath(folder, video)}/img/{name}"


# ---------------- what leaves the shop today (TPL.drop) ----------------
def sevan(folder, src_dir):
    shop, day = shop_now()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if today_mx().isoformat() != day:
        raise SystemExit(f"STOP: the live shop is {day}, but today in CDMX is {today_mx()}; run before the 6 PM reset")
    leaving = [e for e in shop["entries"] if e["outDate"][:10] == day and e.get("brItems") and e["finalPrice"] > 0]
    if not leaving:
        raise SystemExit("STOP: nothing with items leaves the shop today")
    e = sorted(leaving, key=lambda x: (-x["regularPrice"], -x["finalPrice"], name_of(x)))[0]
    name = name_of(e)
    items = e["brItems"]
    order = {"outfit": 0, "backpack": 1, "pickaxe": 2, "glider": 3, "emote": 4}
    shown = sorted(items, key=lambda i: (order.get(i["type"]["value"], 9), i["name"]))[:3]
    cards = []
    for i in shown:
        art = (i.get("images") or {}).get("featured") or (i.get("images") or {}).get("icon")
        open(os.path.join(folder, "img", f"{i['id']}.png"), "wb").write(get(art))
        cards.append({"name": i["name"], "img": img_rel(folder, f"{i['id']}.png"), "rarity": i["rarity"]["value"]})
    counts = {}
    for i in items:
        counts[i["type"]["value"]] = counts.get(i["type"]["value"], 0) + 1
    parts = [(f"{n} {TYPES[t][1]}" if n > 1 else TYPES[t][0]) for t, n in sorted(counts.items(), key=lambda kv: order.get(kv[0], 9)) if t in TYPES]
    contents = (", ".join(parts[:-1]) + " y " + parts[-1]) if len(parts) > 1 else (parts[0] if parts else "")
    json.dump({"date": shop["date"], "entries": [e]}, open(os.path.join(folder, "sources", "shop-entry.json"), "w"), indent=1, ensure_ascii=False)
    copy_sources(folder, src_dir, ["vgc-odds-confirmed.json"], [{"file": "shop-entry.json", "source": f"Epic's item shop for {day}: this listing (es-419)", "url": API, "fetched_utc": now}])
    f, r = e["finalPrice"], e["regularPrice"]
    ph = [{"text": "¿Y si hoy te sale la Tienda Pirata? Esto se va hoy de la tienda.", "pause": 0.2},
          {"text": f"El {name}." if not name.lower().startswith(("el ", "la ", "los ", "las ")) else f"{name}.", "pause": 0.2},
          {"text": f"Trae {contents}.", "pause": 0.2},
          {"text": f"Cuesta {f} pavos" + (f", y normalmente {r}." if r > f else "."), "pause": 0.2},
          {"text": "Y se va hoy a las 6 de la tarde, hora de la Ciudad de México.", "pause": 0.2},
          {"text": "Con la Tienda Pirata, te lo llevarías sin gastar ni un pavo. ¿Te lo llevarías?", "pause": 0.25},
          {"text": CODE[0], "say": CODE[1], "pause": 0.25},
          {"text": THANKS, "pause": 0.3}]
    sp = spoken(name)
    if sp != name:
        ph[1]["say"] = ph[1]["text"].replace(name, sp)
    body = f"Se va HOY de la tienda ⏰ {name}: {f} pavos" + (f" (normalmente {r})" if r > f else "") + "… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛"
    cfg = {"title": ["¡Se va HOY! ⏰", "¿y la Tienda Pirata? 🏴‍☠️"], "cards": cards, "name": name, "contents": contents[0].upper() + contents[1:],
           "price": {"final": f, "regular": r}, "when": "se va hoy · 6 PM (CDMX)", "tone": "last", "ask": ["¿Te lo", "llevarías?"],
           "caption": f"#EpicPartner {body}\n#fortnite #tiendapirata #tiendafortnite",
           "claims": [{"text": f"{name}: {f} pavos (normalmente {r}) en la tienda de hoy ({day})", "source": "shop-entry.json",
                       "match": [name, f'"finalPrice": {f}', f'"regularPrice": {r}', f'"date": "{shop["date"]}"']},
                      {"text": f"Se va hoy a las 6 PM (CDMX): outDate {e['outDate']}", "source": "shop-entry.json", "match": [f'"outDate": "{e["outDate"]}"'],
                       "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"},
                      {"text": f"Trae {contents}", "source": "shop-entry.json", "match": [i["name"] for i in items]}, FREE]}
    write(folder, "sevan", cfg, ph, posts(body, "#fortnite #tiendapirata #tiendafortnite", "#Fortnite #TiendaPirata #Shorts",
                                          f"Se va HOY de la tienda de Fortnite ⏰ {name} #Shorts"[:100], "Se va HOY ⏰", f"Se va hoy: {name}"[:90],
                                          ["fortnite", "tienda fortnite", "tienda pirata", "tienda fortnite hoy", "fortnite español"]),
          f"Se va hoy ({day}): {name}, {f} pavos (normalmente {r})")


# ---------------- the 5 most expensive listings today (TPL.top) ----------------
def caros(folder, src_dir):
    shop, day = shop_now()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if today_mx().isoformat() != day:
        raise SystemExit(f"STOP: the live shop is {day}, but today in CDMX is {today_mx()}; run before the 6 PM reset")
    pool = sorted([e for e in shop["entries"] if e["finalPrice"] > 0 and name_of(e) and art_of(e)], key=lambda x: (-x["finalPrice"], -x["regularPrice"], name_of(x)))
    seen, ranked = set(), []
    for e in pool:                                   # one listing per name
        if name_of(e) not in seen:
            seen.add(name_of(e))
            ranked.append(e)
    pick, nxt = ranked[:5], ranked[5] if len(ranked) > 5 else None
    if len(pick) < 5:
        raise SystemExit("STOP: fewer than 5 priced listings in today's shop")
    sidekick = any(i["type"]["value"] == "sidekick" for e in pick for i in (e.get("brItems") or []))
    keep = pick + ([nxt] if nxt and nxt["finalPrice"] == pick[-1]["finalPrice"] else [])
    for k, e in enumerate(pick):
        open(os.path.join(folder, "img", f"top{k + 1}.png"), "wb").write(get(art_of(e)))
    json.dump({"date": shop["date"], "entries": keep}, open(os.path.join(folder, "sources", "shop-entries.json"), "w"), indent=1, ensure_ascii=False)
    copy_sources(folder, src_dir, ["vgc-odds-confirmed.json"] + (["vgc-guide.json"] if sidekick else []),
                 [{"file": "shop-entries.json", "source": f"Epic's item shop for {day}: these listings (es-419)", "url": API, "fetched_utc": now}])
    order = list(reversed(pick))                      # countdown: #5 first
    ph = []

    def say(text, pause=0.2, sound=None):
        ph.append({"text": text, "pause": pause, **({"say": sound} if sound else {})})
        return len(ph) - 1

    say("Lo más caro de la tienda de hoy… ¡y con la Tienda Pirata te lo llevarías sin gastar ni un pavo!", 0.15)
    hook = {"lines": ["la tienda de hoy:", "lo más CARO 💸"], "tag": "¿cuánto cuesta? 👀", "tag2": {"p": 0, "dt": 1.8, "text": "¿y con la Tienda Pirata? 🏴‍☠️"}}
    items, claims, tease = [], [], None
    for k, e in enumerate(order):
        rank, name, f, r = 5 - k, name_of(e), e["finalPrice"], e["regularPrice"]
        if rank == 1:
            tease = {"p": say("¿Y el número uno?", 0.6), "title": "y el #1 es… 🥁", "text": "lo más caro de hoy 💸"}
        line = f"¡{name}!" if rank == 1 else f"Número {NUM[rank]}: {name}."
        p = say(line, 0.15, line.replace(name, spoken(name)) if spoken(name) != name else None)
        prev = order[k - 1] if k else None
        c = say("Cuesta…", 0.5)
        v = say(f"¡También {f} pavos!" if prev and prev["finalPrice"] == f else f"¡{f} pavos!", 0.2)
        it = {"rank": rank, "label": name, "img": img_rel(folder, f"top{rank}.png"), "rarity": rarity_of(e), "p": p, "kind": "skin",
              "cap": {"p": c, "text": "cuesta"},
              "box": {"hidden": "???? pavos", "count": [{"p": c, "dt": 0.45, "text": f"{max(100, f * 3 // 1000 * 100)} pavos"}, {"p": c, "dt": 0.9, "text": f"{max(200, f * 7 // 1000 * 100)} pavos"}],
                      "reveal": {"p": v, "text": f"{f} pavos"}},
              "recap": f"#{rank} · {f}"}
        extras = []
        if rank == 5 and nxt and nxt["finalPrice"] == f:
            n6 = name_of(nxt)
            extras.append({"p": say(f"Y empatado: {n6}.", 0.15, f"Y empatado: {spoken(n6)}." if spoken(n6) != n6 else None), "text": f"empata con {n6}"})
            claims.append({"text": f"{n6} también cuesta {f} pavos hoy", "source": "shop-entries.json", "match": [n6, f'"finalPrice": {f}']})
        if rank == 1 and r > f:
            extras.append({"p": say(f"Y su precio normal es de {r}.", 0.15), "text": f"precio normal: {r}"})
            claims.append({"text": f"{name}: precio normal {r} pavos", "source": "shop-entries.json", "match": [name, f'"regularPrice": {r}']})
        if extras:
            it["extras"] = extras
        if rank == 1:
            it["sticker"] = {"p": say("¡Con la Tienda Pirata: cero pavos!", 0.65), "text": "con la Tienda Pirata: 0 pavos 🏴‍☠️"}
            it["flag"] = "🏴‍☠️"
        claims.append({"text": f"{name}: {f} pavos en la tienda de hoy ({day})", "source": "shop-entries.json", "match": [name, f'"finalPrice": {f}', f'"date": "{shop["date"]}"']})
        items.append(it)
    recap_line = "Con la Tienda Pirata, todo lo que cuesta pavos es gratis por 14 días" + (", y solo puedes reclamar un sidekick." if sidekick else ".") + " ¿Cuál te llevarías?"
    recap = {"p": say(recap_line, 0.2), "title": "¿cuál te llevarías? 👇"}
    claims.append(FREE)
    if sidekick:
        claims.append({"text": "Solo puedes reclamar un sidekick", "source": "vgc-guide.json", "match": ["you can only claim one free sidekick"]})
    say(*CODE[:1], 0.25, CODE[1])
    say(THANKS, 0.3)
    one = pick[0]
    body = (f"Lo más caro de la tienda de hoy 💸 El #1: {name_of(one)}, {one['finalPrice']} pavos… y con la Tienda Pirata te lo llevarías sin gastar ni un pavo 🏴‍☠️\n"
            "¿Cuál te llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛")
    cfg = {"hook": hook, "items": items, "tease": tease, "recap": recap, "caption": f"#EpicPartner {body}\n#fortnite #tiendapirata #tiendafortnite", "claims": claims}
    write(folder, "caros", cfg, ph, posts(body, "#fortnite #tiendapirata #tiendafortnite", "#Fortnite #TiendaPirata #Shorts",
                                          "Lo MÁS CARO de la tienda de Fortnite HOY 💸 ¿y con la Tienda Pirata? #Shorts", "Lo más caro de hoy 💸", "Lo más caro de la tienda de hoy",
                                          ["fortnite", "tienda fortnite", "tienda pirata", "tienda fortnite hoy", "fortnite español"]),
          f"Lo más caro de hoy ({day}): " + ", ".join(f"{name_of(e)} {e['finalPrice']}" for e in pick))


# ---------------- quedan N días + dato del día (TPL.guide) ----------------
def cuenta(folder, src_dir, day=None):
    d = day or today_mx()
    if d > END:
        raise SystemExit("STOP: the treasure hunt ended on December 5; the daily Tienda Pirata posts are over")
    left = (END - d).days
    sat = d.weekday() == 5 and d in TREASURE_HOURS
    fri = d.weekday() == 4 and (d + datetime.timedelta(days=1)) in TREASURE_HOURS
    ph, facts, claims = [], [], []
    quedan = "Último día" if left == 0 else ("Queda 1 día" if left == 1 else f"Quedan {left} días")

    def say(text, pause=0.25, sound=None):
        ph.append({"text": text, "pause": pause, **({"say": sound} if sound else {})})
        return len(ph) - 1

    if left == 0:
        say("¡Hoy es el último día para encontrar la Tienda Pirata!")
        title, coin = ["¡Último día!", "Tienda Pirata 🏴‍☠️"], ["HOY", "ÚLTIMO DÍA"]
    elif left == 1:
        say("¡Mañana es el último día para encontrar la Tienda Pirata!")
        title, coin = ["¡Queda 1 día!", "para encontrarla 🏴‍☠️"], ["QUEDA", "1 DÍA"]
    else:
        say(f"¡Quedan {left} días para encontrar la Tienda Pirata!")
        title, coin = [f"¡Quedan {left} días!", "para encontrarla 🏴‍☠️"], ["QUEDAN", f"{left} DÍAS"]
    facts.append({"p": say("La búsqueda del tesoro termina el 5 de diciembre."), "label": "TERMINA", "text": "5 de diciembre"})
    claims.append({"text": f"La búsqueda termina el 5 de diciembre (hoy {d.isoformat()}: quedan {left} días)", "source": "vgc-odds-confirmed.json",
                   "match": ["is set to end on December 5"], "visual": f"{d.isoformat()} to 2026-12-05 is {left} days"})
    facts.append({"p": say("Y recuerda: sale en 1 de cada 137 500 cofres.", 0.25, "Y recuerda: sale en uno de cada ciento treinta y siete mil quinientos cofres."),
                  "label": "PROBABILIDAD", "text": "1 en 137 500 por cofre"})
    claims.append({"text": "1 en 137 500 por cofre", "source": "vgc-odds-confirmed.json", "match": ["Pirate Shop Access – 1 in 137,500"]})
    if sat or fri or d == START:
        th = d if sat else (d + datetime.timedelta(days=1) if fri else min(x for x in TREASURE_HOURS if x >= d))
        when = "hoy" if sat else ("mañana" if fri else f"este sábado {th.day}")
        facts.append({"p": say(f"Dato del día: ¡{when} hay Horas del Tesoro!" if sat else f"Dato del día: {when} hay Horas del Tesoro."),
                      "label": "DATO DEL DÍA", "text": f"{when.capitalize()}: Horas del Tesoro"})
        if sat:                                    # the countdown posts at 7:30 PM: the first round is over, the second is next
            facts.append({"p": say("La segunda ronda es de 8 a 10 de la noche, hora de la Ciudad de México, y la probabilidad sube 1.5 veces.", 0.25,
                                   "La segunda ronda es de ocho a diez de la noche, hora de la Ciudad de México, y la probabilidad sube una vez y media."),
                          "label": "HOY · HORA CDMX · ×1.5", "text": "8 a 10 PM (segunda ronda)"})
        else:
            facts.append({"p": say("De 1 a 3 de la tarde y de 8 a 10 de la noche, hora de la Ciudad de México, la probabilidad sube 1.5 veces.", 0.25,
                                   "De una a tres de la tarde y de ocho a diez de la noche, hora de la Ciudad de México, la probabilidad sube una vez y media."),
                          "label": "HORA CDMX · ×1.5", "text": "1 a 3 PM y 8 a 10 PM"})
        claims.append({"text": f"Horas del Tesoro el {th.day} de {MES[th.month - 1]}: 1 a 3 PM y 8 a 10 PM (CDMX), ×1.5", "source": "vice-treasure-hours.json",
                       "match": [f"| {TH_ROW[th]} | 11 AM–1 PM PT / 2–4 PM ET | 6–8 PM PT / 9–11 PM ET | 1.5× both rounds |"],
                       "visual": "11 AM–1 PM and 6–8 PM Pacific (UTC-8) are 1–3 PM and 8–10 PM in Mexico City (UTC-6)"})
        dato = "¡Hoy hay Horas del Tesoro! Segunda ronda: 8 a 10 PM (CDMX) ⏰" if sat else f"{when.capitalize()} hay Horas del Tesoro: 1 a 3 PM y 8 a 10 PM (CDMX) ⏰"
        topic = f"{quedan} · dato: Horas del Tesoro {th.isoformat()}"
    else:
        # the n-th weekday of the series that is not a Friday/Saturday Treasure Hours day takes the n-th fact of the pool
        n, x = 0, START
        while x < d:
            if not ((x.weekday() == 5 and x in TREASURE_HOURS) or (x.weekday() == 4 and (x + datetime.timedelta(days=1)) in TREASURE_HOURS)):
                n += 1
            x += datetime.timedelta(days=1)
        entry = POOL[1 + (n - 1) % (len(POOL) - 1)] if n else POOL[1]
        label, text, lines, claim = entry[0], entry[1], entry[2], entry[3]
        sound = entry[4] if len(entry) > 4 else [None] * len(lines)
        facts.append({"p": say("Dato del día: " + lines[0][0].lower() + lines[0][1:], 0.25, ("Dato del día: " + sound[0][0].lower() + sound[0][1:]) if sound[0] else None),
                      "label": label, "text": text})
        claims.append({"text": f"Dato del día: {text}", **claim})
        dato = lines[0]
        topic = f"{quedan} · dato: {label.lower()}"
    say("¿Ya te salió? Dímelo en los comentarios.", 0.2)        # short enough for one caption line (a 2-line caption runs into the coin)
    say(*CODE[:1], 0.25, CODE[1])
    say(THANKS, 0.3)
    copy_sources(folder, src_dir, sorted({c["source"] for c in claims}), [])
    head = "¡Hoy es el último día para encontrar la Tienda Pirata!" if left == 0 else ("¡Mañana es el último día para encontrar la Tienda Pirata!" if left == 1 else f"¡Quedan {left} días para encontrar la Tienda Pirata!")
    body = f"{head} 🏴‍☠️ La búsqueda termina el 5 de diciembre\n{dato}\n¿Ya te salió? 👇\nUsa el código BAD en la tienda de objetos 💛"
    cfg = {"kicker": "TIENDA PIRATA", "title": title, "coin": coin, "facts": facts[:4], "caption": f"#EpicPartner {body}\n#fortnite #tiendapirata #fortniteespañol", "claims": claims}
    yt = ("Tienda Pirata: ¡HOY es el último día! 🏴‍☠️ #Shorts" if left == 0 else "Tienda Pirata: ¡queda 1 día! 🏴‍☠️ #Shorts" if left == 1
          else f"Tienda Pirata de Fortnite: ¡quedan {left} días! 🏴‍☠️ #Shorts")
    write(folder, "cuenta", cfg, ph, posts(body, "#fortnite #tiendapirata #fortniteespañol", "#Fortnite #TiendaPirata #Shorts", yt,
                                           f"Quedan {left} días 🏴‍☠️" if left > 1 else "¡Último momento! 🏴‍☠️", f"Tienda Pirata: quedan {left} días" if left > 1 else "Tienda Pirata: último momento",
                                           ["fortnite", "tienda pirata", "token tienda pirata", "tienda pirata fortnite", "fortnite español"]),
          topic)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[2] not in ("sevan", "caros", "cuenta"):
        raise SystemExit(__doc__)
    if today_mx() > END:
        raise SystemExit("STOP: the treasure hunt ended on December 5; the daily Tienda Pirata posts are over")
    out = os.path.abspath(sys.argv[1])
    os.makedirs(os.path.join(out, "sources"), exist_ok=True)
    os.makedirs(os.path.join(out, "img"), exist_ok=True)
    {"sevan": sevan, "caros": caros, "cuenta": cuenta}[sys.argv[2]](out, os.path.abspath(sys.argv[3]))
