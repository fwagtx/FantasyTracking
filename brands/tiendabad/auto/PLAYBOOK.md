# LA TIENDITA BAD autopilot playbook

The daily Fortnite item shop and news in Latin American Spanish, on the accounts that used to be StreakPros.
The owner (2026-11-09): *"we should be posting 3x a day on every platform and we should post 3x long form
youtube videos a week."* Runs decide and continue on their own, so the checks below are the only safety net:
**when anything is off, skip that post and say why in the summary. Never guess, never post something that didn't pass.**
A skipped slot costs nothing; a wrong post costs the channel.

## Hard rules (every run)

1. **Metricool brand `7065085` (LA TIENDITA BAD) only.** Never read, write or mention any other brand.
   Don't call getBrandSettings. Times are America/Mexico_City (CDMX).
2. **Disclosure:** every caption starts with `#EpicPartner`, and `#EpicPartner` is on screen in every frame. The code
   ask is exactly "Usa el código BAD en la tienda de objetos". Never call the code a discount (descuento) or free,
   never promise rewards or giveaways, never imply Epic sponsors or endorses the channel.
3. **Facts only from saved sources:** `brands/tiendabad/sources/` (fetch_sources.py: official map, places and in-game
   news, es-419), that day's shop folder, or a series' saved sources folder (like `video/auto/pirata-sources/`).
   Every on-screen fact is a claim in `SCENE.claims` that matches the source text. No leaks, no rumors, no
   "coming soon", no invented dates.
4. **Voice:** `brands/tiendabad/voice.json` (Kokoro `ef_dora`, es-419, speed 1.2), applied by the scripts; never
   edit it. Write lines the way they should sound: questions with ¿?, excitement with ¡!. BAD is said as the
   word "bad": give the code line a `say` of "Usa el código bad, en la tienda de objetos. ...". **Every script
   ends** with the code line and then exactly `¡Gracias por ver! Síguenos para más contenido diario.`
