/* LA TIENDITA BAD · noticia del juego: Semana dorada + Día de la comunidad. Facts: sources/ (news.json, vgc-guide.json). */
window.SCENE = TPL.news({
 "kicker": "NOTICIAS DEL JUEGO",
 "hook": [
  "¡SEMANA DORADA",
  "EN FORTNITE!"
 ],
 "hookY": 820,
 "tile": "Semana dorada + Día de la comunidad",
 "tileSize": 58,
 "quote": [
  {
   "p": 1,
   "text": "A partir del 28/11, el botín actualizado se une al tesoro desenterrado durante la Semana dorada,"
  },
  {
   "p": 2,
   "text": "donde los bienes dorados de cada Capitán vuelven a estar en cofres junto con el doble de polvo de espíritu y PE."
  },
  {
   "p": 3,
   "text": "Luego, súbete a bordo para el Día de la comunidad el 5/12."
  }
 ],
 "points": [
  {
   "p": 4,
   "label": "28 DE NOVIEMBRE",
   "text": "Bienes dorados de vuelta en los cofres"
  },
  {
   "p": 5,
   "label": "DOBLE",
   "text": "Polvo de espíritu y PE"
  },
  {
   "p": 6,
   "label": "5 DE DICIEMBRE",
   "text": "Día de la comunidad"
  }
 ],
 "caption": "#EpicPartner Noticia nueva en Fortnite: ¡la Semana dorada empieza el 28 de noviembre! ✨ Los bienes dorados de cada Capitán vuelven a los cofres, con doble polvo de espíritu y PE. Y el 5 de diciembre, Día de la comunidad\n¿Lo vas a aprovechar? 👇\nUsa el código BAD en la tienda de objetos 💛\n#fortnite #noticiasfortnite #fortniteespañol",
 "claims": [
  {
   "text": "Noticia del juego: «Semana dorada + Día de la comunidad» (cita textual)",
   "source": "news.json",
   "match": [
    "\"title\": \"Semana dorada + Día de la comunidad\"",
    "\"body\": \"A partir del 28/11, el botín actualizado se une al tesoro desenterrado durante la Semana dorada, donde los bienes dorados de cada Capitán vuelven a estar en cofres junto con el doble de polvo de espíritu y PE. Luego, súbete a bordo para el Día de la comunidad el 5/12.\""
   ]
  },
  {
   "text": "La búsqueda del tesoro (la temporada) termina el 5 de diciembre",
   "source": "vgc-guide.json",
   "match": [
    "The season is set to end on December 5"
   ]
  },
  {
   "text": "Cualquier cofre (en Battle Royale o Cero construcción) puede dar la Tienda Pirata",
   "source": "vgc-guide.json",
   "match": [
    "To find one, you have to open chests during Battle Royale and Zero Build modes. This can be any type of chest",
    "every time you open or break a chest, there’s a small chance that you’ll get a treasure reward"
   ]
  }
 ]
});
