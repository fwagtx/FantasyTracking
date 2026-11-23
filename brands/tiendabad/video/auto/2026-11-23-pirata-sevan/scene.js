/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Faye Valentine",
   "img": "../../tiendabad/video/auto/2026-11-23-pirata-sevan/img/Character_KneeLens.png",
   "rarity": "epic"
  },
  {
   "name": "Spike Spiegel",
   "img": "../../tiendabad/video/auto/2026-11-23-pirata-sevan/img/Character_StudyBench.png",
   "rarity": "epic"
  },
  {
   "name": "Rosas para Julia",
   "img": "../../tiendabad/video/auto/2026-11-23-pirata-sevan/img/Backpack_StudyBench.png",
   "rarity": "rare"
  }
 ],
 "name": "Lote Cowboy Bebop",
 "contents": "2 skins, 2 mochilas, 2 picos, 2 gestos y un envoltorio",
 "price": {
  "final": 3400,
  "regular": 6600
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Cowboy Bebop: 3400 pavos (normalmente 6600)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Cowboy Bebop: 3400 pavos (normalmente 6600) en la tienda de hoy (2026-11-23)",
   "source": "shop-entry.json",
   "match": [
    "Lote Cowboy Bebop",
    "\"finalPrice\": 3400",
    "\"regularPrice\": 6600",
    "\"date\": \"2026-11-23T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-23T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-23T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae 2 skins, 2 mochilas, 2 picos, 2 gestos y un envoltorio",
   "source": "shop-entry.json",
   "match": [
    "Faye Valentine",
    "Spike Spiegel",
    "Ritmo Bop",
    "Mini-Swordfish II",
    "Mazo de la Red Tail",
    "Segador de la Swordfish II",
    "Teléfono multiusos",
    "Rosas para Julia",
    "Azul Valentine"
   ]
  },
  {
   "text": "Con la Tienda Pirata, todo lo que cuesta pavos es gratis por 14 días",
   "source": "vgc-odds-confirmed.json",
   "match": [
    "every single item in the Fortnite Shop which costs V-Bucks",
    "becomes entirely free to that player for the next 14 days"
   ]
  }
 ]
});
