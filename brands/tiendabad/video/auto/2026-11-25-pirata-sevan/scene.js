/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Pía",
   "img": "../../tiendabad/video/auto/2026-11-25-pirata-sevan/img/Character_OmelettePop.png",
   "rarity": "epic"
  },
  {
   "name": "Gallinas gorditas",
   "img": "../../tiendabad/video/auto/2026-11-25-pirata-sevan/img/Shoes_BarnCourt.png",
   "rarity": "uncommon"
  }
 ],
 "name": "Lote Libre albedrío",
 "contents": "Una skin y un calzado",
 "price": {
  "final": 1600,
  "regular": 1800
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Libre albedrío: 1600 pavos (normalmente 1800)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Libre albedrío: 1600 pavos (normalmente 1800) en la tienda de hoy (2026-11-25)",
   "source": "shop-entry.json",
   "match": [
    "Lote Libre albedrío",
    "\"finalPrice\": 1600",
    "\"regularPrice\": 1800",
    "\"date\": \"2026-11-25T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-25T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-25T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae una skin y un calzado",
   "source": "shop-entry.json",
   "match": [
    "Pía",
    "Gallinas gorditas"
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
