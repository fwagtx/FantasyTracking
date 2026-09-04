"""
Dynasty League Explorer
--------------------------
Public site: type any Sleeper username, see every dynasty league, full
standings, and a Flock-Fantasy-style positional value breakdown for every
team using real dynasty trade values from FantasyCalc's public API.
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

import os
import random
import time
import urllib.parse
from datetime import date
import requests
from flask import Flask, request, session, redirect, render_template_string, jsonify

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SITE_PASSWORD = os.environ["SITE_PASSWORD"]
MY_USERNAME = os.environ.get("SLEEPER_USERNAME", "")
SEASON = os.environ.get("SEASON", "2026")
GEMINI_MODEL = "gemini-2.5-flash"

SLEEPER_BASE = "https://api.sleeper.app/v1"
FANTASYCALC_BASE = "https://api.fantasycalc.com/values/current"
ADP_BASE = "https://fantasyfootballcalculator.com/api/v1/adp/standard"
POSITIONS = ["QB", "RB", "WR", "TE"]

PICK_ICON = "data:image/svg+xml;utf8," + urllib.parse.quote(
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
    '<rect width="40" height="40" rx="9" fill="#b97a1f"/>'
    '<text x="20" y="25" font-size="12" text-anchor="middle" fill="white" '
    'font-family="monospace" font-weight="bold">PICK</text></svg>'
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-me-" + SITE_PASSWORD)

# In-memory community vote tally -- resets on restart. See note in the
# system prompt reply: add a small free external store (e.g. Upstash
# Redis) if you want this to persist permanently across restarts/visits.
vote_counts = {}


def pick_similar_trio(num_qbs=1, is_dynasty=True):
    """3 players clustered near the same dynasty value, for the KTC-style
    Keep/Trade/Cut widget."""
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


def player_photo_url(sid):
    return f"https://sleepercdn.com/content/nfl/players/{sid}.jpg"

# ---------------- Sleeper helpers ----------------

def get_user_id(username):
    r = requests.get(f"{SLEEPER_BASE}/user/{username}")
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"No Sleeper user found named '{username}'")
    return data["user_id"], data.get("display_name", username)


def get_leagues(user_id, season):
    r = requests.get(f"{SLEEPER_BASE}/user/{user_id}/leagues/nfl/{season}")
    r.raise_for_status()
    return r.json()


def get_rosters(league_id):
    r = requests.get(f"{SLEEPER_BASE}/league/{league_id}/rosters")
    r.raise_for_status()
    return r.json()


def get_league_users(league_id):
    r = requests.get(f"{SLEEPER_BASE}/league/{league_id}/users")
    r.raise_for_status()
    return r.json()


def get_all_players(cache={}):
    if "players" not in cache:
        r = requests.get(f"{SLEEPER_BASE}/players/nfl")
        r.raise_for_status()
        cache["players"] = r.json()
    return cache["players"]

# ---------------- FantasyCalc (real dynasty/redraft trade values, incl. picks) ----------------

def get_fantasycalc_values(num_qbs, is_dynasty=True, cache={}):
    """Returns {"players": {sleeper_id: {...}}, "picks": {pick_id: {...}}},
    cached 1hr per (format, dynasty-vs-redraft)."""
    key = (num_qbs, is_dynasty)
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 3600:
        return entry["data"]

    r = requests.get(FANTASYCALC_BASE, params={
        "isDynasty": "true" if is_dynasty else "false",
        "numQbs": num_qbs, "numTeams": 12, "ppr": 1,
    })
    r.raise_for_status()
    players, picks = {}, {}
    for item in r.json():
        player = item.get("player", {})
        sid = player.get("sleeperId")
        pos = player.get("position")
        if sid:
            players[str(sid)] = {
                "value": item.get("value", 0),
                "position_rank": item.get("positionRank"),
                "overall_rank": item.get("overallRank"),
                "position": pos,
                "redraft_value": item.get("redraftValue"),
                "trend_30day": item.get("trend30Day"),
            }
        elif pos not in POSITIONS:
            # No sleeperId and not a real offensive position -> a draft pick
            # or similar non-Sleeper asset FantasyCalc tracks.
            pid = f"pick_{player.get('id')}"
            name = player.get("name")
            if name:
                picks[pid] = {"name": name, "value": item.get("value", 0), "overall_rank": item.get("overallRank")}

    data = {"players": players, "picks": picks}
    cache[key] = {"data": data, "time": now}
    return data


def get_adp_data(cache={}):
    """Returns {"by_key": {(name_lower, POS): adp}, "by_name_only": {name_lower: adp}}.
    Matching on (name, position) avoids mixing up real players who share a
    name (there's more than one NFL "Josh Allen", for instance)."""
    now = time.time()
    if "data" in cache and now - cache.get("time", 0) < 3600:
        return cache["data"]
    try:
        r = requests.get(ADP_BASE, params={"teams": 12, "year": SEASON}, timeout=15)
        r.raise_for_status()
        players = r.json().get("players", [])
        by_key, by_name_only = {}, {}
        for p in players:
            nm = p.get("name", "").strip().lower()
            pos = (p.get("position") or "").strip().upper()
            adp = p.get("adp")
            if not nm or adp is None:
                continue
            by_key[(nm, pos)] = adp
            by_name_only.setdefault(nm, adp)
        data = {"by_key": by_key, "by_name_only": by_name_only}
        cache["data"] = data
        cache["time"] = now
        return data
    except Exception:
        return {"by_key": {}, "by_name_only": {}}


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


def get_season_stats(season, cache={}):
    """player_id -> {games, fpts} aggregated across all weeks of a real
    NFL season, from Sleeper's public (if lightly-documented) stats
    endpoint. Cached 1hr. Best-effort -- skips any week that errors."""
    now = time.time()
    entry = cache.get(season)
    if entry and now - entry["time"] < 3600:
        return entry["data"]

    agg = {}
    for week in range(1, 19):
        try:
            r = requests.get(f"{SLEEPER_BASE}/stats/nfl/regular/{season}/{week}", timeout=15)
            if r.status_code != 200:
                continue
            week_data = r.json()
            if not isinstance(week_data, dict):
                continue
            for pid, stats in week_data.items():
                if not isinstance(stats, dict):
                    continue
                pts = stats.get("pts_ppr")
                if pts is None:
                    continue
                p_entry = agg.setdefault(pid, {"games": 0, "fpts": 0.0, "weeks": {}})
                p_entry["games"] += 1
                p_entry["fpts"] += pts
                p_entry["weeks"][week] = round(pts, 1)
        except Exception:
            continue

    cache[season] = {"data": agg, "time": now}
    return agg


def rank_tier(rank):
    if rank is None:
        return "flat"
    if rank <= 12:
        return "good"
    if rank <= 36:
        return "warning"
    return "critical"


def team_power_tier(rank_index, n_teams):
    """Flock-style label based on where a team's total dynasty value ranks
    within its own league (0 = strongest)."""
    frac = rank_index / max(n_teams - 1, 1)
    if frac <= 0.15:
        return "Juggernaut", "tier-juggernaut"
    if frac <= 0.4:
        return "Strong Contender", "tier-contender"
    if frac <= 0.65:
        return "Balanced", "tier-balanced"
    if frac <= 0.85:
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


def build_league_teams(league_id, league, all_players, league_users, user_id):
    fc = get_fantasycalc_values(league_num_qbs(league))
    fc_players = fc["players"]
    rosters = get_rosters(league_id)

    team_infos = []
    for r in rosters:
        settings = r.get("settings", {})
        positions = roster_positions(r, all_players)
        pos_value = {}
        for pos in POSITIONS:
            pos_value[pos] = sum(fc_players.get(pid, {}).get("value", 0) for pid, _ in positions.get(pos, []))
        team_infos.append({
            "roster_id": r["roster_id"],
            "owner_name": league_users.get(r.get("owner_id"), "Unknown"),
            "wins": settings.get("wins", 0), "losses": settings.get("losses", 0),
            "is_you": r.get("owner_id") == user_id,
            "positions": positions,
            "pos_value": pos_value,
        })

    for pos in POSITIONS:
        ordered = sorted(team_infos, key=lambda t: -t["pos_value"][pos])
        for i, t in enumerate(ordered):
            t.setdefault("pos_rank", {})[pos] = i + 1

    for t in team_infos:
        total_val = sum(t["pos_value"].values()) or 1
        t["bar"] = [(pos, round(100 * t["pos_value"][pos] / total_val, 1), t["pos_rank"][pos]) for pos in POSITIONS]

    team_infos.sort(key=lambda t: (-t["wins"], -sum(t["pos_value"].values())))
    for i, t in enumerate(team_infos):
        t["power_tier"], t["power_tier_class"] = team_power_tier(i, len(team_infos))
    return team_infos


def build_leagues_for_user(username, cache={}):
    key = username.lower()
    now = time.time()
    entry = cache.get(key)
    if entry and now - entry["time"] < 600:
        return entry["data"]

    all_players = get_all_players()
    user_id, display_name = get_user_id(username)
    leagues = get_leagues(user_id, SEASON)

    result = []
    for league in leagues:
        league_users = {u["user_id"]: u.get("display_name", "?") for u in get_league_users(league["league_id"])}
        teams = build_league_teams(league["league_id"], league, all_players, league_users, user_id)
        result.append({
            "league_id": league["league_id"],
            "league_name": league.get("name", "Unnamed League"),
            "num_qbs": league_num_qbs(league),
            "teams": teams,
        })

    data = {"display_name": display_name, "user_id": user_id, "leagues": result}
    cache[key] = {"data": data, "time": now}
    return data


def build_league_detail(league_id, username, roster_id=None):
    all_players = get_all_players()
    user_id, display_name = get_user_id(username)
    leagues = get_leagues(user_id, SEASON)
    league = next((l for l in leagues if l["league_id"] == league_id), None)
    if league is None:
        league = {"league_id": league_id, "name": "League", "roster_positions": []}

    league_users = {u["user_id"]: u.get("display_name", "?") for u in get_league_users(league_id)}
    teams = build_league_teams(league_id, league, all_players, league_users, user_id)
    fc_players = get_fantasycalc_values(league_num_qbs(league))["players"]

    target = None
    if roster_id is not None:
        target = next((t for t in teams if t["roster_id"] == roster_id), None)
    if target is None:
        target = next((t for t in teams if t["is_you"]), None)
    if target is None:
        target = teams[0] if teams else None
    if target is None:
        raise ValueError("No teams found in this league.")

    num_qbs = league_num_qbs(league)
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
        "league_name": league.get("name", "League"), "owner_name": target["owner_name"],
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

@app.route("/")
def home():
    username = request.args.get("u", "").strip()
    error = None
    data = None
    if username:
        try:
            data = build_leagues_for_user(username)
        except Exception as e:
            error = str(e)
    return render_template_string(HOME_HTML, username=username, data=data, error=error)


@app.route("/league")
def league_detail():
    league_id = request.args.get("league_id", "")
    username = request.args.get("u", "")
    roster_id = request.args.get("roster_id", type=int)
    try:
        detail = build_league_detail(league_id, username, roster_id)
        return render_template_string(LEAGUE_DETAIL_HTML, detail=detail, username=username, league_id=league_id)
    except Exception as e:
        return f"Error: {e}", 500


@app.route("/player")
def player_detail():
    sid = request.args.get("sid", "")
    num_qbs = request.args.get("numqbs", default=1, type=int)
    username = request.args.get("u", "")
    tab = request.args.get("tab", "general")
    all_players = get_all_players()
    p = all_players.get(sid)
    if not p:
        return "Player not found", 404
    fc_players = get_fantasycalc_values(num_qbs)["players"]
    v = fc_players.get(sid, {})
    full_name = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
    adp_data = get_adp_data()

    current_season = int(SEASON)
    # Real career range, derived from the player's actual years of NFL
    # experience -- capped so an extreme-tenure veteran (20+ year kicker)
    # doesn't trigger hundreds of requests on the very first page load.
    # Once a season's data is fetched it's cached and shared across every
    # player's page, so this cost is paid once, not per-visitor.
    MAX_CAREER_SEASONS = 15
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

    this_season_stats = get_season_stats(str(season)).get(sid, {"games": 0, "fpts": 0.0, "weeks": {}})
    weekly = [{"week": w, "pts": this_season_stats.get("weeks", {}).get(w, 0)} for w in range(1, 19)]
    max_pts = max([w["pts"] for w in weekly] + [1])
    for w in weekly:
        w["pct"] = round(100 * w["pts"] / max_pts, 1) if max_pts else 0

    career_rows = []
    for yr in available_seasons:
        s = get_season_stats(str(yr)).get(sid, {"games": 0, "fpts": 0.0})
        career_rows.append({
            "season": yr, "games": s.get("games", 0),
            "fpts": round(s.get("fpts", 0), 1) if s.get("games") else None,
            "fpts_per_game": round(s.get("fpts", 0) / s["games"], 1) if s.get("games") else None,
        })

    current_ppg = round(this_season_stats["fpts"] / this_season_stats["games"], 1) if this_season_stats.get("games") else None

    adp_key = (full_name.lower(), (p.get("position") or "").upper())
    adp = adp_data["by_key"].get(adp_key) or adp_data["by_name_only"].get(full_name.lower())

    info = {
        "name": full_name, "photo": player_photo_url(sid),
        "position": p.get("position", "?"), "team": p.get("team") or "Free agent",
        "age": compute_age_decimal(p.get("birth_date")) or p.get("age"), "years_exp": p.get("years_exp"), "college": p.get("college"),
        "height": format_height(p.get("height")), "weight": p.get("weight"),
        "status": p.get("status"), "injury_status": p.get("injury_status"),
        "value": v.get("value"), "position_rank": v.get("position_rank"), "overall_rank": v.get("overall_rank"),
        "redraft_value": v.get("redraft_value"), "tier": rank_tier(v.get("overall_rank")),
        "adp": adp, "ppg": current_ppg,
    }
    return render_template_string(
        PLAYER_HTML, p=info, username=username, sid=sid, num_qbs=num_qbs, tab=tab,
        season=season, prev_season=prev_season, next_season=next_season,
        weekly=weekly, career_rows=career_rows,
    )


@app.route("/api/player-search")
def api_player_search():
    q = request.args.get("q", "").strip()
    fmt = request.args.get("format", "1qb")
    mode = request.args.get("mode", "dynasty")
    if not q:
        return jsonify({"results": []})

    num_qbs = 2 if fmt == "superflex" else 1
    is_dynasty = mode != "redraft"
    fc = get_fantasycalc_values(num_qbs, is_dynasty)
    all_players = get_all_players()
    q_low = q.lower()

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
        for pid, pk in fc["picks"].items():
            if q_low in pk["name"].lower():
                results.append({
                    "sid": pid, "name": pk["name"], "position": "PICK", "team": "",
                    "photo": PICK_ICON, "value": pk["value"],
                })

    results.sort(key=lambda r: (not r["name"].lower().startswith(q_low), -(r["value"] or 0), r["name"]))
    return jsonify({"results": results[:10]})


@app.route("/rankings")
def rankings():
    fmt = request.args.get("format", "1qb")
    num_qbs = 2 if fmt == "superflex" else 1
    fc_players = get_fantasycalc_values(num_qbs)["players"]
    all_players = get_all_players()
    season_stats = get_season_stats(SEASON)
    rows = []
    for sid, v in fc_players.items():
        p = all_players.get(sid)
        if not p or v.get("position") not in POSITIONS:
            continue
        stat_line = season_stats.get(sid, {})
        games = stat_line.get("games", 0)
        fpts = stat_line.get("fpts", 0.0)
        rows.append({
            "sid": sid, "photo": player_photo_url(sid),
            "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position": v.get("position"), "team": p.get("team") or "FA",
            "age": compute_age_decimal(p.get("birth_date")),
            "games": games,
            "fpts": round(fpts, 1) if games else None,
            "fpts_per_game": round(fpts / games, 1) if games else None,
            "position_rank": v.get("position_rank"),
            "value": v.get("value", 0), "overall_rank": v.get("overall_rank") or 9999,
        })
    rows.sort(key=lambda r: r["overall_rank"])
    return render_template_string(RANKINGS_HTML, rows=rows[:100], fmt=fmt)


@app.route("/trade-calculator")
def trade_calculator():
    fmt = request.args.get("format", "1qb")
    mode = request.args.get("mode", "dynasty")
    num_qbs = 2 if fmt == "superflex" else 1
    is_dynasty = mode != "redraft"
    side1_ids = [x for x in request.args.get("side1", "").split(",") if x]
    side2_ids = [x for x in request.args.get("side2", "").split(",") if x]

    u = request.args.get("u", "").strip()
    league_id = request.args.get("league_id", "")
    my_roster_id = request.args.get("my_roster_id", type=int)
    other_roster_id = request.args.get("other_roster_id", type=int)

    fc = get_fantasycalc_values(num_qbs, is_dynasty)
    all_players = get_all_players()

    def build_side(ids):
        items, total = [], 0
        for sid in ids:
            if sid.startswith("pick_"):
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
    result = None
    if side1_ids or side2_ids:
        result = {
            "side1_items": side1_items, "side1_total": side1_total,
            "side2_items": side2_items, "side2_total": side2_total,
            "diff": side2_total - side1_total,
        }

    # ---- optional league link ----
    league_link = None
    my_quick, other_quick = [], []
    if u:
        try:
            udata = build_leagues_for_user(u)
            selected_league = next((l for l in udata["leagues"] if l["league_id"] == league_id), None) if league_id else None
            my_team = other_team = None
            other_teams = []
            if selected_league:
                my_team = next(
                    (t for t in selected_league["teams"]
                     if (my_roster_id and t["roster_id"] == my_roster_id) or (not my_roster_id and t["is_you"])),
                    None,
                )
                other_teams = [t for t in selected_league["teams"] if not my_team or t["roster_id"] != my_team["roster_id"]]
                if other_roster_id:
                    other_team = next((t for t in selected_league["teams"] if t["roster_id"] == other_roster_id), None)
                if my_team:
                    my_quick = quick_add_list(my_team, fc["players"])
                if other_team:
                    other_quick = quick_add_list(other_team, fc["players"])
            league_link = {
                "username": u, "leagues": udata["leagues"], "selected_league_id": league_id,
                "my_team": my_team, "other_team": other_team, "other_teams": other_teams,
            }
        except Exception as e:
            league_link = {"error": str(e), "username": u}

    # ---- balance suggestions ----
    suggestions = []
    if result and result["diff"] != 0 and (my_quick or other_quick):
        gap = abs(result["diff"])
        if result["diff"] > 0:
            pool = [p for p in my_quick if p["sid"] not in side1_ids]
        else:
            pool = [p for p in other_quick if p["sid"] not in side2_ids]
        pool_sorted = sorted(pool, key=lambda p: abs((p["value"] or 0) - gap))
        suggestions = pool_sorted[:3]

    return render_template_string(
        TRADE_CALC_HTML, result=result, fmt=fmt, mode=mode,
        side1_ids=",".join(side1_ids), side2_ids=",".join(side2_ids),
        league_link=league_link, my_quick=my_quick, other_quick=other_quick,
        suggestions=suggestions, u=u, league_id=league_id,
        my_roster_id=my_roster_id, other_roster_id=other_roster_id,
    )


@app.route("/api/vote-trio")
def api_vote_trio():
    return jsonify({"players": pick_similar_trio()})


@app.route("/api/submit-vote", methods=["POST"])
def api_submit_vote():
    data = request.get_json(force=True, silent=True) or {}
    for sid, label in data.get("votes", {}).items():
        entry = vote_counts.setdefault(sid, {"start": 0, "bench": 0, "cut": 0})
        if label in entry:
            entry[label] += 1
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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@600;700;800;900&family=Source+Sans+3:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --paper:#f3f4f1; --paper-raised:#ffffff; --paper-sunken:#e9ebe5;
    --ink:#14201b; --ink-secondary:#4c564e; --ink-muted:#808a7e;
    --line:rgba(20,32,27,0.12); --line-strong:rgba(20,32,27,0.22);
    --accent:#b97a1f; --accent-ink:#8a5c15; --accent-on:#fff8ec;
    --good:#0ca30c; --good-wash:#e3f5e1;
    --warning:#b5790f; --warning-wash:#faf0d9;
    --critical:#d03b3b; --critical-wash:#fbe7e5;
    --pos-qb:#1baf7a; --pos-rb:#2a78d6; --pos-wr:#e0397a; --pos-te:#7b5ce0;
    --shadow: 0 1px 2px rgba(20,32,27,0.06), 0 8px 24px -12px rgba(20,32,27,0.18);
  }
  *{ box-sizing:border-box; }
  body{ margin:0; background:var(--paper); color:var(--ink); font-family:"Source Sans 3",system-ui,sans-serif; -webkit-font-smoothing:antialiased; }
  h1,h2,h3{ font-family:"Big Shoulders Display",system-ui,sans-serif; font-weight:800; text-transform:uppercase; letter-spacing:0.01em; margin:0; line-height:0.95; }
  p{ margin:0; line-height:1.6; }
  a{ color:inherit; }
  .mono{ font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; }
  .wrap{ max-width:1060px; margin:0 auto; padding:0 24px; }
  header.site{ position:sticky; top:0; z-index:50; background:color-mix(in srgb, var(--paper-raised) 92%, transparent); backdrop-filter:blur(10px); border-bottom:1px solid var(--line); }
  .nav-row{ display:flex; align-items:center; justify-content:space-between; height:64px; flex-wrap:wrap; gap:8px; }
  .wordmark{ display:flex; align-items:center; gap:9px; text-decoration:none; }
  .wordmark svg{ width:22px; height:22px; }
  .wordmark span{ font-family:"Big Shoulders Display"; font-weight:800; font-size:18px; letter-spacing:0.03em; text-transform:uppercase; }
  nav.links{ display:flex; align-items:center; gap:20px; flex-wrap:wrap; }
  nav.links a{ text-decoration:none; font-size:13.5px; font-weight:600; color:var(--ink-secondary); }
  nav.links a:hover, nav.links a.active{ color:var(--accent-ink); }
  main{ padding: 32px 0 80px; }
  .panel{ background:var(--paper-raised); border:1px solid var(--line); border-radius:16px; box-shadow:var(--shadow); padding:22px 24px; margin-top:18px; }
  .panel h2{ font-size:20px; margin-bottom:2px; }
  .eyebrow{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; font-weight:600; letter-spacing:0.1em; text-transform:uppercase; color:var(--accent-ink); }
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
  .seg-qb{ background:var(--pos-qb); } .seg-rb{ background:var(--pos-rb); }
  .seg-wr{ background:var(--pos-wr); } .seg-te{ background:var(--pos-te); }
  .legend-row{ display:flex; gap:14px; flex-wrap:wrap; margin-top:14px; padding-top:12px; border-top:1px solid var(--line); }
  .legend-item{ display:flex; align-items:center; gap:6px; font-size:11.5px; color:var(--ink-secondary); font-weight:600; }
  .legend-item i{ width:9px; height:9px; border-radius:2px; display:inline-block; }

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
  .pname-row{ display:flex; align-items:center; gap:8px; min-width:0; }
  .pname{ font-weight:600; text-decoration:none; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .pname:hover{ color:var(--accent-ink); text-decoration:underline; }
  .rank-pair{ display:flex; gap:5px; font-family:"IBM Plex Mono"; font-size:11px; flex:none; }
  .rank-pair span{ padding:2px 6px; border-radius:5px; }
  .rank-plain{ color:var(--ink-muted); }
  .rank-badge.good{ background:var(--good-wash); color:var(--good); font-weight:700; }
  .rank-badge.warning{ background:var(--warning-wash); color:var(--warning); font-weight:700; }
  .rank-badge.critical{ background:var(--critical-wash); color:var(--critical); font-weight:700; }
  .rank-badge.flat{ background:var(--paper-sunken); color:var(--ink-muted); font-weight:700; }

  .player-hero{ display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  .player-hero img{ width:72px; height:72px; border-radius:14px; object-fit:cover; background:var(--paper-sunken); }
  .player-hero h2{ font-size:28px; }
  .fact-grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:14px; margin-top:18px; }
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
  .trade-side-label{ font-weight:700; font-size:14px; margin-bottom:8px; }
  .search-wrap{ position:relative; }
  .search-dropdown{ position:absolute; top:100%; left:0; right:0; background:var(--paper-raised); border:1px solid var(--line-strong); border-radius:8px; box-shadow:var(--shadow); z-index:30; max-height:260px; overflow-y:auto; display:none; margin-top:4px; }
  .search-dropdown.open{ display:block; }
  .search-dropdown-item{ display:flex; align-items:center; gap:10px; padding:8px 10px; cursor:pointer; }
  .search-dropdown-item:hover{ background:var(--paper-sunken); }
  .search-dropdown-item img{ width:28px; height:28px; border-radius:50%; object-fit:cover; background:var(--paper-sunken); }
  .chip-list{ display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; min-height:8px; }
  .chip{ display:flex; align-items:center; gap:6px; background:var(--paper-sunken); border-radius:99px; padding:4px 10px 4px 4px; font-size:12.5px; font-weight:600; }
  .chip img{ width:24px; height:24px; border-radius:50%; object-fit:cover; }
  .chip .remove{ cursor:pointer; color:var(--ink-muted); font-weight:800; padding:0 2px; }
  .chip .remove:hover{ color:var(--critical); }
  .trade-total{ margin-top:10px; font-family:"IBM Plex Mono"; font-size:13px; color:var(--ink-secondary); }
  .trade-result{ margin-top:20px; padding-top:18px; border-top:1px solid var(--line); }
  .verdict{ font-family:"Big Shoulders Display"; font-size:26px; font-weight:800; }

  .link-box{ background:var(--paper-sunken); border-radius:10px; padding:14px 16px; margin-top:12px; }
  .quick-add-grid{ display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; max-height:180px; overflow-y:auto; }
  .quick-add-tile{ display:flex; align-items:center; gap:6px; background:var(--paper-raised); border:1px solid var(--line); border-radius:99px; padding:4px 10px 4px 4px; font-size:12px; font-weight:600; cursor:pointer; }
  .quick-add-tile:hover{ border-color:var(--accent); }
  .quick-add-tile img{ width:22px; height:22px; border-radius:50%; object-fit:cover; }
  .league-chip-row{ display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
  .suggestion-row{ display:flex; align-items:center; gap:8px; padding:6px 0; font-size:13px; }
  .suggestion-row img{ width:24px; height:24px; border-radius:50%; object-fit:cover; }

  .vote-overlay{ position:fixed; inset:0; background:rgba(20,32,27,0.55); z-index:100; display:none; align-items:center; justify-content:center; padding:20px; }
  .vote-overlay.open{ display:flex; }
  .vote-modal{ background:var(--paper-raised); border-radius:18px; max-width:640px; width:100%; padding:32px 28px 26px; text-align:center; position:relative; box-shadow:var(--shadow); }
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
  <a class="wordmark" href="/">{LOGO_SVG}<span>Dynasty Explorer</span></a>
  <nav class="links">
    <a class="{cls('league')}" href="/">League Manager</a>
    <a class="{cls('rankings')}" href="/rankings">Rankings</a>
    <a class="{cls('trade')}" href="/trade-calculator">Trade Calculator</a>
    <a class="{cls('mock')}" href="/mock-drafts">Mock Drafts</a>
    <a href="/chat">My Trade Chat</a>
  </nav>
</div></header>
"""

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
  try {
    const resp = await fetch('/api/vote-trio');
    const data = await resp.json();
    if (!data.players || data.players.length < 3) return;
    voteTrio = data.players;
    voteLabels = {};
    const container = document.getElementById('voteCards');
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
    document.getElementById('voteOverlay').classList.add('open');
  } catch (e) { /* fail silently -- never block the page over this */ }
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
    await fetch('/api/submit-vote', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({votes: voteLabels}),
    });
  } catch (e) {}
  closeVoteModal(true);
}

function closeVoteModal(submitted) {
  document.getElementById('voteOverlay').classList.remove('open');
  sessionStorage.setItem('vote_shown', '1');
}

document.addEventListener('DOMContentLoaded', loadVoteModal);
</script>
"""

HOME_HTML = BASE_STYLE + make_header("league") + VOTE_MODAL_HTML + """
<main><div class="wrap">
  <div class="panel">
    <p class="eyebrow">League lookup</p>
    <h2>Find any dynasty manager</h2>
    <form method="get" class="search-row">
      <input type="text" name="u" placeholder="Sleeper username" value="{{ username }}" autofocus>
      <button class="btn" type="submit">Search</button>
    </form>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
  </div>

  {% if data %}
  <p class="muted" style="margin-top:18px;">Showing leagues for <strong style="color:var(--accent-ink);">{{ data.display_name }}</strong> &middot; ranks based on real dynasty trade values</p>
  {% for lg in data.leagues %}
  <div class="panel">
    <div style="display:flex; justify-content:space-between; align-items:baseline;">
      <h2>{{ lg.league_name }}</h2>
      <span class="sample-tag">Live data</span>
    </div>
    {% for t in lg.teams %}
    <div class="team-row">
      <a class="team-name" href="/league?league_id={{ lg.league_id }}&roster_id={{ t.roster_id }}&u={{ username }}">{{ t.owner_name }}{% if t.is_you %} &#9733;{% endif %}</a>
      <span class="tier-badge {{ t.power_tier_class }}">{{ t.power_tier }}</span>
      <span class="wl">{{ t.wins }}-{{ t.losses }}</span>
      <div class="value-bar">
        {% for pos, pct, rank in t.bar %}<span class="seg seg-{{ pos.lower() }}" style="width:{{ pct }}%">{{ rank }}</span>{% endfor %}
      </div>
    </div>
    {% endfor %}
    <div class="legend-row">
      <span class="legend-item"><i style="background:var(--pos-qb)"></i>QB</span>
      <span class="legend-item"><i style="background:var(--pos-rb)"></i>RB</span>
      <span class="legend-item"><i style="background:var(--pos-wr)"></i>WR</span>
      <span class="legend-item"><i style="background:var(--pos-te)"></i>TE</span>
    </div>
    <div class="legend-box">
      <b>How to read this bar:</b> each colored segment is a position group, sized by that team's share of total dynasty value. The number inside each segment is that team's <b>rank at that position</b> vs. everyone else in this league (1 = strongest). Click any manager's name to see their full roster broken down.
    </div>
  </div>
  {% endfor %}
  {% endif %}
</div></main>
"""

LEAGUE_DETAIL_HTML = BASE_STYLE + make_header("league") + """
<main><div class="wrap">
  <a href="/?u={{ username }}" class="muted">&larr; Back to leagues</a>
  <div class="panel">
    <p class="eyebrow">{{ detail.league_name }}</p>
    <h2>{{ detail.owner_name }}'s roster value</h2>

    <div class="team-switcher">
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
            <a class="pname" href="/player?sid={{ p.sleeper_id }}&numqbs={{ detail.num_qbs }}&u={{ username }}">{{ p.name }}</a>
          </div>
          <span class="rank-pair">
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
      <b>How to read this:</b> the badge in each colored header (e.g. "Rank 3") is this team's rank at that position among everyone in the league &mdash; 1 is the strongest. For each player: the small gray number on the left is their rank among all players at their position; the colored badge on the right is their rank among <em>every</em> player league-wide, colored green (top 12), yellow (top 36), or red (below that). Click a player's photo or name for full detail. Values from FantasyCalc.
    </div>
  </div>
</div></main>
"""

PLAYER_HTML = BASE_STYLE + make_header("league") + """
<main><div class="wrap" style="max-width:700px;">
  <a href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=general" class="muted">&larr; Back</a>
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
    </div>

    <div class="format-toggle" style="margin-top:20px;">
      <a class="{{ 'active' if tab=='general' else '' }}" href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=general">General</a>
      <a class="{{ 'active' if tab=='log' else '' }}" href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=log&season={{ season }}">Season Log</a>
      <a class="{{ 'active' if tab=='career' else '' }}" href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=career">Career</a>
    </div>

    {% if tab == 'general' %}
    <div class="legend-box" style="margin-top:18px;">
      {% if p.college %}<div><b>College:</b> {{ p.college }}</div>{% endif %}
      {% if p.height or p.weight %}<div><b>Size:</b> {{ p.height or '\u2014' }}, {{ p.weight or '\u2014' }} lb</div>{% endif %}
      {% if p.years_exp is not none %}<div><b>Years exp.:</b> {{ p.years_exp }}</div>{% endif %}
      {% if p.injury_status %}<div><b>Injury status:</b> {{ p.injury_status }}</div>{% endif %}
    </div>
    {% endif %}

    {% if tab == 'log' %}
    <div style="display:flex; align-items:center; justify-content:space-between; margin-top:18px;">
      {% if prev_season %}<a class="team-chip" href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=log&season={{ prev_season }}">&larr; {{ prev_season }}</a>{% else %}<span></span>{% endif %}
      <strong>{{ season }} season</strong>
      {% if next_season %}<a class="team-chip" href="/player?sid={{ sid }}&numqbs={{ num_qbs }}&u={{ username }}&tab=log&season={{ next_season }}">{{ next_season }} &rarr;</a>{% else %}<span></span>{% endif %}
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
      <tr><th>Season</th><th>Games</th><th>Total FPTS</th><th>FPTS/G</th></tr>
      {% for r in career_rows %}
      <tr>
        <td class="mono">{{ r.season }}</td>
        <td class="mono">{{ r.games if r.games else '\u2014' }}</td>
        <td class="mono">{{ r.fpts if r.fpts is not none else '\u2014' }}</td>
        <td class="mono">{{ r.fpts_per_game if r.fpts_per_game is not none else '\u2014' }}</td>
      </tr>
      {% endfor %}
    </table>
    {% endif %}
  </div>
</div></main>
"""

RANKINGS_HTML = BASE_STYLE + make_header("rankings") + """
<main><div class="wrap">
  <div class="panel">
    <p class="eyebrow">Consensus dynasty rankings</p>
    <h2>Top 100 players</h2>
    <div class="format-toggle" style="margin-top:12px;">
      <a class="{{ 'active' if fmt=='1qb' else '' }}" href="/rankings?format=1qb">1QB</a>
      <a class="{{ 'active' if fmt=='superflex' else '' }}" href="/rankings?format=superflex">Superflex</a>
    </div>
    <table class="rank-table">
      <tr><th>#</th><th>Player</th><th>Pos</th><th>Team</th><th>Age</th><th>Played</th><th>FPTS</th><th>FPTS/G</th><th>Pos Rank</th><th>Value</th></tr>
      {% for r in rows %}
      <tr>
        <td class="mono">{{ r.overall_rank }}</td>
        <td><a class="pname" style="display:flex; align-items:center; gap:8px;" href="/player?sid={{ r.sid }}&numqbs={{ 2 if fmt=='superflex' else 1 }}"><img src="{{ r.photo }}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">{{ r.name }}</a></td>
        <td><span class="pos-chip" style="background:var(--pos-{{ r.position.lower() }});">{{ r.position }}</span></td>
        <td>{{ r.team }}</td>
        <td class="mono">{{ r.age if r.age else '\u2014' }}</td>
        <td class="mono">{{ r.games if r.games else '\u2014' }}</td>
        <td class="mono">{{ r.fpts if r.fpts is not none else '\u2014' }}</td>
        <td class="mono">{{ r.fpts_per_game if r.fpts_per_game is not none else '\u2014' }}</td>
        <td class="mono">{{ r.position_rank or '\u2014' }}</td>
        <td class="mono">{{ r.value }}</td>
      </tr>
      {% endfor %}
    </table>
    <p class="muted" style="margin-top:12px;">Age updates automatically from each player's real birth date. Played/FPTS reflect the {{ rows[0].games and 'current' or 'most recent available' }} regular season. Dynasty value &amp; Pos Rank: FantasyCalc.</p>
  </div>
</div></main>
"""

TRADE_CALC_HTML = BASE_STYLE + make_header("trade") + """
<main><div class="wrap">
  <div class="panel">
    <p class="eyebrow">Real dynasty &amp; redraft values, incl. draft picks &middot; FantasyCalc</p>
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
    </div>

    <div class="link-box">
      {% if not league_link %}
      <form method="get" class="search-row" style="margin-top:0;">
        <input type="hidden" name="format" value="{{ fmt }}"><input type="hidden" name="mode" value="{{ mode }}">
        <input type="text" name="u" placeholder="Link your Sleeper username (optional)">
        <button class="btn" type="submit">Load leagues</button>
      </form>
      {% elif league_link.error %}
      <div class="error">{{ league_link.error }}</div>
      {% elif not league_link.selected_league_id %}
      <p class="muted">Pick a league for <strong>{{ league_link.username }}</strong>:</p>
      <div class="league-chip-row">
        {% for lg in league_link.leagues %}
        <a class="team-chip" href="/trade-calculator?format={{ fmt }}&mode={{ mode }}&u={{ league_link.username }}&league_id={{ lg.league_id }}">{{ lg.league_name }}</a>
        {% endfor %}
      </div>
      {% else %}
      <p class="muted">Playing as <strong style="color:var(--accent-ink);">{{ league_link.my_team.owner_name if league_link.my_team else '?' }}</strong>. Trade with:</p>
      <div class="league-chip-row">
        {% for t in league_link.other_teams %}
        <a class="team-chip {{ 'active' if league_link.other_team and t.roster_id == league_link.other_team.roster_id else '' }}" href="/trade-calculator?format={{ fmt }}&mode={{ mode }}&u={{ league_link.username }}&league_id={{ league_link.selected_league_id }}&other_roster_id={{ t.roster_id }}&side1={{ side1_ids }}&side2={{ side2_ids }}">{{ t.owner_name }}</a>
        {% endfor %}
      </div>
      {% endif %}
    </div>

    <div class="trade-cols">
      <div>
        <div class="trade-side-label">You send</div>
        <div class="search-wrap">
          <input type="text" id="search1" placeholder="Type a player or pick name&hellip;" autocomplete="off">
          <div class="search-dropdown" id="dropdown1"></div>
        </div>
        <div class="chip-list" id="chips1"></div>
        <div class="trade-total" id="total1">Total: 0</div>
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
      <div>
        <div class="trade-side-label">You receive</div>
        <div class="search-wrap">
          <input type="text" id="search2" placeholder="Type a player or pick name&hellip;" autocomplete="off">
          <div class="search-dropdown" id="dropdown2"></div>
        </div>
        <div class="chip-list" id="chips2"></div>
        <div class="trade-total" id="total2">Total: 0</div>
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

    {% if result %}
    <div class="trade-result">
      <p class="verdict" style="color:{{ 'var(--good)' if result.diff >= 0 else 'var(--critical)' }};">
        {{ 'You gain' if result.diff >= 0 else 'You lose' }} {{ result.diff|abs }} pts of value
      </p>
      {% if suggestions %}
      <p class="muted" style="margin-top:10px;">To get this closer to even, consider adding:</p>
      {% for s in suggestions %}
      <div class="suggestion-row"><img src="{{ s.photo }}" onerror="this.style.visibility='hidden'"><span>{{ s.name }}</span><span class="mono muted">({{ s.value }} pts)</span></div>
      {% endfor %}
      {% endif %}
    </div>
    {% endif %}
  </div>
</div></main>

<script>
const fmt = {{ fmt|tojson }};
const mode = {{ mode|tojson }};
const initialSide1 = {{ result.side1_items|tojson if result else '[]' }};
const initialSide2 = {{ result.side2_items|tojson if result else '[]' }};

function setParam(key, val) {
  const url = new URL(window.location.href);
  url.searchParams.set(key, val);
  url.searchParams.set('side1', selected1.map(p => p.sid).join(','));
  url.searchParams.set('side2', selected2.map(p => p.sid).join(','));
  window.location.href = url.toString();
}

let selected1 = initialSide1.map(p => ({sid: p.sid, name: p.name, position: p.position, team: p.team, photo: p.photo, value: p.value}));
let selected2 = initialSide2.map(p => ({sid: p.sid, name: p.name, position: p.position, team: p.team, photo: p.photo, value: p.value}));

function renderChips(side) {
  const list = side === 1 ? selected1 : selected2;
  const container = document.getElementById('chips' + side);
  container.innerHTML = '';
  let total = 0;
  list.forEach(p => {
    total += p.value || 0;
    const chip = document.createElement('div');
    chip.className = 'chip';
    chip.innerHTML = `<img src="${p.photo}" onerror="this.style.visibility='hidden'"><span>${p.name}</span><span class="remove" data-sid="${p.sid}">&times;</span>`;
    chip.querySelector('.remove').onclick = () => removePlayer(side, p.sid);
    container.appendChild(chip);
  });
  document.getElementById('total' + side).textContent = 'Total: ' + total;
}

function removePlayer(side, sid) {
  if (side === 1) selected1 = selected1.filter(p => p.sid !== sid);
  else selected2 = selected2.filter(p => p.sid !== sid);
  renderChips(side);
  recalc();
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
  recalc();
}

function quickAddClick(side, el) {
  addPlayer(side, {
    sid: el.dataset.sid, name: el.dataset.name, position: el.dataset.position,
    team: el.dataset.team, photo: el.dataset.photo, value: parseFloat(el.dataset.value),
  });
}

function recalc() {
  if (selected1.length === 0 && selected2.length === 0) return;
  const url = new URL(window.location.href);
  url.searchParams.set('format', fmt);
  url.searchParams.set('mode', mode);
  url.searchParams.set('side1', selected1.map(p => p.sid).join(','));
  url.searchParams.set('side2', selected2.map(p => p.sid).join(','));
  window.location.href = url.toString();
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
      const resp = await fetch(`/api/player-search?q=${encodeURIComponent(q)}&format=${fmt}&mode=${mode}`);
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
