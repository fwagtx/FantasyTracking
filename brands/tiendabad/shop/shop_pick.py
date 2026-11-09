"""LA TIENDITA BAD daily Item Shop short (Spanish twin of BAD ROYALE's shop video).
Fetches today's shop in Latin American Spanish, picks the three facts, writes the Spanish voice script.

Usage: python3 shop_pick.py [YYYY-MM-DD]   (default: today's shop, UTC)
Writes shop/<date>/: shop.json, history.json, manifest.json, img/*.png, plan.json, vo.json, plan.js

Picks (all from Epic's shop data, mirrored by fortnite-api.com, language es-419):
  deal  - the bundle with the biggest gap between its regular price and today's price (plus the saving)
  og    - the oldest outfit(s) in the shop, by the season Epic says they were introduced, and how many
          days since each was last in the shop (from the item's shop history)
  last  - outfits on their last day: their listing ends at the next reset (6 PM Mexico City time)
"""
import datetime, json, os, sys, time, urllib.request
from collections import Counter
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://fortnite-api.com"
LANG = "es-419"
MX = ZoneInfo("America/Mexico_City")
MES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
MES3 = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
# feminine number words (las skins); larger numbers stay digits, which the voice reads in Spanish
NUM_F = {1: "una", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis", 7: "siete", 8: "ocho", 9: "nueve", 10: "diez", 11: "once", 12: "doce"}
# rarity as an adjective for "skin" (feminine): (singular, plural)
RAR_ADJ = {"common": ("común", "comunes"), "uncommon": ("poco común", "poco comunes"), "rare": ("rara", "raras"), "epic": ("épica", "épicas"),
           "legendary": ("legendaria", "legendarias"), "mythic": ("mítica", "míticas")}


def get(url, tries=4):
    for i in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=120).read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def num(n):          # on screen: Spanish style, no separator below 10 000, a thin space above
    return f"{n:,}".replace(",", " ") if n >= 10000 else str(n)


def rar_words(key, label, n):
    if key in RAR_ADJ:
        return RAR_ADJ[key][0 if n == 1 else 1]
    return "de " + (label or "").strip()          # e.g. "de Serie de ídolos"


