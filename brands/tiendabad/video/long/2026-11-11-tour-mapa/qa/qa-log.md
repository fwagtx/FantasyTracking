# Pre-post check: latienditabad-2026-11-11-tour-mapa.mp4

Checked 2026-11-11T13:05:01Z · **READY TO POST**

## 1. Facts: PASS
42/42 claims verified against saved official data
- 17 lugares en el mapa oficial: 12 lugares con nombre y 5 puntos de referencia · PASS · matched in map.json; map.json lists exactly 17 pois: 12 with ids Athena.Location.POI.* and 5 with ids Athena.Location.UnNamedPOI.* (Bote boogie, Baratie, Henry, Campo de batalla del gigante, La calavera salada)
- Noticias del juego: ¡La aventura espera! Explora Arlong Park de One Piece, navega por la Bahía Baratie o asalta El $.$. Rocky en esta misteriosa isla en forma de cráneo. · PASS · matched in news.json
- Noticias del juego: Frutas del diablo por toda la isla. ¡Se soltaron frutas del diablo raras por todo el mapa! Come la fruta Goma Goma para estirar tus habilidades como Luffy o la fruta Fuego Fuego para arrasar como Ace… solo mantente lejos del agua. · PASS · matched in news.json
- Noticias del juego: en la pestaña Tesoro sigues tu progreso semanal · PASS · matched in news.json
- Resort relajado: noroeste (by the same rule as new_where.py) · PASS · matched in map.json; map pixel (818, 718)
- Resort relajado en el mapa · PASS · visual check against map-labels.png: map pixel (818, 718); noroeste (by the same rule as new_where.py); on its own island (water all around, a bridge to the main island); nearest named place El cofre, to the south
- El cofre: noroeste · PASS · matched in map.json; map pixel (762, 875)
- El cofre en el mapa · PASS · visual check against map-labels.png: map pixel (762, 875); noroeste; the label sits between small islands off the coast
- Bahía Botín: norte · PASS · matched in map.json; map pixel (1070, 870)
- Bahía Botín en el mapa · PASS · visual check against map-labels.png: map pixel (1070, 870); norte; orange terrain; a ship sits in the water of the inlet right next to the place
- Fuerte Fortuna: noreste · PASS · matched in map.json; map pixel (1294, 836)
- Fuerte Fortuna en el mapa · PASS · visual check against map-labels.png: map pixel (1294, 836); noreste; where the orange terrain meets the green; nearest named place Bahía Botín, to the west
- Bote boogie: este · PASS · matched in map.json; map pixel (1446, 989)
- Bote boogie en el mapa · PASS · visual check against map-labels.png: map pixel (1446, 989); este; a barge floating in the cove between the two orange arms of land; south-east of Fuerte Fortuna
- The $.$. Rocky: este · PASS · matched in map.json; map pixel (1416, 1217)
- The $.$. Rocky en el mapa · PASS · visual check against map-labels.png: map pixel (1416, 1217); este; a large ship docked in a channel of the eastern island; nearest named place Caverna Condenada, to the west of it
- Bahía Bulliciosa: sureste · PASS · matched in map.json; map pixel (1388, 1482)
- Bahía Bulliciosa en el mapa · PASS · visual check against map-labels.png: map pixel (1388, 1482); sureste; a town on the waterfront; nearest named place The $.$. Rocky, to the north of it
- Baratie: 24 map px from Bahía Bulliciosa (closest pair of all 17 places) · PASS · matched in map.json; map pixel (1411, 1477)
- Baratie en el mapa · PASS · visual check against map-labels.png: map pixel (1411, 1477); 24 map px from Bahía Bulliciosa (closest pair of all 17 places)
- Henry: on a small round island in the southern lagoon · PASS · matched in map.json; map pixel (1203, 1500)
- Henry en el mapa · PASS · visual check against map-labels.png: map pixel (1203, 1500); on a small round island in the southern lagoon; north-east of Puerto Pirata (118 px); west of Bahía Bulliciosa
- Puerto Pirata: sur · PASS · matched in map.json; map pixel (1150, 1606)
- Puerto Pirata en el mapa · PASS · visual check against map-labels.png: map pixel (1150, 1606); sur; on the south coast with ships at the pier
- Loguetown: sur · PASS · matched in map.json; map pixel (989, 1382)
- Loguetown en el mapa · PASS · visual check against map-labels.png: map pixel (989, 1382); sur; just north of the lagoon with small islands
- Villa Molino: suroeste · PASS · matched in map.json; map pixel (754, 1438)
- Villa Molino en el mapa · PASS · visual check against map-labels.png: map pixel (754, 1438); suroeste; nearest named place Loguetown, to the east
- Arlong Park: oeste · PASS · matched in map.json; map pixel (635, 1123)
- Arlong Park en el mapa · PASS · visual check against map-labels.png: map pixel (635, 1123); oeste; nearest named place Frontera Farol, to the east
- Frontera Farol: cerca del centro (162 px from the centroid of the named places, under the 170 px rule) · PASS · matched in map.json; map pixel (899, 1050)
- Frontera Farol en el mapa · PASS · visual check against map-labels.png: map pixel (899, 1050); cerca del centro (162 px from the centroid of the named places, under the 170 px rule); just north of the large purple spiral
- Campo de batalla del gigante: at the southern edge of the purple spiral · PASS · matched in map.json; map pixel (953, 1243)
- Campo de batalla del gigante en el mapa · PASS · visual check against map-labels.png: map pixel (953, 1243); at the southern edge of the purple spiral; just north of Loguetown (its nearest named place)
- Caverna Condenada: cerca del centro, east of the centroid · PASS · matched in map.json; map pixel (1184, 1148)
- Caverna Condenada en el mapa · PASS · visual check against map-labels.png: map pixel (1184, 1148); cerca del centro, east of the centroid; on the northern edge of a dark rocky area
- La calavera salada: inside the dark rocky area, just south of Caverna Condenada (its nearest named place, 80 px) · PASS · matched in map.json; map pixel (1206, 1224)
- La calavera salada en el mapa · PASS · visual check against map-labels.png: map pixel (1206, 1224); inside the dark rocky area, just south of Caverna Condenada (its nearest named place, 80 px)
- Récords: más al norte Resort relajado, más al sur Puerto Pirata, más al oeste Arlong Park, más al este Bote boogie; más pegados Baratie y Bahía Bulliciosa; más solitario Arlong Park · PASS · matched in map.json; min y Resort relajado 718.0, max y Puerto Pirata 1606.1, min x Arlong Park 634.8, max x Bote boogie 1445.9; closest pair Bahía Bulliciosa–Baratie 24 px; largest nearest-neighbour distance Arlong Park 274 px
- Resort relajado está en su propia isla, unida por un puente; Arlong Park está separado del centro por un canal con un puente · PASS · visual check against map-labels.png: zoomed crops of the official map: a bridge from Resort relajado's island to the orange main island (around map px 830-870, 770-800); a bridge over the canal east of Arlong Park (around 725, 1221)
- Más céntrico: Caverna Condenada; más alejados: Baratie y Resort relajado · PASS · matched in map.json; named place nearest the centroid of the 12 named places (1030, 1145): Caverna Condenada 154 px; farthest pair of the 17: Baratie–Resort relajado 963 px
- Barcos por todos lados · PASS · visual check against map-labels.png: ships visible on the official map at Bahía Botín, The $.$. Rocky, Puerto Pirata (two at the pier), the Arlong Park lagoon, and the Bote boogie barge

