# LA TIENDITA BAD

The daily Fortnite item shop in Latin American Spanish, for the social accounts that used to be StreakPros.
It is the BAD ROYALE shop video rebuilt in Spanish, with a few upgrades. Fan-made, not affiliated with
Epic Games; it supports creator code BAD (#EpicPartner on screen and in every caption).

Nothing here touches the website. It runs on the BrandPros shared engine (`brand/_shared`: the Kokoro
voice in `voice/vo.py`, the Playwright renderer in `video/render.mjs` with `lib.js`, `style2.js`, `mix.py`).
Copy this folder to `brand/tiendabad` in a BrandPros checkout, then `bash brand/tiendabad/build.sh`.

| File | What it does |
| --- | --- |
| `voice.json` | Voice: Kokoro `ef_dora`, `es-419` (Latin American "s"), speed 1.2 |
| `shop/shop_pick.py` | Reads today's shop from fortnite-api.com in `es-419`, picks the three stories, writes the Spanish script |
| `shop/scene.js` | The 1080x1920, 60 fps video |
| `video/templates_es.js` | BAD ROYALE's templates with the Spanish end card and the official Spanish map |
| `auto/new_where.py` | "¿Dónde está …?" map short (`python3 auto/new_where.py <folder> "<lugar>"`). It reads `sources/`: `map.json` from `https://fortnite-api.com/v1/map?language=es-419`, `map-labels.png` from `https://fortnite-api.com/images/map_es-419.png`, `map-blank.png` from `https://fortnite-api.com/images/map.png` |
| `brand/` | Brand art the videos load: `watermark.svg`, `awning.svg` and `bad.svg` (the end card) |
| `brand/logo/` | Logo masters (vector, text outlined): main logo, icon, wide logo, each in colour, white and black |
| `brand/src/` | Generators for the logos, social images and brand guide (`kit.py`, `social.py`); fonts go in `brand/src/fonts` or `$BRAND_FONTS` |
| `build.sh` | One day's shop video, start to finish |

## The three stories, every day

1. **Mejor oferta**: the bundle with the biggest drop from its regular price, the saving and the percent.
2. **Regreso OG**: the oldest outfit in the shop (chapter and season it came out) and how many days since
   it was last in the shop, from its shop history.
3. **Último día**: outfits whose listing ends at the next reset, 6 PM Mexico City time.

## What changed from the English video

- The hook shows all three stories up front (OFERTA / OG / ÚLTIMO DÍA), so viewers wait for them.
- Numbered beats, a savings sticker (-63% / AHORRAS 6500) and "146 días sin salir" for the OG.
- Times are in Mexico City time. Item names come from the game's own Spanish data.
- The end card is the shop itself: the brand awning over a turquoise counter with CÓDIGO BAD, and a
  reminder that the code lasts 14 days ("Dura 14 días · ¡renuévalo!").
- A LA TIENDITA BAD watermark (mini awning) on every frame.

## Brand

Rosa `#E4007C`, Amarillo `#FFD21E`, Turquesa `#13B5C6`, Tinta `#0B0D1A`; video background Noche `#1A1F7A` to
Morado `#2A0F5E`. Type: Barlow Condensed Black Italic (logo, titles) and Montserrat ExtraBold/Black (captions).

## Accounts

- Name: `LA TIENDITA BAD · Fortnite`. Handle: `@latienditabad`.
- TikTok bio: `La tienda de Fortnite cada día, en español. Código BAD #EpicPartner`
- Instagram / Threads bio: `La tienda de objetos de Fortnite cada día: ofertas, regresos OG y lo que se va. Hecho por fans, sin afiliación con Epic. Código BAD #EpicPartner`
- Facebook / YouTube: `La tienda de Fortnite cada día, en español. Código BAD en la tienda de objetos. Hecho por fans, sin afiliación con Epic Games. #EpicPartner`
