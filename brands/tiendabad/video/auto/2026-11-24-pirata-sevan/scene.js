/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "2D",
   "img": "../../tiendabad/video/auto/2026-11-24-pirata-sevan/img/Character_PajamaSoar.png",
   "rarity": "icon"
  },
  {
   "name": "Noodle",
   "img": "../../tiendabad/video/auto/2026-11-24-pirata-sevan/img/Character_DegreeProper.png",
   "rarity": "icon"
  },
  {
   "name": "Armonía de Noodle",
   "img": "../../tiendabad/video/auto/2026-11-24-pirata-sevan/img/Backpack_DegreeProper_Guitar.png",
   "rarity": "icon"
  }
 ],
 "name": "Lote Noodle y 2D",
 "contents": "2 skins, 4 mochilas, 2 picos, un gesto, una estela y un envoltorio",
 "price": {
  "final": 2600,
  "regular": 7300
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Noodle y 2D: 2600 pavos (normalmente 7300)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Noodle y 2D: 2600 pavos (normalmente 7300) en la tienda de hoy (2026-11-24)",
   "source": "shop-entry.json",
   "match": [
    "Lote Noodle y 2D",
    "\"finalPrice\": 2600",
    "\"regularPrice\": 7300",
    "\"date\": \"2026-11-24T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-24T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-24T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae 2 skins, 4 mochilas, 2 picos, un gesto, una estela y un envoltorio",
   "source": "shop-entry.json",
   "match": [
    "Noodle",
    "2D",
    "DARE",
    "Funda de Noodle",
    "Armonía de Noodle",
    "Armonía de Noodle",
    "Megáfono de 2D",
    "Globos de Bonesy",
    "Melodía de 2D",
    "Melodía de 2D",
    "Camuflaje de Gorillaz"
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
