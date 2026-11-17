/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Katalina",
   "img": "../../tiendabad/video/auto/2026-11-17-pirata-sevan/img/Character_ExcitedCyan.png",
   "rarity": "epic"
  },
  {
   "name": "Michimochila de Katalina",
   "img": "../../tiendabad/video/auto/2026-11-17-pirata-sevan/img/Backpack_ExcitedCyan.png",
   "rarity": "rare"
  },
  {
   "name": "Garras de Kat",
   "img": "../../tiendabad/video/auto/2026-11-17-pirata-sevan/img/Pickaxe_ExcitedCyan.png",
   "rarity": "rare"
  }
 ],
 "name": "Lote Katalina",
 "contents": "Una skin, una mochila, un pico y un envoltorio",
 "price": {
  "final": 1600,
  "regular": 2500
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Katalina: 1600 pavos (normalmente 2500)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Katalina: 1600 pavos (normalmente 2500) en la tienda de hoy (2026-11-17)",
   "source": "shop-entry.json",
   "match": [
    "Lote Katalina",
    "\"finalPrice\": 1600",
    "\"regularPrice\": 2500",
    "\"date\": \"2026-11-17T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-17T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-17T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae una skin, una mochila, un pico y un envoltorio",
   "source": "shop-entry.json",
   "match": [
    "Katalina",
    "Michimochila de Katalina",
    "Garras de Kat",
    "Michi táctico"
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