def main():
    raw = json.loads(get(f"{API}/v2/shop?language={LANG}"))
    shop = raw["data"]
    day = shop["date"][:10]
    want = sys.argv[1] if len(sys.argv) > 1 else day
    if want != day:
        raise SystemExit(f"Live shop is {day}, not {want}. Run after the reset.")
    out = os.path.join(HERE, day)
    os.makedirs(os.path.join(out, "img"), exist_ok=True)
    json.dump(raw, open(os.path.join(out, "shop.json"), "w"))
    d0 = datetime.date.fromisoformat(day)

    outfits, bundles = {}, []
    for e in shop["entries"]:
        items = [i for i in (e.get("brItems") or []) if i["type"]["value"] == "outfit"]
        if not items:
            continue
        if e.get("bundle"):
            bundles.append({"name": e["bundle"]["name"].strip(), "final": e["finalPrice"], "regular": e["regularPrice"], "out": e["outDate"], "outfits": [i["id"] for i in items]})
        for i in items:
            rec = outfits.setdefault(i["id"], {"id": i["id"], "name": i["name"].strip(), "rarity": i["rarity"]["displayValue"], "rarityKey": i["rarity"]["value"],
                                               "series": (i.get("series") or {}).get("value"), "img": i["images"].get("featured") or i["images"]["icon"], "price": None, "out": None})
            if not e.get("bundle") and len(e.get("brItems") or []) <= 2 and (rec["price"] is None or e["finalPrice"] < rec["price"]):
                rec["price"], rec["out"] = e["finalPrice"], e["outDate"][:10]

    # introduction season and shop history for each outfit
    hist = {}
    for oid in outfits:
        try:
            c = json.loads(get(f"{API}/v2/cosmetics/br/{oid}?language={LANG}&responseFlags=4"))["data"]
            intro = c.get("introduction") or {}
            seen = sorted(x[:10] for x in (c.get("shopHistory") or []) if x[:10] < day)
            gap = (d0 - datetime.date.fromisoformat(seen[-1])).days if seen else None
            hist[oid] = {"name": c["name"], "introduction": intro.get("text"), "chapter": int(intro.get("chapter") or 99), "season": int(intro.get("season") or 99),
                         "lastSeen": seen[-1] if seen else None, "gapDays": gap, "appearances": len(seen)}
        except Exception as ex:
            hist[oid] = {"name": outfits[oid]["name"], "error": str(ex), "chapter": 99, "season": 99, "gapDays": None}
    json.dump(hist, open(os.path.join(out, "history.json"), "w"), indent=1, ensure_ascii=False)

    deal = max(bundles, key=lambda b: (b["regular"] - b["final"], b["regular"]))
    saving = deal["regular"] - deal["final"]
    pct = round(100 * saving / deal["regular"]) if deal["regular"] else 0
    priced = [o for o in outfits if outfits[o]["price"]]
    oldest = min((hist[o]["chapter"], hist[o]["season"]) for o in priced)
    og = sorted([o for o in priced if (hist[o]["chapter"], hist[o]["season"]) == oldest], key=lambda o: (-(hist[o]["gapDays"] or 0), outfits[o]["name"]))[:2]
    gap = max((hist[o]["gapDays"] or 0) for o in og)
    last = [o for o, r in outfits.items() if r["out"] == day and r["price"]]
    last.sort(key=lambda o: (-outfits[o]["price"], outfits[o]["name"]))

    og_recs = [dict(outfits[o], gapDays=hist[o]["gapDays"]) for o in og]
    ch, se = oldest
    last_recs = [outfits[o] for o in last]
    reset_mx = datetime.datetime.combine(d0 + datetime.timedelta(days=1), datetime.time(0), datetime.timezone.utc).astimezone(MX)
    deal_out = datetime.datetime.fromisoformat(deal["out"].replace("Z", "+00:00"))
    if deal_out.second >= 59:                     # listings end at 23:59:59.999Z, i.e. the next reset
        deal_out = deal_out.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    deal_out = deal_out.astimezone(MX)

    # the voice says only what matters; prices and dates also sit on screen. "say" is how the voice reads it.
    P = [("intro", f"Esta es la tienda de objetos de hoy, {d0.day} de {MES[d0.month - 1]}.", None, 0.08),
         ("hook", "¡Tres cosas antes de gastar un solo pavo!", None, 0.22),
         ("deal_name", f"El {deal['name']}:", None, 0.06),
         ("deal_price", f"{num(deal['final'])} pavos, en vez de {num(deal['regular'])}.", f"{deal['final']} pavos, en vez de {deal['regular']}.", 0.1),
         ("saving", f"¡Te ahorras {num(saving)}!", f"¡Te ahorras {saving}!", 0.26)]
    if len(og_recs) == 2:
        P += [("og_name", f"{og_recs[0]['name']} y {og_recs[1]['name']} están en la tienda.", None, 0.06)]
        era = f"Las dos son del Capítulo {ch}, Temporada {se}"
    else:
        P += [("og_name", f"{og_recs[0]['name']} está en la tienda.", None, 0.06)]
        era = f"Es del Capítulo {ch}, Temporada {se}"
    P += [("og_era", era + (f", y no salía desde hace {gap} días." if gap >= 30 else "."), None, 0.26)]
    if last_recs:
        top, k = Counter(r["rarityKey"] for r in last_recs).most_common(1)[0]
        label = next(r["rarity"] for r in last_recs if r["rarityKey"] == top)
        n = len(last_recs)
        nw = lambda x: NUM_F.get(x, str(x))
        if k == n:
            line = f"Y {nw(n)} skin{'s' if n > 1 else ''} {rar_words(top, label, n)} {'están' if n > 1 else 'está'} en su último día."
        else:
            line = f"Y {nw(n)} skins están en su último día, {nw(k)} de ellas {rar_words(top, label, k)}."
        P += [("last", line, None, 0.3)]
        last.sort(key=lambda o: (outfits[o]["rarityKey"] != top, -outfits[o]["price"], outfits[o]["name"]))
        last_recs = [outfits[o] for o in last]
    P += [("ask", "¿Vas a comprar algo hoy?", None, 0.06),
          ("code", "Usa el código BAD en la tienda de objetos. Le ayuda muchísimo al canal.", "Usa el código bad, en la tienda de objetos. Le ayuda muchísimo al canal.", 0.25),
          ("thanks", "¡Gracias por ver! Síguenos para más contenido diario.", None, 0.3)]

    need = set(deal["outfits"][:4]) | set(og) | set(last[:6])
    for oid in need:
        p = os.path.join(out, "img", oid + ".png")
        if not os.path.exists(p):
            open(p, "wb").write(get(outfits[oid]["img"]))
    open(os.path.join(out, "img", "vbuck.png"), "wb").write(get(shop["vbuckIcon"]))

    voice = json.load(open(os.path.join(HERE, "..", "voice.json")))
    # names styled with symbols (A$AP Rocky, The $.$. Rocky) are read with the letter they stand for
    spoken = lambda x: x.replace("$", "S")
    json.dump({**{k: voice[k] for k in ("voice", "speed", "lang", "lead", "mode", "slack") if k in voice},
               "phrases": [dict({"text": t, "pause": p}, **({"say": spoken(s or t)} if (s or spoken(t) != t) else {})) for _, t, s, p in P]},
              open(os.path.join(out, "vo.json"), "w"), indent=1, ensure_ascii=False)

    plan = {"date": day, "dateLabel": f"{d0.day} {MES3[d0.month - 1]}", "dateLong": f"{d0.day} de {MES[d0.month - 1]}",
            "resetLabel": reset_mx.strftime("%-I %p").replace("PM", "PM").replace("AM", "AM"), "resetZone": "CDMX",
            "dealOutLabel": f"{deal_out.day} {MES3[deal_out.month - 1]}", "dealOutTime": deal_out.strftime("%-I %p"),
            "deal": {**deal, "saving": saving, "pct": pct, "outfits": [outfits[o] for o in deal["outfits"][:4]]},
            "og": {"chapter": ch, "season": se, "gapDays": gap, "outfits": og_recs}, "last": {"count": len(last_recs), "outfits": last_recs[:6]},
            "roles": [r for r, _, _, _ in P], "phrases": [t for _, t, _, _ in P]}
    json.dump(plan, open(os.path.join(out, "plan.json"), "w"), indent=1, ensure_ascii=False)
    open(os.path.join(out, "plan.js"), "w").write("window.SHOP=" + json.dumps(plan, ensure_ascii=False) + ";\n")

    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    json.dump({"via": "fortnite-api.com (mirror of Epic's in-game shop)", "language": LANG, "files": [
        {"file": "shop.json", "source": f"Item Shop for {day}", "url": f"{API}/v2/shop?language={LANG}", "fetched_utc": now},
        {"file": "history.json", "source": "Introduction seasons and shop history", "url": f"{API}/v2/cosmetics/br/<id>?language={LANG}&responseFlags=4", "fetched_utc": now}]},
        open(os.path.join(out, "manifest.json"), "w"), indent=1)
    print(json.dumps({"date": day, "deal": [deal["name"], deal["final"], deal["regular"], f"-{pct}%"], "og": [r["name"] for r in og_recs], "season": oldest, "gapDays": gap,
                      "last": len(last_recs)}, indent=1, ensure_ascii=False))
    print("\n".join(t for _, t, _, _ in P))


main()
