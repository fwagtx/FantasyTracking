"""How Kokoro es-419 should say names it would otherwise read letter by letter or with Spanish phonetics.

Shared by the daily shop video (shop/shop_pick.py) and the Tienda Pirata shorts (auto/new_pirata.py): a name the voice
gets wrong is fixed here once, for both. Keys are matched as plain text, so keep them to names, never Spanish words.
"""

SAY = {"A$AP Rocky": "Éisap Róqui",            # before "$": the voice read "ASAP" as "a-sáp"
       "$": "S", "Spider-Man": "Spáider Man", "Buggy": "Bugui", "Jason": "Yéison", "Like a G6": "Laik a yi six",
       "Volkswagen": "Folksvaguen", "Speedy": "Spidi", "Pierogi": "Pierogui", "Hatsune": "Jatsune", "Cyclone": "Saiclon",
       "Nike Air Force 1": "Naik Er Fors Uan",
       "Karol G": "Karol Yi",                  # the G would be read "je"
       "Noodle": "Núdel", "2D": "Tu Di",       # 2026-11-22: Lote Noodle y 2D (Gorillaz) would have been "No-o-dle y dos de"
       "Bandicoot": "Bándicut",                # 2026-11-22: Crash Bandicoot would have been "Ban-di-co-ot"
       "Spyro the Dragon": "Spáiro de Drágon", "Lachlan": "Láclan",   # 2026-11-22: Lote Crash Bandicoot y Spyro the Dragon, Lote Lachlan
       # 2026-11-23 (the week's shop): checked with Kokoro's phonemizer; left alone they came out as "Jak", "Jo-nes", "Elisabét",
       # "Gori-yás", "Ru-sél", "Overwátch", "De. Ba", "Tra-sér", "Jenji" (jota), "Te Kid", "Cowbói Bebóp", "Faye Valentíne",
       # "Spi-ke", "Mordécai", "Éidi", "Ludwíg", "One Pi-é-se", "San-ji" (jota)
       "Jack Sparrow": "Yak Spárrou", "Davy Jones": "Déivi Yons", "Elizabeth Swann": "Elísabet Suan",
       "Phineas": "Fíneas", "Dr. Doofenshmirtz": "Doctor Dúfenshmirtz", "Doofenshmirtz": "Dúfenshmirtz",
       "Gorillaz": "Gorílas", "Murdoc": "Múrdoc", "Russel": "Rásel",
       "Overwatch": "Óverguach", "D.Va": "Di Va", "Tracer": "Tréiser", "Genji": "Yen yi", "Widowmaker": "Uídou Méiker",
       "Ice Spice": "Ais Spais", "The Kid LAROI": "De Kid La Rói", "The Rogue LAROI": "De Róug La Rói",
       "Cowboy Bebop": "Cáuboi Bíbop", "Faye Valentine": "Féi Válentain", "Spike Spiegel": "Spáik Spíguel", "Mordecai": "Mórdecai",
       "Heidi": "Jáidi", "Ludwig": "Lúdvig", "One Piece": "Uan Pis", "Sanji": "San yi",
       "One Punch Man": "Uan Panch Man",       # 2026-11-23: Lote One Punch Man would have been "Óne Púnch Man"
       "Genos": "Yénos",                       # 2026-11-24: read with a jota ("Jénos"); the Japanese name is "Jenosu"
       "Persona 5 Royal": "Persona 5 Róial",   # 2026-11-25: "Royal" was read "ro-yál"; whole name keyed so "Battle Royale" stays as is
       "Panther (P5R)": "Pánter, de Persona 5 Róial",   # 2026-11-25: "(P5R)" was read "pe cinco erre"
       "Addison Rae": "Ádison Réi",            # 2026-11-25: was "ad-dí-son rá-e"
       "MrSavage": "Míster Sávich",            # 2026-11-25: was "eme-erre sa-vá-je"
       "Killian": "Kílian",                    # 2026-11-26: the "ll" was read as a y ("Ki-yian")
       "KizunaAI": "Kizuna Ai",                # 2026-11-26: "AI" was read "a-í" (like "ahí"); the name is "Kizuna Ai"
       "Bianca Belair": "Bianca Belér",        # 2026-11-26: "Belair" was read "be-láir"; it rhymes with "air"
       "Solo Leveling: ARISE": "Sólo Lévelin: Aráis",   # 2026-11-28: was "le-bé-ling: a-rí-se"; keyed before the bare name
       "Solo Leveling": "Sólo Lévelin"}        # 2026-11-28: the anime/game name on its own


def say_names(s):
    for a, b in SAY.items():
        s = s.replace(a, b)
    return s
