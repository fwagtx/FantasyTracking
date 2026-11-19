/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Kenji",
   "img": "../../tiendabad/video/auto/2026-11-19-pirata-sevan/img/CID_361_Athena_Commando_M_BandageNinja.png",
   "rarity": "epic"
  },
  {
   "name": "Kuno",
   "img": "../../tiendabad/video/auto/2026-11-19-pirata-sevan/img/CID_362_Athena_Commando_F_BandageNinja.png",
   "rarity": "epic"
  },
  {
   "name": "Doble Kama",
   "img": "../../tiendabad/video/auto/2026-11-19-pirata-sevan/img/BID_226_BandageNinjaFemale.png",
   "rarity": "epic"
  }
 ],
 "name": "Lote Clan Halcón",
 "contents": "2 skins, 2 mochilas, 2 picos y un planeador",
 "price": {
  "final": 2800,
  "regular": 5800
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Clan Halcón: 2800 pavos (normalmente 5800)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Clan Halcón: 2800 pavos (normalmente 5800) en la tienda de hoy (2026-11-19)",
   "source": "shop-entry.json",
   "match": [
    "Lote Clan Halcón",
    "\"finalPrice\": 2800",
    "\"regularPrice\": 5800",
    "\"date\": \"2026-11-19T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-19T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-19T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae 2 skins, 2 mochilas, 2 picos y un planeador",
   "source": "shop-entry.json",
   "match": [
    "Kuno",
    "Kenji",
    "Halcón",
    "Garras",
    "Respuesta rápida",
    "Katana y Kunai",
    "Doble Kama"
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
