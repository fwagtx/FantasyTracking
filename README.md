# Dynasty League Explorer

Flask app: type a Sleeper username, see every dynasty league, standings, and
positional value breakdowns using dynasty trade values from FantasyCalc's
public API. Includes rankings, a photo-based trade calculator, ADP data,
player profiles, and Gemini-powered chat.

## Required environment variables

| Variable | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | yes | Powers the `/chat` assistant |
| `SITE_PASSWORD` | yes | Shared secret for `/api/sync-stats`; also the fallback Flask session key |
| `DATABASE_URL` | no (accounts/votes/stats disabled without it) | Postgres connection string (e.g. Neon) |
| `FLASK_SECRET` | no | Explicit Flask session secret (defaults to a value derived from `SITE_PASSWORD`) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | no | Enables "Sign in with Google" |
| `SLEEPER_USERNAME` | no | Prefills your username on the chat page |
| `SEASON` | no | Overrides the auto-detected current NFL season |
| `PORT` | no | Local dev server port (default `5000`) |

## Deploying (Render)

`render.yaml` is included as a Render Blueprint. Create a new Blueprint
instance from this repo, then fill in the env vars above in the Render
dashboard (they're intentionally left unset in `render.yaml` since they're
secrets). The service starts via `gunicorn webapp:app`.

Postgres isn't provisioned by the blueprint — point `DATABASE_URL` at your
own instance (e.g. a free Neon database).

## Automated stats sync (GitHub Actions)

Two workflows under `.github/workflows/` keep player stats fresh by calling
the deployed site's `/api/sync-stats` endpoint:

- **`sync-stats.yml`** — runs every 2 hours automatically, syncing the
  current season only.
- **`backfill-stats.yml`** — manual only (`workflow_dispatch`), loops over
  every season from 2015 to present. Run this once after first deploying,
  then let `sync-stats.yml` handle the rest.

Both call `https://fantasyfootballcalc.com/api/sync-stats` — update that
hostname in both workflow files if the app is deployed elsewhere.

- **`keep-warm.yml`** — runs every 12 minutes, pinging `/healthz` (keeps
  Render's free-tier worker from spinning down after ~15 min idle) and
  `/api/warm` (refreshes the in-memory player/trade-value/ADP caches in the
  background, so a real visitor is never the one who pays a cold-fetch
  cost). Both endpoints are safe to call repeatedly — the underlying
  caches no-op unless their own TTL has actually expired.

### One-time setup required

These workflows only work once a **`SITE_PASSWORD` repository secret** is
added (Settings → Secrets and variables → Actions → New repository
secret), matching the `SITE_PASSWORD` env var set on the deployed app.
Without it, `/api/sync-stats` returns `401 unauthorized` and every run
fails.

To manually trigger a sync (or the initial backfill), go to the Actions
tab, pick the workflow, and click "Run workflow".
