/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Syd",
   "img": "../../tiendabad/video/auto/2026-11-22-pirata-sevan/img/CID_A_453_Athena_Commando_F_FuzzyBearSummer.png",
   "rarity": "rare"
  },
  {
   "name": "Osito cariñoso",
   "img": "../../tiendabad/video/auto/2026-11-22-pirata-sevan/img/BID_A_042_FuzzyBearSummerFemale.png",
   "rarity": "rare"
  },
  {
   "name": "Puños espinosos",
   "img": "../../tiendabad/video/auto/2026-11-22-pirata-sevan/img/Pickaxe_ID_831_FuzzyBearSummerFemale.png",
   "rarity": "rare"
  }
 ],
 "name": "Lote Osa amorosa",
 "contents": "Una skin, una mochila, un pico y un envoltorio",
 "price": {
  "final": 1700,
  "regular": 2300
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Osa amorosa: 1700 pavos (normalmente 2300)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Osa amorosa: 1700 pavos (normalmente 2300) en la tienda de hoy (2026-11-22)",
   "source": "shop-entry.json",
   "match": [
    "Lote Osa amorosa",
    "\"finalPrice\": 1700",
    "\"regularPrice\": 2300",
    "\"date\": \"2026-11-22T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-22T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-22T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae una skin, una mochila, un pico y un envoltorio",
   "source": "shop-entry.json",
   "match": [
    "Syd",
    "Osito cariñoso",
    "Puños espinosos",
    "Ola de amor"
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
