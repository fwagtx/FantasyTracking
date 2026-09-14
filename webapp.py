"""
Dynasty League Explorer
--------------------------
Public site: type any Sleeper username, see every dynasty league, full
standings, and a Flock-Fantasy-style positional value breakdown for every
team using real dynasty trade values.
Includes Rankings, a photo-based live-search Trade Calculator (Dynasty/
Redraft + 1QB/Superflex, with real draft-pick pricing and optional league
linking so you can click straight from a roster instead of typing), real
ADP on player pages via Fantasy Football Calculator's public API, and
clickable player profiles. Player photos come from Sleeper's official
headshot CDN.

Required environment variables:
  GEMINI_API_KEY, SITE_PASSWORD
Optional:
  SLEEPER_USERNAME (prefills your username on the chat page)
"""

import bisect
import gzip
import html
import math
import os
import random
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import requests
import psycopg2
import psycopg2.extras
import psycopg2.pool
from flask import Flask, request, session, redirect, render_template_string, jsonify, url_for, make_response
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SITE_PASSWORD = os.environ["SITE_PASSWORD"]
MY_USERNAME = os.environ.get("SLEEPER_USERNAME", "")
def current_nfl_season():
    """NFL seasons are named for the year they start in (the '2026
    season' runs Sept 2026 through the Feb 2027 Super Bowl). January and
    February still belong to the season that started the previous fall;
    March onward, we've rolled into the new one."""
    today = date.today()
    return today.year - 1 if today.month <= 2 else today.year


SEASON = os.environ.get("SEASON") or str(current_nfl_season())
GEMINI_MODEL = "gemini-2.5-flash"

SLEEPER_BASE = "https://api.sleeper.app/v1"
FANTASYCALC_BASE = "https://api.fantasycalc.com/values/current"
ADP_BASE = "https://fantasyfootballcalculator.com/api/v1/adp/ppr"
POSITIONS = ["QB", "RB", "WR", "TE"]
IDP_POSITIONS = ["DL", "LB", "DB"]
# Every position the performance board scores. Deliberately WIDER than
# POSITIONS, and deliberately not used anywhere that needs a dynasty
# value: FantasyCalc only publishes QB/RB/WR/TE, so Rankings, the Trade
# Calculator and Matchup Grades stay on POSITIONS rather than listing
# defenders they could never price. Performances and depth charts run on
# real stats, which Sleeper does provide for defenders, so those use
# this.
# Kickers score through Sleeper's own pts_ppr like everyone else, so
# they need nothing but a place in this list to appear on the board.
KICKER_POSITIONS = ["K"]
SCORED_POSITIONS = POSITIONS + IDP_POSITIONS + KICKER_POSITIONS
IDP_POSITION_MAP = {
    "DE": "DL", "DT": "DL", "NT": "DL", "DL": "DL",
    "LB": "LB", "OLB": "LB", "ILB": "LB", "MLB": "LB",
    "CB": "DB", "S": "DB", "FS": "DB", "SS": "DB", "DB": "DB",
}

PICK_ICON = "data:image/svg+xml;utf8," + urllib.parse.quote(
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
    '<rect width="40" height="40" rx="9" fill="#b97a1f"/>'
    '<text x="20" y="25" font-size="12" text-anchor="middle" fill="white" '
    'font-family="monospace" font-weight="bold">PICK</text></svg>'
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-me-" + SITE_PASSWORD)
# "Remember me" (login_user(..., remember=True)) issues a persistent cookie
# that can live for up to a year -- make sure it (and the regular session
# cookie) is only ever sent over HTTPS, which is all this app is served
# over in production (Render).
app.config["SESSION_COOKIE_SECURE"] = True
app.config["REMEMBER_COOKIE_SECURE"] = True
app.config["REMEMBER_COOKIE_HTTPONLY"] = True

_COMPRESSIBLE_MIMETYPES = (
    "text/html", "text/css", "text/javascript", "text/plain",
    "application/javascript", "application/json", "image/svg+xml",
)


@app.after_request
def _compress_response(response):
    """Gzips every text/HTML/JSON response for a client that says it can
    accept it -- this app's pages (Matchups, Rankings, League Manager)
    render as one large inline HTML/CSS/JS blob with no separate static
    assets, so nothing else on the page benefits from a browser cache;
    shrinking the actual bytes sent is the one transfer-time win
    available for every page load, at essentially zero cost (a few ms of
    CPU) since Flask already buffers the whole body before this hook
    runs. Skips anything already encoded, streamed responses (direct_
    passthrough, e.g. a future file download), non-2xx bodies, tiny
    bodies where the gzip header overhead isn't worth it, and non-text
    mimetypes (images/fonts are already compressed formats)."""
    try:
        if (
            response.direct_passthrough
            or "gzip" not in (request.headers.get("Accept-Encoding", "")).lower()
            or "Content-Encoding" in response.headers
            or not (200 <= response.status_code < 300)
            or not (response.mimetype or "").startswith(_COMPRESSIBLE_MIMETYPES)
        ):
            return response
        body = response.get_data()
        if len(body) < 500:
            return response
        compressed = gzip.compress(body, compresslevel=6)
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(compressed))
        vary = response.headers.get("Vary", "")
        if "accept-encoding" not in vary.lower():
            response.headers["Vary"] = (vary + ", Accept-Encoding").lstrip(", ")
    except Exception:
        # Never let a compression bug turn into a broken page -- worst
        # case, this request just goes out uncompressed.
        pass
    return response

# ---------------- Accounts: database ----------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")


_db_pool = None
_db_pool_lock = threading.Lock()
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))


def _get_db_pool():
    """Lazily creates the connection pool on first real use (DATABASE_URL
    can be empty in local/dev/test runs, and importing this module must
    never fail just because Postgres isn't configured)."""
    global _db_pool
    if _db_pool is None:
        with _db_pool_lock:
            if _db_pool is None:
                _db_pool = psycopg2.pool.ThreadedConnectionPool(
                    1, DB_POOL_MAX, DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor,
                )
    return _db_pool


class _PooledConnection:
    """Every one of this file's many get_db() call sites already follows
    `conn = get_db(); try: ... finally: conn.close()` -- opening a brand
    new TCP+TLS connection to Postgres for every single query (the old
    behavior) was a real, avoidable chunk of every page's load time,
    multiplied by how many self-healing checks (ensure_schedule_synced,
    get_season_stats, get_schedule_for_team_week, ...) a single request
    can now trigger. This wraps a pooled connection so `.close()` hands
    it back to the pool instead of tearing it down -- none of those
    existing call sites need to change. Rolling back before returning it
    clears any uncommitted/aborted transaction state first, which is
    exactly what closing a raw connection without a prior commit()
    already did -- so this preserves today's behavior, it doesn't change
    it, while actually reusing the underlying connection.

    A raw psycopg2 connection tolerates being close()d more than once --
    it's a harmless no-op. Returning the SAME connection to a pool twice
    is not: the pool can't find it the second time and raises, and if
    that ever happened right after a legitimate first return, a second
    caller could receive it from getconn() while the code that "closed"
    it the first time still thinks it owns it. close() here is made
    idempotent (and never lets a pool-level error escape to break a
    request) specifically so this wrapper is at least as forgiving as
    the raw connection it replaces."""
    __slots__ = ("_conn", "_pool", "_returned")

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._returned = False

    def close(self):
        if self._returned:
            return
        self._returned = True
        try:
            self._conn.rollback()
        except Exception:
            # A dead/broken connection (the rollback itself failed) must
            # not go back into circulation for the next borrower to trip
            # over -- tell the pool to actually discard it instead.
            try:
                self._pool.putconn(self._conn, close=True)
            except Exception:
                pass
            return
        try:
            self._pool.putconn(self._conn)
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


def get_db():
    """A pooled connection -- see _PooledConnection's docstring. Falls
    back to a plain unpooled connection if the pool can't be created
    (e.g. a malformed DATABASE_URL), so a pool-specific failure doesn't
    take down every DB-using route that already worked before pooling
    existed."""
    try:
        pool = _get_db_pool()
        return _PooledConnection(pool.getconn(), pool)
    except Exception:
        return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    if not DATABASE_URL:
        return
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE,
                    password_hash TEXT,
                    username TEXT UNIQUE NOT NULL,
                    oauth_provider TEXT,
                    oauth_sub TEXT,
                    referral_code TEXT,
                    newsletter_opt_in BOOLEAN DEFAULT TRUE,
                    is_member BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE (oauth_provider, oauth_sub)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS votes (
                    id SERIAL PRIMARY KEY,
                    sleeper_id TEXT NOT NULL,
                    player_name TEXT,
                    position TEXT,
                    label TEXT NOT NULL CHECK (label IN ('start', 'bench', 'cut')),
                    user_id INTEGER REFERENCES users(id),
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_votes_sleeper_id ON votes (sleeper_id);")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users (lower(username));")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS player_stats (
                    sleeper_id TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    week INTEGER NOT NULL,
                    fpts REAL DEFAULT 0,
                    off_snp INTEGER DEFAULT 0,
                    tm_off_snp INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (sleeper_id, season, week)
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_player_stats_lookup ON player_stats (sleeper_id, season);")
            # get_season_stats -- the single most-called query in the app
            # now that matchup grading pulls it for the current season,
            # last season, and (for head-to-head history) up to 6 seasons
            # back -- filters on season ALONE. Neither the primary key
            # nor idx_player_stats_lookup above are usable for that (both
            # lead with sleeper_id), so every one of those calls was
            # doing a full sequential scan of a table that only grows.
            cur.execute("CREATE INDEX IF NOT EXISTS idx_player_stats_season ON player_stats (season);")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS sleeper_username TEXT;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS synced_leagues (
                    user_id INTEGER REFERENCES users(id),
                    league_id TEXT NOT NULL,
                    PRIMARY KEY (user_id, league_id)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS nfl_schedule (
                    espn_event_id TEXT PRIMARY KEY,
                    season INTEGER NOT NULL,
                    week INTEGER NOT NULL,
                    season_type INTEGER NOT NULL DEFAULT 2,
                    kickoff TIMESTAMP,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    home_score INTEGER,
                    away_score INTEGER,
                    status TEXT,
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nfl_schedule_season_week ON nfl_schedule (season, week);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nfl_schedule_home_team ON nfl_schedule (season, home_team);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_nfl_schedule_away_team ON nfl_schedule (season, away_team);")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS referee_games (
                    espn_event_id TEXT PRIMARY KEY,
                    season INTEGER NOT NULL,
                    week INTEGER NOT NULL,
                    referee_name TEXT,
                    officials_json JSONB,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    home_score INTEGER,
                    away_score INTEGER,
                    home_penalties INTEGER,
                    home_penalty_yards INTEGER,
                    away_penalties INTEGER,
                    away_penalty_yards INTEGER,
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_referee_games_referee ON referee_games (referee_name);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_referee_games_season ON referee_games (season);")
            # One row per player, holding the injury designation we last
            # saw and the one before it. Sleeper publishes only the
            # CURRENT designation -- "Out -> Active" is not a thing you
            # can fetch, it is a thing you have to have been watching
            # for. This table is the watching.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS player_injury_state (
                    sleeper_id      TEXT PRIMARY KEY,
                    status          TEXT,
                    previous_status TEXT,
                    changed_at      TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_injury_state_changed "
                        "ON player_injury_state (changed_at DESC);")
        conn.commit()
    finally:
        conn.close()


if DATABASE_URL:
    init_db()

# ---------------- Accounts: Flask-Login ----------------

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.email = row["email"]
        self.username = row["username"]
        self.is_member = row["is_member"]
        self.sleeper_username = row.get("sleeper_username")


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return User(row) if row else None
    finally:
        conn.close()


def username_valid(u):
    return bool(u) and 1 <= len(u) <= 20 and re.fullmatch(r"[A-Za-z0-9_]+", u)


def username_available(u):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE lower(username) = lower(%s)", (u,))
            return cur.fetchone() is None
    finally:
        conn.close()


def get_synced_league_ids(user_id):
    """None means the user has never made a selection yet -- show the
    picker. A saved selection always has at least one league (the picker
    requires picking at least one before it submits)."""
    if not DATABASE_URL:
        return None
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT league_id FROM synced_leagues WHERE user_id = %s", (user_id,))
            rows = cur.fetchall()
            if not rows:
                return None
            return {r["league_id"] for r in rows}
    finally:
        conn.close()


def set_synced_league_ids(user_id, league_ids):
    if not DATABASE_URL:
        return
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM synced_leagues WHERE user_id = %s", (user_id,))
            if league_ids:
                psycopg2.extras.execute_values(
                    cur, "INSERT INTO synced_leagues (user_id, league_id) VALUES %s",
                    [(user_id, lid) for lid in league_ids],
                )
        conn.commit()
    finally:
        conn.close()

# ---------------- Accounts: Google OAuth ----------------

oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def pick_offense_trio(num_qbs=1, is_dynasty=True):
    fc = get_fantasycalc_values(num_qbs, is_dynasty)
    all_players = get_all_players()
    candidates = []
    for sid, v in fc["players"].items():
        p = all_players.get(sid)
        if not p or v.get("position") not in POSITIONS or not v.get("value"):
            continue
        candidates.append((sid, v["value"]))
    if len(candidates) < 20:
        return []

    candidates.sort(key=lambda x: x[1])
    idx = random.randint(0, len(candidates) - 10)
    window = candidates[idx: idx + 10]
    random.shuffle(window)

    trio = []
    for sid, val in window[:3]:
        p = all_players[sid]
        trio.append({
            "sid": sid, "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position": p.get("position"), "team": p.get("team") or "FA",
            "age": p.get("age"), "photo": player_photo_url(sid), "value": val,
        })
    return trio


def get_total_vote_count(cache={}):
    """Real count of rows in the votes table, cached 10 min. Used to know
    whether we're still in the 'gathering data' phase. Returns 0 if the
    database isn't reachable, which just keeps us in the cautious phase."""
    now = time.time()
    if "count" in cache and now - cache.get("time", 0) < 600:
        return cache["count"]
    if not DATABASE_URL:
        return 0
    try:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM votes")
                count = cur.fetchone()["c"]
        finally:
            conn.close()
        cache["count"] = count
        cache["time"] = now
        return count
    except Exception:
        return cache.get("count", 0)


COMMUNITY_SCORE_MIN_VOTES = 10


def get_community_score(sid):
    """Real Start/Bench/Cut sentiment for one player, computed live from
    the votes table: (start - cut) / total, as a percentage. Bench votes
    are neutral -- they don't push the score either direction. Returns
    None (not a fabricated 0%) if there aren't enough real votes yet to
    mean anything, per COMMUNITY_SCORE_MIN_VOTES."""
    if not DATABASE_URL:
        return None
    try:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT label, COUNT(*) AS c FROM votes WHERE sleeper_id = %s GROUP BY label", (sid,))
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return None

    counts = {"start": 0, "bench": 0, "cut": 0}
    for row in rows:
        if row["label"] in counts:
            counts[row["label"]] = row["c"]
    total = sum(counts.values())
    if total < COMMUNITY_SCORE_MIN_VOTES:
        return None

    pct = round(100 * (counts["start"] - counts["cut"]) / total, 1)
    return {"pct": pct, "total": total}


def pick_idp_trio():
    """Same idea as pick_offense_trio, but for IDP -- there's no licensed
    dynasty value or ADP source we have access to for defensive players,
    so real season fantasy points (computed from Sleeper's own raw
    defensive stats) is the fairest 'similar tier' signal available.

    Only real, currently-rostered players are eligible (Active or
    Injured Reserve, with a real team assigned) -- practice squad players
    and free agents are excluded since they carry no real fantasy value.

    While vote data is still thin (early on), the pool is narrowed to the
    top real producers by season points, so people are voting on players
    they actually recognize instead of deep bench names. Once real vote
    volume builds up, the pool opens up to everyone eligible."""
    all_players = get_all_players()
    season_stats = get_season_stats(str(int(SEASON) - 1))
    candidates = []
    for sid, p in all_players.items():
        raw_pos = p.get("position")
        category = IDP_POSITION_MAP.get(raw_pos)
        if not category:
            continue
        if not p.get("team") or p.get("status") not in ("Active", "Injured Reserve"):
            continue
        stat_line = season_stats.get(sid)
        if not stat_line or not stat_line.get("games"):
            continue
        candidates.append((sid, stat_line["fpts"], category))
    if len(candidates) < 20:
        return []

    candidates.sort(key=lambda x: -x[1])  # highest real production first
    if get_total_vote_count() < 300:
        candidates = candidates[:40]  # still gathering data -- stick to recognizable names

    candidates.sort(key=lambda x: x[1])  # back to ascending for tier-window sampling
    idx = random.randint(0, len(candidates) - 10)
    window = candidates[idx: idx + 10]
    random.shuffle(window)

    trio = []
    for sid, fpts, category in window[:3]:
        p = all_players[sid]
        trio.append({
            "sid": sid, "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position": category, "team": p.get("team") or "FA",
            "age": p.get("age"), "photo": player_photo_url(sid), "value": round(fpts, 1),
        })
    return trio


def pick_similar_trio(num_qbs=1, is_dynasty=True):
    """3 players clustered at a similar tier, for the KTC-style Start/
    Bench/Cut widget. Mostly offense (real dynasty value), with IDP
    players mixed in sometimes (grouped by real season production)."""
    if random.random() < 0.75:
        trio = pick_offense_trio(num_qbs, is_dynasty)
        if trio:
            return trio
    trio = pick_idp_trio()
    if trio:
        return trio
    return pick_offense_trio(num_qbs, is_dynasty)


def player_photo_url(sid):
    return f"https://sleepercdn.com/content/nfl/players/{sid}.jpg"

# ---------------- Player news (ESPN NFL RSS -- free, public, syndication-friendly) ----------------
# We only ever show a headline + short snippet + a link back to the original
# ESPN article, with "via ESPN" credited right on each item. No full article
# text is ever copied or stored.

ESPN_NFL_RSS = "https://www.espn.com/espn/rss/nfl/news"
ROTOWIRE_NFL_RSS = "https://www.rotowire.com/rss/news.php?sport=NFL"
NEWS_SOURCE_URL = "https://www.espn.com/nfl/"  # kept for the page-level "powered by" footer link


def _strip_html(raw):
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _relative_time(pub_date_str):
    if not pub_date_str:
        return ""
    try:
        dt = parsedate_to_datetime(pub_date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        secs = delta.total_seconds()
        if secs < 3600:
            return f"{max(1, int(secs // 60))}m ago"
        if secs < 86400:
            return f"{int(secs // 3600)}h ago"
        return f"{int(secs // 86400)}d ago"
    except Exception:
        return ""


def _fetch_rss_feed(url, source_name, cache):
    """Shared fetch logic for any standard RSS feed -- used for both ESPN
    and RotoWire. Cached 30min per source so neither gets hit more than
    twice an hour. Fails silently and keeps showing the last good copy
    (empty on a cold start) if a feed hiccups or changes format, so one
    source going down never breaks the page."""
    now = time.time()
    if "items" in cache and now - cache.get("time", 0) < 1800:
        return cache["items"]
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            desc = _strip_html(item.findtext("description") or "")
            if title and link:
                items.append({"title": title, "link": link, "pub_date": pub_date, "desc": desc, "source": source_name})
        cache["items"] = items
        cache["time"] = now
        return items
    except Exception:
        return cache.get("items", [])


def get_espn_nfl_news(cache={}):
    return _fetch_rss_feed(ESPN_NFL_RSS, "ESPN", cache)


def get_rotowire_nfl_news(cache={}):
    return _fetch_rss_feed(ROTOWIRE_NFL_RSS, "RotoWire", cache)


def get_player_news(full_name, team, limit=3):
    if not full_name:
        return []
    all_items = get_espn_nfl_news() + get_rotowire_nfl_news()
    full_low = full_name.lower()
    last_low = full_name.split()[-1].lower() if full_name.split() else ""
    team_low = (team or "").lower()
    matches = []
    for it in all_items:
        hay = f"{it['title']} {it['desc']}".lower()
        hit = full_low in hay
        if not hit and len(last_low) >= 4:
            # Fall back to last-name-only matching once it's at least 4
            # letters, and only alongside the player's team, to avoid
            # pulling in unrelated players who happen to share a surname.
            # This used to require 6+ letters, which silently excluded a
            # huge share of real NFL surnames (Price, Cook, Hill, Chase,
            # Kelce, Adams, Allen, Diggs, Evans, Jones, Smith, Davis...)
            # from ever matching a headline that used last-name-only, which
            # is how most beat-writer blurbs are actually written.
            hit = last_low in hay and team_low and team_low in hay
        if hit:
            matches.append({
                "title": it["title"], "link": it["link"],
                "desc": (it["desc"][:220] + "...") if len(it["desc"]) > 220 else it["desc"],
                "ago": _relative_time(it["pub_date"]),
                "source": it["source"],
            })
        if len(matches) >= limit:
            break
    return matches

# ---------------- Team depth chart (from Sleeper's own player data) ----------------

DEPTH_SLOT_PRIORITY = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "DL": 4, "LB": 5, "DB": 6}

# Sleeper labels defensive depth slots by field alignment (LDE, MLB, RCB,
# SS...) rather than by the DL/LB/DB groups IDP formats actually use --
# exactly the same mismatch that made LWR/RWR/SWR invisible as receivers.
# Mapping each alignment onto its group lets a defensive depth chart read
# the way an IDP roster does, with one ordered column per group.
DEF_SLOT_GROUPS = {
    "LDE": "DL", "RDE": "DL", "DE": "DL", "LDT": "DL", "RDT": "DL",
    "DT": "DL", "NT": "DL", "EDGE": "DL",
    "LOLB": "LB", "ROLB": "LB", "OLB": "LB", "LILB": "LB", "RILB": "LB",
    "ILB": "LB", "MLB": "LB", "WLB": "LB", "SLB": "LB", "LB": "LB",
    "LCB": "DB", "RCB": "DB", "CB": "DB", "NB": "DB", "NCB": "DB",
    "SS": "DB", "FS": "DB", "S": "DB", "DB": "DB",
}

# Sleeper labels receiver depth slots by side (Left/Right/Slot WR), not a
# flat WR1/WR2 pattern like QB/RB/TE use -- this was the actual bug
# causing every WR to be silently dropped, since nothing recognized
# LWR/RWR/SWR as a WR at all.
WR_VARIANTS = {"LWR", "RWR", "SWR"}


def _depth_slot_base(slot):
    base = re.sub(r"\d+$", "", slot)
    if base in WR_VARIANTS:
        return "WR"
    return DEF_SLOT_GROUPS.get(base, base)


def _depth_slot_sort_key(slot):
    base = _depth_slot_base(slot)
    digits = re.sub(r"\D", "", slot)
    return (DEPTH_SLOT_PRIORITY.get(base, 9), int(digits) if digits else 0)


# Sleeper's own injury_status field is refreshed by Sleeper throughout the
# week (their player dump is what feeds the "Q"/"O"/"IR" tags all over
# their own app), and we already pull it into `all_players` via
# get_all_players() -- which itself refetches at most once every 24h (see
# above). So the depth chart badges below update automatically as soon as
# Sleeper's data changes, with no separate scrape of NFL.com or anywhere
# else needed, and nothing manual for us to maintain.
# label, full status name, severity tier for color-coding (see .injury-*
# in BASE_STYLE) -- "OUT" spelled out rather than "O" since a single
# letter O is easy to misread as the digit 0 at small badge size; "Q" for
# Questionable is kept since that one-letter code is a near-universal
# fantasy-football convention on its own.
INJURY_BADGE = {
    "IR": ("cross", "Injured Reserve", "out"),
    "OUT": ("OUT", "Out", "out"),
    "DOUBTFUL": ("DOUB", "Doubtful", "doubtful"),
    "QUESTIONABLE": ("Q", "Questionable", "questionable"),
    "PUP": ("PUP", "Physically Unable to Perform", "admin"),
    "SUSPENDED": ("SUSP", "Suspended", "admin"),
    "NA": ("NA", "Not Active", "admin"),
    "COV": ("COV", "COVID-19 list", "admin"),
}
INJURY_TIER_COLOR = {
    "out": "var(--critical)", "doubtful": "#e27834",
    "questionable": "var(--warning)", "admin": "var(--ink-muted)",
}


def _injury_badge(pl):
    status = (pl.get("injury_status") or "").strip().upper()
    if not status:
        return None
    label, title, tier = INJURY_BADGE.get(status, (status[:4], status.title(), "admin"))
    body_part = (pl.get("injury_body_part") or "").strip()
    if body_part:
        title = f"{title} — {body_part}"
    return {
        "label": label, "title": title, "tier": tier, "is_ir": status == "IR",
        "color": INJURY_TIER_COLOR.get(tier, "var(--ink-muted)"),
    }


def get_team_depth_chart(team, all_players):
    """Groups every player on `team` by Sleeper's own depth_chart_position/
    depth_chart_order fields -- no extra API call needed, Sleeper's player
    dump already carries this. Covers the offensive skill positions and
    the three IDP groups (see SCORED_POSITIONS).

    Sleeper labels receiver depth slots by side (LWR/RWR/SWR) instead of a
    flat WR1/WR2/WR3 pattern like QB/RB/TE use. We merge all three into one
    WR column here (ordered by Sleeper's depth_chart_order) so the chart
    shows one real WR pecking order instead of three separate boxes, and
    number every player within their column (QB1, QB2, WR1, WR2, ...).

    Defensive alignments are merged the same way, via DEF_SLOT_GROUPS --
    LDE/RDE/DT all become DL, the linebacker alignments become LB, and
    the secondary becomes DB."""
    if not team:
        return []
    groups = {}
    for sid, pl in all_players.items():
        if pl.get("team") != team:
            continue
        slot = pl.get("depth_chart_position")
        if not slot:
            continue
        base = _depth_slot_base(slot)
        # SCORED_POSITIONS, not POSITIONS: depth charts run on Sleeper's
        # own roster data, which covers defenders perfectly well. It's the
        # dynasty-value pages that can't include them.
        if base not in SCORED_POSITIONS:
            continue
        order = pl.get("depth_chart_order")
        groups.setdefault(base, []).append({
            "sid": sid,
            "name": f"{pl.get('first_name','')} {pl.get('last_name','')}".strip(),
            "photo": player_photo_url(sid),
            "base": base,
            "order": order if order is not None else 999,
            "injury": _injury_badge(pl),
        })
    result = []
    for base in sorted(groups.keys(), key=lambda b: DEPTH_SLOT_PRIORITY.get(b, 9)):
        players = sorted(groups[base], key=lambda x: x["order"])
        for i, pl in enumerate(players, start=1):
            pl["rank_label"] = f"{base}{i}"
        result.append({"slot": base, "base": base, "players": players})
    return result

# ---------------- Sleeper helpers ----------------

def _cached_get(url, cache, ttl=300):
    """Small shared helper: cache any Sleeper GET for `ttl` seconds. 5 min
    is short enough that roster/lineup changes show up quickly, but long
    enough that clicking around the same league doesn't re-fetch the same
    data over and over."""
    now = time.time()
    entry = cache.get(url)
    if entry and now - entry["time"] < ttl:
        return entry["data"]
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    cache[url] = {"data": data, "time": now}
    return data


def get_user_id(username, cache={}):
    data = _cached_get(f"{SLEEPER_BASE}/user/{username}", cache)
    if not data:
        raise ValueError(f"No Sleeper user found named '{username}'")
    return data["user_id"], data.get("display_name", username)


def get_leagues(user_id, season, cache={}):
    return _cached_get(f"{SLEEPER_BASE}/user/{user_id}/leagues/nfl/{season}", cache)


def get_rosters(league_id, cache={}):
    return _cached_get(f"{SLEEPER_BASE}/league/{league_id}/rosters", cache)


def get_league_users(league_id, cache={}):
    return _cached_get(f"{SLEEPER_BASE}/league/{league_id}/users", cache)


# How stale the player dump may get before it is refetched. It used to
# be a flat 24 hours, which was right when the only thing riding on it
# was a depth-chart badge. The injury feed reads the same dump, and an
# injury report that is up to a day late is not an injury report.
#
# Three hours rather than minutes because this is a ~5MB document and
# nothing in it moves faster than that: Sleeper updates injury
# designations over the course of a week, not a drive.
PLAYERS_REFRESH_S = 3 * 3600
_players_refresh_lock = threading.Lock()
_players_refreshing = set()


def _refresh_all_players_background(cache):
    """Refetch the player dump off the request path.

    The previous version let the TTL expire and then made whoever
    happened to arrive next wait for a multi-megabyte download and parse.
    Serving the slightly stale copy and refreshing behind it is strictly
    better: nobody waits, and the data is never more than one refresh
    interval behind."""
    with _players_refresh_lock:
        if "players" in _players_refreshing:
            return
        _players_refreshing.add("players")

    def _run():
        try:
            with _BACKGROUND_SYNC_SLOTS:
                r = requests.get(f"{SLEEPER_BASE}/players/nfl", timeout=60)
                r.raise_for_status()
                fresh = r.json()
            previous = cache.get("players")
            cache["players"] = fresh
            cache["time"] = time.time()
            # Sleeper publishes only the CURRENT designation, so a change
            # exists only if something noticed it. This is that.
            if previous:
                record_injury_changes(fresh)
        except Exception:
            pass
        finally:
            with _players_refresh_lock:
                _players_refreshing.discard("players")

    threading.Thread(target=_run, daemon=True).start()


def get_all_players(cache={}):
    now = time.time()
    if "players" not in cache:
        # Nothing cached at all -- the very first call has to wait.
        r = requests.get(f"{SLEEPER_BASE}/players/nfl")
        r.raise_for_status()
        cache["players"] = r.json()
        cache["time"] = now
        return cache["players"]
    if now - cache.get("time", 0) > PLAYERS_REFRESH_S:
        _refresh_all_players_background(cache)
    return cache["players"]

# ---------------- FantasyCalc (real dynasty/redraft trade values, incl. picks) ----------------

def get_fantasycalc_values(num_qbs, is_dynasty=True, num_teams=12, cache={}):
    """Returns {"players": {sleeper_id: {...}}, "picks": {pick_id: {...}}},
    cached 1hr per (format, dynasty-vs-redraft, league size). num_teams
    lets the trade calculator price picks/players for the user's actual
    league instead of only a fixed 12-team consensus."""
    key = (num_qbs, is_dynasty, num_teams)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 3600:
        return entry["data"]

    r = requests.get(FANTASYCALC_BASE, params={
        "isDynasty": "true" if is_dynasty else "false",
        "numQbs": num_qbs, "numTeams": num_teams, "ppr": 1,
    })
    r.raise_for_status()
    players, picks = {}, {}
    for item in r.json():
        player = item.get("player", {})
        sid = player.get("sleeperId")
        pos = player.get("position")
        # Classify by position first, not by whether sleeperId happens to
        # be truthy: a real skill player always has one of POSITIONS, and
        # a draft pick never does, regardless of what (if anything) the
        # API puts in its sleeperId field. Classifying picks off "no
        # sleeperId" alone silently mis-filed them as players (under a
        # bogus id nothing else could ever look up) if the API ever sends
        # a non-empty placeholder id for a pick entry.
        if pos in POSITIONS and sid:
            players[str(sid)] = {
                "value": item.get("value", 0),
                "position_rank": item.get("positionRank"),
                "overall_rank": item.get("overallRank"),
                "position": pos,
                "redraft_value": item.get("redraftValue"),
                "trend_30day": item.get("trend30Day"),
            }
        elif pos not in POSITIONS:
            # Not a real offensive position -> a draft pick or similar
            # non-Sleeper asset FantasyCalc tracks. Picks are labeled like
            # "2026 Mid 1st" -- pull the year out so the trade calculator
            # can group/filter them without re-parsing names everywhere.
            pid = f"pick_{player.get('id')}"
            name = player.get("name")
            if name:
                year_match = re.search(r"(20\d{2})", name)
                picks[pid] = {
                    "sid": pid, "name": name, "value": item.get("value", 0), "overall_rank": item.get("overallRank"),
                    "year": int(year_match.group(1)) if year_match else None,
                }

    data = {"players": players, "picks": picks}
    cache[key] = {"data": data, "time": now}
    return data


PICK_TIER_ORDER = {"early": 0, "mid": 1, "late": 2}


def pick_sort_key(pick):
    """Chronological ordering for a pick label like "2026 Mid 1st" --
    (year, round, early/mid/late tier, name). Shared by the browsable
    picks panel and by search, so "typed pick" and "browsed pick" order
    the same way."""
    name = pick["name"]
    round_match = re.search(r"(\d+)(?:st|nd|rd|th)", name)
    tier_match = re.search(r"\b(early|mid|late)\b", name, re.I)
    rnd = int(round_match.group(1)) if round_match else 9
    tier = PICK_TIER_ORDER.get(tier_match.group(1).lower(), 1) if tier_match else 1
    return (pick["year"] if pick["year"] is not None else 9999, rnd, tier, name)


def sorted_upcoming_picks(picks, num_years=3):
    """Picks grouped/ordered for a browsable UI: nearest `num_years` draft
    classes present in the data (oldest first), each sorted by round then
    early/mid/late tier -- so "2026 1st" always comes before "2026 2nd",
    and "2026 Early 1st" before "2026 Late 1st". Falls back gracefully for
    any label FantasyCalc formats differently than expected."""
    years_present = sorted({p["year"] for p in picks.values() if p["year"] is not None})
    keep_years = set(years_present[:num_years])
    kept = [p for p in picks.values() if p["year"] in keep_years]
    return sorted(kept, key=pick_sort_key)


def parse_pick_slot_query(query):
    """Parse "1.02"-style round.slot shorthand (how dynasty players
    actually refer to a specific pick) into (round, slot) ints, or None
    if `query` isn't that shape at all."""
    m = re.match(r"^(\d)\.(\d{1,2})$", query)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def pick_tier_value_map(picks):
    """{(year, round): {"early"/"mid"/"late"/"flat": value}} built from
    FantasyCalc's own labels -- "flat" means that round wasn't
    tier-subdivided for that year (just a single published value)."""
    out = {}
    for pk in picks.values():
        name = pk["name"]
        round_match = re.search(r"(\d+)(?:st|nd|rd|th)", name)
        tier_match = re.search(r"\b(early|mid|late)\b", name, re.I)
        if pk["year"] is None or not round_match:
            continue
        key = (pk["year"], int(round_match.group(1)))
        tier = tier_match.group(1).lower() if tier_match else "flat"
        out.setdefault(key, {})[tier] = pk["value"]
    return out


def interpolate_pick_slot_value(slot, num_teams, tier_values):
    """Derive a value for one exact slot (1..num_teams) from FantasyCalc's
    published early/mid/late tier values for that round -- they don't
    publish a value per individual slot, only per tier. Fits a smooth
    decay curve (linear in log-value space, i.e. geometric decay -- the
    standard shape for draft-pick value curves) through whichever tier
    midpoints are known, then evaluates every slot in the round and
    forces the result non-increasing left-to-right. That clamp matters at
    the two edges: a curve fit through only 2-3 points can flatten out or
    even curve the wrong way right past its outermost anchor, which
    otherwise made pick 1.01 and 1.02 land on the exact same number --
    the earliest slot should never come out cheaper than a later one.
    With only one tier value available (a round FantasyCalc doesn't
    subdivide by tier), every slot in it gets that same value -- there's
    nothing more granular to derive it from."""
    real_tiers = {k: v for k, v in tier_values.items() if k != "flat" and v}
    if not real_tiers:
        return tier_values.get("flat")

    third = max(num_teams // 3, 1)
    bounds = {
        "early": (1, min(third, num_teams)),
        "mid": (third + 1, min(2 * third, num_teams)),
        "late": (2 * third + 1, num_teams),
    }
    anchors = sorted(
        ((bounds[tier][0] + bounds[tier][1]) / 2, value)
        for tier, value in real_tiers.items()
        if bounds[tier][0] <= bounds[tier][1]
    )
    if len(anchors) <= 1:
        return anchors[0][1] if anchors else next(iter(real_tiers.values()))

    xs = [a[0] for a in anchors]
    ys = [math.log(max(a[1], 1)) for a in anchors]

    if len(anchors) == 2:
        x1, y1, x2, y2 = xs[0], ys[0], xs[1], ys[1]
        slope = (y2 - y1) / (x2 - x1) if x2 != x1 else 0
        curve = lambda x: y1 + slope * (x - x1)  # noqa: E731
    else:
        x0, x1, x2 = xs
        y0, y1, y2 = ys

        def curve(x):
            l0 = (x - x1) * (x - x2) / ((x0 - x1) * (x0 - x2))
            l1 = (x - x0) * (x - x2) / ((x1 - x0) * (x1 - x2))
            l2 = (x - x0) * (x - x1) / ((x2 - x0) * (x2 - x1))
            return y0 * l0 + y1 * l1 + y2 * l2

    raw_values = [math.exp(curve(s)) for s in range(1, num_teams + 1)]
    clamped, running_min = [], float("inf")
    for v in raw_values:
        running_min = min(running_min, v)
        clamped.append(running_min)
    return round(clamped[slot - 1])


SLOT_PICK_SID_RE = re.compile(r"^pick_slot_(\d{4})_(\d+)_(\d+)_(\d+)$")


def resolve_slot_pick(sid, picks, num_teams):
    """Rebuild a synthetic exact-slot pick from its sid (e.g.
    "pick_slot_2026_1_2_12"). Shared by search (building the result fresh)
    and the trade calculator route (re-resolving a slot pick a side
    already has, on every page render) so the two never disagree. Value
    is recomputed against the CURRENT `num_teams` selection rather than
    whatever's embedded in the sid, so a pick added under one league size
    stays consistent if the user switches league size afterward. Returns
    None if the sid isn't a slot-pick id, the slot's out of range for
    `num_teams`, or that round isn't priced yet."""
    m = SLOT_PICK_SID_RE.match(sid)
    if not m:
        return None
    year, rnd, slot = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if slot < 1 or slot > num_teams:
        return None
    tier_values = pick_tier_value_map(picks).get((year, rnd))
    if not tier_values:
        return None
    value = interpolate_pick_slot_value(slot, num_teams, tier_values)
    if value is None:
        return None
    ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(rnd, f"{rnd}th")
    return {"name": f"{year} {rnd}.{slot:02d} ({ordinal}, {num_teams}-team, est.)", "value": round(value)}


def exact_slot_picks(picks, num_teams, rnd, slot, num_years=3):
    """Synthetic individual-pick search results for one exact slot (e.g.
    "1.02") across the nearest `num_years` draft classes that actually
    have that round priced. Values are interpolated (see
    interpolate_pick_slot_value) since FantasyCalc only publishes
    early/mid/late tiers, not individual slots -- labeled "(est.)" so
    it's clear these are derived, not a directly-sourced number. Returns
    [] if `slot` isn't valid for `num_teams`, or nothing is priced for
    that round yet."""
    if slot < 1 or slot > num_teams:
        return []
    tier_map = pick_tier_value_map(picks)
    years = sorted(year for (year, r) in tier_map.keys() if r == rnd)[:num_years]
    results = []
    for year in years:
        sid = f"pick_slot_{year}_{rnd}_{slot}_{num_teams}"
        resolved = resolve_slot_pick(sid, picks, num_teams)
        if resolved:
            results.append({"sid": sid, "year": year, **resolved})
    return results


def normalize_name(name):
    """Strip punctuation and suffixes so 'D.J. Moore' and 'DJ Moore', or
    'Kenneth Walker III' and 'Kenneth Walker', still match each other."""
    name = name.lower()
    name = re.sub(r"[.'\u2019-]", "", name)
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", name)
    return re.sub(r"\s+", " ", name).strip()


def get_adp_data(cache={}):
    """Returns {"by_key": {(name_lower, POS): adp}, "by_name_only": {...},
    "by_normalized": {...}}. Matching on (name, position) first avoids
    mixing up real players who share a name (there's more than one NFL
    "Josh Allen"); the normalized tier is a fallback for formatting
    differences between Sleeper and Fantasy Football Calculator's data."""
    now = time.time()
    if "data" in cache and now - cache.get("time", 0) < 3600:
        return cache["data"]
    try:
        r = requests.get(ADP_BASE, params={"teams": 12, "year": SEASON}, timeout=15)
        r.raise_for_status()
        players = r.json().get("players", [])
        by_key, by_name_only, by_normalized = {}, {}, {}
        for p in players:
            nm = p.get("name", "").strip().lower()
            pos = (p.get("position") or "").strip().upper()
            adp = p.get("adp")
            if not nm or adp is None:
                continue
            by_key[(nm, pos)] = adp
            by_name_only.setdefault(nm, adp)
            by_normalized.setdefault(normalize_name(nm), adp)
        data = {"by_key": by_key, "by_name_only": by_name_only, "by_normalized": by_normalized}
        cache["data"] = data
        cache["time"] = now
        return data
    except Exception:
        return {"by_key": {}, "by_name_only": {}, "by_normalized": {}}


# ---------------- ESPN live scores / schedule / officials ----------------
# Uses ESPN's public "site" API (no key, same domain family as the player
# news RSS feed above) -- unofficial and undocumented, so it could change
# without notice, but it's the only free source that covers officiating
# crews and full box scores. TTLs below are deliberately state-dependent
# (short while a game is actually live, long once nothing can change) so
# the scores/game pages feel fast during games without hammering ESPN or
# re-fetching finished games that will never change again.

ESPN_SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"

# Known ESPN-vs-Sleeper team abbreviation mismatches. Verify/extend this
# once /api/debug-espn is deployed and real payloads can be inspected --
# Washington is the one mismatch known in advance; there may be others.
TEAM_ABBR_ESPN_TO_SLEEPER = {
    "WSH": "WAS",
}

_ESPN_STATE_TO_STATUS = {"pre": "scheduled", "in": "in_progress", "post": "final"}


def normalize_team_abbr(espn_abbr):
    """Maps an ESPN team abbreviation to the Sleeper convention used
    everywhere else in this app (get_all_players()[sid]['team'])."""
    if not espn_abbr:
        return espn_abbr
    abbr = espn_abbr.upper()
    return TEAM_ABBR_ESPN_TO_SLEEPER.get(abbr, abbr)


def team_logo_url(team_abbr):
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{team_abbr.lower()}.png" if team_abbr else None


def _safe_int(value, fallback):
    """Coerces an ESPN field to a plain int, defensively -- some fields
    that are documented elsewhere as a bare number (like season.type)
    have turned out in practice to sometimes come back as a nested
    object instead (e.g. {"id": "2", "type": 2, "name": "Regular
    Season"}). Using a value like that as part of a cache dict's tuple
    key crashes with "unhashable type: dict", which is exactly the bug
    this guards against -- pull a plausible int out of a dict shape, or
    fall back cleanly rather than ever propagating a dict downstream."""
    if isinstance(value, dict):
        value = value.get("type") if isinstance(value.get("type"), (int, str)) else value.get("id")
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def get_current_week_info(cache={}):
    """Hits the bare scoreboard endpoint (no date/week params) -- ESPN
    resolves that to "the current week" on its own, avoiding us having to
    hand-roll season-opener/bye-week math. TTL 300s (it rarely matters if
    this is a few minutes stale)."""
    now = time.time()
    if "data" in cache and now - cache.get("time", 0) < 300:
        return cache["data"]
    fallback = {"season": int(SEASON), "week": 1, "season_type": 2}
    try:
        r = requests.get(f"{ESPN_SITE_BASE}/scoreboard", timeout=15)
        r.raise_for_status()
        body = r.json()
        wk = body.get("week", {}) or {}
        leagues = body.get("leagues") or [{}]
        season = leagues[0].get("season", {}) or {}
        data = {
            "season": _safe_int(season.get("year"), fallback["season"]),
            "week": _safe_int(wk.get("number"), fallback["week"]),
            "season_type": _safe_int(season.get("type"), fallback["season_type"]),
        }
        cache["data"] = data
        cache["time"] = now
        return data
    except Exception:
        return cache.get("data", fallback)


def espn_week_scoreboard(season, week, season_type=2, cache={}):
    """One call returns every game (Thu-Mon) in a given week. TTL is 20s
    whenever any game in the response is actually in progress -- so the
    calendar/scoreboard feels "immediate live" -- and 3600s otherwise,
    since a week with nothing live can't change (final scores are done,
    future kickoff times essentially never move)."""
    # Defense in depth against a caller (or a future get_current_week_info
    # response shape surprise) passing something that isn't a plain int --
    # a dict anywhere in this tuple crashes with "unhashable type: dict"
    # the moment it's used as a cache key, so coerce before that can happen.
    season, week, season_type = _safe_int(season, int(SEASON)), _safe_int(week, 1), _safe_int(season_type, 2)
    key = (season, week, season_type)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < (20 if entry.get("any_live") else 3600):
        return entry["data"]
    try:
        # `dates` is what actually selects the season here -- NOT `year`.
        # ESPN's scoreboard endpoint silently ignores an unrecognized
        # `year` param and just answers for the CURRENT season, which is
        # the single bug behind every "last season has no data" symptom
        # this app has had: a sync for 2025 fetched 2026's games, whose
        # event IDs then collided with the real 2026 rows already in
        # nfl_schedule, so the upsert only refreshed those and nothing
        # ever landed under season=2025. The sync loop reported a
        # perfectly healthy "18 weeks, N rows each" the whole time.
        r = requests.get(
            f"{ESPN_SITE_BASE}/scoreboard",
            params={"week": week, "seasontype": season_type, "dates": season},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        any_live = any(
            ((ev.get("status") or {}).get("type") or {}).get("state") == "in"
            for ev in data.get("events", [])
        )
        cache[key] = {"data": data, "time": now, "any_live": any_live}
        return data
    except Exception:
        return entry["data"] if entry else {"events": []}


def espn_day_scoreboard(date_str, cache={}):
    """Games for one specific calendar day (YYYYMMDD), used by the
    calendar/month view to fetch a single clicked-on day on demand
    instead of eagerly pulling every week that could ever be shown.
    Same live-aware TTL as espn_week_scoreboard."""
    now = time.time()
    entry = cache.get(date_str)
    if entry and now - entry["time"] < (20 if entry.get("any_live") else 3600):
        return entry["data"]
    try:
        r = requests.get(f"{ESPN_SITE_BASE}/scoreboard", params={"dates": date_str}, timeout=15)
        r.raise_for_status()
        data = r.json()
        any_live = any(
            ((ev.get("status") or {}).get("type") or {}).get("state") == "in"
            for ev in data.get("events", [])
        )
        cache[date_str] = {"data": data, "time": now, "any_live": any_live}
        return data
    except Exception:
        return entry["data"] if entry else {"events": []}


def _espn_broadcast(comp):
    """Which network is carrying this game ("FOX", "CBS", "NBC/Peacock").

    ESPN puts this in at least three different places depending on the
    endpoint and how far out the game is, and none of them is documented
    -- so try each and take the first that yields a name rather than
    assuming one shape. Returns None when nothing is listed yet, which is
    normal for a game more than a week or two out.
    """
    for b in (comp.get("broadcasts") or []):
        names = b.get("names") or ([b.get("shortName")] if b.get("shortName") else [])
        names = [n for n in names if n]
        if names:
            return "/".join(names)
    for b in (comp.get("geoBroadcasts") or []):
        media = (b.get("media") or {}).get("shortName")
        if media:
            return media
    return None


def _espn_spread(comp):
    """The point spread as a plain phrase ("LAC by 9.5"), or None.

    ESPN expresses this a few different ways -- sometimes a `details`
    string already in "LAC -9.5" form, sometimes only a favorite team id
    plus a numeric `spread`. Both are handled, and anything unrecognized
    returns None rather than guessing, because a spread shown backwards
    is far worse than no spread at all.

    A spread of exactly 0 is a pick'em, not a missing value, so it's
    reported as such instead of being swallowed by a falsy check."""
    odds_list = comp.get("odds") or []
    if not odds_list:
        return None
    odds = odds_list[0] or {}

    details = (odds.get("details") or "").strip()
    if details:
        # "LAC -9.5" -> "LAC by 9.5". "EVEN"/"PK" is a pick'em.
        if details.upper() in ("EVEN", "PK", "PICK", "PICK'EM"):
            return "Pick'em"
        parts = details.split()
        if len(parts) == 2 and parts[1].lstrip("+-").replace(".", "", 1).isdigit():
            team, number = parts[0], float(parts[1])
            if number == 0:
                return "Pick'em"
            # A positive number here would mean the named team is the
            # UNDERDOG, which ESPN doesn't normally emit -- name the
            # favourite either way rather than printing "X by -3".
            if number < 0:
                return f"{team} by {abs(number):g}"
            return details
        return details

    spread = odds.get("spread")
    if isinstance(spread, (int, float)):
        if spread == 0:
            return "Pick'em"
        fav = ((odds.get("homeTeamOdds") or {}).get("favorite") and "home") or \
              ((odds.get("awayTeamOdds") or {}).get("favorite") and "away")
        if fav:
            competitors = comp.get("competitors") or []
            side = next((c for c in competitors if c.get("homeAway") == fav), None)
            abbr = normalize_team_abbr(((side or {}).get("team") or {}).get("abbreviation"))
            if abbr:
                return f"{abbr} by {abs(spread):g}"
    return None


def espn_event_to_card(ev):
    """Pure function: one ESPN scoreboard event -> the display-ready
    shape the /scores and /api/scoreboard cards need (richer than
    _parse_espn_event's flat DB row -- team names/logos, live clock)."""
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    status = comp.get("status") or {}
    state = (status.get("type") or {}).get("state")

    def side(c):
        team = c.get("team") or {}
        abbr = normalize_team_abbr(team.get("abbreviation"))
        return {
            "abbr": abbr,
            "name": team.get("shortDisplayName") or team.get("displayName") or abbr,
            "score": c.get("score"),
            "logo": team_logo_url(abbr),
        }

    date_raw = ev.get("date") or ""
    return {
        "id": ev.get("id"),
        "date": date_raw,
        "broadcast": _espn_broadcast(comp),
        "spread": _espn_spread(comp),
        # NOTE: no server-computed date_key here on purpose. ESPN's `date`
        # is UTC (a "Z"-suffixed ISO string) -- a Sunday 8:20pm ET kickoff
        # is already after midnight UTC, so naively slicing the first 10
        # characters puts it on Monday for anyone west of the UK. The
        # client computes the visitor's actual local calendar day from
        # this ISO string instead (see indexGames() in SCORES_HTML), which
        # is the only place that can know the visitor's real timezone.
        "status": _ESPN_STATE_TO_STATUS.get(state, "scheduled"),
        "period": status.get("period"),
        "clock": status.get("displayClock"),
        "status_detail": (status.get("type") or {}).get("shortDetail"),
        "home": side(home),
        "away": side(away),
    }


def espn_game_summary(event_id, cache={}):
    """Full game detail: venue, officials, box score, leaders. TTL 20s
    while live, 300s pregame (inactives/injury designations can still
    change), 86400s once final (a finished game never changes again)."""
    now = time.time()
    entry = cache.get(event_id)
    if entry:
        state = entry.get("state")
        ttl = 20 if state == "in" else (86400 if state == "post" else 300)
        if now - entry["time"] < ttl:
            return entry["data"]
    try:
        r = requests.get(f"{ESPN_SITE_BASE}/summary", params={"event": event_id}, timeout=15)
        r.raise_for_status()
        data = r.json()
        state = (((data.get("header") or {}).get("competitions") or [{}])[0].get("status") or {}).get("type", {}).get("state")
        cache[event_id] = {"data": data, "time": now, "state": state}
        return data
    except Exception:
        return entry["data"] if entry else {}


# ESPN writes players in play text as "8-K.Cousins" or "K.Cousins" --
# jersey number optional, always first initial then last name.
_PLAY_NAME_RE = re.compile(r"(?:\d{1,2}-)?([A-Z][A-Za-z]?\.[A-Z][A-Za-z'\-]+)")

# The player a play should lead with. ESPN writes a pass as "K.Cousins
# pass short right to J.Jefferson", so reading the text in order puts the
# quarterback's face on every completion -- but the play belongs to the
# man who caught it. Same for a turnover: the play is the defender's.
_PLAY_TARGET_RE = re.compile(r"\bto\s+(?:\d{1,2}-)?([A-Z][A-Za-z]?\.[A-Z][A-Za-z'\-]+)")
_PLAY_INTERCEPT_RE = re.compile(r"intercepted by\s+(?:\d{1,2}-)?([A-Z][A-Za-z]?\.[A-Z][A-Za-z'\-]+)",
                                re.IGNORECASE)
_PLAY_RECOVER_RE = re.compile(r"recovered by\s+(?:[A-Z]{2,3}-)?(?:\d{1,2}-)?([A-Z][A-Za-z]?\.[A-Z][A-Za-z'\-]+)",
                              re.IGNORECASE)
# "sacked by A.Donald" and "sacked at MIN 20 for -7 yards (A.Donald)"
# are both written by ESPN, so both spellings are read here.
_PLAY_SACK_RE = re.compile(r"sacked\b[^()]*(?:by\s+|\()(?:\d{1,2}-)?([A-Z][A-Za-z]?\.[A-Z][A-Za-z'\-]+)",
                           re.IGNORECASE)


def _play_lead_name(text):
    """Which name in a play's text the play actually belongs to, or None
    to keep ESPN's own order.

    Only reordered where the writing order and the ownership of the play
    genuinely disagree -- a completed pass, an interception, a fumble
    recovery, a sack. A run, a kick, an incompletion all already lead
    with the right player, and guessing at those would only introduce
    mistakes."""
    t = text or ""
    low = t.lower()
    for pattern in (_PLAY_INTERCEPT_RE, _PLAY_RECOVER_RE, _PLAY_SACK_RE):
        m = pattern.search(t)
        if m:
            return m.group(1)
    # A completion: "... pass short right to J.Jefferson for 12 yards".
    # Not an incompletion -- nobody caught it, so it stays the passer's.
    if " pass" in low and "incomplete" not in low and "intended for" not in low:
        m = _PLAY_TARGET_RE.search(t)
        if m:
            return m.group(1)
    return None


# ESPN writes the depth and direction of every pass -- "pass short
# right", "pass deep left" -- which is the only description of HOW a
# catch was made that its feed actually carries. "Short" is the default
# and adding it to a headline is noise, so only the direction survives
# unless the ball went deep.
_PASS_SHAPE_RE = re.compile(r"pass\s+(short|deep)\s+(left|middle|right)", re.IGNORECASE)
_RUSH_SHAPE_RE = re.compile(r"\b(left|right)\s+(end|tackle|guard)\b|\b(up the middle)\b",
                            re.IGNORECASE)
# "PENALTY on NYG-E.Campbell, Offensive Holding, 10 yards, enforced at..."
_PENALTY_RE = re.compile(
    r"PENALTY on (?:[A-Z]{2,3}-)?([A-Z][A-Za-z.'\-]+),\s*([^,]+?),\s*(\d+)\s*yards",
    re.IGNORECASE)


# ESPN appends the kick or conversion to the SAME sentence as the score:
# "...to I.Likely for 15 yards, TOUCHDOWN. D.Zvada extra point is GOOD".
# Classifying the play means reading only the part before that, or a
# touchdown run whose conversion was a pass gets titled a catch.
_SCORE_TAIL_RE = re.compile(r"\b(touchdown|two-point conversion|extra point)\b",
                            re.IGNORECASE)


def _primary_clause(text):
    """The play itself, without the point-after ESPN writes into the same
    sentence."""
    t = text or ""
    m = _SCORE_TAIL_RE.search(t)
    return t[:m.start()] if m else t


def _pass_shape(text):
    """("deep", "left") for a pass whose depth and direction ESPN wrote,
    otherwise (None, None)."""
    m = _PASS_SHAPE_RE.search(text or "")
    return (m.group(1).lower(), m.group(2).lower()) if m else (None, None)


def _yard_prefix(y):
    """"32-yd " for a gain, nothing otherwise.

    Nothing for a loss in particular: a play that went backwards is
    described as a loss below rather than as a gain of minus three."""
    if not isinstance(y, (int, float)) or y <= 0:
        return ""
    return f"{int(y)}-yd "


def _loss_headline(y):
    """"7-yd loss", when the play went the wrong way."""
    return f"{abs(int(y))}-yd loss"


def _went_backwards(y, td):
    """A play that lost yardage and did not score.

    The `td` guard matters: a touchdown is never negative yardage, so a
    feed reporting one is wrong about the number, not about the score.
    Those print without a distance rather than as "-10-yd TD pass"."""
    return isinstance(y, (int, float)) and y < 0 and not td


def _play_headline(text, yards, scoring):
    """A short title for a play -- "15-yd short-right TD catch", "3-yd
    rush", "Sack" -- the way the reference feed leads each row, instead
    of repeating the full sentence twice.

    Derived from the text rather than from a play-type field because
    ESPN's own type labels are coarse (everything is "pass" or "rush")
    and miss the cases that matter most to read at a glance: a sack, a
    turnover, a kneel."""
    t = (text or "")
    low = t.lower()
    # Only the play itself decides what the play was.
    main = _primary_clause(t)
    main_low = main.lower()
    y = yards if isinstance(yards, (int, float)) else None
    yd = _yard_prefix(y)
    # A play that reached the end zone is a touchdown play. Settled from
    # the WORD, never from ESPN's scoringPlay flag: a field goal is a
    # scoring play too, and trusting the flag is how a kicker's three
    # field goals came to be titled as touchdown runs. It is also settled
    # BEFORE any kicking check, because ESPN writes the extra point into
    # the same sentence as the score -- which is how a 15-yard touchdown
    # catch once ended up titled "Extra point".
    td = "touchdown" in low

    if "intercepted" in main_low:
        return "Pick-six" if td else "Interception"
    if "fumbles" in main_low and "recovered by" in main_low:
        return "Fumble returned for TD" if td else "Fumble"
    if "sacked" in main_low:
        return "Sack"

    if not td:
        if "kneel" in low:
            return "Kneel"
        if "spiked the ball" in low:
            return "Spike"
        if "punts" in low:
            return "Punt"
        if "field goal" in low:
            m = re.search(r"(\d{1,3})\s+yard field goal", t, re.I)
            made = "field goal is good" in low
            return ((f"{m.group(1)}-yd field goal" if m else "Field goal")
                    + ("" if made else " missed"))
        if "two-point conversion" in low:
            return "Two-point conversion"
        if "extra point" in low:
            return "Extra point"
        if "kicks" in low and "yards from" in low:
            return "Kickoff"
        if "two-minute warning" in low:
            return "Two-minute warning"
        if "end quarter" in low or "end game" in low or "end of" in low:
            return t.strip().title()[:40]
        if "no play" in low and "penalty" in low:
            return "Penalty"
        if "incomplete" in low:
            return "Incomplete"

    if " pass " in main_low or "pass short" in main_low or "pass deep" in main_low:
        depth, direction = _pass_shape(main)
        # "deep-left", or just "right" on a short throw -- short is the
        # default and saying so adds length without adding meaning.
        shape = ""
        if direction:
            shape = f"{depth}-{direction} " if depth == "deep" else f"{direction} "
        if td:
            return f"{yd}{shape}TD catch"
        if _went_backwards(y, td):
            return _loss_headline(y)
        return f"{yd}{shape}catch" if (yd or shape) else "Catch"
    if "scrambles" in main_low:
        if td:
            return f"{yd}TD scramble"
        if _went_backwards(y, td):
            return _loss_headline(y)
        return f"{yd}scramble" if yd else "Scramble"
    # Anything left with a ball-carrier reads as a run.
    if td:
        return f"{yd}TD run" if yd else "TD run"
    if _went_backwards(y, td):
        return _loss_headline(y)
    return f"{yd}rush" if yd else "Rush"


def _play_notes(text, yards, down, distance, scoring):
    """The lines under a play: what else happened, one fact per line,
    each with a coloured dot saying whether it went well.

    This replaced dumping ESPN's raw sentence under every row. That
    sentence carries real information -- whether the extra point was
    good, who was flagged, who made the tackle -- but buried in
    officialese ("Center-B.Mann, Holder-J.Stout") that nobody reads. So
    the facts worth having are pulled out and the sentence is dropped.

    `tone` is good / bad / warn / info, and only ever reflects something
    the text actually says."""
    t = (text or "")
    low = t.lower()
    out = []
    # The word, not the scoringPlay flag -- a field goal is a scoring
    # play and is not a touchdown.
    td = "touchdown" in low

    # The kick after a score, which is the whole reason the sentence was
    # worth parsing: a green dot for a good one, red for a miss.
    if "extra point" in low:
        good = "extra point is good" in low
        out.append({"tone": "good" if good else "bad",
                    "label": "Extra point is good" if good else "Extra point is no good"})
    if "two-point conversion" in low:
        good = "conversion succeeds" in low or "attempt succeeds" in low
        out.append({"tone": "good" if good else "bad",
                    "label": "Two-point conversion" + (" is good" if good else " failed")})
    if "field goal" in low and "extra point" not in low:
        good = "is good" in low
        out.append({"tone": "good" if good else "bad",
                    "label": "Field goal is good" if good else "Field goal is no good"})

    # A touchdown is already the headline; repeating it as a note is the
    # kind of duplication this rewrite is removing.
    if not td and isinstance(yards, (int, float)) and isinstance(distance, (int, float)) \
            and distance > 0 and yards >= distance and "no play" not in low:
        out.append({"tone": "good", "label": "First down"})

    if "intercepted" in low:
        out.append({"tone": "bad", "label": "Intercepted"})
    if "fumbles" in low:
        out.append({"tone": "bad",
                    "label": "Fumble lost" if "recovered by" in low else "Fumble"})
    if "sacked" in low:
        out.append({"tone": "bad", "label": "Sacked"})

    # The flag, written the way a broadcast says it rather than the way
    # the league records it.
    m = _PENALTY_RE.search(t)
    if m:
        who, foul, pen_yards = m.group(1), m.group(2).strip(), m.group(3)
        declined = "declined" in low
        out.append({"tone": "warn",
                    "label": f"{who} \u00b7 {pen_yards}-yd {foul.lower()}"
                             + (", declined" if declined else "")})
    elif "penalty" in low:
        out.append({"tone": "warn", "label": "Penalty on the play"})

    if "no play" in low:
        out.append({"tone": "warn", "label": "No play"})
    return out


def summary_season_week(summary_json, fallback=None):
    """Which season and week a game summary belongs to.

    Read off the game itself rather than assumed to be the current week,
    so opening a game from an earlier week links its players to the
    performance they actually had in that game."""
    fallback = fallback or {}
    header = (summary_json or {}).get("header") or {}
    season_block = header.get("season") if isinstance(header.get("season"), dict) else {}
    season = _safe_int(season_block.get("year"), None)
    week = _safe_int(header.get("week"), None)
    season_type = _safe_int(season_block.get("type"), None)
    return {
        "season": season or _safe_int(fallback.get("season"), int(SEASON)),
        "week": week or _safe_int(fallback.get("week"), 1),
        "season_type": season_type or _safe_int(fallback.get("season_type"), 2),
    }


def _player_name_index(all_players):
    """{(team, "K.Cousins"): sleeper_id} so a name in ESPN's play text can
    be resolved to a real player -- for their photo and live points.

    Keyed by team as well as name because initial+surname collides often
    enough across a whole league to matter, and a play always knows which
    offense it belongs to."""
    index = {}
    for sid, p in (all_players or {}).items():
        first, last, team = p.get("first_name"), p.get("last_name"), p.get("team")
        if not first or not last or not team:
            continue
        key = f"{first[0]}.{last}"
        index.setdefault((team, key), sid)
        # Also unkeyed by team, as a fallback for defenders credited on a
        # tackle, who belong to the other side of the play.
        index.setdefault((None, key), sid)
    return index


def enrich_plays(plays, season, week):
    """Adds the headline, notes, and involved players (with photo and
    live fantasy points) to each play, so the feed can render the way the
    reference does rather than as a wall of ESPN's sentences.

    Player points come from the same live feed the performer board uses,
    so a play row and the leaderboard never disagree about what someone
    has scored."""
    if not plays:
        return []
    all_players = get_all_players()
    index = _player_name_index(all_players)
    live = get_live_week_stats(season, week, allow_fetch=False) or {}

    for play in plays:
        team = play.get("team")
        play["headline"] = _play_headline(play.get("text"), play.get("yards"), play.get("scoring"))
        play["notes"] = _play_notes(play.get("text"), play.get("yards"),
                                    play.get("down"), play.get("distance"), play.get("scoring"))
        # Names in the order ESPN wrote them, except that the player the
        # play belongs to is pulled to the front -- the receiver on a
        # catch, the defender on a turnover -- so the face beside a play
        # is the one who made it. Tacklers appear in parentheses at the
        # end and fall out naturally below the cap.
        text = play.get("text") or ""
        seen, names = set(), []
        for name in _PLAY_NAME_RE.findall(text):
            if name not in seen:
                seen.add(name)
                names.append(name)
        lead_name = _play_lead_name(text)
        if lead_name and lead_name in seen:
            names.remove(lead_name)
            names.insert(0, lead_name)

        people = []
        for name in names:
            sid = index.get((team, name)) or index.get((None, name))
            if not sid:
                continue
            p = all_players.get(sid) or {}
            people.append({
                "sid": sid,
                "name": name,
                "full_name": (f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip()
                              or name),
                "position": p.get("position"),
                "photo": player_photo_url(sid),
                "fpts": (live.get(sid) or {}).get("pts"),
            })
            if len(people) >= 3:
                break
        play["people"] = people
    return plays


# ESPN states the yardage in the sentence -- "for 15 yards", "for 1
# yard", "for no gain" -- and that sentence is the authority. The
# structured statYardage field beside it does NOT always agree: a
# quarterback's page showed "-10-yd TD pass" and "-9-yd TD pass" off
# that field, for goal-line throws that plainly gained one or two. A
# touchdown is never negative yardage, so when the two disagree the
# words win.
_TEXT_YARDS_RE = re.compile(r"\bfor\s+(-?\d{1,3})\s+yards?\b", re.I)
_NO_GAIN_RE = re.compile(r"\bfor\s+no\s+gain\b", re.I)


def play_yards(text, stat_yardage=None):
    """How far the play went, read from the sentence, falling back to
    ESPN's own field only when the sentence does not say."""
    main = _primary_clause(text or "")
    m = _TEXT_YARDS_RE.search(main)
    if m:
        return int(m.group(1))
    if _NO_GAIN_RE.search(main):
        return 0
    return _safe_int(stat_yardage, None) if stat_yardage is not None else None


def extract_drive_plays(summary_json, limit=60):
    """The live play-by-play feed, newest first.

    ESPN's summary carries drives in two places -- `drives.current` for
    the drive in progress and `drives.previous` for everything finished
    -- and neither is guaranteed present, so both are read defensively
    and anything missing simply contributes nothing.

    Every field here comes from the live feed. The site used to layer
    nflverse's expected-points figures on afterwards, which meant the
    most interesting number about a play only existed the morning after
    it; ESPN's own win-probability series is read alongside these
    instead, and it publishes as the game happens."""
    drives = (summary_json or {}).get("drives") or {}
    buckets = []
    if isinstance(drives.get("previous"), list):
        buckets.extend(drives["previous"])
    if isinstance(drives.get("current"), dict):
        buckets.append(drives["current"])

    out = []
    for drive in buckets:
        if not isinstance(drive, dict):
            continue
        team = ((drive.get("team") or {}).get("abbreviation")
                or (drive.get("team") or {}).get("shortDisplayName"))
        for play in (drive.get("plays") or []):
            if not isinstance(play, dict):
                continue
            text = (play.get("text") or "").strip()
            if not text:
                continue
            period = ((play.get("period") or {}).get("number")
                      if isinstance(play.get("period"), dict) else play.get("period"))
            clock = ((play.get("clock") or {}).get("displayValue")
                     if isinstance(play.get("clock"), dict) else play.get("clock"))
            start = play.get("start") or {}
            out.append({
                "id": play.get("id"),
                "team": normalize_team_abbr(team) if team else None,
                "period": _safe_int(period, 0) or None,
                "clock": clock,
                "down": start.get("down") or None,
                "distance": start.get("distance"),
                "yardline": start.get("possessionText") or start.get("downDistanceText"),
                "text": text[:400],
                "yards": play_yards(text, play.get("statYardage")),
                "scoring": bool(play.get("scoringPlay")),
                "away_score": play.get("awayScore"),
                "home_score": play.get("homeScore"),
            })
    # Newest first -- a live feed is read from the top. ESPN lists drives
    # oldest-first, so this is a straight reverse rather than a sort on a
    # clock that counts DOWN within a period and would order wrongly.
    out.reverse()
    return out[:limit]


MOMENTUM_POINTS = 160


def extract_momentum(summary_json, max_points=MOMENTUM_POINTS):
    """The game's swing, as a win-probability curve.

    ESPN records the home team's win probability after every single play,
    which is the honest version of "momentum" -- not a vibe, but how much
    each play actually moved the result. Returned oldest-first as
    percentages, with the play that moved it most called out.

    Long-downed to `max_points` because a full game is 150-400 entries
    and the chart is a couple of hundred pixels wide: past that, points
    land on the same pixel and only cost payload. The first and last are
    always kept so the curve starts at the opening kickoff and ends where
    the game actually stands."""
    raw = (summary_json or {}).get("winprobability") or []
    points = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pct = entry.get("homeWinPercentage")
        if pct is None:
            continue
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            continue
        # ESPN gives a 0-1 fraction; a stray 0-100 payload is taken as-is.
        points.append({"home": round((pct * 100 if pct <= 1 else pct), 1),
                       "play_id": entry.get("playId")})
    if not points:
        return {"points": [], "swing": None}

    if len(points) > max_points:
        step = (len(points) - 1) / float(max_points - 1)
        kept = [points[int(round(i * step))] for i in range(max_points)]
        kept[-1] = points[-1]
        points = kept

    # The single biggest move, which is the play worth naming.
    swing = None
    for prev, cur in zip(points, points[1:]):
        delta = cur["home"] - prev["home"]
        if swing is None or abs(delta) > abs(swing["delta"]):
            swing = {"delta": round(delta, 1), "play_id": cur.get("play_id"),
                     "to": cur["home"]}
    if swing and abs(swing["delta"]) < 1:
        swing = None
    return {"points": points, "swing": swing}


def _odds_price(value):
    """American odds as they are written on a board: +150, -110."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.startswith(("+", "-")):
        return text
    try:
        n = int(float(text))
    except (TypeError, ValueError):
        return text
    return f"+{n}" if n > 0 else str(n)


def extract_betting(summary_json, away_abbr, home_abbr):
    """The betting board: every sportsbook line ESPN carries for this
    game, plus each side's record against the spread.

    Reported, never offered -- this shows what the market says about the
    game, the same way the score shows what the scoreboard says. No bet
    is placed, priced or linked from here."""
    picks = (summary_json or {}).get("pickcenter") or []
    lines = []
    for p in picks:
        if not isinstance(p, dict):
            continue
        away_o = p.get("awayTeamOdds") or {}
        home_o = p.get("homeTeamOdds") or {}
        spread = p.get("details") or p.get("spread")
        over_under = p.get("overUnder")
        if spread is None and over_under is None:
            continue
        lines.append({
            "provider": (p.get("provider") or {}).get("name") or "Consensus",
            "spread": spread if isinstance(spread, str) else (
                f"{spread:+g}" if isinstance(spread, (int, float)) else None),
            "over_under": over_under,
            "over_odds": _odds_price(p.get("overOdds")),
            "under_odds": _odds_price(p.get("underOdds")),
            "away_ml": _odds_price(away_o.get("moneyLine")),
            "home_ml": _odds_price(home_o.get("moneyLine")),
            "favorite": (away_abbr if away_o.get("favorite")
                         else (home_abbr if home_o.get("favorite") else None)),
        })

    ats = []
    for block in (summary_json or {}).get("againstTheSpread") or []:
        if not isinstance(block, dict):
            continue
        abbr = normalize_team_abbr((block.get("team") or {}).get("abbreviation"))
        records = block.get("records") or []
        summary = None
        for r in records:
            if isinstance(r, dict) and r.get("summary"):
                summary = r.get("summary")
                break
        if abbr and summary:
            ats.append({"team": abbr, "record": summary})
    return {"lines": lines, "ats": ats}


# Conditions to a single glyph, matched on ESPN's own words. Order
# matters: "partly cloudy" has to be tested before "cloudy", or every
# broken-cloud afternoon reads as overcast.
_WEATHER_EMOJI = [
    ("blizzard", "\u2744\uFE0F"), ("flurr", "\u2744\uFE0F"), ("snow", "\u2744\uFE0F"),
    ("sleet", "\U0001F328\uFE0F"), ("hail", "\U0001F328\uFE0F"), ("freezing", "\U0001F328\uFE0F"),
    ("thunder", "\u26C8\uFE0F"), ("lightning", "\u26C8\uFE0F"), ("storm", "\u26C8\uFE0F"),
    ("drizzle", "\U0001F327\uFE0F"), ("shower", "\U0001F327\uFE0F"), ("rain", "\U0001F327\uFE0F"),
    ("fog", "\U0001F32B\uFE0F"), ("haze", "\U0001F32B\uFE0F"), ("hazy", "\U0001F32B\uFE0F"),
    ("mist", "\U0001F32B\uFE0F"), ("smoke", "\U0001F32B\uFE0F"),
    ("wind", "\U0001F4A8"), ("breez", "\U0001F4A8"), ("blustery", "\U0001F4A8"),
    ("partly cloudy", "\u26C5"), ("partly sunny", "\u26C5"),
    ("mostly sunny", "\u26C5"), ("intermittent clouds", "\u26C5"),
    ("mostly cloudy", "\u2601\uFE0F"), ("overcast", "\u2601\uFE0F"), ("cloud", "\u2601\uFE0F"),
    ("sunny", "\u2600\uFE0F"), ("clear", "\u2600\uFE0F"), ("fair", "\u2600\uFE0F"),
]
INDOOR_EMOJI = "\U0001F3DF\uFE0F"


def weather_emoji(text):
    """The glyph for a condition, or None when the words do not say.

    Matched on ESPN's English, never on a code -- a word we can read is a
    word we can be right about."""
    low = (text or "").lower()
    if not low:
        return None
    for needle, emoji in _WEATHER_EMOJI:
        if needle in low:
            return emoji
    return None


def _fahrenheit(value):
    """A temperature only when it is plainly a temperature.

    ESPN has been seen returning strings here, and a stray value is worse
    than a blank -- so anything outside the range a football game has
    ever been played in is dropped rather than printed."""
    if value in (None, ""):
        return None
    try:
        temp = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return temp if -60 <= temp <= 140 else None


def extract_game_info(summary_json):
    """Everything about the occasion rather than the play: where it is
    being held, who is officiating, how many are watching, what the
    weather is doing.

    The condition is taken ONLY from ESPN's displayValue. Its weather
    block also carries a numeric conditionId, and that used to be the
    fallback -- which is how a Kansas City game in September came to
    report its weather as "1". ESPN does not publish what those numbers
    mean, and the obvious guess (the legacy Yahoo codes, where 1 is a
    tropical storm) is plainly wrong for the games it was tested on. So
    the id is never shown: no words from ESPN means no condition claimed,
    and the temperature stands on its own."""
    info = (summary_json or {}).get("gameInfo") or {}
    venue = info.get("venue") or {}
    address = venue.get("address") or {}
    weather = info.get("weather") or {}
    indoor = venue.get("indoor")

    condition = (weather.get("displayValue") or "").strip() or None
    temp = _fahrenheit(weather.get("temperature"))
    if temp is None:
        temp = _fahrenheit(weather.get("highTemperature"))

    # Under a roof the forecast is beside the point, and saying so is
    # both shorter and truer than reporting the weather outside.
    if indoor and not condition:
        condition = "Indoors"
    emoji = INDOOR_EMOJI if (indoor and condition == "Indoors") else weather_emoji(condition)

    return {
        "attendance": info.get("attendance"),
        "capacity": venue.get("capacity"),
        "indoor": indoor,
        "surface": "Grass" if venue.get("grass") else ("Turf" if venue.get("grass") is False else None),
        "city": address.get("city"),
        "state": address.get("state"),
        "weather": condition,
        "weather_emoji": emoji,
        "temperature": temp,
    }


def extract_field_position(summary_json):
    """Where the ball is right now: possession, yard line, down and
    distance. Returns None outside of live play, which is correct -- a
    finished or unstarted game has no current field position."""
    comp = (((summary_json or {}).get("header") or {}).get("competitions") or [{}])[0]
    situation = comp.get("situation") or (summary_json or {}).get("situation") or {}
    if not situation:
        return None
    poss = situation.get("possession")
    abbr = None
    # `possession` is a team ID, so resolve it against the competitors.
    for c in (comp.get("competitors") or []):
        if str(c.get("id")) == str(poss):
            abbr = normalize_team_abbr((c.get("team") or {}).get("abbreviation"))
    down_text = situation.get("downDistanceText") or situation.get("shortDownDistanceText")
    yardline = situation.get("yardLine")
    if abbr is None and not down_text and yardline is None:
        return None
    return {
        "possession": abbr,
        "down_distance": down_text,
        "yardline": _safe_int(yardline, None) if yardline is not None else None,
        "possession_text": situation.get("possessionText"),
        "is_red_zone": bool(situation.get("isRedZone")),
    }


# ESPN groups box-score statistics by category, and each category carries
# its own ordered `labels` with a matching `stats` array per athlete. The
# categories worth surfacing, in the order a box score reads.
_BOX_CATEGORY_ORDER = ["passing", "rushing", "receiving", "defensive",
                       "interceptions", "fumbles", "kicking", "punting", "returns"]

# Which side of the ball a player is on, by position. This is the ground
# truth and is checked FIRST, because classifying by stat category gets
# it wrong: ESPN's "fumbles" category lists the ball carriers who
# fumbled, not the defenders who recovered, so a quarterback who put the
# ball on the ground was being filed under Defense.
_OFFENSE_POSITIONS = {"QB", "RB", "FB", "HB", "WR", "TE",
                      "OL", "OT", "OG", "C", "G", "T", "LS"}
_DEFENSE_POSITIONS = {"DL", "DE", "DT", "NT", "EDGE",
                      "LB", "OLB", "ILB", "MLB",
                      "DB", "CB", "S", "SS", "FS", "NB"}
_SPECIAL_POSITIONS = {"K", "P", "PK"}

# Category fallback, only for a player whose position ESPN omits.
# "fumbles" is deliberately NOT here -- see above.
_BOX_DEFENSIVE = {"defensive", "interceptions"}

# The order a box score reads down the page: quarterbacks, then backs,
# then receivers, then the line; the defensive front, then the back
# seven; kickers last. A position ESPN gives us that isn't listed sorts
# after everything named, ahead of the unknown-position rows.
_BOX_POSITION_ORDER = [
    "QB", "RB", "HB", "FB", "WR", "TE",
    "OL", "OT", "OG", "C", "G", "T", "LS",
    "DL", "DE", "DT", "NT", "EDGE",
    "LB", "OLB", "ILB", "MLB",
    "DB", "CB", "S", "SS", "FS", "NB",
    "K", "PK", "P",
]
_BOX_POSITION_RANK = {p: i for i, p in enumerate(_BOX_POSITION_ORDER)}


def _box_num(value):
    """The leading number out of a box-score cell. ESPN mixes plain
    numbers ("84"), fractions ("24/35") and compounds ("2-14"), and only
    the first figure of each is the one being ranked on."""
    text = str(value if value is not None else "").strip()
    m = re.match(r"-?\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else 0.0


def _box_production(entry):
    """A single number standing in for how much a player did, used only
    to order players within their own position group.

    Deliberately crude and category-aware: it is a sort key, not a
    rating, and the page already prints the real stat line beside every
    name. Yards, scores and takeaways are what a box score is read for,
    so those are what it weighs."""
    total = 0.0
    for stat in entry.get("stats") or []:
        label = (stat.get("label") or "").upper()
        cat = stat.get("category") or ""
        value = _box_num(stat.get("value"))
        if label == "YDS":
            total += value * (0.04 if cat == "passing" else 0.1)
        elif label == "TD" and cat != "defensive":
            total += value * 6
        elif label == "REC":
            total += value * 2
        elif label == "CAR":
            total += value
        elif label == "TOT" and cat == "defensive":
            total += value * 3
        elif label == "SACKS":
            total += value * 8
        elif label == "INT" and cat != "passing":
            total += value * 10
        elif label == "TB" or label == "LOST":
            total -= value * 4
        elif label == "FG" or label == "XP":
            total += value * 3
    return total


def _box_sort_key(entry):
    """Position order first, production within the position second, and
    anyone who took the field without recording anything last -- the
    bench, in the same position order as everyone above it."""
    pos = (entry.get("position") or "").upper()
    rank = _BOX_POSITION_RANK.get(pos, len(_BOX_POSITION_ORDER) + (0 if pos else 1))
    produced = entry.get("production", 0.0)
    return (0 if produced > 0 else 1, rank, -produced, entry.get("name") or "")


def _box_side_for(position, categories):
    """Which panel a box-score player belongs on.

    Position wins whenever ESPN gives one. The stat categories a player
    appears in are only a fallback, and a poor one: plenty of categories
    are mixed, so guessing from them is how offensive players ended up
    filed under Defense."""
    pos = (position or "").upper()
    if pos in _DEFENSE_POSITIONS:
        return "defense"
    if pos in _OFFENSE_POSITIONS or pos in _SPECIAL_POSITIONS:
        return "offense"
    # No usable position. Fall back to categories, and require a
    # genuinely defensive one rather than a mixed one.
    if any(c in _BOX_DEFENSIVE for c in categories):
        return "defense"
    return "offense"


def extract_box_score(summary_json):
    """Per-team, per-player statistics: {team_abbr: {"offense": [...],
    "defense": [...]}}.

    Each player row carries the stat labels alongside the values, because
    ESPN's label set differs by category and hardcoding column headers
    here would silently mislabel numbers the day ESPN changes one."""
    players = (summary_json or {}).get("boxscore", {}).get("players") or []
    out = {}
    for team_block in players:
        if not isinstance(team_block, dict):
            continue
        abbr = normalize_team_abbr((team_block.get("team") or {}).get("abbreviation"))
        if not abbr:
            continue
        side = {"offense": [], "defense": []}
        # One athlete can appear in several categories (a back who also
        # caught passes); merge by athlete so the box score has one row
        # per player rather than one per category.
        merged = {}
        for cat in (team_block.get("statistics") or []):
            if not isinstance(cat, dict):
                continue
            name = (cat.get("name") or "").lower()
            labels = cat.get("labels") or []
            for athlete_row in (cat.get("athletes") or []):
                ath = athlete_row.get("athlete") or {}
                aid = ath.get("id")
                if not aid:
                    continue
                entry = merged.setdefault(aid, {
                    "id": aid,
                    "sid": None,
                    "name": ath.get("displayName") or ath.get("shortName") or "",
                    "position": ((ath.get("position") or {}).get("abbreviation")
                                 if isinstance(ath.get("position"), dict) else None),
                    "headshot": (ath.get("headshot") or {}).get("href")
                                if isinstance(ath.get("headshot"), dict) else None,
                    "stats": [],
                    "categories": set(),
                })
                values = athlete_row.get("stats") or []
                for label, value in zip(labels, values):
                    entry["stats"].append({"label": label, "value": value, "category": name})
                entry["categories"].add(name)
        for entry in merged.values():
            entry["categories"] = sorted(entry["categories"])
            entry["production"] = _box_production(entry)
            entry["bench"] = entry["production"] <= 0
            side[_box_side_for(entry["position"], entry["categories"])].append(entry)
        # Grouped by position in the order a box score reads, ranked by
        # production inside each position, with anyone who did not record
        # anything gathered at the bottom.
        for group in side.values():
            group.sort(key=_box_sort_key)
        out[abbr] = side
    return out


def _full_name_index(all_players):
    """{(team, "baker mayfield"): sleeper_id} -- ESPN's box score gives
    full display names, unlike play text which gives "B.Mayfield", so
    this is a separate index from _player_name_index."""
    index = {}
    for sid, p in (all_players or {}).items():
        first, last, team = p.get("first_name"), p.get("last_name"), p.get("team")
        if not first or not last:
            continue
        key = f"{first} {last}".lower()
        if team:
            index.setdefault((team, key), sid)
        index.setdefault((None, key), sid)
    return index


def attach_box_photos(box):
    """Fill in a headshot for every box-score player.

    ESPN supplies one for most players but not all, and a name with no
    face next to it in a list where everyone else has one reads as a
    rendering fault. Falls back to Sleeper's photo, matched on full name
    and team, and leaves the field None only when neither source knows
    the player -- the template then renders a neutral placeholder rather
    than a broken image."""
    if not box:
        return box
    try:
        index = _full_name_index(get_all_players())
    except Exception:
        return box
    for _team, sides in box.items():
        for group in sides.values():
            for entry in group:
                key = (entry.get("name") or "").lower()
                sid = index.get((_team, key)) or index.get((None, key))
                if sid:
                    entry["sid"] = sid
                    entry["headshot"] = entry.get("headshot") or player_photo_url(sid)
    return box


def attach_leader_ids(leaders):
    """Resolve each "Top Performers" leader to a Sleeper id, so the row
    is a link to that player's game rather than a dead line of text.

    ESPN gives a display name and a team, which is exactly what the
    box-score name index is built on, so the same index answers both."""
    if not leaders:
        return leaders
    try:
        index = _full_name_index(get_all_players())
    except Exception:
        return leaders
    for row in leaders:
        key = (row.get("athlete") or "").lower()
        row["sid"] = index.get((row.get("team"), key)) or index.get((None, key))
    return leaders


def extract_team_totals(summary_json):
    """Team-level totals ({team_abbr: [{label, value}, ...]}) for the
    header row above each team's player rows."""
    teams = (summary_json or {}).get("boxscore", {}).get("teams") or []
    out = {}
    for block in teams:
        if not isinstance(block, dict):
            continue
        abbr = normalize_team_abbr((block.get("team") or {}).get("abbreviation"))
        if not abbr:
            continue
        out[abbr] = [
            {"label": s.get("label") or s.get("name"), "value": s.get("displayValue")}
            for s in (block.get("statistics") or [])
            if isinstance(s, dict) and (s.get("label") or s.get("name"))
        ]
    return out


def extract_game_detail(summary_json):
    """Pure function, no I/O: turns a raw espn_game_summary() payload
    into the shape /game and /api/game-live both need. Field paths here
    are based on ESPN's commonly-documented (by the hobbyist community,
    since ESPN itself publishes no spec) summary shape. Several fields
    (venue, officials) are looked up at more than one plausible location
    since this is unverified against a live response from this sandbox
    (see /api/debug-espn?endpoint=summary&event=<id> once deployed) --
    a missing/renamed field degrades to an empty value here, never
    crashes the page. Box score/leaders/win-probability are legitimately
    empty for a game that hasn't kicked off yet -- that's not a bug, the
    UI shows pregame info (broadcast, odds, kickoff time) instead."""
    header = summary_json.get("header") or {}
    comp = (header.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    home_c = next((c for c in competitors if c.get("homeAway") == "home"), None) or {}
    away_c = next((c for c in competitors if c.get("homeAway") == "away"), None) or {}
    status = comp.get("status") or {}
    state = (status.get("type") or {}).get("state")

    def linescores(c):
        # ESPN's per-quarter entries have been observed under more than one
        # key ("value" being the documented one, "displayValue" a string
        # fallback seen on some real responses) -- if neither resolves for
        # ANY quarter, treat the whole list as unusable and return [] so
        # the UI hides the panel instead of rendering a row of "None".
        vals = []
        for ls in (c.get("linescores") or []):
            if not isinstance(ls, dict):
                continue
            v = ls.get("value")
            if v is None:
                v = ls.get("displayValue")
            vals.append(v)
        return vals if vals and any(v is not None for v in vals) else []

    def side(c):
        team = c.get("team") or {}
        abbr = normalize_team_abbr(team.get("abbreviation"))
        return {
            "abbr": abbr,
            "name": team.get("displayName") or abbr,
            "score": c.get("score"),
            "logo": team_logo_url(abbr),
            "linescores": linescores(c),
            "record": ((c.get("records") or [{}])[0]).get("summary"),
        }

    home, away = side(home_c), side(away_c)

    # Venue/officials are checked at several plausible locations -- ESPN's
    # summary endpoint has been observed to nest these under a top-level
    # "gameInfo" object in some sports and directly on the competition in
    # others, so try both rather than betting on just one.
    game_info = summary_json.get("gameInfo") or {}
    venue = game_info.get("venue") or comp.get("venue") or header.get("venue") or {}
    address = venue.get("address") or {}
    officials_raw = game_info.get("officials") or comp.get("officials") or summary_json.get("officials") or []
    officials = [
        {
            "name": o.get("displayName") or o.get("fullName") or o.get("name"),
            "position": (o.get("position") or {}).get("name") or o.get("position"),
        }
        for o in officials_raw
        if isinstance(o, dict)
    ]

    broadcasts = [
        b.get("name") or b.get("callLetters") or (b.get("names") or [None])[0]
        for b in (game_info.get("broadcasts") or comp.get("broadcasts") or [])
        if isinstance(b, dict)
    ]
    broadcasts = [b for b in broadcasts if b]

    odds_raw = (comp.get("odds") or summary_json.get("odds") or [{}])
    odds_entry = odds_raw[0] if isinstance(odds_raw, list) else odds_raw
    odds = None
    if isinstance(odds_entry, dict) and (odds_entry.get("details") or odds_entry.get("overUnder")):
        odds = {"spread": odds_entry.get("details"), "over_under": odds_entry.get("overUnder")}

    win_prob = None
    predictor = summary_json.get("predictor") or {}
    home_wp = (predictor.get("homeTeam") or {}).get("gameProjection")
    away_wp = (predictor.get("awayTeam") or {}).get("gameProjection")
    if home_wp is not None and away_wp is not None:
        win_prob = {"home_pct": round(float(home_wp)), "away_pct": round(float(away_wp))}

    team_stats = []
    box_teams = (summary_json.get("boxscore") or {}).get("teams") or []
    if len(box_teams) == 2:
        t0_abbr = normalize_team_abbr((box_teams[0].get("team") or {}).get("abbreviation"))
        home_entry, away_entry = (box_teams[0], box_teams[1]) if t0_abbr == home["abbr"] else (box_teams[1], box_teams[0])
        home_stats = {s.get("name"): s.get("displayValue") for s in (home_entry.get("statistics") or [])}
        away_stats = {s.get("name"): s.get("displayValue") for s in (away_entry.get("statistics") or [])}
        for s in home_entry.get("statistics") or []:
            name = s.get("name")
            team_stats.append({
                "label": s.get("label") or s.get("displayName") or name,
                "home": home_stats.get(name, "—"),
                "away": away_stats.get(name, "—"),
            })

    player_leaders = []
    for group in summary_json.get("leaders") or []:
        team_abbr = normalize_team_abbr((group.get("team") or {}).get("abbreviation"))
        for cat in group.get("leaders") or []:
            top = (cat.get("leaders") or [None])[0]
            if not top:
                continue
            player_leaders.append({
                "team": team_abbr,
                "category": cat.get("displayName") or cat.get("name"),
                "athlete": (top.get("athlete") or {}).get("displayName"),
                "stat_line": top.get("displayValue"),
            })

    return {
        "status": _ESPN_STATE_TO_STATUS.get(state, "scheduled"),
        "period": status.get("period"),
        "clock": status.get("displayClock"),
        "status_detail": (status.get("type") or {}).get("shortDetail"),
        "kickoff": comp.get("date") or header.get("date"),
        "venue": {"name": venue.get("fullName"), "city": address.get("city"), "state": address.get("state")},
        "officials": officials,
        "broadcasts": broadcasts,
        "odds": odds,
        "win_prob": win_prob,
        "home": home, "away": away,
        "team_stats": team_stats,
        "player_leaders": player_leaders,
    }


def _parse_espn_event(ev):
    """Pure function: one ESPN scoreboard 'event' -> the flat row shape
    nfl_schedule stores. Defensive about missing keys since this is an
    undocumented API -- a malformed event should be skipped, not crash
    the whole sync."""
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    state = ((comp.get("status") or {}).get("type") or {}).get("state")
    status = _ESPN_STATE_TO_STATUS.get(state, "scheduled")

    # ESPN reports "0" for both sides of a game that has not kicked off.
    # Storing that is how every unplayed game on a team's schedule became
    # a 0-0 TIE, and how "has a score" stopped meaning "has been played".
    # A game that has not started has no score: NULL.
    def score(side):
        if status == "scheduled":
            return None
        raw = side.get("score")
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    return {
        "espn_event_id": ev.get("id"),
        "kickoff": ev.get("date"),
        "home_team": normalize_team_abbr((home.get("team") or {}).get("abbreviation")),
        "away_team": normalize_team_abbr((away.get("team") or {}).get("abbreviation")),
        "home_score": score(home),
        "away_score": score(away),
        "status": status,
    }


def _event_belongs_to_season(row, season):
    """True if a parsed ESPN event's kickoff really falls inside `season`.

    An NFL season spans two calendar years -- September through early
    January of season+1 (and the Super Bowl in February of season+1) --
    so both years are legitimate for a given season, and nothing else
    is. This is the guard that makes a wrong-season response from ESPN
    (which is what silently poisoned this table before) impossible to
    write: no kickoff date, no write.

    January/February belong to the season that STARTED the previous
    fall, so a 2026-01-04 kickoff is season 2025, not 2026 -- the same
    rule current_nfl_season() already uses for "what season is it".
    """
    kickoff = row.get("kickoff")
    if not kickoff:
        return False
    try:
        year, month = int(str(kickoff)[:4]), int(str(kickoff)[5:7])
    except (ValueError, TypeError):
        return False
    return year - 1 == season if month <= 2 else year == season


def sync_week_schedule_to_db(season, week, season_type=2):
    """Fetch one week's games from ESPN and upsert into nfl_schedule.
    Returns rows upserted. Safe to call repeatedly (ON CONFLICT DO
    UPDATE) -- this is how in-progress/final scores get refreshed.

    Every event is checked against the season actually asked for before
    anything is written (see _event_belongs_to_season). ESPN answering
    with a different season than requested is not hypothetical -- it is
    exactly what happened for months here, and because the old upsert
    left `season`/`week` untouched on conflict, those wrong-season rows
    quietly refreshed the current season's rows instead of ever landing
    under the requested one. Now a mismatch is dropped outright, and a
    row that DOES belong gets its season/week corrected on conflict, so
    any row previously filed under the wrong season self-heals the first
    time that week is synced again."""
    if not DATABASE_URL:
        return 0
    data = espn_week_scoreboard(season, week, season_type)
    parsed = [r for r in (_parse_espn_event(ev) for ev in data.get("events", [])) if r]
    rows = [r for r in parsed if _event_belongs_to_season(r, season)]
    if not rows:
        return 0
    conn = get_db()
    try:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """INSERT INTO nfl_schedule
                           (espn_event_id, season, week, season_type, kickoff,
                            home_team, away_team, home_score, away_score, status, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                       ON CONFLICT (espn_event_id) DO UPDATE SET
                           season = EXCLUDED.season, week = EXCLUDED.week,
                           season_type = EXCLUDED.season_type,
                           home_team = EXCLUDED.home_team, away_team = EXCLUDED.away_team,
                           home_score = EXCLUDED.home_score, away_score = EXCLUDED.away_score,
                           status = EXCLUDED.status, kickoff = EXCLUDED.kickoff, updated_at = NOW()""",
                    (row["espn_event_id"], int(season), int(week), int(season_type), row["kickoff"],
                     row["home_team"], row["away_team"], row["home_score"], row["away_score"], row["status"]),
                )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def _row_get(row, key, default=None):
    """Read a column from a database row that may be a psycopg2 DictRow
    (which raises KeyError for an absent column rather than returning
    None) or a plain dict."""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def get_schedule_for_team_week(season, week, team_abbr, cache={}):
    """Opponent/home-away/kickoff/status for one team in one week, read
    from our own DB (survives cold caches/restarts, unlike a pure
    in-memory cache of ESPN's response). Returns None for a bye week or
    a week that hasn't been synced yet. TTL 600s."""
    key = (season, week, team_abbr)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 600:
        return entry["data"]
    if not DATABASE_URL:
        return None
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM nfl_schedule WHERE season = %s AND week = %s
                       AND (home_team = %s OR away_team = %s) LIMIT 1""",
                (season, week, team_abbr, team_abbr),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        cache[key] = {"data": None, "time": now}
        return None
    is_home = row["home_team"] == team_abbr
    data = {
        "opponent": row["away_team"] if is_home else row["home_team"],
        "home": is_home,
        "kickoff": row["kickoff"],
        "status": row["status"],
        "espn_event_id": row["espn_event_id"],
        # Oriented to the team that was asked about, not home/away, so a
        # caller never has to re-derive which score belongs to whom.
        # Fetched tolerantly: a scoreless row (a game not yet played, or
        # a query that didn't select these columns) should read as "no
        # score yet", never take down an opponent lookup that the whole
        # matchup-grading path depends on.
        "team_score": _row_get(row, "home_score" if is_home else "away_score"),
        "opp_score": _row_get(row, "away_score" if is_home else "home_score"),
    }
    cache[key] = {"data": data, "time": now}
    return data


def _parse_penalty_stat(value):
    """ESPN box scores commonly report penalties as a combined "5-45"
    (count-yards) string; be defensive since this is unverified against
    a live response -- also accept a bare number as a count with no
    yards figure, and anything unparseable as (None, None)."""
    if not value:
        return None, None
    s = str(value).strip()
    if "-" in s:
        parts = s.split("-", 1)
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None, None
    try:
        return int(s), None
    except ValueError:
        return None, None


def sync_referee_game(event_id, season, week):
    """One game's officiating crew + penalty stats -> referee_games.
    Single outbound ESPN call (via extract_game_detail/espn_game_summary,
    already cached), safe to call synchronously per-game from the
    backfill workflow's loop -- no background thread needed the way the
    18-week stats sync uses one. Returns True on success, False if the
    game had no usable data (so the backfill workflow can log/skip
    without aborting the whole run)."""
    if not DATABASE_URL:
        return False
    detail = extract_game_detail(espn_game_summary(event_id))
    if not detail["home"]["abbr"] or not detail["away"]["abbr"]:
        return False
    officials = detail["officials"]
    head_ref = next((o["name"] for o in officials if (o.get("position") or "").lower() == "referee"), None)
    if not head_ref and officials:
        head_ref = officials[0]["name"]
    home_pen_count, home_pen_yards = None, None
    away_pen_count, away_pen_yards = None, None
    for s in detail["team_stats"]:
        if "penalt" in (s.get("label") or "").lower():
            home_pen_count, home_pen_yards = _parse_penalty_stat(s.get("home"))
            away_pen_count, away_pen_yards = _parse_penalty_stat(s.get("away"))
            break
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO referee_games
                       (espn_event_id, season, week, referee_name, officials_json,
                        home_team, away_team, home_score, away_score,
                        home_penalties, home_penalty_yards, away_penalties, away_penalty_yards, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                   ON CONFLICT (espn_event_id) DO UPDATE SET
                       referee_name = EXCLUDED.referee_name, officials_json = EXCLUDED.officials_json,
                       home_score = EXCLUDED.home_score, away_score = EXCLUDED.away_score,
                       home_penalties = EXCLUDED.home_penalties, home_penalty_yards = EXCLUDED.home_penalty_yards,
                       away_penalties = EXCLUDED.away_penalties, away_penalty_yards = EXCLUDED.away_penalty_yards,
                       updated_at = NOW()""",
                (event_id, int(season), int(week), head_ref, psycopg2.extras.Json(officials),
                 detail["home"]["abbr"], detail["away"]["abbr"],
                 detail["home"]["score"], detail["away"]["score"],
                 home_pen_count, home_pen_yards, away_pen_count, away_pen_yards),
            )
        conn.commit()
    finally:
        conn.close()
    return True


def get_referee_tendencies(cache={}):
    """Per-referee tendency summary, GROUP BY referee_name over
    referee_games -- an on-demand cached aggregate (same pattern as
    get_season_finish_ranks) rather than a second synced summary table,
    since referee_games stays small (a 2-3 season backfill is ~800-900
    rows) so this query is cheap even run fresh. TTL 21600s (6h)."""
    now = time.time()
    if "data" in cache and now - cache.get("time", 0) < 21600:
        return cache["data"]
    if not DATABASE_URL:
        return {}
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT referee_name,
                       COUNT(*) AS games,
                       AVG(COALESCE(home_penalties, 0) + COALESCE(away_penalties, 0)) AS avg_penalties,
                       AVG(COALESCE(home_penalty_yards, 0) + COALESCE(away_penalty_yards, 0)) AS avg_penalty_yards,
                       AVG(COALESCE(home_score, 0) + COALESCE(away_score, 0)) AS avg_combined_score,
                       AVG(CASE WHEN home_score > away_score THEN 1.0 ELSE 0.0 END) AS home_win_rate,
                       AVG(COALESCE(home_penalties, 0) - COALESCE(away_penalties, 0)) AS home_penalty_diff
                FROM referee_games
                WHERE referee_name IS NOT NULL
                GROUP BY referee_name
                HAVING COUNT(*) >= 3
                ORDER BY games DESC
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    data = {
        r["referee_name"]: {
            "games": r["games"],
            "avg_penalties": round(float(r["avg_penalties"]), 1),
            "avg_penalty_yards": round(float(r["avg_penalty_yards"]), 1),
            "avg_combined_score": round(float(r["avg_combined_score"]), 1),
            "home_win_rate": round(float(r["home_win_rate"]), 3),
            "home_penalty_diff": round(float(r["home_penalty_diff"]), 2),
        }
        for r in rows
    }
    cache["data"] = data
    cache["time"] = now
    return data


_defense_vs_position_cache = {}


def get_defense_vs_position(season, cache=_defense_vs_position_cache):
    """{team: {position: {fpts_allowed_per_game, games, rank}}} -- how
    many fantasy points a team gives up per game to each position,
    ranked 1 (fewest allowed, toughest matchup) to N (most allowed,
    easiest matchup). Derived entirely from data already flowing through
    the app: get_season_stats' weekly fpts joined against the new
    schedule table via each player's *current* team (see the accepted
    trade-week approximation noted in the implementation plan -- a
    mid-season trade misattributes a handful of historical weeks).
    TTL 3600s; cheap to recompute since every input is itself cached.

    Self-heals nfl_schedule for THIS season the same way /scores and
    /matchups already do -- matchup grading calls this for season-1 too
    (see compute_matchup_grade's last-year fallback), and the recurring
    cron only ever syncs the CURRENT season, so without this a prior
    season's schedule would simply never exist in the DB and every
    fallback lookup would silently come back empty forever, not just
    "not synced yet". ensure_schedule_synced no-ops instantly once a
    season is confirmed present, so this costs nothing after the first
    call per season per process."""
    now = time.time()
    entry = cache.get(season)
    if entry and now - entry["time"] < 3600:
        # Deliberately BEFORE the self-heal call below. This function is
        # invoked once per graded player per season, so with the history
        # search reaching several seasons back a single /matchups render
        # calls it hundreds of times -- and ensure_schedule_synced runs a
        # COUNT(DISTINCT week) against nfl_schedule for any season it
        # hasn't yet confirmed complete. Probing ahead of the cache check
        # meant a season mid-backfill cost one such query on every one of
        # those calls. A warm cache now short-circuits the whole thing.
        return entry["data"]
    ensure_schedule_synced(season)

    all_players = get_all_players()
    season_stats = get_season_stats(season)
    allowed = {}
    for sid, stat in season_stats.items():
        p = all_players.get(sid)
        if not p or p.get("position") not in POSITIONS:
            continue
        team = p.get("team")
        if not team:
            continue
        pos = p["position"]
        for week, fpts in (stat.get("weeks") or {}).items():
            sched = get_schedule_for_team_week(season, week, team)
            if not sched:
                continue
            entry_pos = allowed.setdefault(sched["opponent"], {}).setdefault(pos, {"fpts": 0.0, "weeks": set()})
            entry_pos["fpts"] += fpts
            entry_pos["weeks"].add(week)

    result = {
        team: {
            pos: {"fpts_allowed_per_game": round(v["fpts"] / len(v["weeks"]), 1), "games": len(v["weeks"])}
            for pos, v in by_pos.items() if v["weeks"]
        }
        for team, by_pos in allowed.items()
    }
    for pos in POSITIONS:
        ranked = sorted(
            ((team, result[team][pos]["fpts_allowed_per_game"]) for team in result if pos in result[team]),
            key=lambda x: x[1],
        )
        for i, (team, _) in enumerate(ranked):
            result[team][pos]["rank"] = i + 1

    cache[season] = {"data": result, "time": now}
    return result


def _grade_reasoning(c):
    """One short, plain-English sentence explaining a matchup grade's
    components -- shown on the Matchups list and folded into the
    head-to-head comparison's reasons, so the grade never reads as a
    bare, unexplained letter.

    A player whose game for the week is already final gets a distinct,
    unambiguous callout instead of a pregame-style projection -- grading
    someone "start" or "sit" for a game that's already been played reads
    as broken, so this takes priority over everything else. "opponent not
    set yet" is reserved for an actual bye (no game on the schedule at
    all); a known opponent with no defense-vs-position data yet (typical
    in the first weeks of a season, before any team has faced that
    position enough) instead falls back to last year's number, flagged as
    such -- see def_source in compute_matchup_grade."""
    if c["game_status"] == "final":
        if c["actual_week_pts"] is not None:
            return f"Already played this week -- scored {c['actual_week_pts']:.1f} pts."
        return "Already played this week."
    if c["game_status"] == "in_progress":
        return "This game is live right now."

    if c["injury_tier"] in ("out", "admin"):
        return "Not expected to play this week."
    if c["injury_tier"] == "doubtful":
        return "Doubtful to play -- check the injury report before kickoff."

    if c["opponent"] is None:
        return "No game scheduled this week (bye)."

    if c["def_rank_used"] is None:
        # A genuine data void -- every season _league_average_defense
        # checked had zero teams with any data at all for this position.
        # Extremely rare (would require a season with no games played by
        # anyone at that position), unlike the old, common "no data yet
        # for this specific opponent" gap that used to land here.
        matchup_desc = "not enough defensive data yet to grade the matchup"
    else:
        if c["def_source"] == "current":
            source_note = ""
        elif c["def_source"] == "last_year":
            source_note = " (based on last year)"
        elif c["def_source"] == "historical":
            source_note = f" (based on {c['def_season_used']})"
        elif c["def_source"] == "league_average":
            source_note = " (league average -- no specific history for this opponent yet)"
        else:
            games = c["def_games_sampled"]
            source_note = f" (small sample -- {games} game{'s' if games != 1 else ''} this year)"
        # Percentile-based, not a hardcoded rank cutoff -- def_pool_size
        # varies (a league-average fallback may be built from fewer
        # teams than a full 32-team season), so a fixed "rank >= 24"
        # boundary would misclassify a matchup once the pool isn't a
        # full 32.
        pct = c["def_percentile"]
        if pct >= 0.7:
            matchup_desc = f"a great matchup{source_note}"
        elif pct >= 0.5:
            matchup_desc = f"a favorable matchup{source_note}"
        elif pct <= 0.25:
            matchup_desc = f"a tough matchup{source_note}"
        else:
            matchup_desc = f"an average matchup{source_note}"

    if c["trend_score"] >= 0.65:
        trend_desc = "trending up"
    elif c["trend_score"] <= 0.35:
        trend_desc = "trending down"
    else:
        trend_desc = "steady lately"

    sentence = f"{matchup_desc[0].upper()}{matchup_desc[1:]}, {trend_desc}"
    if c["injury_tier"] == "questionable":
        sentence += ", questionable to play"
    return sentence + "."


MIN_DEF_GAMES_FOR_CURRENT_YEAR = 4
# A defense needs to have actually faced a position this many times before
# its current-season sample outranks last year's full 17-game read on that
# same defense. Early in a season this is almost always false league-wide
# (nobody has played 4 games yet), so grading is effectively "last year's
# numbers only" until the sample is real -- then it switches over
# automatically, team by team, position by position, with no manual
# intervention as the season progresses.

DEF_HISTORY_SEASONS_BACK = 4

# How far back a power ranking may reach when the current season has not
# been played yet. Two, so a page opened in August still ranks on the
# season just finished rather than showing a row of dashes.
RANK_FALLBACK_SEASONS_BACK = 2
# How far back compute_matchup_grade searches for a specific opponent's
# defense-vs-position figure once the current season's sample is too
# thin to trust.
#
# This sat at 1 (last year only) for a while because setting it to 4
# once took /matchups offline completely. Worth being precise about why,
# since the depth itself was never the real problem: each extra season
# meant another get_defense_vs_position, and any season not yet synced
# triggered a full-season schedule AND stats background sync on the
# spot. Nothing capped those across seasons, so one page load could
# start ten concurrent 18-week sync threads -- on a 0.1-vCPU instance
# that starved the request thread that spawned them.
#
# Both halves of that are now fixed independently of the hosting tier:
# _BACKGROUND_SYNC_SLOTS caps concurrent syncs globally, and
# get_defense_vs_position checks its cache before probing the DB. The
# extra CPU means a cold computation is merely slower rather than fatal,
# but the structural fixes are what make this depth safe to keep.
#
# The league-average fallback below is unaffected either way -- it still
# guarantees a real number even when every one of these seasons is empty
# for a given team.


def _defense_entry_multi_season(opponent, position, season, seasons_back=DEF_HISTORY_SEASONS_BACK):
    """Searches season-1, season-2, ... back through `seasons_back` prior
    years for a real defense-vs-position entry for this exact opponent,
    returning (year, pool_size, entry) for the first one found, or
    (None, None, None) if every one of those years is empty for this
    team (get_defense_vs_position is cached per season, so re-checking
    several years costs nothing after the first computation each)."""
    for offset in range(1, seasons_back + 1):
        yr = season - offset
        dvp = get_defense_vs_position(yr)
        entry = dvp.get(opponent, {}).get(position)
        if entry:
            return yr, (len(dvp) or 32), entry
    return None, None, None


def _league_average_defense(position, season, seasons_back=DEF_HISTORY_SEASONS_BACK):
    """League-wide average fpts allowed per game to `position`, used as
    the absolute last resort when a specific opponent has zero real data
    across every season checked (a genuine data gap, or a team that
    doesn't resolve under this abbreviation in any synced season). A
    paid, data-driven product should never just say "not enough data" --
    a neutral, league-average estimate (and a middle-of-the-pack rank,
    so it doesn't quietly bias the grade toward "great" or "tough") beats
    showing no number at all. Searches the current season first, then
    the same prior seasons the specific-opponent lookup already checks,
    stopping at the first season with ANY real per-team data for this
    position."""
    for offset in range(0, seasons_back + 1):
        yr = season - offset
        dvp = get_defense_vs_position(yr)
        values = [team_data[position]["fpts_allowed_per_game"] for team_data in dvp.values() if position in team_data]
        if values:
            return {
                "fpts_allowed_per_game": round(sum(values) / len(values), 1),
                "season": yr,
                "pool_size": len(values),
            }
    return None


# Shared with _star_pct_for_composite below, so the star fill's tier
# slicing always stays perfectly in sync with the letter-grade
# boundaries -- one source of truth for where each of the 13 tiers
# starts and ends in composite space.
_GRADE_BANDS = [
    (0.92, "A+", 5), (0.85, "A", 5), (0.78, "A-", 5),
    (0.71, "B+", 4), (0.64, "B", 4), (0.57, "B-", 4),
    (0.50, "C+", 3), (0.43, "C", 3), (0.36, "C-", 3),
    (0.29, "D+", 2), (0.22, "D", 2), (0.15, "D-", 2),
]
# Ascending lower bound of each tier's composite range, F through A+
# (F's lower bound is 0.0, everything below D-'s 0.15).
_TIER_LOWER_BOUNDS = [0.0] + [threshold for threshold, _, _ in reversed(_GRADE_BANDS)]


def _letter_grade(composite):
    """13-tier letter grade (A+ down to F) from a 0-1 composite score --
    a bare 5-bucket A/B/C/D/F band crowded together matchups that were
    actually meaningfully different. Stars stay a coarser 1-5 scale
    grouped by the base letter (every A-tier is 5 stars, etc.)."""
    for threshold, grade, stars in _GRADE_BANDS:
        if composite >= threshold:
            return grade, stars
    return "F", 1


def _star_pct_for_composite(composite):
    """Continuous star fill (5-100%) that ALWAYS looks visibly different
    between two different letter grades, while still varying smoothly
    between two matchups that share the same grade.

    A plain composite*100 mapping (the previous approach) let a
    borderline B- (composite just above 0.57) and a borderline C+
    (composite just below 0.57) render with virtually identical fill,
    since they're separated by only a fraction of a point of raw
    composite despite carrying different letter grades -- exactly the
    "why do these look the same" bug this replaces.

    Each of the 13 tiers gets an equal-width slice of the 5-100 range,
    and only the middle 60% of each slice is actually used for the
    continuous within-tier fill -- the reserved 40% (20% on each side)
    guarantees a minimum gap between any two adjacent tiers' ranges, so
    crossing a letter-grade boundary always moves the fill by at least
    that gap, no matter how close the two composites are."""
    n = len(_TIER_LOWER_BOUNDS)
    tier = 0
    for i, lower in enumerate(_TIER_LOWER_BOUNDS):
        if composite >= lower:
            tier = i
    lower = _TIER_LOWER_BOUNDS[tier]
    upper = _TIER_LOWER_BOUNDS[tier + 1] if tier + 1 < n else 1.0
    frac = (composite - lower) / (upper - lower) if upper > lower else 0.0
    frac = max(0.0, min(1.0, frac))
    slice_width = (100 - 5) / n
    usable = slice_width * 0.6
    offset = slice_width * 0.2
    pct = 5 + tier * slice_width + offset + frac * usable
    return max(5, min(100, round(pct)))


# How many performances at each position count as "fantasy starter"
# territory in a typical 12-team league. A performance is graded against
# this pool rather than against every rostered player's week, because
# including every deep-bench 0.5-point line would drag the median down
# far enough that a merely-adequate game grades out as elite. Comparing a
# starter's day to other starters' days is both the intuitive reading of
# "how good was this?" and the standard way fantasy replacement level is
# defined.
_PERF_POOL_SIZE = {
    "QB": 12, "RB": 24, "WR": 36, "TE": 12,
    # IDP leagues typically start more defenders than any single
    # position group of offensive skill players, and the scoring is much
    # flatter, so the pools are wider -- a top-24 linebacker week is
    # still an ordinary starter week in most formats.
    "DL": 24, "LB": 24, "DB": 24,
}
_PERF_POOL_DEFAULT = 24

# Last-resort reference points if no season anywhere has usable data --
# approximate PPR per-game medians for a starter at each position. Only
# ever reached on a completely unseeded database, and always reported
# with source "baseline" so the UI can say so rather than presenting a
# guess as a measurement.
_PERF_BASELINE_MEDIAN = {
    "QB": 17.0, "RB": 12.0, "WR": 11.0, "TE": 8.0,
    "DL": 7.0, "LB": 9.0, "DB": 8.0,
}

_perf_distribution_cache = {}


def get_performance_distribution(season, cache=_perf_distribution_cache):
    """{position: sorted list of per-game fantasy points} for the
    starter-caliber performances of one season -- the reference a single
    game's output is graded against.

    Built from data already in the database (get_season_stats joined to
    each player's position), so it needs no new source. Cached for an
    hour per season; the shape of a full season's distribution barely
    moves week to week, so this does not need to be fresh to be right."""
    season = _safe_int(season, int(SEASON))
    now = time.time()
    entry = cache.get(season)
    if entry and now - entry["time"] < 3600:
        return entry["data"]

    all_players = get_all_players()
    season_stats = get_season_stats(season)
    # week -> position -> [fpts], so each week can be truncated to its own
    # starter pool before everything is pooled together. Truncating only
    # at the end would let one huge week's depth dilute another's.
    by_week = {}
    for sid, stat in season_stats.items():
        p = all_players.get(sid)
        pos = (p or {}).get("position")
        if not pos or pos not in SCORED_POSITIONS:
            continue
        for week, fpts in (stat.get("weeks") or {}).items():
            if not isinstance(fpts, (int, float)):
                continue
            by_week.setdefault(week, {}).setdefault(pos, []).append(float(fpts))

    pools = {}
    for _week, by_pos in by_week.items():
        for pos, values in by_pos.items():
            values.sort(reverse=True)
            keep = _PERF_POOL_SIZE.get(pos, _PERF_POOL_DEFAULT)
            pools.setdefault(pos, []).extend(values[:keep])
    for pos in pools:
        pools[pos].sort()

    cache[season] = {"data": pools, "time": now}
    return pools


def _performance_pool(position, season, seasons_back=DEF_HISTORY_SEASONS_BACK):
    """The reference distribution to grade a `position` performance
    against, as (pool, source_season).

    Prefers the most recently COMPLETED season over the one in progress:
    a full season is a stable, finished distribution, while the current
    one is still a handful of weeks deep in September and would have its
    shape shift underneath the grades every week. Falls back through
    earlier seasons, then to the current one, then gives up and lets the
    caller use the static baseline."""
    for yr in [season - 1] + [season] + [season - o for o in range(2, seasons_back + 1)]:
        if yr < 1:
            continue
        pool = (get_performance_distribution(yr) or {}).get(position)
        if pool and len(pool) >= 20:
            return pool, yr
    return None, None


# The 0-10 performance score's anchor points. Each one is a real,
# measured feature of the positional distribution rather than a taste
# call, which is what makes the number defensible:
#
#   0.0  ->  zero fantasy points (the player did nothing)
#   5.0  ->  the MEDIAN starter performance at that position
#   9.0  ->  the 99th percentile -- about the best anyone manages
#
# Above the 99th the same slope simply CONTINUES. There is deliberately
# no ceiling: an all-time game should be free to score 12 or 14 and say
# so, rather than being flattened into a 10 alongside every other big
# day. That open top end is the whole reason this isn't a percentile --
# a percentile puts a 26-point game and a 40-point game within a
# rounding error of each other, because both are "better than ~99% of
# games". Anchoring the midpoint on the median is what keeps an average
# day reading as a 5 instead of drifting with the distribution's skew.
_SCORE_MID = 5.0
_SCORE_P99 = 9.0


def _percentile_in(pool, value):
    """Where `value` falls in a sorted pool, 0.0-1.0, interpolating
    between neighbours so two close performances never collapse onto the
    same number."""
    if not pool:
        return 0.0
    i = bisect.bisect_left(pool, value)
    if i <= 0:
        return 0.0
    if i >= len(pool):
        return 1.0
    lo, hi = pool[i - 1], pool[i]
    frac = 0.0 if hi == lo else (value - lo) / (hi - lo)
    return (i - 1 + frac) / len(pool)


def _pool_quantile(pool, f):
    return pool[min(len(pool) - 1, int(f * len(pool)))] if pool else 0.0


def performance_score(fpts, median, p99):
    """The 0-10 number itself, as a pure function of the performance and
    two anchors -- separated out so it can be reasoned about and tested
    without a database behind it.

    Piecewise linear: 0 -> 0.0, `median` -> 5.0, `p99` -> 9.0, and then
    the same slope onward with NO upper bound -- a once-a-decade game is
    allowed to score 13 and be recognisable as one."""
    fpts = float(fpts or 0)
    if fpts <= 0 or median <= 0:
        return 0.0
    if fpts <= median:
        score = _SCORE_MID * (fpts / median)
    else:
        span = p99 - median
        slope = (_SCORE_P99 - _SCORE_MID) / span if span > 0 else 0.0
        score = _SCORE_MID + (fpts - median) * slope
    return round(max(0.0, score), 1)


def _score_class(score):
    """Colour band for a score, keyed to what the anchors actually mean:
    5.0 is an average starter day, 9.0 is roughly the top 1%. Anything
    past 9 is beyond the 99th percentile and gets its own band, which is
    only reachable because the scale has no ceiling."""
    if score >= 9.0:
        return "historic"
    if score >= 7.0:
        return "elite"
    if score >= 5.5:
        return "good"
    if score >= 3.5:
        return "mid"
    return "poor"


def grade_performance(position, fpts, season=None):
    """Rate one game's fantasy output against the distribution of
    starter-caliber performances at that position.

    The scale is open-ended: 5.0 is an average starter game and 9.0 is
    about the 99th percentile, but nothing caps the top, so a genuinely
    historic performance scores above 10 rather than being flattened
    into a tie with every other big day.

    Returns {score, score_class, percentile, better_than, pool_size,
    median, p99, source, source_season} -- the anchors and the percentile
    travel with the score so the UI can show exactly what the number was
    measured against, rather than asking anyone to trust a bare figure.

    `source` is "distribution" for a real measured pool and "baseline"
    for the static fallback, which only happens on an unseeded database.
    Never returns None: a performance always gets a score."""
    season = _safe_int(season if season is not None else SEASON, int(SEASON))
    fpts = float(fpts or 0)
    pool, source_season = _performance_pool(position, season)

    if pool:
        median = _pool_quantile(pool, 0.50)
        p99 = _pool_quantile(pool, 0.99)
        better_than = bisect.bisect_left(pool, fpts)
        percentile = _percentile_in(pool, fpts)
        source, pool_size = "distribution", len(pool)
    else:
        # No season anywhere has a usable pool. Fall back to documented
        # positional medians so a score still exists, and say so.
        median = _PERF_BASELINE_MEDIAN.get(position, 10.0)
        p99 = median * 2.6   # the median-to-99th ratio real pools show
        better_than, source, pool_size, source_season = None, "baseline", None, None
        percentile = max(0.0, min(1.0, fpts / (median * 2))) if median else 0.0

    score = performance_score(fpts, median, p99)
    return {
        "score": score,
        # "2.1x an average game" lands faster than any percentile does.
        "vs_median": round(fpts / median, 1) if median else None,
        "score_class": _score_class(score),
        "percentile": round(percentile * 100),
        "better_than": better_than,
        "pool_size": pool_size,
        "median": round(median, 1),
        "p99": round(p99, 1),
        "source": source,
        "source_season": source_season,
    }


def _star_pct_for_grade(grade):
    """Star fill for a grade that was set directly rather than derived
    from a composite -- the injury overrides (OUT/IR -> F, Doubtful ->
    D-), where the composite deliberately no longer describes the
    outlook.

    Returns the middle of that grade's own slice, so the stars and the
    letter on the badge can never disagree. These used to be hardcoded
    (F = 8%, D- = 25%), and 25% actually landed inside the D band, not
    D- -- a Doubtful player's badge said "D-" while their stars drew a
    "D". Deriving it from the same tier math that _star_pct_for_composite
    uses makes that class of mismatch impossible for any grade."""
    n = len(_TIER_LOWER_BOUNDS)
    # _GRADE_ORDER runs best-to-worst; tier indexes run worst-to-best.
    tier = n - 1 - _GRADE_ORDER.index(grade) if grade in _GRADE_ORDER else 0
    slice_width = (100 - 5) / n
    return max(5, min(100, round(5 + tier * slice_width + slice_width * 0.5)))


def _grade_css_class(grade):
    """CSS-safe token for a letter grade -- a bare '+'/'-' isn't valid in
    a plain class-selector token, so 'A+' -> 'ap', 'B-' -> 'bm', 'C' ->
    'c'. The badge's visible text still shows the real letter grade;
    only the class list uses this."""
    return grade.lower().replace("+", "p").replace("-", "m")


# Full best-to-worst order, including the two injury-forced overrides
# (out/admin -> "F", doubtful -> "D-") compute_matchup_grade can also
# return -- covers every value compare_matchups needs to rank between,
# so head-to-head comparisons never KeyError on a grade this scale
# actually produces.
_GRADE_ORDER = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"]
_GRADE_RANK = {g: len(_GRADE_ORDER) - i for i, g in enumerate(_GRADE_ORDER)}


_matchup_grade_cache = {}


def compute_matchup_grade(sid, season, week, cache=_matchup_grade_cache):
    """Composite 'should you start them' grade for one player in one
    week: opponent defense strength at their position, recent scoring
    trend, a talent baseline from their dynasty value, and recent
    week-to-week consistency. An OUT/IR/suspended-type injury caps the
    grade at F (they're not playing, matchup quality is irrelevant);
    Doubtful caps at D; Questionable applies a moderate penalty instead
    of a hard cap, since questionable players often do play. Returns
    None for a non-skill-position player. TTL 3600s, keyed by
    (sid, season, week) -- cheap since every input is already cached,
    safe to compute for every rostered skill player on a page render."""
    key = (sid, season, week)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 3600:
        return entry["data"]

    all_players = get_all_players()
    p = all_players.get(sid)
    if not p or p.get("position") not in POSITIONS:
        return None
    position = p["position"]
    team = p.get("team")

    sched = get_schedule_for_team_week(season, week, team) if team else None
    dvp = get_defense_vs_position(season)
    opp_entry = dvp.get(sched["opponent"], {}).get(position) if sched else None

    # Last season's version of the same figure -- early in a season the
    # current-year sample per defense is thin to nonexistent (zero games
    # in week 1, a handful for the first month), so a defense needs at
    # least MIN_DEF_GAMES_FOR_CURRENT_YEAR games logged against this
    # position before its current-year number outranks a real prior-year
    # read on the same defense. Below that threshold the grade searches
    # backward through real history (see _defense_entry_multi_season);
    # once a defense clears the current-year threshold (which happens
    # automatically, team by team, as the season plays out), the switch
    # to this year's real, current data is also automatic.
    if opp_entry and opp_entry["games"] >= MIN_DEF_GAMES_FOR_CURRENT_YEAR:
        def_rank_used, def_source, def_pool_size = opp_entry["rank"], "current", len(dvp) or 32
        def_games_sampled = opp_entry["games"]
        def_fpts_allowed_pg_used, def_season_used = opp_entry["fpts_allowed_per_game"], season
    else:
        hist_year, hist_pool_size, hist_entry = (
            _defense_entry_multi_season(sched["opponent"], position, season) if sched else (None, None, None)
        )
        if hist_entry:
            def_rank_used, def_pool_size = hist_entry["rank"], hist_pool_size
            def_source = "last_year" if hist_year == season - 1 else "historical"
            def_games_sampled = hist_entry["games"]
            def_fpts_allowed_pg_used, def_season_used = hist_entry["fpts_allowed_per_game"], hist_year
        elif opp_entry:
            # Early season, no real prior-year data available either (a
            # team that doesn't resolve under this abbreviation in any
            # synced season) -- better than nothing, but flagged
            # distinctly so the UI is honest about how thin it is.
            def_rank_used, def_source, def_pool_size = opp_entry["rank"], "current_thin", len(dvp) or 32
            def_games_sampled = opp_entry["games"]
            def_fpts_allowed_pg_used, def_season_used = opp_entry["fpts_allowed_per_game"], season
        else:
            # Absolute last resort: this specific opponent has zero real
            # data anywhere in the seasons checked -- a data-driven, paid
            # product should never just say "not enough data", so fall
            # back to a neutral, league-wide average for the position
            # instead of leaving this empty. Only a genuine data void
            # (no team anywhere has any data for this position, in any
            # season checked) leaves this None.
            league_avg = _league_average_defense(position, season) if sched else None
            if league_avg:
                def_pool_size = league_avg["pool_size"]
                def_rank_used = max(1, round(def_pool_size / 2))  # neutral, middle-of-the-pack
                def_source = "league_average"
                def_games_sampled = None
                def_fpts_allowed_pg_used, def_season_used = league_avg["fpts_allowed_per_game"], league_avg["season"]
            else:
                def_rank_used, def_source, def_pool_size, def_games_sampled = None, None, 32, None
                def_fpts_allowed_pg_used, def_season_used = None, None
    def_percentile = (def_rank_used - 1) / max(def_pool_size - 1, 1) if def_rank_used is not None else 0.5
    # def_rank_used counts from 1 = fewest points allowed (toughest
    # matchup) -- correct for the composite math above, but "#3 of 32"
    # on its own doesn't say whether #3 is a great or a brutal matchup
    # unless the reader already knows that convention. Flipped into "how
    # many points allowed" terms instead -- #1 here means the defense
    # that allows the MOST (the single easiest matchup at the position)
    # -- "#1 most points allowed" reads correctly without needing any
    # convention explained alongside it.
    def_rank_most_pts_used = (def_pool_size - def_rank_used + 1) if def_rank_used is not None else None

    season_stats = get_season_stats(season)
    stat = season_stats.get(sid, {})
    weeks_sorted = sorted((stat.get("weeks") or {}).items())
    actual_week_pts = (stat.get("weeks") or {}).get(week)
    recent = [fpts for _, fpts in weeks_sorted[-4:]]
    season_avg = (stat["fpts"] / stat["games"]) if stat.get("games") else 0
    recent_avg = sum(recent) / len(recent) if recent else season_avg
    if season_avg > 0:
        trend_score = max(0.0, min(1.0, 0.5 + (recent_avg - season_avg) / (season_avg * 2)))
    else:
        trend_score = 1.0 if recent_avg > 0 else 0.5

    fc_players = get_fantasycalc_values(1)["players"]
    position_rank = (fc_players.get(sid) or {}).get("position_rank")
    # Rough percentile against a ~60-deep starter pool per position -- good
    # enough as a "is this even a startable-tier player" talent floor.
    talent_score = max(0.0, min(1.0, 1 - (position_rank - 1) / 60)) if position_rank else 0.3

    if len(recent) >= 2:
        mean_r = sum(recent) / len(recent)
        stdev = (sum((x - mean_r) ** 2 for x in recent) / len(recent)) ** 0.5
        consistency_score = max(0.0, min(1.0, 1 - stdev / mean_r)) if mean_r > 0 else 0.5
    else:
        consistency_score = 0.5

    composite = 0.40 * def_percentile + 0.25 * trend_score + 0.20 * talent_score + 0.15 * consistency_score

    badge = _injury_badge(p)
    tier = badge["tier"] if badge else None
    if tier == "questionable":
        composite *= 0.85

    if tier in ("out", "admin"):
        grade, stars, star_pct = "F", 1, _star_pct_for_grade("F")
    elif tier == "doubtful":
        grade, stars, star_pct = "D-", 2, _star_pct_for_grade("D-")
    else:
        grade, stars = _letter_grade(composite)
        # Tier-normalized fill (see _star_pct_for_composite) -- two
        # matchups in the same letter tier still show different amounts
        # of fill, but two DIFFERENT letter grades are now always
        # visibly distinct too, which a raw composite*100 mapping didn't
        # guarantee near a tier boundary.
        star_pct = _star_pct_for_composite(composite)

    components = {
        "opponent": sched["opponent"] if sched else None,
        "def_rank": opp_entry["rank"] if opp_entry else None,
        "def_fpts_allowed_pg": opp_entry["fpts_allowed_per_game"] if opp_entry else None,
        "def_rank_used": def_rank_used,
        "def_rank_most_pts_used": def_rank_most_pts_used,
        "def_fpts_allowed_pg_used": def_fpts_allowed_pg_used,
        "def_season_used": def_season_used,
        "def_pool_size": def_pool_size,
        "def_source": def_source,
        "def_games_sampled": def_games_sampled,
        "def_percentile": round(def_percentile, 2),
        "trend_score": round(trend_score, 2),
        "talent_score": round(talent_score, 2),
        "consistency_score": round(consistency_score, 2),
        "composite": round(composite, 2),
        "injury_tier": tier,
        "game_status": sched["status"] if sched else None,
        "actual_week_pts": actual_week_pts,
    }
    data = {
        "grade": grade, "grade_class": _grade_css_class(grade), "stars": stars, "star_pct": star_pct,
        "reasoning": _grade_reasoning(components), "components": components,
    }
    cache[key] = {"data": data, "time": now}
    return data


def _player_recent_games(sid, season, n=5):
    """Average fantasy points over a player's last N games played,
    walking back into last season if the current one doesn't have N
    games yet -- so early-season "recent form" isn't computed off just
    1-2 data points. Returns None if the player has no logged games at
    all in either season."""
    cur_weeks = sorted((get_season_stats(season).get(sid, {}).get("weeks") or {}).items())
    tagged = [(season, wk, pts) for wk, pts in cur_weeks]
    if len(tagged) < n:
        last_weeks = sorted((get_season_stats(season - 1).get(sid, {}).get("weeks") or {}).items())
        tagged = [(season - 1, wk, pts) for wk, pts in last_weeks] + tagged
    last_n = tagged[-n:]
    if not last_n:
        return None
    return {
        "avg": round(sum(pts for _, _, pts in last_n) / len(last_n), 1),
        "games": len(last_n),
        "crossed_season": any(yr != season for yr, _, _ in last_n),
    }


H2H_SEASONS_BACK = 6
# How far back head-to-head history will look for meetings between a
# player's team and a specific opponent. Two teams often meet only once
# a year, and non-divisional opponents can skip years entirely, so
# showing a real "last 3 meetings" needs a wide window -- six seasons is
# usually enough to find three meetings even for a rare inter-conference
# pairing. The search below stops as soon as it has enough, so this is a
# ceiling on how far it MAY look, not how far it usually does.


def _player_history_vs_opponent(sid, season, team, opponent, max_meetings=3,
                                seasons_back=H2H_SEASONS_BACK):
    """The last `max_meetings` games (oldest first) where this player's
    current team faced `opponent`, via the same current-team schedule
    join every other matchup figure in this app relies on (see
    get_defense_vs_position's docstring re: the accepted trade-week
    approximation).

    Searches newest season first and stops the moment it has enough
    meetings, then flips the result back to oldest-first for display.
    That direction matters more than the depth does: the old version
    walked every season oldest-first and then threw away all but the
    last three, so its cost was always the full window. Walking
    backwards means the common case (a divisional opponent played twice
    a year) finishes inside one or two seasons and never touches the
    rest, which is what makes a six-season ceiling cheaper in practice
    than the old two-season floor.

    Deep history was briefly cut to two seasons because each extra
    season could kick off its own background sync and a single Compare
    click fanned out into a dozen of them. That fan-out is now bounded
    globally by _BACKGROUND_SYNC_SLOTS and the repeat DB probes by
    _seed_probe_due, so depth is no longer what makes Compare slow."""
    if not team or not opponent:
        return []
    out = []
    for yr in range(season, season - seasons_back - 1, -1):
        ensure_schedule_synced(yr)
        weeks = sorted((get_season_stats(yr).get(sid, {}).get("weeks") or {}).items())
        meetings_this_year = []
        for wk, pts in weeks:
            sched = get_schedule_for_team_week(yr, wk, team)
            if sched and sched["opponent"] == opponent:
                meetings_this_year.append({"season": yr, "week": wk, "fpts": pts})
        # Prepend: we're walking seasons backwards, but each season's own
        # weeks are already in ascending order.
        out = meetings_this_year + out
        if len(out) >= max_meetings:
            break
    return out[-max_meetings:]


def compare_matchups(sid_a, sid_b, season, week):
    """Head-to-head start/sit call between two players: reuses
    compute_matchup_grade for each (same cache, so this is free once
    the matchups page has already computed either grade) and builds a
    plain-English case for whichever one grades out ahead. Returns None
    if either sid isn't a graded skill-position player."""
    if sid_a == sid_b:
        return None
    grade_a = compute_matchup_grade(sid_a, season, week)
    grade_b = compute_matchup_grade(sid_b, season, week)
    if not grade_a or not grade_b:
        return None

    all_players = get_all_players()
    season_stats = get_season_stats(season)

    def summarize(sid, grade):
        p = all_players.get(sid, {})
        stat = season_stats.get(sid, {})
        badge = _injury_badge(p)
        c = grade["components"]
        l5 = _player_recent_games(sid, season, 5)
        history_vs_opp = _player_history_vs_opponent(sid, season, p.get("team"), c["opponent"])
        return {
            "sid": sid,
            "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position": p.get("position"), "team": p.get("team") or "FA",
            "photo": player_photo_url(sid),
            "opponent": c["opponent"], "def_rank": c["def_rank"],
            "def_fpts_allowed_pg": c["def_fpts_allowed_pg"],
            "def_rank_used": c["def_rank_used"], "def_rank_most_pts_used": c["def_rank_most_pts_used"],
            "def_fpts_allowed_pg_used": c["def_fpts_allowed_pg_used"],
            "def_season_used": c["def_season_used"], "def_pool_size": c["def_pool_size"],
            "def_source": c["def_source"], "def_games_sampled": c["def_games_sampled"],
            "grade": grade["grade"], "grade_class": grade["grade_class"], "stars": grade["stars"], "star_pct": grade["star_pct"], "composite": c["composite"],
            "season_avg": round(stat["fpts"] / stat["games"], 1) if stat.get("games") else 0.0,
            "recent_avg": l5["avg"] if l5 else 0.0,
            "recent_games": l5["games"] if l5 else 0,
            "recent_crossed_season": l5["crossed_season"] if l5 else False,
            "history_vs_opp": history_vs_opp,
            "injury": (badge["title"] if badge else "Healthy"),
            "injury_tier": c["injury_tier"],
            "reasoning": grade["reasoning"],
            "game_status": c["game_status"],
            "actual_week_pts": c["actual_week_pts"],
        }

    a, b = summarize(sid_a, grade_a), summarize(sid_b, grade_b)

    if _GRADE_RANK[a["grade"]] != _GRADE_RANK[b["grade"]]:
        start, sit = (a, b) if _GRADE_RANK[a["grade"]] > _GRADE_RANK[b["grade"]] else (b, a)
    else:
        start, sit = (a, b) if a["composite"] >= b["composite"] else (b, a)

    # Build the reasons in order of how much they actually drove the
    # call -- an injury edge first (it's decisive), then the matchup,
    # then recent form, falling back to "just grades out higher" only
    # if nothing else distinguishes them.
    hurt_tiers = ("out", "admin", "doubtful")
    reasons = []
    if sit["injury_tier"] in hurt_tiers and start["injury_tier"] not in hurt_tiers:
        reasons.append(f"{sit['name']} carries an injury designation ({sit['injury']}) that caps their outlook this week.")
    # Compare whichever defensive figure actually fed each player's
    # grade (this year's real sample, or a real prior-year read) --
    # comparing start's current-year rank against sit's current-year
    # rank doesn't mean much if one of them is grading off a prior
    # season's numbers because their current sample is too thin to
    # trust yet. Skipped entirely when either side fell all the way back
    # to a league-average estimate (see def_source == "league_average"),
    # since "X's opponent ranks #N" would misattribute a league-wide
    # number to that specific opponent -- the per-side bullets below
    # still surface that fallback honestly on its own.
    if (
        start["def_rank_used"] is not None and sit["def_rank_used"] is not None
        and start["def_rank_used"] != sit["def_rank_used"]
        and start["def_source"] != "league_average" and sit["def_source"] != "league_average"
    ):
        if start["def_rank_used"] > sit["def_rank_used"]:
            reasons.append(
                f"{start['name']} draws the easier matchup -- {start['opponent']} ranks #{start['def_rank_most_pts_used']} most points allowed "
                f"to {start['position']} in {start['def_season_used']}, vs. {sit['opponent']} at #{sit['def_rank_most_pts_used']} for {sit['name']}."
            )
    if start["recent_avg"] > sit["recent_avg"] + 1:
        reasons.append(f"{start['name']} is trending up recently ({start['recent_avg']} pts/gm over their last {start['recent_games']} games vs. {sit['recent_avg']} for {sit['name']}).")
    if not reasons:
        # "Grades out higher (B- vs. B-)" reads as a contradiction when
        # the two letter grades are actually tied -- that happens
        # whenever nothing else above distinguished them and the tie
        # was broken by the underlying composite score alone, so say
        # that instead of implying a letter-grade difference that isn't
        # there.
        if start["grade"] == sit["grade"]:
            reasons.append(
                f"{start['name']} and {sit['name']} both grade out as a {start['grade']} this week -- "
                f"{start['name']} edges it on the underlying matchup score ({start['composite']:.2f} vs {sit['composite']:.2f})."
            )
        else:
            reasons.append(f"{start['name']} grades out higher overall this week ({start['grade']} vs. {sit['grade']}).")

    # Beyond the factors that actually decided the call above, always
    # surface the underlying stats themselves -- each player's last-5-
    # game form (crossing into last season early on, same as the grade's
    # own trend factor), each side's opponent-defense split, and any
    # real history against this exact opponent -- so the comparison
    # reads as "the stats behind the call" the page promises, not just a
    # bare verdict.
    for side in (start, sit):
        if side["recent_games"]:
            note = " (includes last season)" if side["recent_crossed_season"] else ""
            reasons.append(f"{side['name']} has averaged {side['recent_avg']} pts over their last {side['recent_games']} games{note}.")
    for side in (start, sit):
        if side["opponent"] and side["def_rank_used"] is not None:
            if side["def_source"] == "league_average":
                # Never attribute a league-wide average to the specific
                # opponent as if it were their real number -- label it
                # for what it is.
                reasons.append(
                    f"No specific history for {side['opponent']} vs {side['position']} yet -- using the "
                    f"{side['def_season_used']} league average of {side['def_fpts_allowed_pg_used']} pts/gm instead."
                )
            else:
                reasons.append(
                    f"{side['opponent']} vs {side['position']} in {side['def_season_used']}: allowed "
                    f"{side['def_fpts_allowed_pg_used']} pts/gm (#{side['def_rank_most_pts_used']} most points allowed)."
                )
    for side in (start, sit):
        hist = side["history_vs_opp"]
        if hist:
            avg_hist = round(sum(g["fpts"] for g in hist) / len(hist), 1)
            # Oldest-to-newest game log, so the trend reads left-to-right
            # the way a schedule would -- not just the average and the
            # single most recent result.
            games_desc = ", ".join(f"{g['fpts']} pts ({g['season']} wk{g['week']})" for g in hist)
            reasons.append(
                f"{side['name']}'s last {len(hist)} meeting(s) with {side['opponent']}: {games_desc} -- averaging {avg_hist} pts."
            )

    return {"a": a, "b": b, "start_sid": start["sid"], "sit_sid": sit["sid"], "reasons": reasons}


def league_num_qbs(league):
    positions = league.get("roster_positions", []) or []
    if any(p in ("SUPER_FLEX", "SUPERFLEX") for p in positions):
        return 2
    return max(1, positions.count("QB"))


def format_height(raw):
    """Sleeper stores height as total inches (e.g. '69'). Convert to 5'9"."""
    if not raw:
        return None
    try:
        total_inches = int(raw)
        feet, inches = divmod(total_inches, 12)
        return f"{feet}'{inches}\""
    except (ValueError, TypeError):
        return raw


def compute_age_decimal(birth_date_str):
    """Real decimal age (e.g. 30.2) computed from today's date -- climbs
    automatically as time passes, no manual updates needed."""
    if not birth_date_str:
        return None
    try:
        y, m, d = [int(x) for x in birth_date_str.split("-")]
        days = (date.today() - date(y, m, d)).days
        return round(days / 365.25, 1)
    except Exception:
        return None


def compute_idp_points(stats):
    """Best-effort IDP fantasy points from raw Sleeper defensive stats,
    using commonly-used standard weights (solo tackle=1, assist=0.5,
    sack=2, INT=3, forced fumble=2, fumble recovery=2, pass defended=1,
    defensive TD=6, safety=2). Field names are our best guess from public
    fantasy-dev documentation, not verified live -- if this comes back
    all zeros after deploying, the field names need adjusting.
    Returns None if there's no defensive stat signal at all (so we don't
    misclassify an offensive player with a missing pts_ppr as a zero)."""
    solo = stats.get("idp_tkl_solo") or stats.get("def_tkl_solo") or 0
    ast = stats.get("idp_tkl_ast") or stats.get("def_tkl_ast") or 0
    sack = stats.get("idp_sack") or stats.get("def_sack") or 0
    interception = stats.get("idp_int") or stats.get("def_int") or 0
    ff = stats.get("idp_ff") or stats.get("def_ff") or 0
    fum_rec = stats.get("idp_fum_rec") or stats.get("def_fr") or 0
    pass_def = stats.get("idp_pass_def") or stats.get("def_pass_def") or 0
    td = stats.get("idp_def_td") or stats.get("def_td") or 0
    safety = stats.get("idp_safety") or stats.get("def_safety") or 0

    if not any([solo, ast, sack, interception, ff, fum_rec, pass_def, td, safety]):
        return None

    points = (solo * 1) + (ast * 0.5) + (sack * 2) + (interception * 3) + (ff * 2) + \
             (fum_rec * 2) + (pass_def * 1) + (td * 6) + (safety * 2)
    return round(points, 1)


def fetch_season_stats_from_sleeper(season):
    """The actual Sleeper fetch -- parallel across all 18 weeks. This is
    the slow part; it's only ever called by the scheduled sync job, or
    once as a fallback the very first time a season is requested before
    it's been synced yet (after which it's saved and never live-fetched
    again). Returns {pid: {week: {pts, off_snp, tm_off_snp}}}.

    Confirmed via /api/debug-sleeper that the real response is a LIST of
    entries like {"player_id": "...", "stats": {...}, ...} -- not a dict
    keyed by player_id like earlier code assumed. That mismatch was the
    actual bug causing every sync to silently save 0 rows despite a real
    200 OK response."""
    def fetch_week(week):
        try:
            r = requests.get(
                f"https://api.sleeper.com/stats/nfl/{season}/{week}",
                params={"season_type": "regular"},
                timeout=15,
            )
            if r.status_code != 200:
                return week, None
            data = r.json()
            return week, data if isinstance(data, list) else None
        except Exception:
            return week, None

    weekly = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(fetch_week, w) for w in range(1, 19)]
        for future in as_completed(futures):
            week, entries = future.result()
            if not entries:
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                pid = entry.get("player_id")
                stats = entry.get("stats")
                if not pid or not isinstance(stats, dict):
                    continue
                pts = stats.get("pts_ppr")
                if pts is None:
                    pts = compute_idp_points(stats)
                    if pts is None:
                        continue
                off_snp = stats.get("off_snp")
                tm_off_snp = stats.get("tm_off_snp")
                weekly.setdefault(pid, {})[week] = {
                    "pts": round(pts, 1),
                    "off_snp": off_snp if isinstance(off_snp, (int, float)) else 0,
                    "tm_off_snp": tm_off_snp if isinstance(tm_off_snp, (int, float)) else 0,
                }
    return weekly


# Stat keys worth showing on a performance card, per position group,
# in the order they should read. Sleeper's own naming; the labels are the
# short forms the reference app uses ("138 yds  1 td  4 rec").
_STAT_LINE_FIELDS = {
    "QB": [("pass_yd", "pyds"), ("pass_td", "ptd"), ("rush_td", "rutd"), ("pass_int", "int")],
    "RB": [("rush_yd", "yds"), ("rush_td", "td"), ("rec", "rec"), ("rec_yd", "ryds")],
    "WR": [("rec_yd", "yds"), ("rec_td", "td"), ("rec", "rec")],
    "TE": [("rec_yd", "yds"), ("rec_td", "td"), ("rec", "rec")],
    # A kicker kicks. Without this the line fell through to the
    # defensive fallback below and every kicker on the board read
    # "0 fr" -- nought fumble recoveries -- which is true and useless.
    "K": [("fgm", "fg"), ("xpm", "xp"), ("fgm_lng", "lng"), ("fga", "fga")],
}
# Anything defensive falls back to this, so an IDP performance still
# shows a real line rather than a blank card.
_STAT_LINE_IDP = [("idp_fum_rec", "fr"), ("idp_sack", "sack"), ("idp_tkl_solo", "solo"),
                  ("idp_int", "int"), ("idp_tkl_loss", "tfl")]


def build_stat_line(position, stats, max_items=3):
    """The headline stat line for one performance -- [(value, label), ...]
    ready to render, e.g. [(138, "yds"), (1, "td"), (4, "rec")].

    Picks the fields that matter for the player's position and keeps only
    the ones that actually happened, so a receiver with no touchdown shows
    yards and catches rather than a apologetic '0 td'. A zero IS kept when
    dropping it would leave the line empty, since a blank card reads as
    broken data rather than as a quiet game."""
    stats = stats or {}
    fields = _STAT_LINE_FIELDS.get(position) or _STAT_LINE_IDP
    line = []
    for key, label in fields:
        val = stats.get(key)
        if isinstance(val, (int, float)) and val:
            line.append((round(val, 1) if isinstance(val, float) and val % 1 else int(val), label))
        if len(line) >= max_items:
            break
    if not line:
        for key, label in fields[:1]:
            val = stats.get(key)
            line.append((int(val) if isinstance(val, (int, float)) else 0, label))
    return line


_live_week_stats_cache = {}


def get_live_week_stats(season, week, cache=_live_week_stats_cache, allow_fetch=True):
    """{player_id: {"pts": fantasy points, "stats": raw Sleeper stats}}
    for ONE week, fetched live rather than read from our database.

    This is deliberately not get_season_stats. That one reads player_stats,
    which only changes when the sync job runs -- fine for season-long
    aggregates and history, useless for a leaderboard that is supposed to
    move while games are being played. Sleeper updates this endpoint
    during games, and it already carries pts_ppr, so the live board scores
    identically to every other fantasy figure on the site instead of
    re-deriving points from a second source with its own rounding.

    TTL 45s: fast enough to feel live next to a scoreboard that polls on
    its own, slow enough that a busy Sunday is a couple of requests a
    minute rather than one per visitor. Falls back to the last good
    response if Sleeper hiccups, so a blip empties nothing.

    allow_fetch=False returns only what is already cached (None if
    nothing is), for callers on a path that must never block on a live
    request -- see get_week_performers."""
    season, week = _safe_int(season, int(SEASON)), _safe_int(week, 1)
    key = (season, week)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 45:
        return entry["data"]
    if not allow_fetch:
        # Caller is on a request path that must not block. Serve whatever
        # is cached (even if stale) and let them refresh out of band.
        return entry["data"] if entry else None
    try:
        r = requests.get(
            f"https://api.sleeper.com/stats/nfl/{season}/{week}",
            params={"season_type": "regular"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise ValueError(f"expected a list of stat entries, got {type(data).__name__}")
        out = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            pid, stats = item.get("player_id"), item.get("stats")
            if not pid or not isinstance(stats, dict):
                continue
            pts = stats.get("pts_ppr")
            if pts is None:
                pts = compute_idp_points(stats)
            if pts is None:
                continue
            out[pid] = {"pts": round(pts, 1), "stats": stats}
        cache[key] = {"data": out, "time": now}
        return out
    except Exception:
        return entry["data"] if entry else {}


def sync_season_to_db(season):
    """Fetch a season fresh from Sleeper and permanently save every
    player-week row to the database. This is what the scheduled GitHub
    Actions job calls -- the only place that should be hitting Sleeper's
    stats endpoint live on a regular basis. Returns rows saved."""
    weekly = fetch_season_stats_from_sleeper(season)
    if not weekly or not DATABASE_URL:
        return 0
    conn = get_db()
    rows_saved = 0
    try:
        with conn.cursor() as cur:
            for pid, weeks in weekly.items():
                for week, w in weeks.items():
                    cur.execute(
                        """INSERT INTO player_stats (sleeper_id, season, week, fpts, off_snp, tm_off_snp, updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s, NOW())
                           ON CONFLICT (sleeper_id, season, week)
                           DO UPDATE SET fpts = EXCLUDED.fpts, off_snp = EXCLUDED.off_snp,
                                         tm_off_snp = EXCLUDED.tm_off_snp, updated_at = NOW()""",
                        (pid, int(season), week, w["pts"], w["off_snp"], w["tm_off_snp"]),
                    )
                    rows_saved += 1
        conn.commit()
    finally:
        conn.close()
    return rows_saved


# NOTE: startup-time auto-backfill was removed. Kicking off an 11-season
# fetch the instant the app boots was competing with Render's own
# health check for CPU/memory right when the app most needs to be
# lightweight and responsive -- a likely cause of the app failing to
# start cleanly. All syncing now happens exclusively via the external
# GitHub Actions workflow, which runs on separate infrastructure and
# can't destabilize the app's own startup.

_stats_sync_lock = threading.Lock()
_stats_sync_busy_seasons = set()
# How long to wait before re-running the "is this season complete yet?"
# COUNT query for a season already known to be incomplete.
#
# The seeded-season set short-circuits seasons confirmed COMPLETE, but
# there was nothing for the other case: a season mid-backfill fails the
# check every time, so every caller re-ran the count. With the history
# search now reaching several seasons back, a single page render asks
# about several incomplete seasons, and the answer cannot meaningfully
# change between two calls a few milliseconds apart -- an 18-week sync
# takes far longer than that. Re-probing once a minute per season is
# plenty to notice one finishing.
_SEED_PROBE_COOLDOWN_S = 60
_seed_probe_last = {}


def _seed_probe_due(kind, season, now=None):
    """True if it's worth spending a DB query asking whether `season` is
    fully synced yet. Records the attempt when it returns True."""
    now = time.time() if now is None else now
    key = (kind, season)
    last = _seed_probe_last.get(key)
    if last is not None and now - last < _SEED_PROBE_COOLDOWN_S:
        return False
    _seed_probe_last[key] = now
    return True


_stats_seeded_seasons = set()


def ensure_season_stats_synced(season):
    """Self-heals a season whose player_stats coverage is incomplete --
    the gap matchup grading actually hit: the one-time historical
    backfill (backfill-stats.yml) only ever runs when someone manually
    dispatches it in GitHub Actions, and the recurring 2-hour cron only
    ever syncs "the current season". A season that's already over by the
    time this app starts treating something newer as current -- last
    season, right after a new one kicks off -- has no automatic path to
    ever get synced unless something explicitly asks for it. This is
    that ask, fired the moment anything (the /matchups page, the
    defense-vs-position fallback) actually needs a prior season's stats.

    Counts DISTINCT weeks present, not just "any row at all" -- checking
    for a single row (the previous version of this check) meant a sync
    that died partway through (a transient Sleeper hiccup on a handful
    of weeks, a process recycle on a free-tier host) looked "seeded"
    forever after saving just a few players' worth of one or two weeks,
    which is exactly the shape of gap that made defense-vs-position
    silently empty for most teams despite individual players' own
    season stats existing. Same fix already applied to
    ensure_schedule_synced for the identical failure mode.

    Deliberately a background thread, never a blocking fetch: an earlier
    version of get_season_stats DID block on a live fetch when a season
    was empty, and that was the actual, confirmed cause of the whole site
    timing out (see that function's docstring) -- so this must never
    repeat that mistake no matter how tempting a synchronous "just fetch
    it now" would be here."""
    if season in _stats_seeded_seasons or not DATABASE_URL:
        return
    if not _seed_probe_due("stats", season):
        return
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT week) AS n FROM player_stats WHERE season = %s", (season,))
            weeks_present = (cur.fetchone() or {}).get("n", 0)
    finally:
        conn.close()
    info = get_current_week_info()
    weeks_expected = SCHEDULE_WEEKS_PER_SEASON if season < info["season"] else min(info["week"], SCHEDULE_WEEKS_PER_SEASON)
    if weeks_present >= weeks_expected:
        _stats_seeded_seasons.add(season)
        return
    with _stats_sync_lock:
        if season in _stats_sync_busy_seasons:
            return
        _stats_sync_busy_seasons.add(season)

    def _run():
        try:
            with _BACKGROUND_SYNC_SLOTS:
                sync_season_to_db(season)
        except Exception:
            pass
        finally:
            with _stats_sync_lock:
                _stats_sync_busy_seasons.discard(season)
            # Whatever computed (and cached) an answer from the empty
            # season needs to see the fresh data on the next call, not
            # its own up-to-an-hour-old cached "nothing here" result.
            _defense_vs_position_cache.clear()
            _matchup_grade_cache.clear()

    threading.Thread(target=_run, daemon=True).start()


def _refresh_season_stats_background(season):
    """Re-fetches a season that already has rows, in the background --
    same single-flight-per-season lock as ensure_season_stats_synced
    (the two never need to run at once for the same season: one means
    "empty, backfill it", this one means "has data, keep it current").
    See get_season_stats' call site for why this must never be inline."""
    with _stats_sync_lock:
        if season in _stats_sync_busy_seasons:
            return
        _stats_sync_busy_seasons.add(season)

    def _run():
        try:
            with _BACKGROUND_SYNC_SLOTS:
                sync_season_to_db(season)
        except Exception:
            pass
        finally:
            with _stats_sync_lock:
                _stats_sync_busy_seasons.discard(season)
            _defense_vs_position_cache.clear()
            _matchup_grade_cache.clear()

    threading.Thread(target=_run, daemon=True).start()


def get_season_stats(season, cache={}):
    """player_id -> {games, fpts, weeks, snap_pct} for a season. Reads
    only from our own database -- never calls Sleeper live during a page
    request, no matter what. If a season hasn't been synced yet, this
    just returns empty rather than blocking the page load on a live
    fetch (that blocking fallback was the actual cause of the site
    timing out entirely -- fixed by removing it here)."""
    now = time.time()
    entry = cache.get(season)
    if entry and now - entry["time"] < 600:
        return entry["data"]

    agg = {}
    if DATABASE_URL:
        try:
            conn = get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT sleeper_id, week, fpts, off_snp, tm_off_snp FROM player_stats WHERE season = %s",
                        (int(season),),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()
            for row in rows:
                p_entry = agg.setdefault(row["sleeper_id"], {
                    "games": 0, "fpts": 0.0, "weeks": {}, "off_snp_total": 0, "tm_off_snp_total": 0,
                })
                p_entry["games"] += 1
                p_entry["fpts"] += row["fpts"] or 0
                p_entry["weeks"][row["week"]] = round(row["fpts"] or 0, 1)
                p_entry["off_snp_total"] += row["off_snp"] or 0
                p_entry["tm_off_snp_total"] += row["tm_off_snp"] or 0
        except Exception:
            agg = {}
        if agg:
            # Keep an already-synced season current (this week's new
            # stats, updated fpts) -- but NEVER inline/blocking. This
            # used to call sync_season_to_db() directly here, which is a
            # full live Sleeper refetch (an 18-week ThreadPoolExecutor
            # fetch) run synchronously in the middle of a page request,
            # every single time this season's 600s cache expired --
            # directly contradicting this function's own "never calls
            # Sleeper live during a page request" docstring above, and
            # the actual cause of pages going slow once matchup grading
            # started querying several seasons (current, last year, and
            # now up to a few more for head-to-head history) in one
            # request: every one of those was a potential multi-second
            # live refetch stacked in sequence.
            _refresh_season_stats_background(season)
        else:
            # Zero rows means this season has genuinely never been synced
            # (not "just hasn't updated in a while" -- that's the branch
            # above) -- self-heal it in the background rather than
            # leaving it silently, permanently empty forever.
            ensure_season_stats_synced(season)

    for p_entry in agg.values():
        tm_total = p_entry["tm_off_snp_total"]
        p_entry["snap_pct"] = round(100 * p_entry["off_snp_total"] / tm_total, 1) if tm_total else None

    cache[season] = {"data": agg, "time": now}
    return agg


def get_season_finish_ranks(season, cache={}):
    """{sid: {'overall': N, 'position': N}} -- where a player actually
    finished that season, ranked by real total fantasy points against
    everyone else who played, both overall and within their own
    position. This is a real 'how they performed that year' ranking,
    different from dynasty value (which is forward-looking). Cached 1hr
    since it's a full-league computation, not a single-player lookup."""
    now = time.time()
    entry = cache.get(season)
    if entry and now - entry["time"] < 3600:
        return entry["data"]

    ranks = {}
    if DATABASE_URL:
        try:
            conn = get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT sleeper_id, SUM(fpts) AS total_fpts FROM player_stats WHERE season = %s GROUP BY sleeper_id",
                        (int(season),),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()

            all_players = get_all_players()
            totals = []
            for row in rows:
                p = all_players.get(row["sleeper_id"])
                if not p or p.get("position") not in POSITIONS:
                    continue
                totals.append((row["sleeper_id"], p["position"], row["total_fpts"] or 0))

            totals.sort(key=lambda x: -x[2])
            for i, (sid, position, _) in enumerate(totals):
                ranks.setdefault(sid, {})["overall"] = i + 1

            for pos in POSITIONS:
                pos_totals = sorted([t for t in totals if t[1] == pos], key=lambda x: -x[2])
                for i, (sid, _, _) in enumerate(pos_totals):
                    ranks.setdefault(sid, {})["position"] = i + 1
        except Exception:
            ranks = {}

    cache[season] = {"data": ranks, "time": now}
    return ranks


def rank_tier(rank):
    if rank is None:
        return "flat"
    if rank <= 12:
        return "good"
    if rank <= 36:
        return "warning"
    return "critical"


def team_power_tier(total_value, mean_value, stdev_value):
    """Flock-style label based on how far this team's total dynasty value
    sits from its own league's average, in standard deviations -- not a
    fixed rank-percentage cutoff. A rank-based cutoff (e.g. "top 15% of
    teams") forces the same tier shape onto every league regardless of
    whether teams are actually bunched together or spread out -- in any
    10-team league it always hands out exactly 2 Juggernauts and 2
    Purgatory teams, even when the 2nd-ranked team is nearly tied with the
    5th. This reflects the real spread instead: a league where everyone's
    close in value can end up almost entirely "Balanced" with nobody in
    the extreme tiers, and a league with one dominant roster can have
    exactly one real Juggernaut and nobody else close."""
    if stdev_value <= 0:
        return "Balanced", "tier-balanced"
    z = (total_value - mean_value) / stdev_value
    if z >= 1.0:
        return "Juggernaut", "tier-juggernaut"
    if z >= 0.35:
        return "Strong Contender", "tier-contender"
    if z >= -0.35:
        return "Balanced", "tier-balanced"
    if z >= -1.0:
        return "Strong Rebuilder", "tier-rebuilder"
    return "Purgatory", "tier-purgatory"

# ---------------- Core data builders ----------------

def roster_positions(roster, all_players):
    by_pos = {}
    for pid in roster.get("players") or []:
        p = all_players.get(pid)
        if not p:
            continue
        pos = p.get("position", "?")
        name = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        by_pos.setdefault(pos, []).append((pid, name))
    return by_pos


def quick_add_list(team, fc_players):
    """Flatten a team's roster into value-sorted chips for the trade calculator."""
    items = []
    for pos in POSITIONS:
        for sid, name in team["positions"].get(pos, []):
            v = fc_players.get(sid, {}).get("value", 0)
            items.append({"sid": sid, "name": name, "position": pos, "team": "", "photo": player_photo_url(sid), "value": v})
    items.sort(key=lambda x: -x["value"])
    return items


def sleeper_avatar_url(avatar_id):
    return f"https://sleepercdn.com/avatars/thumbs/{avatar_id}" if avatar_id else None


def build_league_teams(league_id, league, all_players, league_users, user_id):
    with ThreadPoolExecutor(max_workers=2) as executor:
        fc_future = executor.submit(get_fantasycalc_values, league_num_qbs(league))
        rosters_future = executor.submit(get_rosters, league_id)
        fc = fc_future.result()
        rosters = rosters_future.result()
    fc_players = fc["players"]

    team_infos = []
    for r in rosters:
        settings = r.get("settings", {})
        positions = roster_positions(r, all_players)
        pos_value = {}
        for pos in POSITIONS:
            pos_value[pos] = sum(fc_players.get(pid, {}).get("value", 0) for pid, _ in positions.get(pos, []))
        owner = league_users.get(r.get("owner_id"), {})
        team_infos.append({
            "roster_id": r["roster_id"],
            "owner_name": owner.get("name", "Unknown"),
            "avatar_url": owner.get("avatar_url"),
            "wins": settings.get("wins", 0), "losses": settings.get("losses", 0),
            "is_you": r.get("owner_id") == user_id,
            "positions": positions,
            "pos_value": pos_value,
        })

    n_teams = len(team_infos)
    for pos in POSITIONS:
        ordered = sorted(team_infos, key=lambda t: -t["pos_value"][pos])
        for i, t in enumerate(ordered):
            t.setdefault("pos_rank", {})[pos] = i + 1

    for t in team_infos:
        total_val = sum(t["pos_value"].values()) or 1
        bar = []
        for pos in POSITIONS:
            rank = t["pos_rank"][pos]
            pct = round(100 * t["pos_value"][pos] / total_val, 1)
            # 1.0 for the #1 team at that position, fading toward ~0.15 for last place
            intensity = round(1 - (rank - 1) / max(n_teams - 1, 1), 2)
            bar.append((pos, pct, rank, intensity))
        t["bar"] = bar

    team_infos.sort(key=lambda t: (-t["wins"], -sum(t["pos_value"].values())))
    totals = [sum(t["pos_value"].values()) for t in team_infos]
    mean_value = sum(totals) / len(totals) if totals else 0
    stdev_value = (sum((v - mean_value) ** 2 for v in totals) / len(totals)) ** 0.5 if totals else 0
    for t in team_infos:
        t["power_tier"], t["power_tier_class"] = team_power_tier(sum(t["pos_value"].values()), mean_value, stdev_value)
    return team_infos


def get_leagues_brief(username):
    """Fast, lightweight league list for the sync picker -- just names and
    avatars from the single already-cached /leagues call, with none of the
    per-league roster/value building build_leagues_for_user does."""
    user_id, display_name = get_user_id(username)
    leagues = get_leagues(user_id, SEASON)
    brief = [{
        "league_id": lg["league_id"],
        "name": lg.get("name", "Unnamed League"),
        "avatar_url": sleeper_avatar_url(lg.get("avatar")),
        "total_rosters": lg.get("total_rosters", 0),
    } for lg in leagues]
    return user_id, display_name, brief


def _build_one_league(league, all_players, user_id):
    league_users = {
        u["user_id"]: {"name": u.get("display_name", "?"), "avatar_url": sleeper_avatar_url(u.get("avatar"))}
        for u in get_league_users(league["league_id"])
    }
    teams = build_league_teams(league["league_id"], league, all_players, league_users, user_id)
    return {
        "league_id": league["league_id"],
        "league_name": league.get("name", "Unnamed League"),
        "num_qbs": league_num_qbs(league),
        "teams": teams,
        "my_team": next((t for t in teams if t["is_you"]), None),
    }


def build_leagues_for_user(username, league_ids=None, cache={}):
    """league_ids, when given, restricts building to just that subset --
    the whole point of the sync picker is to skip fetching and scoring
    leagues the user didn't ask to track, which is also what keeps this
    fast for anyone in a lot of leagues."""
    key = (username.lower(), tuple(sorted(league_ids)) if league_ids is not None else None)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 600:
        return entry["data"]

    all_players = get_all_players()
    user_id, display_name = get_user_id(username)
    leagues = get_leagues(user_id, SEASON)
    if league_ids is not None:
        leagues = [lg for lg in leagues if lg["league_id"] in league_ids]

    # Each league's build is an independent round-trip to Sleeper/FantasyCalc,
    # so building them in parallel turns the wall-clock cost from "sum of every
    # league" into "the slowest single league" -- the main lever on reload
    # speed once the picker has already cut the list down to what's synced.
    result = []
    if leagues:
        with ThreadPoolExecutor(max_workers=min(8, len(leagues))) as executor:
            futures = [executor.submit(_build_one_league, league, all_players, user_id) for league in leagues]
            result = [f.result() for f in futures]

    data = {"display_name": display_name, "user_id": user_id, "leagues": result}
    cache[key] = {"data": data, "time": now}
    return data


def get_my_players_by_team(username, league_ids):
    """{"team_abbr": [{"sid","name","position","leagues":[...]}, ...]} for
    every DISTINCT player on the account's own roster, across every
    synced league, grouped by the NFL team they play for -- this is what
    lets /scores show "N of your players" on a game card. Deduped by
    (team, sid): the same real player rostered in more than one synced
    league (common when several dynasty leagues share a player pool)
    counts once, with every league it's rostered in recorded on that one
    entry -- otherwise the count and any rendered name list would repeat
    the same player once per league, which is exactly the "duplicate
    names" bug this replaced. Reuses build_leagues_for_user's existing
    per-league cache, so this is free if League Manager has already
    rendered for this account/selection."""
    if not league_ids:
        return {}
    data = build_leagues_for_user(username, league_ids=set(league_ids))
    all_players = get_all_players()
    by_team = {}
    seen = {}  # (team, sid) -> the entry already added to by_team, so a
               # repeat sighting in another league just appends there
    for lg in data["leagues"]:
        me = next((t for t in lg["teams"] if t["is_you"]), None)
        if not me:
            continue
        for pos in POSITIONS:
            for sid, name in me["positions"].get(pos, []):
                team = (all_players.get(sid) or {}).get("team")
                if not team:
                    continue
                key = (team, sid)
                existing = seen.get(key)
                if existing:
                    if lg["league_name"] not in existing["leagues"]:
                        existing["leagues"].append(lg["league_name"])
                    continue
                entry = {"sid": sid, "name": name, "position": pos, "leagues": [lg["league_name"]]}
                seen[key] = entry
                by_team.setdefault(team, []).append(entry)
    return by_team


def build_league_detail(league_id, username, roster_id=None):
    all_players = get_all_players()
    user_id, display_name = get_user_id(username)
    leagues = get_leagues(user_id, SEASON)
    league = next((l for l in leagues if l["league_id"] == league_id), None)
    if league is None:
        league = {"league_id": league_id, "name": "League", "roster_positions": []}

    league_users = {
        u["user_id"]: {"name": u.get("display_name", "?"), "avatar_url": sleeper_avatar_url(u.get("avatar"))}
        for u in get_league_users(league_id)
    }
    teams = build_league_teams(league_id, league, all_players, league_users, user_id)
    if not teams:
        raise ValueError("No teams found in this league.")

    if roster_id is None:
        # No specific manager picked -- this is the "View League" landing
        # page, so show the full standings/rankings list (every team's tier
        # and value bar) rather than defaulting to any one roster.
        ranked = [{
            "roster_id": t["roster_id"], "owner_name": t["owner_name"], "avatar_url": t["avatar_url"],
            "is_you": t["is_you"], "wins": t["wins"], "losses": t["losses"],
            "power_tier": t["power_tier"], "power_tier_class": t["power_tier_class"], "bar": t["bar"],
        } for t in teams]
        return {"mode": "rankings", "league_name": league.get("name", "League"), "teams": ranked}

    target = next((t for t in teams if t["roster_id"] == roster_id), None)
    if target is None:
        target = next((t for t in teams if t["is_you"]), None) or teams[0]

    num_qbs = league_num_qbs(league)
    fc_players = get_fantasycalc_values(num_qbs)["players"]
    columns = {}
    for pos in POSITIONS:
        players = []
        for pid, name in target["positions"].get(pos, []):
            v = fc_players.get(pid, {})
            players.append({
                "sleeper_id": pid, "name": name, "photo": player_photo_url(pid),
                "position_rank": v.get("position_rank"),
                "overall_rank": v.get("overall_rank"),
                "value": v.get("value", 0),
                "tier": rank_tier(v.get("overall_rank")),
            })
        players.sort(key=lambda p: -p["value"])
        columns[pos] = {"players": players, "team_rank": target["pos_rank"][pos]}

    team_switcher = sorted(
        [{"roster_id": t["roster_id"], "owner_name": t["owner_name"], "is_you": t["is_you"]} for t in teams],
        key=lambda t: t["owner_name"].lower(),
    )

    return {
        "mode": "roster", "league_name": league.get("name", "League"), "owner_name": target["owner_name"],
        "roster_id": target["roster_id"], "columns": columns, "num_qbs": num_qbs, "teams": team_switcher,
    }


def build_context_text(username):
    data = build_leagues_for_user(username)
    lines = []
    for lg in data["leagues"]:
        me = next((t for t in lg["teams"] if t["is_you"]), None)
        if not me:
            continue
        lines.append(f"League '{lg['league_name']}' ({me['wins']}-{me['losses']})")
    return "\n".join(lines)

# ---------------- Gemini ----------------

def ask_gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    resp = requests.post(
        url,
        headers={"x-goog-api-key": GEMINI_API_KEY, "content-type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

# ---------------- Public pages ----------------

@app.route("/api/check-username")
def api_check_username():
    u = request.args.get("u", "").strip()
    if not username_valid(u):
        return jsonify({"available": False, "reason": "invalid"})
    if not username_available(u):
        return jsonify({"available": False, "reason": "taken"})
    return jsonify({"available": True, "reason": None})


@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        username = request.form.get("username", "").strip()
        referral_code = request.form.get("referral_code", "").strip() or None
        newsletter = "newsletter" in request.form
        agreed_tos = "agree_tos" in request.form

        if not email or "@" not in email:
            error = "Enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif not username_valid(username):
            error = "Username must be 1-20 letters, numbers, or underscores."
        elif not agreed_tos:
            error = "You must agree to the Terms of Service and Privacy Policy."

        if not error:
            conn = None
            try:
                conn = get_db()
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO users (email, password_hash, username, referral_code, newsletter_opt_in)
                           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                        (email, generate_password_hash(password), username, referral_code, newsletter),
                    )
                    new_id = cur.fetchone()["id"]
                conn.commit()
                login_user(User({"id": new_id, "email": email, "username": username, "is_member": False}), remember=True)
                return redirect("/")
            except psycopg2.errors.UniqueViolation:
                if conn:
                    conn.rollback()
                error = "That username or email is already taken."
            except Exception as e:
                if conn:
                    conn.rollback()
                error = f"Something went wrong creating your account: {e}"
            finally:
                if conn:
                    conn.close()

    return render_template_string(SIGNUP_HTML, error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        identifier = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        try:
            conn = get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM users WHERE email = %s OR lower(username) = %s",
                        (identifier, identifier),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()
        except Exception as e:
            row = None
            error = f"Couldn't reach the login system: {e}"

        if not error:
            if row and row["password_hash"] and check_password_hash(row["password_hash"], password):
                login_user(User(row), remember=True)
                return redirect("/")
            error = "Incorrect email/username or password."
    return render_template_string(LOGIN_PAGE_HTML, error=error)


@app.route("/logout")
def logout():
    logout_user()
    return redirect("/")


@app.route("/auth/google/login")
def google_login():
    if not os.environ.get("GOOGLE_CLIENT_ID") or not os.environ.get("GOOGLE_CLIENT_SECRET"):
        return render_template_string(
            LOGIN_PAGE_HTML,
            error="Google Sign-In isn't configured on the server yet (missing GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET). Use email/password for now.",
        )
    redirect_uri = url_for("google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def google_callback():
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo") or oauth.google.userinfo()
    email = (userinfo.get("email") or "").lower()
    sub = userinfo.get("sub")

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE oauth_provider = 'google' AND oauth_sub = %s", (sub,))
            row = cur.fetchone()
            if not row and email:
                # Link to an existing email/password account instead of duplicating
                cur.execute("SELECT * FROM users WHERE email = %s", (email,))
                row = cur.fetchone()
                if row:
                    cur.execute(
                        "UPDATE users SET oauth_provider='google', oauth_sub=%s WHERE id=%s",
                        (sub, row["id"]),
                    )
                    conn.commit()
            if not row:
                base_username = re.sub(r"[^A-Za-z0-9_]", "", email.split("@")[0])[:15] or "user"
                username = base_username
                suffix = 1
                while not username_available(username):
                    suffix += 1
                    username = f"{base_username}{suffix}"
                cur.execute(
                    """INSERT INTO users (email, username, oauth_provider, oauth_sub)
                       VALUES (%s, %s, 'google', %s) RETURNING *""",
                    (email, username, sub),
                )
                row = cur.fetchone()
                conn.commit()
    finally:
        conn.close()

    login_user(User(row), remember=True)
    return redirect("/")


def build_portfolio_summary(data, num_qbs=1):
    fc_players = get_fantasycalc_values(num_qbs)["players"]
    pos_totals = {p: 0 for p in POSITIONS}
    exposure = {}
    leagues_count = 0

    for lg in data["leagues"]:
        me = next((t for t in lg["teams"] if t["is_you"]), None)
        if not me:
            continue
        leagues_count += 1
        for pos in POSITIONS:
            for sid, name in me["positions"].get(pos, []):
                val = fc_players.get(sid, {}).get("value", 0)
                pos_totals[pos] += val
                entry = exposure.setdefault(sid, {
                    "sid": sid, "name": name, "position": pos, "shares": 0,
                    "value": val, "photo": player_photo_url(sid),
                })
                entry["shares"] += 1

    total_val = sum(pos_totals.values()) or 1
    pos_pct = [(p, round(100 * pos_totals[p] / total_val, 1)) for p in POSITIONS]
    exposure_list = sorted(exposure.values(), key=lambda x: -x["value"])
    return {"leagues_count": leagues_count, "pos_pct": pos_pct, "exposure": exposure_list[:75]}


@app.route("/league-manager")
def leagues_page():
    username = request.args.get("u", "").strip()
    fmt = request.args.get("fmt", "1qb")
    manage = request.args.get("manage") == "1"
    chosen_param = request.args.getlist("leagues")
    error = None
    data = None
    portfolio = None
    used_saved = False
    picker = None

    if not username and current_user.is_authenticated and current_user.sleeper_username:
        username = current_user.sleeper_username
        used_saved = True

    if username:
        try:
            user_id, display_name, brief = get_leagues_brief(username)

            if current_user.is_authenticated and username != (current_user.sleeper_username or ""):
                conn = get_db()
                try:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE users SET sleeper_username = %s WHERE id = %s", (username, current_user.id))
                    conn.commit()
                    current_user.sleeper_username = username
                finally:
                    conn.close()

            saved_ids = get_synced_league_ids(current_user.id) if current_user.is_authenticated else None

            if manage:
                # Reopen the picker to add/remove leagues -- preselect whatever
                # is currently synced (or currently on-screen, for a guest who
                # has no saved state) rather than starting from scratch.
                preselected = set(chosen_param) if chosen_param else (saved_ids or set())
                picker = {"leagues": brief, "display_name": display_name, "preselected": preselected, "default_all": False}
            elif chosen_param:
                valid_ids = {lg["league_id"] for lg in brief}
                chosen_ids = [lid for lid in chosen_param if lid in valid_ids]
                if current_user.is_authenticated:
                    set_synced_league_ids(current_user.id, chosen_ids)
                if not chosen_ids:
                    error = "Pick at least one league to sync."
                    picker = {"leagues": brief, "display_name": display_name, "preselected": set(), "default_all": False}
                else:
                    data = build_leagues_for_user(username, league_ids=set(chosen_ids))
                    portfolio = build_portfolio_summary(data, num_qbs=2 if fmt == "superflex" else 1)
            elif saved_ids is not None:
                data = build_leagues_for_user(username, league_ids=saved_ids)
                portfolio = build_portfolio_summary(data, num_qbs=2 if fmt == "superflex" else 1)
            else:
                # First time we've seen this username with no saved selection --
                # ask which leagues to sync instead of building every one of
                # them up front.
                picker = {"leagues": brief, "display_name": display_name, "preselected": set(), "default_all": True}
        except Exception as e:
            error = str(e)

    return render_template_string(
        HOME_HTML, username=username, data=data, error=error, portfolio=portfolio, fmt=fmt,
        used_saved=used_saved, picker=picker,
    )


@app.route("/league")
def league_detail():
    league_id = request.args.get("league_id", "")
    username = request.args.get("u", "")
    roster_id = request.args.get("roster_id", type=int)
    try:
        detail = build_league_detail(league_id, username, roster_id)
        # Matchup grades are an auth-gated perk (eventually a paid one, once
        # billing exists -- see /matchups) computed here in the route rather
        # than inside build_league_detail, which stays pure/auth-unaware so
        # it's reusable wherever a roster needs building regardless of who's
        # asking. Every grade lookup below hits already-warm caches, so this
        # adds no new I/O for a signed-in visitor.
        if detail.get("mode") == "roster" and current_user.is_authenticated:
            info = get_current_week_info()
            ensure_schedule_synced(info["season"])
            for col in detail["columns"].values():
                for p in col["players"]:
                    grade = compute_matchup_grade(p["sleeper_id"], info["season"], info["week"])
                    p["grade"] = grade["grade"] if grade else None
                    p["grade_class"] = grade["grade_class"] if grade else None
        return render_template_string(LEAGUE_DETAIL_HTML, detail=detail, username=username, league_id=league_id)
    except Exception as e:
        return f"Error: {e}", 500


@app.route("/player")
def player_detail():
    sid = request.args.get("sid", "")
    num_qbs = request.args.get("numqbs", default=1, type=int)
    username = request.args.get("u", "")
    tab = request.args.get("tab", "general")
    ref = request.args.get("ref", "/rankings")
    all_players = get_all_players()
    p = all_players.get(sid)
    if not p:
        return "Player not found", 404
    fc_players = get_fantasycalc_values(num_qbs)["players"]
    v = fc_players.get(sid, {})
    full_name = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
    adp_data = get_adp_data()

    current_season = int(SEASON)
    # Real career range, derived from years of NFL experience -- capped at
    # 6 seasons. With the per-season week-fetch now parallelized, 6 cold
    # seasons still means ~6 concurrent bursts of 18 requests; much more
    # than that was timing out the page entirely on Render's free tier.
    MAX_CAREER_SEASONS = 6
    years_exp = p.get("years_exp")
    if years_exp is None:
        years_exp = 4
    years_exp = max(0, min(years_exp, MAX_CAREER_SEASONS - 1))
    rookie_season = current_season - years_exp
    available_seasons = list(range(rookie_season, current_season + 1))

    season = request.args.get("season", type=int, default=current_season)
    if season not in available_seasons:
        season = current_season
    season_idx = available_seasons.index(season)
    prev_season = available_seasons[season_idx - 1] if season_idx > 0 else None
    next_season = available_seasons[season_idx + 1] if season_idx < len(available_seasons) - 1 else None

    # Only fetch what the current tab actually needs -- General only ever
    # needs the current season (for the PPG tile, shown on every tab).
    # Log needs whichever specific season is being viewed. Career is the
    # only tab that genuinely needs the full multi-season range. This is
    # the fix for player pages being slow to open from Rankings: every
    # click used to fetch all 6 career seasons regardless of which tab
    # you'd land on, even though General (the default) only needed 1.
    seasons_to_fetch = {current_season}
    if tab == "log":
        seasons_to_fetch.add(season)
    if tab == "career":
        seasons_to_fetch.update(available_seasons)

    season_data = {}
    with ThreadPoolExecutor(max_workers=max(len(seasons_to_fetch), 1)) as executor:
        future_map = {executor.submit(get_season_stats, str(yr)): yr for yr in seasons_to_fetch}
        for future in as_completed(future_map):
            season_data[future_map[future]] = future.result()

    this_season_stats = season_data[season].get(sid, {"games": 0, "fpts": 0.0, "weeks": {}})
    weekly = [{"week": w, "pts": this_season_stats.get("weeks", {}).get(w, 0)} for w in range(1, 19)]
    max_pts = max([w["pts"] for w in weekly] + [1])
    for w in weekly:
        w["pct"] = round(100 * w["pts"] / max_pts, 1) if max_pts else 0

    career_rows = []
    for yr in available_seasons:
        s = season_data.get(yr, {}).get(sid, {"games": 0, "fpts": 0.0})
        finish = get_season_finish_ranks(yr).get(sid, {}) if tab == "career" and s.get("games") else {}
        career_rows.append({
            "season": yr, "games": s.get("games", 0),
            "fpts": round(s.get("fpts", 0), 1) if s.get("games") else None,
            "fpts_per_game": round(s.get("fpts", 0) / s["games"], 1) if s.get("games") else None,
            "overall_finish": finish.get("overall"),
            "position_finish": finish.get("position"),
        })

    current_ppg = round(this_season_stats["fpts"] / this_season_stats["games"], 1) if this_season_stats.get("games") else None

    adp_key = (full_name.lower(), (p.get("position") or "").upper())
    adp = (
        adp_data["by_key"].get(adp_key)
        or adp_data["by_name_only"].get(full_name.lower())
        or adp_data["by_normalized"].get(normalize_name(full_name))
    )

    info = {
        "name": full_name, "photo": player_photo_url(sid),
        "position": p.get("position", "?"), "team": p.get("team") or "Free agent",
        "age": compute_age_decimal(p.get("birth_date")) or p.get("age"), "years_exp": p.get("years_exp"), "college": p.get("college"),
        "height": format_height(p.get("height")), "weight": p.get("weight"),
        "status": p.get("status"), "injury": _injury_badge(p),
        "value": v.get("value"), "position_rank": v.get("position_rank"), "overall_rank": v.get("overall_rank"),
        "redraft_value": v.get("redraft_value"), "tier": rank_tier(v.get("overall_rank")),
        "adp": adp, "ppg": current_ppg,
        "community": get_community_score(sid),
    }
    raw_team = p.get("team")
    depth_chart = get_team_depth_chart(raw_team, all_players)
    news = get_player_news(full_name, raw_team)
    return render_template_string(
        PLAYER_HTML, p=info, username=username, sid=sid, num_qbs=num_qbs, tab=tab, ref=ref,
        season=season, prev_season=prev_season, next_season=next_season,
        weekly=weekly, career_rows=career_rows,
        depth_chart=depth_chart, news=news,
        news_source_url=NEWS_SOURCE_URL,
    )


@app.route("/api/player-search")
def api_player_search():
    q = request.args.get("q", "").strip()
    fmt = request.args.get("format", "1qb")
    mode = request.args.get("mode", "dynasty")
    teams = request.args.get("teams", default=12, type=int)
    if teams not in (8, 10, 12, 14):
        teams = 12
    if not q:
        return jsonify({"results": []})

    num_qbs = 2 if fmt == "superflex" else 1
    is_dynasty = mode != "redraft"
    fc = get_fantasycalc_values(num_qbs, is_dynasty, teams)
    all_players = get_all_players()
    q_low = q.lower()
    # Pick labels look like "2026 Mid 1st" -- they never contain the words
    # people actually type when looking for one. Treat a generic
    # "pick"/"draft" query as "show me picks" (for someone who doesn't
    # know their exact draft position yet), and "1.02"-style round.slot
    # shorthand as "give me that exact slot" (for someone who does),
    # synthesizing a value FantasyCalc doesn't publish directly.
    q_is_generic_pick = q_low in ("pick", "picks", "draft", "draft pick", "draft picks")
    slot_query = None if q_is_generic_pick else parse_pick_slot_query(q_low)
    is_pick_query = q_is_generic_pick or slot_query is not None

    results = []
    for sid, p in all_players.items():
        if p.get("position") not in POSITIONS:
            continue
        full = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        if q_low in full.lower():
            results.append({
                "sid": sid, "name": full, "position": p.get("position"), "team": p.get("team") or "FA",
                "photo": player_photo_url(sid), "value": fc["players"].get(sid, {}).get("value", 0),
            })

    if is_dynasty:
        if q_is_generic_pick:
            matched_picks = sorted(fc["picks"].values(), key=pick_sort_key)
            for pk in matched_picks:
                results.append({
                    "sid": pk["sid"], "name": pk["name"], "position": "PICK", "team": "",
                    "photo": PICK_ICON, "value": pk["value"],
                })
        elif slot_query:
            rnd, slot = slot_query
            for pk in exact_slot_picks(fc["picks"], teams, rnd, slot):
                results.append({
                    "sid": pk["sid"], "name": pk["name"], "position": "PICK", "team": "",
                    "photo": PICK_ICON, "value": pk["value"],
                })
        else:
            for pk in fc["picks"].values():
                if q_low in pk["name"].lower():
                    results.append({
                        "sid": pk["sid"], "name": pk["name"], "position": "PICK", "team": "",
                        "photo": PICK_ICON, "value": pk["value"],
                    })

    if is_pick_query:
        # Chronological order from above (year -> round -> tier), not
        # relevance/value -- that's the whole point of "give me picks in
        # order" instead of highest-value-first.
        return jsonify({"results": results[:30]})

    results.sort(key=lambda r: (not r["name"].lower().startswith(q_low), -(r["value"] or 0), r["name"]))
    return jsonify({"results": results[:10]})


def _resolve_scores_username():
    """The username to cross-reference for "your players in this game" --
    an explicit ?u= wins, otherwise a signed-in account's saved Sleeper
    username (same auto-load convention League Manager already uses)."""
    username = request.args.get("u", "").strip()
    if not username and current_user.is_authenticated and current_user.sleeper_username:
        username = current_user.sleeper_username
    return username


def _annotate_my_players(games, username):
    """Mutates each game card in place, adding my_home_players/
    my_away_players -- only does real work (and only for a signed-in
    account with leagues already synced on League Manager) so a
    request with no username attached costs nothing extra."""
    if not username or not current_user.is_authenticated:
        return
    league_ids = get_synced_league_ids(current_user.id)
    if not league_ids:
        return
    try:
        by_team = get_my_players_by_team(username, league_ids)
    except Exception:
        return
    for g in games:
        g["my_home_players"] = by_team.get(g["home"]["abbr"], [])
        g["my_away_players"] = by_team.get(g["away"]["abbr"], [])


def _my_players_for_game(detail, username):
    """Same lookup as _annotate_my_players but shaped for a single game's
    detail dict ({"home": [...], "away": [...]} or None) -- used by the
    /game page's "Your Players In This Game" panel."""
    if not username or not current_user.is_authenticated:
        return None
    league_ids = get_synced_league_ids(current_user.id)
    if not league_ids:
        return None
    try:
        by_team = get_my_players_by_team(username, league_ids)
    except Exception:
        return None
    home_abbr = detail.get("home", {}).get("abbr")
    away_abbr = detail.get("away", {}).get("abbr")
    home_players = by_team.get(home_abbr, []) if home_abbr else []
    away_players = by_team.get(away_abbr, []) if away_abbr else []
    if not home_players and not away_players:
        return None
    return {"home": home_players, "away": away_players}


# How many players per team the /scores PREVIEW row carries. That row
# shows a top 10 and links to the full board, so it only needs enough per
# team that whichever single day the visitor selects still has ten real
# names to draw from -- not the whole slate, which would mean baking
# hundreds of rows into a page that displays ten of them.
#
# The full /performances board passes per_team=None and scores everyone
# who played. This cap is a payload budget for one preview, never a limit
# on who gets rated.
PERFORMERS_PER_TEAM = 5
_week_performers_cache = {}


def get_week_performers(season, week, cache=_week_performers_cache, allow_fetch=True,
                        per_team=PERFORMERS_PER_TEAM):
    """The best fantasy performances of one week, as display-ready rows.

    Returns a list of dicts sorted best-first, each carrying the player,
    their team and opponent, the fantasy points, the headline stat line,
    and a full performance grade (see grade_performance).

    Built from get_live_week_stats rather than the database, so the board
    moves while games are being played instead of waiting for the next
    sync. Keyed by team so the /scores page can filter the week down to
    the teams that played on whichever date the visitor has selected --
    that filtering happens on the client on purpose, because the visitor's
    browser is the only place that knows their real local calendar day
    (ESPN's kickoff times are UTC, so a Sunday night game is already
    Monday in UTC for anyone west of the UK).

    TTL 45s, matching the live stats fetch underneath it -- no point
    caching the derived board longer than its own input.

    allow_fetch=False makes this cache-only. /scores renders with it off
    on purpose: the board is worth baking in when it's already warm (no
    second request, no flash of empty content), but it is never worth
    holding up the whole scoreboard behind a live Sleeper call. This app
    has already shipped that exact bug once, in get_season_stats, and it
    took the site down -- the page always renders first."""
    season, week = _safe_int(season, int(SEASON)), _safe_int(week, 1)
    key = (season, week, per_team)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 45:
        return entry["data"]

    live = get_live_week_stats(season, week, allow_fetch=allow_fetch)
    if not live:
        # Keep serving the last good board rather than blanking the
        # section on one failed upstream call -- or, when this is a
        # non-blocking caller and nothing is cached yet, an empty board
        # the client will fill in for itself a moment later.
        return entry["data"] if entry else []

    all_players = get_all_players()
    by_team = {}
    for sid, rec in live.items():
        p = all_players.get(sid)
        if not p:
            continue
        pos = p.get("position")
        team = p.get("team")
        if not pos or pos not in SCORED_POSITIONS or not team:
            continue
        by_team.setdefault(team, []).append((rec["pts"], sid, p, rec))

    rows = []
    for team, entries in by_team.items():
        # Ranked within the team by raw points only to decide WHO makes a
        # capped preview -- the board itself is then ordered by score
        # below. Points are the right cut for "this team's most notable
        # days"; score is the right order for "best performances", and
        # conflating the two is what put a 20-point quarterback above a
        # 15-point tight end who had the better game.
        entries.sort(key=lambda e: -e[0])
        sched = get_schedule_for_team_week(season, week, team)
        opponent = (sched or {}).get("opponent")
        is_home = (sched or {}).get("home")
        for pts, sid, p, rec in (entries if per_team is None else entries[:per_team]):
            pos = p["position"]
            rows.append({
                "sid": sid,
                "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                "position": pos,
                "team": team,
                "opponent": opponent,
                # "@ IND" vs "vs TB", the way the reference board reads.
                "vs_label": (None if not opponent else
                             (f"vs {opponent}" if is_home else f"@ {opponent}")),
                "photo": player_photo_url(sid),
                "fpts": pts,
                "stat_line": build_stat_line(pos, rec.get("stats")),
                "grade": grade_performance(pos, pts, season),
            })

    # Ordered by the SCORE, not by raw points. That's the whole point of
    # scoring positionally: 15 points is a better game for a tight end
    # than 20 is for a quarterback, and a board that claims to rank
    # performances has to say so. Raw points break exact ties.
    rows.sort(key=lambda r: (-r["grade"]["score"], -r["fpts"]))
    cache[key] = {"data": rows, "time": now}
    return rows


# The full stat grid on a performance page, per position group. Every
# entry is (Sleeper stat key, short label). Only keys actually present in
# the payload are rendered, so this can list more than any one game will
# ever fill without producing a grid full of blanks -- and a stat Sleeper
# renames or stops sending simply disappears instead of erroring.
_STAT_GRID_FIELDS = {
    "QB": [
        ("pass_yd", "PYDS"), ("pass_td", "PTD"), ("pass_int", "INT"),
        ("pass_cmp", "CMP"), ("pass_att", "ATT"), ("pass_lng", "LNG"),
        ("pass_sack", "SACK"), ("rush_att", "CAR"), ("rush_yd", "RUYDS"),
        ("rush_td", "RUTD"), ("fum_lost", "FL"),
    ],
    "RB": [
        ("rush_yd", "RUYDS"), ("rush_td", "RUTD"), ("rush_att", "CAR"),
        ("rush_ypa", "YPC"), ("rush_lng", "LNG"), ("rec", "REC"),
        ("rec_yd", "REYDS"), ("rec_td", "RETD"), ("rec_tgt", "TGT"),
        ("fum_lost", "FL"),
    ],
    "WR": [
        ("rec_yd", "REYDS"), ("rec_td", "RETD"), ("rec", "REC"),
        ("rec_tgt", "TGT"), ("rec_ypr", "REAVG"), ("rec_lng", "LNG"),
        ("rec_yar", "YAC"), ("rec_air_yd", "AIRYD"), ("rush_att", "CAR"),
        ("rush_yd", "RUYDS"), ("rush_td", "RUTD"), ("fum_lost", "FL"),
    ],
}
_STAT_GRID_FIELDS["TE"] = _STAT_GRID_FIELDS["WR"]
_STAT_GRID_FIELDS["K"] = [
    ("fgm", "FG"), ("fga", "FGA"), ("fgm_lng", "LNG"), ("fgm_50p", "50+"),
    ("fgmiss", "MISS"), ("xpm", "XP"), ("xpa", "XPA"), ("xpmiss", "XPMISS"),
    ("fgm_pct", "FG%"),
]
_STAT_GRID_IDP = [
    ("idp_tkl_solo", "SOLO"), ("idp_tkl_ast", "AST"), ("idp_sack", "SACK"),
    ("idp_tkl_loss", "TFL"), ("idp_qb_hit", "QBH"), ("idp_int", "INT"),
    ("idp_pass_def", "PD"), ("idp_ff", "FF"), ("idp_fum_rec", "FR"),
]


def build_stat_grid(position, stats):
    """The full [(value, label), ...] grid for a performance page.

    Unlike build_stat_line (three headline numbers for a list row) this
    keeps zeros: on a detail page "0 FL" is information -- it says the
    player didn't fumble -- whereas on a one-line summary it would just
    be noise crowding out the stats that did happen."""
    stats = stats or {}
    fields = _STAT_GRID_FIELDS.get(position) or _STAT_GRID_IDP
    grid = []
    for key, label in fields:
        val = stats.get(key)
        if not isinstance(val, (int, float)):
            continue
        grid.append((round(val, 1) if isinstance(val, float) and val % 1 else int(val), label))
    return grid


def get_player_game_swing(sid, season, week):
    """How much this player's plays actually moved the result, in
    percentage points of win probability.

    Live: ESPN publishes the win-probability series play by play as the
    game happens, so this fills in during the game rather than the
    morning after. `total` is the sum across everything the player did,
    `best` the single play that moved it most.

    Returns None when ESPN carries no win-probability series for the
    game, which the page shows as an absent panel rather than a zero."""
    plays = [p for p in (get_player_espn_plays(sid, season, week) or [])
             if p.get("swing") is not None]
    if not plays:
        return None
    total = sum(p["swing"] for p in plays)
    gained = sum(p["swing"] for p in plays if p["swing"] > 0)
    return {
        "total": round(total, 1),
        "gained": round(gained, 1),
        "plays": len(plays),
        "per_play": round(total / len(plays), 2),
        "best": max(plays, key=lambda p: p["swing"]),
    }


# --- Per-play ratings and the quarter breakdown -------------------------
#
# One source, ESPN's own play-by-play, for every number on the
# performance page. The quarter panel used to read nflverse play-by-play
# and was empty for nearly everybody, because nflverse publishes a game
# only after the final whistle AND only once a nightly sync has run.
# ESPN's drive feed is live, covers every game, and already carries
# everything a play row needs -- the score at the time, the quarter and
# clock, the down and distance -- alongside the win-probability series
# that says how much each play mattered.

# How much one play was worth to the player it belonged to, in
# fantasy-point-shaped units. These weights only ever decide the
# RELATIVE size of one play against another (the quarter ratings are a
# split of the score the player actually earned, and the per-play rating
# is a curve over this value), so they are a weighting, not a scoring
# system, and they do not have to match any particular league's rules.
_PLAY_W_YARD = 0.1
_PLAY_W_PASS_YARD = 0.04
_PLAY_W_TD = 6.0
_PLAY_W_PASS_TD = 4.0
_PLAY_W_RECEPTION = 1.0
# Kicking, on the tiering every fantasy league uses -- a 50-yarder is
# worth more than a chip shot, and a miss is worth nothing rather than a
# negative (see _play_value_for on why nothing here goes below zero).
_PLAY_W_FG = 3.0
_PLAY_W_FG_40 = 4.0
_PLAY_W_FG_50 = 5.0
_PLAY_W_XP = 1.0
_PLAY_W_SACK = 4.0
_PLAY_W_INT = 6.0
_PLAY_W_FUMBLE = 4.0
_PLAY_W_TACKLE = 1.0

# The compressive curve that turns that value into the 0-10 rating shown
# beside a play: 10 * v / (v + K). A touchdown run lands in the high
# sixes, a chunk gain in the fours, a routine carry near one, and nothing
# ever reaches ten. K is fitted so the curve reproduces the per-play
# ratings the reference app publishes for a known game.
PLAY_RATING_K = 4.3

_PLAY_PASSER_RE = re.compile(
    r"(?:\d{1,2}-)?([A-Z][A-Za-z]?\.[A-Z][A-Za-z'\-]+)\s+(?:pass|sacked)\b")
_PLAY_RECEIVER_RE = re.compile(
    r"\bto\s+(?:\d{1,2}-)?([A-Z][A-Za-z]?\.[A-Z][A-Za-z'\-]+)")
_PLAY_PAREN_RE = re.compile(r"\(([^)]*)\)")
_FG_DISTANCE_RE = r"\s+(\d{1,3})\s+yard field goal"


def play_rating(value):
    """A play's 0-10 rating from its weighted value.

    The curve only approaches ten, and the rounding is held just below it
    as well, so a ten on this page always means the season-long rating it
    is reserved for rather than one very good snap."""
    v = max(0.0, float(value or 0))
    return min(9.9, round(10 * v / (v + PLAY_RATING_K), 1))


def play_role_for(text, name):
    """What THIS player did on this play, as a role name.

    One play is several different events depending on whose page you are
    reading it on. "22-D.Henry right tackle for 32 yards, TOUCHDOWN.
    8-E.McPherson extra point is GOOD, Center-C.Adomitis,
    Holder-R.Robbins" is a touchdown run for Henry, an extra point for
    McPherson, and nothing at all for the two men who got the ball to the
    tee -- and every one of those four names is in the same sentence.

    Resolving the role ONCE, here, is what keeps the number beside a play
    and the words describing it from disagreeing: both read this. It is
    also the fix for a kicker's page listing three touchdown runs, which
    is what "whoever is named, assume they carried it" produced.

    Returns None when the player is only incidentally named."""
    t = text or ""
    if not name or name not in t:
        return None
    esc = re.escape(name)
    low = t.lower()
    main = _primary_clause(t)
    main_low = main.lower()

    # --- kicking, read off the whole sentence ------------------------
    # The extra point is written AFTER the word "touchdown", which is
    # exactly where _primary_clause cuts, so the kicker is invisible to
    # anything that only looks at the primary clause.
    if re.search(r"(?:center|holder|snapper)-(?:\d{1,2}-)?" + esc, t, re.I):
        return "snap_hold"
    if re.search(esc + r"\s+kicks\b", t, re.I):
        return "kickoff"
    if re.search(esc + r"\s+punts\b", t, re.I):
        return "punt"
    if re.search(esc + _FG_DISTANCE_RE, t, re.I):
        return "field_goal"
    if re.search(esc + r"\s+extra point", t, re.I):
        return "extra_point"

    # --- passing and receiving ---------------------------------------
    m_pass = _PLAY_PASSER_RE.search(main)
    if m_pass and m_pass.group(1) == name:
        if "sacked" in main_low:
            return "sacked"
        if "intercepted" in main_low:
            return "interception_thrown"
        return "passer"
    m_rec = _PLAY_RECEIVER_RE.search(main)
    if m_rec and m_rec.group(1) == name:
        if "incomplete" in main_low or "intercepted" in main_low:
            return "target"
        return "receiver"

    # --- defence, read off the clauses ESPN writes it in --------------
    # Case-insensitively: ESPN writes these in capitals in a gamebook
    # sentence and in title case elsewhere, and both turn up.
    # ESPN writes the credited player as "K.Hamilton", "37-K.Hamilton"
    # or "BAL-37-K.Hamilton" depending on the feed, so team prefix and
    # jersey number are each optional AND stackable -- matching only one
    # of them read a fumble recovery as a carry.
    _CREDIT = r"\s+(?:[A-Z]{2,3}-)?(?:\d{1,2}-)?"
    if re.search(r"intercepted by" + _CREDIT + esc, main, re.I):
        return "interception"
    if re.search(r"recovered by" + _CREDIT + esc, main, re.I):
        return "fumble_recovery"
    if any(name in group for group in _PLAY_PAREN_RE.findall(main)):
        return "sack" if "sacked" in main_low else "tackle"

    # Anything left with the player's name in the play itself is a carry.
    return "rusher" if name in main else None


def _field_goal_distance(text, name):
    """The length of the kick, when the sentence says it."""
    m = re.search(re.escape(name) + _FG_DISTANCE_RE, text or "", re.I)
    return int(m.group(1)) if m else None


def _play_value_for(text, yards, scoring, name):
    """What `name` did on this play, as a non-negative weight.

    Returns 0 for a play the player was only incidentally named in, and
    never goes negative: a lost fumble or an interception is a real part
    of the game, but a negative weight would subtract from a quarter's
    share of a rating the player genuinely earned, which reads as
    nonsense on a bar chart."""
    role = play_role_for(text, name)
    if role is None:
        return 0.0
    t = text or ""
    low = t.lower()
    y = yards if isinstance(yards, (int, float)) else 0
    gain = max(0, int(y))
    # Read off the WHOLE sentence, not the primary clause -- the clause
    # splitter cuts at the word "touchdown" itself, so asking the clause
    # whether a touchdown happened always says no.
    td = "touchdown" in low

    if role == "field_goal":
        if "field goal is good" not in low:
            return 0.0
        d = _field_goal_distance(t, name) or 0
        return (_PLAY_W_FG_50 if d >= 50 else
                _PLAY_W_FG_40 if d >= 40 else _PLAY_W_FG)
    if role == "extra_point":
        return _PLAY_W_XP if "extra point is good" in low else 0.0
    # A kickoff, a punt, a snap and a hold are all real jobs and none of
    # them is scored in any fantasy league.
    if role in ("kickoff", "punt", "snap_hold"):
        return 0.0

    if role == "passer":
        return gain * _PLAY_W_PASS_YARD + (_PLAY_W_PASS_TD if td else 0.0)
    if role in ("sacked", "interception_thrown", "target"):
        return 0.0
    if role == "receiver":
        return gain * _PLAY_W_YARD + _PLAY_W_RECEPTION + (_PLAY_W_TD if td else 0.0)

    if role == "interception":
        return _PLAY_W_INT + (_PLAY_W_TD if td else 0.0)
    if role == "fumble_recovery":
        return _PLAY_W_FUMBLE + (_PLAY_W_TD if td else 0.0)
    if role == "sack":
        return _PLAY_W_SACK
    if role == "tackle":
        return _PLAY_W_TACKLE

    return gain * _PLAY_W_YARD + (_PLAY_W_TD if td else 0.0)


def play_headline_for(text, yards, scoring, name):
    """The play, described as THIS player's play.

    _play_headline titles a play by whoever it mainly belonged to, which
    is right on a game feed where every play appears once. On a player's
    own page every row is his row, so the same sentence has to be read
    from where he was standing -- otherwise a kicker's page reports the
    touchdown runs he kicked the extra points after."""
    role = play_role_for(text, name)
    t = text or ""
    low = t.lower()
    y = yards if isinstance(yards, (int, float)) else None
    td = "touchdown" in low

    if role == "field_goal":
        d = _field_goal_distance(t, name)
        made = "field goal is good" in low
        return (f"{d}-yd field goal" if d else "Field goal") + ("" if made else " missed")
    if role == "extra_point":
        return "Extra point" if "extra point is good" in low else "Extra point missed"
    if role == "kickoff":
        return "Kickoff"
    if role == "punt":
        return "Punt"
    if role == "snap_hold":
        return "Snap"

    if role == "passer":
        yd = _yard_prefix(y)
        if td:
            return f"{yd}TD pass"
        if _went_backwards(y, td):
            return _loss_headline(y)
        return f"{yd}pass" if yd else "Completion"
    if role == "sacked":
        return "Sacked"
    if role == "interception_thrown":
        return "Interception thrown"
    if role == "target":
        return "Incomplete"

    if role == "interception":
        return "Pick-six" if td else "Interception"
    if role == "fumble_recovery":
        return "Fumble returned for TD" if td else "Fumble recovery"
    if role == "sack":
        return "Sack"
    if role == "tackle":
        return "Tackle for loss" if (y is not None and y < 0) else "Tackle"

    # Receiver and rusher read the same either way, so the play's own
    # title is already the right one.
    return _play_headline(text, yards, scoring)


def _player_play_name(player):
    """The "D.Henry" form ESPN writes in play text."""
    first, last = (player or {}).get("first_name"), (player or {}).get("last_name")
    return f"{first[0]}.{last}" if first and last else None


def _win_probability_deltas(summary_json, is_home):
    """{play_id: percentage points this play moved the player's team's
    win probability}.

    ESPN records the home side's win probability after every play, so the
    swing a play produced is the difference from the play before it,
    flipped for an away player. The first entry has nothing before it and
    is skipped rather than measured against zero.

    This is what replaced nflverse EPA. EPA is the better measure in
    principle, but it is published the morning after a game and behind a
    nightly sync, so it could never put a number on a play while anyone
    was watching it. Win probability added answers nearly the same
    question -- how much did this actually change the game -- from a feed
    that updates during the game, and in a unit that reads on its own
    without a reference distribution."""
    raw = (summary_json or {}).get("winprobability") or []
    out, prev = {}, None
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pct = entry.get("homeWinPercentage")
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            continue
        # ESPN gives a 0-1 fraction; a stray 0-100 payload is taken as-is.
        pct = pct * 100 if abs(pct) <= 1 else pct
        play_id = entry.get("playId")
        if prev is not None and play_id is not None:
            delta = pct - prev
            out[str(play_id)] = round(delta if is_home else -delta, 1)
        prev = pct
    return out


_espn_player_plays_cache = {}


def get_player_espn_plays(sid, season, week, cache=_espn_player_plays_cache):
    """Every play this player appeared in for one game, best first, from
    ESPN's live play-by-play.

    Each row carries what a play line needs to be read without the
    surrounding sentence: the quarter and clock, the down and distance,
    the score at that moment, a short headline, and a 0-10 rating.

    Returns [] rather than raising for a player with no team, a week
    that has not been synced, or an ESPN response that arrived empty --
    the panels above it simply do not render."""
    season, week = _safe_int(season, int(SEASON)), _safe_int(week, 1)
    key = (sid, season, week)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 45:
        return entry["data"]

    player = get_all_players().get(sid) or {}
    name, team = _player_play_name(player), player.get("team")
    sched = get_schedule_for_team_week(season, week, team) if (name and team) else None
    event_id = (sched or {}).get("espn_event_id")
    if not event_id:
        cache[key] = {"data": [], "time": now}
        return []

    summary = espn_game_summary(event_id) or {}
    # A whole game is 150-190 plays; the default feed cap of 60 would
    # silently drop the first half.
    raw = extract_drive_plays(summary, limit=400)
    is_home = bool((sched or {}).get("home"))
    opponent = (sched or {}).get("opponent")

    # How much each play moved the result, from ESPN's own
    # win-probability series. This is the live replacement for nflverse
    # EPA: published play by play as the game happens rather than the
    # morning after, and in a unit that needs no reference pool to read
    # -- percentage points of win probability.
    swing_by_play = _win_probability_deltas(summary, is_home)

    out = []
    for play in raw:
        text = play.get("text") or ""
        if not name or name not in text:
            continue
        value = _play_value_for(text, play.get("yards"), play.get("scoring"), name)
        if value <= 0:
            # Nothing this player did on this play counts: a bystander
            # ESPN happened to name, an incompletion, a kickoff, or the
            # long snapper on somebody else's field goal. The scoringPlay
            # flag used to wave these through, which is how a kickoff
            # turned up on a kicker's highlight list.
            continue
        role = play_role_for(text, name)
        touchdown = ("touchdown" in text.lower()
                     and role in ("receiver", "rusher", "passer",
                                  "interception", "fumble_recovery"))
        qtr = play.get("period")
        clock = (play.get("clock") or "").strip()
        out.append({
            "qtr": qtr,
            "clock": clock,
            "down": play.get("down"),
            "ydstogo": play.get("distance"),
            "description": text,
            # Described from where THIS player was standing, not from
            # whoever the play mainly belonged to.
            "headline": play_headline_for(text, play.get("yards"),
                                          play.get("scoring"), name),
            "role": role,
            "yards_gained": play.get("yards"),
            # Only a touchdown THIS player was part of reads as one --
            # the kicker's extra point on somebody else's score is not
            # the kicker's touchdown.
            "touchdown": touchdown,
            # Did this player put points on the board? Touchdowns and
            # made kicks both did; a missed kick never reaches here,
            # since it is worth nothing and gets filtered out above.
            "scored": touchdown or role in ("field_goal", "extra_point"),
            "value": round(value, 2),
            "rating": play_rating(value),
            "swing": swing_by_play.get(str(play.get("id"))) if play.get("id") else None,
            "team": team,
            "opponent": opponent,
            "team_score": play.get("home_score") if is_home else play.get("away_score"),
            "opp_score": play.get("away_score") if is_home else play.get("home_score"),
        })

    out.sort(key=lambda p: (p["value"], p.get("qtr") or 0), reverse=True)
    cache[key] = {"data": out, "time": now}
    return out


def get_player_quarter_breakdown(sid, season, week, score=None):
    """The player's game rating, split across the quarters they earned it
    in -- so the four bars add up to the one number at the top of the
    page rather than sitting on some unrelated scale.

    Weighting comes from ESPN's play-by-play, which is live and covers
    every game. Where ESPN also carries a win-probability series, the
    swing each play produced is the weight -- a truer measure of when a
    game was actually won than yardage is -- and where it does not, the
    play's own production stands in. Both are live; the split is on the
    same scale either way.

    Quarters run from the first one the player appeared in to the last,
    so a quarter they were on the field for but did nothing in shows as a
    zero -- which is information -- while quarters before they entered or
    after they left are simply absent, which is also information."""
    season, week = _safe_int(season, int(SEASON)), _safe_int(week, 1)
    if score is None:
        player = get_all_players().get(sid) or {}
        live = (get_live_week_stats(season, week, allow_fetch=False) or {}).get(sid) or {}
        score = (grade_performance(player.get("position"), live.get("pts"), season) or {}).get("score")
    if score is None:
        return []

    plays = get_player_espn_plays(sid, season, week)
    if not plays:
        return []

    # Swing first, production second -- and never a mix of the two in one
    # chart, since they are different units and a half-and-half split
    # would be measuring two things at once.
    swings = [p for p in plays if p.get("qtr") and p.get("swing") is not None]
    if swings and sum(abs(p["swing"]) for p in swings) > 0:
        source = "swing"
        weighted = [(int(p["qtr"]), abs(p["swing"])) for p in swings]
    else:
        source = "production"
        weighted = [(int(p["qtr"]), p["value"]) for p in plays if p.get("qtr")]

    weights = {}
    for qtr, w in weighted:
        weights[qtr] = weights.get(qtr, 0.0) + w

    if not weights:
        return []
    total = sum(weights.values())
    if total <= 0:
        return []

    lo, hi = min(weights), max(weights)
    out = []
    for q in range(lo, hi + 1):
        w = weights.get(q, 0.0)
        out.append({
            "qtr": q,
            "label": f"Q{q}" if q <= 4 else ("OT" if q == 5 else f"OT{q - 4}"),
            "rating": round(score * w / total, 2),
            "share": round(100 * w / total),
            "source": source,
        })

    # Rounding must not cost the player part of their score: the
    # leftover thousandths go to the biggest quarter, so the bars always
    # add back up to the number printed at the top of the page.
    drift = round(score - sum(q["rating"] for q in out), 2)
    if drift and out:
        top = max(out, key=lambda q: q["rating"])
        top["rating"] = round(top["rating"] + drift, 2)
    return out


def ordinal(n):
    """1st, 2nd, 3rd, 4th -- with the teens exception, which a bare
    last-digit rule gets wrong in both directions (11th not 11st, but
    21st not 21th). Registered as a Jinja filter because this was being
    written inline in templates, where the short version produced "22th".
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


app.jinja_env.filters["ordinal"] = ordinal


def get_performance_detail(sid, season, week):
    """Everything the /performance page shows for one player-week, or
    None if that player has no stat line for the week.

    Reads the same live week stats the performer board does, so a page
    opened mid-game shows the same numbers the board just showed rather
    than a staler set from the database."""
    season, week = _safe_int(season, int(SEASON)), _safe_int(week, 1)
    live = get_live_week_stats(season, week)
    rec = (live or {}).get(sid)
    p = get_all_players().get(sid)
    if not rec or not p:
        return None

    position = p.get("position")
    team = p.get("team")
    stats = rec.get("stats") or {}
    fpts = rec.get("pts", 0)
    sched = get_schedule_for_team_week(season, week, team) if team else None

    # Snap share is real, already synced, and the closest thing this app
    # has to the tracking data a paid provider would sell -- how much of
    # the offense the player was actually on the field for.
    snap_pct = None
    off_snp, tm_off_snp = stats.get("off_snp"), stats.get("tm_off_snp")
    if isinstance(off_snp, (int, float)) and isinstance(tm_off_snp, (int, float)) and tm_off_snp:
        snap_pct = round(100 * off_snp / tm_off_snp)

    # One source for everything on this page: ESPN's own play-by-play,
    # which is live and covers every game.
    feed_plays = get_player_espn_plays(sid, season, week)
    swing = get_player_game_swing(sid, season, week)

    # Target share, same idea, for pass catchers.
    target_share = None
    tgt, team_tgt = stats.get("rec_tgt"), stats.get("tm_pass_att")
    if isinstance(tgt, (int, float)) and isinstance(team_tgt, (int, float)) and team_tgt:
        target_share = round(100 * tgt / team_tgt)

    return {
        "sid": sid,
        "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
        "position": position,
        "team": team,
        "photo": player_photo_url(sid),
        "season": season,
        "week": week,
        "fpts": fpts,
        "opponent": (sched or {}).get("opponent"),
        "is_home": (sched or {}).get("home"),
        "vs_label": (None if not sched else
                     (f"vs {sched['opponent']}" if sched.get("home") else f"@ {sched['opponent']}")),
        "team_score": (sched or {}).get("team_score"),
        "opp_score": (sched or {}).get("opp_score"),
        "game_status": (sched or {}).get("status"),
        "headline": build_stat_line(position, stats),
        "grid": build_stat_grid(position, stats),
        "snap_pct": snap_pct,
        "target_share": target_share,
        "score": grade_performance(position, fpts, season),
        # Fills in DURING the game now, rather than after it: ESPN
        # publishes its win-probability series play by play.
        "swing": swing,
        "feed_plays": feed_plays,
        "opp_logo": team_logo_url((sched or {}).get("opponent")) if sched else None,
        "team_logo": team_logo_url(team) if team else None,
    }


def get_player_season_log(sid, season, position, through_week=None):
    """This player's score for every week they've played this season, for
    the game-log chart -- so one performance can be read against their
    own body of work, not just against the league.

    Comes from the database rather than the live feed (only the current
    week is live, and a season-long chart needs all of them), with the
    live figure patched over the current week so the chart's last bar
    matches the score shown at the top of the page instead of lagging a
    sync behind."""
    season = _safe_int(season, int(SEASON))
    weeks = dict((get_season_stats(season).get(sid) or {}).get("weeks") or {})
    if through_week:
        live = get_live_week_stats(season, through_week, allow_fetch=False) or {}
        if sid in live:
            weeks[through_week] = live[sid].get("pts", 0)
    out = []
    for wk in sorted(weeks, key=lambda w: _safe_int(w, 0)):
        pts = weeks[wk]
        if not isinstance(pts, (int, float)):
            continue
        out.append({
            "week": _safe_int(wk, 0),
            "fpts": round(float(pts), 1),
            "score": grade_performance(position, pts, season)["score"],
        })
    return out


# The ranges the performance leaderboard offers, and how far back each
# one reaches. "All time" means every season actually synced into this
# database, not every season in NFL history -- the label says "all time"
# because that's what it is for this site, and the page says how many
# seasons that covers rather than implying more.
PERF_SCOPES = ("day", "week", "month", "season", "alltime")
# The earliest season this site ever backfills (see backfill-stats.yml,
# which seeds 2015 through the current year). Anchored to a START YEAR
# rather than "N seasons back" on purpose: a fixed offset silently stops
# meaning 2015 the moment a new season rolls over, so "all time" would
# quietly shed its oldest season every September.
PERF_EARLIEST_SEASON = 2015
_perf_board_cache = {}


def _month_weeks(season, month, season_type=2):
    """Which week numbers of `season` have games falling in `month`.

    Derived from the schedule table rather than assumed, because the NFL
    week grid doesn't line up with calendar months and drifts year to
    year."""
    if not DATABASE_URL:
        return []
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT week FROM nfl_schedule
                   WHERE season = %s AND season_type = %s
                     AND EXTRACT(MONTH FROM kickoff) = %s
                   ORDER BY week""",
                (season, season_type, month),
            )
            return [r["week"] for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def _historical_performances(season, position=None, weeks=None):
    """Every scored performance from one season, out of the database.

    This is the path the month/season/all-time ranges use. It can't use
    the live feed -- that only covers the current week -- so it reads
    player_stats, which is exactly what that table is for."""
    all_players = get_all_players()
    stats = get_season_stats(season)
    week_filter = set(weeks) if weeks else None
    out = []
    for sid, stat in stats.items():
        p = all_players.get(sid)
        if not p:
            continue
        pos = p.get("position")
        if not pos or pos not in SCORED_POSITIONS:
            continue
        if position and pos != position:
            continue
        for week, fpts in (stat.get("weeks") or {}).items():
            wk = _safe_int(week, 0)
            if week_filter is not None and wk not in week_filter:
                continue
            if not isinstance(fpts, (int, float)):
                continue
            out.append({
                "sid": sid,
                "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                "position": pos,
                "team": p.get("team") or "FA",
                "photo": player_photo_url(sid),
                "season": season,
                "week": wk,
                "fpts": round(float(fpts), 1),
                "grade": grade_performance(pos, fpts, season),
                # A historical row has no live stat payload to summarise,
                # so the points stand in for the line. The detail page
                # still shows the full breakdown when it's opened.
                "stat_line": [],
                "vs_label": None,
            })
    return out


def get_performance_board(scope="week", position=None, season=None, week=None,
                          order="top", limit=250, cache=_perf_board_cache):
    """The performance leaderboard, for any range and position.

    `scope` is one of PERF_SCOPES. "day" and "week" come from the live
    feed so an in-progress slate updates; everything longer comes from
    the database, because only the current week is ever live.

    `order` is "top" or "lowest". Lowest deliberately excludes anyone who
    scored nothing at all -- a list of players who didn't play isn't a
    list of bad performances, it's a list of absences, and it would be
    the same few hundred names every week."""
    season = _safe_int(season if season is not None else SEASON, int(SEASON))
    week = _safe_int(week if week is not None else 1, 1)
    scope = scope if scope in PERF_SCOPES else "week"
    order = "lowest" if order == "lowest" else "top"
    key = (scope, position, season, week, order, limit)
    now = time.time()
    entry = cache.get(key)
    # Live ranges go stale in 45s; historical ones can't change at all
    # until the next sync, so they're held for an hour.
    ttl = 45 if scope in ("day", "week") else 3600
    if entry and now - entry["time"] < ttl:
        return entry["data"]

    if scope in ("day", "week"):
        rows = [r for r in get_week_performers(season, week, per_team=None)
                if not position or r["position"] == position]
    elif scope == "month":
        info = get_current_week_info()
        month = None
        sched = None
        # Which calendar month the selected week sits in.
        for wk in (week,):
            games, _ = _week_games(season, wk, info.get("season_type", 2))
            if games and games[0].get("date"):
                month = _safe_int(str(games[0]["date"])[5:7], 0)
        weeks = _month_weeks(season, month) if month else [week]
        rows = _historical_performances(season, position, weeks or [week])
    elif scope == "season":
        rows = _historical_performances(season, position)
    else:
        rows = []
        for yr in range(season, PERF_EARLIEST_SEASON - 1, -1):
            rows.extend(_historical_performances(yr, position))

    if order == "lowest":
        rows = [r for r in rows if r["fpts"] > 0]
        rows.sort(key=lambda r: (r["grade"]["score"], r["fpts"]))
    else:
        rows.sort(key=lambda r: (-r["grade"]["score"], -r["fpts"]))

    rows = rows[:limit]
    cache[key] = {"data": rows, "time": now}
    return rows


# Still-to-play first, finished last -- the same ordering the scores
# board uses, kept in one place so the two can't drift apart.
GAME_STATUS_ORDER = {"in_progress": 0, "scheduled": 1, "final": 2}


def _week_games(season, week, season_type=2):
    """Shared by /scores and /api/scoreboard so both build cards the same
    way. Returns (games, any_live) where games is a list of card dicts
    from espn_event_to_card, already filtered for malformed events."""
    data = espn_week_scoreboard(season, week, season_type)
    games = [c for c in (espn_event_to_card(ev) for ev in data.get("events", [])) if c]
    # Tag the week we asked for onto each card. The date strip labels every
    # tab "W1 / Sep 13", and a scoreboard event doesn't reliably carry its
    # own week number -- but the caller always knows which week it fetched.
    for g in games:
        g["week"] = week
        g["season"] = season
    any_live = any(g["status"] == "in_progress" for g in games)
    return games, any_live


# Conference and division for all 32 teams. Static on purpose: the NFL's
# alignment has not changed since 2002 and a realignment would be league
# news, not a silent data drift -- so hardcoding it is more reliable than
# scraping it, and it means standings work from the schedule table alone.
NFL_DIVISIONS = {
    "BAL": ("AFC", "North"), "CIN": ("AFC", "North"), "CLE": ("AFC", "North"), "PIT": ("AFC", "North"),
    "HOU": ("AFC", "South"), "IND": ("AFC", "South"), "JAX": ("AFC", "South"), "TEN": ("AFC", "South"),
    "BUF": ("AFC", "East"), "MIA": ("AFC", "East"), "NE": ("AFC", "East"), "NYJ": ("AFC", "East"),
    "DEN": ("AFC", "West"), "KC": ("AFC", "West"), "LV": ("AFC", "West"), "LAC": ("AFC", "West"),
    "CHI": ("NFC", "North"), "DET": ("NFC", "North"), "GB": ("NFC", "North"), "MIN": ("NFC", "North"),
    "ATL": ("NFC", "South"), "CAR": ("NFC", "South"), "NO": ("NFC", "South"), "TB": ("NFC", "South"),
    "DAL": ("NFC", "East"), "NYG": ("NFC", "East"), "PHI": ("NFC", "East"), "WAS": ("NFC", "East"),
    "ARI": ("NFC", "West"), "LAR": ("NFC", "West"), "SF": ("NFC", "West"), "SEA": ("NFC", "West"),
}
# Full names for the 32 teams. Same reasoning as the division map: stable
# league facts, and hardcoding them means a team page renders correctly
# even before any schedule has synced.
TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}
CONFERENCES = ("AFC", "NFC")
DIVISIONS = ("North", "South", "East", "West")

_standings_cache = {}


def _finished_games(season, season_type=2, include_live=False):
    """Every completed game of a season, oldest first, as plain rows.

    One query feeds standings, rankings, recent form and the team page's
    results list -- they are all the same underlying facts, and deriving
    them from a single read keeps them from ever disagreeing.

    `include_live` additionally counts games still being played, at the
    score they currently hold. Standings must not use it -- a win is not
    a win until the whistle -- but a power ranking should move while the
    games move, which is what it is for."""
    if not DATABASE_URL:
        return []
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT espn_event_id, week, kickoff, home_team, away_team,
                          home_score, away_score, status
                   FROM nfl_schedule
                   WHERE season = %s AND season_type = %s
                     AND home_score IS NOT NULL AND away_score IS NOT NULL
                     AND status = ANY(%s)
                   ORDER BY kickoff""",
                (season, season_type,
                 ["final", "in_progress"] if include_live else ["final"]),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def get_team_standings(season, season_type=2, cache=_standings_cache):
    """{team: {...}} -- record, win pct, points for/against, division
    record, last five, streak and conference rank.

    Computed from our own schedule rather than fetched, so it is correct
    for whatever the table holds and needs no second source to stay in
    step with the scores already on the site."""
    season, season_type = _safe_int(season, int(SEASON)), _safe_int(season_type, 2)
    key = (season, season_type)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 300:
        return entry["data"]

    rows = {t: {"team": t, "conference": c, "division": d,
                "wins": 0, "losses": 0, "ties": 0,
                "pf": 0, "pa": 0, "games": 0,
                "div_wins": 0, "div_losses": 0, "div_ties": 0,
                "results": []}
            for t, (c, d) in NFL_DIVISIONS.items()}

    for g in _finished_games(season, season_type):
        home, away = g["home_team"], g["away_team"]
        hs, as_ = g["home_score"], g["away_score"]
        if home not in rows or away not in rows:
            continue
        same_div = (NFL_DIVISIONS[home] == NFL_DIVISIONS[away])
        for team, own, opp in ((home, hs, as_), (away, as_, hs)):
            r = rows[team]
            r["games"] += 1
            r["pf"] += own
            r["pa"] += opp
            outcome = "W" if own > opp else ("L" if own < opp else "T")
            r["results"].append(outcome)
            r["wins" if outcome == "W" else ("losses" if outcome == "L" else "ties")] += 1
            if same_div:
                r["div_wins" if outcome == "W" else
                  ("div_losses" if outcome == "L" else "div_ties")] += 1

    for r in rows.values():
        played = r["games"] or 0
        decided = r["wins"] + r["losses"] + r["ties"]
        # Ties count as half a win, which is how the NFL computes win pct.
        r["pct"] = round((r["wins"] + 0.5 * r["ties"]) / decided, 3) if decided else 0.0
        r["ppg"] = round(r["pf"] / played, 1) if played else 0.0
        r["papg"] = round(r["pa"] / played, 1) if played else 0.0
        r["diff"] = r["pf"] - r["pa"]
        r["record"] = f"{r['wins']}-{r['losses']}" + (f"-{r['ties']}" if r["ties"] else "")
        r["div_record"] = (f"{r['div_wins']}-{r['div_losses']}"
                           + (f"-{r['div_ties']}" if r["div_ties"] else ""))
        last5 = r["results"][-5:]
        r["last5"] = f"{last5.count('W')}-{last5.count('L')}" + (
            f"-{last5.count('T')}" if last5.count("T") else "")
        # Current streak, counted back from the most recent result.
        streak = 0
        for outcome in reversed(r["results"]):
            if streak and outcome != r["results"][-1]:
                break
            streak += 1
        r["streak"] = f"{r['results'][-1]}{streak}" if r["results"] else "-"

    # Conference rank, the "6th AFC" figure on each row. Ordered by win
    # pct, then point differential as the tie-break -- not the NFL's full
    # tiebreaker ladder (head-to-head, common games, strength of victory),
    # which needs more than a schedule table; this is an ordering for
    # display, and the page says as much.
    for conf in CONFERENCES:
        members = sorted((r for r in rows.values() if r["conference"] == conf),
                         key=lambda r: (-r["pct"], -r["diff"], -r["pf"]))
        for i, r in enumerate(members, 1):
            r["conf_rank"] = i

    _assign_playoff_seeds(rows)
    cache[key] = {"data": rows, "time": now}
    return rows


# The marks the reference standings use, and what each one means. Kept in
# one place so the rows and the legend below them can never disagree.
PLAYOFF_MARKERS = {
    "bye":      {"emoji": "\U0001F410", "label": "Bye to Divisional Round"},
    "division": {"emoji": "\U0001F451", "label": "Division leader"},
    "wildcard": {"emoji": "\U0001F0CF", "label": "Wild Card team"},
}
TRADED_PICK_EMOJI = "\u27A1\uFE0F"

# Where a traded first-round pick actually lands: {(season, original
# team): team that now holds it}. Deliberately empty.
#
# Nobody publishes traded draft picks in a free feed -- not ESPN's
# scoreboard, not Sleeper, not nflverse. The marker below renders the
# moment an entry exists here, so this is the one line to fill in (by
# hand, or from a paid feed) rather than a feature to rebuild. An empty
# map means the draft order simply shows no trades, which is honest.
TRADED_DRAFT_PICKS = {}


def _seed_order(r):
    """Win pct, then point differential, then points scored.

    Not the NFL's full tiebreaker ladder -- head-to-head, common games
    and strength of victory need more than a schedule table. This is an
    ordering for display and the page says so."""
    return (-r["pct"], -r["diff"], -r["pf"])


def _assign_playoff_seeds(rows):
    """Give every team its conference seed and the mark that goes with
    it: the goat for the first-round bye, the crown for the other three
    division winners, the joker for the wild cards.

    Division winners take seeds 1-4 however their records compare to the
    rest of the conference -- that is the whole point of winning a
    division, and a 9-8 winner really does seed above an 11-6 wild
    card."""
    for conf in CONFERENCES:
        members = [r for r in rows.values() if r["conference"] == conf]
        leaders = []
        for d in DIVISIONS:
            division = sorted((r for r in members if r["division"] == d), key=_seed_order)
            if division:
                leaders.append(division[0])
        leaders.sort(key=_seed_order)
        leader_teams = {r["team"] for r in leaders}
        rest = sorted((r for r in members if r["team"] not in leader_teams), key=_seed_order)

        for i, r in enumerate(leaders + rest, 1):
            kind = ("bye" if i == 1 else
                    "division" if i <= 4 else
                    "wildcard" if i <= 7 else None)
            mark = PLAYOFF_MARKERS.get(kind)
            r["seed"] = i
            r["seed_kind"] = kind
            r["marker"] = mark["emoji"] if mark else None
            r["marker_label"] = mark["label"] if mark else None
            r["in_playoffs"] = kind is not None


def get_playoff_field(season, conf, season_type=2):
    """One conference in seed order, playoff teams first -- the same list
    the reference shows behind its Playoffs tab."""
    rows = get_team_standings(season, season_type)
    members = [r for r in rows.values() if r["conference"] == conf]
    members.sort(key=lambda r: r.get("seed") or 99)
    return members


def _season_opponents(season, season_type=2, cache={}):
    """{team: [opponent, ...]} across the WHOLE schedule, played or not.

    Strength of schedule counts every opponent a team is down to face,
    each appearance separately, so a division rival met twice counts
    twice. That is how the league computes it, and it is why the figure
    is meaningful in week 1 when almost nothing has been played."""
    season, season_type = _safe_int(season, int(SEASON)), _safe_int(season_type, 2)
    key = (season, season_type)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 3600:
        return entry["data"]
    out = {}
    if DATABASE_URL:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT home_team, away_team FROM nfl_schedule
                       WHERE season = %s AND season_type = %s""",
                    (season, season_type),
                )
                for r in cur.fetchall():
                    out.setdefault(r["home_team"], []).append(r["away_team"])
                    out.setdefault(r["away_team"], []).append(r["home_team"])
        except Exception:
            out = {}
        finally:
            conn.close()
    cache[key] = {"data": out, "time": now}
    return out


def get_draft_order(season, season_type=2):
    """All 32 teams in draft order: worst record picks first, ties broken
    by the easier schedule, as the league breaks them.

    This is the regular-season order. Once the postseason is played the
    last fourteen picks reorder by how far each team went, which needs
    playoff results this does not have yet -- so during the season it is
    right, and in January it is the order before the bracket moves it."""
    rows = get_team_standings(season, season_type)
    opponents = _season_opponents(season, season_type)
    out = []
    for team, r in rows.items():
        wins = losses = ties = 0
        for opp in opponents.get(team, []):
            o = rows.get(opp)
            if not o:
                continue
            wins += o["wins"]
            losses += o["losses"]
            ties += o["ties"]
        decided = wins + losses + ties
        sos = round((wins + 0.5 * ties) / decided, 3) if decided else 0.0
        entry = dict(r)
        entry["sos"] = sos
        entry["traded_to"] = TRADED_DRAFT_PICKS.get((_safe_int(season, int(SEASON)), team))
        out.append(entry)
    # Worst record first; the easier schedule picks ahead of the harder
    # one; points allowed as a last resort so the order is stable.
    out.sort(key=lambda r: (r["pct"], r["sos"], -r["pa"]))
    for i, r in enumerate(out, 1):
        r["pick"] = i
    return out


# The position families the scores board leads with, in the order the
# reference shows them. Kickers get their own line rather than being
# folded in with the skill positions, where a 14-point day would never
# out-rank a receiver's 30.
PERFORMER_GROUPS = [
    {"key": "qb",  "title": "Top QB",         "positions": ["QB"]},
    {"key": "flex", "title": "Top WR/RB/TE",  "positions": ["WR", "RB", "TE"]},
    {"key": "idp", "title": "Top DB/LB/DL",   "positions": ["DB", "LB", "DL"]},
    {"key": "k",   "title": "Top K",          "positions": ["K"]},
]

# How many teams the scores board's power-ranking strip shows before
# handing off to the full standings page.
POWER_BOARD_SIZE = 10


def get_power_board(season, season_type=2, limit=POWER_BOARD_SIZE):
    """The top of the power ranking, ready to render: rank, logo, record
    and point differential.

    Ranked on the four-round window rather than the whole season, which
    is the one people mean by "power ranking" -- who is good NOW, not who
    banked wins in September. Teams with nothing in that window fall back
    to their season rank inside get_team_rankings, so nobody is missing.

    Returns [] rather than raising when the season has no finished games
    yet; the strip simply does not render."""
    try:
        ranks = get_team_rankings(season, season_type)
        standings = get_team_standings(season, season_type)
    except Exception:
        return []
    out = []
    for team, r in (ranks or {}).items():
        place = r.get("d30") or r.get("season")
        if not place:
            continue
        st = standings.get(team) or {}
        record = f"{st.get('wins', 0)}-{st.get('losses', 0)}"
        if st.get("ties"):
            record += f"-{st['ties']}"
        out.append({
            "team": team, "rank": place, "logo": team_logo_url(team),
            "record": record, "diff": st.get("diff"),
        })
    out.sort(key=lambda t: t["rank"])
    return out[:limit]


# --- Injuries and birthdays ---------------------------------------------
#
# Both read the player dump the site already pulls, so neither costs a
# new source. Injuries need one thing the dump cannot give on its own:
# Sleeper publishes the CURRENT designation, never the change, so
# "Out -> Active" only exists if something was watching for it. That is
# what player_injury_state is for -- it is written every time the dump
# refreshes and remembers what each designation used to be.

# Sleeper leaves injury_status null for a healthy player. "Active" is
# what that means and what the reference calls it.
ACTIVE_STATUS = "Active"

# Designations that read as a setback, so a change INTO one is bad news
# and a change out of one is good news.
_INJURY_BAD = {"IR", "OUT", "DOUBTFUL", "PUP", "SUSPENDED", "NA", "COV"}

INJURY_FEED_SIZE = 6
BIRTHDAY_FEED_SIZE = 6


def _players_or_empty():
    """The player dump, or {} when it is unavailable or not the shape we
    expect.

    Injuries and birthdays are decoration on a page whose job is live
    scores. An upstream hiccup, or a response that arrives in an
    unexpected shape, must cost those two sections and nothing else --
    it must never be able to blank the scoreboard above them, which is
    what an exception escaping into the route would do."""
    try:
        players = get_all_players()
    except Exception:
        return {}
    return players if isinstance(players, dict) else {}


def _injury_status_of(player):
    """The designation as it should be displayed: a real status, or
    Active when Sleeper leaves it blank."""
    raw = ((player or {}).get("injury_status") or "").strip()
    if not raw:
        return ACTIVE_STATUS
    return INJURY_BADGE.get(raw.upper(), (None, raw.title(), None))[1]


def record_injury_changes(all_players):
    """Write every designation that moved since the last time we looked.

    Called after the player dump refreshes, from the background thread
    that refreshed it -- never on a request path. Returns the number of
    changes recorded (0 when there is no database, which is a valid
    configuration here and simply means no injury feed)."""
    if not DATABASE_URL or not isinstance(all_players, dict) or not all_players:
        return 0
    current = {}
    for sid, p in all_players.items():
        # Only players on a roster. A dump of every player Sleeper has
        # ever heard of would otherwise fill the feed with the retired.
        if (p or {}).get("team"):
            current[sid] = _injury_status_of(p)
    if not current:
        return 0

    conn = get_db()
    changed = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sleeper_id, status FROM player_injury_state")
            known = {r["sleeper_id"]: r["status"] for r in cur.fetchall()}
            rows = [(sid, status, known.get(sid))
                    for sid, status in current.items()
                    if known.get(sid) != status]
            if not rows:
                return 0
            # A player we have never seen before is not a change -- there
            # is nothing to have changed FROM. Seed them silently by
            # recording the status with no previous one, so the feed
            # starts reporting the moment anything actually moves.
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO player_injury_state
                       (sleeper_id, status, previous_status, changed_at)
                   VALUES %s
                   ON CONFLICT (sleeper_id) DO UPDATE SET
                       previous_status = player_injury_state.status,
                       status = EXCLUDED.status,
                       changed_at = NOW()""",
                [(sid, status, prev) for sid, status, prev in rows],
            )
            changed = len(rows)
        conn.commit()
    except Exception:
        changed = 0
    finally:
        conn.close()
    _injury_feed_cache.clear()
    return changed


_injury_feed_cache = {}


def get_injury_changes(limit=INJURY_FEED_SIZE, cache=_injury_feed_cache):
    """The most recent designation changes, newest first.

    Each row is ready to render: who, from what to what, whether that is
    good news, and how long ago. Returns [] rather than raising when
    there is no database or nothing has moved yet -- the section simply
    does not render."""
    now = time.time()
    entry = cache.get(limit)
    if entry and now - entry["time"] < 300:
        return entry["data"]
    if not DATABASE_URL:
        return []
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT sleeper_id, status, previous_status, changed_at
                   FROM player_injury_state
                   WHERE previous_status IS NOT NULL
                     AND previous_status IS DISTINCT FROM status
                   ORDER BY changed_at DESC LIMIT %s""",
                (limit * 3,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()

    all_players = _players_or_empty()
    out = []
    for r in rows:
        p = all_players.get(r["sleeper_id"])
        if not p or not p.get("team"):
            continue
        status = r["status"] or ACTIVE_STATUS
        out.append({
            "sid": r["sleeper_id"],
            "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position": p.get("position"),
            "team": p.get("team"),
            "logo": team_logo_url(p.get("team")),
            "photo": player_photo_url(r["sleeper_id"]),
            "from": r["previous_status"] or ACTIVE_STATUS,
            "to": status,
            # Green for a return, red for a setback -- the same reading
            # the reference gives them.
            "good": status == ACTIVE_STATUS,
            "ago": _time_ago(r["changed_at"]),
        })
        if len(out) >= limit:
            break
    cache[limit] = {"data": out, "time": now}
    return out


def _time_ago(when):
    """"4h", "2d" -- the age of a change, the way a feed writes it."""
    if not when:
        return None
    try:
        delta = datetime.utcnow() - when.replace(tzinfo=None)
    except Exception:
        return None
    secs = max(0, int(delta.total_seconds()))
    if secs < 3600:
        return f"{max(1, secs // 60)}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _ordinal_age(n):
    """22nd, 31st, 23rd -- reusing the same rule the rest of the site
    uses so an age and a draft slot are never written differently."""
    return ordinal(n)


def get_birthdays_today(limit=BIRTHDAY_FEED_SIZE, today=None):
    """Everyone on a roster whose birthday is today, most notable first.

    Notability is Sleeper's own search_rank, which is how prominent a
    player is in their app -- the closest thing the dump has to "who
    would you actually care about", and it costs nothing extra.

    Leap-day birthdays are celebrated on the 28th in non-leap years,
    which is the common convention and better than skipping the player
    entirely three years in four."""
    today = today or date.today()
    out = []
    for sid, p in _players_or_empty().items():
        if not isinstance(p, dict) or not p.get("team"):
            continue
        raw = (p.get("birth_date") or "").strip()
        if not raw:
            continue
        try:
            y, m, d = [int(x) for x in raw.split("-")]
        except (ValueError, TypeError):
            continue
        match = (m == today.month and d == today.day)
        if not match and m == 2 and d == 29 and today.month == 2 and today.day == 28:
            try:
                date(today.year, 2, 29)
            except ValueError:
                match = True    # no 29th this year, so today is the day
        if not match:
            continue
        age = today.year - y
        if age <= 0 or age > 70:
            continue
        out.append({
            "sid": sid,
            "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position": p.get("position"),
            "team": p.get("team"),
            "logo": team_logo_url(p.get("team")),
            "photo": player_photo_url(sid),
            "age": age,
            "age_label": _ordinal_age(age),
            "rank": p.get("search_rank") or 999999,
        })
    out.sort(key=lambda b: b["rank"])
    return out[:limit]


_team_rank_cache = {}


def get_team_rankings(season, season_type=2, cache=_team_rank_cache):
    """Power ranking over three windows -- the latest round, the last
    four rounds, and the whole season -- as {team: {"d7": n, "d30": n,
    "season": n}}.

    Ranked on average point differential, which is the single most
    predictive simple measure of team strength and, importantly, is
    computable from data the site already has.

    Three things make it a live ranking rather than a weekly one:

    * Games in progress count, at the score they currently hold, so the
      board moves while the games move.
    * The windows are counted in weeks rather than days. The NFL plays
      in rounds, and a date window catches the Thursday game but misses
      the Sunday one from the same round -- so "last 7 days" means the
      most recent round, and "last 30 days" the most recent four.
    * A team with nothing in a narrow window (a bye, or a round it has
      not played yet) inherits its rank from the next window out rather
      than showing a blank. Every team that has played is ranked in all
      three columns."""
    season, season_type = _safe_int(season, int(SEASON)), _safe_int(season_type, 2)
    key = (season, season_type)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < entry.get("ttl", 300):
        return entry["data"]

    games = _finished_games(season, season_type, include_live=True)
    live = any(g.get("status") == "in_progress" for g in games)
    latest_week = max((g["week"] for g in games if g["week"]), default=None)
    out = {t: {} for t in NFL_DIVISIONS}

    # Widest first, so each narrower window has something to fall back on.
    windows = [("season", None),
               ("d30", PLAYER_WINDOW_WEEKS["d30"]),
               ("d7", PLAYER_WINDOW_WEEKS["d7"])]
    wider = {"d30": "season", "d7": "d30"}

    for label, weeks in windows:
        totals = {}
        for g in games:
            if weeks is not None:
                if not g["week"] or latest_week is None:
                    continue
                if g["week"] <= latest_week - weeks:
                    continue
            for team, own, opp in ((g["home_team"], g["home_score"], g["away_score"]),
                                   (g["away_team"], g["away_score"], g["home_score"])):
                if team not in out or own is None or opp is None:
                    continue
                t = totals.setdefault(team, {"diff": 0, "games": 0})
                t["diff"] += own - opp
                t["games"] += 1
        ranked = sorted(((team, v["diff"] / v["games"]) for team, v in totals.items() if v["games"]),
                        key=lambda kv: -kv[1])
        for i, (team, _avg) in enumerate(ranked, 1):
            out[team][label] = i
        fallback = wider.get(label)
        if fallback:
            for vals in out.values():
                if label not in vals and fallback in vals:
                    vals[label] = vals[fallback]

    # While games are being played the ranking is worth recomputing often;
    # between rounds it cannot change, so it is cached the usual way.
    cache[key] = {"data": out, "time": now, "ttl": 45 if live else 300}
    return out


def get_team_schedule(team, season, season_type=2):
    """Every game on a team's schedule, played or not, oldest first --
    with the bye week identified by its absence rather than assumed."""
    season = _safe_int(season, int(SEASON))
    if not DATABASE_URL or not team:
        return {"games": [], "bye_week": None}
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT espn_event_id, week, kickoff, home_team, away_team,
                          home_score, away_score, status
                   FROM nfl_schedule
                   WHERE season = %s AND season_type = %s AND (home_team = %s OR away_team = %s)
                   ORDER BY week""",
                (season, season_type, team, team),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        return {"games": [], "bye_week": None}
    finally:
        conn.close()

    games = []
    for r in rows:
        is_home = r["home_team"] == team
        own = r["home_score"] if is_home else r["away_score"]
        opp_score = r["away_score"] if is_home else r["home_score"]
        # Whether a game has a result is decided by its status, not by
        # whether a score column happens to hold a number -- rows written
        # before kickoff (and older rows that stored ESPN's placeholder
        # 0-0) otherwise render as a schedule full of ties.
        played = r["status"] in ("final", "in_progress") and own is not None and opp_score is not None
        games.append({
            "id": r["espn_event_id"], "week": r["week"], "kickoff": r["kickoff"],
            "opponent": r["away_team"] if is_home else r["home_team"],
            "home": is_home,
            "score": own if played else None,
            "opp_score": opp_score if played else None,
            "status": r["status"],
            "result": (("W" if own > opp_score else ("L" if own < opp_score else "T"))
                       if played else None),
            "diff": (own - opp_score) if played else None,
        })
    played_weeks = {g["week"] for g in games}
    bye = next((w for w in range(1, SCHEDULE_WEEKS_PER_SEASON + 1) if w not in played_weeks), None)
    return {"games": games, "bye_week": bye}


# NFL teams play once a week, so a "last 7 days" window is the most
# recent week and "last 30 days" is roughly the last four. Deriving the
# windows from weeks rather than from timestamps keeps them aligned to
# games actually played -- a date-based window would sometimes catch a
# Thursday game and miss the Sunday one from the same round.
PLAYER_WINDOW_WEEKS = {"d7": 1, "d30": 4}
_player_rank_cache = {}


def get_player_window_ranks(season, cache=_player_rank_cache):
    """{sleeper_id: {d7, d30, season, alltime}} -- where each player
    ranks by fantasy points over each window.

    All-time spans every season this database holds, back to
    PERF_EARLIEST_SEASON, so it grows as the backfill does rather than
    being pinned to a fixed set."""
    season = _safe_int(season, int(SEASON))
    now = time.time()
    entry = cache.get(season)
    if entry and now - entry["time"] < 900:
        return entry["data"]

    stats = get_season_stats(season) or {}
    weeks_seen = [_safe_int(w, 0) for s in stats.values() for w in (s.get("weeks") or {})]
    latest_week = max(weeks_seen) if weeks_seen else 0

    totals = {"d7": {}, "d30": {}, "season": {}, "alltime": {}}
    for sid, stat in stats.items():
        for w, pts in (stat.get("weeks") or {}).items():
            if not isinstance(pts, (int, float)):
                continue
            wk = _safe_int(w, 0)
            totals["season"][sid] = totals["season"].get(sid, 0) + pts
            for label, span in PLAYER_WINDOW_WEEKS.items():
                if latest_week and wk > latest_week - span:
                    totals[label][sid] = totals[label].get(sid, 0) + pts

    for yr in range(season, PERF_EARLIEST_SEASON - 1, -1):
        for sid, stat in (get_season_stats(yr) or {}).items():
            for _w, pts in (stat.get("weeks") or {}).items():
                if isinstance(pts, (int, float)):
                    totals["alltime"][sid] = totals["alltime"].get(sid, 0) + pts

    out = {}
    for label, bucket in totals.items():
        for i, (sid, _pts) in enumerate(
                sorted(bucket.items(), key=lambda kv: -kv[1]), 1):
            out.setdefault(sid, {})[label] = i
    cache[season] = {"data": out, "time": now}
    return out


def get_team_roster(team, season):
    """The team's players, split offence/defence, each with their window
    ranks -- the roster view on the team page.

    Read down the page by position in depth-chart order -- quarterbacks,
    backs, receivers, tight ends -- and within each position by season
    rank, so the starter leads their own group rather than every
    quarterback being buried under the receivers. Players who have not
    played this season are gathered at the bottom as the bench, in the
    same position order."""
    if not team:
        return {"offense": [], "defense": []}
    ranks = get_player_window_ranks(season)
    out = {"offense": [], "defense": []}
    for sid, p in (get_all_players() or {}).items():
        if p.get("team") != team:
            continue
        pos = p.get("position")
        if not pos or pos not in SCORED_POSITIONS:
            continue
        r = ranks.get(sid) or {}
        out["defense" if pos in IDP_POSITIONS else "offense"].append({
            "sid": sid,
            "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position": pos,
            "number": p.get("number"),
            "status": (p.get("status") or "").title() or None,
            "injury": _injury_badge(p),
            "photo": player_photo_url(sid),
            "ranks": r,
        })
    order = {pos: i for i, pos in enumerate(POSITIONS + IDP_POSITIONS)}
    for group in out.values():
        for p in group:
            p["bench"] = not p["ranks"].get("season")
        group.sort(key=lambda x: (1 if x["bench"] else 0,
                                  order.get(x["position"], len(order)),
                                  x["ranks"].get("season") or 10**9,
                                  x["name"]))
    return out


def _utc_isoformat(dt):
    """An ISO timestamp a browser will read as UTC.

    `nfl_schedule.kickoff` is a naive TIMESTAMP holding UTC wall time.
    `new Date("2026-09-14T00:15:00")` -- no offset -- is LOCAL time by
    the ECMAScript spec, so handing the client a bare isoformat() means
    it never converts and every late kickoff lands a day late."""
    if not dt:
        return None
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return dt.astimezone(timezone.utc).isoformat()


def get_season_game_days(season, season_type=2, cache={}):
    """Every date the season has games on, as
    [{date, week, kickoff_utc}, ...] in order.

    Read from nfl_schedule rather than ESPN, because the date strip needs
    the WHOLE season at once and fetching 18 weeks from ESPN on every
    page load to build a list of tab labels would be absurd. One indexed
    query gives the entire grid.

    The dates here are UTC calendar days, used only to enumerate which
    tabs should exist -- the client still derives each tab's real local
    day from the kickoff timestamp, since a Sunday night game is already
    Monday in UTC for anyone west of the UK.

    Which is why every kickoff goes out explicitly marked as UTC. The
    column is a naive TIMESTAMP holding UTC wall time, and an ISO string
    with no offset is parsed by browsers as LOCAL time -- so without the
    marker the conversion never happens and Thursday Night Football shows
    up as a Friday tab, Monday night as a Tuesday one."""
    season, season_type = _safe_int(season, int(SEASON)), _safe_int(season_type, 2)
    key = (season, season_type)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 3600:
        return entry["data"]
    if not DATABASE_URL:
        return []
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT week, MIN(kickoff) AS first_kick
                   FROM nfl_schedule
                   WHERE season = %s AND season_type = %s AND kickoff IS NOT NULL
                   GROUP BY week, DATE(kickoff)
                   ORDER BY first_kick""",
                (season, season_type),
            )
            rows = cur.fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    days = [{"week": r["week"], "kickoff": _utc_isoformat(r["first_kick"])}
            for r in rows if r.get("first_kick")]
    cache[key] = {"data": days, "time": now}
    return days


def _nearby_weeks_games(season, week, season_type=2):
    """The requested week plus the week before and after, fetched in
    parallel -- gives the date strip several confirmed game-day tabs to
    slide through right away instead of just the ~3 days in one ESPN
    week, without eagerly pulling the whole season. Weeks below 1 are
    skipped rather than sent to ESPN (which would just 400/empty)."""
    weeks_to_fetch = [w for w in (week - 1, week, week + 1) if w >= 1]
    games = []
    with ThreadPoolExecutor(max_workers=len(weeks_to_fetch)) as executor:
        futures = [executor.submit(_week_games, season, w, season_type) for w in weeks_to_fetch]
        for f in futures:
            games.extend(f.result()[0])
    return games


def _safe_feed(fn):
    """Run a decoration feed, or give up on it quietly.

    Belt as well as braces: the feeds guard their own inputs, and this
    guards the route against anything they did not anticipate. The
    scoreboard is the page; Injuries and Birthdays are not worth it."""
    try:
        return fn() or []
    except Exception:
        return []


@app.route("/scores")
def scores_page():
    username = _resolve_scores_username()
    try:
        info = get_current_week_info()
        # The three pages built on nfl_schedule are the ones that have to
        # keep it current. This used to hang off the matchup-grade paths
        # only, so a visitor who went straight to Scores, Standings or a
        # team page never triggered a refresh and sat looking at games
        # frozen at their pre-kickoff state.
        refresh_open_schedule_weeks(info["season"])
        season = request.args.get("season", default=info["season"], type=int)
        week = request.args.get("week", default=info["week"], type=int)
        season_type = request.args.get("seasontype", default=info["season_type"], type=int)
        games = _nearby_weeks_games(season, week, season_type)
        _annotate_my_players(games, username)
        has_synced_leagues = bool(current_user.is_authenticated and get_synced_league_ids(current_user.id))
        # The whole week's board is baked in, and the client filters it to
        # the teams that played on the selected date -- see
        # get_week_performers for why the date split has to happen there.
        performers = get_week_performers(season, week, allow_fetch=False)
        # Every game day in the season, so the strip scrolls straight
        # through to any week instead of dead-ending at the fetched
        # window. Games for a day outside that window load on demand.
        season_days = get_season_game_days(season, season_type)
        return render_template_string(
            SCORES_HTML, games=games, season=season, week=week, season_type=season_type,
            score_mark=SCORE_MARK_SVG, season_days=season_days,
            current_season=info["season"], current_week=info["week"],
            today_key=date.today().isoformat(), load_error=None,
            username=username, has_synced_leagues=has_synced_leagues,
            performers=performers, power=get_power_board(season, season_type),
            perf_groups=PERFORMER_GROUPS,
            injuries=_safe_feed(get_injury_changes),
            birthdays=_safe_feed(get_birthdays_today),
        )
    except Exception as e:
        # ESPN's API is unofficial and unverified against a live response
        # from this environment -- surface the real error on the page
        # instead of a bare 500, so a shape mismatch is diagnosable from
        # a screenshot alone rather than looking like the page is dead.
        return render_template_string(
            SCORES_HTML, games=[], season=int(SEASON), week=1, season_type=2,
            score_mark=SCORE_MARK_SVG, season_days=[],
            current_season=int(SEASON), current_week=1, today_key=date.today().isoformat(),
            load_error=str(e), username=username, has_synced_leagues=False,
            performers=[], power=[], perf_groups=PERFORMER_GROUPS,
            injuries=[], birthdays=[],
        )


@app.route("/standings")
def standings_page():
    """Four views on the same season: each conference by division, the
    playoff field in seed order, and the draft order."""
    try:
        info = get_current_week_info()
        refresh_open_schedule_weeks(info["season"])
        season = request.args.get("season", default=info["season"], type=int)
        view = (request.args.get("view") or "AFC").lower()
        conf = (request.args.get("conf") or "AFC").upper()
        if conf not in CONFERENCES:
            conf = "AFC"
        if view in ("afc", "nfc"):
            conf, view = view.upper(), "conference"
        elif view not in ("playoffs", "draft"):
            view = "conference"

        standings = get_team_standings(season)
        by_div, field, draft = {}, [], []
        if view == "conference":
            for d in DIVISIONS:
                members = [r for r in standings.values()
                           if r["conference"] == conf and r["division"] == d]
                members.sort(key=_seed_order)
                by_div[d] = members
        elif view == "playoffs":
            field = get_playoff_field(season, conf)
        else:
            draft = get_draft_order(season)
        played = any(r["games"] for r in standings.values())
        return render_template_string(
            STANDINGS_HTML, by_div=by_div, field=field, draft=draft, view=view,
            conf=conf, season=season, played=played, markers=PLAYOFF_MARKERS,
            traded_emoji=TRADED_PICK_EMOJI,
            conferences=CONFERENCES, divisions=DIVISIONS, load_error=None)
    except Exception as e:
        return render_template_string(
            STANDINGS_HTML, by_div={}, field=[], draft=[], view="conference",
            conf="AFC", season=int(SEASON), played=False, markers=PLAYOFF_MARKERS,
            traded_emoji=TRADED_PICK_EMOJI,
            conferences=CONFERENCES, divisions=DIVISIONS, load_error=str(e))


@app.route("/team")
def team_page():
    """One team: record and standing, power rank over three windows,
    recent results, and the roster with each player's ranks."""
    abbr = normalize_team_abbr((request.args.get("abbr") or "").upper())
    try:
        info = get_current_week_info()
        refresh_open_schedule_weeks(info["season"])
        season = request.args.get("season", default=info["season"], type=int)
        if abbr not in NFL_DIVISIONS:
            return render_template_string(
                TEAM_HTML, team=None, season=season, load_error=None,
                all_teams=sorted(NFL_DIVISIONS))
        standings = get_team_standings(season)
        # Before a team's first game there is nothing this season to rank
        # on. Rather than three dashes, fall back to the most recent
        # season that WAS played and say so underneath -- which is how a
        # preseason ranking works anywhere else.
        rank = (get_team_rankings(season) or {}).get(abbr, {})
        rank_season = season
        if not rank:
            for back in range(1, RANK_FALLBACK_SEASONS_BACK + 1):
                prior = (get_team_rankings(season - back) or {}).get(abbr, {})
                if prior:
                    rank, rank_season = prior, season - back
                    break
        sched = get_team_schedule(abbr, season)
        row = standings.get(abbr, {})
        # Where they sit in their own division, which is what a team page
        # leads with rather than the conference seed.
        division_members = sorted(
            (r for r in standings.values()
             if r["conference"] == row.get("conference") and r["division"] == row.get("division")),
            key=lambda r: (-r["pct"], -r["diff"], -r["pf"]))
        div_rank = next((i for i, r in enumerate(division_members, 1)
                         if r["team"] == abbr), None)
        played = [g for g in sched["games"] if g["result"]]
        team = {
            "abbr": abbr, "logo": team_logo_url(abbr),
            "name": TEAM_NAMES.get(abbr, abbr),
            "conference": row.get("conference"), "division": row.get("division"),
            "record": row.get("record", "0-0"), "div_rank": div_rank,
            "bye_week": sched["bye_week"], "rank": rank, "standing": row,
            "rank_season": rank_season,
            # Played games only -- the form strip measures point
            # differentials, which an unplayed game does not have.
            "games": played,
            # The whole season, week 1 through the last, for the Games
            # tab: results where there are results, fixtures elsewhere.
            "schedule": sched["games"],
            "upcoming": [g for g in sched["games"] if not g["result"]][:3],
            "roster": get_team_roster(abbr, season),
        }
        return render_template_string(TEAM_HTML, team=team, season=season,
                                      load_error=None, all_teams=sorted(NFL_DIVISIONS))
    except Exception as e:
        return render_template_string(TEAM_HTML, team=None, season=int(SEASON),
                                      load_error=str(e), all_teams=sorted(NFL_DIVISIONS))


@app.route("/performances")
def performances_page():
    """The full performance leaderboard: every scored game, filterable by
    position and by range."""
    try:
        info = get_current_week_info()
        season = request.args.get("season", default=info["season"], type=int)
        week = request.args.get("week", default=info["week"], type=int)
        scope = request.args.get("scope", default="week")
        order = request.args.get("order", default="top")
        position = (request.args.get("position") or "").strip().upper() or None
        if position and position not in SCORED_POSITIONS:
            position = None
        rows = get_performance_board(scope=scope, position=position, season=season,
                                     week=week, order=order)
        return render_template_string(
            PERFORMANCES_HTML, rows=rows, season=season, week=week, score_mark=SCORE_MARK_SVG,
            scope=scope if scope in PERF_SCOPES else "week", order=order,
            position=position, positions=SCORED_POSITIONS,
            load_error=None,
        )
    except Exception as e:
        return render_template_string(
            PERFORMANCES_HTML, rows=[], season=int(SEASON), week=1, score_mark=SCORE_MARK_SVG,
            scope="week", order="top", position=None, positions=SCORED_POSITIONS,
            load_error=str(e),
        )


@app.route("/performance")
def performance_page():
    """One player's game: the score, how it was measured, the full stat
    line, and where it sits in their own season."""
    sid = (request.args.get("sid") or "").strip()
    try:
        info = get_current_week_info()
        season = request.args.get("season", default=info["season"], type=int)
        week = request.args.get("week", default=info["week"], type=int)
        detail = get_performance_detail(sid, season, week) if sid else None
        log = get_player_season_log(sid, season, detail["position"], through_week=week) if detail else []
        quarters = (get_player_quarter_breakdown(
            sid, season, week, score=(detail["score"] or {}).get("score")) if detail else [])
        # The strip across the top is the rest of that week's board, so
        # you can move between performances without going back first.
        peers = [p for p in get_week_performers(season, week, allow_fetch=False) if p["sid"] != sid][:24]
        return render_template_string(
            PERFORMANCE_HTML, detail=detail, log=log, peers=peers, sid=sid,
            quarters=quarters,
            season=season, week=week, load_error=None, score_mark=SCORE_MARK_SVG,
        )
    except Exception as e:
        return render_template_string(
            PERFORMANCE_HTML, detail=None, log=[], peers=[], sid=sid, quarters=[],
            season=int(SEASON), week=1, load_error=str(e), score_mark=SCORE_MARK_SVG,
        )


@app.route("/api/performers")
def api_performers():
    """The week's performer board as JSON, for the /scores live poll.

    Returns the whole week rather than one day for the same reason the
    page bakes the whole week: only the client knows the visitor's real
    local calendar day, so it does the date filtering itself. This just
    keeps the numbers and grades current while games are in progress."""
    try:
        info = get_current_week_info()
        season = request.args.get("season", default=info["season"], type=int)
        week = request.args.get("week", default=info["week"], type=int)
        return jsonify({"performers": get_week_performers(season, week),
                        "season": season, "week": week})
    except Exception as e:
        # Same contract as /api/game-live: a degraded board is fine, a 500
        # that kills the page's poll loop is not.
        return jsonify({"performers": [], "error": str(e)})


@app.route("/api/scoreboard")
def api_scoreboard():
    """JSON backing for the calendar's fallback-fetch: either a whole
    week (?season=&week=) for Prev/Next Week navigation, or a single day
    (?date=YYYYMMDD) for a month-view cell click -- whichever wasn't
    already baked into the page at load."""
    username = _resolve_scores_username()
    try:
        date_str = request.args.get("date")
        if date_str:
            data = espn_day_scoreboard(date_str)
            games = [c for c in (espn_event_to_card(ev) for ev in data.get("events", [])) if c]
            _annotate_my_players(games, username)
            return jsonify({"games": games})
        info = get_current_week_info()
        season = request.args.get("season", default=info["season"], type=int)
        week = request.args.get("week", default=info["week"], type=int)
        season_type = request.args.get("seasontype", default=info["season_type"], type=int)
        games, _ = _week_games(season, week, season_type)
        _annotate_my_players(games, username)
        return jsonify({"games": games, "season": season, "week": week, "season_type": season_type})
    except Exception as e:
        return jsonify({"games": [], "error": str(e)})


@app.route("/game")
def game_detail_page():
    event_id = request.args.get("id", "")
    try:
        summary = espn_game_summary(event_id)
        detail = extract_game_detail(summary)
        username = _resolve_scores_username()
        detail["my_players"] = _my_players_for_game(detail, username)
        _wk = get_current_week_info()
        # The game's own season and week, not today's -- opening a game
        # from week 3 should link its players to week 3.
        gw = summary_season_week(summary, _wk)
        detail["season"], detail["week"] = gw["season"], gw["week"]
        detail["plays"] = enrich_plays(extract_drive_plays(summary), gw["season"], gw["week"])
        detail["field"] = extract_field_position(summary)
        detail["box"] = attach_box_photos(extract_box_score(summary))
        detail["totals"] = extract_team_totals(summary)
        detail["momentum"] = extract_momentum(summary)
        detail["betting"] = extract_betting(summary, detail["away"]["abbr"], detail["home"]["abbr"])
        detail["info"] = extract_game_info(summary)
        attach_leader_ids(detail.get("player_leaders"))
        # The rest of the day's slate, for the strip across the top --
        # so you can move between live games without going back first.
        info = _wk
        others = [g for g in _week_games(info["season"], info["week"], info["season_type"])[0]
                  if g["id"] != event_id]
        others.sort(key=lambda g: (GAME_STATUS_ORDER.get(g["status"], 1), str(g.get("date") or "")))
        return render_template_string(GAME_DETAIL_HTML, event_id=event_id, detail=detail,
                                      others=others[:12], load_error=None)
    except Exception as e:
        empty = {"status": "scheduled", "period": None, "clock": None, "status_detail": None,
                  "venue": {}, "officials": [], "home": {"abbr": None, "name": "?", "score": None, "logo": None},
                  "away": {"abbr": None, "name": "?", "score": None, "logo": None}, "team_stats": [], "player_leaders": [],
                  "plays": [], "field": None, "box": {}, "totals": {},
                  "momentum": {"points": [], "swing": None},
                  "betting": {"lines": [], "ats": []}, "info": {},
                  "season": int(SEASON), "week": 1}
        return render_template_string(GAME_DETAIL_HTML, event_id=event_id, detail=empty,
                                      others=[], load_error=str(e))


@app.route("/api/game-live")
def api_game_live():
    """Trimmed live-poll JSON -- only what can actually change mid-game
    (score, clock, status, team stats). Venue/officials never change
    once the game starts, so the polling loop never re-fetches them."""
    event_id = request.args.get("id", "")
    try:
        summary = espn_game_summary(event_id)
        detail = extract_game_detail(summary)
        gw = summary_season_week(summary, get_current_week_info())
        return jsonify({
            "season": gw["season"], "week": gw["week"],
            "status": detail["status"], "period": detail["period"], "clock": detail["clock"],
            "status_detail": detail["status_detail"],
            "home_score": detail["home"]["score"], "away_score": detail["away"]["score"],
            "home_linescores": detail["home"]["linescores"], "away_linescores": detail["away"]["linescores"],
            "team_stats": detail["team_stats"],
            "player_leaders": attach_leader_ids(detail["player_leaders"]),
            "plays": enrich_plays(extract_drive_plays(summary), gw["season"], gw["week"]),
            "field": extract_field_position(summary),
            # The momentum curve moves on every play, so it rides the
            # poll. Betting lines and the venue do not, so they don't.
            "momentum": extract_momentum(summary),
            "box": attach_box_photos(extract_box_score(summary)),
            "totals": extract_team_totals(summary),
            "win_prob": detail["win_prob"],
        })
    except Exception as e:
        return jsonify({"status": "final", "error": str(e)})


@app.route("/matchups")
def matchups_page():
    """Standalone matchup-grade browser -- independent of any synced
    league, like Rankings. Gated behind login for now (gate-blur, same
    pattern HOME_HTML uses for guests): this is meant to become a paid
    subscription perk once Stripe exists, but that billing isn't wired
    up yet, so login is the only gate today. Swap the `is_authenticated`
    check below for an `is_member` check once it is."""
    t0 = time.monotonic()
    info = get_current_week_info()
    season = request.args.get("season", default=info["season"], type=int)
    week = request.args.get("week", default=info["week"], type=int)

    rows = []
    load_error = None
    timing = []
    if current_user.is_authenticated:
        try:
            ensure_schedule_synced(season)
            timing.append(("schedule", time.monotonic() - t0))
            t1 = time.monotonic()
            all_players = get_all_players()
            fc_players = get_fantasycalc_values(1)["players"]
            timing.append(("players", time.monotonic() - t1))
            t2 = time.monotonic()
            MAX_ROWS = 300
            # Grade highest-dynasty-value players first and stop once the
            # page is full, instead of computing a grade for every single
            # rostered-and-below player in the pool (often 1000+) before
            # ever sorting or truncating -- most of that work was thrown
            # away on every single page load.
            candidates = sorted(fc_players.items(), key=lambda kv: -(kv[1].get("value") or 0))
            for sid, v in candidates:
                if len(rows) >= MAX_ROWS:
                    break
                p = all_players.get(sid)
                if not p or v.get("position") not in POSITIONS:
                    continue
                grade = compute_matchup_grade(sid, season, week)
                if not grade:
                    continue
                rows.append({
                    "sid": sid,
                    "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                    "position": v.get("position"), "team": p.get("team") or "FA",
                    "photo": player_photo_url(sid),
                    "opponent": grade["components"].get("opponent"),
                    "grade": grade["grade"], "grade_class": grade["grade_class"], "stars": grade["stars"], "star_pct": grade["star_pct"],
                    "composite": grade["components"].get("composite", 0),
                    "reasoning": grade["reasoning"],
                    "value": v.get("value", 0),
                    # A game that's already final (or live, or a bye) isn't
                    # a start/sit decision at all -- those sort below every
                    # player who can still actually be started this week.
                    # Read with .get: one missing optional field must never
                    # take down the whole board, which a hard lookup here
                    # would do (the exception is caught page-wide and
                    # renders an empty list).
                    "decidable": 0 if (grade["components"].get("game_status") in ("final", "in_progress")
                                       or grade["components"].get("opponent") is None) else 1,
                })
            # Most startable first: best grade down to worst.
            #
            # Ranked on _GRADE_RANK (the 13-tier order of the grade
            # actually shown) rather than on composite alone, because an
            # injury override sets the grade directly -- an OUT player is
            # an F no matter how strong their underlying composite is, so
            # a pure composite sort would list them above their own
            # displayed grade. Composite then orders players who share a
            # letter (a strong B+ above a weak one), and dynasty value
            # breaks an exact tie so the order is fully deterministic
            # rather than dependent on dict iteration order.
            rows.sort(key=lambda r: (-r["decidable"], -_GRADE_RANK[r["grade"]], -r["composite"], -r["value"]))
            # Rank runs straight through both groups -- it's one ordered
            # board -- and the first row of the already-played/bye group
            # carries a flag so the template can show a divider there.
            # Without it, an A-graded finished game sitting below an F
            # would just look like the sort is broken on a board that
            # says it runs highest grade to lowest.
            seen_inactive = False
            for i, r in enumerate(rows, 1):
                r["rank"] = i
                r["starts_inactive"] = not r["decidable"] and not seen_inactive
                seen_inactive = seen_inactive or not r["decidable"]
            timing.append(("grading", time.monotonic() - t2))
        except Exception as e:
            load_error = str(e)

    t3 = time.monotonic()
    html = render_template_string(
        MATCHUPS_HTML, rows=rows, season=season, week=week,
        current_season=info["season"], current_week=info["week"], load_error=load_error,
    )
    timing.append(("render", time.monotonic() - t3))
    timing.append(("total", time.monotonic() - t0))
    resp = make_response(html)
    # Invisible on the page itself -- only shows up in a browser's
    # DevTools Network tab (Timing panel) or `curl -D -`, so this can
    # stay on in production without repeating the earlier mistake of
    # dumping diagnostic text into the rendered page. Answers "which
    # part is actually slow" with real numbers instead of another guess.
    resp.headers["Server-Timing"] = ", ".join(f"{name};dur={dur*1000:.0f}" for name, dur in timing)
    return resp


@app.route("/api/matchup-compare")
def api_matchup_compare():
    """Head-to-head start/sit call for two players -- backs the
    Matchups page's comparison tool. Auth-gated like the rest of
    matchup grading (see matchups_page's docstring re: the future
    is_member swap)."""
    if not current_user.is_authenticated:
        return jsonify({"ok": False, "error": "Sign in to compare players."}), 401
    sid_a = request.args.get("a", "")
    sid_b = request.args.get("b", "")
    if not sid_a or not sid_b:
        return jsonify({"ok": False, "error": "Pick two players to compare."}), 400
    if sid_a == sid_b:
        return jsonify({"ok": False, "error": "Pick two different players."}), 400
    info = get_current_week_info()
    season = request.args.get("season", default=info["season"], type=int)
    week = request.args.get("week", default=info["week"], type=int)
    t0 = time.monotonic()
    try:
        ensure_schedule_synced(season)
        t1 = time.monotonic()
        result = compare_matchups(sid_a, sid_b, season, week)
        t2 = time.monotonic()
        if not result:
            return jsonify({"ok": False, "error": "Couldn't grade one of those players -- try a different skill-position player."}), 400
        # Real per-phase timing, invisible to the UI (the JS never reads
        # this field) but visible in a browser's DevTools Network tab
        # under this request's Response, so "comparing players is slow"
        # can be answered with actual numbers on the very next click
        # instead of another round of guessing.
        result["_timing_ms"] = {
            "schedule_check": round((t1 - t0) * 1000),
            "compare": round((t2 - t1) * 1000),
            "total": round((time.monotonic() - t0) * 1000),
        }
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/")
@app.route("/rankings")
def rankings():
    fmt = request.args.get("format", "1qb")
    mode = request.args.get("mode", "dynasty")
    if mode not in ("dynasty", "redraft"):
        mode = "dynasty"
    is_dynasty = mode == "dynasty"
    pos_filter = request.args.get("pos", "overall")
    view = request.args.get("view", "list")
    num_qbs = 2 if fmt == "superflex" else 1
    # Show last completed season's games/points, not the current one --
    # early in the year the current season has 0 games for everyone,
    # which isn't useful to look at.
    stats_season = str(int(SEASON) - 1)

    with ThreadPoolExecutor(max_workers=3) as executor:
        fc_future = executor.submit(get_fantasycalc_values, num_qbs, is_dynasty)
        players_future = executor.submit(get_all_players)
        stats_future = executor.submit(get_season_stats, stats_season)
        fc_players = fc_future.result()["players"]
        all_players = players_future.result()
        season_stats = stats_future.result()

    prelim = []
    for sid, v in fc_players.items():
        p = all_players.get(sid)
        if not p or v.get("position") not in POSITIONS:
            continue
        stat_line = season_stats.get(sid, {})
        prelim.append({
            "sid": sid, "p": p, "v": v, "stat_line": stat_line,
            "overall_rank": v.get("overall_rank") or 9999,
            "position_rank": v.get("position_rank") or 999,
            "position": v.get("position"),
        })

    # Find the single furthest-out overall rank among top-32 QBs, and
    # push the D/F boundary out to at least cover it. One shared
    # boundary for everyone -- can't flip-flop, since it's one number,
    # not a per-row exception.
    D_CUTOFF = 100
    qb_overall_ranks = [r["overall_rank"] for r in prelim if r["position"] == "QB" and r["position_rank"] <= 32]
    if qb_overall_ranks:
        D_CUTOFF = max(D_CUTOFF, max(qb_overall_ranks))

    rows = []
    for r in prelim:
        p, v, stat_line = r["p"], r["v"], r["stat_line"]
        games = stat_line.get("games", 0)
        fpts = stat_line.get("fpts", 0.0)
        overall_rank = r["overall_rank"]
        if overall_rank <= 4:
            tier = "S"
        elif overall_rank <= 12:
            tier = "A"
        elif overall_rank <= 24:
            tier = "B"
        elif overall_rank <= 36:
            tier = "C"
        elif overall_rank <= D_CUTOFF:
            tier = "D"
        else:
            tier = "F"
        rows.append({
            "sid": r["sid"], "photo": player_photo_url(r["sid"]),
            "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position": v.get("position"), "team": p.get("team") or "FA",
            "is_rookie": p.get("years_exp") == 0,
            "age": compute_age_decimal(p.get("birth_date")),
            "games": games,
            "fpts": round(fpts, 1) if games else 0,
            "fpts_per_game": round(fpts / games, 1) if games else 0,
            "snap_pct": stat_line.get("snap_pct"),
            "position_rank": v.get("position_rank") or 999,
            "value": v.get("value", 0), "overall_rank": overall_rank,
            "tier": tier,
        })
    rows.sort(key=lambda r: r["overall_rank"])
    # 300 instead of 100 so filtering down to a single position (e.g. TE,
    # which ranks lower overall than WR/RB) still has a real list to show.
    return render_template_string(RANKINGS_HTML, rows=rows[:300], fmt=fmt, mode=mode, pos_filter=pos_filter, view=view, stats_season=stats_season)


def consolidation_adjusted_value(items):
    """Package-size value adjustment, same idea most dynasty trade
    calculators apply: a side's raw point total overstates uneven packages,
    because roster spots are scarce and a 3rd or 4th piece has less
    marginal usefulness than the 1st. Rank a side's own pieces by value and
    decay each one 8% per rank below the top piece, compounding -- a
    single-asset side is untouched (its one piece is always rank 0), while
    a side stacked with role players loses a bit of its raw sum. Comparing
    two sides' adjusted totals then naturally favors consolidating into
    fewer, bigger pieces over spreading the same value across more of
    them, without needing to know anything about the other side."""
    ranked = sorted(items, key=lambda p: p.get("value") or 0, reverse=True)
    return sum((p.get("value") or 0) * (0.92 ** i) for i, p in enumerate(ranked))


def compute_trade_state(args):
    """Everything the trade calculator page needs, computed once: parsed
    format/mode/league-size, both sides' items/totals (raw and
    package-size-adjusted), balance suggestions, and (if a Sleeper
    username is linked) that league's roster quick-add lists. Shared by
    the full page route and /api/trade-result (the lightweight JSON
    endpoint the page's own JS calls on every add/remove so the verdict
    updates without a full reload) so the two can never disagree."""
    fmt = args.get("format", "1qb")
    mode = args.get("mode", "dynasty")
    teams = args.get("teams", default=12, type=int)
    if teams not in (8, 10, 12, 14):
        teams = 12
    num_qbs = 2 if fmt == "superflex" else 1
    is_dynasty = mode != "redraft"
    side1_ids = [x for x in args.get("side1", "").split(",") if x]
    side2_ids = [x for x in args.get("side2", "").split(",") if x]

    u = args.get("u", "").strip()
    league_id = args.get("league_id", "")
    my_roster_id = args.get("my_roster_id", type=int)
    other_roster_id = args.get("other_roster_id", type=int)

    fc = get_fantasycalc_values(num_qbs, is_dynasty, teams)
    all_players = get_all_players()

    draft_picks_quick = []
    if is_dynasty:
        draft_picks_quick = [
            {"sid": p["sid"], "name": p["name"], "position": "PICK", "team": "", "photo": PICK_ICON, "value": p["value"]}
            for p in sorted_upcoming_picks(fc["picks"])
        ]

    def build_side(ids):
        items, total = [], 0
        for sid in ids:
            if sid.startswith("pick_slot_"):
                pk = resolve_slot_pick(sid, fc["picks"], teams)
                if not pk:
                    continue
                items.append({"sid": sid, "name": pk["name"], "position": "PICK", "team": "", "photo": PICK_ICON, "value": pk["value"]})
                total += pk["value"]
            elif sid.startswith("pick_"):
                pk = fc["picks"].get(sid)
                if not pk:
                    continue
                items.append({"sid": sid, "name": pk["name"], "position": "PICK", "team": "", "photo": PICK_ICON, "value": pk["value"]})
                total += pk["value"]
            else:
                p = all_players.get(sid)
                if not p:
                    continue
                val = fc["players"].get(sid, {}).get("value", 0)
                items.append({
                    "sid": sid, "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                    "position": p.get("position"), "team": p.get("team") or "FA",
                    "photo": player_photo_url(sid), "value": val,
                })
                total += val
        return items, total

    side1_items, side1_total = build_side(side1_ids)
    side2_items, side2_total = build_side(side2_ids)
    side1_adjusted = round(consolidation_adjusted_value(side1_items))
    side2_adjusted = round(consolidation_adjusted_value(side2_items))
    result = None
    if side1_ids or side2_ids:
        result = {
            "side1_items": side1_items, "side1_total": side1_total, "side1_adjusted": side1_adjusted,
            "side2_items": side2_items, "side2_total": side2_total, "side2_adjusted": side2_adjusted,
            "diff": side2_total - side1_total,
            "adjusted_diff": side2_adjusted - side1_adjusted,
        }

    # ---- optional league link ----
    # Only the brief (name-only) league list is needed for the "pick a
    # league" dropdown; the full roster/value build only ever runs for the
    # one league actually selected, instead of every league the user is
    # in -- building all of them here just to show a dropdown and score
    # one is exactly the wasted work the league-sync picker was added to
    # avoid on the league-manager page.
    league_link = None
    my_quick, other_quick = [], []
    if u:
        try:
            user_id, display_name, brief = get_leagues_brief(u)
            my_team = other_team = None
            other_teams = []
            if league_id:
                leagues_raw = get_leagues(user_id, SEASON)
                league_raw = next((lg for lg in leagues_raw if lg["league_id"] == league_id), None)
                if league_raw:
                    all_players = get_all_players()
                    league_users = {
                        lu["user_id"]: {"name": lu.get("display_name", "?"), "avatar_url": sleeper_avatar_url(lu.get("avatar"))}
                        for lu in get_league_users(league_id)
                    }
                    selected_teams = build_league_teams(league_id, league_raw, all_players, league_users, user_id)
                    my_team = next(
                        (t for t in selected_teams
                         if (my_roster_id and t["roster_id"] == my_roster_id) or (not my_roster_id and t["is_you"])),
                        None,
                    )
                    other_teams = [t for t in selected_teams if not my_team or t["roster_id"] != my_team["roster_id"]]
                    if other_roster_id:
                        other_team = next((t for t in selected_teams if t["roster_id"] == other_roster_id), None)
                    if my_team:
                        my_quick = quick_add_list(my_team, fc["players"])
                    if other_team:
                        other_quick = quick_add_list(other_team, fc["players"])
            league_link = {
                "username": u,
                "leagues": [{"league_id": b["league_id"], "league_name": b["name"]} for b in brief],
                "selected_league_id": league_id,
                "my_team": my_team, "other_team": other_team, "other_teams": other_teams,
            }
        except Exception as e:
            league_link = {"error": str(e), "username": u}

    # ---- balance suggestions ----
    suggestions = []
    if result and result["adjusted_diff"] != 0 and (my_quick or other_quick):
        gap = abs(result["adjusted_diff"])
        if result["adjusted_diff"] > 0:
            pool = [p for p in my_quick if p["sid"] not in side1_ids]
        else:
            pool = [p for p in other_quick if p["sid"] not in side2_ids]
        pool_sorted = sorted(pool, key=lambda p: abs((p["value"] or 0) - gap))
        suggestions = pool_sorted[:3]

    return {
        "fmt": fmt, "mode": mode, "teams": teams,
        "side1_ids": side1_ids, "side2_ids": side2_ids,
        "result": result, "suggestions": suggestions,
        "league_link": league_link, "my_quick": my_quick, "other_quick": other_quick,
        "draft_picks_quick": draft_picks_quick,
        "u": u, "league_id": league_id, "my_roster_id": my_roster_id, "other_roster_id": other_roster_id,
    }


@app.route("/trade-calculator")
def trade_calculator():
    s = compute_trade_state(request.args)
    return render_template_string(
        TRADE_CALC_HTML, result=s["result"], fmt=s["fmt"], mode=s["mode"], teams=s["teams"],
        side1_ids=",".join(s["side1_ids"]), side2_ids=",".join(s["side2_ids"]),
        league_link=s["league_link"], my_quick=s["my_quick"], other_quick=s["other_quick"],
        draft_picks_quick=s["draft_picks_quick"],
        suggestions=s["suggestions"], u=s["u"], league_id=s["league_id"],
        my_roster_id=s["my_roster_id"], other_roster_id=s["other_roster_id"],
    )


@app.route("/api/trade-result")
def api_trade_result():
    """JSON companion to /trade-calculator: the same calculation, no HTML.
    The trade calculator's own JS calls this on every add/remove so the
    verdict/adjusted-totals/suggestions update in place instead of a full
    page reload. Deliberately omits side1_items/side2_items (the client
    already has those in its own selected1/selected2 state) and
    my_quick/other_quick/draft_picks_quick (those don't depend on
    side1/side2 at all, so there's no reason to resend them on every
    recalc) -- keeps the payload to just what actually changes."""
    s = compute_trade_state(request.args)
    result = s["result"]
    result_json = None
    if result:
        result_json = {
            "side1_total": result["side1_total"], "side1_adjusted": result["side1_adjusted"],
            "side2_total": result["side2_total"], "side2_adjusted": result["side2_adjusted"],
            "diff": result["diff"], "adjusted_diff": result["adjusted_diff"],
        }
    return jsonify({"result": result_json, "suggestions": s["suggestions"]})


def _secret_ok():
    """Shared auth check for every /api/* job endpoint.

    Also accepts the secret in an X-Sync-Secret header, not just
    ?secret=, so a scheduled job doesn't have to carry a credential in a
    URL (query strings end up in access logs and referrers; headers
    don't). The query param stays supported -- manual curl calls and
    existing bookmarks use it.

    An unset SITE_PASSWORD authorizes nothing. Without that guard an
    empty ?secret= would compare equal to an empty password and leave
    every sync endpoint open to anyone."""
    if not SITE_PASSWORD:
        return False
    provided = request.headers.get("X-Sync-Secret") or request.args.get("secret")
    return provided == SITE_PASSWORD


@app.route("/api/debug-news")
def api_debug_news():
    """Temporary diagnostic endpoint -- shows exactly what's in the two
    news feeds right now and how a given player name/team matches (or
    doesn't) against them, so a "why is there no news for X" report can
    be root-caused against live feed content instead of guessed at from
    a sandbox that can't reach these feeds itself. Remove once news
    matching is confirmed working end to end."""
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    name = request.args.get("name", "").strip()
    team = request.args.get("team", "").strip()
    try:
        espn_items = get_espn_nfl_news()
        roto_items = get_rotowire_nfl_news()
        all_items = espn_items + roto_items
        result = {
            "espn_item_count": len(espn_items),
            "rotowire_item_count": len(roto_items),
            "espn_sample_titles": [it["title"] for it in espn_items[:8]],
            "rotowire_sample_titles": [it["title"] for it in roto_items[:8]],
        }
        if name:
            last = name.split()[-1].lower() if name.split() else ""
            raw_last_name_hits = [
                {"title": it["title"], "source": it["source"], "desc": it["desc"][:150]}
                for it in all_items
                if last and last in f"{it['title']} {it['desc']}".lower()
            ]
            result["queried_name"] = name
            result["queried_team"] = team
            result["raw_last_name_substring_hits"] = raw_last_name_hits
            result["get_player_news_result"] = get_player_news(name, team)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/debug-fantasycalc")
def api_debug_fantasycalc():
    """Temporary diagnostic endpoint -- shows exactly what
    get_fantasycalc_values() parsed (pick count, a sample, and the years
    present) plus a couple of raw items straight from FantasyCalc's API,
    so a live "why are there no picks" report can be root-caused instead
    of guessed at. Remove once picks are confirmed working end to end."""
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    num_qbs = 2 if request.args.get("format", "1qb") == "superflex" else 1
    teams = request.args.get("teams", default=12, type=int)
    try:
        raw = requests.get(FANTASYCALC_BASE, params={
            "isDynasty": "true", "numQbs": num_qbs, "numTeams": teams, "ppr": 1,
        }, timeout=15)
        raw_items = raw.json()
        raw_non_offense_sample = [
            item for item in raw_items
            if (item.get("player") or {}).get("position") not in POSITIONS
        ][:5]
        fc = get_fantasycalc_values(num_qbs, True, teams)
        picks = list(fc["picks"].values())
        return jsonify({
            "raw_status_code": raw.status_code,
            "raw_item_count": len(raw_items),
            "raw_non_offense_sample": raw_non_offense_sample,
            "parsed_player_count": len(fc["players"]),
            "parsed_pick_count": len(picks),
            "parsed_pick_years_present": sorted({p["year"] for p in picks if p["year"] is not None}),
            "parsed_pick_sample": sorted(picks, key=pick_sort_key)[:12],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/debug-sleeper")
def api_debug_sleeper():
    """Temporary diagnostic endpoint -- shows the actual raw response
    from Sleeper's stats endpoint so we can see exactly what's coming
    back, instead of guessing. Remove once the real sync is confirmed
    working."""
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    season = request.args.get("season", default=int(SEASON), type=int)
    week = request.args.get("week", default=1, type=int)
    url = f"https://api.sleeper.com/stats/nfl/{season}/{week}"
    try:
        r = requests.get(url, params={"season_type": "regular"}, timeout=10)
        return jsonify({
            "requested_url": r.url,
            "status_code": r.status_code,
            "body_preview": r.text[:800],
            "body_type": str(type(r.json())) if r.headers.get("content-type", "").startswith("application/json") else "not json",
        })
    except Exception as e:
        return jsonify({"requested_url": url, "error": str(e)})


@app.route("/api/debug-espn")
def api_debug_espn():
    """Temporary diagnostic endpoint -- ESPN's live-scores API is
    undocumented, and this sandbox's outbound network can't reach it at
    all during development, so this is how the real response shapes get
    confirmed once deployed (Render has full internet access). Shows the
    raw body plus a best-effort probe of the specific fields the live
    scores / officials / box-score features depend on, so a quick look
    here answers "does this field exist and what's it actually called"
    without guessing. Remove once Milestones 1-5 are confirmed working
    against real data.

    ?endpoint=scoreboard (default): pass season/week/seasontype
    ?endpoint=summary: pass event=<espn_event_id>
    """
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    endpoint = request.args.get("endpoint", "scoreboard")
    try:
        if endpoint == "summary":
            event_id = request.args.get("event", "")
            r = requests.get(f"{ESPN_SITE_BASE}/summary", params={"event": event_id}, timeout=15)
            body = r.json()
            comp = (((body.get("header") or {}).get("competitions") or [{}])[0])
            box = body.get("boxscore") or {}
            game_info = body.get("gameInfo") or {}
            probe = {
                "top_level_keys": sorted(body.keys()),
                "header_competition_keys": sorted(comp.keys()),
                "status_type_state": (comp.get("status") or {}).get("type", {}).get("state"),
                "game_info_keys": sorted(game_info.keys()),
                "venue_raw": game_info.get("venue") or comp.get("venue"),
                "officials_raw": game_info.get("officials") or comp.get("officials") or box.get("officials"),
                "boxscore_keys": sorted(box.keys()),
                "boxscore_teams_sample": (box.get("teams") or [None])[0],
                "leaders_sample": (body.get("leaders") or [None])[0],
                "extract_game_detail_result": extract_game_detail(body),
            }
        else:
            season = request.args.get("season", default=int(SEASON), type=int)
            week = request.args.get("week", default=1, type=int)
            season_type = request.args.get("seasontype", default=2, type=int)
            r = requests.get(
                f"{ESPN_SITE_BASE}/scoreboard",
                params={"week": week, "seasontype": season_type, "dates": season},
                timeout=15,
            )
            body = r.json()
            events = body.get("events", [])
            probe = {
                "top_level_keys": sorted(body.keys()),
                "event_count": len(events),
                "first_event_keys": sorted(events[0].keys()) if events else [],
                "first_event_competitors": (
                    (events[0].get("competitions") or [{}])[0].get("competitors") if events else None
                ),
                "first_event_status": (events[0].get("status") if events else None),
            }
        return jsonify({
            "requested_url": r.url,
            "status_code": r.status_code,
            "probe": probe,
            "body_preview": r.text[:3000],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


_sync_lock = threading.Lock()
_sync_busy = False


@app.route("/api/sync-stats", methods=["GET", "POST"])
def api_sync_stats():
    """Protected endpoint the scheduled sync job calls. Not meant for
    browsers -- requires the site password as a shared secret.

    Returns immediately and does the actual fetch+save in a background
    thread -- holding the HTTP request open for the whole duration was
    tying up the server's single worker on Render's free tier, making
    the entire site unresponsive to real visitors while a sync ran. Also
    refuses to start a second sync while one's already running, since
    the workflow now calls this for every season in quick succession and
    letting them all run at once would fight over the same limited CPU."""
    global _sync_busy
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    season = request.args.get("season", default=int(SEASON), type=int)

    with _sync_lock:
        if _sync_busy:
            return jsonify({"ok": True, "season": season, "skipped": "another sync already running, try again shortly"})
        _sync_busy = True

    def _run():
        global _sync_busy
        try:
            sync_season_to_db(season)
        except Exception:
            pass
        finally:
            with _sync_lock:
                _sync_busy = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "season": season, "started": True})


# Global ceiling on how many 18-week background syncs may run at once,
# across ALL seasons and both kinds (schedule and stats).
#
# The per-season locks below already stop two triggers for the SAME
# season from racing, but nothing stopped five DIFFERENT seasons from
# each starting their own schedule sync and their own stats sync -- up
# to ten concurrent threads, each walking 18 weeks of outbound HTTP,
# every one of them kicked off by a single visitor's page load. That is
# what actually took /matchups offline when DEF_HISTORY_SEASONS_BACK was
# first raised to 4; the thin CPU allowance made it fatal rather than
# merely wasteful, so more CPU alone would hide this rather than fix it.
#
# Acquired INSIDE the worker thread, never by the caller, so a request
# still returns instantly -- queued syncs simply wait their turn in the
# background. The per-season busy flag stays set for the whole wait, so
# queueing never lets a duplicate of the same season slip past.
_BACKGROUND_SYNC_SLOTS = threading.BoundedSemaphore(2)


_schedule_sync_lock = threading.Lock()
_schedule_sync_busy_seasons = set()
_schedule_sync_last_result = {}
# Keyed by season, NOT a single shared flag -- syncing the current
# season (the recurring 2-hour cron) and backfilling a prior season (the
# matchup-grade fallback's self-heal) are unrelated operations. A single
# shared busy flag meant whichever one happened to be running blocked the
# other from ever starting, so a last-year backfill could keep losing the
# race against the current season's own recurring sync indefinitely and
# never get a clear window to run -- exactly the kind of silent, hard-to-
# diagnose gap that left last season's defense data permanently empty.


def _sync_full_season_schedule_background(season):
    """Kicks off a background thread syncing every week of `season` into
    nfl_schedule, guarded by a per-season single-flight lock so two
    triggers for the SAME season (the cron, the auto-heal check below, a
    manual dispatch) never run concurrently, while different seasons are
    always free to run at the same time. Returns immediately either way
    -- never blocks the caller on a live fetch, the same lesson
    get_season_stats already learned the hard way (see its docstring)."""
    with _schedule_sync_lock:
        if season in _schedule_sync_busy_seasons:
            return False
        _schedule_sync_busy_seasons.add(season)

    def _run():
        weeks_synced = 0
        last_error = None
        zero_row_probe = None
        # See _BACKGROUND_SYNC_SLOTS: one page load can now want several
        # seasons at once, and 18 weeks of outbound fetches per season is
        # exactly the work that must not all run at once. Acquired here,
        # inside the thread, rather than in the caller -- the request
        # that triggered this still returns immediately either way.
        _BACKGROUND_SYNC_SLOTS.acquire()
        try:
            for week in range(1, 19):
                # One bad week (a transient ESPN hiccup, a rate limit, a
                # week that legitimately doesn't exist) must not abort the
                # other 17 -- this used to be one try/except around the
                # whole loop, so a single failure silently zeroed out the
                # entire backfill and the next trigger would just repeat
                # the same failure forever, leaving last season's defense
                # data permanently empty.
                try:
                    rows = sync_week_schedule_to_db(season, week)
                    if rows:
                        weeks_synced += 1
                    elif zero_row_probe is None:
                        # sync_week_schedule_to_db goes through
                        # espn_week_scoreboard, which swallows every
                        # request error and returns an empty events list
                        # -- indistinguishable here from a genuine "no
                        # games this week". Probe ESPN directly, uncached,
                        # once per run, so a real failure (wrong params
                        # for a completed past season, a non-200, a
                        # reshaped body) is captured instead of just
                        # "0 rows, no idea why" -- this background path
                        # has never had this visibility before, unlike
                        # the manual /api/sync-schedule-now endpoint.
                        try:
                            probe = requests.get(
                                f"{ESPN_SITE_BASE}/scoreboard",
                                params={"week": week, "seasontype": 2, "dates": season},
                                timeout=15,
                            )
                            zero_row_probe = {
                                "week": week, "status": probe.status_code,
                                "event_count": len(probe.json().get("events", [])) if probe.ok else None,
                            }
                        except Exception as probe_e:
                            zero_row_probe = {"week": week, "probe_error": str(probe_e)}
                except Exception as e:
                    last_error = f"week {week}: {e}"
        finally:
            _BACKGROUND_SYNC_SLOTS.release()
            with _schedule_sync_lock:
                _schedule_sync_busy_seasons.discard(season)
            # Every prior fix here (loop-abort, lock contention, partial-
            # backfill detection, cache staleness) turned out to be real
            # but each only got the page a step closer -- and this
            # background path swallows every per-week exception, so a
            # SYSTEMATIC failure (every week for this season erroring or
            # coming back with 0 rows) has never once been visible
            # anywhere. Recording the outcome here means the next look at
            # /matchups shows a real reason instead of another guess.
            _schedule_sync_last_result[season] = {
                "weeks_synced": weeks_synced, "last_error": last_error,
                "zero_row_probe": zero_row_probe, "time": time.time(),
            }
            # A completed backfill writes straight to the DB, but
            # get_defense_vs_position/compute_matchup_grade each cache
            # their own results in memory for up to an hour -- without
            # clearing them here, a genuinely successful sync would still
            # leave every matchup grade serving its old cached answer
            # (computed before this ran) for up to an hour with no
            # outward sign the underlying data had actually changed.
            _defense_vs_position_cache.clear()
            _matchup_grade_cache.clear()

    threading.Thread(target=_run, daemon=True).start()
    return True


# How often a live week is worth re-pulling from ESPN. Games change on
# the order of a play, but a page's standings and rankings do not need to
# be second-accurate, and this is a shared upstream nobody is paying for.
_SCHEDULE_REFRESH_COOLDOWN_S = 120
# How many weeks one refresh pass may pull. A site that was down for a
# month has a month of weeks sitting at their pre-kickoff state; this
# lets it catch up a few weeks per pass rather than firing eighteen
# outbound calls at once, and the cooldown means it converges in minutes.
_SCHEDULE_REFRESH_MAX_WEEKS = 3
_schedule_refresh_last = {}
_schedule_refresh_busy = set()
_schedule_refresh_lock = threading.Lock()


def _open_weeks(season, weeks, season_type=2):
    """Of `weeks`, the ones still worth re-pulling: a week with no rows
    yet, or one still holding a game that has not finished.

    One grouped query over an indexed column, so asking is far cheaper
    than fetching."""
    if not DATABASE_URL or not weeks:
        return []
    weeks = [w for w in weeks if 1 <= w <= SCHEDULE_WEEKS_PER_SEASON]
    if not weeks:
        return []
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT week, COUNT(*) FILTER (WHERE status <> 'final') AS unfinished
                   FROM nfl_schedule
                   WHERE season = %s AND season_type = %s AND week = ANY(%s)
                   GROUP BY week""",
                (season, season_type, weeks),
            )
            seen = {r["week"]: r["unfinished"] for r in cur.fetchall()}
    except Exception:
        return []
    finally:
        conn.close()
    # A week with no rows at all is missing, not finished.
    return [w for w in weeks if seen.get(w) is None or seen[w]]


def refresh_open_schedule_weeks(season, season_type=2):
    """Re-pull the weeks whose games are still moving, in the background.

    This is what makes scores, standings and rankings fill themselves in
    without anything scheduled having to fire. ensure_schedule_synced
    below only ever asked "does every week have ROWS yet" -- and rows for
    a week exist from the moment its schedule is published, days before
    kickoff. So once a week was seeded the season was marked done and its
    scores were never fetched again: every game sat at its pre-kickoff
    state forever, standings stayed empty, and the power ranking had
    nothing to rank. The only thing that would have refreshed it was the
    cron, which fails silently whenever its secret is unset.

    Every week up to the current one is a candidate, newest first, a few
    per pass. That covers both the week being played right now and any
    earlier week the site never got a second look at -- and the week
    before the current one specifically, since a Monday night game is
    still being played after ESPN has rolled the week number over."""
    season, season_type = _safe_int(season, int(SEASON)), _safe_int(season_type, 2)
    if not DATABASE_URL:
        return False
    key = (season, season_type)
    now = time.time()
    last = _schedule_refresh_last.get(key)
    if last is not None and now - last < _SCHEDULE_REFRESH_COOLDOWN_S:
        return False
    _schedule_refresh_last[key] = now

    info = get_current_week_info()
    if season != info["season"]:
        return False   # a finished season has nothing left to move
    # Every week up to now, not just this one: a week seeded before its
    # kickoff and never looked at again sits at 0-0 forever, and that is
    # exactly the state this is here to clear. Newest first, so the week
    # being played is always the one that gets fixed first.
    weeks = _open_weeks(season, list(range(1, info["week"] + 1)), season_type)
    if not weeks:
        return False
    weeks = sorted(weeks, reverse=True)[:_SCHEDULE_REFRESH_MAX_WEEKS]

    with _schedule_refresh_lock:
        if key in _schedule_refresh_busy:
            return False
        _schedule_refresh_busy.add(key)

    def _run():
        _BACKGROUND_SYNC_SLOTS.acquire()
        changed = False
        try:
            for week in weeks:
                try:
                    if sync_week_schedule_to_db(season, week, season_type):
                        changed = True
                except Exception:
                    continue   # one bad week must not cost the others
        finally:
            _BACKGROUND_SYNC_SLOTS.release()
            with _schedule_refresh_lock:
                _schedule_refresh_busy.discard(key)
        if changed:
            # Everything derived from the schedule caches its own answer
            # for minutes at a time; without clearing them a successful
            # refresh would still serve the pre-kickoff numbers.
            _standings_cache.clear()
            _team_rank_cache.clear()
            _defense_vs_position_cache.clear()
            _matchup_grade_cache.clear()

    threading.Thread(target=_run, daemon=True).start()
    return True


_schedule_seeded_seasons = set()


SCHEDULE_WEEKS_PER_SEASON = 18


def ensure_schedule_synced(season):
    """Self-heals the common "just deployed, the 2-hour cron hasn't
    fired yet" gap: if nfl_schedule doesn't have (nearly) every week of
    this season yet, kick off a background sync so the page renders
    honestly (a real bye, just not-yet-synced) now and correctly on the
    next request or two, without ever blocking this render on a live
    fetch. Checks an in-memory set first so a season already confirmed
    complete this process never re-queries the DB on every request.

    Counts DISTINCT weeks present, not just "any row at all" -- a
    backfill that only got partway through before the process restarted
    (a real risk on a free-tier host that can recycle mid-request) would
    otherwise look "seeded" forever after syncing just one or two weeks,
    permanently stranding the other 16-17 with no data and no further
    retries for the rest of this process's life."""
    if not DATABASE_URL:
        return
    # Runs even for a season already seeded -- seeded means "every week
    # has rows", which says nothing about whether their scores are
    # current. This is the half that keeps the site up to date on its own.
    refresh_open_schedule_weeks(season)
    if season in _schedule_seeded_seasons:
        return
    if not _seed_probe_due("schedule", season):
        return
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT week) AS n FROM nfl_schedule WHERE season = %s", (season,))
            weeks_present = (cur.fetchone() or {}).get("n", 0)
    finally:
        conn.close()
    # A season in progress won't have all 18 weeks yet (that's correct,
    # not a gap) -- only require "as many weeks as have actually
    # happened" for the current season, but the full season for any
    # prior (fully completed) one.
    info = get_current_week_info()
    weeks_expected = SCHEDULE_WEEKS_PER_SEASON if season < info["season"] else min(info["week"], SCHEDULE_WEEKS_PER_SEASON)
    if weeks_present >= weeks_expected:
        _schedule_seeded_seasons.add(season)
    else:
        _sync_full_season_schedule_background(season)


@app.route("/api/sync-schedule", methods=["GET", "POST"])
def api_sync_schedule():
    """Protected endpoint the scheduled sync job calls to refresh
    nfl_schedule for every week of a season (defaults to the current
    season) -- same background-thread + single-flight-lock shape as
    /api/sync-stats, kept as a separate lock so a schedule sync and a
    stats sync never block each other."""
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    season = request.args.get("season", default=int(SEASON), type=int)
    started = _sync_full_season_schedule_background(season)
    if not started:
        return jsonify({"ok": True, "season": season, "skipped": "another schedule sync already running"})
    return jsonify({"ok": True, "season": season, "started": True})


@app.route("/api/sync-schedule-now", methods=["GET", "POST"])
def api_sync_schedule_now():
    """Protected, SYNCHRONOUS variant of /api/sync-schedule: blocks the
    request for however long an 18-week backfill actually takes and
    returns the real per-week outcome (rows written or the exact
    exception) in the response, instead of firing a background thread
    whose success or failure is invisible to whoever triggered it.

    This exists because the background-thread self-heal has repeatedly
    "should have worked" without producing visible results, with no way
    to tell from the outside whether it ran at all, died partway through,
    or never started (e.g. DATABASE_URL missing, a lock never releasing).
    Hitting this URL directly answers that in one request: either it
    reports weeks_synced close to 18 and the problem is now fixed, or it
    reports exactly which week failed and why, which is the fastest way
    to find the REAL remaining blocker instead of guessing again.

    Deliberately NOT used by the recurring cron or any page's self-heal
    -- only for a manual, one-off trigger, since blocking a request for
    this long is the wrong tradeoff for routine traffic."""
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if not DATABASE_URL:
        return jsonify({"ok": False, "error": "DATABASE_URL is not configured on this deploy -- there is no database to sync into"})
    season = request.args.get("season", default=int(SEASON), type=int)
    season_type = request.args.get("seasontype", default=2, type=int)
    detail = {}
    for week in range(1, 19):
        try:
            rows = sync_week_schedule_to_db(season, week, season_type)
            entry = {"ok": True, "rows": rows}
            if rows == 0:
                # sync_week_schedule_to_db goes through espn_week_scoreboard,
                # which swallows every request error and returns an empty
                # events list -- indistinguishable from a genuine "no games
                # this week". Probe ESPN directly, uncached, so a REAL
                # failure (wrong params for a completed past season, a
                # non-200, a reshaped body) is visible here instead of
                # just "0 rows, no idea why".
                try:
                    probe = requests.get(
                        f"{ESPN_SITE_BASE}/scoreboard",
                        params={"week": week, "seasontype": season_type, "dates": season},
                        timeout=15,
                    )
                    entry["probe_http_status"] = probe.status_code
                    try:
                        entry["probe_raw_event_count"] = len(probe.json().get("events", []))
                    except Exception:
                        entry["probe_body_preview"] = probe.text[:300]
                except Exception as probe_e:
                    entry["probe_error"] = str(probe_e)
            detail[week] = entry
        except Exception as e:
            detail[week] = {"ok": False, "error": str(e)}
    weeks_with_rows = sum(1 for r in detail.values() if r.get("ok") and r.get("rows"))
    _schedule_seeded_seasons.discard(season)  # force a fresh completeness check on the next request
    # A manual sync writes straight to the DB, but get_defense_vs_position
    # and compute_matchup_grade each cache their own results in memory for
    # up to an hour -- without clearing them here, the DB would already
    # have the fresh schedule while every grade/matchup page kept serving
    # the exact same stale "not enough data" result it had computed and
    # cached before this sync ran, for up to an hour with no visible sign
    # anything had changed.
    _defense_vs_position_cache.clear()
    _matchup_grade_cache.clear()
    return jsonify({"ok": True, "season": season, "weeks_with_rows": weeks_with_rows, "detail": detail})


@app.route("/api/defense-vs-position-debug")
def api_defense_vs_position_debug():
    """Protected, read-only: why get_defense_vs_position(season) might
    still have gaps even with a fully-synced schedule. A synced schedule
    is only half the join -- the other half is get_season_stats(season)
    (a completely separate table, player_stats) and matching each
    player's CURRENT team abbreviation (Sleeper's convention) against
    what's stored in nfl_schedule (ESPN's, run through
    normalize_team_abbr -- which so far only maps WSH -> WAS and was
    never verified against a real ESPN response for every other team,
    per the project's own original open-risks list). A mismatch on any
    other team would silently zero out that team's entries with no
    error anywhere. This reports, for one season: how many players have
    any stats at all, which of the 32 real team abbreviations never show
    up as a schedule row's home/away team, and exactly which
    teams have zero position entries in the computed defense table --
    that last list is the direct answer to "why does this specific
    team's matchup still say no data"."""
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    season = request.args.get("season", default=int(SEASON) - 1, type=int)

    season_stats = get_season_stats(season)
    all_players = get_all_players()
    player_teams = {p.get("team") for sid, p in all_players.items() if sid in season_stats and p.get("team")}

    schedule_teams = set()
    if DATABASE_URL:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT home_team FROM nfl_schedule WHERE season = %s", (season,))
                schedule_teams.update(r["home_team"] for r in cur.fetchall())
                cur.execute("SELECT DISTINCT away_team FROM nfl_schedule WHERE season = %s", (season,))
                schedule_teams.update(r["away_team"] for r in cur.fetchall())
        finally:
            conn.close()

    # A player's CURRENT team that never once appears in the season's
    # schedule table is exactly the abbreviation-mismatch signature: the
    # team objectively played 17 games that season, so its real
    # abbreviation MUST appear in nfl_schedule somewhere unless
    # normalize_team_abbr is spelling it differently than Sleeper does.
    unmatched_player_teams = sorted(player_teams - schedule_teams)

    dvp = get_defense_vs_position(season)
    coverage = {pos: sorted(team for team in dvp if pos in dvp[team]) for pos in POSITIONS}
    teams_with_any_position_data = {team for team in dvp if dvp[team]}
    teams_with_zero_data = sorted(schedule_teams - teams_with_any_position_data)

    return jsonify({
        "ok": True, "season": season,
        "players_with_season_stats": len(season_stats),
        "distinct_current_teams_among_those_players": len(player_teams),
        "distinct_teams_in_schedule_table": len(schedule_teams),
        "player_teams_never_seen_in_schedule": unmatched_player_teams,
        "teams_with_zero_defense_data_despite_being_in_schedule": teams_with_zero_data,
        "position_coverage_team_counts": {pos: len(teams) for pos, teams in coverage.items()},
    })


@app.route("/api/schedule-status")
def api_schedule_status():
    """Protected, read-only: how many distinct weeks of nfl_schedule
    actually exist for each of the last few seasons, straight from the
    DB -- lets a real check (hitting this URL) confirm whether a
    season's backfill has genuinely finished instead of guessing from
    what /matchups shows. Also reports whether a sync is in-flight for
    that season right now, since the answer might just be "still
    running, check back in a minute" rather than a bug."""
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if not DATABASE_URL:
        return jsonify({"ok": True, "database_configured": False, "seasons": {}})
    info = get_current_week_info()
    seasons_to_check = request.args.get("seasons")
    seasons = [int(s) for s in seasons_to_check.split(",")] if seasons_to_check else [info["season"], info["season"] - 1, info["season"] - 2]
    conn = get_db()
    try:
        with conn.cursor() as cur:
            result = {}
            for season in seasons:
                cur.execute(
                    "SELECT COUNT(DISTINCT week) AS n, COUNT(*) AS rows FROM nfl_schedule WHERE season = %s",
                    (season,),
                )
                row = cur.fetchone() or {"n": 0, "rows": 0}
                weeks_expected = SCHEDULE_WEEKS_PER_SEASON if season < info["season"] else min(info["week"], SCHEDULE_WEEKS_PER_SEASON)
                result[str(season)] = {
                    "weeks_present": row["n"], "weeks_expected": weeks_expected,
                    "rows": row["rows"], "complete": row["n"] >= weeks_expected,
                    "sync_in_progress": season in _schedule_sync_busy_seasons,
                }
    finally:
        conn.close()
    return jsonify({"ok": True, "database_configured": True, "current_season": info["season"], "seasons": result})


@app.route("/api/schedule-week-ids")
def api_schedule_week_ids():
    """Protected, read-only: the espn_event_id list for one season/week,
    straight from our own DB. Used by the referee backfill workflow to
    discover which games to sync without embedding ESPN-parsing logic in
    a shell script."""
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    season = request.args.get("season", default=int(SEASON), type=int)
    week = request.args.get("week", default=1, type=int)
    if not DATABASE_URL:
        return jsonify({"ok": True, "ids": []})
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT espn_event_id FROM nfl_schedule WHERE season = %s AND week = %s",
                (season, week),
            )
            ids = [r["espn_event_id"] for r in cur.fetchall()]
    finally:
        conn.close()
    return jsonify({"ok": True, "ids": ids})


@app.route("/api/sync-referee-game", methods=["GET", "POST"])
def api_sync_referee_game():
    """Protected: syncs one game's officiating/penalty data into
    referee_games. Called per-game, in a loop, by the one-time backfill
    workflow -- runs synchronously (a single ESPN call, well under
    gunicorn's timeout) rather than via a background thread, so the
    workflow's own per-game logging reflects real success/failure
    instead of racing a detached thread."""
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    event_id = request.args.get("id", "")
    season = request.args.get("season", default=int(SEASON), type=int)
    week = request.args.get("week", default=1, type=int)
    if not event_id:
        return jsonify({"ok": False, "error": "missing id"}), 400
    try:
        ok = sync_referee_game(event_id, season, week)
        return jsonify({"ok": ok, "event_id": event_id})
    except Exception as e:
        return jsonify({"ok": False, "event_id": event_id, "error": str(e)})


@app.route("/healthz")
def healthz():
    """Public, does-nothing-but-200 endpoint for uptime/keep-alive pings --
    deliberately cheap so it can't be abused, but hitting it on a schedule
    keeps Render's free tier from spinning the worker down between real
    visitors."""
    return jsonify({"ok": True})


@app.route("/api/warm", methods=["GET", "POST"])
def api_warm():
    """Companion to /healthz: actually refreshes the in-memory caches
    (players, trade values, ADP) in the background so a real visitor is
    never the one who pays a cold-fetch cost. Each of these already no-ops
    unless its own TTL has expired, so calling them on a schedule is cheap.
    Secret-protected like /api/sync-stats since it triggers real outbound
    API calls."""
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    def _run():
        try:
            get_all_players()
            get_adp_data()
            # All (format x mode x league-size) combos the trade calculator
            # actually offers -- so switching to an 8/10/14-team league
            # never makes a real visitor eat a cold FantasyCalc fetch
            # either, same as the 12-team default already got.
            for num_qbs in (1, 2):
                for is_dynasty in (True, False):
                    for num_teams in (8, 10, 12, 14):
                        get_fantasycalc_values(num_qbs, is_dynasty, num_teams)
            # Live-scores/matchup-grade caches -- no separate keep-warm cron
            # for these (a 15-20s poll from an open /game page already keeps
            # that one warm on its own), just piggyback the cheap ones onto
            # this existing 12-minute ping so the first visitor of the day
            # never eats a cold ESPN fetch or a cold defense/referee
            # aggregate query either.
            info = get_current_week_info()
            espn_week_scoreboard(info["season"], info["week"], info["season_type"])
            # /scores bakes the performer board only when it's already
            # cached (it must never block a render on a live fetch), so
            # this is what actually keeps it populated.
            get_week_performers(info["season"], info["week"])
            # Matchup grading searches back up to DEF_HISTORY_SEASONS_BACK
            # prior seasons (plus a league-average fallback built from
            # whichever of those has any data) any time a specific
            # opponent's current-year sample is too thin, and head-to-head
            # history reaches back further still. Warm every season either
            # chain can reach.
            #
            # This is the part that matters most now that those depths are
            # back up: a season nobody has touched yet needs its schedule
            # and stats seeded, and whoever asks first is the one who pays
            # for discovering that. Doing it here means that "first asker"
            # is this scheduled job rather than a real visitor's page load,
            # which is what turns a deep history search from a latency
            # problem into a background one. _BACKGROUND_SYNC_SLOTS keeps
            # the seeding itself from running more than two seasons at a
            # time regardless.
            warm_depth = max(DEF_HISTORY_SEASONS_BACK, H2H_SEASONS_BACK)
            for offset in range(0, warm_depth + 1):
                yr = int(SEASON) - offset
                ensure_schedule_synced(yr)
                ensure_season_stats_synced(yr)
            # Only the defense-history depth needs the (more expensive)
            # per-team aggregate computed and cached; the deeper H2H
            # seasons just need their raw rows present, seeded above.
            for offset in range(0, DEF_HISTORY_SEASONS_BACK + 1):
                get_defense_vs_position(int(SEASON) - offset)
            get_referee_tendencies()
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "started": True})


@app.route("/api/vote-trio")
def api_vote_trio():
    return jsonify({"players": pick_similar_trio()})


@app.route("/api/submit-vote", methods=["POST"])
def api_submit_vote():
    data = request.get_json(force=True, silent=True) or {}
    votes = data.get("votes", [])
    user_id = current_user.id if current_user.is_authenticated else None

    if not DATABASE_URL:
        return jsonify({"ok": False, "error": "Voting storage isn't configured."}), 500

    conn = get_db()
    try:
        with conn.cursor() as cur:
            for v in votes:
                sid = v.get("sid")
                label = v.get("label")
                if not sid or label not in ("start", "bench", "cut"):
                    continue
                cur.execute(
                    """INSERT INTO votes (sleeper_id, player_name, position, label, user_id)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (sid, v.get("name"), v.get("position"), label, user_id),
                )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/mock-drafts")
def mock_drafts():
    return render_template_string(COMING_SOON_HTML, title="Mock Drafts",
        body="A live mock draft room (bots, real ADP, pick timer) is a bigger build than the rest of this site -- it's next on the list, not built yet.")

# ---------------- Private chat ----------------

@app.route("/chat-login", methods=["GET", "POST"])
def chat_login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == SITE_PASSWORD:
            session["authed"] = True
            return redirect("/chat")
        error = "Wrong password."
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/chat", methods=["GET", "POST"])
def chat():
    if not session.get("authed"):
        return redirect("/chat-login")

    answer = None
    question = ""
    username = request.values.get("u", MY_USERNAME)
    if request.method == "POST":
        question = request.form.get("question", "")
        if question.strip():
            try:
                context = build_context_text(username)
                prompt = (
                    "You are a knowledgeable dynasty fantasy football advisor. "
                    f"Here are the user's current leagues:\n{context}\n\n"
                    f"The user asks: {question}\n\n"
                    "Give direct, specific advice. Be conversational, under "
                    "180 words unless genuinely more detail is needed."
                )
                answer = ask_gemini(prompt)
            except Exception as e:
                answer = f"Error: {e}"
    return render_template_string(CHAT_HTML, answer=answer, question=question, username=username)

# ---------------- Design system ----------------

BASE_STYLE = """
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@600;700;800;900&family=Source+Sans+3:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
  html{ -webkit-text-size-adjust:100%; text-size-adjust:100%; }  :root{
    --paper:#0d0f0d; --paper-raised:#151815; --paper-sunken:#1c201c;
    --ink:#e8e6df; --ink-secondary:#a8ada4; --ink-muted:#8b9089;
    --line:rgba(255,255,255,0.10); --line-strong:rgba(255,255,255,0.18);
    --accent:#b97a1f; --accent-ink:#e0a542; --accent-on:#fff8ec;
    --good:#1fae5a; --good-wash:rgba(31,174,90,0.16);
    --warning:#d1a521; --warning-wash:rgba(209,165,33,0.16);
    --critical:#e2534a; --critical-wash:rgba(226,83,74,0.16);
    --pos-qb:#1baf7a; --pos-rb:#4a90e2; --pos-wr:#e0397a; --pos-te:#9575e8;
    --shadow: 0 1px 2px rgba(0,0,0,0.2), 0 8px 24px -12px rgba(0,0,0,0.5);
  }
  *{ box-sizing:border-box; }
  body{ margin:0; background:var(--paper); color:var(--ink); font-family:"Source Sans 3",system-ui,sans-serif; -webkit-font-smoothing:antialiased; }
  h1,h2,h3{ font-family:"Big Shoulders Display",system-ui,sans-serif; font-weight:800; text-transform:uppercase; letter-spacing:0.01em; margin:0; line-height:0.95; }
  p{ margin:0; line-height:1.6; }
  a{ color:inherit; }
  .mono{ font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; }
  .wrap{ max-width:1060px; margin:0 auto; padding:0 24px; }
  header.site{ position:sticky; top:0; z-index:50; background:color-mix(in srgb, var(--paper-raised) 92%, transparent); backdrop-filter:blur(10px); border-bottom:1px solid var(--line); }
  .nav-row{ display:flex; align-items:center; justify-content:space-between; height:64px; flex-wrap:wrap; gap:8px; position:relative; }
  .nav-toggle-checkbox{ display:none; }
  .nav-toggle-btn{ display:none; cursor:pointer; font-size:24px; line-height:1; color:var(--ink); padding:4px 6px; }
  .wordmark{ display:flex; align-items:center; gap:9px; text-decoration:none; }
  .wordmark svg{ width:22px; height:22px; }
  .wordmark span{ font-family:"Big Shoulders Display"; font-weight:800; font-size:18px; letter-spacing:0.03em; text-transform:uppercase; }
  nav.links{ display:flex; align-items:center; gap:20px; flex-wrap:wrap; }
  nav.links a{ text-decoration:none; font-size:13.5px; font-weight:600; color:var(--ink-secondary); }
  nav.links a:hover, nav.links a.active{ color:var(--accent-ink); }
  main{ padding: 32px 0 80px; }
  .panel{ background:var(--paper-raised); border:1px solid var(--line); border-radius:16px; box-shadow:var(--shadow); padding:22px 24px; margin-top:18px; }
  .panel h2{ font-size:20px; margin-top:6px; margin-bottom:2px; }
  .eyebrow{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; font-weight:600; letter-spacing:0.1em; text-transform:uppercase; color:var(--accent-ink); }
  .eyebrow-desc{ font-family:"Source Sans 3"; font-size:13.5px; font-weight:600; letter-spacing:0.01em; color:var(--accent-ink); }
  .search-row{ display:flex; gap:10px; margin-top:14px; flex-wrap:wrap; }
  input[type=text],input[type=password]{ flex:1; min-width:180px; border:1px solid var(--line-strong); border-radius:8px; padding:12px 14px; font-size:15px; font-family:inherit; background:var(--paper-raised); color:var(--ink); }
  .btn{ display:inline-flex; align-items:center; justify-content:center; gap:8px; font-family:"Source Sans 3"; font-weight:700; font-size:15px; border-radius:8px; padding:12px 22px; text-decoration:none; cursor:pointer; border:1px solid transparent; background:var(--accent); color:var(--accent-on); }
  .btn:hover{ opacity:0.92; }
  .error{ color:var(--critical); font-size:14px; margin-top:10px; }
  .sample-tag{ font-family:"IBM Plex Mono"; font-size:10.5px; font-weight:600; letter-spacing:0.06em; text-transform:uppercase; color:var(--ink-muted); background:var(--paper-sunken); border-radius:5px; padding:3px 7px; }
  .muted{ color:var(--ink-muted); font-size:13px; }
  .answer{ white-space:pre-wrap; line-height:1.6; margin-top:16px; padding-top:16px; border-top:1px solid var(--line); }
  .legend-box{ background:var(--paper-sunken); border-radius:10px; padding:12px 14px; font-size:12.5px; color:var(--ink-secondary); margin-top:14px; line-height:1.6; }
  .legend-box b{ color:var(--ink); }

  .team-row{ display:flex; align-items:center; gap:12px; padding:12px 4px; border-top:1px solid var(--line); }
  .team-row:first-of-type{ border-top:none; }
  .team-avatar{ width:28px; height:28px; border-radius:50%; object-fit:cover; flex:none; background:var(--paper-sunken); }
  .team-name{ font-weight:700; font-size:14px; width:150px; flex:none; text-decoration:none; color:var(--ink); }
  .team-name:hover{ color:var(--accent-ink); }
  .tier-badge{ font-size:10.5px; font-weight:700; padding:3px 9px; border-radius:99px; flex:none; white-space:nowrap; }
  .tier-juggernaut{ background:#dce8fb; color:#2a5fb0; }
  .tier-contender{ background:var(--good-wash); color:var(--good); }
  .tier-balanced{ background:var(--warning-wash); color:var(--warning); }
  .tier-rebuilder{ background:#e4e9fb; color:#5361c9; }
  .tier-purgatory{ background:var(--critical-wash); color:var(--critical); }
  .wl{ font-family:"IBM Plex Mono"; font-size:11.5px; color:var(--ink-secondary); width:44px; flex:none; }
  .value-bar{ flex:1; height:22px; border-radius:6px; overflow:hidden; display:flex; background:var(--paper-sunken); }
  .value-bar .seg{ height:100%; display:flex; align-items:center; justify-content:center; color:#fff; font-family:"IBM Plex Mono"; font-size:11px; font-weight:700; min-width:16px; }
  .rank-bubble{ display:inline-flex; align-items:center; justify-content:center; min-width:17px; height:17px; border-radius:50%; padding:0 3px; }
  .seg-qb{ background:var(--pos-qb); } .seg-rb{ background:var(--pos-rb); }
  .seg-wr{ background:var(--pos-wr); } .seg-te{ background:var(--pos-te); }
  .legend-row{ display:flex; gap:14px; flex-wrap:wrap; margin-top:14px; padding-top:12px; border-top:1px solid var(--line); }
  .legend-item{ display:flex; align-items:center; gap:6px; font-size:11.5px; color:var(--ink-secondary); font-weight:600; }
  .legend-item i{ width:9px; height:9px; border-radius:2px; display:inline-block; }
  .view-league-btn{ display:flex; width:100%; margin-top:16px; padding:10px 16px; font-size:13.5px; }

  .league-pick-list{ display:flex; flex-direction:column; gap:8px; margin-top:16px; max-height:420px; overflow-y:auto; }
  .league-pick-row{ display:flex; align-items:center; gap:12px; padding:10px 14px; border:1px solid var(--line); border-radius:10px; cursor:pointer; transition:border-color 0.15s; }
  .league-pick-row:hover{ border-color:var(--accent); }
  .league-pick-row input{ width:17px; height:17px; accent-color:var(--accent); flex:none; cursor:pointer; }
  .league-pick-avatar{ width:28px; height:28px; border-radius:50%; object-fit:cover; flex:none; background:var(--paper-sunken); }
  .league-pick-name{ font-weight:600; font-size:14px; }
  .league-pick-actions{ display:flex; gap:10px; margin-top:16px; align-items:center; flex-wrap:wrap; }
  .link-btn{ background:none; border:none; padding:0; font:inherit; font-weight:600; font-size:12.5px; color:var(--ink-secondary); text-decoration:underline; cursor:pointer; }
  .link-btn:hover{ color:var(--accent-ink); }

  .team-switcher{ display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }
  .team-chip{ font-size:12.5px; font-weight:600; padding:6px 12px; border-radius:99px; border:1px solid var(--line-strong); text-decoration:none; color:var(--ink-secondary); }
  .team-chip.active{ background:var(--ink); color:var(--paper-raised); border-color:var(--ink); }
  .team-chip:hover{ border-color:var(--accent); }

  .col-grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:16px; margin-top:14px; }
  .col-head{ font-family:"IBM Plex Mono"; font-size:11px; font-weight:700; letter-spacing:0.05em; text-transform:uppercase; padding:6px 8px; border-radius:6px; margin-bottom:8px; color:#fff; display:flex; align-items:center; justify-content:space-between; }
  .col-head.qb{ background:var(--pos-qb); } .col-head.rb{ background:var(--pos-rb); }
  .col-head.wr{ background:var(--pos-wr); } .col-head.te{ background:var(--pos-te); }
  .col-head .rank-badge-inline{ background:rgba(255,255,255,0.28); border-radius:5px; padding:2px 7px; font-size:11px; }
  .player-row{ display:flex; justify-content:space-between; align-items:center; gap:8px; padding:7px 4px; border-top:1px solid var(--line); font-size:13px; }
  .player-row:first-of-type{ border-top:none; }
  .player-row img{ width:26px; height:26px; border-radius:50%; object-fit:cover; background:var(--paper-sunken); flex:none; }
  .pname-row{ display:flex; align-items:center; gap:8px; min-width:0; flex:1; }
  .pname{ font-weight:600; text-decoration:none; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }
  .pname:hover{ color:var(--accent-ink); text-decoration:underline; }
  .rank-pair{ display:flex; gap:5px; font-family:"IBM Plex Mono"; font-size:11px; flex:none; }
  .rank-pair span{ padding:2px 6px; border-radius:5px; }
  .rank-plain{ color:var(--ink-muted); }
  .rank-badge.good{ background:var(--good-wash); color:var(--good); font-weight:700; }
  .rank-badge.warning{ background:var(--warning-wash); color:var(--warning); font-weight:700; }
  .rank-badge.critical{ background:var(--critical-wash); color:var(--critical); font-weight:700; }
  .rank-badge.flat{ background:var(--paper-sunken); color:var(--ink-muted); font-weight:700; }
  .grade-badge{ font-weight:700; }
  .grade-badge.grade-ap, .grade-badge.grade-a, .grade-badge.grade-am,
  .grade-badge.grade-bp, .grade-badge.grade-b, .grade-badge.grade-bm{ background:var(--good-wash); color:var(--good); }
  .grade-badge.grade-cp, .grade-badge.grade-c, .grade-badge.grade-cm{ background:var(--warning-wash); color:var(--warning); }
  .grade-badge.grade-dp, .grade-badge.grade-d, .grade-badge.grade-dm, .grade-badge.grade-f{ background:var(--critical-wash); color:var(--critical); }
  .legend-key{ display:flex; flex-wrap:wrap; gap:10px 20px; align-items:center; }
  .legend-key-item{ display:flex; align-items:center; gap:8px; font-size:12.5px; color:var(--ink-secondary); }
  .col-head-sample{ display:inline-flex; padding:3px 6px; border-radius:5px; background:var(--ink-muted); flex:none; }

  .player-hero{ display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  .player-hero img{ width:72px; height:72px; border-radius:14px; object-fit:cover; background:var(--paper-sunken); }
  .player-hero h2{ font-size:28px; }
  .fact-grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:14px; margin-top:18px; }

  .news-item{ padding:12px 0; border-top:1px solid var(--line); }
  .news-item:first-of-type{ border-top:none; padding-top:2px; }
  .news-item a.news-title{ font-weight:700; font-size:14.5px; text-decoration:none; color:var(--ink); line-height:1.35; }
  .news-item a.news-title:hover{ color:var(--accent-ink); text-decoration:underline; }
  .news-meta{ font-size:11.5px; color:var(--ink-muted); margin-top:3px; }
  .news-meta a{ color:var(--ink-muted); text-decoration:underline; }
  .news-desc{ font-size:13px; color:var(--ink-secondary); margin-top:6px; line-height:1.55; }
  .news-credit{ font-size:11px; color:var(--ink-muted); margin-top:12px; padding-top:10px; border-top:1px solid var(--line); }
  .news-credit a{ color:var(--accent-ink); text-decoration:none; }
  .news-credit a:hover{ text-decoration:underline; }
  .depth-you{ color:var(--accent-ink); font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; min-width:34px; }
  .depth-you-tag{ font-size:10px; color:var(--ink-muted); font-weight:600; text-transform:uppercase; letter-spacing:0.04em; margin-left:6px; flex:none; white-space:nowrap; }
  .depth-rank{ font-family:"IBM Plex Mono"; font-size:10.5px; font-weight:700; color:var(--ink-muted); min-width:26px; flex:none; }
  .injury-badge{ margin-left:auto; flex:none; font-family:"IBM Plex Mono"; font-size:10px; font-weight:800; border-radius:5px; padding:1px 6px; line-height:1.5; letter-spacing:0.02em; }
  /* Color says the severity even before you read the letters: red = not
     playing this week (Out/IR), amber = uncertain (Doubtful/Questionable),
     gray = a roster/administrative status rather than a game-day call. */
  .injury-out{ background:var(--critical-wash); color:var(--critical); }
  .injury-doubtful{ background:rgba(226,120,52,0.18); color:#e27834; }
  .injury-questionable{ background:var(--warning-wash); color:var(--warning); }
  .injury-admin{ background:var(--paper-sunken); color:var(--ink-muted); }
  .injury-ir{ background:var(--critical-wash); color:var(--critical); font-size:12px; padding:1px 5px; }
  .fact-tile{ background:var(--paper-sunken); border-radius:10px; padding:12px 14px; }
  .fact-tile b{ display:block; font-family:"Big Shoulders Display"; font-size:22px; font-weight:800; }
  .fact-tile span{ font-size:11.5px; color:var(--ink-secondary); font-weight:600; text-transform:uppercase; letter-spacing:0.04em; }

  table.rank-table{ width:100%; border-collapse:collapse; font-size:14px; margin-top:8px; }
  table.rank-table th{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:0.05em; color:var(--ink-muted); padding:6px 8px; border-bottom:1px solid var(--line-strong); }
  table.rank-table td{ padding:8px; border-bottom:1px solid var(--line); vertical-align:middle; }
  table.rank-table img{ width:28px; height:28px; border-radius:50%; object-fit:cover; background:var(--paper-sunken); }
  .pos-chip{ font-family:"IBM Plex Mono"; font-size:10.5px; font-weight:700; padding:2px 7px; border-radius:99px; color:#fff; }
  .toggle-row{ display:flex; gap:18px; flex-wrap:wrap; margin-top:12px; }
  .toggle-group{ display:flex; gap:8px; align-items:center; }
  .toggle-group .glabel{ font-size:11.5px; color:var(--ink-muted); font-weight:600; text-transform:uppercase; letter-spacing:0.04em; margin-right:4px; }
  .format-toggle{ display:flex; gap:8px; }
  .format-toggle a{ font-size:12.5px; font-weight:700; padding:6px 12px; border-radius:99px; border:1px solid var(--line-strong); text-decoration:none; color:var(--ink-secondary); }
  .format-toggle a.active{ background:var(--ink); color:var(--paper-raised); border-color:var(--ink); }

  .trade-cols{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-top:16px; }
  .trade-side-box{ background:var(--paper-sunken); border:1px solid var(--line); border-radius:12px; padding:16px; }
  .trade-side-label{ font-weight:700; font-size:14px; margin-bottom:10px; }
  .search-wrap{ position:relative; }
  .search-wrap input[type=text]{ width:100%; }
  .search-dropdown{ position:absolute; top:100%; left:0; right:0; background:var(--paper-raised); border:1px solid var(--line-strong); border-radius:8px; box-shadow:var(--shadow); z-index:30; max-height:260px; overflow-y:auto; display:none; margin-top:4px; }
  .search-dropdown.open{ display:block; }
  .search-dropdown-item{ display:flex; align-items:center; gap:10px; padding:8px 10px; cursor:pointer; }
  .search-dropdown-item:hover{ background:var(--paper-sunken); }
  .search-dropdown-item img{ width:28px; height:28px; border-radius:50%; object-fit:cover; background:var(--paper-sunken); }
  .chip-list{ display:flex; flex-wrap:wrap; gap:10px; margin-top:14px; min-height:8px; }
  .chip{ position:relative; width:92px; display:flex; flex-direction:column; align-items:center; text-align:center; gap:0; background:var(--paper-raised); border:1px solid var(--line); border-radius:10px; padding:8px 6px 9px; font-size:11.5px; font-weight:700; line-height:1.25; }
  .chip img{ width:60px; height:60px; border-radius:8px; object-fit:cover; background:var(--paper-sunken); }
  .chip .pos-chip{ position:absolute; top:6px; left:6px; }
  .chip .team-tag{ position:absolute; top:6px; right:6px; font-family:"IBM Plex Mono"; font-size:9px; font-weight:700; color:var(--ink-muted); background:var(--paper-sunken); padding:1px 5px; border-radius:5px; }
  .chip .pname-sm{ margin-top:7px; }
  .chip .remove{ position:absolute; top:-7px; right:-7px; width:18px; height:18px; border-radius:50%; background:var(--paper-raised); border:1px solid var(--line-strong); display:flex; align-items:center; justify-content:center; cursor:pointer; color:var(--ink-muted); font-weight:800; font-size:12px; line-height:1; }
  .chip .remove:hover{ color:#fff; background:var(--critical); border-color:var(--critical); }
  .trade-total{ margin-top:14px; font-family:"IBM Plex Mono"; font-size:13px; font-weight:700; color:var(--ink-secondary); }
  .trade-total-adjusted{ margin-top:2px; font-family:"IBM Plex Mono"; font-size:11.5px; color:var(--ink-muted); }
  .trade-result{ margin-top:20px; padding-top:18px; border-top:1px solid var(--line); }
  .verdict{ font-family:"Big Shoulders Display"; font-size:26px; font-weight:800; }
  /* A visual tug-of-war between the two sides' adjusted value -- fixed
     colors per side (blue vs. the site's own accent gold) so the bar
     always reads the same way; the text verdict above/below still owns
     "who's actually winning," this just shows the raw proportion. */
  .balance-bar-wrap{ margin-top:20px; }
  .balance-bar{ position:relative; height:16px; border-radius:99px; overflow:hidden; display:flex; background:var(--paper-sunken); box-shadow: inset 0 1px 3px rgba(0,0,0,0.35); }
  .balance-fill{ height:100%; transition: width 0.5s cubic-bezier(.4,0,.2,1); }
  .balance-fill-1{ background: linear-gradient(90deg, #3f7fc9, #5a9ae0); }
  .balance-fill-2{ background: linear-gradient(90deg, var(--accent), var(--accent-ink)); }
  .balance-center-marker{ position:absolute; left:50%; top:-3px; bottom:-3px; width:2px; background:rgba(255,255,255,0.35); transform:translateX(-50%); pointer-events:none; }
  .balance-pointer{ position:absolute; top:-7px; width:0; height:0; border-left:6px solid transparent; border-right:6px solid transparent; border-top:8px solid var(--ink); transform:translateX(-50%); transition: left 0.5s cubic-bezier(.4,0,.2,1); filter:drop-shadow(0 1px 1px rgba(0,0,0,0.4)); }
  .balance-labels{ display:flex; justify-content:space-between; margin-top:8px; font-family:"IBM Plex Mono"; font-size:11.5px; color:var(--ink-muted); }
  .balance-labels .leading{ color:var(--ink); font-weight:700; }

  .link-box{ background:var(--paper-sunken); border-radius:10px; padding:14px 16px; margin-top:12px; }
  .gate-wrap{ position:relative; margin-top:18px; }
  .gate-blur{ filter:blur(6px); pointer-events:none; user-select:none; opacity:0.55; max-height:520px; overflow:hidden; }
  .gate-card{ position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); background:var(--paper-raised); border:1px solid var(--line-strong); border-radius:16px; box-shadow:var(--shadow); padding:36px 32px; max-width:380px; width:88%; text-align:center; z-index:10; }
  .gate-card h3{ font-size:22px; }
  .gate-card p{ color:var(--ink-secondary); font-size:14px; margin-top:10px; line-height:1.6; }
  .gate-benefits{ text-align:left; margin-top:18px; display:flex; flex-direction:column; gap:9px; font-size:13.5px; color:var(--ink-secondary); }
  .gate-benefits span{ display:flex; align-items:center; gap:8px; }
  .gate-benefits span::before{ content:'\\2713'; color:var(--good); font-weight:700; }
  .quick-add-grid{ display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; max-height:180px; overflow-y:auto; }
  .quick-add-tile{ display:flex; align-items:center; gap:6px; background:var(--paper-raised); border:1px solid var(--line); border-radius:99px; padding:4px 10px 4px 4px; font-size:12px; font-weight:600; cursor:pointer; }
  .quick-add-tile:hover{ border-color:var(--accent); }
  .quick-add-tile img{ width:22px; height:22px; border-radius:50%; object-fit:cover; }
  .league-chip-row{ display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
  .suggestion-row{ display:flex; align-items:center; gap:8px; padding:6px 0; font-size:13px; }
  .suggestion-row img{ width:24px; height:24px; border-radius:50%; object-fit:cover; }

  .vote-overlay{ position:fixed; inset:0; background:rgba(20,32,27,0.55); z-index:100; display:none; align-items:center; justify-content:center; padding:20px; }
  .vote-overlay.open{ display:flex; }
  .vote-modal{ background:var(--paper-raised); border-radius:18px; max-width:640px; width:100%; padding:32px 28px 26px; text-align:center; position:relative; box-shadow:var(--shadow); max-height:88vh; overflow-y:auto; }
  .vote-modal h2{ font-size:30px; }
  .vote-modal .sub{ color:var(--ink-secondary); font-size:14px; margin-top:8px; }
  .vote-close{ position:absolute; top:16px; right:18px; cursor:pointer; font-size:22px; color:var(--ink-muted); background:none; border:none; }
  .vote-cards{ display:flex; gap:14px; margin-top:22px; flex-wrap:wrap; justify-content:center; }
  .vote-card{ flex:1; min-width:160px; border:1px solid var(--line-strong); border-radius:12px; padding:16px 12px; }
  .vote-card img{ width:56px; height:56px; border-radius:50%; object-fit:cover; margin-bottom:8px; background:var(--paper-sunken); }
  .vote-card .vname{ font-weight:700; font-size:14px; }
  .vote-card .vmeta{ font-size:12px; color:var(--ink-muted); margin-top:2px; }
  .vote-btns{ display:flex; justify-content:space-between; gap:6px; margin-top:14px; border-top:1px solid var(--line); padding-top:10px; }
  .vote-btn{ flex:1; background:none; border:none; cursor:pointer; font-size:11px; font-weight:700; letter-spacing:0.03em; color:var(--ink-secondary); padding:4px; border-radius:6px; }
  .vote-btn.start.selected{ background:var(--good-wash); color:var(--good); }
  .vote-btn.bench.selected{ background:var(--paper-sunken); color:var(--ink); }
  .vote-btn.cut.selected{ background:var(--critical-wash); color:var(--critical); }
  .vote-submit{ margin-top:20px; width:100%; padding:12px; border-radius:8px; border:none; font-weight:700; font-size:14px; background:var(--paper-sunken); color:var(--ink-muted); cursor:not-allowed; }
  .vote-submit.ready{ background:var(--accent); color:var(--accent-on); cursor:pointer; }
  .vote-skip{ display:block; margin-top:14px; font-size:12.5px; color:var(--accent-ink); text-decoration:underline; cursor:pointer; background:none; border:none; }

  @media (max-width: 760px) {
    .wrap{ padding:0 16px; }
    .nav-toggle-btn{ display:block; }
    nav.links{
      display:none; position:absolute; top:100%; left:0; right:0;
      flex-direction:column; align-items:stretch; gap:0;
      background:var(--paper-raised); border:1px solid var(--line);
      border-top:none; padding:6px 16px 14px; z-index:60;
    }
    .nav-toggle-checkbox:checked ~ nav.links{ display:flex; }
    nav.links a{ padding:13px 4px; border-top:1px solid var(--line); margin:0; }
    .panel{ padding:18px 16px; }
    .trade-cols{ grid-template-columns:1fr; }
    .vote-cards{ flex-direction:column; }
    .vote-modal{ padding:22px 18px; }
    .vote-card{ padding:12px 10px; }
    .vote-card img{ width:44px; height:44px; margin-bottom:6px; }
    .vote-btns{ margin-top:10px; padding-top:8px; }
    .col-grid{ grid-template-columns:1fr; }
    .team-row{ flex-wrap:wrap; gap:8px 10px; }
    .team-name{ width:auto; }
    .value-bar{ flex-basis:100%; order:3; }
    table.rank-table{ display:block; overflow-x:auto; white-space:nowrap; }
    .fact-grid{ grid-template-columns:repeat(2,1fr); }
    .player-hero{ gap:12px; }
    .toggle-row{ gap:12px; }
  }
</style>
"""

SCORE_MARK_SVG = ('<svg class="score-mark" viewBox="0 0 12 14" fill="none" aria-hidden="true">'
                  '<rect x="0.75" y="0.75" width="10.5" height="12.5" rx="2" '
                  'stroke="currentColor" stroke-width="1.3"/>'
                  '<rect x="3" y="3" width="6" height="2.2" rx="0.6" fill="currentColor"/>'
                  '<circle cx="3.9" cy="8" r="0.85" fill="currentColor"/>'
                  '<circle cx="8.1" cy="8" r="0.85" fill="currentColor"/>'
                  '<circle cx="3.9" cy="10.9" r="0.85" fill="currentColor"/>'
                  '<circle cx="8.1" cy="10.9" r="0.85" fill="currentColor"/>'
                  '</svg>')


LOGO_SVG = """<svg viewBox="0 0 26 26" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <rect x="1" y="1" width="24" height="24" rx="5" fill="var(--ink)"/>
  <path d="M6 13H20M6 8H14M6 18H14" stroke="var(--paper)" stroke-width="2" stroke-linecap="round"/>
</svg>"""

def make_header(active=""):
    def cls(name):
        return "active" if name == active else ""
    return f"""
<header class="site"><div class="wrap nav-row">
  <a class="wordmark" href="/">{LOGO_SVG}<span>Fantasy Football Calc</span></a>
  <input type="checkbox" id="navToggle" class="nav-toggle-checkbox">
  <label for="navToggle" class="nav-toggle-btn" aria-label="Menu">&#9776;</label>
  <nav class="links">
    <a class="{cls('league')}" href="/league-manager">League Manager</a>
    <a class="{cls('scores')}" href="/scores">Scores</a>
    <a class="{cls('rankings')}" href="/rankings">Rankings</a>
    <a class="{cls('matchups')}" href="/matchups">Matchups</a>
    <a class="{cls('trade')}" href="/trade-calculator">Trade Calculator</a>
    <a class="{cls('sbc')}" href="/start-bench-cut">Start/Bench/Cut</a>
    {{% if current_user.is_authenticated %}}
      <a href="/">{{{{ current_user.username }}}}</a><a href="/logout">Log Out</a>
    {{% else %}}
      <a href="/login">Sign In</a><a href="/signup">Create Account</a>
    {{% endif %}}
  </nav>
</div></header>
"""

SBC_PAGE_HTML = BASE_STYLE + make_header("sbc") + """
<main><div class="wrap" style="max-width:700px;">
  <div class="panel" style="text-align:center;">
    <p class="eyebrow">Community rankings game</p>
    <h2 style="font-size:28px;">Start / Bench / Cut</h2>
    <p class="sub" style="margin-top:10px;">Help keep our rankings sharp &mdash; rank each trio by how you value them. Play as many rounds as you want.</p>
    <p class="sub"><b>Start</b> the most valuable, <b>Bench</b> the middle, <b>Cut</b> the least valuable.</p>
    <p class="muted" style="margin-top:10px;">Round <span id="roundNum">1</span> &middot; <span id="voteTally">0</span> voted this session</p>
    <div class="vote-cards" id="sbcCards" style="margin-top:20px;"></div>
    <button class="vote-submit" id="sbcSubmit" onclick="sbcSubmit()" style="max-width:340px; margin-left:auto; margin-right:auto;">Submit</button>
    <button class="vote-skip" onclick="sbcLoadRound()">Skip this trio</button>
  </div>
</div></main>
<script>
let sbcTrio = [];
let sbcLabels = {};
let sbcRound = 1;
let sbcTally = 0;

async function sbcLoadRound() {
  sbcLabels = {};
  const container = document.getElementById('sbcCards');
  container.innerHTML = '<div class="muted" style="padding:30px; text-align:center;">Loading players...</div>';
  document.getElementById('sbcSubmit').classList.remove('ready');
  try {
    const resp = await fetch('/api/vote-trio');
    const data = await resp.json();
    if (!data.players || data.players.length < 3) {
      container.innerHTML = '<div class="muted" style="padding:30px;">Not enough data to build a round right now -- try again shortly.</div>';
      return;
    }
    sbcTrio = data.players;
    container.innerHTML = '';
    sbcTrio.forEach(p => {
      const card = document.createElement('div');
      card.className = 'vote-card';
      card.innerHTML = `
        <img src="${p.photo}" onerror="this.style.visibility='hidden'">
        <div class="vname">${p.name}</div>
        <div class="vmeta">${p.position} &middot; ${p.team}${p.age ? ' &middot; ' + Math.round(p.age) + ' yo' : ''}</div>
        <div class="vote-btns">
          <button class="vote-btn start" onclick="sbcSetLabel('${p.sid}','start',this)">START</button>
          <button class="vote-btn bench" onclick="sbcSetLabel('${p.sid}','bench',this)">BENCH</button>
          <button class="vote-btn cut" onclick="sbcSetLabel('${p.sid}','cut',this)">CUT</button>
        </div>`;
      container.appendChild(card);
    });
  } catch (e) {
    container.innerHTML = '<div class="muted" style="padding:30px;">Couldn\\'t load a round -- try again.</div>';
  }
}

function sbcSetLabel(sid, label, btnEl) {
  for (const key in sbcLabels) {
    if (sbcLabels[key] === label) delete sbcLabels[key];
  }
  sbcLabels[sid] = label;
  document.querySelectorAll('#sbcCards .vote-card').forEach(card => {
    card.querySelectorAll('.vote-btn').forEach(b => b.classList.remove('selected'));
  });
  sbcTrio.forEach((p, idx) => {
    if (sbcLabels[p.sid]) {
      const card = document.querySelectorAll('#sbcCards .vote-card')[idx];
      const btn = card.querySelector('.vote-btn.' + sbcLabels[p.sid]);
      if (btn) btn.classList.add('selected');
    }
  });
  document.getElementById('sbcSubmit').classList.toggle('ready', Object.keys(sbcLabels).length === 3);
}

async function sbcSubmit() {
  if (Object.keys(sbcLabels).length !== 3) return;
  try {
    const payload = sbcTrio.map(p => ({sid: p.sid, name: p.name, position: p.position, label: sbcLabels[p.sid]}));
    await fetch('/api/submit-vote', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({votes: payload}),
    });
  } catch (e) {}
  sbcTally += 3;
  sbcRound += 1;
  document.getElementById('roundNum').textContent = sbcRound;
  document.getElementById('voteTally').textContent = sbcTally;
  sbcLoadRound();
}

sbcLoadRound();
</script>
"""


@app.route("/start-bench-cut")
def start_bench_cut():
    return render_template_string(SBC_PAGE_HTML)


VOTE_MODAL_HTML = """
<div class="vote-overlay" id="voteOverlay">
  <div class="vote-modal">
    <button class="vote-close" onclick="closeVoteModal(false)">&times;</button>
    <h2>Your Thoughts?</h2>
    <p class="sub">Help keep our rankings sharp &mdash; rank these three players by how you value them.</p>
    <p class="sub"><b>Start</b> the most valuable, <b>Bench</b> the middle, <b>Cut</b> the least valuable.</p>
    <div class="vote-cards" id="voteCards"></div>
    <button class="vote-submit" id="voteSubmit" onclick="submitVote()">Submit</button>
    <button class="vote-skip" onclick="closeVoteModal(false)">I don't know all of these players</button>
  </div>
</div>
<script>
let voteTrio = [];
let voteLabels = {};

async function loadVoteModal() {
  if (sessionStorage.getItem('vote_shown')) return;

  // Show the modal shell immediately with a loading skeleton so it
  // doesn't feel like it's waiting on the network -- fill in real cards
  // once the fetch resolves.
  const container = document.getElementById('voteCards');
  container.innerHTML = '<div class="muted" style="padding:30px; text-align:center;">Loading players...</div>';
  document.getElementById('voteOverlay').classList.add('open');

  try {
    const resp = await fetch('/api/vote-trio');
    const data = await resp.json();
    if (!data.players || data.players.length < 3) {
      document.getElementById('voteOverlay').classList.remove('open');
      return;
    }
    voteTrio = data.players;
    voteLabels = {};
    container.innerHTML = '';
    voteTrio.forEach(p => {
      const card = document.createElement('div');
      card.className = 'vote-card';
      card.innerHTML = `
        <img src="${p.photo}" onerror="this.style.visibility='hidden'">
        <div class="vname">${p.name}</div>
        <div class="vmeta">${p.position} &middot; ${p.team}${p.age ? ' &middot; ' + Math.round(p.age) + ' yo' : ''}</div>
        <div class="vote-btns">
          <button class="vote-btn start" onclick="setVoteLabel('${p.sid}','start',this)">START</button>
          <button class="vote-btn bench" onclick="setVoteLabel('${p.sid}','bench',this)">BENCH</button>
          <button class="vote-btn cut" onclick="setVoteLabel('${p.sid}','cut',this)">CUT</button>
        </div>`;
      container.appendChild(card);
    });
  } catch (e) {
    document.getElementById('voteOverlay').classList.remove('open');
  }
}

function setVoteLabel(sid, label, btnEl) {
  // Each label (keep/trade/cut) can only be used once across the 3 cards.
  for (const key in voteLabels) {
    if (voteLabels[key] === label) delete voteLabels[key];
  }
  voteLabels[sid] = label;

  document.querySelectorAll('.vote-card').forEach(card => {
    card.querySelectorAll('.vote-btn').forEach(b => b.classList.remove('selected'));
  });
  voteTrio.forEach(p => {
    if (voteLabels[p.sid]) {
      const idx = voteTrio.indexOf(p);
      const card = document.querySelectorAll('.vote-card')[idx];
      const btn = card.querySelector('.vote-btn.' + voteLabels[p.sid]);
      if (btn) btn.classList.add('selected');
    }
  });

  const submitBtn = document.getElementById('voteSubmit');
  const ready = Object.keys(voteLabels).length === 3;
  submitBtn.classList.toggle('ready', ready);
  submitBtn.disabled = !ready;
}

async function submitVote() {
  if (Object.keys(voteLabels).length !== 3) return;
  try {
    const payload = voteTrio.map(p => ({sid: p.sid, name: p.name, position: p.position, label: voteLabels[p.sid]}));
    await fetch('/api/submit-vote', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({votes: payload}),
    });
  } catch (e) {}
  closeVoteModal(true);
}

function closeVoteModal(submitted) {
  document.getElementById('voteOverlay').classList.remove('open');
  sessionStorage.setItem('vote_shown', '1');
}

{% if not current_user.is_authenticated %}
document.addEventListener('DOMContentLoaded', () => setTimeout(loadVoteModal, 350));
{% endif %}
</script>
"""

HOME_HTML = BASE_STYLE + make_header("league") + VOTE_MODAL_HTML + """
{% macro league_panels(leagues, username) %}
  {% for lg in leagues %}
  {% set t = lg.my_team %}
  <div class="panel">
    <div style="display:flex; justify-content:space-between; align-items:baseline;">
      <h2>{{ lg.league_name }}</h2>
      <span class="sample-tag">Live data</span>
    </div>
    {% if t %}
    <div class="team-row">
      <img class="team-avatar" src="{{ t.avatar_url or 'data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2232%22 height=%2232%22><rect width=%2232%22 height=%2232%22 rx=%2216%22 fill=%22%23444841%22/></svg>' }}" alt="" onerror="this.style.visibility='hidden'">
      <span class="team-name">{{ t.owner_name }}</span>
      <span class="tier-badge {{ t.power_tier_class }}">{{ t.power_tier }}</span>
      <span class="wl">{{ t.wins }}-{{ t.losses }}</span>
      <div class="value-bar">
        {% for pos, pct, rank, intensity in t.bar %}<span class="seg seg-{{ pos.lower() }}" style="width:{{ pct }}%"><span class="rank-bubble" style="background:rgba(0,0,0,{{ (0.15 + intensity*0.45)|round(2) }});">{{ rank }}</span></span>{% endfor %}
      </div>
    </div>
    <div class="legend-row">
      <span class="legend-item"><i style="background:var(--pos-qb)"></i>QB</span>
      <span class="legend-item"><i style="background:var(--pos-rb)"></i>RB</span>
      <span class="legend-item"><i style="background:var(--pos-wr)"></i>WR</span>
      <span class="legend-item"><i style="background:var(--pos-te)"></i>TE</span>
    </div>
    {% else %}
    <p class="muted" style="margin-top:14px;">Your roster wasn't found in this league.</p>
    {% endif %}
    <a class="btn view-league-btn" href="/league?league_id={{ lg.league_id }}&u={{ username }}">View League &rarr;</a>
  </div>
  {% endfor %}
{% endmacro %}

{% macro portfolio_panel(portfolio, fmt, username) %}
<div class="panel">
  <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
    <div>
      <p class="eyebrow">Portfolio</p>
      <h2 style="font-size:20px;">{{ portfolio.leagues_count }} League{{ 's' if portfolio.leagues_count != 1 else '' }}</h2>
    </div>
    <div class="format-toggle">
      <a class="{{ 'active' if fmt=='1qb' else '' }}" href="/league-manager?u={{ username }}&fmt=1qb">1QB</a>
      <a class="{{ 'active' if fmt=='superflex' else '' }}" href="/league-manager?u={{ username }}&fmt=superflex">Superflex</a>
    </div>
  </div>

  <div style="margin-top:18px; display:flex; flex-direction:column; gap:8px;">
    {% for pos, pct in portfolio.pos_pct %}
    <div style="display:flex; align-items:center; gap:10px;">
      <span style="width:30px; font-size:12.5px; font-weight:700; color:var(--pos-{{ pos.lower() }});">{{ pos }}</span>
      <div style="flex:1; height:10px; border-radius:99px; background:var(--paper-sunken); overflow:hidden;">
        <span style="display:block; height:100%; width:{{ pct }}%; background:var(--pos-{{ pos.lower() }});"></span>
      </div>
      <span class="mono" style="width:46px; text-align:right; font-size:12px; color:var(--ink-secondary);">{{ pct }}%</span>
    </div>
    {% endfor %}
  </div>

  <p class="eyebrow" style="margin-top:24px;">Player Exposure</p>
  <div class="search-row" style="margin-top:6px;">
    <input type="text" id="expoSearch" placeholder="Search player...">
  </div>
  <div class="format-toggle" style="margin-top:10px;" id="expoPosTabs">
    <a class="active" data-pos="all" href="#">All</a>
    <a data-pos="QB" href="#">QB</a>
    <a data-pos="RB" href="#">RB</a>
    <a data-pos="WR" href="#">WR</a>
    <a data-pos="TE" href="#">TE</a>
  </div>
  <table class="rank-table" style="margin-top:10px;" id="expoTable">
    <tr><th>Player</th><th>Pos</th><th>Shares</th><th>Value</th></tr>
    {% for p in portfolio.exposure %}
    <tr class="expo-row" data-name="{{ p.name|lower }}" data-pos="{{ p.position }}">
      <td><a class="pname" style="display:flex; align-items:center; gap:8px;" href="/player?sid={{ p.sid }}&numqbs={{ 2 if fmt=='superflex' else 1 }}&u={{ username }}"><img src="{{ p.photo }}" style="width:24px; height:24px; border-radius:50%; object-fit:cover;" onerror="this.style.visibility='hidden'">{{ p.name }}</a></td>
      <td><span class="pos-chip" style="background:var(--pos-{{ p.position.lower() }});">{{ p.position }}</span></td>
      <td class="mono">{{ p.shares }}</td>
      <td class="mono">{{ p.value }}</td>
    </tr>
    {% endfor %}
  </table>
  <div style="display:flex; align-items:center; justify-content:space-between; margin-top:12px;">
    <button type="button" class="team-chip" id="expoPrev">&larr; Prev</button>
    <span class="muted" id="expoPageLabel">Page 1</span>
    <button type="button" class="team-chip" id="expoNext">Next &rarr;</button>
  </div>
</div>
<script>
(function(){
  const search = document.getElementById('expoSearch');
  const tabs = document.getElementById('expoPosTabs');
  const prevBtn = document.getElementById('expoPrev');
  const nextBtn = document.getElementById('expoNext');
  const pageLabel = document.getElementById('expoPageLabel');
  if(!search || !tabs) return;

  const PAGE_SIZE = 10;
  const allRows = Array.from(document.querySelectorAll('#expoTable .expo-row'));
  let activePos = 'all';
  let page = 1;

  function matchingRows(){
    const q = search.value.trim().toLowerCase();
    return allRows.filter(function(row){
      const matchesPos = activePos === 'all' || row.dataset.pos === activePos;
      const matchesSearch = !q || row.dataset.name.includes(q);
      return matchesPos && matchesSearch;
    });
  }

  function render(){
    const matches = matchingRows();
    const totalPages = Math.max(1, Math.ceil(matches.length / PAGE_SIZE));
    if (page > totalPages) page = totalPages;
    const start = (page - 1) * PAGE_SIZE;
    const visible = new Set(matches.slice(start, start + PAGE_SIZE));

    allRows.forEach(function(row){ row.style.display = visible.has(row) ? '' : 'none'; });
    pageLabel.textContent = matches.length ? `Page ${page} of ${totalPages}` : 'No players match';
    prevBtn.disabled = page <= 1;
    nextBtn.disabled = page >= totalPages;
    prevBtn.style.opacity = prevBtn.disabled ? 0.4 : 1;
    nextBtn.style.opacity = nextBtn.disabled ? 0.4 : 1;
  }

  search.addEventListener('input', function(){ page = 1; render(); });
  prevBtn.addEventListener('click', function(){ page -= 1; render(); });
  nextBtn.addEventListener('click', function(){ page += 1; render(); });
  tabs.querySelectorAll('a').forEach(function(a){
    a.addEventListener('click', function(e){
      e.preventDefault();
      activePos = a.dataset.pos;
      page = 1;
      tabs.querySelectorAll('a').forEach(function(x){ x.classList.remove('active'); });
      a.classList.add('active');
      render();
    });
  });

  render();
})();
</script>
{% endmacro %}

{% macro league_picker(picker, username, fmt) %}
<div class="panel">
  <p class="eyebrow">Choose leagues to sync</p>
  <h2>{{ picker.display_name }}'s leagues on Sleeper</h2>
  <p class="muted" style="margin-top:6px;">We found {{ picker.leagues|length }} league{{ 's' if picker.leagues|length != 1 else '' }}. Pick the ones you want tracked here &mdash; you can add or remove leagues anytime.</p>
  <form method="get" action="/league-manager" id="leaguePickForm">
    <input type="hidden" name="u" value="{{ username }}">
    <input type="hidden" name="fmt" value="{{ fmt }}">
    <div class="league-pick-list" id="leaguePickList">
      {% for lg in picker.leagues %}
      <label class="league-pick-row">
        <input type="checkbox" name="leagues" value="{{ lg.league_id }}" {% if lg.league_id in picker.preselected or picker.default_all %}checked{% endif %}>
        <img class="league-pick-avatar" src="{{ lg.avatar_url or 'data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2228%22 height=%2228%22><rect width=%2228%22 height=%2228%22 rx=%2214%22 fill=%22%23444841%22/></svg>' }}" alt="" onerror="this.style.visibility='hidden'">
        <span class="league-pick-name">{{ lg.name }}</span>
        <span class="muted mono" style="margin-left:auto;">{{ lg.total_rosters }} teams</span>
      </label>
      {% endfor %}
    </div>
    <div class="league-pick-actions">
      <button type="button" class="link-btn" id="pickAllBtn">Select all</button>
      <button type="button" class="link-btn" id="pickNoneBtn">Select none</button>
      <button class="btn" type="submit" style="margin-left:auto;">Sync selected leagues</button>
    </div>
  </form>
</div>
<script>
(function(){
  var list = document.getElementById('leaguePickList');
  var allBtn = document.getElementById('pickAllBtn');
  var noneBtn = document.getElementById('pickNoneBtn');
  if(!list) return;
  function setAll(checked){
    list.querySelectorAll('input[type=checkbox]').forEach(function(cb){ cb.checked = checked; });
  }
  allBtn.addEventListener('click', function(){ setAll(true); });
  noneBtn.addEventListener('click', function(){ setAll(false); });
})();
</script>
{% endmacro %}
<main><div class="wrap">
  <div class="panel">
    <p class="eyebrow">League lookup</p>
    <h2>Find any dynasty manager</h2>
    <form method="get" class="search-row">
      <input type="text" name="u" placeholder="Sleeper username" value="{{ username }}" autofocus>
      <button class="btn" type="submit">Search</button>
    </form>
    {% if used_saved %}
    <p class="muted" style="margin-top:10px;">Showing your saved username (<strong style="color:var(--accent-ink);">{{ username }}</strong>). Search a different one above anytime to update it.</p>
    {% elif current_user.is_authenticated and username %}
    <p class="muted" style="margin-top:10px;">Saved <strong style="color:var(--accent-ink);">{{ username }}</strong> to your account &mdash; it'll load automatically next time you visit.</p>
    {% endif %}
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
  </div>

  {% if picker %}
  {{ league_picker(picker, username, fmt) }}
  {% endif %}

  {% if data %}
  <p class="muted" style="margin-top:18px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
    <span>Showing {{ data.leagues|length }} synced league{{ 's' if data.leagues|length != 1 else '' }} for <strong style="color:var(--accent-ink);">{{ data.display_name }}</strong> &middot; ranks based on real dynasty trade values</span>
    <a href="/league-manager?u={{ username }}&fmt={{ fmt }}&manage=1{% for lg in data.leagues %}&leagues={{ lg.league_id }}{% endfor %}" class="link-btn">+ Add or remove leagues</a>
  </p>

  {% if current_user.is_authenticated %}
    {% if portfolio %}{{ portfolio_panel(portfolio, fmt, username) }}{% endif %}
    {{ league_panels(data.leagues, username) }}
  {% else %}
  <div class="gate-wrap">
    <div class="gate-blur">{{ league_panels(data.leagues, username) }}</div>
    <div class="gate-card">
      <h3>Build Your <span style="color:var(--accent-ink);">Dynasty Portfolio</span></h3>
      <p>Create a free account to unlock full team breakdowns, save your leagues, and vote on community rankings.</p>
      <div class="gate-benefits">
        <span>Full roster breakdowns for every team</span>
        <span>Save your leagues, no re-searching</span>
        <span>Vote on community Start/Bench/Cut rankings</span>
        <span>Unlimited trade calculator access</span>
      </div>
      <a href="/signup" class="btn" style="margin-top:22px; width:100%;">Create Account</a>
    </div>
  </div>
  {% endif %}
  {% endif %}
</div></main>
"""

LEAGUE_DETAIL_HTML = BASE_STYLE + make_header("league") + """
<main><div class="wrap">
  <a href="/league-manager?u={{ username }}" class="muted">&larr; Back to leagues</a>

  {% if detail.mode == 'rankings' %}
  <div class="panel">
    <p class="eyebrow">{{ detail.league_name }}</p>
    <h2>League Rankings</h2>
    {% for t in detail.teams %}
    <div class="team-row">
      <img class="team-avatar" src="{{ t.avatar_url or 'data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2232%22 height=%2232%22><rect width=%2232%22 height=%2232%22 rx=%2216%22 fill=%22%23444841%22/></svg>' }}" alt="" onerror="this.style.visibility='hidden'">
      <a class="team-name" href="/league?league_id={{ league_id }}&roster_id={{ t.roster_id }}&u={{ username }}">{{ t.owner_name }}{% if t.is_you %} &#9733;{% endif %}</a>
      <span class="tier-badge {{ t.power_tier_class }}">{{ t.power_tier }}</span>
      <span class="wl">{{ t.wins }}-{{ t.losses }}</span>
      <div class="value-bar">
        {% for pos, pct, rank, intensity in t.bar %}<span class="seg seg-{{ pos.lower() }}" style="width:{{ pct }}%"><span class="rank-bubble" style="background:rgba(0,0,0,{{ (0.15 + intensity*0.45)|round(2) }});">{{ rank }}</span></span>{% endfor %}
      </div>
    </div>
    {% endfor %}
    <div class="legend-row">
      <span class="legend-item"><i style="background:var(--pos-qb)"></i>QB</span>
      <span class="legend-item"><i style="background:var(--pos-rb)"></i>RB</span>
      <span class="legend-item"><i style="background:var(--pos-wr)"></i>WR</span>
      <span class="legend-item"><i style="background:var(--pos-te)"></i>TE</span>
    </div>
    <p class="muted" style="margin-top:10px;">Click a manager to see their full roster broken down by position.</p>
  </div>

  {% else %}
  <div class="panel">
    <p class="eyebrow">{{ detail.league_name }}</p>
    <h2>{{ detail.owner_name }}'s roster value</h2>

    <div class="team-switcher">
      <a class="team-chip" href="/league?league_id={{ league_id }}&u={{ username }}">&larr; Rankings</a>
      {% for t in detail.teams %}
      <a class="team-chip {{ 'active' if t.roster_id == detail.roster_id else '' }}" href="/league?league_id={{ league_id }}&roster_id={{ t.roster_id }}&u={{ username }}">{{ t.owner_name }}{% if t.is_you %} &#9733;{% endif %}</a>
      {% endfor %}
    </div>

    <div class="col-grid">
      {% for pos, col in detail.columns.items() %}
      <div>
        <div class="col-head {{ pos.lower() }}">
          <span>{{ pos }}</span>
          <span class="rank-badge-inline">Rank {{ col.team_rank }}</span>
        </div>
        {% for p in col.players %}
        <div class="player-row">
          <div class="pname-row">
            <img src="{{ p.photo }}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
            <a class="pname" href="/player?sid={{ p.sleeper_id }}&numqbs={{ detail.num_qbs }}&u={{ username }}&ref={{ ('/league?league_id=' ~ league_id ~ '&roster_id=' ~ detail.roster_id ~ '&u=' ~ username)|urlencode }}">{{ p.name }}</a>
          </div>
          <span class="rank-pair">
            {% if p.grade %}<span class="grade-badge grade-{{ p.grade_class }}">{{ p.grade }}</span>{% endif %}
            <span class="rank-plain">{{ p.position_rank or '\u2014' }}</span>
            <span class="rank-badge {{ p.tier }}">{{ p.overall_rank or '\u2014' }}</span>
          </span>
        </div>
        {% endfor %}
        {% if not col.players %}<p class="muted" style="padding:6px 4px;">No players rostered here.</p>{% endif %}
      </div>
      {% endfor %}
    </div>

    <div class="legend-box">
      <div class="legend-key">
        <span class="legend-key-item"><span class="col-head-sample"><span class="rank-badge-inline">Rank 3</span></span> Team's rank at that position</span>
        <span class="legend-key-item"><span class="rank-pair"><span class="rank-plain">12</span></span> Player's rank at their position</span>
        <span class="legend-key-item"><span class="rank-pair"><span class="rank-badge good">8</span></span> Top 12 player league-wide</span>
        <span class="legend-key-item"><span class="rank-pair"><span class="rank-badge warning">28</span></span> Top 36 player league-wide</span>
        <span class="legend-key-item"><span class="rank-pair"><span class="rank-badge critical">54</span></span> Outside the top 36</span>
      </div>
      <p class="muted" style="margin-top:10px;">Click a player's photo or name for full detail.</p>
    </div>
  </div>
  {% endif %}
</div></main>
"""

PLAYER_HTML = BASE_STYLE + make_header("league") + """
<main><div class="wrap" style="max-width:700px;">
  {% if tab == 'general' %}
  <a href="{{ ref }}" class="muted">&larr; Back</a>
  {% else %}
  <a href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=general&ref={{ ref|urlencode }}" class="muted">&larr; Back</a>
  {% endif %}
  <div class="panel">
    <div class="player-hero">
      <img src="{{ p.photo }}" alt="" onerror="this.style.visibility='hidden'">
      <div>
        <span class="pos-chip" style="background:var(--pos-{{ p.position.lower() }});">{{ p.position }}{{ p.position_rank if p.position_rank else '' }}</span>
        <h2 style="margin-top:6px;">{{ p.name }}</h2>
        <span class="muted">{{ p.team }}{% if p.age %} &middot; {{ p.age }} yo{% endif %}</span>
      </div>
    </div>
    <div class="fact-grid">
      <div class="fact-tile"><b>{{ p.overall_rank or '\u2014' }}</b><span>Overall rank</span></div>
      <div class="fact-tile"><b>{{ p.position_rank or '\u2014' }}</b><span>{{ p.position }} rank</span></div>
      <div class="fact-tile"><b>{{ p.value or '\u2014' }}</b><span>Dynasty value</span></div>
      {% if p.redraft_value %}<div class="fact-tile"><b>{{ p.redraft_value }}</b><span>Redraft value</span></div>{% endif %}
      {% if p.adp %}<div class="fact-tile"><b>{{ '%.1f'|format(p.adp) }}</b><span>Redraft ADP</span></div>{% endif %}
      {% if p.ppg %}<div class="fact-tile"><b>{{ p.ppg }}</b><span>PPG ({{ season }})</span></div>{% endif %}
      {% if p.community %}<div class="fact-tile"><b>{{ p.community.pct }}%</b><span>Community Start ({{ p.community.total }} votes)</span></div>{% endif %}
    </div>

    <div class="format-toggle" style="margin-top:20px;">
      <a class="{{ 'active' if tab=='general' else '' }}" href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=general&ref={{ ref|urlencode }}">General</a>
      <a class="{{ 'active' if tab=='log' else '' }}" href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=log&season={{ season }}&ref={{ ref|urlencode }}">Season Log</a>
      <a class="{{ 'active' if tab=='career' else '' }}" href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=career&ref={{ ref|urlencode }}">Career</a>
    </div>

    {% if tab == 'general' %}
    <div class="legend-box" style="margin-top:18px;">
      {% if p.college %}<div><b>College:</b> {{ p.college }}</div>{% endif %}
      {% if p.height or p.weight %}<div><b>Size:</b> {{ p.height or '\u2014' }}, {{ p.weight or '\u2014' }} lb</div>{% endif %}
      {% if p.years_exp is not none %}<div><b>Years exp.:</b> {{ p.years_exp }}</div>{% endif %}
      {% if p.injury %}<div><b>Injury status:</b> <span style="color:{{ p.injury.color }};font-weight:700;">{{ p.injury.title }}</span></div>{% endif %}
      {% if p.adp %}<div style="margin-top:6px;">ADP data via <a href="https://fantasyfootballcalculator.com/adp/ppr" target="_blank" style="color:var(--accent-ink);">Fantasy Football Calculator</a></div>{% endif %}
    </div>
    {% endif %}

    {% if tab == 'log' %}
    <div style="display:flex; align-items:center; justify-content:space-between; margin-top:18px;">
      {% if prev_season %}<a class="team-chip" href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=log&season={{ prev_season }}&ref={{ ref|urlencode }}">&larr; {{ prev_season }}</a>{% else %}<span></span>{% endif %}
      <strong>{{ season }} season</strong>
      {% if next_season %}<a class="team-chip" href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=log&season={{ next_season }}&ref={{ ref|urlencode }}">{{ next_season }} &rarr;</a>{% else %}<span></span>{% endif %}
    </div>
    <div style="display:flex; align-items:flex-end; gap:4px; height:140px; margin-top:16px; border-bottom:1px solid var(--line); padding-bottom:4px;">
      {% for w in weekly %}
      <div style="flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%;" title="Week {{ w.week }}: {{ w.pts }} pts">
        <div style="font-size:9px; color:var(--ink-muted); margin-bottom:2px;">{{ w.pts if w.pts else '' }}</div>
        <div style="width:70%; background:{{ 'var(--pos-' + p.position.lower() + ')' if w.pts else 'var(--paper-sunken)' }}; height:{{ w.pct }}%; min-height:2px; border-radius:2px 2px 0 0;"></div>
      </div>
      {% endfor %}
    </div>
    <div style="display:flex; gap:4px; margin-top:4px;">
      {% for w in weekly %}<div style="flex:1; text-align:center; font-size:9px; color:var(--ink-muted);">{{ w.week }}</div>{% endfor %}
    </div>
    <p class="muted" style="margin-top:14px;">PPR fantasy points per week, from Sleeper's real stats. Bye weeks / games not yet played show as empty.</p>
    {% endif %}

    {% if tab == 'career' %}
    <table class="rank-table" style="margin-top:14px;">
      <tr><th>Season</th><th>Games</th><th>Total FPTS</th><th>FPTS/G</th><th>Ovr Finish</th><th>Pos Finish</th></tr>
      {% for r in career_rows %}
      {% set orank = r.overall_finish %}
      {% set prank = r.position_finish %}
      {% set obg = 'var(--good-wash)' if orank and orank<=36 else ('var(--warning-wash)' if orank and orank<=100 else 'var(--critical-wash)') %}
      {% set ocol = 'var(--good)' if orank and orank<=36 else ('var(--warning)' if orank and orank<=100 else 'var(--critical)') %}
      {% set pbg = 'var(--good-wash)' if prank and prank<=12 else ('var(--warning-wash)' if prank and prank<=32 else 'var(--critical-wash)') %}
      {% set pcol = 'var(--good)' if prank and prank<=12 else ('var(--warning)' if prank and prank<=32 else 'var(--critical)') %}
      <tr>
        <td class="mono">{{ r.season }}</td>
        <td class="mono">{{ r.games if r.games else '\u2014' }}</td>
        <td class="mono">{{ r.fpts if r.fpts is not none else '\u2014' }}</td>
        <td class="mono">{{ r.fpts_per_game if r.fpts_per_game is not none else '\u2014' }}</td>
        <td>{% if orank %}<span style="font-family:'IBM Plex Mono',monospace; padding:3px 8px; border-radius:6px; display:inline-block; min-width:30px; text-align:center; background:{{ obg }}; color:{{ ocol }};">{{ orank }}</span>{% else %}<span class="mono">&mdash;</span>{% endif %}</td>
        <td>{% if prank %}<span style="font-family:'IBM Plex Mono',monospace; padding:3px 8px; border-radius:6px; display:inline-block; text-align:center; background:{{ pbg }}; color:{{ pcol }};">{{ p.position }}{{ prank }}</span>{% else %}<span class="mono">&mdash;</span>{% endif %}</td>
      </tr>
      {% endfor %}
    </table>
    <p class="muted" style="margin-top:10px;">Ovr/Pos Finish = real rank among all players that season, by total fantasy points scored &mdash; not dynasty value.</p>
    {% endif %}
  </div>

  <div class="panel">
    <h2 style="font-size:16px;">Latest News</h2>
    {% if news %}
      {% for n in news %}
      <div class="news-item">
        <a class="news-title" href="{{ n.link }}" target="_blank" rel="noopener">{{ n.title }}</a>
        <div class="news-meta">{{ n.ago }}{% if n.ago %} &middot; {% endif %}via {{ n.source }}</div>
        {% if n.desc %}<div class="news-desc">{{ n.desc }}</div>{% endif %}
      </div>
      {% endfor %}
    {% else %}
      <p class="muted" style="margin-top:10px;">No recent headlines mention {{ p.name }} right now.</p>
    {% endif %}
    <div class="news-credit">News via <a href="{{ news_source_url }}" target="_blank" rel="noopener">ESPN</a> and <a href="https://www.rotowire.com/football/" target="_blank" rel="noopener">RotoWire</a> &middot; headline &amp; summary only, links back to the original article.</div>
  </div>

  {% if depth_chart %}
  <div class="panel">
    <h2 style="font-size:16px;">{{ p.team }} Depth Chart</h2>
    <p class="muted" style="margin-top:4px;">Click any player to open their page.</p>
    <div class="col-grid" style="margin-top:12px;">
      {% for col in depth_chart %}
      <div>
        <div class="col-head {{ col.base.lower() }}"><span>{{ col.slot }}</span></div>
        {% for dp in col.players %}
        <div class="player-row">
          <div class="pname-row">
            <span class="depth-rank">{{ dp.rank_label }}</span>
            <img src="{{ dp.photo }}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
            {% if dp.sid == sid %}
            <span class="depth-you">{{ dp.name }}</span><span class="depth-you-tag">you</span>
            {% else %}
            <a class="pname" href="/player?sid={{ dp.sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=general&ref={{ ('/player?sid=' ~ sid ~ '&numqbs=' ~ num_qbs ~ '&u=' ~ username ~ '&tab=' ~ tab ~ '&ref=' ~ ref)|urlencode }}">{{ dp.name }}</a>
            {% endif %}
            {% if dp.injury and dp.sid != sid %}
              {% if dp.injury.is_ir %}
              <span class="injury-badge injury-ir" title="{{ dp.injury.title }}">&#10013;</span>
              {% else %}
              <span class="injury-badge injury-{{ dp.injury.tier }}" title="{{ dp.injury.title }}">{{ dp.injury.label }}</span>
              {% endif %}
            {% endif %}
          </div>
        </div>
        {% endfor %}
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}
</div></main>
"""

SCORES_HTML = BASE_STYLE + make_header("scores") + """
<style>
  .sc-page{
    --sc-bg:#0d0f0d; --sc-surface:#151815; --sc-surface2:#1c201c;
    --sc-line:rgba(255,255,255,0.08); --sc-text:#e8e6df; --sc-muted:#8b9089;
    --sc-live:#d1a521; --sc-live-wash:rgba(209,165,33,0.16);
    background:var(--sc-bg); color:var(--sc-text); padding-bottom:60px;
    font-family:"Source Sans 3",system-ui,sans-serif;
  }
  .sc-toolbar{ position:sticky; top:64px; z-index:40; background:color-mix(in srgb, var(--sc-bg) 92%, transparent); backdrop-filter:blur(8px); border-bottom:1px solid var(--sc-line); padding:16px 0; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  .sc-title{ font-family:"Big Shoulders Display"; font-size:22px; font-weight:800; text-transform:uppercase; margin-right:auto; color:var(--sc-text); }
  .sc-week-nav{ display:flex; align-items:center; gap:8px; }
  .sc-icon-btn{ width:36px; height:36px; border-radius:8px; background:var(--sc-surface); border:1px solid var(--sc-line); color:var(--sc-muted); display:flex; align-items:center; justify-content:center; cursor:pointer; font-size:15px; }
  .sc-icon-btn:hover{ color:var(--sc-text); border-color:var(--accent); }
  .sc-icon-btn.active{ color:var(--sc-text); border-color:var(--accent); }
  .sc-week-label{ font-weight:700; font-size:13.5px; min-width:80px; text-align:center; }

  /* Date strip: one tab per game day, labelled with its week and date,
     scrolling horizontally into future weeks. Underline-style rather
     than pill-style so a long run of tabs reads as one continuous
     timeline instead of a row of disconnected buttons.
     Deliberately NOT scroll-snapped. Snapping locked each tab to the
     middle of the strip, which hid the fact that there was anything
     either side of it; free scrolling lets the next tab sit half-cut at
     the edge, which is what tells you the strip scrolls at all. */
  .sc-day-tabs{
    display:flex; gap:0; margin-top:12px; overflow-x:auto;
    -webkit-overflow-scrolling:touch; scrollbar-width:none;
    border-bottom:1px solid var(--sc-line);
    /* The same job from the other side: the right edge fades out, so a
       strip that runs past the screen never looks like it ends there. */
    -webkit-mask-image:linear-gradient(to right, #000 calc(100% - 34px), transparent);
    mask-image:linear-gradient(to right, #000 calc(100% - 34px), transparent);
  }
  .sc-day-tabs::-webkit-scrollbar{ display:none; }
  .sc-day-tab{
    cursor:pointer; user-select:none; flex:none;
    display:flex; flex-direction:column; align-items:center; gap:1px;
    padding:8px 16px 10px; min-width:92px; position:relative;
    border-bottom:2px solid transparent; color:var(--sc-muted);
  }
  .sc-day-tab .wk{ font-size:12px; font-weight:700; letter-spacing:0.01em; white-space:nowrap; }
  .sc-day-tab .dow{ font-size:18px; font-weight:800; font-family:"Big Shoulders Display"; letter-spacing:0.02em; }
  .sc-day-tab.active{ color:var(--accent-ink); border-bottom-color:var(--accent-ink); }
  .sc-day-tab.today:not(.active){ color:var(--sc-text); }
  /* A live dot sits above the tab, so an in-progress slate is visible
     without reading any of the labels. */
  .sc-day-tab .dot{
    position:absolute; top:2px; left:50%; transform:translateX(-50%);
    width:6px; height:6px; border-radius:50%; background:var(--sc-live);
  }
  .sc-day-tab .dot.done{ background:var(--sc-muted); opacity:0.55; }

  .sc-month{ display:none; margin-top:16px; background:var(--sc-surface); border:1px solid var(--sc-line); border-radius:14px; padding:16px; }
  .sc-month.open{ display:block; }
  .sc-month-head{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; font-weight:700; font-family:"Big Shoulders Display"; text-transform:uppercase; }
  .sc-month-grid{ display:grid; grid-template-columns:repeat(7,1fr); gap:6px; }
  .sc-month-dow{ font-size:10px; text-transform:uppercase; color:var(--sc-muted); text-align:center; padding-bottom:4px; }
  .sc-month-cell{ aspect-ratio:1; border-radius:8px; background:var(--sc-surface2); display:flex; flex-direction:column; align-items:center; justify-content:center; font-size:12px; color:var(--sc-muted); cursor:pointer; border:1px solid transparent; }
  .sc-month-cell:hover{ border-color:var(--accent); }
  .sc-month-cell.empty{ visibility:hidden; cursor:default; }
  .sc-month-cell.today{ border-color:var(--accent); color:var(--sc-text); }
  .sc-month-cell.selected{ background:var(--accent); color:var(--accent-on); }
  .sc-month-cell .dot{ width:5px; height:5px; border-radius:50%; background:var(--sc-live); margin-top:3px; }

  /* Games grid: columns of four, scrolling horizontally into the rest of
     the slate. On a wide screen several columns are visible at once and
     the scroll only kicks in for a genuinely long slate.
     The columns used to snap MANDATORY, which paged the board one clean
     screenful at a time -- and a board that only ever shows whole
     columns looks like a board with nothing beside it. Free scrolling,
     a column narrower than the screen, and a faded right edge all say
     the same thing instead: there is more over here. */
  .sc-games{
    display:grid; grid-auto-flow:column; grid-template-rows:repeat(4, auto);
    grid-auto-columns:minmax(280px, 1fr); gap:0; margin-top:16px;
    overflow-x:auto; -webkit-overflow-scrolling:touch;
    scrollbar-width:none; border:1px solid var(--sc-line); border-radius:12px;
  }
  .sc-games::-webkit-scrollbar{ display:none; }
  @media (min-width:900px){ .sc-games{ grid-auto-columns:minmax(330px, 1fr); } }
  /* Just under a screenful on a phone, so the next column always shows
     an edge rather than hiding exactly off-screen. */
  @media (max-width:640px){ .sc-games{ grid-auto-columns:calc(100vw - 74px); } }

  .sc-game-card{
    display:flex; flex-direction:column; gap:8px; padding:12px 14px;
    text-decoration:none; color:var(--sc-text); cursor:pointer;
    border-right:1px solid var(--sc-line); border-bottom:1px solid var(--sc-line);
    background:var(--sc-surface); min-width:0;
  }
  .sc-game-card:hover{ background:var(--sc-surface2); }
  /* A live game is outlined, the way the reference board marks the games
     actually worth looking at right now. */
  .sc-game-card.live{ background:var(--sc-surface2); box-shadow:inset 0 0 0 1px var(--accent-ink); }
  /* Finished games are still worth showing, but they've been settled --
     they recede so the live and upcoming cards in front of them read
     first. */
  .sc-game-card.done{ opacity:0.62; }
  .sc-game-card.done:hover{ opacity:1; }
  .sc-game-top{ display:flex; align-items:stretch; gap:10px; }
  /* Both teams stack on the left, game state on the right -- the
     reference layout, and it reads better than left/right teams once a
     card is only ~300px wide. */
  .sc-game-teams{ display:flex; flex-direction:column; gap:6px; flex:1; min-width:0; }
  .sc-game-meta{ display:flex; flex-direction:column; align-items:flex-end; justify-content:center; gap:2px; flex:none; text-align:right; }
  .sc-team-row{ display:flex; align-items:center; gap:8px; min-width:0; }
  .sc-team-row img{ width:26px; height:26px; object-fit:contain; flex:none; }
  .sc-team-row .nm{ font-size:13px; color:var(--sc-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .sc-team-row .sc-game-score{ margin-left:auto; }
  /* The leading side is the one in full-strength text; the trailing side
     stays muted. Beats a coloured dot nobody has a legend for. */
  .sc-team-row.leading .nm{ color:var(--sc-text); font-weight:700; }
  .sc-team-row.leading .sc-game-score{ color:var(--sc-text); }
  .sc-game-kick{ font-size:13px; font-weight:700; color:var(--sc-text); white-space:nowrap; }
  .sc-game-net{ font-size:11px; color:var(--sc-muted); white-space:nowrap; }
  .sc-game-spread{ font-size:11px; color:var(--sc-muted); white-space:nowrap; }
  .sc-game-live-clock{ font-size:13px; font-weight:800; color:var(--sc-live); white-space:nowrap; }

  /* --- daily performer board --- */
  .sc-section-head{ display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin:26px 0 10px; }
  .sc-section-head h2{ font-family:"Big Shoulders Display"; font-size:22px; font-weight:800; text-transform:uppercase; margin:0; color:var(--sc-text); }
  .sc-section-head .sub{ font-size:11.5px; color:var(--sc-muted); }
  .sc-viewall{ font-size:12.5px; font-weight:700; color:var(--accent-ink); text-decoration:none; white-space:nowrap; }
  .sc-perf-note{ font-size:11.5px; color:var(--sc-muted); margin:20px 0 -10px; }
  .sc-perf{ display:flex; flex-direction:column; border:1px solid var(--sc-line); border-radius:12px; overflow:hidden; background:var(--sc-surface); }
  .sc-perf-row{ display:flex; align-items:center; gap:10px; padding:10px 12px; border-top:1px solid var(--sc-line); text-decoration:none; color:var(--sc-text); }
  .sc-perf-row:first-child{ border-top:none; }
  .sc-perf-row:hover{ background:var(--sc-surface2); }
  .sc-perf-rank{ font-family:"IBM Plex Mono"; font-size:12px; color:var(--sc-muted); width:20px; flex:none; text-align:right; font-variant-numeric:tabular-nums; }
  .sc-perf-row img{ width:38px; height:38px; border-radius:50%; object-fit:cover; background:var(--sc-surface2); flex:none; }
  .sc-perf-main{ flex:1; min-width:0; display:flex; flex-direction:column; gap:3px; }
  .sc-perf-name{ font-weight:700; font-size:13.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .sc-perf-name .pts{ color:var(--sc-muted); font-weight:600; font-size:12px; margin-left:6px; }
  .sc-perf-stats{ display:flex; align-items:baseline; gap:9px; flex-wrap:wrap; }
  .sc-perf-stat b{ font-family:"IBM Plex Mono"; font-size:15px; font-weight:700; }
  .sc-perf-stat span{ font-size:10.5px; color:var(--sc-muted); margin-left:2px; }
  .sc-perf-sub{ font-size:11px; color:var(--sc-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .sc-perf-grade{ font-family:"IBM Plex Mono"; font-size:20px; font-weight:700; flex:none; min-width:44px; text-align:right; font-variant-numeric:tabular-nums; }
  .sc-perf-empty{ color:var(--sc-muted); padding:24px; text-align:center; font-size:13px; }
  .sc-group{ display:none; }
  .sc-group.on{ display:block; }

  /* Injuries and birthdays: the same row, since they say the same shape
     of thing -- a face, a name, one line about what changed, and when. */
  .sc-feed{ display:flex; flex-direction:column; border:1px solid var(--sc-line);
            border-radius:12px; overflow:hidden; background:var(--sc-surface); }
  .sc-feed-row{ display:flex; align-items:center; gap:11px; padding:10px 13px;
                border-top:1px solid var(--sc-line); text-decoration:none;
                color:var(--sc-text); }
  .sc-feed-row:first-child{ border-top:none; }
  .sc-feed-row:hover{ background:var(--sc-surface2); }
  /* The crest sits on the corner of the headshot rather than beside it,
     so a row stays one column of faces however long the names run. */
  .sc-feed-mug{ position:relative; flex:none; width:40px; height:40px; }
  .sc-feed-mug img{ width:40px; height:40px; border-radius:50%; object-fit:cover;
                    background:var(--sc-surface2); }
  .sc-feed-mug img.crest{ position:absolute; right:-3px; bottom:-2px; width:18px;
                          height:18px; border-radius:0; object-fit:contain;
                          background:none; }
  .sc-feed-main{ flex:1; min-width:0; display:flex; flex-direction:column; gap:2px; }
  .sc-feed-name{ font-weight:700; font-size:14px; white-space:nowrap;
                 overflow:hidden; text-overflow:ellipsis; }
  .sc-feed-sub{ font-size:12.5px; color:var(--sc-muted); }
  .sc-feed-sub .arrow{ padding:0 2px; }
  .sc-feed-sub b.good{ color:var(--good); }
  .sc-feed-sub b.bad{ color:var(--critical); }
  .sc-feed-ago{ flex:none; font-size:11.5px; color:var(--sc-muted);
                font-family:"IBM Plex Mono"; }

  /* Power-ranking strip. Deliberately terser than the standings table it
     links to -- rank, crest, record, differential -- so it reads at a
     glance and the full table stays one tap away. */
  .sc-power{ display:flex; flex-direction:column; border:1px solid var(--sc-line);
             border-radius:12px; overflow:hidden; background:var(--sc-surface); }
  .sc-power-row{ display:flex; align-items:center; gap:11px; padding:9px 13px;
                 border-top:1px solid var(--sc-line); text-decoration:none;
                 color:var(--sc-text); }
  .sc-power-row:first-child{ border-top:none; }
  .sc-power-row:hover{ background:var(--sc-surface2); }
  .sc-power-rank{ font-family:"IBM Plex Mono"; font-size:12.5px; color:var(--sc-muted);
                  width:20px; flex:none; text-align:right; font-variant-numeric:tabular-nums; }
  .sc-power-row img{ width:26px; height:26px; object-fit:contain; flex:none; }
  .sc-power-name{ font-weight:700; font-size:14px; flex:1; min-width:0;
                  font-family:"Big Shoulders Display"; letter-spacing:0.02em; }
  .sc-power-rec{ font-family:"IBM Plex Mono"; font-size:12.5px; color:var(--sc-muted); flex:none; }
  .sc-power-diff{ font-family:"IBM Plex Mono"; font-size:12.5px; font-weight:700;
                  flex:none; min-width:38px; text-align:right;
                  font-variant-numeric:tabular-nums; color:var(--sc-muted); }
  .sc-power-diff.pos{ color:var(--good); }
  .sc-power-diff.neg{ color:var(--critical); }
  /* Deliberately NOT colour-banded. A ranked list is already ordered
     best-to-worst, so colouring each score repeats information the
     position already carries, and three colours down a long list reads
     as noise. The mark before the number does the signalling instead. */
  .sc-perf-grade{ color:var(--sc-text); display:inline-flex; align-items:center; gap:5px; justify-content:flex-end; }
  .score-mark{ width:11px; height:13px; flex:none; opacity:0.55; }
  /* Count of your players, sitting on that team's own row. Reads as
     part of the team line rather than as a separate fact to be matched
     back to a side. */
  .sc-mine{
    display:inline-flex; align-items:center; justify-content:center; flex:none;
    min-width:19px; height:19px; padding:0 5px; margin-left:6px;
    border-radius:99px; background:var(--good-wash); color:var(--good);
    font-family:"IBM Plex Mono"; font-size:11px; font-weight:700; line-height:1;
  }
  .sc-my-players{ padding-top:7px; margin-top:1px; border-top:1px solid var(--sc-line);
                  font-size:10.5px; color:var(--sc-muted); }
  .sc-sync-banner{ display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; background:var(--sc-surface); border:1px solid var(--sc-line); border-radius:10px; padding:10px 16px; margin-top:14px; font-size:13px; color:var(--sc-muted); }
  .sc-sync-banner a{ color:var(--accent-ink); text-decoration:none; font-weight:700; }
  .sc-game-score{ font-family:"IBM Plex Mono"; font-size:20px; font-weight:700; min-width:34px; text-align:center; }
  .sc-status-pill{ font-size:10.5px; font-weight:700; text-transform:uppercase; padding:3px 9px; border-radius:99px; }
  .sc-status-pill.scheduled{ background:var(--sc-surface2); color:var(--sc-muted); }
  .sc-status-pill.final{ background:var(--sc-surface2); color:var(--sc-muted); }
  .sc-status-pill.in_progress{ background:var(--sc-live-wash); color:var(--sc-live); }
  .sc-empty{ color:var(--sc-muted); padding:30px; text-align:center; }

  @media (max-width: 640px) {
    .sc-toolbar{ flex-wrap:wrap; }
    .sc-title{ width:100%; }
    .sc-game-card{ gap:6px; padding:11px 12px; }
    .sc-team-row img{ width:23px; height:23px; }
    .sc-team-row .nm{ font-size:12.5px; }
    .sc-game-score{ font-size:17px; min-width:24px; }
    .sc-day-tab{ min-width:82px; padding:8px 12px 10px; }
  }
</style>

<div class="sc-page">
<div class="wrap">
  {% if load_error %}<div class="error">Couldn't load live scores right now: {{ load_error }}</div>{% endif %}
  <div class="sc-toolbar">
    <span class="sc-title">Scores</span>
    <div class="sc-week-nav">
      <button type="button" class="sc-icon-btn" id="scPrevWeek" title="Previous week">&larr;</button>
      <span class="sc-week-label" id="scWeekLabel">Week {{ week }}</span>
      <button type="button" class="sc-icon-btn" id="scNextWeek" title="Next week">&rarr;</button>
    </div>
    <button type="button" class="sc-icon-btn" id="scMonthToggle" title="Month view">&#128197;</button>
    <a class="sc-viewall" href="/standings" style="margin-left:10px;">Standings &rsaquo;</a>
  </div>

  {% if has_synced_leagues %}
  <div class="sc-sync-banner">
    <span>Showing how many of <strong style="color:var(--sc-text);">{{ username }}</strong>'s players are in each game.</span>
    <a href="/league-manager">Manage synced leagues</a>
  </div>
  {% else %}
  <div class="sc-sync-banner">
    <span>Sync your leagues to see which of your players are playing in each game.</span>
    <a href="/league-manager">Sync your leagues &rarr;</a>
  </div>
  {% endif %}

  <div class="sc-day-tabs" id="scDayTabs"></div>

  <div class="sc-month" id="scMonth">
    <div class="sc-month-head">
      <button type="button" class="sc-icon-btn" id="scMonthPrev">&larr;</button>
      <span id="scMonthLabel"></span>
      <button type="button" class="sc-icon-btn" id="scMonthNext">&rarr;</button>
    </div>
    <div class="sc-month-grid" id="scMonthGrid"></div>
  </div>

  <div class="sc-games" id="scGames"></div>

  <!-- First after the scores: the day's best performances, every
       position in one ranked list. The per-position sections further
       down answer "who led at each spot"; this one answers "who had the
       best day, full stop", which is a different question. -->
  <div class="sc-group" id="scGroupAll">
    <div class="sc-section-head">
      <h2>Player Rankings</h2>
      <a class="sc-viewall" id="scPerfMore" href="/performances">View all &rsaquo;</a>
    </div>
    <div class="sc-perf-note" id="scPerfSub"></div>
    <div class="sc-perf" id="scPerf"></div>
  </div>

  {% if power %}
  <!-- Second: who is actually good right now, on the four-round window
       rather than the whole season. -->
  <div class="sc-section-head">
    <h2>Team Rankings</h2>
    <a class="sc-viewall" href="/standings">Full standings &rsaquo;</a>
  </div>
  <div class="sc-power">
    {% for t in power %}
    <a class="sc-power-row" href="/team?abbr={{ t.team }}&amp;season={{ season }}">
      <span class="sc-power-rank">{{ t.rank }}</span>
      <img src="{{ t.logo }}" alt="" loading="lazy"
           onerror="this.style.visibility='hidden'">
      <span class="sc-power-name">{{ t.team }}</span>
      <span class="sc-power-rec">{{ t.record }}</span>
      <span class="sc-power-diff {{ 'pos' if t.diff and t.diff > 0 else ('neg' if t.diff and t.diff < 0 else '') }}">
        {%- if t.diff is not none %}{{ '%+d'|format(t.diff) }}{% endif -%}
      </span>
    </a>
    {% endfor %}
  </div>
  {% endif %}

  <!-- Third: the day's leaders, one section per position family, the way
       the reference board lays them out. Filled client-side from the
       same baked board the day tabs filter, so switching days moves
       these too. -->
  {% for g in perf_groups %}
  <div class="sc-group" id="scGroup-{{ g.key }}" data-positions="{{ g.positions|join(',') }}">
    <div class="sc-section-head">
      <h2>{{ g.title }}</h2>
      <a class="sc-viewall" data-group-more="{{ g.key }}"
         href="/performances">View more &rsaquo;</a>
    </div>
    <div class="sc-perf" data-group-rows="{{ g.key }}"></div>
  </div>
  {% endfor %}

  {% if injuries %}
  <!-- Designation changes, newest first. Sleeper publishes only the
       current status, so these exist because the app has been watching
       the dump refresh and writing down what moved. -->
  {# No "View more" here on purpose: there is no fuller injuries page to
     send anyone to, and a link that goes somewhere almost-right is worse
     than no link. Each row opens that player instead. #}
  <div class="sc-section-head">
    <h2>Injuries</h2>
  </div>
  <div class="sc-feed">
    {% for r in injuries %}
    <a class="sc-feed-row" href="/player?sid={{ r.sid }}">
      <span class="sc-feed-mug">
        <img src="{{ r.photo }}" alt="" loading="lazy"
             onerror="this.style.visibility='hidden'">
        <img class="crest" src="{{ r.logo }}" alt=""
             onerror="this.style.display='none'">
      </span>
      <span class="sc-feed-main">
        <span class="sc-feed-name">{{ r.name }}</span>
        <span class="sc-feed-sub">
          {{ r.from }} <span class="arrow">&rarr;</span>
          <b class="{{ 'good' if r.good else 'bad' }}">{{ r.to }}</b>
        </span>
      </span>
      {% if r.ago %}<span class="sc-feed-ago">{{ r.ago }}</span>{% endif %}
    </a>
    {% endfor %}
  </div>
  {% endif %}

  {% if birthdays %}
  <div class="sc-section-head">
    <h2>Birthdays</h2>
  </div>
  <div class="sc-feed">
    {% for b in birthdays %}
    <a class="sc-feed-row" href="/player?sid={{ b.sid }}">
      <span class="sc-feed-mug">
        <img src="{{ b.photo }}" alt="" loading="lazy"
             onerror="this.style.visibility='hidden'">
        <img class="crest" src="{{ b.logo }}" alt=""
             onerror="this.style.display='none'">
      </span>
      <span class="sc-feed-main">
        <span class="sc-feed-name">{{ b.name }}</span>
        <span class="sc-feed-sub">Happy {{ b.age_label }} Birthday &#127881;</span>
      </span>
      <span class="sc-feed-ago">{{ b.position }}{% if b.team %} &middot; {{ b.team }}{% endif %}</span>
    </a>
    {% endfor %}
  </div>
  {% endif %}
</div>
</div>

<script>
const SCORES_WEEK = {{ games|tojson }};
const SCORES_PERFORMERS = {{ performers|tojson }};
const SCORE_MARK = {{ score_mark|tojson }};
const SEASON_DAYS = {{ season_days|tojson }};
const CURRENT_SEASON = {{ current_season }};
const CURRENT_WEEK = {{ current_week }};
let scSeason = {{ season }};
let scWeek = {{ week }};
const scSeasonType = {{ season_type }};
// The server's own calendar day, kept only as a last resort. It is NOT
// what "today" means on this page: Render's clock runs on UTC, so a
// Sunday 8pm Eastern visit lands on Monday there, and the board opened
// on Monday Night Football while the visitor was still watching Sunday.
// The real today is computed from the visitor's own clock below.
const scServerTodayKey = {{ today_key|tojson }};

(function(){
  const daysIndex = {};  // 'YYYY-MM-DD' (visitor's LOCAL calendar day) -> [game, ...]

  function dateKey(d){
    return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
  }

  // ESPN's game "date" is UTC (a "Z"-suffixed ISO string) -- a Sunday
  // 8:20pm ET kickoff is already past midnight UTC, so grouping games by
  // date has to convert to the visitor's own local day, not just read the
  // UTC calendar date off the string. `new Date(iso)` parses the UTC
  // instant correctly; dateKey()'s getFullYear/getMonth/getDate are local
  // getters, so this naturally lands the game on the day it's actually
  // played from the visitor's own clock.
  function localDateKey(isoString){
    return dateKey(new Date(isoString));
  }

  // Every other date on this page is the visitor's local day, and this
  // has to agree with them or the strip highlights one day and opens
  // another.
  function todayKey(){
    try { return dateKey(new Date()); } catch (e) { return scServerTodayKey; }
  }
  const scTodayKey = todayKey();

  function indexGames(games){
    games.forEach(function(g){
      if(!g.date) return;
      const key = localDateKey(g.date);
      g.date_key = key;
      if(!daysIndex[key]) daysIndex[key] = [];
      const existingIdx = daysIndex[key].findIndex(function(x){ return x.id === g.id; });
      if(existingIdx >= 0) daysIndex[key][existingIdx] = g;
      else daysIndex[key].push(g);
    });
  }
  indexGames(SCORES_WEEK);

  // Set for real in the init block at the bottom, once the season's game
  // days are known -- see pickOpeningDay().
  let selectedDay = scTodayKey;
  let monthCursor = new Date(selectedDay + "T00:00:00");

  const dayTabsEl = document.getElementById('scDayTabs');
  const gamesEl = document.getElementById('scGames');
  const weekLabelEl = document.getElementById('scWeekLabel');
  const monthEl = document.getElementById('scMonth');
  const monthGridEl = document.getElementById('scMonthGrid');
  const monthLabelEl = document.getElementById('scMonthLabel');

  // A swipeable strip showing ONLY days that actually have games -- not
  // every calendar day. The page bakes in the requested week plus the
  // week before/after (see _nearby_weeks_games), so there's normally a
  // handful of real game days to slide through right away; Prev/Next
  // Week below adds more as confirmed data comes in. Slides via native
  // horizontal scroll (touch swipe on mobile, trackpad/shift-wheel on
  // desktop) -- no custom drag code needed for that.
  // Every game day in the season, keyed by the visitor's own local
  // calendar day. Built from the schedule rather than from whichever
  // weeks happen to be loaded, so the strip runs end to end and the week
  // arrows become a shortcut rather than the only way to move.
  const seasonDayWeeks = {};
  (SEASON_DAYS || []).forEach(function(d){
    if (!d.kickoff) return;
    const key = localDateKey(d.kickoff);
    if (!key || key.indexOf('NaN') !== -1) return;
    if (seasonDayWeeks[key] === undefined) seasonDayWeeks[key] = d.week;
  });

  function allDayKeys(){
    const keys = {};
    Object.keys(seasonDayWeeks).forEach(function(k){ keys[k] = true; });
    // A day we've already loaded games for belongs in the strip even if
    // the schedule table hasn't been synced that far yet.
    Object.keys(daysIndex).forEach(function(k){ if (daysIndex[k].length) keys[k] = true; });
    return Object.keys(keys).sort();
  }

  // Which day the board should open on: today if there is football
  // today, otherwise the nearest day there is. Looking both ways matters
  // -- on a Tuesday the game worth seeing is last night's, on a
  // Wednesday it is Thursday's -- and a tie goes forward, to the game
  // that hasn't been played yet. Never a fixed day and never the
  // server's idea of today.
  function pickOpeningDay(){
    const today = scTodayKey;
    const keys = allDayKeys();
    if (!keys.length || keys.indexOf(today) >= 0) return today;
    const ahead = keys.filter(function(k){ return k > today; });
    const behind = keys.filter(function(k){ return k < today; });
    const next = ahead[0] || null;
    const prev = behind.length ? behind[behind.length - 1] : null;
    if (!next) return prev || today;
    if (!prev) return next;
    const day = 86400000;
    const t = new Date(today + "T00:00:00").getTime();
    const forward = (new Date(next + "T00:00:00").getTime() - t) / day;
    const back = (t - new Date(prev + "T00:00:00").getTime()) / day;
    return back < forward ? prev : next;
  }

  // The header says "Week N", the performer board is a week's board, and
  // the day strip runs across week boundaries -- so whenever the
  // selected day belongs to another week, those follow it rather than
  // staying on whatever week the page was rendered for.
  function syncWeekToDay(key){
    const games = daysIndex[key] || [];
    const wk = (games.length && games[0].week) || seasonDayWeeks[key];
    if (wk && wk !== scWeek) {
      scWeek = wk;
      weekLabelEl.textContent = 'Week ' + scWeek;
      refreshPerformers();
    }
  }

  function renderDayTabs(){
    dayTabsEl.innerHTML = '';
    const keys = allDayKeys();
    keys.forEach(function(key){
      const d = new Date(key + "T00:00:00");
      const isActive = key === selectedDay;
      const isToday = key === scTodayKey;
      const btn = document.createElement('div');
      btn.className = 'sc-day-tab' + (isActive ? ' active' : '') + (isToday ? ' today' : '');
      btn.dataset.dateKey = key;
      const games = daysIndex[key] || [];
      const anyLive = games.some(function(g){ return g.status === 'in_progress'; });
      // "W1 / Sep 13" over "Sun" -- the week matters as much as the date
      // when you're scrolling several weeks ahead, and neither is
      // guessable from the other.
      // Prefer the loaded game's own week, fall back to the schedule's --
      // a tab for a day whose games aren't loaded yet still needs a label.
      const wkNum = (games.length && games[0].week) || seasonDayWeeks[key];
      const wk = wkNum ? 'W' + wkNum + ' \u00b7 ' : '';
      const anyDone = games.some(function(g){ return g.status === 'final'; });
      btn.innerHTML =
        (anyLive || anyDone ? '<span class="dot' + (anyLive ? '' : ' done') + '"></span>' : '') +
        '<span class="wk">' + wk + d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + '</span>' +
        '<span class="dow">' + d.toLocaleDateString(undefined, { weekday: 'short' }) + '</span>';
      btn.addEventListener('click', function(){ selectDay(key); });
      dayTabsEl.appendChild(btn);
    });
    const activeEl = dayTabsEl.querySelector('.sc-day-tab.active');
    if (activeEl && typeof activeEl.scrollIntoView === 'function') {
      activeEl.scrollIntoView({ inline: 'center', block: 'nearest' });
    }
  }

  // What's still to come leads the board; finished games fall to the back.
  // A slate you can still do something about is the reason to open this
  // page at all, and on a Sunday afternoon the early games would
  // otherwise sit in front of every window that hasn't kicked off yet.
  // Live first (happening now), then scheduled (about to), then final.
  const GAME_ORDER = { in_progress: 0, scheduled: 1, final: 2 };

  function renderGames(){
    const games = (daysIndex[selectedDay] || []).slice().sort(function(a, b){
      const pa = GAME_ORDER[a.status] === undefined ? 1 : GAME_ORDER[a.status];
      const pb = GAME_ORDER[b.status] === undefined ? 1 : GAME_ORDER[b.status];
      if (pa !== pb) return pa - pb;
      // Within a group, the order that answers the question you are
      // asking of it. A game still to come: soonest first, because the
      // next kickoff is the one you care about. A game already finished:
      // most recent first, because the late window is the news and the
      // early games have been sitting there all afternoon.
      const cmp = String(a.date || '').localeCompare(String(b.date || ''));
      return a.status === 'final' ? -cmp : cmp;
    });
    gamesEl.innerHTML = '';
    if(!games.length){
      gamesEl.innerHTML = '<div class="sc-empty">No games this day.</div>';
      return;
    }
    games.forEach(function(g){
      const a = document.createElement('a');
      a.className = 'sc-game-card' + (g.status === 'in_progress' ? ' live' : '') +
                    (g.status === 'final' ? ' done' : '');
      a.href = '/game?id=' + encodeURIComponent(g.id);

      const away = g.away || {}, home = g.home || {};
      const aScore = away.score == null ? '' : away.score;
      const hScore = home.score == null ? '' : home.score;
      // Highlight the leader, but only once there's a real score to lead
      // with -- before kickoff both sides are 0 and neither is "winning".
      const played = g.status !== 'scheduled';
      const an = Number(aScore), hn = Number(hScore);
      const awayLeads = played && isFinite(an) && isFinite(hn) && an > hn;
      const homeLeads = played && isFinite(an) && isFinite(hn) && hn > an;

      // The count of your players rides on the team's OWN row. It used to
      // sit in a separate strip under both teams, one pill pushed left
      // and one right, which meant working out which number belonged to
      // which team by matching horizontal position -- fine with two
      // pills, ambiguous the moment one team had none.
      function teamRow(t, score, leads, mineCount){
        const pill = mineCount
          ? '<span class="sc-mine" title="' + mineCount + ' of your players">' + mineCount + '</span>'
          : '';
        return '<div class="sc-team-row' + (leads ? ' leading' : '') + '">' +
          '<img src="' + (t.logo || '') + '" alt="" onerror="this.style.visibility=\\'hidden\\'">' +
          '<span class="nm">' + (t.name || t.abbr || '') + '</span>' +
          pill +
          '<span class="sc-game-score">' + score + '</span>' +
        '</div>';
      }

      // Right-hand column: what's happening. A live game leads with the
      // clock, a finished one says Final, an upcoming one shows kickoff.
      let meta = '';
      if (g.status === 'in_progress') {
        meta = '<span class="sc-game-live-clock">' + (g.clock || '') +
               (g.period ? ' Q' + g.period : '') + '</span>';
      } else if (g.status === 'final') {
        meta = '<span class="sc-game-kick">Final</span>';
      } else {
        const kick = g.date ? new Date(g.date).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '';
        meta = '<span class="sc-game-kick">' + kick + '</span>';
      }
      if (g.broadcast) meta += '<span class="sc-game-net">' + g.broadcast + '</span>';
      // The spread only means anything before kickoff -- once a game is
      // live or final the actual score has superseded it.
      if (g.spread && g.status === 'scheduled') {
        meta += '<span class="sc-game-spread">' + g.spread + '</span>';
      }

      const topRow =
        '<div class="sc-game-top">' +
          '<div class="sc-game-teams">' +
            teamRow(away, aScore, awayLeads, (g.my_away_players || []).length) +
            teamRow(home, hScore, homeLeads, (g.my_home_players || []).length) +
          '</div>' +
          '<div class="sc-game-meta">' + meta + '</div>' +
        '</div>';
      // Compact count-only pills here on purpose -- the full name-by-name
      // breakdown lives on /game (see the chip grid there). A card in a
      // list of a dozen games has no room for a wall of comma-separated
      // names without either truncating illegibly or blowing out the
      // row height, so the card just answers "how many", and clicking
      // through answers "who".
      // The per-team counts are now on the rows themselves, so all that's
      // left here is the total -- one number that answers "is this game
      // worth watching for me at all" without re-reading both rows.
      const awayMine = g.my_away_players || [];
      const homeMine = g.my_home_players || [];
      const totalMine = awayMine.length + homeMine.length;
      const myRow = totalMine
        ? '<div class="sc-my-players">' + totalMine +
          (totalMine === 1 ? ' of your players' : ' of your players') + ' in this game</div>'
        : '';
      a.innerHTML = topRow + myRow;
      gamesEl.appendChild(a);
    });
  }

  function selectDay(key){
    selectedDay = key;
    // Sync the label off the schedule first, so a day whose games are
    // still loading (or fail to load) doesn't sit under the wrong week.
    syncWeekToDay(key);
    if(!daysIndex[key]){
      fetch('/api/scoreboard?date=' + key.replace(/-/g, ''))
        .then(function(r){ return r.json(); })
        .then(function(data){
          daysIndex[key] = data.games || [];
          syncWeekToDay(key);
          renderDayTabs();
          renderGames();
          renderPerformers();
          renderMonth();
        })
        .catch(function(){ daysIndex[key] = []; renderGames(); renderPerformers(); });
      return;
    }
    syncWeekToDay(key);
    renderDayTabs();
    renderGames();
    renderPerformers();
    renderMonth();
  }

  function renderMonth(){
    const y = monthCursor.getFullYear(), m = monthCursor.getMonth();
    monthLabelEl.textContent = monthCursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
    monthGridEl.innerHTML = '';
    ['S','M','T','W','T','F','S'].forEach(function(d){
      const el = document.createElement('div');
      el.className = 'sc-month-dow';
      el.textContent = d;
      monthGridEl.appendChild(el);
    });
    const firstDow = new Date(y, m, 1).getDay();
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    for(let i = 0; i < firstDow; i++){
      const el = document.createElement('div');
      el.className = 'sc-month-cell empty';
      monthGridEl.appendChild(el);
    }
    for(let day = 1; day <= daysInMonth; day++){
      const key = y + '-' + String(m+1).padStart(2,'0') + '-' + String(day).padStart(2,'0');
      const cell = document.createElement('div');
      let cls = 'sc-month-cell';
      if(key === scTodayKey) cls += ' today';
      if(key === selectedDay) cls += ' selected';
      cell.className = cls;
      const anyLive = daysIndex[key] && daysIndex[key].some(function(g){ return g.status === 'in_progress'; });
      cell.innerHTML = day + (daysIndex[key] && daysIndex[key].length ? '<span class="dot" style="background:' + (anyLive ? 'var(--sc-live)' : 'var(--sc-muted)') + '"></span>' : '');
      cell.addEventListener('click', function(){ selectDay(key); });
      monthGridEl.appendChild(cell);
    }
  }

  function loadWeek(season, week){
    fetch('/api/scoreboard?season=' + season + '&week=' + week + '&seasontype=' + scSeasonType)
      .then(function(r){ return r.json(); })
      .then(function(data){
        scSeason = data.season; scWeek = data.week;
        weekLabelEl.textContent = 'Week ' + scWeek;
        indexGames(data.games || []);
        const keys = Object.keys(daysIndex).sort();
        if(keys.length) selectDay(keys.find(function(k){ return (data.games||[]).some(function(g){ return g.date_key === k; }); }) || keys[0]);
        renderDayTabs();
        renderMonth();
        // A different week needs a different board -- the baked-in one
        // only covers the week the page was rendered for.
        refreshPerformers();
      });
  }

  document.getElementById('scPrevWeek').addEventListener('click', function(){ loadWeek(scSeason, scWeek - 1); });
  document.getElementById('scNextWeek').addEventListener('click', function(){ loadWeek(scSeason, scWeek + 1); });
  document.getElementById('scMonthToggle').addEventListener('click', function(){
    monthEl.classList.toggle('open');
    document.getElementById('scMonthToggle').classList.toggle('active');
    if(monthEl.classList.contains('open')) renderMonth();
  });
  document.getElementById('scMonthPrev').addEventListener('click', function(){ monthCursor = new Date(monthCursor.getFullYear(), monthCursor.getMonth()-1, 1); renderMonth(); });
  document.getElementById('scMonthNext').addEventListener('click', function(){ monthCursor = new Date(monthCursor.getFullYear(), monthCursor.getMonth()+1, 1); renderMonth(); });

  // ---------------- daily performer board ----------------
  // The week's whole board is baked into the page; this filters it down
  // to the teams that actually played on the selected date. The split
  // has to happen here rather than on the server, because the browser is
  // the only place that knows the visitor's real local calendar day --
  // the same reason indexGames() derives date_key on the client.
  // 1st/2nd/3rd/4th -- the teens are the exception that a bare "th"
  // gets wrong in both directions (11th not 11st, but 21st not 21th).
  function ordinal(n){
    const rem100 = n % 100;
    if (rem100 >= 11 && rem100 <= 13) return n + 'th';
    return n + ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th');
  }

  const perfSubEl = document.getElementById('scPerfSub');
  const perfAllEl = document.getElementById('scPerf');
  const perfAllSection = document.getElementById('scGroupAll');
  // Ten across every position for the combined board, three per family
  // below it -- enough to see who led each spot without every section
  // turning into its own full board.
  const PERF_SHOWN = 10;
  const PERF_PER_GROUP = 3;
  const perfGroups = Array.prototype.slice.call(document.querySelectorAll('.sc-group[data-positions]'))
    .map(function(el){
      return {
        el: el,
        key: el.id.replace('scGroup-', ''),
        positions: (el.getAttribute('data-positions') || '').split(',').filter(Boolean),
        rowsEl: el.querySelector('[data-group-rows]'),
        moreEl: el.querySelector('[data-group-more]')
      };
    });
  let performers = SCORES_PERFORMERS || [];

  function perfRow(p, i){
    const stats = (p.stat_line || []).map(function(s){
      return '<span class="sc-perf-stat"><b>' + s[0] + '</b><span>' + s[1] + '</span></span>';
    }).join('');
    const grade = p.grade || {};
    const pct = grade.percentile == null ? '' : ordinal(grade.percentile) + ' pct';
    const sub = [p.position + ' · ' + p.team, p.vs_label, pct]
      .filter(Boolean).join(' · ');
    const href = '/performance?sid=' + encodeURIComponent(p.sid) +
                 '&season=' + scSeason + '&week=' + scWeek;
    return '<a class="sc-perf-row" href="' + href + '">' +
      '<span class="sc-perf-rank">' + (i + 1) + '</span>' +
      '<img src="' + (p.photo || '') + '" alt="" onerror="this.style.visibility=\\'hidden\\'">' +
      '<span class="sc-perf-main">' +
        '<span class="sc-perf-name">' + p.name +
          '<span class="pts">' + p.fpts + ' pts</span></span>' +
        '<span class="sc-perf-stats">' + stats + '</span>' +
        '<span class="sc-perf-sub">' + sub + '</span>' +
      '</span>' +
      '<span class="sc-perf-grade">' + SCORE_MARK +
        (grade.score == null ? '' : grade.score.toFixed(1)) + '</span>' +
    '</a>';
  }

  function renderPerformers(){
    const games = daysIndex[selectedDay] || [];
    const teams = new Set();
    games.forEach(function(g){
      if (g.away && g.away.abbr) teams.add(g.away.abbr);
      if (g.home && g.home.abbr) teams.add(g.home.abbr);
    });

    // Sorted here rather than trusting the order it arrived in. The
    // board already comes back best-first, but every section below
    // slices off the top of this list, so the ranking is worth owning
    // where it is used. By SCORE, not raw points -- that is what lets a
    // kicker's 19 and a receiver's 33 sit in one list at all.
    const played = performers.filter(function(p){
      return teams.has(p.team) && p.fpts > 0;
    }).sort(function(a, b){
      return ((b.grade || {}).score || 0) - ((a.grade || {}).score || 0);
    });
    const anyLive = games.some(function(g){ return g.status === 'in_progress'; });
    const anyPlayed = games.some(function(g){ return g.status !== 'scheduled'; });

    perfSubEl.textContent = played.length
      ? (anyLive ? 'Live · 5.0 = an average starter game at the position'
                 : '5.0 = an average starter game at the position')
      : (anyPlayed ? 'No scoring yet in these games.'
                   : 'Leaders appear here once these games kick off.');

    // The combined board. Ranked by SCORE rather than raw points, so a
    // kicker's 19 and a receiver's 33 are compared on what each is worth
    // at its own position instead of on a number they don't share.
    const top = played.slice(0, PERF_SHOWN);
    if (perfAllSection) perfAllSection.classList.add('on');
    if (perfAllEl) {
      perfAllEl.innerHTML = top.length
        ? top.map(perfRow).join('')
        : '<div class="sc-perf-empty">' +
          (anyPlayed ? 'No scoring yet in these games.'
                     : 'Player rankings appear here once these games kick off.') +
          '</div>';
    }
    const allMoreEl = document.getElementById('scPerfMore');
    if (allMoreEl) {
      allMoreEl.href = '/performances?season=' + scSeason + '&week=' + scWeek + '&scope=week';
    }

    perfGroups.forEach(function(g){
      const rows = played.filter(function(p){
        return g.positions.indexOf(p.position) !== -1;
      }).slice(0, PERF_PER_GROUP);
      // A section with nobody in it is hidden rather than shown empty:
      // on a short slate there may be no kicker at all, and a headed
      // box saying nothing reads as broken.
      g.el.classList.toggle('on', rows.length > 0);
      if (!rows.length) { g.rowsEl.innerHTML = ''; return; }
      if (g.moreEl) {
        // One position per family goes into the link; the page's own
        // dropdown covers the rest.
        g.moreEl.href = '/performances?season=' + scSeason + '&week=' + scWeek +
                        '&scope=week&position=' + encodeURIComponent(g.positions[0]);
      }
      g.rowsEl.innerHTML = rows.map(perfRow).join('');
    });
  }

  function refreshPerformers(){
    fetch('/api/performers?season=' + scSeason + '&week=' + scWeek)
      .then(function(r){ return r.json(); })
      .then(function(data){
        if (data && data.performers && data.performers.length) {
          performers = data.performers;
          renderPerformers();
        }
      })
      .catch(function(){ /* keep the board we already have */ });
  }

  // Only poll while something is actually being played. A finished or
  // not-yet-started slate can't change, and polling it would be pure
  // load for no new information.
  setInterval(function(){
    const games = daysIndex[selectedDay] || [];
    if (games.some(function(g){ return g.status === 'in_progress'; })) refreshPerformers();
  }, 45000);

  // Open on a day with football, decided here rather than at page build
  // so it uses the visitor's clock and the whole season's schedule.
  selectedDay = pickOpeningDay();
  monthCursor = new Date(selectedDay + "T00:00:00");
  syncWeekToDay(selectedDay);
  renderDayTabs();
  renderGames();
  renderPerformers();
  renderMonth();
  // A day outside the week baked into the page has no games loaded yet;
  // selectDay fetches that day and repaints when it lands.
  if (!daysIndex[selectedDay]) selectDay(selectedDay);
  // /scores only bakes the board in when it was already cached server-side
  // (it never blocks the render on a live stats fetch), so on a cold cache
  // the page arrives with nothing and fills itself in here.
  if (!performers.length) refreshPerformers();
})();
</script>
"""

STANDINGS_HTML = BASE_STYLE + make_header("scores") + """
<style>
  .st-page{ --st-bg:#0d0f0d; --st-surface:#151815; --st-line:rgba(255,255,255,0.08);
            --st-text:#e8e6df; --st-muted:#8b9089;
            background:var(--st-bg); color:var(--st-text); padding-bottom:60px;
            font-family:"Source Sans 3",system-ui,sans-serif; }
  .st-title{ font-family:"Big Shoulders Display"; font-size:26px; font-weight:800;
             text-transform:uppercase; margin:18px 0 2px; }
  .st-sub{ font-size:11.5px; color:var(--st-muted); margin-bottom:12px; }
  .st-tabs{ display:flex; gap:0; border-bottom:1px solid var(--st-line); margin-bottom:4px;
            overflow-x:auto; scrollbar-width:none; }
  .st-tabs::-webkit-scrollbar{ display:none; }
  .st-tab{ padding:9px 16px; font-size:14px; font-weight:800; text-decoration:none;
           color:var(--st-muted); border-bottom:2px solid transparent; white-space:nowrap; }
  .st-tab.on{ color:var(--accent-ink); border-bottom-color:var(--accent-ink); }
  .st-subtabs{ display:flex; gap:0; border-bottom:1px solid var(--st-line); }
  .st-subtabs .st-tab{ font-size:13px; padding:8px 14px; }
  .st-div{ margin-top:20px; }
  .st-div h3{ font-family:"Big Shoulders Display"; font-size:16px; font-weight:800;
              text-transform:uppercase; color:var(--st-muted); margin:0 0 6px; letter-spacing:0.04em; }
  .st-row{ display:flex; align-items:center; gap:10px; padding:10px 0;
           border-top:1px solid var(--st-line); text-decoration:none; color:var(--st-text); }
  .st-row:hover{ background:rgba(255,255,255,0.02); }
  .st-seed{ font-family:"IBM Plex Mono"; font-size:15px; color:var(--accent-ink);
            width:22px; flex:none; text-align:center; font-weight:700; }
  .st-row img{ width:34px; height:34px; object-fit:contain; flex:none; }
  .st-name{ flex:1; min-width:0; }
  .st-name b{ font-size:14px; display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .st-name span{ font-size:11px; color:var(--st-muted); }
  /* The mark sits on the team's own line, the way the reference does it:
     a goat for the bye, a crown for a division, a joker for a wild card. */
  .st-mark{ font-size:12px; margin-left:5px; }
  .st-traded{ font-size:11px; color:var(--st-muted); margin-left:5px; white-space:nowrap; }
  .st-stats{ display:flex; gap:12px; flex:none; }
  .st-stat{ text-align:center; min-width:38px; }
  .st-stat b{ display:block; font-family:"IBM Plex Mono"; font-size:14px; }
  .st-stat span{ font-size:9px; color:var(--st-muted); text-transform:uppercase; letter-spacing:0.03em; }
  /* Teams that missed the field sit below a rule, so the cut line is a
     thing you can see rather than a seed number you have to count to. */
  .st-cut{ display:flex; align-items:center; gap:10px; margin-top:14px; padding-top:10px;
           border-top:1px dashed var(--st-line); font-size:10.5px; text-transform:uppercase;
           letter-spacing:0.08em; color:var(--st-muted); font-weight:700; }
  .st-legend{ margin-top:22px; padding-top:14px; border-top:1px solid var(--st-line); }
  .st-legend div{ display:flex; align-items:center; gap:9px; font-size:12.5px;
                  color:var(--st-muted); padding:4px 0; }
  .st-empty{ color:var(--st-muted); padding:30px; text-align:center; font-size:13px; }
  @media (max-width:640px){
    .st-stats{ gap:7px; } .st-stat{ min-width:30px; } .st-stat b{ font-size:12.5px; }
    .st-row img{ width:27px; height:27px; }
  }
</style>
<div class="st-page"><div class="wrap">
  {% if load_error %}<div class="error">Couldn't load standings: {{ load_error }}</div>{% endif %}
  <div class="st-title">Standings</div>
  <div class="st-sub">
    {{ season }} season &middot; computed from completed games. Ordered by win
    percentage then point differential &mdash; not the NFL's full tiebreaker ladder.
  </div>

  {% macro stat(value, label) -%}
  <span class="st-stat"><b>{{ value }}</b><span>{{ label }}</span></span>
  {%- endmacro %}

  {% macro mark(r) -%}
  {%- if r.marker %}<span class="st-mark" title="{{ r.marker_label }}">{{ r.marker }}</span>{% endif -%}
  {%- endmacro %}

  <div class="st-tabs">
    {% for c in conferences %}
    <a class="st-tab {{ 'on' if view == 'conference' and c == conf }}"
       href="/standings?season={{ season }}&amp;view={{ c }}">{{ c }}</a>
    {% endfor %}
    <a class="st-tab {{ 'on' if view == 'playoffs' }}"
       href="/standings?season={{ season }}&amp;view=playoffs&amp;conf={{ conf }}">Playoffs</a>
    <a class="st-tab {{ 'on' if view == 'draft' }}"
       href="/standings?season={{ season }}&amp;view=draft">Draft Order</a>
  </div>

  {% if not played %}
    <div class="st-empty">No completed games yet this season.</div>

  {% elif view == 'conference' %}
  {% for d in divisions %}
  <div class="st-div">
    <h3>{{ conf }} {{ d }}</h3>
    {% for r in by_div[d] %}
    <a class="st-row" href="/team?abbr={{ r.team }}&amp;season={{ season }}">
      <span class="st-seed">{{ loop.index }}</span>
      <img src="https://a.espncdn.com/i/teamlogos/nfl/500/{{ r.team|lower }}.png" alt=""
           onerror="this.style.visibility='hidden'">
      <span class="st-name">
        <b>{{ r.team }}{{ mark(r) }}</b>
        <span>{{ r.conf_rank|ordinal }} {{ conf }}</span>
      </span>
      <span class="st-stats">
        {{ stat(r.record, 'W-L') }}{{ stat('%.3f'|format(r.pct), 'PCT') }}
        {{ stat(r.last5, 'L5') }}{{ stat(r.ppg, 'PPG') }}{{ stat(r.div_record, 'DIV') }}
      </span>
    </a>
    {% endfor %}
  </div>
  {% endfor %}

  {% elif view == 'playoffs' %}
  <div class="st-subtabs">
    {% for c in conferences %}
    <a class="st-tab {{ 'on' if c == conf }}"
       href="/standings?season={{ season }}&amp;view=playoffs&amp;conf={{ c }}">{{ c }}</a>
    {% endfor %}
  </div>
  {% for r in field %}
  {% if r.seed == 8 %}<div class="st-cut"><span>Out of the field</span></div>{% endif %}
  <a class="st-row" href="/team?abbr={{ r.team }}&amp;season={{ season }}">
    <span class="st-seed">{{ r.seed }}</span>
    <img src="https://a.espncdn.com/i/teamlogos/nfl/500/{{ r.team|lower }}.png" alt=""
         onerror="this.style.visibility='hidden'">
    <span class="st-name">
      <b>{{ r.team }}{{ mark(r) }}</b>
      <span>{{ r.conf_rank|ordinal }} {{ conf }}</span>
    </span>
    <span class="st-stats">
      {{ stat(r.record, 'W-L') }}{{ stat('%.3f'|format(r.pct), 'PCT') }}
      {{ stat(r.last5, 'L5') }}{{ stat(r.ppg, 'PPG') }}{{ stat(r.div_record, 'DIV') }}
    </span>
  </a>
  {% endfor %}

  {% else %}
  {% for r in draft %}
  <a class="st-row" href="/team?abbr={{ r.team }}&amp;season={{ season }}">
    <span class="st-seed">{{ r.pick }}</span>
    <img src="https://a.espncdn.com/i/teamlogos/nfl/500/{{ r.team|lower }}.png" alt=""
         onerror="this.style.visibility='hidden'">
    <span class="st-name">
      <b>{{ r.team }}{% if r.traded_to %}<span class="st-traded">{{ traded_emoji }} {{ r.traded_to }}</span>{% endif %}</b>
      <span>{{ r.conference }} {{ r.division }}</span>
    </span>
    <span class="st-stats">
      {{ stat('&mdash;'|safe, 'GB') }}{{ stat('%.3f'|format(r.sos), 'SOS') }}
      {{ stat(r.record, 'W-L') }}{{ stat('%.3f'|format(r.pct), 'PCT') }}
      {{ stat(r.last5, 'L5') }}{{ stat(r.ppg, 'PPG') }}
    </span>
  </a>
  {% endfor %}
  <div class="st-sub" style="margin-top:16px;">
    Worst record picks first, ties broken by the easier schedule. The last
    fourteen picks reorder once the postseason is played.
  </div>
  {% endif %}

  {% if played and view != 'draft' %}
  <div class="st-legend">
    {% for key in ['bye', 'division', 'wildcard'] %}
    <div><span class="st-mark">{{ markers[key].emoji }}</span> {{ markers[key].label }}</div>
    {% endfor %}
  </div>
  {% elif played %}
  <div class="st-legend">
    <div><span class="st-mark">{{ traded_emoji }}</span> Pick traded to another team</div>
  </div>
  {% endif %}
</div></div>
"""


TEAM_HTML = BASE_STYLE + make_header("scores") + """
<style>
  .tm-page{ --tm-bg:#0d0f0d; --tm-surface:#151815; --tm-surface2:#1c201c;
            --tm-line:rgba(255,255,255,0.08); --tm-text:#e8e6df; --tm-muted:#8b9089;
            background:var(--tm-bg); color:var(--tm-text); padding-bottom:60px;
            font-family:"Source Sans 3",system-ui,sans-serif; }
  .tm-head{ display:flex; align-items:center; gap:16px; padding:20px 0 14px; }
  .tm-head img{ width:72px; height:72px; object-fit:contain; flex:none; }
  .tm-name{ font-family:"Big Shoulders Display"; font-size:30px; font-weight:800;
            text-transform:uppercase; line-height:1.05; }
  .tm-meta{ font-size:13px; color:var(--tm-muted); margin-top:3px; }
  .tm-ranks{ display:flex; background:var(--tm-surface); border:1px solid var(--tm-line);
             border-radius:12px; overflow:hidden; }
  .tm-rank{ flex:1; text-align:center; padding:12px 6px; border-left:1px solid var(--tm-line);
            color:inherit; text-decoration:none; display:block; }
  a.tm-rank{ cursor:pointer; }
  a.tm-rank:hover{ background:var(--tm-surface2); }
  .tm-rank:first-child{ border-left:none; }
  .tm-rank b{ display:block; font-family:"IBM Plex Mono"; font-size:24px; font-weight:700; }
  .tm-rank b sup{ font-size:12px; }
  .tm-rank span{ font-size:10px; color:var(--accent-ink); text-transform:uppercase;
                 letter-spacing:0.05em; font-weight:700; }
  .tm-rank-note{ font-size:11.5px; color:var(--tm-muted); margin-top:7px; }
  .tm-tabs{ display:flex; border-bottom:1px solid var(--tm-line); margin-top:18px; }
  .tm-tab{ flex:1; padding:11px 8px; text-align:center; font-size:14px; font-weight:800;
           cursor:pointer; color:var(--tm-muted); background:none; border:none;
           border-bottom:2px solid transparent; }
  .tm-tab.on{ color:var(--accent-ink); border-bottom-color:var(--accent-ink); }
  .tm-panel{ display:none; padding-top:14px; } .tm-panel.on{ display:block; }
  .tm-sub{ display:flex; gap:16px; justify-content:center; margin-bottom:10px; }
  .tm-sub button{ background:none; border:none; cursor:pointer; font-size:14px;
                  font-weight:800; color:var(--tm-muted); }
  .tm-sub button.on{ color:var(--accent-ink); }

  .tm-eyebrow{ font-size:11px; text-transform:uppercase; letter-spacing:0.06em;
               color:var(--tm-muted); margin:6px 0 8px; font-weight:700; }
  /* Recent point differentials. Bars grow up for a win and down for a
     loss from a shared centre line, so form reads at a glance. */
  .tm-diffs{ display:flex; align-items:center; gap:5px; height:120px;
             border-bottom:1px solid var(--tm-line); padding-bottom:4px; }
  .tm-diff{ flex:1; display:flex; flex-direction:column; align-items:center;
            justify-content:center; height:100%; min-width:0; }
  .tm-diff-val{ font-family:"IBM Plex Mono"; font-size:10.5px; color:var(--tm-muted); }
  .tm-diff-bar{ width:100%; border-radius:3px; }
  .tm-diff.win .tm-diff-bar{ background:var(--good); }
  .tm-diff.loss .tm-diff-bar{ background:var(--critical); }
  .tm-diff.tie .tm-diff-bar{ background:var(--tm-muted); }
  .tm-diff-opp{ font-size:9.5px; color:var(--tm-muted); margin-top:3px; white-space:nowrap; }

  .tm-game{ display:flex; align-items:center; gap:10px; padding:11px 0;
            border-top:1px solid var(--tm-line); text-decoration:none; color:var(--tm-text); }
  .tm-game-res{ font-family:"Big Shoulders Display"; font-size:20px; font-weight:800;
                width:22px; flex:none; text-align:center; }
  .tm-game-res.W{ color:var(--good); } .tm-game-res.L{ color:var(--critical); }
  .tm-game.upcoming{ opacity:0.7; }
  .tm-game img{ width:28px; height:28px; object-fit:contain; flex:none; }
  .tm-game-main{ flex:1; min-width:0; font-size:13.5px; }
  .tm-game-main span{ display:block; font-size:11px; color:var(--tm-muted); }
  .tm-game-score{ font-family:"IBM Plex Mono"; font-size:16px; font-weight:700; flex:none; }

  .tm-player{ display:flex; align-items:center; gap:11px; padding:11px 0;
              border-top:1px solid var(--tm-line); text-decoration:none; color:var(--tm-text); }
  .tm-player img{ width:46px; height:46px; border-radius:50%; object-fit:cover;
                  background:var(--tm-surface2); flex:none; }
  .tm-player-main{ flex:1; min-width:0; }
  .tm-player-name{ font-size:15px; font-weight:700; }
  .tm-player-ranks{ display:flex; gap:14px; margin-top:4px; }
  .tm-pr b{ display:block; font-family:"IBM Plex Mono"; font-size:13px; }
  .tm-pr span{ font-size:9px; color:var(--tm-muted); text-transform:uppercase; letter-spacing:0.03em; }
  .tm-player-pos{ font-size:12px; color:var(--tm-muted); flex:none; text-align:right; }
  .tm-player.bench{ opacity:0.72; }
  .tm-divider{ margin-top:16px; padding:8px 0 4px; border-top:1px solid var(--line-strong);
               font-size:10.5px; letter-spacing:0.09em; text-transform:uppercase; color:var(--tm-muted); }
  .tm-divider:first-child{ margin-top:4px; border-top:none; }
  .tm-empty{ color:var(--tm-muted); padding:28px; text-align:center; font-size:13px; }
  @media (max-width:640px){
    .tm-name{ font-size:23px; } .tm-head img{ width:56px; height:56px; }
    .tm-rank b{ font-size:20px; } .tm-player-ranks{ gap:10px; }
  }
</style>
<div class="tm-page"><div class="wrap">
  {% if load_error %}<div class="error">Couldn't load this team: {{ load_error }}</div>{% endif %}
  {% if not team %}
    <div class="tm-empty">
      Pick a team:
      <div style="margin-top:10px; display:flex; flex-wrap:wrap; gap:6px; justify-content:center;">
        {% for t in all_teams %}<a href="/team?abbr={{ t }}" style="color:var(--accent-ink); font-weight:700;">{{ t }}</a>{% endfor %}
      </div>
    </div>
  {% else %}
  <div class="tm-head">
    <img src="{{ team.logo }}" alt="" onerror="this.style.visibility='hidden'">
    <div>
      <div class="tm-name">{{ team.name }}</div>
      <div class="tm-meta">
        {{ team.record }}
        {%- if team.div_rank %} &middot; {{ team.div_rank|ordinal }} {{ team.conference }} {{ team.division }}{% endif -%}
        {%- if team.bye_week %} &middot; Bye {{ team.bye_week }}{% endif -%}
      </div>
    </div>
  </div>

  <!-- Power rank over three windows. A window a team hasn't played in
       inherits from the next window out; a season not yet played falls
       back to the last one that was, which the note below says plainly
       rather than passing off as current form. -->
  {% set standings_href = '/standings?season=' ~ season ~ ('&conf=' ~ team.conference if team.conference else '') %}
  <div class="tm-ranks">
    {% for key, label in [('d7', '7-day'), ('d30', '30-day'), ('season', 'Season')] %}
    <a class="tm-rank" href="{{ standings_href }}" title="See where every team ranks">
      {% set v = team.rank.get(key) %}
      <b>{% if v %}{{ v }}<sup>{{ (v|ordinal)[-2:] }}</sup>{% else %}&ndash;{% endif %}</b>
      <span>{{ label }}</span>
    </a>
    {% endfor %}
  </div>
  {% if team.rank and team.rank_season != season %}
  <div class="tm-rank-note">Ranked on {{ team.rank_season }} results until this season&rsquo;s games are final.</div>
  {% elif not team.rank %}
  <div class="tm-rank-note">Power ranking appears once games have been played.</div>
  {% endif %}

  <div class="tm-tabs" id="tmTabs">
    <button class="tm-tab on" data-panel="feed">Feed</button>
    <button class="tm-tab" data-panel="games">Games</button>
    <button class="tm-tab" data-panel="players">Players</button>
  </div>

  <div class="tm-panel on" data-panel="feed">
    {% if team.games %}
    <div class="tm-eyebrow">Recent differentials</div>
    {% set peak = team.games|map(attribute='diff')|map('abs')|max %}
    <div class="tm-diffs">
      {# The games list now runs week 1 -> latest, so the last eight of it
         are the most recent eight, already left-to-right in time order. #}
      {% for g in team.games[-8:] %}
      <div class="tm-diff {{ 'win' if g.result == 'W' else ('loss' if g.result == 'L' else 'tie') }}">
        <span class="tm-diff-val">{{ '%+d'|format(g.diff) }}</span>
        <div class="tm-diff-bar" style="height:{{ ((g.diff|abs) / peak * 70)|round|int if peak else 2 }}%;"></div>
        <span class="tm-diff-opp">{{ '' if g.home else '@' }}{{ g.opponent }}</span>
      </div>
      {% endfor %}
    </div>
    {% endif %}
    {% if team.standing %}
    <div class="tm-eyebrow" style="margin-top:18px;">Season</div>
    <div class="tm-ranks">
      <a class="tm-rank" href="{{ standings_href }}"><b>{{ team.standing.ppg }}</b><span>PPG</span></a>
      <a class="tm-rank" href="{{ standings_href }}"><b>{{ team.standing.papg }}</b><span>Allowed</span></a>
      <a class="tm-rank" href="{{ standings_href }}"><b>{{ '%+d'|format(team.standing.diff) }}</b><span>Diff</span></a>
      <a class="tm-rank" href="{{ standings_href }}"><b>{{ team.standing.streak }}</b><span>Streak</span></a>
    </div>
    {% endif %}
  </div>

  <!-- The whole season in one list, week 1 to the last. A game that has
       been played carries its result and score; one that has not says so
       rather than rendering as a 0-0 tie. -->
  <div class="tm-panel" data-panel="games">
    {% for g in team.schedule %}
    <a class="tm-game{% if not g.result %} upcoming{% endif %}" href="/game?id={{ g.id }}">
      <span class="tm-game-res {{ g.result or '' }}">{{ g.result or '&middot;'|safe }}</span>
      <img src="https://a.espncdn.com/i/teamlogos/nfl/500/{{ g.opponent|lower }}.png" alt=""
           onerror="this.style.visibility='hidden'">
      <span class="tm-game-main">{{ 'vs' if g.home else '@' }} {{ g.opponent }}
        <span>Week {{ g.week }}{% if not g.result %} &middot; {{ 'live' if g.status == 'in_progress' else 'upcoming' }}{% endif %}</span></span>
      <span class="tm-game-score">
        {%- if g.result %}{{ g.score }}&ndash;{{ g.opp_score }}{% endif -%}
      </span>
    </a>
    {% else %}
    <div class="tm-empty">No games on the schedule yet.</div>
    {% endfor %}
  </div>

  <div class="tm-panel" data-panel="players">
    <div class="tm-sub" id="tmSub">
      <button class="on" data-sub="offense">Offense</button>
      <button data-sub="defense">Defense</button>
    </div>
    {% for group in ['offense', 'defense'] %}
    <div data-sub-panel="{{ group }}" style="{{ '' if group == 'offense' else 'display:none;' }}">
      {% for p in team.roster[group] %}
      {% if p.bench and not loop.first and not team.roster[group][loop.index0 - 1].bench %}
      <div class="tm-divider">Bench &middot; yet to play this season</div>
      {% elif not p.bench and (loop.first or team.roster[group][loop.index0 - 1].position != p.position) %}
      <div class="tm-divider">{{ p.position }}</div>
      {% endif %}
      <a class="tm-player{% if p.bench %} bench{% endif %}" href="/player?sid={{ p.sid }}">
        <img src="{{ p.photo }}" alt="" onerror="this.style.visibility='hidden'">
        <span class="tm-player-main">
          <span class="tm-player-name">{{ p.name }}</span>
          <span class="tm-player-ranks">
            {% for key, label in [('d7','7-day'), ('d30','30-day'), ('season','Season'), ('alltime','All-time')] %}
            <span class="tm-pr"><b>{% if p.ranks.get(key) %}{{ '{:,}'.format(p.ranks[key]) }}{% else %}&ndash;{% endif %}</b><span>{{ label }}</span></span>
            {% endfor %}
          </span>
        </span>
        <span class="tm-player-pos">{{ p.position }}{% if p.number %}<br>#{{ p.number }}{% endif %}</span>
      </a>
      {% else %}
      <div class="tm-empty">No {{ group }} players found for this team.</div>
      {% endfor %}
    </div>
    {% endfor %}
  </div>
  {% endif %}
</div></div>
<script>
(function(){
  const tabs = document.getElementById('tmTabs');
  if (tabs) tabs.addEventListener('click', function(e){
    const btn = e.target.closest('.tm-tab');
    if (!btn) return;
    tabs.querySelectorAll('.tm-tab').forEach(function(b){ b.classList.toggle('on', b === btn); });
    document.querySelectorAll('.tm-panel').forEach(function(p){
      p.classList.toggle('on', p.dataset.panel === btn.dataset.panel);
    });
  });
  const sub = document.getElementById('tmSub');
  if (sub) sub.addEventListener('click', function(e){
    const btn = e.target.closest('button');
    if (!btn) return;
    sub.querySelectorAll('button').forEach(function(b){ b.classList.toggle('on', b === btn); });
    document.querySelectorAll('[data-sub-panel]').forEach(function(p){
      p.style.display = p.dataset.subPanel === btn.dataset.sub ? '' : 'none';
    });
  });
})();
</script>
"""


PERFORMANCES_HTML = BASE_STYLE + make_header("scores") + """
<style>
  .pl-page{
    --pl-bg:#0d0f0d; --pl-surface:#151815; --pl-surface2:#1c201c;
    --pl-line:rgba(255,255,255,0.08); --pl-text:#e8e6df; --pl-muted:#8b9089;
    background:var(--pl-bg); color:var(--pl-text); padding-bottom:60px;
    font-family:"Source Sans 3",system-ui,sans-serif;
  }
  .pl-title{ font-family:"Big Shoulders Display"; font-size:26px; font-weight:800;
             text-transform:uppercase; margin:18px 0 4px; }
  .pl-sub{ font-size:12px; color:var(--pl-muted); margin-bottom:14px; }

  /* Filters. Three independent axes -- who, over what stretch, and which
     end of the board -- as dropdowns, so none has to truncate on a phone. */
  .pl-filters{ display:flex; flex-wrap:wrap; gap:8px; position:sticky; top:64px; z-index:30;
               background:color-mix(in srgb, var(--pl-bg) 94%, transparent); backdrop-filter:blur(8px);
               padding:10px 0; border-bottom:1px solid var(--pl-line); }
  .pl-select{ background-color:var(--pl-surface); border:1px solid var(--pl-line);
              color:var(--pl-text); border-radius:8px; padding:9px 12px; font-size:13px;
              font-weight:700; font-family:inherit;
              appearance:none; -webkit-appearance:none;
             background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 8'%3E%3Cpath d='M1 1.5 6 6.5 11 1.5' stroke='%238b9089' stroke-width='1.8' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
             background-repeat:no-repeat; background-position:right 11px center;
             background-size:11px 7px; padding-right:30px; cursor:pointer; }

  .pl-list{ border:1px solid var(--pl-line); border-radius:12px; overflow:hidden;
            background:var(--pl-surface); margin-top:14px; }
  .pl-row-item{ display:flex; align-items:center; gap:10px; padding:10px 12px;
                border-top:1px solid var(--pl-line); text-decoration:none; color:var(--pl-text); }
  .pl-row-item:first-child{ border-top:none; }
  .pl-row-item:hover{ background:var(--pl-surface2); }
  .pl-rank{ font-family:"IBM Plex Mono"; font-size:12px; color:var(--pl-muted);
            width:30px; flex:none; text-align:right; font-variant-numeric:tabular-nums; }
  .pl-row-item img{ width:38px; height:38px; border-radius:50%; object-fit:cover;
                    background:var(--pl-surface2); flex:none; }
  .pl-main{ flex:1; min-width:0; display:flex; flex-direction:column; gap:2px; }
  .pl-name{ font-weight:700; font-size:13.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .pl-name .pts{ color:var(--pl-muted); font-weight:600; font-size:12px; margin-left:6px; }
  .pl-meta{ font-size:11px; color:var(--pl-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .pl-score{ font-family:"IBM Plex Mono"; font-size:20px; font-weight:700; flex:none;
             min-width:46px; text-align:right; font-variant-numeric:tabular-nums; }
  /* Same reasoning as the scores board: the ranking already orders
     these, so the number stays plain and the mark carries the styling. */
  .pl-score{ color:var(--pl-text); display:inline-flex; align-items:center; gap:5px; justify-content:flex-end; }
  .score-mark{ width:11px; height:13px; flex:none; opacity:0.55; }
  .pl-empty{ color:var(--pl-muted); padding:36px; text-align:center; font-size:13px; }
  @media (max-width:640px){
    .pl-title{ font-size:21px; }
    .pl-score{ font-size:17px; min-width:40px; }
  }
</style>

<div class="pl-page">
<div class="wrap">
  {% if load_error %}<div class="error">Couldn't load performances: {{ load_error }}</div>{% endif %}

  <div class="pl-title">Performances</div>
  <div class="pl-sub">
    5.0 is an average starter game at that position. The scale has no ceiling &mdash;
    a historic game scores above 10.
  </div>

  <!-- Three dropdowns rather than fourteen pills across two rows. A
       list this long is what a select is for: it shows the choice you
       made, not every choice you did not. -->
  <div class="pl-filters">
    <select class="pl-select" id="plPosition" aria-label="Position">
      <option value="">All positions</option>
      {% for pos in positions %}
      <option value="{{ pos }}" {{ 'selected' if position == pos }}>{{ pos }}</option>
      {% endfor %}
    </select>
    <select class="pl-select" id="plScope" aria-label="Range">
      {% for key, label in [('day','Today'),('week','This week'),('month','This month'),
                            ('season','This season'),('alltime','All time')] %}
      <option value="{{ key }}" {{ 'selected' if scope == key }}>{{ label }}</option>
      {% endfor %}
    </select>
    <select class="pl-select" id="plOrder" aria-label="Order">
      <option value="top" {{ 'selected' if order != 'lowest' }}>Highest first</option>
      <option value="lowest" {{ 'selected' if order == 'lowest' }}>Lowest first</option>
    </select>
  </div>
  <script>
  (function(){
    const base = {{ ('/performances?season=' ~ season ~ '&week=' ~ week)|tojson }};
    function go(){
      const pos = document.getElementById('plPosition').value;
      const url = base +
        '&scope=' + encodeURIComponent(document.getElementById('plScope').value) +
        '&order=' + encodeURIComponent(document.getElementById('plOrder').value) +
        (pos ? '&position=' + encodeURIComponent(pos) : '');
      window.location.href = url;
    }
    ['plPosition', 'plScope', 'plOrder'].forEach(function(id){
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', go);
    });
  })();
  </script>

  {% if not rows %}
    <div class="pl-empty">
      No performances in this range yet.
      {% if scope in ('month', 'season', 'alltime') %}
        <div style="margin-top:6px;">Historical ranges need the season backfill to have run.</div>
      {% endif %}
    </div>
  {% else %}
  <div class="pl-list">
    {% for r in rows %}
    <a class="pl-row-item"
       href="/performance?sid={{ r.sid }}&amp;season={{ r.season or season }}&amp;week={{ r.week or week }}">
      <span class="pl-rank">{{ loop.index }}</span>
      <img src="{{ r.photo }}" alt="" onerror="this.style.visibility='hidden'">
      <span class="pl-main">
        <span class="pl-name">{{ r.name }}<span class="pts">{{ r.fpts }} pts</span></span>
        <span class="pl-meta">
          {{ r.position }} &middot; {{ r.team }}
          {%- if r.vs_label %} &middot; {{ r.vs_label }}{% endif %}
          {%- if scope != 'day' and scope != 'week' %} &middot; {{ r.season }} wk {{ r.week }}{% endif %}
          &middot; {{ r.grade.percentile }}% of {{ r.position }} games
        </span>
      </span>
      <span class="pl-score">{{ score_mark|safe }}{{ '%.1f'|format(r.grade.score) }}</span>
    </a>
    {% endfor %}
  </div>
  {% endif %}
</div>
</div>
"""


PERFORMANCE_HTML = BASE_STYLE + make_header("scores") + """
<style>
  .pf-page{
    --pf-bg:#0d0f0d; --pf-surface:#151815; --pf-surface2:#1c201c;
    --pf-line:rgba(255,255,255,0.08); --pf-text:#e8e6df; --pf-muted:#8b9089;
    /* Scoring plays only. Kept off the site's own accent on purpose:
       orange is the colour of every link and control here, and a play
       list where every row is orange says nothing about which rows put
       points on the board. */
    --pf-scored:#4c9dff;
    background:var(--pf-bg); color:var(--pf-text); padding-bottom:60px;
    font-family:"Source Sans 3",system-ui,sans-serif;
  }
  /* Peer strip: the rest of the week's board, so you can move between
     performances without backing out to /scores first. */
  .pf-peers{ display:flex; gap:14px; overflow-x:auto; padding:12px 0 6px; scrollbar-width:none;
             border-bottom:1px solid var(--pf-line); }
  .pf-peers::-webkit-scrollbar{ display:none; }
  .pf-peer{ flex:none; width:56px; text-align:center; text-decoration:none; color:var(--pf-muted); }
  .pf-peer img{ width:46px; height:46px; border-radius:50%; object-fit:cover; background:var(--pf-surface2); }
  .pf-peer .sc{ display:block; font-family:"IBM Plex Mono"; font-size:12px; font-weight:700; margin-top:3px; }
  .pf-peer .nm{ display:block; font-size:9.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }

  .pf-head{ display:flex; align-items:center; gap:16px; padding:18px 0 14px; }
  .pf-head img{ width:74px; height:74px; border-radius:50%; object-fit:cover; background:var(--pf-surface2); flex:none; }
  .pf-head-main{ flex:1; min-width:0; }
  .pf-name{ font-family:"Big Shoulders Display"; font-size:30px; font-weight:800; text-transform:uppercase; line-height:1.05; }
  .pf-sub{ font-size:12.5px; color:var(--pf-muted); margin-top:2px; }
  .pf-headline{ display:flex; gap:16px; flex-wrap:wrap; margin-top:8px; }
  .pf-headline b{ font-family:"IBM Plex Mono"; font-size:26px; font-weight:700; }
  .pf-headline span{ font-size:11px; color:var(--pf-muted); margin-left:3px; }
  .pf-scores{ display:flex; gap:14px; flex:none; }
  .pf-score{ flex:none; text-align:center; }
  .pf-score .n{ font-family:"IBM Plex Mono"; font-size:42px; font-weight:700; line-height:1;
                font-variant-numeric:tabular-nums; display:flex; align-items:center;
                justify-content:center; gap:7px; }
  /* Sized against the 42px numeral it sits beside, not the 13px one on a
     board row, so the mark reads as part of the score rather than a speck. */
  .pf-score .n .score-mark{ width:22px; height:26px; opacity:0.5; }
  .pf-score .l{ font-size:10px; color:var(--pf-muted); text-transform:uppercase; letter-spacing:0.06em; }
  .pf-score.elite .n{ color:var(--good); }
  .pf-score.good .n{ color:var(--good); opacity:0.88; }
  .pf-score.mid .n{ color:var(--warning); }
  .pf-score.poor .n{ color:var(--critical); }
  .pf-score.historic .n{ color:var(--accent-ink); }

  /* Rating breakdown: one column per quarter, the rating printed above
     its bar. Columns rather than rows because the question being asked
     is "when did this game happen", and a timeline reads left to right. */
  /* Impact: the plays that moved the result most, with the swing each
     one produced. A list rather than a bar chart -- what matters is
     WHICH plays turned the game, and a name reads faster than a bar. */
  .pf-swings{ display:flex; flex-direction:column; }
  .pf-swing{ display:flex; align-items:baseline; gap:10px; padding:7px 0;
             border-top:1px solid var(--pf-line); }
  .pf-swing:first-child{ border-top:none; }
  .pf-swing-when{ flex:none; width:64px; font-size:11px; color:var(--pf-muted);
                  font-family:"IBM Plex Mono"; }
  .pf-swing-what{ flex:1; min-width:0; font-size:13px; }
  .pf-swing-val{ flex:none; font-family:"IBM Plex Mono"; font-size:13px; font-weight:700;
                 color:var(--pf-muted); font-variant-numeric:tabular-nums; }
  .pf-swing-val.pos{ color:var(--good); }
  .pf-swing-val.neg{ color:var(--critical); }

  .pf-qtrs{ display:flex; align-items:flex-end; gap:12px; height:170px; margin-top:6px; }
  .pf-qtr{ flex:1; display:flex; flex-direction:column; align-items:center;
           justify-content:flex-end; height:100%; min-width:0; }
  .pf-qtr-val{ font-size:15px; font-weight:700; font-family:"IBM Plex Mono";
               font-variant-numeric:tabular-nums; margin-bottom:6px; white-space:nowrap; }
  .pf-qtr-col{ width:100%; border-radius:6px 6px 0 0; background:var(--accent);
               min-height:3px; transition:height 0.2s ease; }
  .pf-qtr-col.zero{ background:var(--pf-surface2); }
  .pf-qtr-lab{ font-size:12.5px; color:var(--pf-muted); margin-top:7px; }
  .pf-note{ font-size:11.5px; color:var(--pf-muted); margin-top:14px; line-height:1.5; }

  /* Play rows, the way the reference writes them: who, when, at what
     score, what happened, and what it was worth. */
  .pf-feed{ display:flex; flex-direction:column; }
  .pf-feed-row{ display:flex; gap:11px; align-items:flex-start; padding:12px 0;
                border-top:1px solid var(--pf-line); text-decoration:none; color:inherit; }
  .pf-feed-row:first-child{ border-top:none; }
  .pf-feed-row img.mug{ width:44px; height:44px; border-radius:50%; object-fit:cover;
                        background:var(--pf-surface2); flex:none; }
  .pf-feed-main{ flex:1; min-width:0; }
  .pf-feed-sit{ display:flex; align-items:center; gap:5px; flex-wrap:wrap;
                font-size:11.5px; color:var(--pf-muted); }
  .pf-feed-sit img{ width:15px; height:15px; object-fit:contain; vertical-align:-2px; }
  .pf-feed-sit b{ color:var(--pf-text); font-family:"IBM Plex Mono"; font-weight:700; }
  /* Plain white for an ordinary play, blue for one that scored -- so
     the rows that mattered are visible without reading a word. */
  .pf-feed-title{ font-size:15.5px; font-weight:700; color:var(--pf-text); margin-top:3px; }
  .pf-feed-title.scored{ color:var(--pf-scored); }
  .pf-feed-rate{ flex:none; display:flex; align-items:center; gap:5px;
                 font-family:"IBM Plex Mono"; font-size:15px; font-weight:700;
                 font-variant-numeric:tabular-nums; }
  .pf-feed-rate .score-mark{ width:11px; height:13px; opacity:0.5; }
  .pf-feed-more{ display:block; text-align:center; padding:11px 0 2px; font-size:12.5px;
                 font-weight:700; color:var(--accent-ink); cursor:pointer;
                 border-top:1px solid var(--pf-line); }
  .pf-feed-row.extra{ display:none; }
  .pf-feed.all .pf-feed-row.extra{ display:flex; }
  .pf-feed.all .pf-feed-more{ display:none; }
  .pf-panel-head{ display:flex; align-items:baseline; justify-content:space-between; gap:10px; }

  .pf-panel{ background:var(--pf-surface); border:1px solid var(--pf-line); border-radius:12px; padding:14px 16px; margin-top:16px; }
  .pf-panel h3{ font-family:"Big Shoulders Display"; font-size:17px; font-weight:800; text-transform:uppercase;
                margin:0 0 10px; color:var(--pf-text); }
  .pf-grid{ display:grid; grid-template-columns:repeat(auto-fit, minmax(64px, 1fr)); gap:12px 8px; }
  .pf-cell{ text-align:left; }
  .pf-cell b{ display:block; font-family:"IBM Plex Mono"; font-size:21px; font-weight:700; line-height:1.1; }
  .pf-cell span{ font-size:10px; color:var(--pf-muted); letter-spacing:0.04em; }

  /* How the score was arrived at. The point of showing this is that the
     number stops being something you have to take on faith. */
  .pf-lede{ font-size:14px; margin:0 0 14px; color:var(--pf-muted); }
  .pf-lede b{ color:var(--pf-text); font-size:15px; }
  .pf-bars{ display:flex; flex-direction:column; gap:9px; }
  .pf-bar-row{ display:flex; align-items:center; gap:10px; font-size:12px; color:var(--pf-muted); }
  .pf-bar-label{ flex:none; width:106px; text-align:right; }
  .pf-bar-track{ flex:1; height:14px; border-radius:4px; background:var(--pf-surface2); overflow:hidden; }
  .pf-bar-fill{ display:block; height:100%; border-radius:4px; background:var(--pf-muted); opacity:0.55; }
  .pf-bar-val{ flex:none; width:42px; font-family:"IBM Plex Mono"; font-size:12.5px; text-align:right; }
  /* The performance being viewed is the one that should read first. */
  .pf-bar-row.is-this{ color:var(--pf-text); font-weight:700; }
  .pf-bar-row.is-this .pf-bar-fill{ background:var(--accent-ink); opacity:1; }
  .pf-bar-row.is-this .pf-bar-val{ font-weight:700; }
  .pf-foot{ font-size:12px; color:var(--pf-muted); margin:14px 0 0; line-height:1.5; }
  .pf-foot b{ color:var(--pf-text); }
  @media (max-width:640px){ .pf-bar-label{ width:84px; font-size:11px; } }

  /* Season game log -- this performance against the player's own body of
     work, which is the context the league-wide score can't give. */
  .pf-log{ display:flex; align-items:flex-end; gap:4px; height:130px; margin-top:6px; }
  .pf-log-bar{ flex:1; min-width:0; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; gap:4px; height:100%; }
  .pf-log-fill{ width:100%; border-radius:3px 3px 0 0; background:var(--pf-surface2); min-height:2px; }
  .pf-log-bar.elite .pf-log-fill{ background:var(--good); }
  .pf-log-bar.good .pf-log-fill{ background:var(--good); opacity:0.7; }
  .pf-log-bar.mid .pf-log-fill{ background:var(--warning); }
  .pf-log-bar.poor .pf-log-fill{ background:var(--critical); opacity:0.8; }
  .pf-log-bar.current .pf-log-fill{ outline:2px solid var(--accent-ink); outline-offset:1px; }
  .pf-log-val{ font-family:"IBM Plex Mono"; font-size:9.5px; color:var(--pf-muted); }
  .pf-log-wk{ font-size:9.5px; color:var(--pf-muted); }
  .pf-empty{ color:var(--pf-muted); padding:26px; text-align:center; font-size:13px; }
  .pf-back{ display:inline-block; margin-top:16px; color:var(--accent-ink); text-decoration:none; font-weight:700; font-size:13px; }
  @media (max-width:640px){
    .pf-name{ font-size:24px; }
    .pf-head img{ width:58px; height:58px; }
    .pf-score .n{ font-size:34px; }
    .pf-headline b{ font-size:21px; }
    .pf-cell b{ font-size:18px; }
  }
</style>

<div class="pf-page">
<div class="wrap">
  {% if load_error %}<div class="error">Couldn't load this performance: {{ load_error }}</div>{% endif %}

  {% if peers %}
  <div class="pf-peers">
    {% for p in peers %}
    <a class="pf-peer" href="/performance?sid={{ p.sid }}&amp;season={{ season }}&amp;week={{ week }}">
      <img src="{{ p.photo }}" alt="" onerror="this.style.visibility='hidden'">
      <span class="sc">{{ '%.1f'|format(p.grade.score) }}</span>
      <span class="nm">{{ p.name.split(' ')[-1] }}</span>
    </a>
    {% endfor %}
  </div>
  {% endif %}

  {% if not detail %}
    <div class="pf-empty">
      No stat line for this player in week {{ week }} yet.
      {# Reachable from a box score, where linemen and special-teamers have
         no fantasy line at all -- so the click still leads somewhere. #}
      <div>
        {% if sid %}<a class="pf-back" href="/player?sid={{ sid }}">Full player profile &rarr;</a> &middot; {% endif %}
        <a class="pf-back" href="/scores">&larr; Back to scores</a>
      </div>
    </div>
  {% else %}
  <div class="pf-head">
    <img src="{{ detail.photo }}" alt="" onerror="this.style.visibility='hidden'">
    <div class="pf-head-main">
      <div class="pf-name">{{ detail.name }}</div>
      <div class="pf-sub">
        {{ detail.position }} &middot; {{ detail.team }}
        {% if detail.vs_label %}&middot; {{ detail.vs_label }}{% endif %}
        {% if detail.team_score is not none and detail.opp_score is not none %}
          &middot; {{ detail.team_score }}&ndash;{{ detail.opp_score }}
        {% endif %}
        &middot; Week {{ detail.week }}
      </div>
      <div class="pf-headline">
        {% for value, label in detail.headline %}
        <span><b>{{ value }}</b><span>{{ label }}</span></span>
        {% endfor %}
        <span><b>{{ detail.fpts }}</b><span>fpts</span></span>
      </div>
    </div>
    <div class="pf-scores">
      {# The calculator mark instead of the word, the same way the score
         is written on the performer board and the power rankings. #}
      <div class="pf-score {{ detail.score.score_class }}">
        <div class="n">{{ score_mark|safe }}{{ '%.1f'|format(detail.score.score) }}</div>
      </div>
      {# The win-probability figure deliberately does NOT sit up here
         beside the rating. Two 42px numerals crowd the header enough to
         wrap the player's own name, and the reference leads with one
         number for a reason -- the swing has its own panel below. #}
    </div>
  </div>

  {% if detail.grid %}
  <div class="pf-panel">
    <h3>Full line</h3>
    <div class="pf-grid">
      {% for value, label in detail.grid %}
      <div class="pf-cell"><b>{{ value }}</b><span>{{ label }}</span></div>
      {% endfor %}
      {% if detail.snap_pct is not none %}
      <div class="pf-cell"><b>{{ detail.snap_pct }}</b><span>SNAP%</span></div>
      {% endif %}
      {% if detail.target_share is not none %}
      <div class="pf-cell"><b>{{ detail.target_share }}</b><span>TGT%</span></div>
      {% endif %}
    </div>
  </div>
  {% endif %}

  <div class="pf-panel">
    <h3>How this scored {{ '%.1f'|format(detail.score.score) }}</h3>

    <!-- Three bars against one shared axis. The previous version put
         "8.5 pts -> 5.0" in a table, which asked the reader to hold two
         different units in their head and mentally interpolate between
         them. Comparing three bars of the same thing needs no
         explanation at all. -->
    {% set peak = [detail.fpts, detail.score.median, detail.score.p99]|max %}
    <p class="pf-lede">
      <b>{{ detail.fpts }} points</b>
      {%- if detail.score.vs_median %} &mdash;
        {{ '%.1f'|format(detail.score.vs_median) }}&times; an average
        {{ detail.position }} game{% endif %}.
    </p>

    <div class="pf-bars">
      <div class="pf-bar-row is-this">
        <span class="pf-bar-label">This game</span>
        <span class="pf-bar-track">
          <span class="pf-bar-fill" style="width:{{ (100 * detail.fpts / peak)|round|int if peak else 0 }}%;"></span>
        </span>
        <span class="pf-bar-val">{{ detail.fpts }}</span>
      </div>
      <div class="pf-bar-row">
        <span class="pf-bar-label">Average {{ detail.position }}</span>
        <span class="pf-bar-track">
          <span class="pf-bar-fill" style="width:{{ (100 * detail.score.median / peak)|round|int if peak else 0 }}%;"></span>
        </span>
        <span class="pf-bar-val">{{ detail.score.median }}</span>
      </div>
      <div class="pf-bar-row">
        <span class="pf-bar-label">Top 1% of {{ detail.position }}s</span>
        <span class="pf-bar-track">
          <span class="pf-bar-fill" style="width:{{ (100 * detail.score.p99 / peak)|round|int if peak else 0 }}%;"></span>
        </span>
        <span class="pf-bar-val">{{ detail.score.p99 }}</span>
      </div>
    </div>

    <p class="pf-foot">
      {% if detail.score.source == 'distribution' %}
        Better than <b>{{ detail.score.percentile }}%</b> of the
        {{ '{:,}'.format(detail.score.pool_size) }} {{ detail.position }} starter games
        played in {{ detail.score.source_season }}.
        An average one scores 5.0, and the top 1% scores 9.0.
      {% else %}
        Scored against a typical {{ detail.position }} baseline &mdash; no season
        of games has been loaded yet to measure against.
      {% endif %}
    </p>
  </div>

  {% if detail.swing %}
  <div class="pf-panel">
    <h3>Impact</h3>
    <p class="pf-lede">
      <b>{{ '%+.1f'|format(detail.swing.total) }} points of win probability</b>
      across {{ detail.swing.plays }} play{{ '' if detail.swing.plays == 1 else 's' }}.
    </p>
    <div class="pf-swings">
      {% for p in detail.feed_plays if p.swing is not none %}
      {% if loop.index <= 5 %}
      <div class="pf-swing">
        <span class="pf-swing-when">
          {% if p.qtr %}Q{{ p.qtr }}{% endif %} {{ p.clock }}
        </span>
        <span class="pf-swing-what">{{ p.headline }}</span>
        <span class="pf-swing-val {{ 'pos' if p.swing > 0 else ('neg' if p.swing < 0 else '') }}">
          {{ '%+.1f'|format(p.swing) }}%
        </span>
      </div>
      {% endif %}
      {% endfor %}
    </div>
    <p class="pf-foot">
      How much each play actually moved the result, not how many fantasy
      points it was worth &mdash; so a one-yard touchdown counts for less
      here than the forty-yard catch that set it up, even though fantasy
      scoring says the opposite. Read straight off the win probability
      ESPN publishes after every snap, which updates while the game is
      still being played.
    </p>
  </div>
  {% endif %}

  {% if quarters %}
  <!-- The rating at the top of the page, split across the quarters it
       was earned in -- so the bars add up to it rather than sitting on
       some unrelated scale. One column per quarter, read left to right. -->
  {% set maxr = (quarters|map(attribute='rating')|max) or 0 %}
  <div class="pf-panel">
    <h3>Rating breakdown</h3>
    <div class="pf-qtrs">
      {% for q in quarters %}
      <div class="pf-qtr">
        <div class="pf-qtr-val">{{ '%.2f'|format(q.rating) }}</div>
        <div class="pf-qtr-col {{ 'zero' if q.rating <= 0 }}"
             style="height:{{ ((118 * q.rating / maxr)|round|int) if maxr > 0 else 3 }}px;"></div>
        <div class="pf-qtr-lab">{{ q.label }}</div>
      </div>
      {% endfor %}
    </div>
    <p class="pf-note">
      Each quarter's share of {{ '%.1f'|format(detail.score.score) }}, weighted by what
      {{ detail.name.split(' ')[-1] }} actually did in it
      {%- if quarters[0].source == 'epa' %} and by how much each play moved the game
      {%- endif %}. The four add back up to the rating above.
    </p>
  </div>
  {% endif %}

  {% if detail.feed_plays %}
  <div class="pf-panel">
    <h3>Plays</h3>
    <!-- The six that mattered, with the rest one tap away -- the same
         shape the reference uses, so a big day reads as a highlight reel
         rather than a transcript. -->
    {% set shown = detail.feed_plays[:24] %}
    <div class="pf-feed" id="pfFeed">
      {% for p in shown %}
      <div class="pf-feed-row {{ 'extra' if loop.index > 6 }}">
        <img class="mug" src="{{ detail.photo }}" alt="" loading="lazy"
             onerror="this.style.visibility='hidden'">
        <div class="pf-feed-main">
          <div class="pf-feed-sit">
            {% if detail.team_logo %}<img src="{{ detail.team_logo }}" alt=""
                 onerror="this.style.display='none'">{% endif %}
            <b>{{ p.team_score if p.team_score is not none else '-' }}</b>
            <span>&ndash;</span>
            <b>{{ p.opp_score if p.opp_score is not none else '-' }}</b>
            {% if detail.opp_logo %}<img src="{{ detail.opp_logo }}" alt=""
                 onerror="this.style.display='none'">{% endif %}
            <span>{% if p.qtr %}Q{{ p.qtr }}{% endif %} {{ p.clock }}</span>
            {%- if p.down %}<span>&middot; {{ p.down|ordinal }} &amp; {{ p.ydstogo }}</span>{% endif %}
          </div>
          <div class="pf-feed-title {{ 'scored' if p.scored }}">{{ p.headline }}</div>
        </div>
        <div class="pf-feed-rate">{{ score_mark|safe }}{{ '%.1f'|format(p.rating) }}</div>
      </div>
      {% endfor %}
      {% if shown|length > 6 %}
      <span class="pf-feed-more" onclick="document.getElementById('pfFeed').classList.add('all');">
        View all {{ shown|length }} plays &rsaquo;
      </span>
      {% endif %}
    </div>
  </div>
  {% endif %}

  {% if log|length > 1 %}
  <div class="pf-panel">
    <h3>{{ detail.season }} game log</h3>
    <div class="pf-log">
      {% for g in log %}
      <div class="pf-log-bar {{ 'current' if g.week == detail.week else '' }}
                  {{ 'elite' if g.score >= 8 else ('good' if g.score >= 6 else ('mid' if g.score >= 4 else 'poor')) }}"
           title="Week {{ g.week }}: {{ g.fpts }} pts, scored {{ '%.1f'|format(g.score) }}">
        <span class="pf-log-val">{{ '%.1f'|format(g.score) }}</span>
        <div class="pf-log-fill" style="height:{{ (g.score * 10)|round|int }}%;"></div>
        <span class="pf-log-wk">{{ g.week }}</span>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  <a class="pf-back" href="/scores">&larr; Back to scores</a>
  {% endif %}
</div>
</div>
"""


GAME_DETAIL_HTML = BASE_STYLE + make_header("scores") + """
<style>
  /* ---- live game layout (strip / field / tabs) ---- */
  .gd-others{ display:flex; gap:0; overflow-x:auto; scrollbar-width:none;
              border-bottom:1px solid var(--line); margin-bottom:14px; }
  .gd-others::-webkit-scrollbar{ display:none; }
  .gd-other{ flex:none; padding:8px 14px; text-decoration:none; color:var(--ink-muted);
             border-bottom:2px solid transparent; font-size:11.5px; white-space:nowrap; }
  .gd-other.live{ color:var(--ink); }
  .gd-other .sc{ font-family:"IBM Plex Mono"; font-weight:700; font-size:13px; display:block; }
  .gd-other .st{ font-size:10px; }

  /* Field position. One bar, both directions of travel, with the ball
     where it actually is -- reading a yard line off text alone is what
     this replaces. */
  .gd-field{ margin:14px 0 4px; }
  .gd-field-bar{ position:relative; height:26px; border-radius:6px; background:var(--paper-sunken);
                 border:1px solid var(--line); overflow:hidden; }
  .gd-field-ez{ position:absolute; top:0; bottom:0; width:9%; background:rgba(255,255,255,0.05); }
  .gd-field-ez.left{ left:0; } .gd-field-ez.right{ right:0; }
  .gd-field-tick{ position:absolute; top:0; bottom:0; width:1px; background:var(--line); }
  .gd-field-ball{ position:absolute; top:50%; transform:translate(-50%,-50%);
                  width:13px; height:9px; border-radius:50%; background:var(--accent-ink); }
  .gd-field-first{ position:absolute; top:0; bottom:0; width:2px; background:var(--warning); }
  .gd-field-meta{ display:flex; justify-content:space-between; font-size:11.5px;
                  color:var(--ink-muted); margin-top:5px; }
  .gd-field-meta b{ color:var(--ink); }

  .gd-tabs{ display:flex; gap:0; overflow-x:auto; scrollbar-width:none; border-bottom:1px solid var(--line);
            margin:18px 0 0; }
  .gd-tabs::-webkit-scrollbar{ display:none; }
  .gd-tab{ flex:none; padding:9px 16px; font-size:13.5px; font-weight:700; cursor:pointer;
           color:var(--ink-muted); border-bottom:2px solid transparent; background:none; border-top:none;
           border-left:none; border-right:none; }
  .gd-tab.on{ color:var(--accent-ink); border-bottom-color:var(--accent-ink); }
  .gd-panel{ display:none; padding-top:14px; } .gd-panel.on{ display:block; }
  .gd-sub{ display:flex; gap:14px; margin-bottom:10px; }
  .gd-sub button{ background:none; border:none; cursor:pointer; font-size:13px; font-weight:700;
                  color:var(--ink-muted); padding:4px 0; }
  .gd-sub button.on{ color:var(--accent-ink); }

  .gd-play{ padding:12px 0; border-top:1px solid var(--line); }
  .gd-play:first-child{ border-top:none; }
  .gd-play-head{ display:flex; justify-content:space-between; gap:10px; font-size:11px;
                 color:var(--ink-muted); margin-bottom:7px; }
  .gd-play-body{ display:flex; gap:11px; align-items:flex-start; }
  /* Two overlapping faces for a play with two players in it. Sized so
     the pair occupies the same column width as a single face does, and
     the row never shifts depending on how many people a play involved. */
  .gd-play-faces{ position:relative; flex:none; width:46px; height:46px; }
  .gd-play-faces.pair{ width:56px; height:52px; }
  .gd-play-faces.pair .gd-play-photo{ position:absolute; width:34px; height:34px; }
  .gd-play-faces.pair .back{ left:0; top:0; }
  .gd-play-faces.pair .front{ right:0; bottom:0; border:2px solid var(--paper-raised); }
  .gd-play-link{ color:inherit; text-decoration:none; }
  .gd-play-link:hover b{ text-decoration:underline; }
  .gd-play-photo{ width:46px; height:46px; border-radius:50%; object-fit:cover; flex:none;
                  background:var(--paper-sunken); }
  .gd-play-main{ flex:1; min-width:0; }
  .gd-play-title{ font-size:17px; font-weight:800; font-family:"Big Shoulders Display";
                  text-transform:uppercase; letter-spacing:0.01em; line-height:1.15; }
  .gd-play.score .gd-play-title{ color:var(--accent-ink); }
  .gd-play-who{ font-size:12.5px; color:var(--ink-secondary); margin-top:2px; }
  .gd-play-who b{ color:var(--ink); font-weight:700; }
  .gd-play-who .fps{ color:var(--ink-muted); }
  .gd-play-notes{ margin-top:7px; display:flex; flex-direction:column; gap:4px; }
  .gd-play-note{ display:flex; align-items:center; gap:7px; font-size:13px;
                 color:var(--ink-secondary); }
  .gd-play-dot{ width:9px; height:9px; border-radius:50%; flex:none;
                background:var(--ink-muted); }
  .gd-play-note.good .gd-play-dot{ background:var(--good); }
  .gd-play-note.bad .gd-play-dot{ background:var(--critical); }
  .gd-play-note.warn .gd-play-dot{ background:var(--warning); }

  .gd-totals{ display:flex; flex-wrap:wrap; gap:12px 18px; padding:10px 0 14px;
              border-bottom:1px solid var(--line); margin-bottom:6px; }
  .gd-total b{ display:block; font-family:"IBM Plex Mono"; font-size:16px; }
  .gd-total span{ font-size:10px; color:var(--ink-muted); text-transform:uppercase; letter-spacing:0.03em; }
  .gd-bp{ padding:10px 0; border-top:1px solid var(--line); display:flex; gap:11px; align-items:flex-start; }
  .gd-bp:first-child{ border-top:none; }
  .gd-bp-photo{ width:40px; height:40px; border-radius:50%; object-fit:cover; flex:none;
                background:var(--paper-sunken); display:block; }
  /* A headshot neither source has becomes a neutral circle rather than a
     broken-image icon, so a row without a photo still lines up with the
     rows that have one. */
  .gd-bp-photo.missing{ background:var(--paper-sunken); }
  .gd-bp-main{ flex:1; min-width:0; }
  .gd-bp-name{ font-size:13.5px; font-weight:700; }
  .gd-bp-name span{ color:var(--ink-muted); font-weight:400; font-size:11.5px; margin-left:5px; }
  .gd-bp-link{ color:inherit; text-decoration:none; }
  .gd-bp-link:hover{ text-decoration:underline; }
  .gd-bp.bench{ opacity:0.72; }
  .gd-bp-divider{ margin-top:14px; padding-top:10px; border-top:1px solid var(--line-strong);
                  font-size:10.5px; letter-spacing:0.09em; text-transform:uppercase;
                  color:var(--ink-muted); }
  .gd-bp-divider + .gd-bp{ border-top:none; }
  .gd-bp-stats{ display:flex; flex-wrap:wrap; gap:10px 16px; margin-top:5px; }
  .gd-bp-stat b{ font-family:"IBM Plex Mono"; font-size:14px; }
  .gd-bp-stat span{ font-size:9.5px; color:var(--ink-muted); margin-left:2px; text-transform:uppercase; }
  .gd-empty{ color:var(--ink-muted); padding:24px; text-align:center; font-size:13px; }

  /* Three columns that hold their shape at phone width. This used to be
     a wrapping flex row, which stacked both teams vertically on a phone
     and lost the head-to-head reading a scoreboard exists to give. */
  .gd-header{ display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:10px; }
  .gd-side{ display:flex; align-items:center; gap:10px; min-width:0; }
  .gd-side img{ width:44px; height:44px; object-fit:contain; flex:none; }
  .gd-side .nm{ font-family:"Big Shoulders Display"; font-size:16px; font-weight:800;
                text-transform:uppercase; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .gd-score{ font-family:"IBM Plex Mono"; font-size:34px; font-weight:700; line-height:1.05; }
  @media (max-width:640px){
    .gd-side img{ width:34px; height:34px; }
    .gd-side .nm{ font-size:13px; }
    .gd-score{ font-size:27px; }
    .gd-header{ gap:6px; }
  }
  .gd-mid{ display:flex; flex-direction:column; align-items:center; gap:6px; }
  .gd-status{ font-size:12px; font-weight:700; text-transform:uppercase; padding:4px 12px; border-radius:99px; background:var(--paper-sunken); color:var(--ink-muted); }
  .gd-status.in_progress{ background:var(--warning-wash); color:var(--warning); }
  .gd-venue{ color:var(--ink-secondary); font-size:13px; margin-top:6px; }
  .gd-officials{ display:flex; flex-wrap:wrap; gap:10px 24px; margin-top:10px; }

  /* ---- section headings inside a tab panel ---- */
  .gd-sect{ font-size:11px; letter-spacing:0.09em; text-transform:uppercase; font-weight:800;
            color:var(--accent-ink); margin:22px 0 8px; }
  .gd-sect.first{ margin-top:4px; }

  /* ---- momentum ---- */
  .gd-momentum{ margin-top:4px; }
  .gd-mom-head{ display:flex; justify-content:space-between; font-size:12.5px; font-weight:700; }
  .gd-mom-home{ color:var(--pos-rb); } .gd-mom-away{ color:var(--pos-wr); }
  .gd-mom-svg{ display:block; width:100%; height:84px; margin:6px 0 2px;
               background:var(--paper-sunken); border-radius:8px; }
  .gd-mom-axis{ display:flex; justify-content:space-between; font-size:10px;
                text-transform:uppercase; letter-spacing:0.06em; color:var(--ink-muted); }
  .gd-mom-swing{ margin-top:8px; font-size:12.5px; color:var(--ink-secondary); }
  .gd-mom-swing b{ color:var(--ink); font-family:"IBM Plex Mono"; }

  /* ---- bets ---- */
  .gd-bet-head{ display:flex; gap:8px; margin-top:4px; }
  .gd-bet-cell{ flex:1; background:var(--paper-sunken); border-radius:10px; padding:12px 10px; text-align:center; }
  .gd-bet-label{ display:block; font-size:9.5px; text-transform:uppercase; letter-spacing:0.07em;
                 color:var(--ink-muted); font-weight:700; }
  .gd-bet-cell b{ display:block; font-family:"IBM Plex Mono"; font-size:15px; margin-top:4px; }
  .gd-bet-sub{ display:block; font-size:11px; color:var(--ink-muted); margin-top:2px; }
  .gd-bet-src{ font-size:11px; color:var(--ink-muted); margin-top:8px; }
  .gd-bet-table{ margin-top:6px; }
  .gd-bet-row{ display:grid; grid-template-columns:1.4fr 1fr 0.8fr 0.9fr 0.9fr; gap:6px;
               padding:9px 0; border-top:1px solid var(--line); font-size:12.5px; align-items:center; }
  .gd-bet-row.head{ border-top:none; font-size:9.5px; text-transform:uppercase;
                    letter-spacing:0.06em; color:var(--ink-muted); font-weight:700; }
  .gd-bet-row span:not(:first-child){ text-align:right; }
  .gd-bet-note{ margin-top:16px; font-size:11px; color:var(--ink-muted); line-height:1.5; }

  /* ---- game information / officials ---- */
  .gd-info-grid{ display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:8px; }
  .gd-info-item{ background:var(--paper-sunken); border-radius:10px; padding:10px 12px; min-width:0; }
  .gd-info-item.wide{ grid-column:1 / -1; }
  .gd-info-item span{ display:block; font-size:9.5px; text-transform:uppercase;
                      letter-spacing:0.07em; color:var(--ink-muted); font-weight:700; }
  .gd-info-item b{ display:block; font-size:13px; margin-top:3px; word-break:break-word; }

  /* ---- top performers, inside the Game tab ---- */
  .gd-leader{ display:flex; align-items:center; gap:10px; padding:10px 0; border-top:1px solid var(--line); }
  .gd-leader-team{ font-size:10.5px; font-weight:800; color:var(--ink-muted); flex:none; width:34px; }
  .gd-leader-main{ flex:1; min-width:0; font-size:13.5px; font-weight:700; }
  .gd-leader-main span{ display:block; font-size:11px; font-weight:400; color:var(--ink-muted); }

  /* ---- the rest of a team's own stat line ---- */
  .gd-more{ margin-top:12px; }
  .gd-more summary{ cursor:pointer; font-size:12px; font-weight:700; color:var(--accent-ink);
                    list-style:none; padding:8px 0; }
  .gd-more summary::-webkit-details-marker{ display:none; }
  .gd-more summary::after{ content:" \\25BE"; }
  .gd-more[open] summary::after{ content:" \\25B4"; }
  .gd-team-stats{ display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:2px 14px; }
  .gd-team-stat{ display:flex; justify-content:space-between; gap:8px; padding:6px 0;
                 border-top:1px solid var(--line); font-size:12.5px; }
  .gd-team-stat span{ color:var(--ink-secondary); min-width:0; }
  .gd-official{ font-size:13px; }
  .gd-official b{ color:var(--ink); }
  .gd-stat-row{ display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:10px; padding:8px 4px; border-top:1px solid var(--line); font-size:13.5px; }
  .gd-stat-row:first-child{ border-top:none; }
  .gd-stat-label{ text-align:center; color:var(--ink-muted); font-size:11.5px; text-transform:uppercase; letter-spacing:0.03em; }
  .gd-stat-val{ font-family:"IBM Plex Mono"; }
  .gd-stat-val.away{ text-align:left; }
  .gd-stat-val.home{ text-align:right; }
  .gd-pregame{ display:flex; gap:18px; flex-wrap:wrap; margin-top:10px; font-size:13px; color:var(--ink-secondary); }
  .gd-wp-bar{ display:flex; height:22px; border-radius:6px; overflow:hidden; margin-top:8px; }
  .gd-my-players{ margin-top:14px; padding-top:12px; border-top:1px solid var(--line); }
  .gd-my-players-group{ display:flex; align-items:baseline; gap:10px; margin-top:8px; flex-wrap:wrap; }
  .gd-my-players-group:first-of-type{ margin-top:2px; }
  .gd-my-players-team{ font-family:"IBM Plex Mono"; font-weight:700; font-size:11.5px; color:var(--ink-muted); flex:none; width:32px; }
  .gd-my-players-chips{ display:flex; flex-wrap:wrap; gap:6px; flex:1; min-width:0; }
  .gd-player-chip{ display:inline-flex; align-items:center; gap:6px; background:var(--paper-sunken); border-radius:99px; padding:4px 10px 4px 5px; font-size:12.5px; white-space:nowrap; color:inherit; text-decoration:none; }
  .gd-player-chip:hover{ background:var(--line); }
  .gd-player-chip-n{ color:var(--ink-muted); font-size:11px; }
  @media (max-width: 480px) {
    .gd-my-players-group{ flex-direction:column; gap:4px; }
    .gd-my-players-team{ width:auto; }
  }
</style>
<main><div class="wrap">
  <a href="/scores" class="muted">&larr; Back to scores</a>
  {% if load_error %}<div class="error">Couldn't load this game right now: {{ load_error }}</div>{% endif %}

  {% if others %}
  <div class="gd-others">
    {% for g in others %}
    <a class="gd-other {{ 'live' if g.status == 'in_progress' }}" href="/game?id={{ g.id }}">
      <span class="sc">{{ g.away.abbr }} {{ g.away.score or 0 }}&ndash;{{ g.home.score or 0 }} {{ g.home.abbr }}</span>
      <span class="st">
        {%- if g.status == 'in_progress' %}{{ g.clock }} Q{{ g.period }}
        {%- elif g.status == 'final' %}Final
        {%- else %}{{ g.broadcast or 'Scheduled' }}{% endif -%}
      </span>
    </a>
    {% endfor %}
  </div>
  {% endif %}
  <div class="panel">
    <div class="gd-header">
      <div class="gd-side">
        <a href="/team?abbr={{ detail.away.abbr }}"><img src="{{ detail.away.logo or '' }}" alt="" onerror="this.style.visibility='hidden'"></a>
        <div><div class="nm"><a href="/team?abbr={{ detail.away.abbr }}" style="color:inherit; text-decoration:none;">{{ detail.away.name }}</a></div><div class="gd-score" id="gdAwayScore">{{ detail.away.score or 0 }}</div>{% if detail.away.record %}<span class="muted mono" style="font-size:11px;">{{ detail.away.record }}</span>{% endif %}</div>
      </div>
      <div class="gd-mid">
        <span class="gd-status {{ detail.status }}" id="gdStatus">{{ detail.status_detail or detail.status }}</span>
        <span class="muted" id="gdClock">{% if detail.status == 'in_progress' %}{{ detail.clock }} &middot; Q{{ detail.period }}{% endif %}</span>
      </div>
      <div class="gd-side" style="flex-direction:row-reverse; text-align:right;">
        <a href="/team?abbr={{ detail.home.abbr }}"><img src="{{ detail.home.logo or '' }}" alt="" onerror="this.style.visibility='hidden'"></a>
        <div><div class="nm"><a href="/team?abbr={{ detail.home.abbr }}" style="color:inherit; text-decoration:none;">{{ detail.home.name }}</a></div><div class="gd-score" id="gdHomeScore">{{ detail.home.score or 0 }}</div>{% if detail.home.record %}<span class="muted mono" style="font-size:11px;">{{ detail.home.record }}</span>{% endif %}</div>
      </div>
    </div>
    {% if detail.field %}
    <!-- Field position. The ball's yardLine is "yards from the opponent's
         goal line", so 50 is midfield and a small number means close to
         scoring -- which side of the bar that lands on depends on who has
         it, hence the flip. -->
    {% set yl = detail.field.yardline %}
    {% set pct = (100 - yl) if (yl is not none and detail.field.possession == detail.away.abbr) else yl %}
    <div class="gd-field">
      <div class="gd-field-bar">
        <div class="gd-field-ez left"></div><div class="gd-field-ez right"></div>
        {% for i in range(1, 10) %}
        <div class="gd-field-tick" style="left:{{ 9 + i * 8.2 }}%;"></div>
        {% endfor %}
        {% if pct is not none %}
        <div class="gd-field-ball" style="left:{{ 9 + (pct|float) * 0.82 }}%;"></div>
        {% endif %}
      </div>
      <div class="gd-field-meta">
        <span>
          {%- if detail.field.possession %}<b>{{ detail.field.possession }}</b> ball{% endif -%}
          {%- if detail.field.possession_text %} &middot; {{ detail.field.possession_text }}{% endif -%}
        </span>
        <span>{% if detail.field.down_distance %}<b>{{ detail.field.down_distance }}</b>{% endif %}</span>
      </div>
    </div>
    {% endif %}

    {# Venue, officials, broadcast and the betting line all live behind
       the Game and Bets tabs now. The header stays what a scoreboard is:
       who, what the score is, and where the ball sits. #}
    {% if detail.status == 'scheduled' and (detail.broadcasts or detail.odds) %}
    <div class="gd-pregame">
      {% if detail.broadcasts %}<span>&#128250; {{ detail.broadcasts|join(', ') }}</span>{% endif %}
      {% if detail.odds and detail.odds.spread %}<span>{{ detail.odds.spread }}{% if detail.odds.over_under %} &middot; O/U {{ detail.odds.over_under }}{% endif %}</span>{% endif %}
    </div>
    {% endif %}
    {% if detail.my_players and (detail.my_players.away or detail.my_players.home) %}
    <div class="gd-my-players">
      <p class="eyebrow">Your Players In This Game</p>
      {% for side_label, players in [(detail.away.abbr, detail.my_players.away), (detail.home.abbr, detail.my_players.home)] %}
        {% if players %}
        <div class="gd-my-players-group">
          <span class="gd-my-players-team">{{ side_label }}</span>
          <div class="gd-my-players-chips">
            {% for p in players %}
            <a class="gd-player-chip" title="{{ p.leagues|join(', ') if p.leagues else '' }}"
               href="/performance?sid={{ p.sid }}&amp;season={{ detail.season }}&amp;week={{ detail.week }}">
              <span class="pos-chip" style="background:var(--pos-{{ p.position|lower }}, var(--ink-muted));">{{ p.position }}</span>{{ p.name }}{% if p.leagues and p.leagues|length > 1 %}<span class="gd-player-chip-n">&times;{{ p.leagues|length }}</span>{% endif %}
            </a>
            {% endfor %}
          </div>
        </div>
        {% endif %}
      {% endfor %}
    </div>
    {% endif %}
  </div>

  <!-- Feed / per-team tabs. Plain buttons toggling panels rather than
       separate pages, so switching between the play feed and either
       team's box score never costs a round trip mid-drive. -->
  <div class="gd-tabs" id="gdTabs">
    <button class="gd-tab on" data-panel="feed">Feed</button>
    <button class="gd-tab" data-panel="bets">Bets</button>
    <button class="gd-tab" data-panel="game">Game</button>
    {% if detail.away.abbr %}<button class="gd-tab" data-panel="away">{{ detail.away.abbr }}</button>{% endif %}
    {% if detail.home.abbr %}<button class="gd-tab" data-panel="home">{{ detail.home.abbr }}</button>{% endif %}
  </div>

  <!-- Rendered by JS, not Jinja, on purpose: the live poll re-renders
       this every few seconds, and having the initial paint come from a
       separate server-side template is how the two silently drift apart.
       One renderer, used by both. -->
  <div class="gd-panel on" data-panel="feed" id="gdFeedPanel"></div>

  <!-- Bets: what the market says about this game. Reported, never
       offered -- there is nothing to click, no book linked, no wager
       placed. The same posture as showing the score. -->
  <div class="gd-panel" data-panel="bets">
    {% if detail.betting.lines %}
    {% set head = detail.betting.lines[0] %}
    <div class="gd-bet-head">
      <div class="gd-bet-cell">
        <span class="gd-bet-label">Spread</span>
        <b>{{ head.spread or '&ndash;' }}</b>
      </div>
      <div class="gd-bet-cell">
        <span class="gd-bet-label">Total</span>
        <b>{% if head.over_under %}{{ head.over_under }}{% else %}&ndash;{% endif %}</b>
        {% if head.over_odds or head.under_odds %}
        <span class="gd-bet-sub">O {{ head.over_odds or '&ndash;' }} &middot; U {{ head.under_odds or '&ndash;' }}</span>
        {% endif %}
      </div>
      <div class="gd-bet-cell">
        <span class="gd-bet-label">Moneyline</span>
        <b>{{ detail.away.abbr }} {{ head.away_ml or '&ndash;' }}</b>
        <span class="gd-bet-sub">{{ detail.home.abbr }} {{ head.home_ml or '&ndash;' }}</span>
      </div>
    </div>
    {% if head.provider %}<div class="gd-bet-src">Line from {{ head.provider }}</div>{% endif %}

    {% if detail.betting.lines|length > 1 %}
    <p class="gd-sect">Every book</p>
    <div class="gd-bet-table">
      <div class="gd-bet-row head">
        <span>Book</span><span>Spread</span><span>Total</span><span>{{ detail.away.abbr }}</span><span>{{ detail.home.abbr }}</span>
      </div>
      {% for l in detail.betting.lines %}
      <div class="gd-bet-row">
        <span>{{ l.provider }}</span>
        <span class="mono">{{ l.spread or '&ndash;' }}</span>
        <span class="mono">{{ l.over_under if l.over_under else '&ndash;' }}</span>
        <span class="mono">{{ l.away_ml or '&ndash;' }}</span>
        <span class="mono">{{ l.home_ml or '&ndash;' }}</span>
      </div>
      {% endfor %}
    </div>
    {% endif %}
    {% else %}
    <div class="gd-empty">No betting lines published for this game.</div>
    {% endif %}

    {% if detail.betting.ats %}
    <p class="gd-sect">Against the spread this season</p>
    <div class="gd-info-grid">
      {% for a in detail.betting.ats %}
      <div class="gd-info-item"><span>{{ a.team }}</span><b>{{ a.record }}</b></div>
      {% endfor %}
    </div>
    {% endif %}
    <p class="gd-bet-note">Lines are shown for information only. Nothing here is a wager or an offer to place one.</p>
  </div>

  <div class="gd-panel" data-panel="game" id="gdGamePanel">
    <!-- Momentum: the home side's win probability after every play. The
         honest version of momentum -- not a feeling, but how far each
         play actually moved the result. JS-rendered so the poll can
         repaint it mid-drive through the same function. -->
    <p class="gd-sect first">Momentum</p>
    <div id="gdMomentum" class="gd-momentum"></div>

    {% if detail.away.linescores and detail.home.linescores %}
    <p class="gd-sect">Score by quarter</p>
    <table class="rank-table" style="margin-top:6px;" id="gdLinescoreTable">
      <tr><th></th>{% for i in range(detail.away.linescores|length) %}<th>Q{{ i+1 }}</th>{% endfor %}<th>T</th></tr>
      <tr data-side="away"><td>{{ detail.away.abbr }}</td>{% for v in detail.away.linescores %}<td class="mono">{{ v if v is not none else '-' }}</td>{% endfor %}<td class="mono" data-final>{{ detail.away.score }}</td></tr>
      <tr data-side="home"><td>{{ detail.home.abbr }}</td>{% for v in detail.home.linescores %}<td class="mono">{{ v if v is not none else '-' }}</td>{% endfor %}<td class="mono" data-final>{{ detail.home.score }}</td></tr>
    </table>
    {% endif %}

    <p class="gd-sect">Team stats</p>
    {% if detail.team_stats %}
      <div class="gd-stat-row" style="font-weight:800;">
        <span class="gd-stat-val away">{{ detail.away.abbr }}</span>
        <span class="gd-stat-label"></span>
        <span class="gd-stat-val home">{{ detail.home.abbr }}</span>
      </div>
      {% for s in detail.team_stats %}
      <div class="gd-stat-row">
        <span class="gd-stat-val away">{{ s.away }}</span>
        <span class="gd-stat-label">{{ s.label }}</span>
        <span class="gd-stat-val home">{{ s.home }}</span>
      </div>
      {% endfor %}
    {% else %}
      <div class="gd-empty">Team stats appear once the game is under way.</div>
    {% endif %}

    {% if detail.player_leaders %}
    <p class="gd-sect">Top performers</p>
    {% for l in detail.player_leaders %}
    <div class="gd-leader">
      <span class="gd-leader-team">{{ l.team }}</span>
      <span class="gd-leader-main">
        {% if l.sid %}<a class="gd-bp-link" href="/performance?sid={{ l.sid }}&amp;season={{ detail.season }}&amp;week={{ detail.week }}">{{ l.athlete }}</a>{% else %}{{ l.athlete }}{% endif %}
        <span>{{ l.category }}</span>
      </span>
      <span class="mono">{{ l.stat_line }}</span>
    </div>
    {% endfor %}
    {% endif %}

    <p class="gd-sect">Game information</p>
    <div class="gd-info-grid">
      {% if detail.venue.name %}
      <div class="gd-info-item wide"><span>Venue</span><b>{{ detail.venue.name }}{% if detail.venue.city %} &middot; {{ detail.venue.city }}{% if detail.venue.state %}, {{ detail.venue.state }}{% endif %}{% endif %}</b></div>
      {% endif %}
      {% if detail.broadcasts %}<div class="gd-info-item"><span>Coverage</span><b>{{ detail.broadcasts|join(', ') }}</b></div>{% endif %}
      {% if detail.info.attendance %}<div class="gd-info-item"><span>Attendance</span><b>{{ '{:,}'.format(detail.info.attendance) }}{% if detail.info.capacity %} / {{ '{:,}'.format(detail.info.capacity) }}{% endif %}</b></div>{% endif %}
      {% if detail.info.surface %}<div class="gd-info-item"><span>Surface</span><b>{{ detail.info.surface }}{% if detail.info.indoor %} &middot; Indoor{% endif %}</b></div>{% endif %}
      {% if detail.info.weather or detail.info.temperature is not none %}
      <div class="gd-info-item"><span>Weather</span><b>
        {%- if detail.info.weather_emoji %}{{ detail.info.weather_emoji }} {% endif -%}
        {%- if detail.info.weather %}{{ detail.info.weather }}{% endif -%}
        {%- if detail.info.temperature is not none %} {{ detail.info.temperature }}&deg;F{% endif -%}
      </b></div>
      {% endif %}
      {% if detail.odds and detail.odds.spread %}
      <div class="gd-info-item"><span>Line</span><b>{{ detail.odds.spread }}{% if detail.odds.over_under %} &middot; O/U {{ detail.odds.over_under }}{% endif %}</b></div>
      {% endif %}
    </div>

    {% if detail.officials %}
    <p class="gd-sect">Officials</p>
    <div class="gd-info-grid">
      {% for o in detail.officials %}
      <div class="gd-info-item"><span>{{ o.position or 'Official' }}</span><b>{{ o.name }}</b></div>
      {% endfor %}
    </div>
    {% endif %}
  </div>

  {% for side, abbr in [('away', detail.away.abbr), ('home', detail.home.abbr)] %}
  {% if abbr %}
  <div class="gd-panel" data-panel="{{ side }}">
    {% if detail.totals.get(abbr) %}
    <div class="gd-totals">
      {% for t in detail.totals[abbr][:9] %}
      <span class="gd-total"><b>{{ t.value }}</b><span>{{ t.label }}</span></span>
      {% endfor %}
    </div>
    {# The headline chips above are the first nine; this is the rest of
       the team's line, so a team tab holds ALL of its stats rather than
       sending you to the Game tab to compare for a single number. #}
    {% if detail.totals[abbr]|length > 9 %}
    <details class="gd-more">
      <summary>All {{ abbr }} team stats</summary>
      <div class="gd-team-stats">
        {% for t in detail.totals[abbr] %}
        <div class="gd-team-stat"><span>{{ t.label }}</span><b class="mono">{{ t.value }}</b></div>
        {% endfor %}
      </div>
    </details>
    {% endif %}
    {% endif %}
    {% set box = detail.box.get(abbr) or {'offense': [], 'defense': []} %}
    {% if box.offense or box.defense %}
    <div class="gd-sub" data-sub-for="{{ side }}">
      <button class="on" data-sub="offense">Offense</button>
      <button data-sub="defense">Defense</button>
    </div>
    {% for group in ['offense', 'defense'] %}
    <div class="gd-subpanel" data-sub-panel="{{ side }}-{{ group }}"
         style="{{ '' if group == 'offense' else 'display:none;' }}">
      {% for pl in box[group] %}
      {% if pl.bench and not loop.first and not box[group][loop.index0 - 1].bench %}
      <div class="gd-bp-divider">Bench</div>
      {% endif %}
      <div class="gd-bp{% if pl.bench %} bench{% endif %}">
        {% set href = '/performance?sid=' ~ pl.sid ~ '&season=' ~ detail.season ~ '&week=' ~ detail.week if pl.sid else None %}
        {% if href %}<a href="{{ href }}" aria-label="{{ pl.name }}">{% endif %}
        {% if pl.headshot %}
        <img class="gd-bp-photo" src="{{ pl.headshot }}" alt=""
             onerror="this.classList.add('missing');">
        {% else %}
        <span class="gd-bp-photo missing"></span>
        {% endif %}
        {% if href %}</a>{% endif %}
        <div class="gd-bp-main">
          <div class="gd-bp-name">
            {% if href %}<a class="gd-bp-link" href="{{ href }}">{{ pl.name }}</a>{% else %}{{ pl.name }}{% endif %}
            {% if pl.position %}<span>{{ pl.position }}</span>{% endif %}
          </div>
          <div class="gd-bp-stats">
            {% for s in pl.stats[:8] %}
            <span class="gd-bp-stat"><b>{{ s.value }}</b><span>{{ s.label }}</span></span>
            {% endfor %}
          </div>
        </div>
      </div>
      {% else %}
      <div class="gd-empty">No {{ group }} stats yet.</div>
      {% endfor %}
    </div>
    {% endfor %}
    {% else %}
    <div class="gd-empty">Box score appears once the game is under way.</div>
    {% endif %}
  </div>
  {% endif %}
  {% endfor %}

  {% if detail.status == 'scheduled' and not detail.team_stats and not detail.player_leaders %}
  <p class="muted" style="margin-top:14px; text-align:center;">Full box score and stats will appear here once the game kicks off.</p>
  {% endif %}
</div></main>
<script>
// ---- one feed renderer, shared by the initial paint and every poll ----
// Exposed on window so the polling IIFE below can call it. Having the
// first render come from a server-side template and the refresh from JS
// is precisely how the two drift apart, so there is only this.
window.GD_STATUS = {{ detail.status|tojson }};
window.GD_SEASON = {{ detail.season|tojson }};
window.GD_WEEK = {{ detail.week|tojson }};
window.gdRenderFeed = function(plays, status){
  const el = document.getElementById('gdFeedPanel');
  if (!el) return;
  plays = plays || [];
  if (!plays.length) {
    el.innerHTML = '<div class="gd-empty">' +
      (status === 'scheduled' ? 'Plays appear here once the game kicks off.'
                              : 'No play-by-play available for this game.') + '</div>';
    return;
  }
  function esc(s){
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function ord(n){ return n === 1 ? '1st' : n === 2 ? '2nd' : n === 3 ? '3rd' : n + 'th'; }
  // Every player in the feed points at their own game -- their plays and
  // their stat line -- so a name or a face is somewhere to go, not decoration.
  function plink(sid){
    return '/performance?sid=' + encodeURIComponent(sid) +
           '&season=' + encodeURIComponent(window.GD_SEASON) +
           '&week=' + encodeURIComponent(window.GD_WEEK);
  }

  el.innerHTML = plays.map(function(p){
    const sit = [
      p.team ? esc(p.team) : null,
      p.period ? 'Q' + p.period + (p.clock ? ' ' + esc(p.clock) : '') : null,
      p.down ? ord(p.down) + ' &amp; ' + esc(p.distance) : null
    ].filter(Boolean).join(' &middot; ');
    const score = (p.away_score != null && p.home_score != null)
      ? esc(p.away_score) + '&ndash;' + esc(p.home_score) : '';
    // Two faces, stacked, the way the reference feed does it: the
    // player the play belongs to in front, whoever else it ran through
    // behind them. One face when that is all there is.
    const cast = (p.people || []).filter(function(pl){ return pl.photo; });
    const lead = cast[0], second = cast[1];
    function face(pl, cls){
      const img = '<img class="gd-play-photo ' + cls + '" src="' + esc(pl.photo) + '" alt="" ' +
                  'onerror="this.style.visibility=\\'hidden\\'">';
      return pl.sid
        ? '<a href="' + plink(pl.sid) + '" aria-label="' + esc(pl.name) + '">' + img + '</a>'
        : img;
    }
    const faces = !lead ? ''
      : '<span class="gd-play-faces' + (second ? ' pair' : '') + '">' +
          (second ? face(second, 'back') : '') + face(lead, 'front') +
        '</span>';
    const who = (p.people || []).map(function(pl){
      const inner = '<b>' + esc(pl.name) + '</b>' +
             (pl.position ? ' <span class="fps">' + esc(pl.position) + '</span>' : '') +
             (pl.fpts != null ? ' <span class="fps">&middot; ' + esc(pl.fpts) + ' fps</span>' : '');
      return '<div>' + (pl.sid
        ? '<a class="gd-play-link" href="' + plink(pl.sid) + '">' + inner + '</a>'
        : inner) + '</div>';
    }).join('');
    // One fact per line with a coloured dot, in place of ESPN's raw
    // sentence -- which said the same things buried in officialese.
    const notes = (p.notes || []).map(function(n){
      return '<div class="gd-play-note ' + esc(n.tone || 'info') + '">' +
             '<span class="gd-play-dot"></span>' + esc(n.label) + '</div>';
    }).join('');
    return '<div class="gd-play' + (p.scoring ? ' score' : '') + '">' +
      '<div class="gd-play-head"><span>' + sit + '</span><span>' + score + '</span></div>' +
      '<div class="gd-play-body">' +
        faces +
        '<div class="gd-play-main">' +
          '<div class="gd-play-title">' + esc(p.headline || '') + '</div>' +
          (who ? '<div class="gd-play-who">' + who + '</div>' : '') +
          (notes ? '<div class="gd-play-notes">' + notes + '</div>' : '') +
        '</div>' +
      '</div>' +
    '</div>';
  }).join('');
};

// The momentum curve: the home side's win probability after every play.
// Rendered here rather than in Jinja for the same reason as the feed --
// the poll repaints it mid-drive and one renderer cannot drift from
// itself. Above the centre line is the home team, below is the away
// team, and the distance from the line is how sure the game looks.
window.gdRenderMomentum = function(momentum, awayAbbr, homeAbbr){
  const el = document.getElementById('gdMomentum');
  if (!el) return;
  const pts = (momentum && momentum.points) || [];
  if (pts.length < 2) {
    el.innerHTML = '<div class="gd-empty">Momentum appears once the game kicks off.</div>';
    return;
  }
  const W = 300, H = 84, MID = H / 2;
  const step = W / (pts.length - 1);
  const xy = pts.map(function(p, i){
    const home = Math.max(0, Math.min(100, p.home));
    return [ +(i * step).toFixed(2), +(H - (home / 100) * H).toFixed(2) ];
  });
  const line = xy.map(function(p, i){ return (i ? 'L' : 'M') + p[0] + ' ' + p[1]; }).join(' ');
  const area = 'M0 ' + MID + ' ' + line.replace(/^M/, 'L') + ' L' + W + ' ' + MID + ' Z';
  const last = pts[pts.length - 1].home;
  const homePct = Math.round(last), awayPct = 100 - Math.round(last);
  const swing = momentum.swing;

  el.innerHTML =
    '<div class="gd-mom-head">' +
      '<span class="gd-mom-home">' + homeAbbr + ' ' + homePct + '%</span>' +
      '<span class="gd-mom-away">' + awayAbbr + ' ' + awayPct + '%</span>' +
    '</div>' +
    '<svg class="gd-mom-svg" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" ' +
         'aria-label="Win probability through the game">' +
      '<defs>' +
        '<clipPath id="gdMomUp"><rect x="0" y="0" width="' + W + '" height="' + MID + '"/></clipPath>' +
        '<clipPath id="gdMomDown"><rect x="0" y="' + MID + '" width="' + W + '" height="' + MID + '"/></clipPath>' +
      '</defs>' +
      '<path d="' + area + '" fill="var(--pos-rb)" opacity="0.55" clip-path="url(#gdMomUp)"/>' +
      '<path d="' + area + '" fill="var(--pos-wr)" opacity="0.55" clip-path="url(#gdMomDown)"/>' +
      '<line x1="0" y1="' + MID + '" x2="' + W + '" y2="' + MID + '" stroke="var(--line-strong)" stroke-width="1" vector-effect="non-scaling-stroke"/>' +
      '<path d="' + line + '" fill="none" stroke="var(--ink)" stroke-width="1.6" ' +
            'stroke-linejoin="round" vector-effect="non-scaling-stroke"/>' +
    '</svg>' +
    '<div class="gd-mom-axis"><span>Kickoff</span><span>' +
      (window.GD_STATUS === 'final' ? 'Final' : 'Now') + '</span></div>' +
    (swing
      ? '<div class="gd-mom-swing">Biggest swing <b>' + Math.abs(swing.delta).toFixed(1) +
        ' pts</b> to ' + (swing.delta > 0 ? homeAbbr : awayAbbr) + '</div>'
      : '');
};

// Where the ball sits on the field bar. Shared for the same reason.
window.gdRenderField = function(field, awayAbbr){
  const wrap = document.querySelector('.gd-field');
  if (!wrap || !field) return;
  const ball = wrap.querySelector('.gd-field-ball');
  const yl = field.yardline;
  if (ball && yl != null) {
    // yardLine is yards from the opponent's goal line, so which end of
    // the bar that maps to depends on who has the ball.
    const pct = field.possession === awayAbbr ? (100 - yl) : yl;
    ball.style.left = (9 + pct * 0.82) + '%';
    ball.style.display = '';
  } else if (ball) {
    ball.style.display = 'none';
  }
  const meta = wrap.querySelectorAll('.gd-field-meta span');
  if (meta.length >= 2) {
    meta[0].innerHTML = (field.possession ? '<b>' + field.possession + '</b> ball' : '') +
                        (field.possession_text ? ' &middot; ' + field.possession_text : '');
    meta[1].innerHTML = field.down_distance ? '<b>' + field.down_distance + '</b>' : '';
  }
};

(function(){
  // Paint the feed from the data baked into the page, using the same
  // renderer the poll uses.
  window.gdRenderFeed({{ detail.plays|tojson }}, window.GD_STATUS);
  window.gdRenderMomentum({{ detail.momentum|tojson }},
                          {{ detail.away.abbr|tojson }}, {{ detail.home.abbr|tojson }});
})();

// Tab + sub-tab switching. Deliberately its own IIFE, OUTSIDE the polling
// one below -- that returns early on a finished game, and a finished game
// still needs its box score tabs to work.
(function(){
  const tabs = document.getElementById('gdTabs');
  if (!tabs) return;
  tabs.addEventListener('click', function(e){
    const btn = e.target.closest('.gd-tab');
    if (!btn) return;
    const want = btn.dataset.panel;
    tabs.querySelectorAll('.gd-tab').forEach(function(b){ b.classList.toggle('on', b === btn); });
    document.querySelectorAll('.gd-panel').forEach(function(p){
      p.classList.toggle('on', p.dataset.panel === want);
    });
  });

  document.querySelectorAll('.gd-sub').forEach(function(sub){
    sub.addEventListener('click', function(e){
      const btn = e.target.closest('button');
      if (!btn) return;
      const side = sub.dataset.subFor;
      sub.querySelectorAll('button').forEach(function(b){ b.classList.toggle('on', b === btn); });
      document.querySelectorAll('[data-sub-panel^="' + side + '-"]').forEach(function(p){
        p.style.display = p.dataset.subPanel === side + '-' + btn.dataset.sub ? '' : 'none';
      });
    });
  });
})();

(function(){
  const initialStatus = {{ detail.status|tojson }};
  if (initialStatus === 'final') return;  // nothing left to poll for
  const eventId = {{ event_id|tojson }};
  const POLL_MS = 10000;
  let requestId = 0;

  // A page opened before kickoff has none of the live panels (win
  // probability, quarter-by-quarter, box score, "your players") rendered
  // yet -- they only exist once the server has real data to show. Rather
  // than duplicate that markup in JS, just reload once kickoff happens so
  // the server renders the real live page. This is what makes the page
  // "fully autonomous": leave it open through kickoff and it updates
  // itself with no manual refresh, here and for every stat below.
  function pollPregame(){
    fetch('/api/game-live?id=' + encodeURIComponent(eventId))
      .then(function(r){ return r.json(); })
      .then(function(data){
        if (data.status && data.status !== 'scheduled') { try { window.location.reload(); } catch (e) {} return; }
        setTimeout(pollPregame, POLL_MS);
      })
      .catch(function(){ setTimeout(pollPregame, POLL_MS); });
  }

  function pollLive(){
    const thisRequestId = ++requestId;
    fetch('/api/game-live?id=' + encodeURIComponent(eventId))
      .then(function(r){ return r.json(); })
      .then(function(data){
        if (thisRequestId !== requestId) return;
        document.getElementById('gdAwayScore').textContent = data.away_score ?? 0;
        document.getElementById('gdHomeScore').textContent = data.home_score ?? 0;
        const statusEl = document.getElementById('gdStatus');
        statusEl.textContent = data.status_detail || data.status;
        statusEl.className = 'gd-status ' + data.status;
        document.getElementById('gdClock').textContent = data.status === 'in_progress' ? (data.clock + ' · Q' + data.period) : '';

        // THE point of the poll: the feed and the field re-render every
        // cycle, through the same functions that painted them initially.
        // Without this the page updated its score while the play list sat
        // frozen at whatever was happening when the tab was opened.
        window.gdRenderFeed(data.plays, data.status);
        window.gdRenderField(data.field, {{ detail.away.abbr|tojson }});
        window.GD_STATUS = data.status;
        if (data.momentum) {
          window.gdRenderMomentum(data.momentum,
                                  {{ detail.away.abbr|tojson }}, {{ detail.home.abbr|tojson }});
        }

        // A game that just ended still needs one last paint (done above)
        // before the loop stops, so this check comes after the render.
        if (data.status === 'final') { return; }

        // Momentum grows by a point every play, so it is redrawn rather
        // than patched -- same renderer as the first paint.
        if (data.momentum) {
          window.gdRenderMomentum(data.momentum,
                                  {{ detail.away.abbr|tojson }}, {{ detail.home.abbr|tojson }});
        }

        // Quarter-by-quarter scores fill in as each quarter ends -- patch
        // the existing cells (same reasoning: the table only exists if
        // linescores were already present at initial load).
        const table = document.getElementById('gdLinescoreTable');
        if (table && data.away_linescores && data.home_linescores) {
          [['away', data.away_linescores, data.away_score], ['home', data.home_linescores, data.home_score]].forEach(function(entry){
            const row = table.querySelector('tr[data-side="' + entry[0] + '"]');
            if (!row) return;
            const cells = row.querySelectorAll('td.mono:not([data-final])');
            entry[1].forEach(function(v, i){ if (cells[i]) cells[i].textContent = v; });
            const finalCell = row.querySelector('td[data-final]');
            if (finalCell) finalCell.textContent = entry[2] ?? 0;
          });
        }

        if (data.status !== 'in_progress') {
          if (data.status === 'final') { try { window.location.reload(); } catch (e) {} }  // pick up the final box score/leaders
          return;
        }
        setTimeout(pollLive, POLL_MS);
      })
      .catch(function(){ setTimeout(pollLive, POLL_MS); });
  }

  setTimeout(initialStatus === 'scheduled' ? pollPregame : pollLive, POLL_MS);
})();
</script>
"""

MATCHUPS_HTML = BASE_STYLE + make_header("matchups") + """
<style>
  .mu-toolbar{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-top:6px; }
  .mu-week-nav{ display:flex; align-items:center; gap:8px; margin-left:auto; }
  .mu-search{ background:var(--paper-sunken); border:1px solid var(--line); color:var(--ink); border-radius:8px; padding:9px 12px; font-size:13.5px; width:180px; font-family:inherit; }
  .mu-row{ display:flex; align-items:center; gap:12px; padding:10px 4px; border-top:1px solid var(--line); }
  .mu-row:first-of-type{ border-top:none; }
  .mu-row img{ width:32px; height:32px; border-radius:50%; object-fit:cover; background:var(--paper-sunken); flex:none; }
  /* Tabular figures + a fixed width keep the rank column from shifting
     the rest of the row as the numbers grow from 1 to 300. */
  .mu-rank{ font-family:"IBM Plex Mono"; font-size:11.5px; color:var(--ink-muted); font-variant-numeric:tabular-nums;
            width:26px; flex:none; text-align:right; }
  .mu-divider{ margin-top:14px; padding:6px 4px; border-top:1px solid var(--line); font-size:11px;
               letter-spacing:0.04em; text-transform:uppercase; color:var(--ink-muted); }
  .mu-name-col{ flex:1; min-width:0; display:flex; flex-direction:column; gap:2px; }
  .mu-name-line{ font-weight:700; font-size:13.5px; }
  .mu-reason{ font-size:11.5px; color:var(--ink-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .mu-opp{ color:var(--ink-secondary); font-size:12.5px; width:70px; flex:none; }
  .mu-grade{ font-family:"IBM Plex Mono"; font-weight:700; font-size:12.5px; padding:3px 8px; border-radius:6px; flex:none; width:38px; text-align:center; }
  .mu-grade.ap, .mu-grade.a, .mu-grade.am,
  .mu-grade.bp, .mu-grade.b, .mu-grade.bm{ background:var(--good-wash); color:var(--good); }
  .mu-grade.cp, .mu-grade.c, .mu-grade.cm{ background:var(--warning-wash); color:var(--warning); }
  .mu-grade.dp, .mu-grade.d, .mu-grade.dm, .mu-grade.f{ background:var(--critical-wash); color:var(--critical); }
  .mu-stars{ font-size:12px; width:70px; flex:none; text-align:right; }
  .star-rating{ position:relative; display:inline-block; line-height:1; }
  .star-rating .star-bg{ color:var(--ink-muted); opacity:0.4; }
  .star-rating .star-fg{ position:absolute; top:0; left:0; overflow:hidden; white-space:nowrap; color:#f0b429; }

  .h2h-pickers{ display:grid; grid-template-columns:1fr auto 1fr; align-items:start; gap:14px; margin-top:14px; }
  .h2h-vs{ display:flex; align-items:center; justify-content:center; height:44px; font-family:"Big Shoulders Display"; font-weight:800; color:var(--ink-muted); }
  .h2h-picker{ position:relative; }
  .h2h-picker input{ width:100%; background:var(--paper-sunken); border:1px solid var(--line); color:var(--ink); border-radius:8px; padding:10px 12px; font-size:13.5px; font-family:inherit; }
  .h2h-dropdown{ display:none; position:absolute; top:calc(100% + 4px); left:0; right:0; background:var(--paper-raised); border:1px solid var(--line-strong); border-radius:10px; box-shadow:var(--shadow); z-index:20; max-height:260px; overflow-y:auto; }
  .h2h-dropdown.open{ display:block; }
  .h2h-dropdown-item{ display:flex; align-items:center; gap:10px; padding:9px 12px; cursor:pointer; }
  .h2h-dropdown-item:hover{ background:var(--paper-sunken); }
  .h2h-dropdown-item img{ width:26px; height:26px; border-radius:50%; object-fit:cover; }
  .h2h-selected{ display:none; align-items:center; gap:10px; margin-top:8px; padding:8px 10px; background:var(--paper-sunken); border-radius:8px; }
  .h2h-selected.shown{ display:flex; }
  .h2h-selected img{ width:28px; height:28px; border-radius:50%; object-fit:cover; }
  .h2h-selected .rm{ margin-left:auto; cursor:pointer; color:var(--ink-muted); font-weight:700; }
  .h2h-compare-btn{ width:100%; margin-top:16px; }
  .h2h-result{ margin-top:20px; display:none; }
  .h2h-result.shown{ display:block; }
  .h2h-verdict{ text-align:center; padding:14px; border-radius:10px; background:var(--good-wash); color:var(--good); font-weight:700; font-family:"Big Shoulders Display"; text-transform:uppercase; font-size:16px; }
  .h2h-cards{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }
  .h2h-card{ border:1px solid var(--line); border-radius:12px; padding:14px; }
  .h2h-card.winner{ border-color:var(--good); }
  .h2h-card-head{ display:flex; align-items:center; gap:10px; }
  .h2h-card-head img{ width:40px; height:40px; border-radius:50%; object-fit:cover; }
  .h2h-card-name{ font-weight:700; font-size:14px; }
  .h2h-stat-row{ display:flex; justify-content:space-between; font-size:12.5px; padding:6px 0; border-top:1px solid var(--line); }
  .h2h-reasons{ margin-top:14px; padding-left:18px; font-size:13px; color:var(--ink-secondary); }
  .h2h-reasons li{ margin-top:6px; }
  @media (max-width: 640px) {
    .h2h-pickers{ grid-template-columns:1fr; }
    .h2h-vs{ height:24px; }
    .h2h-cards{ grid-template-columns:1fr; }
  }
</style>
<main><div class="wrap">
  {% if current_user.is_authenticated %}
  <div class="panel">
    <p class="eyebrow">Head-to-Head</p>
    <h2>Start/Sit Calculator</h2>
    <p class="muted" style="margin-top:6px;">Pick two players and see who has the better week ahead, with the stats behind the call.</p>
    <div class="h2h-pickers">
      <div class="h2h-picker">
        <input type="text" id="h2hSearchA" placeholder="Search player A...">
        <div class="h2h-dropdown" id="h2hDropdownA"></div>
        <div class="h2h-selected" id="h2hSelectedA"><img src="" alt=""><span class="nm"></span><span class="rm" data-slot="A">&times;</span></div>
      </div>
      <div class="h2h-vs">VS</div>
      <div class="h2h-picker">
        <input type="text" id="h2hSearchB" placeholder="Search player B...">
        <div class="h2h-dropdown" id="h2hDropdownB"></div>
        <div class="h2h-selected" id="h2hSelectedB"><img src="" alt=""><span class="nm"></span><span class="rm" data-slot="B">&times;</span></div>
      </div>
    </div>
    <button type="button" class="btn h2h-compare-btn" id="h2hCompareBtn" disabled>Compare</button>
    <div class="error" id="h2hError" style="display:none;"></div>
    <div class="h2h-result" id="h2hResult"></div>
  </div>
  {% endif %}
  <div class="panel">
    <p class="eyebrow">Matchups</p>
    <h2>Who's worth starting this week</h2>
    {% if load_error %}<div class="error">Couldn't load matchup grades right now: {{ load_error }}</div>{% endif %}
    {% if current_user.is_authenticated %}
    <div class="mu-toolbar">
      <input type="text" class="mu-search" id="muSearch" placeholder="Search player...">
      <div class="format-toggle" id="muPosTabs">
        <a class="active" data-pos="all" href="#">All</a>
        <a data-pos="QB" href="#">QB</a>
        <a data-pos="RB" href="#">RB</a>
        <a data-pos="WR" href="#">WR</a>
        <a data-pos="TE" href="#">TE</a>
      </div>
      <div class="mu-week-nav">
        <a class="team-chip" href="/matchups?season={{ season }}&week={{ week-1 if week > 1 else week }}">&larr;</a>
        <span class="muted">Week {{ week }}</span>
        <a class="team-chip" href="/matchups?season={{ season }}&week={{ week+1 }}">&rarr;</a>
      </div>
    </div>
    <div id="muList" style="margin-top:14px;">
      {% for r in rows %}
      {% if r.starts_inactive %}
      <div class="mu-divider">No longer a start/sit call &mdash; already played, live, or on bye</div>
      {% endif %}
      <div class="mu-row" data-name="{{ r.name|lower }}" data-pos="{{ r.position }}">
        <span class="mu-rank">{{ r.rank }}</span>
        <img src="{{ r.photo }}" alt="" onerror="this.style.visibility='hidden'">
        <span class="pos-chip" style="background:var(--pos-{{ r.position.lower() }});">{{ r.position }}</span>
        <div class="mu-name-col">
          <span class="mu-name-line">{{ r.name }} <span class="muted">{{ r.team }}</span></span>
          <span class="mu-reason">{{ r.reasoning }}</span>
        </div>
        <span class="mu-opp">{% if r.opponent %}vs {{ r.opponent }}{% else %}BYE{% endif %}</span>
        <span class="mu-stars"><span class="star-rating"><span class="star-bg">★★★★★</span><span class="star-fg" style="width:{{ r.star_pct }}%;">★★★★★</span></span></span>
        <span class="mu-grade {{ r.grade_class }}">{{ r.grade }}</span>
      </div>
      {% endfor %}
      {% if not rows %}<p class="muted" style="padding:20px 0;">No graded players for this week yet.</p>{% endif %}
    </div>
    {% else %}
    <div class="gate-wrap">
      <div class="gate-blur">
        <div class="mu-row"><span class="mu-rank">1</span><img src=""><span class="pos-chip" style="background:var(--pos-qb);">QB</span><span class="mu-name">Sample Player DAL</span><span class="mu-opp">vs SF</span><span class="mu-stars"><span class="star-rating"><span class="star-bg">★★★★★</span><span class="star-fg" style="width:95%;">★★★★★</span></span></span><span class="mu-grade ap">A+</span></div>
        <div class="mu-row"><span class="mu-rank">2</span><img src=""><span class="pos-chip" style="background:var(--pos-rb);">RB</span><span class="mu-name">Sample Player KC</span><span class="mu-opp">vs BUF</span><span class="mu-stars"><span class="star-rating"><span class="star-bg">★★★★★</span><span class="star-fg" style="width:55%;">★★★★★</span></span></span><span class="mu-grade c">C</span></div>
        <div class="mu-row"><span class="mu-rank">3</span><img src=""><span class="pos-chip" style="background:var(--pos-wr);">WR</span><span class="mu-name">Sample Player MIA</span><span class="mu-opp">vs NYJ</span><span class="mu-stars"><span class="star-rating"><span class="star-bg">★★★★★</span><span class="star-fg" style="width:15%;">★★★★★</span></span></span><span class="mu-grade dm">D-</span></div>
      </div>
      <div class="gate-card">
        <h3>Unlock <span style="color:var(--accent-ink);">Matchup Grades</span></h3>
        <p>Create a free account to see every player's start/sit grade, based on their opponent's defense, recent trend, and injury status.</p>
        <div class="gate-benefits">
          <span>A-F grade for every startable player, every week</span>
          <span>Opponent defense strength built in automatically</span>
          <span>Updates as injury reports and matchups change</span>
        </div>
        <a href="/signup" class="btn" style="margin-top:22px; width:100%;">Create Account</a>
      </div>
    </div>
    {% endif %}
  </div>
</div></main>
<script>
(function(){
  const search = document.getElementById('muSearch');
  const tabs = document.getElementById('muPosTabs');
  if (!search || !tabs) return;
  let activePos = 'all';
  const rows = Array.from(document.querySelectorAll('#muList .mu-row'));

  function render(){
    const q = search.value.trim().toLowerCase();
    rows.forEach(function(row){
      const matchesPos = activePos === 'all' || row.dataset.pos === activePos;
      const matchesSearch = !q || row.dataset.name.includes(q);
      row.style.display = (matchesPos && matchesSearch) ? '' : 'none';
    });
  }

  search.addEventListener('input', render);
  tabs.querySelectorAll('a').forEach(function(a){
    a.addEventListener('click', function(e){
      e.preventDefault();
      activePos = a.dataset.pos;
      tabs.querySelectorAll('a').forEach(function(x){ x.classList.remove('active'); });
      a.classList.add('active');
      render();
    });
  });
})();

(function(){
  const picked = {A: null, B: null};
  const compareBtn = document.getElementById('h2hCompareBtn');
  const errorEl = document.getElementById('h2hError');
  const resultEl = document.getElementById('h2hResult');
  if (!compareBtn) return;  // guest view has no h2h panel at all

  function wirePicker(slot){
    const input = document.getElementById('h2hSearch' + slot);
    const dropdown = document.getElementById('h2hDropdown' + slot);
    const selectedBox = document.getElementById('h2hSelected' + slot);
    let debounceTimer;

    input.addEventListener('input', function(){
      clearTimeout(debounceTimer);
      const q = input.value.trim();
      if (!q){ dropdown.classList.remove('open'); return; }
      debounceTimer = setTimeout(function(){
        fetch('/api/player-search?q=' + encodeURIComponent(q))
          .then(function(r){ return r.json(); })
          .then(function(data){
            dropdown.innerHTML = '';
            (data.results || []).filter(function(r){ return r.position !== 'PICK'; }).forEach(function(r){
              const item = document.createElement('div');
              item.className = 'h2h-dropdown-item';
              item.innerHTML = '<img src="' + r.photo + '" onerror="this.style.visibility=\\'hidden\\'"><span>' + r.name + ' <span class="muted">' + r.position + (r.team ? ' &middot; ' + r.team : '') + '</span></span>';
              item.addEventListener('click', function(){
                picked[slot] = r;
                selectedBox.querySelector('img').src = r.photo;
                selectedBox.querySelector('.nm').textContent = r.name + ' (' + r.position + (r.team ? ' ' + r.team : '') + ')';
                selectedBox.classList.add('shown');
                input.value = '';
                dropdown.classList.remove('open');
                updateCompareState();
              });
              dropdown.appendChild(item);
            });
            dropdown.classList.toggle('open', dropdown.children.length > 0);
          });
      }, 200);
    });

    selectedBox.querySelector('.rm').addEventListener('click', function(){
      picked[slot] = null;
      selectedBox.classList.remove('shown');
      updateCompareState();
    });

    document.addEventListener('click', function(e){
      if (!input.contains(e.target) && !dropdown.contains(e.target)) dropdown.classList.remove('open');
    });
  }

  function updateCompareState(){
    compareBtn.disabled = !(picked.A && picked.B);
  }

  function starRatingHtml(pct){
    return '<span class="star-rating"><span class="star-bg">★★★★★</span><span class="star-fg" style="width:' + pct + '%;">★★★★★</span></span>';
  }

  function renderCard(p, isWinner){
    // One row for whichever defensive figure actually fed this player's
    // grade (this year's real sample, a real prior-year read on this
    // exact opponent, or -- only once no real opponent-specific data
    // exists in any season checked -- a league-wide average) -- names
    // the opponent, the position, and the exact season the number comes
    // from, instead of two separate this-year/last-year rows where one
    // was often just a blank "-" early in the season. A league-average
    // number is labeled as such rather than attributed to the specific
    // opponent, since it isn't their real number.
    const defLabel = p.def_source === 'league_average'
      ? 'League avg vs ' + p.position + ' (' + p.def_season_used + ')'
      : p.opponent + ' vs ' + p.position + ' in ' + p.def_season_used + (p.def_source === 'current_thin' ? ' (early sample)' : '');
    const defRow = (p.def_rank_most_pts_used != null && p.def_fpts_allowed_pg_used != null)
      // A rank is meaningless next to a league AVERAGE -- it IS the
      // middle of the pack by definition, so printing "#N most points
      // allowed" there states a ranking the number doesn't have.
      ? '<div class="h2h-stat-row"><span class="muted">' + defLabel + '</span><span>' + p.def_fpts_allowed_pg_used + ' pts/gm' + (p.def_source === 'league_average' ? '' : ' (#' + p.def_rank_most_pts_used + ' most points allowed)') + '</span></div>'
      : '<div class="h2h-stat-row"><span class="muted">Defense vs ' + p.position + '</span><span>Not enough data yet</span></div>';
    // A game that's already final (or live) makes a "start/sit" call moot
    // -- call that out plainly instead of only leaving it to the prose
    // reasoning line above, since it's the single most decision-relevant
    // fact when it applies.
    let resultRow = '';
    if (p.game_status === 'final') {
      resultRow = '<div class="h2h-stat-row" style="color:var(--good);"><span class="muted">Result</span><span>Final' + (p.actual_week_pts != null ? ' -- ' + p.actual_week_pts.toFixed(1) + ' pts' : '') + '</span></div>';
    } else if (p.game_status === 'in_progress') {
      resultRow = '<div class="h2h-stat-row" style="color:var(--warning);"><span class="muted">Result</span><span>Live now</span></div>';
    }
    return '<div class="h2h-card' + (isWinner ? ' winner' : '') + '">' +
      '<div class="h2h-card-head"><img src="' + p.photo + '" onerror="this.style.visibility=\\'hidden\\'">' +
        '<div><div class="h2h-card-name">' + p.name + '</div><span class="muted">' + p.position + ' &middot; ' + p.team + '</span></div>' +
        '<span class="mu-grade ' + p.grade_class + '" style="margin-left:auto;">' + p.grade + '</span></div>' +
      '<div style="text-align:center; margin-top:8px;">' + starRatingHtml(p.star_pct) + '</div>' +
      '<p class="muted" style="text-align:center; font-size:12px; margin-top:6px;">' + p.reasoning + '</p>' +
      resultRow +
      '<div class="h2h-stat-row"><span class="muted">Opponent</span><span>' + (p.opponent ? 'vs ' + p.opponent : 'BYE') + '</span></div>' +
      defRow +
      '<div class="h2h-stat-row"><span class="muted">Season avg</span><span>' + p.season_avg + ' pts</span></div>' +
      '<div class="h2h-stat-row"><span class="muted">Last ' + (p.recent_games || 5) + ' games avg' + (p.recent_crossed_season ? ' (incl. last season)' : '') + '</span><span>' + p.recent_avg + ' pts</span></div>' +
      '<div class="h2h-stat-row"><span class="muted">Injury</span><span>' + p.injury + '</span></div>' +
    '</div>';
  }

  compareBtn.addEventListener('click', function(){
    errorEl.style.display = 'none';
    resultEl.classList.remove('shown');
    compareBtn.disabled = true;
    compareBtn.textContent = 'Comparing...';
    fetch('/api/matchup-compare?a=' + encodeURIComponent(picked.A.sid) + '&b=' + encodeURIComponent(picked.B.sid))
      .then(function(r){ return r.json(); })
      .then(function(data){
        compareBtn.disabled = false;
        compareBtn.textContent = 'Compare';
        if (!data.ok){
          errorEl.textContent = data.error || 'Could not compare these players.';
          errorEl.style.display = 'block';
          return;
        }
        const res = data.result;
        const startPlayer = res.a.sid === res.start_sid ? res.a : res.b;
        const sitPlayer = res.a.sid === res.start_sid ? res.b : res.a;
        resultEl.innerHTML =
          '<div class="h2h-verdict">Start ' + startPlayer.name + ' over ' + sitPlayer.name + '</div>' +
          '<div class="h2h-cards">' + renderCard(startPlayer, true) + renderCard(sitPlayer, false) + '</div>' +
          '<ul class="h2h-reasons">' + res.reasons.map(function(r){ return '<li>' + r + '</li>'; }).join('') + '</ul>';
        resultEl.classList.add('shown');
      })
      .catch(function(){
        compareBtn.disabled = false;
        compareBtn.textContent = 'Compare';
        errorEl.textContent = 'Something went wrong comparing these players.';
        errorEl.style.display = 'block';
      });
  });

  wirePicker('A');
  wirePicker('B');
})();
</script>
"""

RANKINGS_HTML = BASE_STYLE + make_header("rankings") + VOTE_MODAL_HTML + """
<style>
  .rk-page{
    --rk-bg:#0d0f0d; --rk-surface:#151815; --rk-surface2:#1c201c;
    --rk-line:rgba(255,255,255,0.08); --rk-text:#e8e6df; --rk-muted:#8b9089;
    --rk-good:#1fae5a; --rk-good-wash:rgba(31,174,90,0.16);
    --rk-warn:#d1a521; --rk-warn-wash:rgba(209,165,33,0.16);
    --rk-bad:#e2534a; --rk-bad-wash:rgba(226,83,74,0.16);
    background:var(--rk-bg); color:var(--rk-text); margin:0 -24px; padding:0 24px 60px;
    font-family:"Source Sans 3",system-ui,sans-serif;
  }
  .rk-toolbar{ position:sticky; top:64px; z-index:40; background:color-mix(in srgb, var(--rk-bg) 92%, transparent); backdrop-filter:blur(8px); border-bottom:1px solid var(--rk-line); padding:16px 0; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  .rk-title{ font-family:"Big Shoulders Display"; font-size:22px; font-weight:800; text-transform:uppercase; margin-right:auto; color:var(--rk-text); }
  /* A styled select loses the platform's own arrow, and a control with
     no arrow does not read as a control. Drawn back on explicitly. */
  .rk-select{ background-color:var(--rk-surface); border:1px solid var(--rk-line);
              color:var(--rk-text); border-radius:8px; padding:9px 12px; font-size:13.5px;
              font-weight:600; font-family:inherit;
              appearance:none; -webkit-appearance:none;
             background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 8'%3E%3Cpath d='M1 1.5 6 6.5 11 1.5' stroke='%238b9089' stroke-width='1.8' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
             background-repeat:no-repeat; background-position:right 11px center;
             background-size:11px 7px; padding-right:30px; cursor:pointer; }
  .rk-icon-btn{ width:36px; height:36px; border-radius:8px; background:var(--rk-surface); border:1px solid var(--rk-line); color:var(--rk-muted); display:flex; align-items:center; justify-content:center; cursor:pointer; font-size:15px; }
  .rk-icon-btn.active{ color:var(--rk-text); border-color:var(--accent); }
  .rk-rookie-toggle{ font-size:12px; font-weight:700; padding:0 13px; height:36px; border-radius:99px; border:1px solid var(--rk-line); background:var(--rk-surface); color:var(--rk-muted); cursor:pointer; display:flex; align-items:center; gap:6px; user-select:none; }
  .rk-rookie-toggle svg{ flex:none; }
  .rk-rookie-toggle.active{ background:#f0b429; color:#1a1206; border-color:#f0b429; }
  .rk-search{ background:var(--rk-surface); border:1px solid var(--rk-line); color:var(--rk-text); border-radius:8px; padding:9px 12px; font-size:13.5px; width:180px; font-family:inherit; }

  .rk-tier-bar{ display:flex; align-items:center; gap:10px; padding:8px 14px; margin-top:18px; border-radius:8px; font-family:"Big Shoulders Display"; font-weight:800; font-size:15px; letter-spacing:0.03em; }
  .rk-tier-bar.tier-S{ background:rgba(226,83,74,0.22); color:#ff8a80; }
  .rk-tier-bar.tier-A{ background:rgba(217,131,45,0.22); color:#ffb066; }
  .rk-tier-bar.tier-B{ background:rgba(209,165,33,0.22); color:#f0d060; }
  .rk-tier-bar.tier-C{ background:rgba(220,220,120,0.16); color:#e6e69a; }
  .rk-tier-bar.tier-D{ background:rgba(31,174,90,0.18); color:#7fe0a8; }
  .rk-tier-bar.tier-F{ background:rgba(140,145,138,0.18); color:#9aa199; }

  table.rk-table{ width:100%; border-collapse:collapse; margin-top:6px; font-size:13.5px; }
  table.rk-table th{ text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:0.06em; color:var(--rk-muted); padding:8px 10px; border-bottom:1px solid var(--rk-line); cursor:pointer; user-select:none; white-space:nowrap; }
  table.rk-table th:hover{ color:var(--rk-text); }
  table.rk-table th .arrow{ font-size:9px; opacity:0.6; margin-left:3px; }
  table.rk-table td{ padding:9px 10px; border-bottom:1px solid var(--rk-line); vertical-align:middle; }
  table.rk-table tr.rk-row:hover{ background:var(--rk-surface); cursor:pointer; }
  table.rk-table img{ width:28px; height:28px; border-radius:50%; object-fit:cover; background:var(--rk-surface2); }
  .rk-pname{ display:flex; align-items:center; gap:9px; color:var(--rk-text); text-decoration:none; font-weight:700; }
  .rookie-badge{ flex:none; vertical-align:middle; margin-left:5px; }
  .rk-pname:hover{ color:var(--accent); }
  .rk-tm{ color:var(--rk-muted); font-size:12px; font-weight:600; }
  .rk-stat{ font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; padding:3px 8px; border-radius:6px; display:inline-block; min-width:34px; text-align:center; }
  .rk-stat.good{ background:var(--rk-good-wash); color:var(--rk-good); }
  .rk-stat.warn{ background:var(--rk-warn-wash); color:var(--rk-warn); }
  .rk-stat.bad{ background:var(--rk-bad-wash); color:var(--rk-bad); }
  .rk-stat.flat{ color:var(--rk-muted); background:transparent; }

  .rk-grid{ display:grid; grid-template-columns:repeat(auto-fill,minmax(130px,1fr)); gap:10px; margin-top:14px; }
  .rk-card{ background:var(--rk-surface); border-radius:10px; overflow:hidden; position:relative; cursor:pointer; border:1px solid var(--rk-line); }
  .rk-card:hover{ background:var(--rk-surface2); }
  .rk-card .rk-card-photo{ width:100%; aspect-ratio:1; object-fit:cover; border-bottom:3px solid; background:var(--rk-surface2); }
  .rk-card .rk-card-rank{ position:absolute; top:6px; left:6px; background:rgba(0,0,0,0.6); color:#fff; font-family:"IBM Plex Mono"; font-size:11px; font-weight:700; padding:2px 6px; border-radius:5px; }
  .rk-card .rk-card-stats{ position:absolute; top:6px; right:6px; display:flex; flex-direction:column; gap:3px; align-items:flex-end; }
  .rk-card .rk-card-stats span{ font-family:"IBM Plex Mono"; font-size:10px; font-weight:700; padding:1px 5px; border-radius:4px; }
  .rk-card .rk-card-band{ padding:6px 8px; }
  .rk-card .rk-card-name{ font-weight:800; font-size:12.5px; color:var(--rk-text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .rk-card .rk-card-tm{ font-size:10.5px; color:var(--rk-muted); font-weight:600; }

  .rk-modal-overlay{ position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:90; display:none; align-items:center; justify-content:center; }
  .rk-modal-overlay.open{ display:flex; }
  .rk-modal{ background:var(--rk-surface); border:1px solid var(--rk-line); border-radius:14px; padding:24px; width:320px; }
  .rk-modal h3{ font-family:"Big Shoulders Display"; font-size:18px; margin-bottom:4px; }
  .rk-slider-row{ margin-top:16px; }
  .rk-slider-row label{ font-size:12px; color:var(--rk-muted); display:flex; justify-content:space-between; }
  .rk-slider-row input[type=range]{ width:100%; margin-top:6px; accent-color:var(--accent); }
  .rk-modal-actions{ display:flex; gap:8px; margin-top:20px; }
  .rk-modal-actions button{ flex:1; padding:10px; border-radius:8px; border:1px solid var(--rk-line); background:transparent; color:var(--rk-text); font-weight:700; cursor:pointer; font-family:inherit; }
  .rk-modal-actions button.primary{ background:var(--accent); border-color:var(--accent); color:var(--accent-on); }
  .rk-empty{ color:var(--rk-muted); padding:30px; text-align:center; }

  @media (max-width: 760px) {
    .rk-toolbar{ flex-wrap:wrap; }
    .rk-title{ width:100%; }
    .rk-search{ width:120px; flex:1; }
    .col-snap_pct, .col-games, .col-fpts_per_game, .col-position_rank, .col-fpts{ display:none; }
    .rk-grid{ grid-template-columns:repeat(auto-fill,minmax(105px,1fr)); }
    .gate-card{ padding:26px 20px; width:92%; }
  }
</style>

<div class="rk-page">
<div class="wrap">
  <div class="rk-toolbar">
    <span class="rk-title">Rankings <span style="font-size:12px; color:var(--rk-muted); text-transform:none; font-family:'Source Sans 3';">&middot; GP/FPTS from {{ stats_season }}</span></span>
    <!-- Mode and format were two pairs of pills sitting beside a
         dropdown that did the same job, which is four bubbles and a
         select competing for the same glance. All three are selects
         now, so the toolbar reads as one row of choices. -->
    <select class="rk-select" id="modeSelect" aria-label="Mode">
      <option value="dynasty" {{ 'selected' if mode == 'dynasty' }}>Dynasty</option>
      <option value="redraft" {{ 'selected' if mode == 'redraft' }}>Redraft</option>
    </select>
    <select class="rk-select" id="posSelect">
      <option value="overall">Overall</option>
      <option value="QB">QB</option>
      <option value="RB">RB</option>
      <option value="WR">WR</option>
      <option value="TE">TE</option>
    </select>
    <select class="rk-select" id="fmtSelect" aria-label="Format">
      <option value="1qb" {{ 'selected' if fmt == '1qb' }}>1QB</option>
      <option value="superflex" {{ 'selected' if fmt == 'superflex' }}>Superflex</option>
    </select>
    <div class="rk-rookie-toggle" id="rookieToggle" title="Show only rookies">
      <svg viewBox="0 0 24 24" width="12" height="12"><path d="M12 1.5l2.98 6.63 7.27.7-5.5 4.83 1.63 7.13L12 17.06l-6.38 3.73 1.63-7.13-5.5-4.83 7.27-.7z" fill="currentColor"/></svg>
      Rookies
    </div>
    <input class="rk-search" id="rkSearch" type="text" placeholder="Search player...">
    <div class="rk-icon-btn" id="viewList" title="List view">&#9776;</div>
    <div class="rk-icon-btn" id="viewGrid" title="Grid view">&#9638;</div>
    <div class="rk-icon-btn" id="openFilters" title="Filters">&#9881;</div>
  </div>

  <div id="rkListWrap">
    <table class="rk-table" id="rkTable">
      <thead>
        <tr id="rkHeaderRow"></tr>
      </thead>
      <tbody id="rkBody"></tbody>
    </table>
    <div class="gate-wrap" id="rkGateWrap" style="display:none;">
      <div class="gate-blur">
        <table class="rk-table"><tbody id="rkGatedBody"></tbody></table>
      </div>
      <div class="gate-card">
        <h3>Unlock the Full <span style="color:var(--accent-ink);">Rankings</span></h3>
        <p>Create a free account to see every player, not just the top tier.</p>
        <div class="gate-benefits">
          <span>Every player ranked, not just the top tier</span>
          <span>Save your leagues, no re-searching</span>
          <span>Vote on community Start/Bench/Cut rankings</span>
          <span>Unlimited trade calculator access</span>
        </div>
        <a href="/signup" class="btn" style="margin-top:22px; width:100%;">Create Account</a>
      </div>
    </div>
  </div>
  <div class="rk-grid" id="rkGrid" style="display:none;"></div>
  <p class="rk-empty" id="rkEmpty" style="display:none;">No players match your filters.</p>
</div>
</div>

<div class="rk-modal-overlay" id="filterModal">
  <div class="rk-modal">
    <h3>Filters</h3>
    <div class="rk-slider-row">
      <label><span>Min Snap%</span><span id="snapVal">0</span></label>
      <input type="range" id="snapSlider" min="0" max="100" value="0">
    </div>
    <div class="rk-slider-row">
      <label><span>Min Games Played</span><span id="gamesVal">0</span></label>
      <input type="range" id="gamesSlider" min="0" max="18" value="0">
    </div>
    <div class="rk-slider-row">
      <label><span>Min Value</span><span id="valueVal">0</span></label>
      <input type="range" id="valueSlider" min="0" max="12000" value="0" step="100">
    </div>
    <div class="rk-modal-actions">
      <button id="resetFilters">Reset</button>
      <button class="primary" id="applyFilters">Apply</button>
    </div>
  </div>
</div>

<script>
const RK_DATA = [
  {% for r in rows %}
  {sid:{{ r.sid|tojson }}, photo:{{ r.photo|tojson }}, name:{{ r.name|tojson }}, position:{{ r.position|tojson }},
   is_rookie:{{ r.is_rookie|tojson }},
   team:{{ r.team|tojson }}, age:{{ r.age|tojson }}, games:{{ r.games|tojson }}, fpts:{{ r.fpts|tojson }},
   fpts_per_game:{{ r.fpts_per_game|tojson }}, snap_pct:{{ r.snap_pct|tojson }}, position_rank:{{ r.position_rank|tojson }}, value:{{ r.value|tojson }},
   overall_rank:{{ r.overall_rank|tojson }}, tier:{{ r.tier|tojson }}},
  {% endfor %}
];
const RK_FMT = {{ fmt|tojson }};
const RK_MODE = {{ mode|tojson }};
const RK_AUTHED = {{ current_user.is_authenticated | tojson }};
const posColors = {QB:'#1baf7a', RB:'#2a78d6', WR:'#e0397a', TE:'#7b5ce0'};
// Multi-point star with "R" for a rookie (years_exp === 0 in Sleeper's
// own data) -- inline SVG so it scales crisply at any size instead of
// relying on a font glyph.
const ROOKIE_BADGE = '<svg class="rookie-badge" viewBox="0 0 24 24" width="15" height="15" title="Rookie" aria-label="Rookie">' +
  '<path d="M12 1.5l2.98 6.63 7.27.7-5.5 4.83 1.63 7.13L12 17.06l-6.38 3.73 1.63-7.13-5.5-4.83 7.27-.7z" fill="#f0b429"/>' +
  '<text x="12" y="13.5" text-anchor="middle" dominant-baseline="central" font-size="8.5" font-weight="800" fill="#1a1206" font-family="IBM Plex Mono, monospace">R</text>' +
  '</svg>';

let state = {
  pos: {{ pos_filter|tojson }},
  view: {{ view|tojson }},
  search: '',
  sortKey: 'overall_rank',
  sortDir: 1,
  minSnap: 0, minGames: 0, minValue: 0,
  rookiesOnly: false,
};

function playerUrl(sid) {
  const numqbs = RK_FMT === 'superflex' ? 2 : 1;
  const ref = encodeURIComponent('/rankings?format=' + RK_FMT + '&mode=' + RK_MODE + '&pos=' + state.pos + '&view=' + state.view);
  return `/player?sid=${sid}&numqbs=${numqbs}&ref=${ref}`;
}

function percentileClass(values, val, higherIsBetter) {
  if (val === null || val === undefined || values.length < 3) return 'flat';
  const sorted = [...values].filter(v => v !== null && v !== undefined).sort((a,b) => a-b);
  const idx = sorted.indexOf(val);
  const pct = idx / Math.max(sorted.length - 1, 1);
  const good = higherIsBetter ? pct >= 0.66 : pct <= 0.33;
  const bad = higherIsBetter ? pct <= 0.33 : pct >= 0.66;
  if (good) return 'good';
  if (bad) return 'bad';
  return 'warn';
}

function getFiltered() {
  let rows = RK_DATA.filter(r => {
    if (state.pos !== 'overall' && r.position !== state.pos) return false;
    if (state.rookiesOnly && !r.is_rookie) return false;
    if (state.search && !r.name.toLowerCase().includes(state.search.toLowerCase())) return false;
    if (r.snap_pct !== null && r.snap_pct < state.minSnap) return false;
    if (r.games < state.minGames) return false;
    if (r.value < state.minValue) return false;
    return true;
  });
  rows.sort((a, b) => {
    const av = a[state.sortKey], bv = b[state.sortKey];
    if (av === null) return 1;
    if (bv === null) return -1;
    return (av - bv) * state.sortDir;
  });
  return rows;
}

const OVERALL_COLS = [
  {key:'overall_rank', label:'#'}, {key:'name', label:'Player'}, {key:'position', label:'Pos'},
  {key:'team', label:'TM'}, {key:'snap_pct', label:'Snap%'}, {key:'games', label:'GP'},
  {key:'fpts_per_game', label:'FPTS/G'}, {key:'position_rank', label:'Pos Rank'}, {key:'overall_rank', label:'Ovr Rank'},
];
const POSITION_COLS = [
  {key:'overall_rank', label:'#'}, {key:'name', label:'Player'}, {key:'snap_pct', label:'Snap%'},
  {key:'games', label:'GP'}, {key:'fpts', label:'FPTS'}, {key:'fpts_per_game', label:'FPTS/G'},
  {key:'overall_rank', label:'Ovr Rank'},
];

function renderHeader() {
  const cols = state.pos === 'overall' ? OVERALL_COLS : POSITION_COLS;
  const headerRow = document.getElementById('rkHeaderRow');
  headerRow.innerHTML = '';
  cols.forEach(c => {
    const th = document.createElement('th');
    th.className = 'col-' + c.key;
    const arrow = state.sortKey === c.key ? (state.sortDir === 1 ? '&#9650;' : '&#9660;') : '';
    th.innerHTML = c.label + ` <span class="arrow">${arrow}</span>`;
    th.onclick = () => {
      if (state.sortKey === c.key) state.sortDir *= -1;
      else { state.sortKey = c.key; state.sortDir = 1; }
      render();
    };
    headerRow.appendChild(th);
  });
}

function statCell(val, cls, suffix) {
  if (val === null || val === undefined) return '<span class="rk-stat flat">&mdash;</span>';
  return `<span class="rk-stat ${cls}">${val}${suffix || ''}</span>`;
}

function buildRowEl(r, cols, valArrays) {
  const { snapVals, gamesVals, fpgVals, fptsVals, posRankVals } = valArrays;
  const tr = document.createElement('tr');
  tr.className = 'rk-row';
  tr.onclick = () => window.location.href = playerUrl(r.sid);
  let cells = '';
  cols.forEach(c => {
    if (c.key === 'name') {
      cells += `<td class="col-name"><span class="rk-pname"><img src="${r.photo}" onerror="this.style.visibility='hidden'">${r.name}${r.is_rookie ? ROOKIE_BADGE : ''}</span></td>`;
    } else if (c.key === 'position' && state.pos === 'overall') {
      const rankSuffix = r.position_rank ? r.position_rank : '';
      cells += `<td class="col-position"><span class="rk-stat" style="background:${posColors[r.position]}22; color:${posColors[r.position]};">${r.position}${rankSuffix}</span></td>`;
    } else if (c.key === 'team') {
      cells += `<td class="rk-tm col-team">${r.team}</td>`;
    } else if (c.key === 'snap_pct') {
      cells += `<td class="col-snap_pct">${statCell(r.snap_pct !== null ? r.snap_pct + '%' : null, percentileClass(snapVals, r.snap_pct, true))}</td>`;
    } else if (c.key === 'games') {
      cells += `<td class="col-games">${statCell(r.games, percentileClass(gamesVals, r.games, true))}</td>`;
    } else if (c.key === 'fpts') {
      cells += `<td class="col-fpts">${statCell(r.fpts, percentileClass(fptsVals, r.fpts, true))}</td>`;
    } else if (c.key === 'fpts_per_game') {
      cells += `<td class="col-fpts_per_game">${statCell(r.fpts_per_game, percentileClass(fpgVals, r.fpts_per_game, true))}</td>`;
    } else if (c.key === 'position_rank') {
      cells += `<td class="col-position_rank">${statCell(r.position_rank, percentileClass(posRankVals, r.position_rank, false))}</td>`;
    } else if (c.key === 'overall_rank') {
      cells += `<td class="col-overall_rank">${statCell(r.overall_rank, 'flat')}</td>`;
    }
  });
  tr.innerHTML = cells;
  return tr;
}

function appendWithTierDividers(target, rows, cols, valArrays, startTier, showDividers) {
  let lastTier = startTier;
  rows.forEach(r => {
    if (showDividers && r.tier !== lastTier) {
      lastTier = r.tier;
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = cols.length;
      td.innerHTML = `<div class="rk-tier-bar tier-${r.tier}">TIER ${r.tier}</div>`;
      tr.appendChild(td);
      target.appendChild(tr);
    }
    target.appendChild(buildRowEl(r, cols, valArrays));
  });
}

function renderList(rows) {
  const cols = state.pos === 'overall' ? OVERALL_COLS : POSITION_COLS;
  const body = document.getElementById('rkBody');
  const gatedBody = document.getElementById('rkGatedBody');
  const gateWrap = document.getElementById('rkGateWrap');
  body.innerHTML = '';
  gatedBody.innerHTML = '';
  const valArrays = {
    snapVals: rows.map(r => r.snap_pct), gamesVals: rows.map(r => r.games),
    fpgVals: rows.map(r => r.fpts_per_game), fptsVals: rows.map(r => r.fpts),
    posRankVals: rows.map(r => r.position_rank),
  };

  const showTiers = state.sortKey === 'overall_rank' && state.sortDir === 1 && state.pos === 'overall';

  if (!RK_AUTHED && showTiers) {
    const visible = rows.filter(r => r.tier === 'S');
    const gated = rows.filter(r => r.tier !== 'S');
    appendWithTierDividers(body, visible, cols, valArrays, null, true);
    if (gated.length) {
      appendWithTierDividers(gatedBody, gated, cols, valArrays, 'S', true);
      gateWrap.style.display = 'block';
    } else {
      gateWrap.style.display = 'none';
    }
  } else {
    appendWithTierDividers(body, rows, cols, valArrays, null, showTiers);
    gateWrap.style.display = 'none';
  }
}

function renderGrid(rows) {
  const grid = document.getElementById('rkGrid');
  grid.innerHTML = '';
  const valueVals = rows.map(r => r.value);
  const fpgVals = rows.map(r => r.fpts_per_game);
  rows.forEach(r => {
    const card = document.createElement('div');
    card.className = 'rk-card';
    card.onclick = () => window.location.href = playerUrl(r.sid);
    const color = posColors[r.position] || '#888';
    card.innerHTML = `
      <img class="rk-card-photo" src="${r.photo}" style="border-color:${color};" onerror="this.style.visibility='hidden'">
      <div class="rk-card-rank">#${r.overall_rank}</div>
      <div class="rk-card-stats">
        ${statCell(r.value, percentileClass(valueVals, r.value, true))}
        ${statCell(r.fpts_per_game, percentileClass(fpgVals, r.fpts_per_game, true))}
      </div>
      <div class="rk-card-band">
        <div class="rk-card-name">${r.name}${r.is_rookie ? ROOKIE_BADGE : ''}</div>
        <div class="rk-card-tm">${r.position} &middot; ${r.team}</div>
      </div>`;
    grid.appendChild(card);
  });
}

function updateUrl() {
  const url = new URL(window.location.href);
  url.searchParams.set('pos', state.pos);
  url.searchParams.set('view', state.view);
  window.history.replaceState({}, '', url);
}

function render() {
  const rows = getFiltered();
  document.getElementById('rkEmpty').style.display = rows.length ? 'none' : 'block';
  renderHeader();
  if (state.view === 'grid') {
    document.getElementById('rkListWrap').style.display = 'none';
    document.getElementById('rkGrid').style.display = rows.length ? 'grid' : 'none';
    renderGrid(rows);
  } else {
    document.getElementById('rkGrid').style.display = 'none';
    document.getElementById('rkListWrap').style.display = rows.length ? 'block' : 'none';
    renderList(rows);
  }
  document.getElementById('viewList').classList.toggle('active', state.view === 'list');
  document.getElementById('viewGrid').classList.toggle('active', state.view === 'grid');
  updateUrl();
}

document.getElementById('posSelect').value = state.pos;
document.getElementById('posSelect').addEventListener('change', e => { state.pos = e.target.value; render(); });

// Mode and format come from the server (they change which value set is
// loaded), so these navigate rather than re-render, carrying the rest of
// the toolbar's state across with them.
['modeSelect', 'fmtSelect'].forEach(function(id){
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('change', function(e){
    const url = new URL(window.location.href);
    url.searchParams.set(id === 'modeSelect' ? 'mode' : 'format', e.target.value);
    url.searchParams.set('pos', state.pos);
    url.searchParams.set('view', state.view);
    window.location.href = url.toString();
  });
});
document.getElementById('viewList').addEventListener('click', () => { state.view = 'list'; render(); });
document.getElementById('viewGrid').addEventListener('click', () => { state.view = 'grid'; render(); });
document.getElementById('rookieToggle').addEventListener('click', () => {
  state.rookiesOnly = !state.rookiesOnly;
  document.getElementById('rookieToggle').classList.toggle('active', state.rookiesOnly);
  render();
});
document.getElementById('rkSearch').addEventListener('input', e => { state.search = e.target.value; render(); });

const filterModal = document.getElementById('filterModal');
document.getElementById('openFilters').addEventListener('click', () => filterModal.classList.add('open'));
filterModal.addEventListener('click', e => { if (e.target === filterModal) filterModal.classList.remove('open'); });

['snap','games','value'].forEach(key => {
  const slider = document.getElementById(key + 'Slider');
  const label = document.getElementById(key + 'Val');
  slider.addEventListener('input', () => { label.textContent = slider.value; });
});
document.getElementById('applyFilters').addEventListener('click', () => {
  state.minSnap = parseInt(document.getElementById('snapSlider').value);
  state.minGames = parseInt(document.getElementById('gamesSlider').value);
  state.minValue = parseInt(document.getElementById('valueSlider').value);
  filterModal.classList.remove('open');
  render();
});
document.getElementById('resetFilters').addEventListener('click', () => {
  ['snap','games','value'].forEach(key => {
    document.getElementById(key + 'Slider').value = 0;
    document.getElementById(key + 'Val').textContent = '0';
  });
  state.minSnap = 0; state.minGames = 0; state.minValue = 0;
  render();
});

render();
</script>
"""

TRADE_CALC_HTML = BASE_STYLE + make_header("trade") + """
<main><div class="wrap">
  <div class="panel">
    <p class="eyebrow-desc">Real dynasty &amp; redraft values, including draft picks</p>
    <h2>Trade calculator</h2>

    <div class="toggle-row">
      <div class="toggle-group">
        <span class="glabel">Format</span>
        <div class="format-toggle">
          <a class="{{ 'active' if fmt=='1qb' else '' }}" href="#" onclick="setParam('format','1qb');return false;">1QB</a>
          <a class="{{ 'active' if fmt=='superflex' else '' }}" href="#" onclick="setParam('format','superflex');return false;">Superflex</a>
        </div>
      </div>
      <div class="toggle-group">
        <span class="glabel">Mode</span>
        <div class="format-toggle">
          <a class="{{ 'active' if mode=='dynasty' else '' }}" href="#" onclick="setParam('mode','dynasty');return false;">Dynasty</a>
          <a class="{{ 'active' if mode=='redraft' else '' }}" href="#" onclick="setParam('mode','redraft');return false;">Redraft</a>
        </div>
      </div>
      <div class="toggle-group">
        <span class="glabel">League size</span>
        <div class="format-toggle">
          {% for n in [8, 10, 12, 14] %}
          <a class="{{ 'active' if teams==n else '' }}" href="#" onclick="setParam('teams',{{ n }});return false;">{{ n }}</a>
          {% endfor %}
        </div>
      </div>
    </div>
    <p class="muted" style="margin-top:8px;font-size:12px;">Values default to a 12-team consensus &mdash; switch this to match your actual league size for more accurate pricing.</p>

    <div class="link-box">
      {% if not league_link %}
      <form method="get" class="search-row" style="margin-top:0;">
        <input type="hidden" name="format" value="{{ fmt }}"><input type="hidden" name="mode" value="{{ mode }}"><input type="hidden" name="teams" value="{{ teams }}">
        <input type="text" name="u" placeholder="Link your Sleeper username (optional)">
        <button class="btn" type="submit">Load leagues</button>
      </form>
      {% elif league_link.error %}
      <div class="error">{{ league_link.error }}</div>
      {% elif not league_link.selected_league_id %}
      <p class="muted">Pick a league for <strong>{{ league_link.username }}</strong>:</p>
      <div class="league-chip-row">
        {% for lg in league_link.leagues %}
        <a class="team-chip" href="/trade-calculator?format={{ fmt }}&mode={{ mode }}&teams={{ teams }}&u={{ league_link.username }}&league_id={{ lg.league_id }}">{{ lg.league_name }}</a>
        {% endfor %}
      </div>
      {% else %}
      <p class="muted">Playing as <strong style="color:var(--accent-ink);">{{ league_link.my_team.owner_name if league_link.my_team else '?' }}</strong>. Trade with:</p>
      <div class="league-chip-row">
        {% for t in league_link.other_teams %}
        <a class="team-chip {{ 'active' if league_link.other_team and t.roster_id == league_link.other_team.roster_id else '' }}" href="/trade-calculator?format={{ fmt }}&mode={{ mode }}&teams={{ teams }}&u={{ league_link.username }}&league_id={{ league_link.selected_league_id }}&other_roster_id={{ t.roster_id }}&side1={{ side1_ids }}&side2={{ side2_ids }}">{{ t.owner_name }}</a>
        {% endfor %}
      </div>
      {% endif %}
    </div>

    <div class="trade-cols">
      <div class="trade-side-box">
        <div class="trade-side-label">You send</div>
        <div class="search-wrap">
          <input type="text" id="search1" placeholder="{{ 'Search for Players & Draft Picks' if mode=='dynasty' else 'Search a Player' }}" autocomplete="off">
          <div class="search-dropdown" id="dropdown1"></div>
        </div>
        <div class="chip-list" id="chips1"></div>
        <div class="trade-total" id="total1">Total: 0</div>
        <div class="trade-total-adjusted" id="adjusted1" style="display:none;"></div>
        {% if draft_picks_quick %}
        <p class="muted" style="margin-top:10px;">Draft picks (click to add):</p>
        <div class="quick-add-grid">
          {% for pk in draft_picks_quick %}
          <div class="quick-add-tile" data-sid="{{ pk.sid }}" data-name="{{ pk.name }}" data-position="{{ pk.position }}" data-team="{{ pk.team }}" data-photo="{{ pk.photo }}" data-value="{{ pk.value }}" onclick="quickAddClick(1,this)">
            <img src="{{ pk.photo }}" onerror="this.style.visibility='hidden'">{{ pk.name }}
          </div>
          {% endfor %}
        </div>
        {% endif %}
        {% if my_quick %}
        <p class="muted" style="margin-top:10px;">Your roster (click to add):</p>
        <div class="quick-add-grid">
          {% for pl in my_quick %}
          <div class="quick-add-tile" data-sid="{{ pl.sid }}" data-name="{{ pl.name }}" data-position="{{ pl.position }}" data-team="{{ pl.team }}" data-photo="{{ pl.photo }}" data-value="{{ pl.value }}" onclick="quickAddClick(1,this)">
            <img src="{{ pl.photo }}" onerror="this.style.visibility='hidden'">{{ pl.name }}
          </div>
          {% endfor %}
        </div>
        {% endif %}
      </div>
      <div class="trade-side-box">
        <div class="trade-side-label">You receive</div>
        <div class="search-wrap">
          <input type="text" id="search2" placeholder="{{ 'Search for Players & Draft Picks' if mode=='dynasty' else 'Search a Player' }}" autocomplete="off">
          <div class="search-dropdown" id="dropdown2"></div>
        </div>
        <div class="chip-list" id="chips2"></div>
        <div class="trade-total" id="total2">Total: 0</div>
        <div class="trade-total-adjusted" id="adjusted2" style="display:none;"></div>
        {% if draft_picks_quick %}
        <p class="muted" style="margin-top:10px;">Draft picks (click to add):</p>
        <div class="quick-add-grid">
          {% for pk in draft_picks_quick %}
          <div class="quick-add-tile" data-sid="{{ pk.sid }}" data-name="{{ pk.name }}" data-position="{{ pk.position }}" data-team="{{ pk.team }}" data-photo="{{ pk.photo }}" data-value="{{ pk.value }}" onclick="quickAddClick(2,this)">
            <img src="{{ pk.photo }}" onerror="this.style.visibility='hidden'">{{ pk.name }}
          </div>
          {% endfor %}
        </div>
        {% endif %}
        {% if other_quick %}
        <p class="muted" style="margin-top:10px;">{{ league_link.other_team.owner_name }}'s roster (click to add):</p>
        <div class="quick-add-grid">
          {% for pl in other_quick %}
          <div class="quick-add-tile" data-sid="{{ pl.sid }}" data-name="{{ pl.name }}" data-position="{{ pl.position }}" data-team="{{ pl.team }}" data-photo="{{ pl.photo }}" data-value="{{ pl.value }}" onclick="quickAddClick(2,this)">
            <img src="{{ pl.photo }}" onerror="this.style.visibility='hidden'">{{ pl.name }}
          </div>
          {% endfor %}
        </div>
        {% endif %}
      </div>
    </div>

    <div class="balance-bar-wrap" id="balanceBarWrap" style="display:none;">
      <div class="balance-bar">
        <div class="balance-fill balance-fill-1" id="balanceFill1"></div>
        <div class="balance-fill balance-fill-2" id="balanceFill2"></div>
        <div class="balance-center-marker"></div>
        <div class="balance-pointer" id="balancePointer"></div>
      </div>
      <div class="balance-labels">
        <span id="balanceLabel1"></span>
        <span id="balanceLabel2"></span>
      </div>
    </div>

    <div class="trade-result" id="tradeResult" style="display:none;">
      <p class="verdict" id="verdictText"></p>
      <p class="muted" id="verdictCaption" style="margin-top:4px;font-size:12px;display:none;"></p>
      <div id="suggestionsBlock" style="display:none;">
        <p class="muted" style="margin-top:10px;">To get this closer to even, consider adding:</p>
        <div id="suggestionsList"></div>
      </div>
    </div>
  </div>
</div></main>

<script>
const fmt = {{ fmt|tojson }};
const mode = {{ mode|tojson }};
const teams = {{ teams|tojson }};
const initialSide1 = {{ result.side1_items|tojson if result else '[]' }};
const initialSide2 = {{ result.side2_items|tojson if result else '[]' }};
const initialResult = {{ result|tojson if result else 'null' }};
const initialSuggestions = {{ suggestions|tojson }};

function setParam(key, val) {
  const url = new URL(window.location.href);
  url.searchParams.set(key, val);
  url.searchParams.set('side1', selected1.map(p => p.sid).join(','));
  url.searchParams.set('side2', selected2.map(p => p.sid).join(','));
  window.location.href = url.toString();
}

let selected1 = initialSide1.map(p => ({sid: p.sid, name: p.name, position: p.position, team: p.team, photo: p.photo, value: p.value}));
let selected2 = initialSide2.map(p => ({sid: p.sid, name: p.name, position: p.position, team: p.team, photo: p.photo, value: p.value}));

const POS_COLOR_VAR = {QB: '--pos-qb', RB: '--pos-rb', WR: '--pos-wr', TE: '--pos-te'};
function posColorVar(pos) {
  return `var(${POS_COLOR_VAR[pos] || '--accent'})`;
}

function renderChips(side) {
  const list = side === 1 ? selected1 : selected2;
  const container = document.getElementById('chips' + side);
  container.innerHTML = '';
  let total = 0;
  list.forEach(p => {
    total += p.value || 0;
    const chip = document.createElement('div');
    chip.className = 'chip';
    chip.innerHTML = `
      <span class="remove" data-sid="${p.sid}">&times;</span>
      <img src="${p.photo}" onerror="this.style.visibility='hidden'">
      ${p.position ? `<span class="pos-chip" style="background:${posColorVar(p.position)};">${p.position}</span>` : ''}
      ${p.team ? `<span class="team-tag">${p.team}</span>` : ''}
      <span class="pname-sm">${p.name}</span>
    `;
    chip.querySelector('.remove').onclick = () => removePlayer(side, p.sid);
    container.appendChild(chip);
  });
  document.getElementById('total' + side).textContent = 'Total: ' + total;
}

function removePlayer(side, sid) {
  if (side === 1) selected1 = selected1.filter(p => p.sid !== sid);
  else selected2 = selected2.filter(p => p.sid !== sid);
  renderChips(side);
  updateTradeResult();
}

function addPlayer(side, player) {
  const list = side === 1 ? selected1 : selected2;
  if (list.some(p => p.sid === player.sid)) return;
  list.push(player);
  renderChips(side);
  const input = document.getElementById('search' + side);
  if (input) input.value = '';
  const dd = document.getElementById('dropdown' + side);
  if (dd) dd.classList.remove('open');
  updateTradeResult();
}

function quickAddClick(side, el) {
  addPlayer(side, {
    sid: el.dataset.sid, name: el.dataset.name, position: el.dataset.position,
    team: el.dataset.team, photo: el.dataset.photo, value: parseFloat(el.dataset.value),
  });
}

function renderSuggestions(suggestions) {
  const block = document.getElementById('suggestionsBlock');
  const list = document.getElementById('suggestionsList');
  list.innerHTML = '';
  if (!suggestions || !suggestions.length) { block.style.display = 'none'; return; }
  suggestions.forEach(s => {
    const row = document.createElement('div');
    row.className = 'suggestion-row';
    row.innerHTML = `<img src="${s.photo}" onerror="this.style.visibility='hidden'"><span>${s.name}</span><span class="mono muted">(${s.value} pts)</span>`;
    list.appendChild(row);
  });
  block.style.display = '';
}

function updateBalanceBar(result) {
  const wrap = document.getElementById('balanceBarWrap');
  const total = result ? result.side1_adjusted + result.side2_adjusted : 0;
  if (!result || total <= 0) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = '';

  const pct1 = (result.side1_adjusted / total) * 100;
  const pct2 = 100 - pct1;
  document.getElementById('balanceFill1').style.width = pct1 + '%';
  document.getElementById('balanceFill2').style.width = pct2 + '%';
  document.getElementById('balancePointer').style.left = pct1 + '%';

  const label1 = document.getElementById('balanceLabel1');
  const label2 = document.getElementById('balanceLabel2');
  label1.textContent = 'You send: ' + Math.round(pct1) + '%';
  label2.textContent = 'You receive: ' + Math.round(pct2) + '%';
  label1.classList.toggle('leading', pct1 > pct2);
  label2.classList.toggle('leading', pct2 > pct1);
}

function applyTradeResult(data) {
  const result = data.result;

  const adj1 = document.getElementById('adjusted1');
  if (result && result.side1_adjusted !== result.side1_total) {
    adj1.textContent = 'Adjusted: ' + result.side1_adjusted;
    adj1.style.display = '';
  } else {
    adj1.style.display = 'none';
  }

  const adj2 = document.getElementById('adjusted2');
  if (result && result.side2_adjusted !== result.side2_total) {
    adj2.textContent = 'Adjusted: ' + result.side2_adjusted;
    adj2.style.display = '';
  } else {
    adj2.style.display = 'none';
  }

  updateBalanceBar(result);

  const tradeResult = document.getElementById('tradeResult');
  if (!result) {
    tradeResult.style.display = 'none';
    renderSuggestions(null);
    return;
  }
  tradeResult.style.display = '';

  const verdict = document.getElementById('verdictText');
  const diff = result.adjusted_diff;
  verdict.style.color = diff >= 0 ? 'var(--good)' : 'var(--critical)';
  verdict.textContent = (diff >= 0 ? 'You gain ' : 'You lose ') + Math.abs(diff) + ' pts of value';

  const caption = document.getElementById('verdictCaption');
  if (result.adjusted_diff !== result.diff) {
    caption.textContent = 'Adjusted for package size (fewer, bigger pieces carry a premium) · raw value diff: ' + result.diff;
    caption.style.display = '';
  } else {
    caption.style.display = 'none';
  }

  renderSuggestions(data.suggestions);
}

let tradeResultRequestId = 0;

function updateTradeResult() {
  // Was a full window.location.href navigation on every single
  // add/remove -- reloaded the entire page (fonts, header, everything)
  // just to update a verdict box. Now: update the URL in place (so the
  // trade is still bookmarkable/shareable) and fetch just the
  // verdict/suggestions JSON, patching the DOM instead of reloading it.
  const url = new URL(window.location.href);
  url.searchParams.set('format', fmt);
  url.searchParams.set('mode', mode);
  url.searchParams.set('teams', teams);
  url.searchParams.set('side1', selected1.map(p => p.sid).join(','));
  url.searchParams.set('side2', selected2.map(p => p.sid).join(','));
  history.replaceState(null, '', url.toString());

  // Rapid-fire adds/removes (e.g. quickly clicking two roster tiles)
  // fire overlapping fetches that can resolve out of order over a real
  // network -- an earlier request's response arriving after a later
  // one would otherwise overwrite the correct result with a stale one.
  // Only the response to the MOST RECENT request is allowed to apply.
  const thisRequestId = ++tradeResultRequestId;
  fetch('/api/trade-result?' + url.searchParams.toString())
    .then(r => r.json())
    .then(data => { if (thisRequestId === tradeResultRequestId) applyTradeResult(data); })
    .catch(() => {});
}

let debounceTimer;
function wireSearch(side) {
  const input = document.getElementById('search' + side);
  const dropdown = document.getElementById('dropdown' + side);
  input.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    const q = input.value.trim();
    if (!q) { dropdown.classList.remove('open'); return; }
    debounceTimer = setTimeout(async () => {
      const resp = await fetch(`/api/player-search?q=${encodeURIComponent(q)}&format=${fmt}&mode=${mode}&teams=${teams}`);
      const data = await resp.json();
      dropdown.innerHTML = '';
      data.results.forEach(r => {
        const item = document.createElement('div');
        item.className = 'search-dropdown-item';
        item.innerHTML = `<img src="${r.photo}" onerror="this.style.visibility='hidden'"><span>${r.name} <span class="muted">${r.position}${r.team ? ' &middot; ' + r.team : ''}</span></span>`;
        item.onclick = () => addPlayer(side, {sid: r.sid, name: r.name, position: r.position, team: r.team, photo: r.photo, value: r.value});
        dropdown.appendChild(item);
      });
      dropdown.classList.toggle('open', data.results.length > 0);
    }, 200);
  });
  document.addEventListener('click', (e) => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) dropdown.classList.remove('open');
  });
}

renderChips(1);
renderChips(2);
applyTradeResult({result: initialResult, suggestions: initialSuggestions});
wireSearch(1);
wireSearch(2);
</script>
"""

COMING_SOON_HTML = BASE_STYLE + make_header("mock") + """
<main><div class="wrap">
  <div class="panel">
    <p class="eyebrow">Coming soon</p>
    <h2>{{ title }}</h2>
    <p class="muted" style="margin-top:10px;">{{ body }}</p>
  </div>
</div></main>
"""

AUTH_STYLE = """
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@700;800;900&family=Source+Sans+3:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  html{ -webkit-text-size-adjust:100%; text-size-adjust:100%; }  body{ margin:0; background:#0d0f0d; color:#e8e6df; font-family:"Source Sans 3",system-ui,sans-serif; min-height:100vh; }
  .auth-top{ display:flex; justify-content:flex-end; padding:24px 32px; }
  .auth-logo{ font-family:"Big Shoulders Display"; font-weight:800; font-size:18px; text-transform:uppercase; color:#e8e6df; text-decoration:none; }
  .auth-wrap{ max-width:400px; margin:20px auto 80px; padding:0 24px; }
  .auth-wrap h1{ font-family:"Big Shoulders Display"; font-size:32px; font-weight:800; text-transform:uppercase; margin:0; }
  .auth-sub{ color:#8b9089; font-size:14px; margin-top:8px; }
  .auth-sub a{ color:#b97a1f; text-decoration:none; font-weight:600; }
  .auth-field{ margin-top:18px; }
  .auth-field label{ font-size:12.5px; font-weight:600; color:#8b9089; display:block; margin-bottom:6px; }
  .auth-field input[type=text], .auth-field input[type=email], .auth-field input[type=password]{
    width:100%; background:#151815; border:1px solid rgba(255,255,255,0.12); color:#e8e6df;
    border-radius:8px; padding:12px 14px; font-size:15px; font-family:inherit; box-sizing:border-box;
  }
  .pw-row{ position:relative; }
  .pw-toggle{ position:absolute; right:12px; top:50%; transform:translateY(-50%); background:none; border:none; color:#8b9089; font-size:12px; font-weight:600; cursor:pointer; }
  .username-row{ display:flex; gap:8px; }
  .username-row input{ flex:1; }
  .gen-btn{ background:#1c201c; border:1px solid rgba(255,255,255,0.12); color:#e8e6df; border-radius:8px; padding:0 16px; font-weight:600; cursor:pointer; font-size:13px; }
  .username-status{ font-size:12px; margin-top:6px; min-height:16px; }
  .username-status.ok{ color:#1fae5a; }
  .username-status.bad{ color:#e2534a; }
  .username-status.checking{ color:#8b9089; }
  .checkbox-row{ display:flex; align-items:flex-start; gap:8px; margin-top:16px; font-size:13px; color:#8b9089; }
  .checkbox-row input{ margin-top:2px; }
  .checkbox-row a{ color:#b97a1f; text-decoration:none; }
  .join-btn{ width:100%; margin-top:22px; padding:13px; border-radius:8px; border:none; background:#2fae4e; color:#fff; font-weight:800; font-size:15px; cursor:pointer; }
  .join-btn:disabled{ background:#264d31; color:#7a9a83; cursor:not-allowed; }
  .divider{ display:flex; align-items:center; gap:12px; margin:22px 0; color:#8b9089; font-size:12.5px; }
  .divider::before, .divider::after{ content:''; flex:1; height:1px; background:rgba(255,255,255,0.12); }
  .oauth-btn{ width:100%; display:flex; align-items:center; justify-content:center; gap:10px; padding:12px; border-radius:8px; font-weight:700; font-size:14px; text-decoration:none; margin-top:10px; box-sizing:border-box; }
  .oauth-google{ background:#fff; color:#1f1f1f; border:1px solid rgba(0,0,0,0.1); }
  .auth-error{ background:rgba(226,83,74,0.16); color:#e2534a; padding:10px 14px; border-radius:8px; font-size:13.5px; margin-top:16px; }
</style>
"""

SIGNUP_HTML = AUTH_STYLE + """
<div class="auth-top"><a class="auth-logo" href="/">Fantasy Football Calc</a></div>
<div class="auth-wrap">
  <h1>Create Account</h1>
  <p class="auth-sub">Already have an account? <a href="/login">Sign In</a></p>
  {% if error %}<div class="auth-error">{{ error }}</div>{% endif %}
  <form method="post" id="signupForm">
    <div class="auth-field">
      <label>Email Address</label>
      <input type="email" name="email" required>
    </div>
    <div class="auth-field">
      <label>Password</label>
      <div class="pw-row">
        <input type="password" name="password" id="pwInput" minlength="8" required>
        <button type="button" class="pw-toggle" onclick="togglePw()">SHOW</button>
      </div>
    </div>
    <div class="auth-field">
      <label>Username</label>
      <div class="username-row">
        <input type="text" name="username" id="usernameInput" maxlength="20" required autocomplete="off">
        <button type="button" class="gen-btn" onclick="generateUsername()">Generate</button>
      </div>
      <div class="username-status" id="usernameStatus"></div>
    </div>
    <div class="auth-field">
      <label>Referral Code (optional)</label>
      <input type="text" name="referral_code">
    </div>
    <div class="checkbox-row">
      <input type="checkbox" name="newsletter" id="newsletterBox" checked>
      <label for="newsletterBox">Send me the free fantasy football newsletter</label>
    </div>
    <div class="checkbox-row">
      <input type="checkbox" name="agree_tos" id="tosBox">
      <label for="tosBox">I agree to the <a href="#">Terms of Service</a> and <a href="#">Privacy Policy</a></label>
    </div>
    <button type="submit" class="join-btn" id="joinBtn" disabled>Create Account</button>
  </form>
  <div class="divider">or</div>
  <a class="oauth-btn oauth-google" href="/auth/google/login">
    <svg width="18" height="18" viewBox="0 0 18 18"><path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"/><path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.87-3.04.87-2.34 0-4.32-1.58-5.03-3.7H.94v2.33A9 9 0 0 0 9 18z"/><path fill="#FBBC05" d="M3.97 10.73A5.4 5.4 0 0 1 3.68 9c0-.6.1-1.19.29-1.73V4.94H.94A9 9 0 0 0 0 9c0 1.45.35 2.83.94 4.06l3.03-2.33z"/><path fill="#EA4335" d="M9 3.58c1.32 0 2.51.45 3.44 1.35l2.59-2.59C13.46.89 11.43 0 9 0A9 9 0 0 0 .94 4.94l3.03 2.33C4.68 5.16 6.66 3.58 9 3.58z"/></svg>
    Continue with Google
  </a>
</div>
<script>
function togglePw() {
  const input = document.getElementById('pwInput');
  const btn = document.querySelector('.pw-toggle');
  if (input.type === 'password') { input.type = 'text'; btn.textContent = 'HIDE'; }
  else { input.type = 'password'; btn.textContent = 'SHOW'; }
}

const adjectives = ['Swift','Bold','Iron','Silent','Golden','Savage','Cosmic','Rogue','Fierce','Clutch'];
const nouns = ['Falcon','Wolf','Titan','Ranger','Phoenix','Hawk','Bison','Comet','Rhino','Viper'];
function generateUsername() {
  const name = adjectives[Math.floor(Math.random()*adjectives.length)] + nouns[Math.floor(Math.random()*nouns.length)] + Math.floor(Math.random()*90+10);
  document.getElementById('usernameInput').value = name;
  checkUsername();
}

let usernameOk = false;
let tosOk = false;
let checkTimer;
function updateJoinBtn() {
  document.getElementById('joinBtn').disabled = !(usernameOk && tosOk);
}

async function checkUsername() {
  const val = document.getElementById('usernameInput').value.trim();
  const statusEl = document.getElementById('usernameStatus');
  if (!val) { statusEl.textContent = ''; statusEl.className = 'username-status'; usernameOk = false; updateJoinBtn(); return; }
  statusEl.textContent = 'checking...';
  statusEl.className = 'username-status checking';
  try {
    const resp = await fetch('/api/check-username?u=' + encodeURIComponent(val));
    const data = await resp.json();
    if (data.available) {
      statusEl.textContent = '\u2713 Available';
      statusEl.className = 'username-status ok';
      usernameOk = true;
    } else {
      statusEl.textContent = data.reason === 'taken' ? '\u2717 Already taken' : '\u2717 Letters, numbers, underscores only (max 20)';
      statusEl.className = 'username-status bad';
      usernameOk = false;
    }
  } catch (e) {
    statusEl.textContent = '';
    usernameOk = false;
  }
  updateJoinBtn();
}

document.getElementById('usernameInput').addEventListener('input', () => {
  usernameOk = false;
  updateJoinBtn();
  clearTimeout(checkTimer);
  checkTimer = setTimeout(checkUsername, 400);
});
document.getElementById('tosBox').addEventListener('change', (e) => { tosOk = e.target.checked; updateJoinBtn(); });
</script>
"""

LOGIN_PAGE_HTML = AUTH_STYLE + """
<div class="auth-top"><a class="auth-logo" href="/">Fantasy Football Calc</a></div>
<div class="auth-wrap">
  <h1>Welcome Back</h1>
  <p class="auth-sub">Don't have an account? <a href="/signup">Create one</a></p>
  {% if error %}<div class="auth-error">{{ error }}</div>{% endif %}
  <form method="post">
    <div class="auth-field">
      <label>Email or Username</label>
      <input type="text" name="email" required>
    </div>
    <div class="auth-field">
      <label>Password</label>
      <div class="pw-row">
        <input type="password" name="password" id="pwInput" required>
        <button type="button" class="pw-toggle" onclick="togglePw()">SHOW</button>
      </div>
    </div>
    <p class="auth-sub" style="margin-top:10px;"><a href="#">Forgot password?</a></p>
    <button type="submit" class="join-btn">Sign In</button>
  </form>
  <div class="divider">or</div>
  <a class="oauth-btn oauth-google" href="/auth/google/login">
    <svg width="18" height="18" viewBox="0 0 18 18"><path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"/><path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.87-3.04.87-2.34 0-4.32-1.58-5.03-3.7H.94v2.33A9 9 0 0 0 9 18z"/><path fill="#FBBC05" d="M3.97 10.73A5.4 5.4 0 0 1 3.68 9c0-.6.1-1.19.29-1.73V4.94H.94A9 9 0 0 0 0 9c0 1.45.35 2.83.94 4.06l3.03-2.33z"/><path fill="#EA4335" d="M9 3.58c1.32 0 2.51.45 3.44 1.35l2.59-2.59C13.46.89 11.43 0 9 0A9 9 0 0 0 .94 4.94l3.03 2.33C4.68 5.16 6.66 3.58 9 3.58z"/></svg>
    Continue with Google
  </a>
</div>
<script>
function togglePw() {
  const input = document.getElementById('pwInput');
  const btn = document.querySelector('.pw-toggle');
  if (input.type === 'password') { input.type = 'text'; btn.textContent = 'HIDE'; }
  else { input.type = 'password'; btn.textContent = 'SHOW'; }
}
</script>
"""

LOGIN_HTML = BASE_STYLE + make_header() + """
<main><div class="wrap" style="max-width:360px;">
  <div class="panel">
    <h2>Trade Chat</h2>
    <p class="muted" style="margin-top:6px;">Private &mdash; password required</p>
    <form method="post" class="search-row" style="margin-top:16px;">
      <input type="password" name="password" placeholder="Password" autofocus>
      <button class="btn" type="submit">Enter</button>
    </form>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
  </div>
</div></main>
"""

CHAT_HTML = BASE_STYLE + make_header() + """
<main><div class="wrap">
  <div class="panel">
    <p class="eyebrow">Trade advisor</p>
    <h2>Ask about a trade or your roster</h2>
    <form method="post" class="search-row">
      <input type="hidden" name="u" value="{{ username }}">
      <input type="text" name="question" placeholder="Should I trade Kelce for a 2nd?" value="{{ question }}" autofocus>
      <button class="btn" type="submit">Ask</button>
    </form>
    {% if answer %}<div class="answer">{{ answer }}</div>{% endif %}
  </div>
</div></main>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
