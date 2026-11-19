/* LA TIENDITA BAD · ¿Dónde está Henry? Facts: brands/tiendabad/sources (map.json, map-labels.png). */
window.SCENE = TPL.map({
 "title": [
  "¿Dónde está",
  "Henry?"
 ],
 "map": "labels",
 "titleSize": 72,
 "shots": [
  {
   "p": 0,
   "x": 1203.2,
   "y": 1500.4,
   "z": 1.6
  },
  {
   "p": 1,
   "x": 1010,
   "y": 1355.4,
   "z": 0.55,
   "ease": "out5",
   "whoosh": 1
  },
  {
   "p": 2,
   "x": 1203.2,
   "y": 1500.4,
   "z": 1.7,
   "ease": "out5",
   "whoosh": 1
  },
  {
   "p": "end",
   "x": 1203.2,
   "y": 1500.4,
   "z": 1.8
  }
 ],
 "pins": [
  {
   "x": 1203.2,
   "y": 1500.4,
   "p": 0,
   "dt": 0.35,
   "label": "Henry",
   "lx": -160,
   "ly": 90
  }
 ],
 "caption": "#EpicPartner ¿No encuentras Henry en Fortnite? 📍 Está en el sureste de la isla, cerca de Puerto Pirata\n¿Ya lo visitaste? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #mapafortnite #henry",
 "claims": [
  {
   "text": "Henry es un lugar del mapa oficial",
   "source": "map.json",
   "match": [
    "Henry"
   ]
  },
  {
   "text": "Está en el sureste de la isla, cerca de Puerto Pirata",
   "source": "map-labels.png",
   "visual": "map pixel (1203, 1500); nearest named place by coordinates: Puerto Pirata"
  }
 ]
});
