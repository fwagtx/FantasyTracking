/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "He-Man",
   "img": "../../tiendabad/video/auto/2026-11-13-pirata-sevan/img/Character_RebelTempo.png",
   "rarity": "epic"
  },
  {
   "name": "She-Ra",
   "img": "../../tiendabad/video/auto/2026-11-13-pirata-sevan/img/Character_TrickCrate.png",
   "rarity": "epic"
  },
  {
   "name": "Skeletor",
   "img": "../../tiendabad/video/auto/2026-11-13-pirata-sevan/img/Character_PrismGlee.png",
   "rarity": "epic"
  }
 ],
 "name": "Lote Amos del universo",
 "contents": "3 skins, 4 mochilas, 4 picos y 2 gestos",
 "price": {
  "final": 3600,
  "regular": 6700
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Amos del universo: 3600 pavos (normalmente 6700)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Amos del universo: 3600 pavos (normalmente 6700) en la tienda de hoy (2026-11-13)",
   "source": "shop-entry.json",
   "match": [
    "Lote Amos del universo",
    "\"finalPrice\": 3600",
    "\"regularPrice\": 6700",
    "\"date\": \"2026-11-13T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-13T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-13T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae 3 skins, 4 mochilas, 4 picos y 2 gestos",
   "source": "shop-entry.json",
   "match": [
    "He-Man",
    "Armas de He-Man",
    "Tahalí del poder",
    "Skeletor",
    "Armas de Skeletor",
    "Capa de Skeletor",
    "She-Ra",
    "Espada de la protección",
    "Capa de She-Ra",
    "Risa del poder",
    "Trono de la derrota",
    "Palitos de Eternia",
    "Palitos de Eternia"
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
