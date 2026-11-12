/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Darth Vader samurái",
   "img": "../../tiendabad/video/auto/2026-11-12-pirata-sevan/img/Character_SequinPie.png",
   "rarity": "starwars"
  },
  {
   "name": "Stormtrooper samurái",
   "img": "../../tiendabad/video/auto/2026-11-12-pirata-sevan/img/Character_BeetTread.png",
   "rarity": "starwars"
  },
  {
   "name": "Estandarte Imperial",
   "img": "../../tiendabad/video/auto/2026-11-12-pirata-sevan/img/Backpack_BeetTread.png",
   "rarity": "starwars"
  }
 ],
 "name": "Lote Star Wars samurái",
 "contents": "2 skins, 2 mochilas, 2 picos, un gesto y 2 envoltorios",
 "price": {
  "final": 3200,
  "regular": 5600
 },
 "when": "se va hoy · 6 PM (CDMX)",
 "tone": "last",
 "ask": [
  "¿Te lo",
  "llevarías?"
 ],
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Star Wars samurái: 3200 pavos (normalmente 5600)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Star Wars samurái: 3200 pavos (normalmente 5600) en la tienda de hoy (2026-11-12)",
   "source": "shop-entry.json",
   "match": [
    "Lote Star Wars samurái",
    "\"finalPrice\": 3200",
    "\"regularPrice\": 5600",
    "\"date\": \"2026-11-12T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-12T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-12T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae 2 skins, 2 mochilas, 2 picos, un gesto y 2 envoltorios",
   "source": "shop-entry.json",
   "match": [
    "Darth Vader samurái",
    "Stormtrooper samurái",
    "Ataque de TIE chiquito",
    "Katana de Vader",
    "Katana de Vader",
    "Kamayari de Stormtrooper",
    "Estandarte Imperial",
    "Stormtrooper samurái",
    "Vader samurái"
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
