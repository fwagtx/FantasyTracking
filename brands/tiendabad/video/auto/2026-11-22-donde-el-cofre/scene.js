/* LA TIENDITA BAD · ¿Dónde está El cofre? Facts: brands/tiendabad/sources (map.json, map-labels.png). */
window.SCENE = TPL.map({
 "title": [
  "¿Dónde está",
  "El cofre?"
 ],
 "map": "labels",
 "titleSize": 72,
 "shots": [
  {
   "p": 0,
   "x": 762.1,
   "y": 875.4,
   "z": 1.6
  },
  {
   "p": 1,
   "x": 1010,
   "y": 1010,
   "z": 0.55,
   "ease": "out5",
   "whoosh": 1
  },
  {
   "p": 2,
   "x": 762.1,
   "y": 875.4,
   "z": 1.7,
   "ease": "out5",
   "whoosh": 1
  },
  {
   "p": "end",
   "x": 762.1,
   "y": 875.4,
   "z": 1.8
  }
 ],
 "pins": [
  {
   "x": 762.1,
   "y": 875.4,
   "p": 0,
   "dt": 0.35,
   "label": "El cofre",
   "lx": -160,
   "ly": 90
  }
 ],
 "caption": "#EpicPartner ¿No encuentras El cofre en Fortnite? 📍 Está en el noroeste de la isla, cerca de Resort relajado\n¿Ya lo visitaste? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #mapafortnite #elcofre",
 "claims": [
  {
   "text": "El cofre es un lugar del mapa oficial",
   "source": "map.json",
   "match": [
    "El cofre"
   ]
  },
  {
   "text": "Está en el noroeste de la isla, cerca de Resort relajado",
   "source": "map-labels.png",
   "visual": "map pixel (762, 875); nearest named place by coordinates: Resort relajado"
  }
 ]
});
