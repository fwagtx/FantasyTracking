/* LA TIENDITA BAD · Tienda Pirata diaria (sevan). Facts: sources/. */
window.SCENE = TPL.drop({
 "title": [
  "¡Se va HOY! ⏰",
  "¿y la Tienda Pirata? 🏴‍☠️"
 ],
 "cards": [
  {
   "name": "Operación submarina",
   "img": "../../tiendabad/video/auto/2026-11-16-pirata-sevan/img/CID_073_Athena_Commando_F_Scuba.png",
   "rarity": "rare"
  },
  {
   "name": "Pezapatos",
   "img": "../../tiendabad/video/auto/2026-11-16-pirata-sevan/img/Shoes_SaltFin.png",
   "rarity": "uncommon"
  }
 ],
 "name": "Lote Mantente a flote",
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
 "caption": "#EpicPartner Se va HOY de la tienda ⏰ Lote Mantente a flote: 1600 pavos (normalmente 1800)… y con la Tienda Pirata, 0 pavos 🏴‍☠️\n¿Te lo llevarías? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #tiendafortnite",
 "claims": [
  {
   "text": "Lote Mantente a flote: 1600 pavos (normalmente 1800) en la tienda de hoy (2026-11-16)",
   "source": "shop-entry.json",
   "match": [
    "Lote Mantente a flote",
    "\"finalPrice\": 1600",
    "\"regularPrice\": 1800",
    "\"date\": \"2026-11-16T00:00:00Z\""
   ]
  },
  {
   "text": "Se va hoy a las 6 PM (CDMX): outDate 2026-11-16T23:59:59.999Z",
   "source": "shop-entry.json",
   "match": [
    "\"outDate\": \"2026-11-16T23:59:59.999Z\""
   ],
   "visual": "23:59 UTC is 5:59 PM in Mexico City (UTC-6): it leaves at the 6 PM reset"
  },
  {
   "text": "Trae una skin y un calzado",
   "source": "shop-entry.json",
   "match": [
    "Operación submarina",
    "Pezapatos"
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
