/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Azote",
   "img": "../../tiendabad/video/auto/2026-11-11-pirata-sevan/img/CID_240_Athena_Commando_F_Plague.png",
   "rarity": "epic"
  },
  {
   "name": "Peste",
   "img": "../../tiendabad/video/auto/2026-11-11-pirata-sevan/img/CID_241_Athena_Commando_M_Plague.png",
   "rarity": "epic"
  },
  {
   "name": "Arcano",
   "img": "../../tiendabad/video/auto/2026-11-11-pirata-sevan/img/BID_128_PlagueFemale.png",
   "rarity": "epic"
  }
 ],
 "name": "Lote Medicina lúgubre",
 "contents": "2 skins, 2 mochilas, pico y planeador",
 "price": {
  "final": 2200,
  "regular": 4600
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Medicina lúgubre: 2200 pavos (normalmente 4600)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Medicina lúgubre: 2200 pavos (normalmente 4600) en la tienda de hoy (2026-11-11)",
   "source": "shop-entry.json",
   "match": [
    "Lote Medicina lúgubre",
    "\"finalPrice\": 2200",
    "\"regularPrice\": 4600",
    "\"date\": \"2026-11-11T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-11T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-11T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae 2 skins, 2 mochilas, pico y planeador",
   "source": "shop-entry.json",
   "match": [
    "Azote",
    "Arcano",
    "Peste",
    "Capa tétrica",
    "Bastón del heraldo",
    "Farol"
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
