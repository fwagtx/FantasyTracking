/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Genos",
   "img": "../../tiendabad/video/auto/2026-11-27-pirata-sevan/img/Character_ArmyFlour.png",
   "rarity": "epic"
  },
  {
   "name": "Saitama",
   "img": "../../tiendabad/video/auto/2026-11-27-pirata-sevan/img/Character_IgniteEgg.png",
   "rarity": "epic"
  },
  {
   "name": "Tornado del Terror",
   "img": "../../tiendabad/video/auto/2026-11-27-pirata-sevan/img/Character_IvyStub.png",
   "rarity": "epic"
  }
 ],
 "name": "Lote One Punch Man",
 "contents": "3 skins, 5 mochilas, 5 picos, un planeador, 3 gestos y un envoltorio",
 "price": {
  "final": 3800,
  "regular": 11900
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote One Punch Man: 3800 pavos (normalmente 11900)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote One Punch Man: 3800 pavos (normalmente 11900) en la tienda de hoy (2026-11-27)",
   "source": "shop-entry.json",
   "match": [
    "Lote One Punch Man",
    "\"finalPrice\": 3800",
    "\"regularPrice\": 11900",
    "\"date\": \"2026-11-27T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-27T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-27T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae 3 skins, 5 mochilas, 5 picos, un planeador, 3 gestos y un envoltorio",
   "source": "shop-entry.json",
   "match": [
    "Saitama",
    "Genos",
    "Tornado del Terror",
    "Centichoro",
    "Puño martillo mecánico",
    "Bolsa de supermercado",
    "Filo telequinético",
    "Rutina de Saitama",
    "Ok",
    "Psicoquinesis",
    "Núcleo de Genos",
    "Despertador de Saitama",
    "Minitornado del Terror",
    "One Punch Man",
    "Kit de One-Punch Man",
    "Voz psicoquinética",
    "Kit de One-Punch Man",
    "Voz psicoquinética"
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
