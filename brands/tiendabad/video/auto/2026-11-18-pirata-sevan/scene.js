/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Glitch",
   "img": "../../tiendabad/video/auto/2026-11-18-pirata-sevan/img/Character_DefectGlitch.png",
   "rarity": "epic"
  },
  {
   "name": "Inhibidor de señal",
   "img": "../../tiendabad/video/auto/2026-11-18-pirata-sevan/img/Backpack_DefectGlitch.png",
   "rarity": "epic"
  },
  {
   "name": "Bate puntiagudo",
   "img": "../../tiendabad/video/auto/2026-11-18-pirata-sevan/img/Pickaxe_ID_449_TripleScoopFemale.png",
   "rarity": "rare"
  }
 ],
 "name": "Lote Casillero Glitch de Candywing",
 "contents": "Una skin, una mochila, un pico, un planeador, un gesto y 2 envoltorios",
 "price": {
  "final": 2800,
  "regular": 4500
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Casillero Glitch de Candywing: 2800 pavos (normalmente 4500)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Casillero Glitch de Candywing: 2800 pavos (normalmente 4500) en la tienda de hoy (2026-11-18)",
   "source": "shop-entry.json",
   "match": [
    "Lote Casillero Glitch de Candywing",
    "\"finalPrice\": 2800",
    "\"regularPrice\": 4500",
    "\"date\": \"2026-11-18T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-18T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-18T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae una skin, una mochila, un pico, un planeador, un gesto y 2 envoltorios",
   "source": "shop-entry.json",
   "match": [
    "Glitch",
    "Inhibidor de señal",
    "Sonar ultrasónico",
    "Bate puntiagudo",
    "Marrajo mecánico",
    "Rock & Sol",
    "Estampado de flores"
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
