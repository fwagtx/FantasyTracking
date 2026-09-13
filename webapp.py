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
from flask import Flask, request, session, redirect, render_template_string, jsonify, url_for
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

# ---------------- Accounts: database ----------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_db():
    """A fresh connection per call -- simplest thing that works for this
    app's traffic level. Neon (or any real Postgres) handles this fine."""
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

DEPTH_SLOT_PRIORITY = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}

# Sleeper labels receiver depth slots by side (Left/Right/Slot WR), not a
# flat WR1/WR2 pattern like QB/RB/TE use -- this was the actual bug
# causing every WR to be silently dropped, since nothing recognized
# LWR/RWR/SWR as a WR at all.
WR_VARIANTS = {"LWR", "RWR", "SWR"}


def _depth_slot_base(slot):
    base = re.sub(r"\d+$", "", slot)
    return "WR" if base in WR_VARIANTS else base


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
    dump already carries this. Restricted to skill positions (QB/RB/WR/TE)
    to match the rest of the site.

    Sleeper labels receiver depth slots by side (LWR/RWR/SWR) instead of a
    flat WR1/WR2/WR3 pattern like QB/RB/TE use. We merge all three into one
    WR column here (ordered by Sleeper's depth_chart_order) so the chart
    shows one real WR pecking order instead of three separate boxes, and
    number every player within their column (QB1, QB2, WR1, WR2, ...)."""
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
        if base not in POSITIONS:
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


def get_all_players(cache={}):
    now = time.time()
    if "players" not in cache or now - cache.get("time", 0) > 86400:
        r = requests.get(f"{SLEEPER_BASE}/players/nfl")
        r.raise_for_status()
        cache["players"] = r.json()
        cache["time"] = now
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
        r = requests.get(
            f"{ESPN_SITE_BASE}/scoreboard",
            params={"week": week, "seasontype": season_type, "year": season},
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
    return {
        "espn_event_id": ev.get("id"),
        "kickoff": ev.get("date"),
        "home_team": normalize_team_abbr((home.get("team") or {}).get("abbreviation")),
        "away_team": normalize_team_abbr((away.get("team") or {}).get("abbreviation")),
        "home_score": int(home["score"]) if home.get("score") not in (None, "") else None,
        "away_score": int(away["score"]) if away.get("score") not in (None, "") else None,
        "status": _ESPN_STATE_TO_STATUS.get(state, "scheduled"),
    }


def sync_week_schedule_to_db(season, week, season_type=2):
    """Fetch one week's games from ESPN and upsert into nfl_schedule.
    Returns rows upserted. Safe to call repeatedly (ON CONFLICT DO
    UPDATE) -- this is how in-progress/final scores get refreshed."""
    if not DATABASE_URL:
        return 0
    data = espn_week_scoreboard(season, week, season_type)
    rows = [r for r in (_parse_espn_event(ev) for ev in data.get("events", [])) if r]
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
                           home_score = EXCLUDED.home_score, away_score = EXCLUDED.away_score,
                           status = EXCLUDED.status, kickoff = EXCLUDED.kickoff, updated_at = NOW()""",
                    (row["espn_event_id"], int(season), int(week), int(season_type), row["kickoff"],
                     row["home_team"], row["away_team"], row["home_score"], row["away_score"], row["status"]),
                )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


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
    ensure_schedule_synced(season)
    now = time.time()
    entry = cache.get(season)
    if entry and now - entry["time"] < 3600:
        return entry["data"]

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
        matchup_desc = "not enough defensive data yet to grade the matchup"
    else:
        if c["def_source"] == "current":
            source_note = ""
        elif c["def_source"] == "last_year":
            source_note = " (based on last year)"
        else:
            games = c["def_games_sampled"]
            source_note = f" (small sample -- {games} game{'s' if games != 1 else ''} this year)"
        rank = c["def_rank_used"]
        if rank >= 24:
            matchup_desc = f"a great matchup{source_note}"
        elif rank >= 17:
            matchup_desc = f"a favorable matchup{source_note}"
        elif rank <= 8:
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