5. **Art:** skin and item art only inside videos about that item (Epic's fan content policy).
6. **Labels:** `isAigc`, `isAiGenerated`, `isAiGeneratedContent` stay false; TikTok `commercialContentThirdParty`
   is true (the code earns a commission). The scripts already write this.
7. **No drafts:** posts go in with `"draft": false` and `"autoPublish": true`, exactly as posts.json has them.
8. **Captions** in natural Mexican/Latin American Spanish: hook line, one or two short lines of the best facts with
   a few emojis, one question that pulls comments, then `Usa el código BAD en la tienda de objetos 💛`, then 3
   hashtags. Threads text stays under 500 characters (the scripts check).
9. **Git:** commit only small files (brands/.gitignore keeps video, audio, images and posts.json out) and push with
   `git push origin HEAD:claude/project-automation-setup-ms0f8e`. Never force-push, never touch other branches.
10. **Networks:** TikTok, Instagram (Reels), Facebook (Reels), YouTube (Shorts), Threads. If Metricool rejects one
   network (for example Instagram while it is still being verified), the others still go; name it in the summary.

## Schedule (America/Mexico_City)

| Run | When | Posts |
|---|---|---|
| **SHOP** | 6:05 PM daily (00:05 UTC), right after the shop resets | Today's shop video about 15 min later, all 5 networks |
| **SHOP safety** | 7:47 PM daily | The SHOP run again; it ends at step 3 if the shop video is already scheduled |
| **DAY** | 11:07 AM daily | **1:00 PM** today's current short (made that morning) and **8:30 PM** evergreen (kept 7 days ahead) |
| **LONG** | 5:13 AM Mon, Wed, Sat | One long YouTube video at **3:00 PM** |

3 shorts a day on every network (1:00 PM, about 6:25 PM, 8:30 PM) plus 3 long YouTube videos a week.

## Setup (start of every run)

1. `git pull origin claude/project-automation-setup-ms0f8e`
2. `bash brands/tiendabad/auto/setup.sh` (installs what's missing; hosting needs the R2 key pair in the
   environment plus TB_R2_BUCKET / TB_R2_ACCOUNT_ID or the owner's BrandPros checkout; if setup says STOP, post
   nothing and say so).

## SHOP run

3. D = `date -u +%F`. **Already posted?** getScheduledPosts (brandId 7065085, timezone America/Mexico_City, D-1 12:00
   to D 23:59): if every network already has a post whose text starts `#EpicPartner Esta es la tienda de objetos de hoy, <día> de <mes>`
   for D, stop and log "shop already posted". Otherwise `python3 brands/tiendabad/auto/shop_daily.py build D`
   (it waits up to 25 min for the shop to turn over).
4. Open `brands/tiendabad/shop/D/qa/review-sheet.png` and look hard: frame 1 shows the date and three items at once,
   real items on every tile, prices and names readable, nothing cut off or overlapping, the code card at the end.
   Read the voice lines it printed: the first is "Esta es la tienda de objetos de hoy, <día> de <mes>." and they all
   make sense spoken. Write `brands/tiendabad/shop/D/review.json`: `{"status": "PASS", "notes": "..."}` or FAIL with
   the reason (then skip to step 9).
5. `python3 brands/tiendabad/auto/shop_daily.py ship D`. If it prints STOP, post nothing; go to step 9.
6. Read `brands/tiendabad/shop/D/posts.json`. For each entry in `calls` not already scheduled (match its `match` text
   against getScheduledPosts), call createScheduledPost with that entry's `blogId`, `date` and `info` exactly as
   given (info as a JSON string). If Metricool answers VIDEO_THUMBNAIL_NOT_APPLICABLE, retry that call without
   `videoThumbnailUrl`. If it says "Failed to normalize media", run `python3 brands/_shared/store.py link shop-D/video.mp4`,
   swap the link in, retry once.
7. getScheduledPosts again: confirm the new posts, draft false, at the time ship printed.
8. `python3 brands/tiendabad/auto/shop_daily.py done D`
9. Append one line to `brands/tiendabad/auto/log.md`, commit `brands/tiendabad/shop brands/tiendabad/auto/log.md`
   ("Tienda D"), push (rule 9). Summary: the post time and networks, or exactly what stopped it.

## DAY run

3. `python3 brands/tiendabad/auto/fetch_sources.py` (prints places and news tiles added or removed).
4. **1:00 PM current short, every day** (if today's 1:00 PM slot is empty). Pick, in order:
   a. a planned series post for today (below);
   b. a news tile added since yesterday, explained with `TPL.news` (quote the tile, say what it means);
   c. a current-season place on the map with `new_where.py` (a place never used in the ledger).
   Never fill a 1:00 PM slot for a later day.
5. **Keep the 8:30 PM evergreen slot 7 days ahead**, at most 2 new shorts per run, earliest empty day first:
   two "¿Dónde está…?" (`python3 brands/tiendabad/auto/new_where.py <folder> "<lugar de map.json>"`, a place never in
   the ledger) for every one "Adivina la temporada" (`python3 brands/tiendabad/auto/new_guess.py <folder> auto`: three
   outfits from today's shop that are not in the ledger; ship it with `--sources <folder>/sources`).
6. For each short: folder `brands/tiendabad/video/auto/<D>-<slug>/`, then
   `python3 brands/tiendabad/auto/make_short.py build <folder>`, open `<folder>/qa/review-sheet.png` and judge it like
   a fan scrolling (frame 1 stops you; every word readable on a phone; nothing overlapping or cut off; fix and
   rebuild up to 3 times), write `<folder>/review.json`, then
   `python3 brands/tiendabad/auto/make_short.py ship <folder> --at "<D> 13:00"` (or 20:30) with `--sources <dir>` when
   the facts come from a series folder. On STOP read `<folder>/qa/qa-log.md`, fix, retry (up to 3 times), else skip.
7. Schedule posts.json exactly as in SHOP 6–7, then `python3 brands/tiendabad/auto/make_short.py done <folder>`.
8. Add each posted topic to `brands/tiendabad/auto/ledger.json`; never repeat a topic. An "Adivina la temporada" entry
   also lists its outfits in `"ids"` (new_guess.py prints them), which is how the next quiz skips them.
9. Check yesterday: getScheduledPosts for D-1; any network not PUBLISHED goes in the summary with its detailedStatus.
10. Log line, commit `brands/tiendabad/video/auto brands/tiendabad/auto brands/tiendabad/sources/manifest.json`, push, summary.

### Already queued: the Tienda Pirata series (made 2026-11-09, leave these posts alone)

Facts from `video/auto/pirata-sources/` (press reports quoting Epic's Fortnite Status post of Nov 7, 2026), built with
`TPL.guide` and scheduled on all networks: Nov 10, 11, 12 and 13 at 1:00 PM (probabilidades, escuadrón, lo que no
incluye, mitos) and Sat Nov 14 at 11:00 AM (Horas del Tesoro: 1–3 PM and 8–10 PM CDMX, ×1.5). On those days the
1:00 PM slot is taken, so the DAY run only does the evergreen slot. If the in-game news or fetch_sources shows the
Treasure Hours schedule or the Pirate Shop rules changed, set the affected queued posts to draft (Facts change).
Extra pirate posts on Nov 10 (the owner asked for more that day): 10:30 AM "las 5 fechas" countdown (TPL.top),
4:00 PM "¿Qué cofres cuentan?" and 10:00 PM "Los 3 niveles de tesoro" (TPL.guide), facts from
`video/auto/pirata-sources-2026-11-10/` (the five articles re-fetched live on Nov 10, passages copied verbatim, plus Epic's
shop data for the One Piece leave date). The Big Lead lists the 2026 activation window as ending Dec 16; VGC (citing Epic)
says Dec 15: always use Dec 15. To make more pirate posts later, refresh the sources first (they must be under 24 h old when shipping), from the
BAD ROYALE branch's `brand/badroyale/video/auto/pirate-shop-sources/` (read only), or skip.

## Facts change: pull outdated posts

If fetch_sources.py reports `news_removed` or `places_removed`, check the next 7 days of posts against the ledger:
a post whose fact is gone goes back to draft with updateScheduledPost (same content, `"draft": true`) and its slot
is refilled.

## LONG run (Mon, Wed, Sat; posts 3:00 PM)

Mon "La tienda de la semana" (from `shop/archive/`), Wed a map tour or "todos los X del mapa" (map.json), Sat the
week's news and the Tienda Pirata guide while the season runs. 4–10 minutes, 1920x1080, YouTube only
(`make_long.py`, once it is in this folder; until then skip and say so).

## If something breaks

- A script fails: read its error, fix the cause if it is in brands/ (a bug, a missing allowlist word for a real
  name), commit the fix, try once more. Never weaken a check to get a post through.
- fortnite-api.com is down: skip what needs it; the next run tries again.
- Metricool rejects a call: retry once if the error is about media; otherwise skip that network and report it.
