"""LA TIENDITA BAD: make a "¿Dónde está <lugar>?" short (Spanish twin of BAD ROYALE's Where Is).

    python3 brands/tiendabad/auto/new_where.py <folder> "<nombre del lugar en map.json (es-419)>"

Facts come from brands/tiendabad/sources/map.json and the Spanish labeled map (fetch_sources.py, refreshed
within 24 hours). Writes scene.js (TPL.map), vo.json and post.json; then make_short.py build / ship.
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "sources")
DIRS = ["norte", "noreste", "este", "sureste", "sur", "suroeste", "oeste", "noroeste"]


def px(p):
    return (round(0.0066081 * p["location"]["x"] + 976.46, 1), round(0.0066058 * p["location"]["y"] + 1016.67, 1))


def direction(x, y, cx, cy):
    dx, dy = x - cx, cy - y
    if math.hypot(dx, dy) < 170:
        return "cerca del centro de la isla"
    d = DIRS[int(((math.degrees(math.atan2(dx, dy)) + 360) % 360 + 22.5) // 45) % 8]
    return f"en el extremo {d} de la isla" if math.hypot(dx, dy) > 620 else f"en el {d} de la isla"


def main(folder, name):
    pois = json.load(open(os.path.join(SRC, "map.json")))["data"]["pois"]
    me = [p for p in pois if p["name"] == name]
    if len(me) != 1:
        raise SystemExit(f"STOP: {name!r} must be exactly one place on the map (found {len(me)})")
    x, y = px(me[0])
    named = [p for p in pois if ".POI." in p["id"] and p["name"] != name]
    allnamed = [px(p) for p in pois if ".POI." in p["id"]]
    cx, cy = sum(a for a, b in allnamed) / len(allnamed), sum(b for a, b in allnamed) / len(allnamed)
    near = min(named, key=lambda q: math.hypot(px(q)[0] - x, px(q)[1] - y))["name"]
    d = direction(x, y, cx, cy)
    wx, wy = min(max(1010, x - 291), x + 436), min(max(1010, y - 145), y + 709)
    head = f"#EpicPartner ¿No encuentras {name} en Fortnite? 📍 Está {d}, cerca de {near}\n¿Ya lo visitaste? 👇\nUsa el código BAD en la tienda de objetos 💛"
    tag = "".join(ch for ch in name.lower() if ch.isalnum())
    post = {
        "tiktok": head + f"\n#fortnite #mapafortnite #{tag}",
        "tiktokTitle": f"¿Dónde está {name}? 📍",
        "instagram": head + f"\n#fortnite #mapafortnite #{tag}",
        "facebook": head,
        "facebookTitle": f"¿Dónde está {name} en Fortnite?",
        "youtube": head + "\n#Fortnite #MapaFortnite #Shorts",
        "youtubeTitle": f"¿Dónde está {name} en Fortnite? 📍 Ubicación exacta #Shorts",
        "threads": head + "\n#fortnite #mapafortnite",
        "tags": ["fortnite", f"{name.lower()} fortnite", f"donde esta {name.lower()}", "mapa fortnite", "fortnite español"],
        "cover_t": 1.0,
    }
    cfg = {"title": ["¿Dónde está", f"{name}?"], "map": "labels", "titleSize": min(72, int(880 / (0.74 * (len(name) + 1)))),
           "shots": [{"p": 0, "x": x, "y": y, "z": 1.6}, {"p": 1, "x": round(wx, 1), "y": round(wy, 1), "z": 0.55, "ease": "out5", "whoosh": 1},
                     {"p": 2, "x": x, "y": y, "z": 1.7, "ease": "out5", "whoosh": 1}, {"p": "end", "x": x, "y": y, "z": 1.8}],
           "pins": [{"x": x, "y": y, "p": 0, "dt": 0.35, "label": name if len(name) < 16 else "AQUÍ", "lx": -160, "ly": 90}],
           "caption": post["tiktok"],
           "claims": [{"text": f"{name} es un lugar del mapa oficial", "source": "map.json", "match": [name]},
                      {"text": f"Está {d}, cerca de {near}", "source": "map-labels.png", "visual": f"map pixel ({x:.0f}, {y:.0f}); nearest named place by coordinates: {near}"}]}
    os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, "scene.js"), "w").write(f"/* LA TIENDITA BAD · ¿Dónde está {name}? Facts: brands/tiendabad/sources (map.json, map-labels.png). */\n"
                                                      "window.SCENE = TPL.map(" + json.dumps(cfg, indent=1, ensure_ascii=False) + ");\n")
    phrases = [{"text": f"¿No encuentras {name} en Fortnite? ¡Aquí está exactamente!", "pause": 0.25},
               {"text": f"Está {d},", "pause": 0.1},
               {"text": f"justo aquí, cerca de {near}.", "pause": 0.3},
               {"text": "¡Guárdalo para tu próxima partida!", "pause": 0.2},
               {"text": "Usa el código BAD en la tienda de objetos. Le ayuda muchísimo al canal.", "say": "Usa el código bad, en la tienda de objetos. Le ayuda muchísimo al canal.", "pause": 0.25},
               {"text": "¡Gracias por ver! Síguenos para más contenido diario.", "pause": 0.3}]
    say = {"$": "S", "Buggy": "Bugui"}      # "$" is read as the letter S; Kokoro es-419 reads "Buggy" as "bug-ji"
    for ph in phrases:
        s = ph.get("say", ph["text"])
        for a, b in say.items():
            s = s.replace(a, b)
        if s != ph["text"]:
            ph["say"] = s
    json.dump({"phrases": phrases}, open(os.path.join(folder, "vo.json"), "w"), indent=1, ensure_ascii=False)
    json.dump(post, open(os.path.join(folder, "post.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps({"lugar": name, "donde": d, "cerca": near}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(os.path.abspath(sys.argv[1]), sys.argv[2])
