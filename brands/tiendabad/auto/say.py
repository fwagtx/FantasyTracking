"""How Kokoro es-419 should say names it would otherwise read letter by letter or with Spanish phonetics.

Shared by the daily shop video (shop/shop_pick.py) and the Tienda Pirata shorts (auto/new_pirata.py): a name the voice
gets wrong is fixed here once, for both. Keys are matched as plain text, so keep them to names, never Spanish words.
"""

SAY = {"$": "S", "Spider-Man": "Spáider Man", "Buggy": "Bugui", "Jason": "Yéison", "Like a G6": "Laik a yi six",
       "Volkswagen": "Folksvaguen", "Speedy": "Spidi", "Pierogi": "Pierogui", "Hatsune": "Jatsune", "Cyclone": "Saiclon",
       "Nike Air Force 1": "Naik Er Fors Uan",
       "Karol G": "Karol Yi",                  # the G would be read "je"
       "Noodle": "Núdel", "2D": "Tu Di",       # 2026-11-22: Lote Noodle y 2D (Gorillaz) would have been "No-o-dle y dos de"
       "Bandicoot": "Bándicut"}                # 2026-11-22: Crash Bandicoot would have been "Ban-di-co-ot"


def say_names(s):
    for a, b in SAY.items():
        s = s.replace(a, b)
    return s
