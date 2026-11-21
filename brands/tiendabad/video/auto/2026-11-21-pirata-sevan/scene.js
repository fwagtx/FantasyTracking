/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Madison Make You Mine",
   "img": "../../tiendabad/video/auto/2026-11-21-pirata-sevan/img/Character_MistTycoonHike.png",
   "rarity": "icon"
  },
  {
   "name": "Madison angelical",
   "img": "../../tiendabad/video/auto/2026-11-21-pirata-sevan/img/Character_MistTycoonLife.png",
   "rarity": "icon"
  },
  {
   "name": "Alas angelicales de Madison",
   "img": "../../tiendabad/video/auto/2026-11-21-pirata-sevan/img/Backpack_MistTycoonLife.png",
   "rarity": "icon"
  }
 ],
 "name": "Lote Madison Beer",
 "contents": "2 skins, 3 mochilas, 2 picos, 2 gestos, un envoltorio y una pantalla de carga",
 "price": {
  "final": 3400,
  "regular": 6800
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Madison Beer: 3400 pavos (normalmente 6800)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Madison Beer: 3400 pavos (normalmente 6800) en la tienda de hoy (2026-11-21)",
   "source": "shop-entry.json",
   "match": [
    "Lote Madison Beer",
    "\"finalPrice\": 3400",
    "\"regularPrice\": 6800",
    "\"date\": \"2026-11-21T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-21T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-21T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae 2 skins, 3 mochilas, 2 picos, 2 gestos, un envoltorio y una pantalla de carga",
   "source": "shop-entry.json",
   "match": [
    "Madison Make You Mine",
    "Arma de Madison",
    "Madison angelical",
    "Alas angelicales de Madison",
    "Peluches de Mookie y Maple",
    "Bittersweet",
    "Yes Baby",
    "Voz de Madison",
    "Voz de Madison",
    "Glamur de Madison",
    "Madison en lo más alto"
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
