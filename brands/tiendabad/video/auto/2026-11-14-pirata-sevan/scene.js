/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Pollito",
   "img": "../../tiendabad/video/auto/2026-11-14-pirata-sevan/img/Character_CosmicSquatter.png",
   "rarity": "rare"
  },
  {
   "name": "Girasol",
   "img": "../../tiendabad/video/auto/2026-11-14-pirata-sevan/img/BID_220_FarmerFemale.png",
   "rarity": "uncommon"
  },
  {
   "name": "Píochila",
   "img": "../../tiendabad/video/auto/2026-11-14-pirata-sevan/img/Backpack_CosmicSquatter.png",
   "rarity": "rare"
  }
 ],
 "name": "Lote Casillero de Pierogi de Jacob",
 "contents": "Una skin, 2 mochilas, un pico, un gesto, un envoltorio, un grafiti y un emoticono",
 "price": {
  "final": 2000,
  "regular": 2700
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Casillero de Pierogi de Jacob: 2000 pavos (normalmente 2700)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Casillero de Pierogi de Jacob: 2000 pavos (normalmente 2700) en la tienda de hoy (2026-11-14)",
   "source": "shop-entry.json",
   "match": [
    "Lote Casillero de Pierogi de Jacob",
    "\"finalPrice\": 2000",
    "\"regularPrice\": 2700",
    "\"date\": \"2026-11-14T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-14T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-14T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae una skin, 2 mochilas, un pico, un gesto, un envoltorio, un grafiti y un emoticono",
   "source": "shop-entry.json",
   "match": [
    "Pollito",
    "Píochila",
    "Girasol",
    "Conductor",
    "Rebote de Billy",
    "Línea de exploración",
    "Pierogi de Jacob",
    "Maleta de Jacob"
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
