"""TIENDA BAD: make a "¿Dónde está <lugar>?" short (Spanish twin of BAD ROYALE's Where Is).

    python3 brand/tiendabad/auto/new_where.py <folder> "<nombre del lugar en map.json (es-419)>"

Facts come from brand/tiendabad/sources/map.json (fortnite-api.com /v1/map?language=es-419) and the Spanish
labeled map. Writes scene.js (Spanish templates + TPL.map) and vo.json.
"""
import json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "sources")
TPL = os.path.join(HERE, "..", "video", "templates_es.js")
C = [1010, 1010]
DIRS = ["norte", "noreste", "este", "sureste", "sur", "suroeste", "oeste", "noroeste"]


def px(p):
    return (round(0.0066081 * p["location"]["x"] + 976.46, 1), round(0.0066058 * p["location"]["y"] + 1016.67, 1))


def direction(x, y):
    dx, dy = x - C[0], C[1] - y
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
    C[0], C[1] = sum(a for a, b in allnamed) / len(allnamed), sum(b for a, b in allnamed) / len(allnamed)
    near = min(named, key=lambda q: math.hypot(px(q)[0] - x, px(q)[1] - y))["name"]
    d = direction(x, y)
    wx, wy = min(max(1010, x - 291), x + 436), min(max(1010, y - 145), y + 709)
    cfg = {"title": ["¿Dónde está", f"{name}?"], "map": "labels", "titleSize": min(72, int(880 / (0.74 * (len(name) + 1)))),
           "shots": [{"p": 0, "x": x, "y": y, "z": 1.6}, {"p": 1, "x": round(wx, 1), "y": round(wy, 1), "z": 0.55, "ease": "out5", "whoosh": 1},
                     {"p": 2, "x": x, "y": y, "z": 1.7, "ease": "out5", "whoosh": 1}, {"p": "end", "x": x, "y": y, "z": 1.8}],
           "pins": [{"x": x, "y": y, "p": 0, "dt": 0.35, "label": name if len(name) < 16 else "AQUÍ", "lx": -160, "ly": 90}],
           "caption": f"#EpicPartner ¿Dónde está {name} en Fortnite? 📍\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #mapafortnite #fortniteenespañol",
           "claims": [{"text": f"{name} es un lugar del mapa oficial", "source": "map.json", "match": [name]},
                      {"text": f"Está {d}, cerca de {near}", "source": "map-labels.png", "visual": f"map pixel ({x:.0f}, {y:.0f}); nearest named POI by coordinates: {near}"}]}
    os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, "scene.js"), "w").write(open(TPL).read() + f"\n/* TIENDA BAD · ¿Dónde está {name}? */\nwindow.SCENE = TPL.map(" + json.dumps(cfg, indent=1, ensure_ascii=False) + ");\n")
    voice = json.load(open(os.path.join(HERE, "..", "voice.json")))
    phrases = [{"text": f"¿No encuentras {name} en Fortnite? Aquí está exactamente.", "pause": 0.25},
               {"text": f"Está {d},", "pause": 0.1},
               {"text": f"justo aquí, cerca de {near}.", "pause": 0.3},
               {"text": "¡Guárdalo para tu próxima misión!", "pause": 0.2},
               {"text": "Usa el código BAD en la tienda de objetos. Le ayuda muchísimo al canal.", "say": "Usa el código bad, en la tienda de objetos. Le ayuda muchísimo al canal.", "pause": 0.25},
               {"text": "¡Gracias por ver! Síguenos para más contenido diario.", "pause": 0.3}]
    json.dump({**{k: voice[k] for k in ("voice", "speed", "lang", "lead", "mode") if k in voice}, "phrases": phrases}, open(os.path.join(folder, "vo.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps({"lugar": name, "donde": d, "cerca": near}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(os.path.abspath(sys.argv[1]), sys.argv[2])
