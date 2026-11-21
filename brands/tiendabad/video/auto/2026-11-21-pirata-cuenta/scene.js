/* LA TIENDITA BAD · Tienda Pirata diaria (cuenta). Facts: sources/. */
window.SCENE = TPL.guide({
 "kicker": "TIENDA PIRATA",
 "title": [
  "¡Quedan 14 días!",
  "para encontrarla 🏴‍☠️"
 ],
 "coin": [
  "QUEDAN",
  "14 DÍAS"
 ],
 "facts": [
  {
   "p": 1,
   "label": "TERMINA",
   "text": "5 de diciembre"
  },
  {
   "p": 2,
   "label": "PROBABILIDAD",
   "text": "1 en 137 500 por cofre"
  },
  {
   "p": 3,
   "label": "DATO DEL DÍA",
   "text": "Hoy: Horas del Tesoro"
  },
  {
   "p": 4,
   "label": "HOY · HORA CDMX · ×1.5",
   "text": "8 a 10 PM (segunda ronda)"
  }
 ],
 "caption": "#EpicPartner ¡Quedan 14 días para encontrar la Tienda Pirata! 🏴‍☠️ La búsqueda termina el 5 de diciembre\n¡Hoy hay Horas del Tesoro! Segunda ronda: 8 a 10 PM (CDMX) ⏰\n¿Ya te salió? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #tiendapirata #fortniteespañol",
 "claims": [
  {
   "text": "La búsqueda termina el 5 de diciembre (hoy 2026-11-21: quedan 14 días)",
   "source": "vgc-odds-confirmed.json",
   "match": [
    "is set to end on December 5"
   ],
   "visual": "2026-11-21 to 2026-12-05 is 14 days"
  },
  {
   "text": "1 en 137 500 por cofre",
   "source": "vgc-odds-confirmed.json",
   "match": [
    "Pirate Shop Access – 1 in 137,500"
   ]
  },
  {
   "text": "Horas del Tesoro el 21 de noviembre: 1 a 3 PM y 8 a 10 PM (CDMX), ×1.5",
   "source": "vice-treasure-hours.json",
   "match": [
    "| November 21 | 11 AM–1 PM PT / 2–4 PM ET | 6–8 PM PT / 9–11 PM ET | 1.5× both rounds |"
   ],
   "visual": "11 AM–1 PM and 6–8 PM Pacific (UTC-8) are 1–3 PM and 8–10 PM in Mexico City (UTC-6)"
  }
 ]
});
