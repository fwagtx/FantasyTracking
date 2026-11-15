/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Mega Man (Dual Override)",
   "img": "../../tiendabad/video/auto/2026-11-15-pirata-sevan/img/Character_DuneBriefMerry.png",
   "rarity": "gaminglegends"
  },
  {
   "name": "Mega Man X",
   "img": "../../tiendabad/video/auto/2026-11-15-pirata-sevan/img/Character_DuneBriefShift.png",
   "rarity": "gaminglegends"
  },
  {
   "name": "Zero (Mega Man)",
   "img": "../../tiendabad/video/auto/2026-11-15-pirata-sevan/img/Character_TackleShow.png",
   "rarity": "gaminglegends"
  }
 ],
 "name": "Lote Mega Man",
 "contents": "3 skins, 2 mochilas, 3 picos, un gesto y un sidekick",
 "price": {
  "final": 3800,
  "regular": 7300
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Mega Man: 3800 pavos (normalmente 7300)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Mega Man: 3800 pavos (normalmente 7300) en la tienda de hoy (2026-11-15)",
   "source": "shop-entry.json",
   "match": [
    "Lote Mega Man",
    "\"finalPrice\": 3800",
    "\"regularPrice\": 7300",
    "\"date\": \"2026-11-15T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-15T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-15T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae 3 skins, 2 mochilas, 3 picos, un gesto y un sidekick",
   "source": "shop-entry.json",
   "match": [
    "Mega Man (Dual Override)",
    "Hoja ígnea de Mega Buster",
    "Mega Man X",
    "Sierra de Mega Buster",
    "Tanque de energía portátil",
    "Zero (Mega Man)",
    "Sable Z",
    "Sable Z",
    "Rush (Mega Man)",
    "Cañonazo de Mega Buster"
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
