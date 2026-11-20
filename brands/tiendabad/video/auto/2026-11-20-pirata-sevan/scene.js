/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Kafka Hibino",
   "img": "../../tiendabad/video/auto/2026-11-20-pirata-sevan/img/Character_SureBamboo.png",
   "rarity": "epic"
  },
  {
   "name": "Kikoru Shinomiya",
   "img": "../../tiendabad/video/auto/2026-11-20-pirata-sevan/img/Character_CafeStove.png",
   "rarity": "epic"
  },
  {
   "name": "Reno Ichikawa",
   "img": "../../tiendabad/video/auto/2026-11-20-pirata-sevan/img/Character_YamPowder.png",
   "rarity": "epic"
  }
 ],
 "name": "Lote Kaiju N.º 8",
 "contents": "3 skins, 3 mochilas, 3 picos, un gesto y un envoltorio",
 "price": {
  "final": 3800,
  "regular": 7700
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Kaiju N.º 8: 3800 pavos (normalmente 7700)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Kaiju N.º 8: 3800 pavos (normalmente 7700) en la tienda de hoy (2026-11-20)",
   "source": "shop-entry.json",
   "match": [
    "Lote Kaiju N.º 8",
    "\"finalPrice\": 3800",
    "\"regularPrice\": 7700",
    "\"date\": \"2026-11-20T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-20T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-20T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae 3 skins, 3 mochilas, 3 picos, un gesto y un envoltorio",
   "source": "shop-entry.json",
   "match": [
    "Kafka Hibino",
    "Kikoru Shinomiya",
    "Reno Ichikawa",
    "Arma personal de Kikoru",
    "Guadaña de Kaiju N.º 8",
    "Hachas de congelación",
    "Espíritu del kaiju",
    "Morral de Kikoru",
    "Máscara quebrada",
    "Equipamiento de Reno",
    "Kaiju N.º 8"
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
