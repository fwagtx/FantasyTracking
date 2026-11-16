/* LA TIENDITA BAD · noticia del juego: Más polvo de espíritus y PE. Facts: sources/ (news.json, vgc-guide.json). */
window.SCENE = TPL.news({
 "kicker": "NOTICIAS DEL JUEGO",
 "hook": ["¡DOBLE PE", "EN FORTNITE!"],
 "hookY": 650,
 "tile": "Más polvo de espíritus y PE",
 "tileSize": 60,
 "quote": [
  {
   "p": 1,
   "text": "¡Te espera un montón de botín para saquear!"
  },
  {
   "p": 2,
   "text": "Doble polvo de espíritus y PE, además de cofres de espíritus potenciados y carteles de Se busca de espíritus."
  }
 ],
 "points": [
  {
   "p": 3,
   "label": "DOBLE",
   "text": "Polvo de espíritus y PE"
  },
  {
   "p": 4,
   "label": "POTENCIADOS",
   "text": "Cofres de espíritus"
  },
  {
   "p": 5,
   "label": "TIENDA PIRATA",
   "text": "Cualquier cofre puede darla"
  }
 ],
 "caption": "#EpicPartner Noticia nueva en Fortnite: ¡doble polvo de espíritus y PE! 👻 Además, cofres de espíritus potenciados y carteles de Se busca de espíritus\n¿Ya lo aprovechaste? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #noticiasfortnite #fortniteespañol",
 "claims": [
  {
   "text": "Noticia del juego: «Más polvo de espíritus y PE» (cita textual)",
   "source": "news.json",
   "match": [
    "\"title\": \"Más polvo de espíritus y PE\"",
    "\"body\": \"¡Te espera un montón de botín para saquear! Doble polvo de espíritus y PE, además de cofres de espíritus potenciados y carteles de Se busca de espíritus.\""
   ]
  },
  {
   "text": "Cualquier cofre en Battle Royale o Cero construcción puede dar la Tienda Pirata",
   "source": "vgc-guide.json",
   "match": [
    "To find one, you have to open chests during Battle Royale and Zero Build modes. This can be any type of chest",
    "every time you open or break a chest, there’s a small chance that you’ll get a treasure reward"
   ]
  }
 ]
});