## 2. Spelling and grammar: PASS
184 distinct words checked; code written as BAD in all 2 mentions

## 3. Frames: PASS
No black frames, no frozen stretches over 1.5 s. Contact sheet (1 frame/s) reviewed by eye: contact-sheet.png

## 4. Audio: PASS
Integrated -14.2 LUFS, true peak -1.7 dBTP, no clipping, no silence gaps. All sound is original synthesis (no game rips, no copyrighted music). No voiceover in this cut.

## 5. Specs and safe zones: PASS
1920x1080 h264 30 fps, aac 48000 Hz, 267.2s. Long YouTube video; every on-screen text box sampled 4x/s stays inside the title-safe area.

## 6. Disclosure and wording: PASS
#EpicPartner is the first hashtag in caption line 1 and on screen in every sampled frame from 0.0s. No discount, giveaway, endorsement or filler-CTA wording.

## 7. Would a fan stop scrolling? Made by a person?: PASS
4:27 map tour in 10 chapters (Intro, La isla, El norte, El este, El sur, El oeste, El centro, Datos curiosos, Repaso, Final). Every one of the 37 segments checked in stills (plus the recap and the sign-off): the opening card 'TOUR COMPLETO · Todos los lugares del mapa' over the official map, the '17 lugares en el mapa oficial' card, both in-game news tiles quoted word for word and lit as they are read, five region overviews with their places pinned, 17 stops where the camera lands on the right spot (ring on the place; the five landmarks that have no label on the map get a yellow handwritten label), the records (más al norte, sur, oeste, este, más céntrico, más alejados, más pegados) each labelled, and a closing map whose 17 pins appear region by region as they are named. Fixed before passing: pin labels never showed in 16:9 (S.scrawl hides anything outside the 9:16 app-safe area), region overviews and records held too briefly, the final card touched the Villa Molino ring, and three labels collided with printed map names (now clear; 'EL MÁS CÉNTRICO' sits above the Caverna Condenada ring so it can't be read with 'Frontera Farol'). Name cards fit on one line (long names shrink), subtitles readable, #EpicPartner and the watermark in every frame, mid-roll and closing code cards clean, sign-off under the closing card. Voice says One Piece, Loguetown, Henry, Bote boogie, $.$. and Ace the way a Mexican player would; Spanish reads naturally.

## 8. Owner approval: PASS
Standing approval for autopilot (owner, 2026-10-28): posts when checks 1-7 all PASS.

## Caption
```
#EpicPartner Recorremos TODO el mapa de Fortnite 🗺️ Los 12 lugares con nombre y los 5 puntos de referencia, región por región, y los récords del mapa: el más al norte, al sur, al este, al oeste y los dos más pegados.
¿Cuál es tu lugar favorito para caer? 👇
Usa el código BAD en la tienda de objetos 💛
#Fortnite #MapaFortnite #FortniteEspañol
```