def _letter_grade(composite):
    """13-tier letter grade (A+ down to F) from a 0-1 composite score --
    a bare 5-bucket A/B/C/D/F band crowded together matchups that were
    actually meaningfully different. Stars stay a coarser 1-5 scale
    grouped by the base letter (every A-tier is 5 stars, etc.)."""
    bands = [
        (0.92, "A+", 5), (0.85, "A", 5), (0.78, "A-", 5),
        (0.71, "B+", 4), (0.64, "B", 4), (0.57, "B-", 4),
        (0.50, "C+", 3), (0.43, "C", 3), (0.36, "C-", 3),
        (0.29, "D+", 2), (0.22, "D", 2), (0.15, "D-", 2),
    ]
    for threshold, grade, stars in bands:
        if composite >= threshold:
            return grade, stars
    return "F", 1


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
    # position before its current-year number outranks last year's full
    # 17-game read on the same defense. Below that threshold the grade
    # relies on last year exclusively; once a defense clears it (which
    # happens automatically, team by team, as the season plays out), the
    # switch to this year's real, current data is also automatic.
    last_year_dvp = get_defense_vs_position(season - 1)
    last_year_entry = last_year_dvp.get(sched["opponent"], {}).get(position) if sched else None

    if opp_entry and opp_entry["games"] >= MIN_DEF_GAMES_FOR_CURRENT_YEAR:
        def_rank_used, def_source, def_pool_size = opp_entry["rank"], "current", len(dvp) or 32
        def_games_sampled = opp_entry["games"]
    elif last_year_entry:
        def_rank_used, def_source, def_pool_size = last_year_entry["rank"], "last_year", len(last_year_dvp) or 32
        def_games_sampled = last_year_entry["games"]
    elif opp_entry:
        # Early season, no last-year data available either (e.g. a team
        # that didn't exist under this abbreviation last year) -- better
        # than nothing, but flagged distinctly so the UI is honest about
        # how thin the sample actually is.
        def_rank_used, def_source, def_pool_size = opp_entry["rank"], "current_thin", len(dvp) or 32
        def_games_sampled = opp_entry["games"]
    else:
        def_rank_used, def_source, def_pool_size, def_games_sampled = None, None, 32, None
    def_percentile = (def_rank_used - 1) / max(def_pool_size - 1, 1) if def_rank_used is not None else 0.5

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
        grade, stars, star_pct = "F", 1, 8
    elif tier == "doubtful":
        grade, stars, star_pct = "D-", 2, 25
    else:
        grade, stars = _letter_grade(composite)
        # A continuous fill (0-100%) driven directly by the composite,
        # not snapped to the coarse 1-5 whole-star count above -- two
        # matchups in the same letter tier (say a strong B+ and a weak
        # one) now visibly show different amounts of star fill instead
        # of looking identical.
        star_pct = max(5, min(100, round(composite * 100)))

    components = {
        "opponent": sched["opponent"] if sched else None,
        "def_rank": opp_entry["rank"] if opp_entry else None,
        "def_fpts_allowed_pg": opp_entry["fpts_allowed_per_game"] if opp_entry else None,
        "def_rank_used": def_rank_used,
        "def_source": def_source,
        "def_games_sampled": def_games_sampled,
        "def_percentile": round(def_percentile, 2),
        "def_rank_last_year": last_year_entry["rank"] if last_year_entry else None,
        "def_fpts_allowed_pg_last_year": last_year_entry["fpts_allowed_per_game"] if last_year_entry else None,
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
        weeks_sorted = sorted((stat.get("weeks") or {}).items())
        recent = [fpts for _, fpts in weeks_sorted[-4:]]
        badge = _injury_badge(p)
        c = grade["components"]
        return {
            "sid": sid,
            "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position": p.get("position"), "team": p.get("team") or "FA",
            "photo": player_photo_url(sid),
            "opponent": c["opponent"], "def_rank": c["def_rank"],
            "def_fpts_allowed_pg": c["def_fpts_allowed_pg"],
            "def_rank_last_year": c["def_rank_last_year"],
            "def_fpts_allowed_pg_last_year": c["def_fpts_allowed_pg_last_year"],
            "def_source": c["def_source"], "def_games_sampled": c["def_games_sampled"],
            "grade": grade["grade"], "grade_class": grade["grade_class"], "stars": grade["stars"], "star_pct": grade["star_pct"], "composite": c["composite"],
            "season_avg": round(stat["fpts"] / stat["games"], 1) if stat.get("games") else 0.0,
            "recent_avg": round(sum(recent) / len(recent), 1) if recent else 0.0,
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
    if start["def_rank"] and sit["def_rank"] and start["def_rank"] != sit["def_rank"]:
        easier, harder = (start, sit) if start["def_rank"] > sit["def_rank"] else (sit, start)
        if easier is start:
            reasons.append(f"{start['name']} draws the easier matchup -- {start['opponent']} ranks {start['def_rank']} against the position this season, vs. {sit['opponent']} at {sit['def_rank']} for {sit['name']}.")
    if start["def_rank_last_year"] and sit["def_rank_last_year"] and start["def_rank_last_year"] != sit["def_rank_last_year"]:
        reasons.append(
            f"Last season, {start['opponent']} allowed {start['def_fpts_allowed_pg_last_year']} pts/gm to the position "
            f"(rank {start['def_rank_last_year']}) vs. {sit['opponent']}'s {sit['def_fpts_allowed_pg_last_year']} (rank {sit['def_rank_last_year']})."
        )
    if start["recent_avg"] > sit["recent_avg"] + 1:
        reasons.append(f"{start['name']} is trending up recently ({start['recent_avg']} pts/gm over their last few weeks vs. {sit['recent_avg']} for {sit['name']}).")
    if not reasons:
        reasons.append(f"{start['name']} grades out higher overall this week ({start['grade']} vs. {sit['grade']}).")

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
_stats_seeded_seasons = set()


def ensure_season_stats_synced(season):
    """Self-heals a season with ZERO rows in player_stats -- the gap
    matchup grading actually hit: the one-time historical backfill
    (backfill-stats.yml) only ever runs when someone manually dispatches
    it in GitHub Actions, and the recurring 2-hour cron only ever syncs
    "the current season". A season that's already over by the time this
    app starts treating something newer as current -- last season, right
    after a new one kicks off -- has no automatic path to ever get synced
    unless something explicitly asks for it. This is that ask, fired the
    moment anything (the /matchups page, the defense-vs-position
    fallback) actually needs a prior season's stats.

    Deliberately a background thread, never a blocking fetch: an earlier
    version of get_season_stats DID block on a live fetch when a season
    was empty, and that was the actual, confirmed cause of the whole site
    timing out (see that function's docstring) -- so this must never
    repeat that mistake no matter how tempting a synchronous "just fetch
    it now" would be here."""
    if season in _stats_seeded_seasons or not DATABASE_URL:
        return
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM player_stats WHERE season = %s LIMIT 1", (season,))
            has_rows = cur.fetchone() is not None
    finally:
        conn.close()
    if has_rows:
        _stats_seeded_seasons.add(season)
        return
    with _stats_sync_lock:
        if season in _stats_sync_busy_seasons:
            return
        _stats_sync_busy_seasons.add(season)

    def _run():
        try:
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
            sync_season_to_db(season)
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


def _week_games(season, week, season_type=2):
    """Shared by /scores and /api/scoreboard so both build cards the same
    way. Returns (games, any_live) where games is a list of card dicts
    from espn_event_to_card, already filtered for malformed events."""
    data = espn_week_scoreboard(season, week, season_type)
    games = [c for c in (espn_event_to_card(ev) for ev in data.get("events", [])) if c]
    any_live = any(g["status"] == "in_progress" for g in games)
    return games, any_live


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


@app.route("/scores")
def scores_page():
    username = _resolve_scores_username()
    try:
        info = get_current_week_info()
        season = request.args.get("season", default=info["season"], type=int)
        week = request.args.get("week", default=info["week"], type=int)
        season_type = request.args.get("seasontype", default=info["season_type"], type=int)
        games = _nearby_weeks_games(season, week, season_type)
        _annotate_my_players(games, username)
        has_synced_leagues = bool(current_user.is_authenticated and get_synced_league_ids(current_user.id))
        return render_template_string(
            SCORES_HTML, games=games, season=season, week=week, season_type=season_type,
            current_season=info["season"], current_week=info["week"],
            today_key=date.today().isoformat(), load_error=None,
            username=username, has_synced_leagues=has_synced_leagues,
        )
    except Exception as e:
        # ESPN's API is unofficial and unverified against a live response
        # from this environment -- surface the real error on the page
        # instead of a bare 500, so a shape mismatch is diagnosable from
        # a screenshot alone rather than looking like the page is dead.
        return render_template_string(
            SCORES_HTML, games=[], season=int(SEASON), week=1, season_type=2,
            current_season=int(SEASON), current_week=1, today_key=date.today().isoformat(),
            load_error=str(e), username=username, has_synced_leagues=False,
        )


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
        return render_template_string(GAME_DETAIL_HTML, event_id=event_id, detail=detail, load_error=None)
    except Exception as e:
        empty = {"status": "scheduled", "period": None, "clock": None, "status_detail": None,
                  "venue": {}, "officials": [], "home": {"abbr": None, "name": "?", "score": None, "logo": None},
                  "away": {"abbr": None, "name": "?", "score": None, "logo": None}, "team_stats": [], "player_leaders": []}
        return render_template_string(GAME_DETAIL_HTML, event_id=event_id, detail=empty, load_error=str(e))


@app.route("/api/game-live")
def api_game_live():
    """Trimmed live-poll JSON -- only what can actually change mid-game
    (score, clock, status, team stats). Venue/officials never change
    once the game starts, so the polling loop never re-fetches them."""
    event_id = request.args.get("id", "")
    try:
        summary = espn_game_summary(event_id)
        detail = extract_game_detail(summary)
        return jsonify({
            "status": detail["status"], "period": detail["period"], "clock": detail["clock"],
            "status_detail": detail["status_detail"],
            "home_score": detail["home"]["score"], "away_score": detail["away"]["score"],
            "home_linescores": detail["home"]["linescores"], "away_linescores": detail["away"]["linescores"],
            "team_stats": detail["team_stats"], "player_leaders": detail["player_leaders"],
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
    info = get_current_week_info()
    season = request.args.get("season", default=info["season"], type=int)
    week = request.args.get("week", default=info["week"], type=int)

    rows = []
    load_error = None
    if current_user.is_authenticated:
        try:
            ensure_schedule_synced(season)
            all_players = get_all_players()
            fc_players = get_fantasycalc_values(1)["players"]
            for sid, v in fc_players.items():
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
                    "opponent": grade["components"]["opponent"],
                    "grade": grade["grade"], "grade_class": grade["grade_class"], "stars": grade["stars"], "star_pct": grade["star_pct"],
                    "composite": grade["components"]["composite"],
                    "reasoning": grade["reasoning"],
                    "value": v.get("value", 0),
                })
            # Sort by the actual composite within a star tier too -- 13
            # letter grades share only 5 star tiers, so sorting on stars
            # alone would leave e.g. A+ and A- in an arbitrary order
            # relative to each other.
            rows.sort(key=lambda r: (-r["stars"], -r["composite"], -r["value"]))
            rows = rows[:300]
        except Exception as e:
            load_error = str(e)

    # Visible right on this page, no separate diagnostic URL needed: how
    # much last-year data actually exists behind the "based on last
    # year"/"not enough data" reasoning above. If last_year_players_with_
    # stats is 0, get_season_stats has no rows for that season at all
    # (a stats-sync gap, unrelated to the schedule); if it's nonzero but
    # teams_with_any_defense_data is well under 32, that's the signature
    # of a team-abbreviation mismatch between this app's data and
    # whichever teams never resolve to a match.
    data_status = None
    sample_trace = []
    if current_user.is_authenticated:
        try:
            last_year = season - 1
            last_year_stats = get_season_stats(last_year)
            last_year_dvp = get_defense_vs_position(last_year)
            data_status = {
                "last_year": last_year,
                "last_year_players_with_stats": len(last_year_stats),
                "last_year_teams_with_any_defense_data": len(last_year_dvp),
                "stats_sync_in_progress": last_year in _stats_sync_busy_seasons,
            }
            # Ground truth straight from nfl_schedule itself, for BOTH
            # last year and the current season side by side -- this is
            # the only way to tell apart "last year's schedule never
            # actually got written" (rows_last_year stays 0 no matter how
            # many times the self-heal fires) from "it's written under
            # some other season number than expected" (rows_last_year is
            # 0 while a manual sync somewhere reported success -- meaning
            # that sync almost certainly wrote to a DIFFERENT season,
            # e.g. because it was triggered without an explicit
            # ?season= and silently defaulted to the current one, which
            # already has its own real rows from the recurring cron).
            if DATABASE_URL:
                conn = get_db()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT COUNT(DISTINCT week) AS n, COUNT(*) AS rows FROM nfl_schedule WHERE season = %s",
                            (last_year,),
                        )
                        ly_row = cur.fetchone() or {"n": 0, "rows": 0}
                        cur.execute(
                            "SELECT COUNT(DISTINCT week) AS n, COUNT(*) AS rows FROM nfl_schedule WHERE season = %s",
                            (season,),
                        )
                        cur_row = cur.fetchone() or {"n": 0, "rows": 0}
                        data_status["schedule_last_year_weeks"] = ly_row["n"]
                        data_status["schedule_last_year_rows"] = ly_row["rows"]
                        data_status["schedule_current_season_weeks"] = cur_row["n"]
                        data_status["schedule_current_season_rows"] = cur_row["rows"]
                        data_status["schedule_last_year_sync_in_progress"] = last_year in _schedule_sync_busy_seasons
                        data_status["schedule_last_year_last_sync_result"] = _schedule_sync_last_result.get(last_year)
                finally:
                    conn.close()
            # Stats exist but NOT ONE team matched the schedule -- rather
            # than guess again at why, show real values: three actual
            # players' current team + a week they have stats for, what
            # get_schedule_for_team_week actually returned for that exact
            # lookup, and every schedule row that team appears in at all
            # (any week). Whatever's different between "the team string
            # this app is looking up" and "what's actually stored" will
            # be directly visible side by side here.
            if last_year_stats and not last_year_dvp and DATABASE_URL:
                all_players = get_all_players()
                conn = get_db()
                try:
                    with conn.cursor() as cur:
                        for sid, stat in last_year_stats.items():
                            if len(sample_trace) >= 3:
                                break
                            p = all_players.get(sid)
                            weeks = stat.get("weeks") or {}
                            if not p or not p.get("team") or not weeks:
                                continue
                            team = p["team"]
                            week = sorted(weeks.keys())[0]
                            sched_result = get_schedule_for_team_week(last_year, week, team)
                            cur.execute(
                                "SELECT week, home_team, away_team FROM nfl_schedule "
                                "WHERE season = %s AND (home_team = %s OR away_team = %s) "
                                "ORDER BY week LIMIT 3",
                                (last_year, team, team),
                            )
                            sample_trace.append({
                                "player_sid": sid, "player_current_team": team, "week_checked": week,
                                "get_schedule_for_team_week_result": sched_result,
                                "schedule_rows_for_this_exact_team_string": cur.fetchall(),
                            })
                finally:
                    conn.close()
        except Exception:
            data_status = None
            sample_trace = []

    return render_template_string(
        MATCHUPS_HTML, rows=rows, season=season, week=week,
        current_season=info["season"], current_week=info["week"], load_error=load_error,
        data_status=data_status, sample_trace=sample_trace,
    )


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
    try:
        ensure_schedule_synced(season)
        result = compare_matchups(sid_a, sid_b, season, week)
        if not result:
            return jsonify({"ok": False, "error": "Couldn't grade one of those players -- try a different skill-position player."}), 400
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


@app.route("/api/debug-news")
def api_debug_news():
    """Temporary diagnostic endpoint -- shows exactly what's in the two
    news feeds right now and how a given player name/team matches (or
    doesn't) against them, so a "why is there no news for X" report can
    be root-caused against live feed content instead of guessed at from
    a sandbox that can't reach these feeds itself. Remove once news
    matching is confirmed working end to end."""
    if request.args.get("secret") != SITE_PASSWORD:
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
    if request.args.get("secret") != SITE_PASSWORD:
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
    if request.args.get("secret") != SITE_PASSWORD:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    season = request.args.get("season", default=2024, type=int)
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
    if request.args.get("secret") != SITE_PASSWORD:
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
                params={"week": week, "seasontype": season_type, "year": season},
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
    if request.args.get("secret") != SITE_PASSWORD:
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
                                params={"week": week, "seasontype": 2, "year": season},
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
    if season in _schedule_seeded_seasons or not DATABASE_URL:
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
    if request.args.get("secret") != SITE_PASSWORD:
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
    if request.args.get("secret") != SITE_PASSWORD:
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
                        params={"week": week, "seasontype": season_type, "year": season},
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
    if request.args.get("secret") != SITE_PASSWORD:
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
    if request.args.get("secret") != SITE_PASSWORD:
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
    if request.args.get("secret") != SITE_PASSWORD:
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
    if request.args.get("secret") != SITE_PASSWORD:
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
    if request.args.get("secret") != SITE_PASSWORD:
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
            get_defense_vs_position(int(SEASON))
            # Matchup grading falls back to last season's defense-vs-position
            # numbers early in a new season (this year's sample is thin to
            # nonexistent) -- warming it here means that backfill kicks off
            # on this 12-minute ping instead of waiting on whichever real
            # visitor happens to load /matchups first.
            get_defense_vs_position(int(SEASON) - 1)
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

  .sc-day-tabs{
    display:flex; gap:8px; margin-top:16px; overflow-x:auto; scroll-snap-type:x proximity;
    -webkit-overflow-scrolling:touch; scrollbar-width:none; padding-bottom:4px;
  }
  .sc-day-tabs::-webkit-scrollbar{ display:none; }
  .sc-day-tab{
    font-size:12px; font-weight:700; padding:8px 12px; border-radius:12px; border:1px solid var(--sc-line);
    background:var(--sc-surface); color:var(--sc-muted); cursor:pointer; user-select:none; flex:none;
    scroll-snap-align:center; display:flex; flex-direction:column; align-items:center; gap:2px; min-width:52px;
  }
  .sc-day-tab .dow{ font-size:10px; text-transform:uppercase; opacity:0.8; }
  .sc-day-tab .dnum{ font-family:"IBM Plex Mono"; font-size:14px; }
  .sc-day-tab.active{ background:var(--accent); color:var(--accent-on); border-color:var(--accent); }
  .sc-day-tab.today:not(.active){ border-color:var(--accent); color:var(--sc-text); }
  .sc-day-tab .dot{ display:inline-block; width:5px; height:5px; border-radius:50%; background:var(--sc-live); }
  .sc-day-tab.active .dot{ background:var(--accent-on); }

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

  .sc-games{ display:flex; flex-direction:column; gap:10px; margin-top:18px; }
  .sc-game-card{ display:flex; flex-direction:column; gap:10px; background:var(--sc-surface); border:1px solid var(--sc-line); border-radius:12px; padding:14px 18px; text-decoration:none; color:var(--sc-text); cursor:pointer; }
  .sc-game-card:hover{ border-color:var(--accent); }
  .sc-game-top{ display:flex; align-items:center; gap:16px; }
  .sc-my-players{ display:flex; justify-content:space-between; gap:10px; padding-top:8px; border-top:1px solid var(--sc-line); flex-wrap:wrap; }
  .sc-my-players-pill{ display:inline-flex; align-items:center; gap:5px; background:var(--good-wash); color:var(--good); font-weight:700; font-size:11px; border-radius:99px; padding:4px 10px; flex:none; }
  .sc-my-players-pill.away{ margin-right:auto; }
  .sc-my-players-pill.home{ margin-left:auto; }
  .sc-sync-banner{ display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; background:var(--sc-surface); border:1px solid var(--sc-line); border-radius:10px; padding:10px 16px; margin-top:14px; font-size:13px; color:var(--sc-muted); }
  .sc-sync-banner a{ color:var(--accent-ink); text-decoration:none; font-weight:700; }
  .sc-game-side{ display:flex; align-items:center; gap:10px; flex:1; min-width:0; }
  .sc-game-side img{ width:32px; height:32px; object-fit:contain; flex:none; }
  .sc-game-side .nm{ font-weight:700; font-size:13.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .sc-game-score{ font-family:"IBM Plex Mono"; font-size:20px; font-weight:700; min-width:34px; text-align:center; }
  .sc-game-mid{ display:flex; flex-direction:column; align-items:center; gap:4px; min-width:90px; }
  .sc-status-pill{ font-size:10.5px; font-weight:700; text-transform:uppercase; padding:3px 9px; border-radius:99px; }
  .sc-status-pill.scheduled{ background:var(--sc-surface2); color:var(--sc-muted); }
  .sc-status-pill.final{ background:var(--sc-surface2); color:var(--sc-muted); }
  .sc-status-pill.in_progress{ background:var(--sc-live-wash); color:var(--sc-live); }
  .sc-empty{ color:var(--sc-muted); padding:30px; text-align:center; }

  @media (max-width: 640px) {
    .sc-toolbar{ flex-wrap:wrap; }
    .sc-title{ width:100%; }
    .sc-game-card{ gap:8px; padding:12px; }
    .sc-game-side{ gap:6px; }
    .sc-game-side .nm{ max-width:56px; font-size:12px; }
    .sc-game-mid{ min-width:56px; }
    .sc-game-score{ font-size:17px; min-width:24px; }
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
</div>
</div>

<script>
const SCORES_WEEK = {{ games|tojson }};
const CURRENT_SEASON = {{ current_season }};
const CURRENT_WEEK = {{ current_week }};
let scSeason = {{ season }};
let scWeek = {{ week }};
const scSeasonType = {{ season_type }};
const scTodayKey = {{ today_key|tojson }};

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

  let selectedDay = (daysIndex[scTodayKey] ? scTodayKey : (Object.keys(daysIndex).sort()[0] || scTodayKey));
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
  function renderDayTabs(){
    dayTabsEl.innerHTML = '';
    const keys = Object.keys(daysIndex).filter(function(k){ return daysIndex[k].length > 0; }).sort();
    keys.forEach(function(key){
      const d = new Date(key + "T00:00:00");
      const isActive = key === selectedDay;
      const isToday = key === scTodayKey;
      const btn = document.createElement('div');
      btn.className = 'sc-day-tab' + (isActive ? ' active' : '') + (isToday ? ' today' : '');
      btn.dataset.dateKey = key;
      const games = daysIndex[key];
      const anyLive = games.some(function(g){ return g.status === 'in_progress'; });
      btn.innerHTML =
        '<span class="dow">' + d.toLocaleDateString(undefined, { weekday: 'short' }) + '</span>' +
        '<span class="dnum">' + d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + '</span>' +
        '<span class="dot" style="background:' + (anyLive ? 'var(--sc-live)' : 'var(--sc-muted)') + '"></span>';
      btn.addEventListener('click', function(){ selectDay(key); });
      dayTabsEl.appendChild(btn);
    });
    const activeEl = dayTabsEl.querySelector('.sc-day-tab.active');
    if (activeEl && typeof activeEl.scrollIntoView === 'function') {
      activeEl.scrollIntoView({ inline: 'center', block: 'nearest' });
    }
  }

  function renderGames(){
    const games = daysIndex[selectedDay] || [];
    gamesEl.innerHTML = '';
    if(!games.length){
      gamesEl.innerHTML = '<div class="sc-empty">No games this day.</div>';
      return;
    }
    games.forEach(function(g){
      const a = document.createElement('a');
      a.className = 'sc-game-card';
      a.href = '/game?id=' + encodeURIComponent(g.id);
      const topRow =
        '<div class="sc-game-top">' +
        '<div class="sc-game-side"><img src="' + (g.away.logo||'') + '" onerror="this.style.visibility=\\'hidden\\'"><span class="nm">' + g.away.name + '</span><span class="sc-game-score">' + (g.away.score ?? '') + '</span></div>' +
        '<div class="sc-game-mid"><span class="sc-status-pill ' + g.status + '">' + (g.status === 'in_progress' ? (g.clock||'') + ' Q' + (g.period||'') : (g.status_detail || g.status)) + '</span></div>' +
        '<div class="sc-game-side" style="justify-content:flex-end; text-align:right;"><span class="sc-game-score">' + (g.home.score ?? '') + '</span><span class="nm">' + g.home.name + '</span><img src="' + (g.home.logo||'') + '" onerror="this.style.visibility=\\'hidden\\'"></div>' +
        '</div>';
      // Compact count-only pills here on purpose -- the full name-by-name
      // breakdown lives on /game (see the chip grid there). A card in a
      // list of a dozen games has no room for a wall of comma-separated
      // names without either truncating illegibly or blowing out the
      // row height, so the card just answers "how many", and clicking
      // through answers "who".
      const awayMine = g.my_away_players || [];
      const homeMine = g.my_home_players || [];
      let myRow = '';
      if (awayMine.length || homeMine.length) {
        myRow = '<div class="sc-my-players">' +
          (awayMine.length ? '<span class="sc-my-players-pill away">' + awayMine.length + ' of yours</span>' : '<span></span>') +
          (homeMine.length ? '<span class="sc-my-players-pill home">' + homeMine.length + ' of yours</span>' : '<span></span>') +
          '</div>';
      }
      a.innerHTML = topRow + myRow;
      gamesEl.appendChild(a);
    });
  }

  function selectDay(key){
    selectedDay = key;
    if(!daysIndex[key]){
      fetch('/api/scoreboard?date=' + key.replace(/-/g, ''))
        .then(function(r){ return r.json(); })
        .then(function(data){
          daysIndex[key] = data.games || [];
          renderDayTabs();
          renderGames();
          renderMonth();
        })
        .catch(function(){ daysIndex[key] = []; renderGames(); });
      return;
    }
    renderDayTabs();
    renderGames();
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

  renderDayTabs();
  renderGames();
  renderMonth();
})();
</script>
"""

GAME_DETAIL_HTML = BASE_STYLE + make_header("scores") + """
<style>
  .gd-header{ display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
  .gd-side{ display:flex; align-items:center; gap:14px; }
  .gd-side img{ width:56px; height:56px; object-fit:contain; }
  .gd-side .nm{ font-family:"Big Shoulders Display"; font-size:20px; font-weight:800; text-transform:uppercase; }
  .gd-score{ font-family:"IBM Plex Mono"; font-size:40px; font-weight:700; }
  .gd-mid{ display:flex; flex-direction:column; align-items:center; gap:6px; }
  .gd-status{ font-size:12px; font-weight:700; text-transform:uppercase; padding:4px 12px; border-radius:99px; background:var(--paper-sunken); color:var(--ink-muted); }
  .gd-status.in_progress{ background:var(--warning-wash); color:var(--warning); }
  .gd-venue{ color:var(--ink-secondary); font-size:13px; margin-top:6px; }
  .gd-officials{ display:flex; flex-wrap:wrap; gap:10px 24px; margin-top:10px; }
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
  .gd-player-chip{ display:inline-flex; align-items:center; gap:6px; background:var(--paper-sunken); border-radius:99px; padding:4px 10px 4px 5px; font-size:12.5px; white-space:nowrap; }
  .gd-player-chip-n{ color:var(--ink-muted); font-size:11px; }
  @media (max-width: 480px) {
    .gd-my-players-group{ flex-direction:column; gap:4px; }
    .gd-my-players-team{ width:auto; }
  }
</style>
<main><div class="wrap">
  <a href="/scores" class="muted">&larr; Back to scores</a>
  {% if load_error %}<div class="error">Couldn't load this game right now: {{ load_error }}</div>{% endif %}
  <div class="panel">
    <div class="gd-header">
      <div class="gd-side">
        <img src="{{ detail.away.logo or '' }}" alt="" onerror="this.style.visibility='hidden'">
        <div><div class="nm">{{ detail.away.name }}</div><div class="gd-score" id="gdAwayScore">{{ detail.away.score or 0 }}</div>{% if detail.away.record %}<span class="muted mono" style="font-size:11px;">{{ detail.away.record }}</span>{% endif %}</div>
      </div>
      <div class="gd-mid">
        <span class="gd-status {{ detail.status }}" id="gdStatus">{{ detail.status_detail or detail.status }}</span>
        <span class="muted" id="gdClock">{% if detail.status == 'in_progress' %}{{ detail.clock }} &middot; Q{{ detail.period }}{% endif %}</span>
      </div>
      <div class="gd-side" style="flex-direction:row-reverse; text-align:right;">
        <img src="{{ detail.home.logo or '' }}" alt="" onerror="this.style.visibility='hidden'">
        <div><div class="nm">{{ detail.home.name }}</div><div class="gd-score" id="gdHomeScore">{{ detail.home.score or 0 }}</div>{% if detail.home.record %}<span class="muted mono" style="font-size:11px;">{{ detail.home.record }}</span>{% endif %}</div>
      </div>
    </div>
    {% if detail.venue.name %}
    <div class="gd-venue">{{ detail.venue.name }}{% if detail.venue.city %} &middot; {{ detail.venue.city }}{% if detail.venue.state %}, {{ detail.venue.state }}{% endif %}{% endif %}</div>
    {% endif %}
    {% if detail.officials %}
    <div class="gd-officials">
      {% for o in detail.officials %}
      <span class="gd-official">{% if o.position %}<span class="muted">{{ o.position }}:</span>{% endif %} <b>{{ o.name }}</b></span>
      {% endfor %}
    </div>
    {% endif %}
    {% if detail.status == 'scheduled' and (detail.broadcasts or detail.odds) %}
    <div class="gd-pregame">
      {% if detail.broadcasts %}<span>&#128250; {{ detail.broadcasts|join(', ') }}</span>{% endif %}
      {% if detail.odds and detail.odds.spread %}<span>{{ detail.odds.spread }}{% if detail.odds.over_under %} &middot; O/U {{ detail.odds.over_under }}{% endif %}</span>{% endif %}
    </div>
    {% endif %}
    {% if detail.my_players %}
    <div class="gd-my-players">
      <p class="eyebrow">Your Players In This Game</p>
      {% for side_label, players in [(detail.away.abbr, detail.my_players.away), (detail.home.abbr, detail.my_players.home)] %}
        {% if players %}
        <div class="gd-my-players-group">
          <span class="gd-my-players-team">{{ side_label }}</span>
          <div class="gd-my-players-chips">
            {% for p in players %}
            <span class="gd-player-chip" title="{{ p.leagues|join(', ') if p.leagues else '' }}">
              <span class="pos-chip" style="background:var(--pos-{{ p.position|lower }}, var(--ink-muted));">{{ p.position }}</span>{{ p.name }}{% if p.leagues and p.leagues|length > 1 %}<span class="gd-player-chip-n">&times;{{ p.leagues|length }}</span>{% endif %}
            </span>
            {% endfor %}
          </div>
        </div>
        {% endif %}
      {% endfor %}
    </div>
    {% endif %}
  </div>

  {% if detail.win_prob %}
  <div class="panel" id="gdWinProbPanel">
    <p class="eyebrow">Win Probability</p>
    <div class="gd-wp-bar">
      <div id="gdWpAwayBar" style="width:{{ detail.win_prob.away_pct }}%; background:var(--pos-wr);"></div>
      <div id="gdWpHomeBar" style="width:{{ detail.win_prob.home_pct }}%; background:var(--pos-rb);"></div>
    </div>
    <div style="display:flex; justify-content:space-between; margin-top:6px; font-size:12.5px;">
      <span>{{ detail.away.abbr }} <span id="gdWpAwayPct">{{ detail.win_prob.away_pct }}</span>%</span>
      <span>{{ detail.home.abbr }} <span id="gdWpHomePct">{{ detail.win_prob.home_pct }}</span>%</span>
    </div>
  </div>
  {% endif %}

  {% if detail.away.linescores and detail.home.linescores %}
  <div class="panel" id="gdLinescorePanel">
    <p class="eyebrow">Score by Quarter</p>
    <table class="rank-table" style="margin-top:6px;" id="gdLinescoreTable">
      <tr><th></th>{% for i in range(detail.away.linescores|length) %}<th>Q{{ i+1 }}</th>{% endfor %}<th>Final</th></tr>
      <tr data-side="away"><td>{{ detail.away.abbr }}</td>{% for v in detail.away.linescores %}<td class="mono">{{ v if v is not none else '-' }}</td>{% endfor %}<td class="mono" data-final>{{ detail.away.score }}</td></tr>
      <tr data-side="home"><td>{{ detail.home.abbr }}</td>{% for v in detail.home.linescores %}<td class="mono">{{ v if v is not none else '-' }}</td>{% endfor %}<td class="mono" data-final>{{ detail.home.score }}</td></tr>
    </table>
  </div>
  {% endif %}

  {% if detail.team_stats %}
  <div class="panel" id="gdStatsPanel">
    <p class="eyebrow">Team Stats</p>
    <div class="gd-stat-row" style="font-weight:700;">
      <span class="gd-stat-val away">{{ detail.away.abbr }}</span>
      <span></span>
      <span class="gd-stat-val home">{{ detail.home.abbr }}</span>
    </div>
    {% for s in detail.team_stats %}
    <div class="gd-stat-row">
      <span class="gd-stat-val away">{{ s.away }}</span>
      <span class="gd-stat-label">{{ s.label }}</span>
      <span class="gd-stat-val home">{{ s.home }}</span>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {% if detail.player_leaders %}
  <div class="panel">
    <p class="eyebrow">Top Performers</p>
    {% for l in detail.player_leaders %}
    <div class="player-row">
      <div class="pname-row"><span class="pos-chip" style="background:var(--paper-sunken); color:var(--ink-secondary);">{{ l.team }}</span> <span style="margin-left:8px;">{{ l.athlete }} &middot; <span class="muted">{{ l.category }}</span></span></div>
      <span class="mono">{{ l.stat_line }}</span>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {% if detail.status == 'scheduled' and not detail.team_stats and not detail.player_leaders %}
  <p class="muted" style="margin-top:14px; text-align:center;">Full box score and stats will appear here once the game kicks off.</p>
  {% endif %}
</div></main>
<script>
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

        // Win probability shifts play by play -- patch it if the panel is
        // already on the page (it only renders when win_prob was present
        // at initial page load).
        if (data.win_prob) {
          const wpAwayBar = document.getElementById('gdWpAwayBar');
          const wpHomeBar = document.getElementById('gdWpHomeBar');
          if (wpAwayBar && wpHomeBar) {
            wpAwayBar.style.width = data.win_prob.away_pct + '%';
            wpHomeBar.style.width = data.win_prob.home_pct + '%';
            document.getElementById('gdWpAwayPct').textContent = data.win_prob.away_pct;
            document.getElementById('gdWpHomePct').textContent = data.win_prob.home_pct;
          }
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
    {% if data_status %}
    <p class="muted" style="font-size:11.5px; margin-top:4px;">
      Data status ({{ data_status.last_year }}): {{ data_status.last_year_players_with_stats }} players tracked,
      {{ data_status.last_year_teams_with_any_defense_data }}/32 teams have defense-vs-position data.
      {% if data_status.last_year_players_with_stats == 0 %}
        {% if data_status.stats_sync_in_progress %}<strong style="color:var(--warning);">No {{ data_status.last_year }} stats were on file -- a one-time sync just started automatically. Refresh in a minute or two.</strong>
        {% else %}<strong style="color:var(--critical);">No {{ data_status.last_year }} stats found, and no sync is running -- reload this page to trigger one.</strong>
        {% endif %}
      {% elif data_status.last_year_teams_with_any_defense_data < 32 %}<strong style="color:var(--warning);">Some teams are missing -- likely a team-abbreviation mismatch.</strong>
      {% endif %}
    </p>
    {% if data_status.schedule_last_year_rows is defined %}
    <p class="muted" style="font-size:11.5px; margin-top:2px;">
      nfl_schedule rows -- {{ data_status.last_year }}: {{ data_status.schedule_last_year_weeks }}/18 weeks, {{ data_status.schedule_last_year_rows }} total rows.
      {{ current_season }}: {{ data_status.schedule_current_season_weeks }} weeks, {{ data_status.schedule_current_season_rows }} total rows.
      {% if data_status.schedule_last_year_rows == 0 and data_status.schedule_current_season_rows > 0 %}
        <strong style="color:var(--critical);">{{ data_status.last_year }} has ZERO schedule rows while {{ current_season }} has real data -- any past "successful" schedule sync almost certainly ran against {{ current_season }} (the default when no season is specified), not {{ data_status.last_year }}.
        {% if data_status.schedule_last_year_sync_in_progress %} A sync for {{ data_status.last_year }} is running right now -- reload in a minute.{% else %} No sync for {{ data_status.last_year }} is currently running; reloading this page will trigger one automatically.{% endif %}</strong>
      {% elif data_status.schedule_last_year_rows == 0 %}
        <strong style="color:var(--warning);">{{ data_status.last_year }} has zero schedule rows.
        {% if data_status.schedule_last_year_sync_in_progress %}A sync is running right now -- reload in a minute.{% else %}No sync is running; reloading this page should trigger one.{% endif %}</strong>
      {% endif %}
    </p>
    {% if data_status.schedule_last_year_last_sync_result %}
    <p class="muted" style="font-size:11.5px; margin-top:2px;">
      Last {{ data_status.last_year }} background sync attempt: {{ data_status.schedule_last_year_last_sync_result.weeks_synced }}/18 weeks wrote rows.
      {% if data_status.schedule_last_year_last_sync_result.last_error %}<strong style="color:var(--critical);">Error: {{ data_status.schedule_last_year_last_sync_result.last_error }}</strong>{% endif %}
      {% if data_status.schedule_last_year_last_sync_result.zero_row_probe %}<strong style="color:var(--critical);">ESPN probe for week {{ data_status.schedule_last_year_last_sync_result.zero_row_probe.week }}: {{ data_status.schedule_last_year_last_sync_result.zero_row_probe }}</strong>{% endif %}
    </p>
    {% endif %}
    {% endif %}
    {% endif %}
    {% if sample_trace %}
    <div style="margin-top:8px; padding:10px 12px; background:var(--paper-sunken); border-radius:8px; font-family:'IBM Plex Mono'; font-size:11px; white-space:pre-wrap; overflow-x:auto;">{% for t in sample_trace %}Player {{ t.player_sid }} -- current team "{{ t.player_current_team }}" -- checked week {{ t.week_checked }}
  get_schedule_for_team_week({{ data_status.last_year }}, {{ t.week_checked }}, "{{ t.player_current_team }}") -&gt; {{ t.get_schedule_for_team_week_result }}
  schedule rows containing "{{ t.player_current_team }}" (any week): {{ t.schedule_rows_for_this_exact_team_string }}
{% endfor %}</div>
    {% endif %}
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
      <div class="mu-row" data-name="{{ r.name|lower }}" data-pos="{{ r.position }}">
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
        <div class="mu-row"><img src=""><span class="pos-chip" style="background:var(--pos-qb);">QB</span><span class="mu-name">Sample Player DAL</span><span class="mu-opp">vs SF</span><span class="mu-stars"><span class="star-rating"><span class="star-bg">★★★★★</span><span class="star-fg" style="width:95%;">★★★★★</span></span></span><span class="mu-grade ap">A+</span></div>
        <div class="mu-row"><img src=""><span class="pos-chip" style="background:var(--pos-rb);">RB</span><span class="mu-name">Sample Player KC</span><span class="mu-opp">vs BUF</span><span class="mu-stars"><span class="star-rating"><span class="star-bg">★★★★★</span><span class="star-fg" style="width:55%;">★★★★★</span></span></span><span class="mu-grade c">C</span></div>
        <div class="mu-row"><img src=""><span class="pos-chip" style="background:var(--pos-wr);">WR</span><span class="mu-name">Sample Player MIA</span><span class="mu-opp">vs NYJ</span><span class="mu-stars"><span class="star-rating"><span class="star-bg">★★★★★</span><span class="star-fg" style="width:15%;">★★★★★</span></span></span><span class="mu-grade dm">D-</span></div>
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
    const lastYear = p.def_rank_last_year
      ? '<div class="h2h-stat-row"><span class="muted">Defense vs pos, last year</span><span>' + p.def_fpts_allowed_pg_last_year + ' pts/gm (rank ' + p.def_rank_last_year + ')</span></div>'
      : '';
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
      '<div class="h2h-stat-row"><span class="muted">Defense vs pos, this year' + (p.def_source === 'current_thin' ? ' (early sample)' : '') + '</span><span>' + (p.def_fpts_allowed_pg != null ? p.def_fpts_allowed_pg + ' pts/gm (rank ' + p.def_rank + ')' : '—') + '</span></div>' +
      lastYear +
      '<div class="h2h-stat-row"><span class="muted">Season avg</span><span>' + p.season_avg + ' pts</span></div>' +
      '<div class="h2h-stat-row"><span class="muted">Last 4 wks avg</span><span>' + p.recent_avg + ' pts</span></div>' +
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
  .rk-select{ background:var(--rk-surface); border:1px solid var(--rk-line); color:var(--rk-text); border-radius:8px; padding:9px 12px; font-size:13.5px; font-weight:600; font-family:inherit; }
  .rk-format-toggle{ display:flex; gap:6px; }
  .rk-format-toggle a{ font-size:12px; font-weight:700; padding:7px 12px; border-radius:99px; border:1px solid var(--rk-line); text-decoration:none; color:var(--rk-muted); }
  .rk-format-toggle a.active{ background:var(--accent); color:var(--accent-on); border-color:var(--accent); }
  .rk-toggle-group{ display:flex; align-items:center; gap:8px; }
  .rk-glabel{ font-size:10.5px; color:var(--rk-muted); font-weight:700; text-transform:uppercase; letter-spacing:0.04em; }
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
    <div class="rk-toggle-group">
      <span class="rk-glabel">Mode</span>
      <div class="rk-format-toggle">
        <a class="{{ 'active' if mode=='dynasty' else '' }}" href="/rankings?format={{ fmt }}&mode=dynasty&pos={{ pos_filter }}&view={{ view }}">Dynasty</a>
        <a class="{{ 'active' if mode=='redraft' else '' }}" href="/rankings?format={{ fmt }}&mode=redraft&pos={{ pos_filter }}&view={{ view }}">Redraft</a>
      </div>
    </div>
    <select class="rk-select" id="posSelect">
      <option value="overall">Overall</option>
      <option value="QB">QB</option>
      <option value="RB">RB</option>
      <option value="WR">WR</option>
      <option value="TE">TE</option>
    </select>
    <div class="rk-toggle-group">
      <span class="rk-glabel">Format</span>
      <div class="rk-format-toggle">
        <a class="{{ 'active' if fmt=='1qb' else '' }}" href="/rankings?format=1qb&mode={{ mode }}&pos={{ pos_filter }}&view={{ view }}">1QB</a>
        <a class="{{ 'active' if fmt=='superflex' else '' }}" href="/rankings?format=superflex&mode={{ mode }}&pos={{ pos_filter }}&view={{ view }}">Superflex</a>
      </div>
    </div>
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
