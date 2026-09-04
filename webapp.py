"""
Dynasty League Explorer
--------------------------
Public site: type any Sleeper username, see every dynasty league, full
standings, and a Flock-Fantasy-style positional value breakdown for every
team in the league (not just your own) using real dynasty trade values
from FantasyCalc's public API. Also includes a real Rankings page, a
functional Trade Calculator, and clickable player detail pages.

Required environment variables:
  GEMINI_API_KEY, SITE_PASSWORD
Optional:
  SLEEPER_USERNAME (prefills your username on the chat page)
"""

import os
import time
import requests
from flask import Flask, request, session, redirect, render_template_string

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SITE_PASSWORD = os.environ["SITE_PASSWORD"]
MY_USERNAME = os.environ.get("SLEEPER_USERNAME", "")
SEASON = os.environ.get("SEASON", "2026")
GEMINI_MODEL = "gemini-2.5-flash"

SLEEPER_BASE = "https://api.sleeper.app/v1"
FANTASYCALC_BASE = "https://api.fantasycalc.com/values/current"
POSITIONS = ["QB", "RB", "WR", "TE"]

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-me-" + SITE_PASSWORD)

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

# ---------------- FantasyCalc (real dynasty trade values) ----------------

def get_fantasycalc_values(num_qbs, cache={}):
    """sleeper_id -> {value, position_rank, overall_rank, position}, cached 1hr per format."""
    now = time.time()
    entry = cache.get(num_qbs)
    if entry and now - entry["time"] < 3600:
        return entry["data"]

    r = requests.get(FANTASYCALC_BASE, params={
        "isDynasty": "true", "numQbs": num_qbs, "numTeams": 12, "ppr": 1,
    })
    r.raise_for_status()
    mapped = {}
    for item in r.json():
        player = item.get("player", {})
        sid = player.get("sleeperId")
        if sid:
            mapped[str(sid)] = {
                "value": item.get("value", 0),
                "position_rank": item.get("positionRank"),
                "overall_rank": item.get("overallRank"),
                "position": player.get("position"),
                "trend_30day": item.get("trend30Day"),
                "redraft_value": item.get("redraftValue"),
            }
    cache[num_qbs] = {"data": mapped, "time": now}
    return mapped


def league_num_qbs(league):
    positions = league.get("roster_positions", []) or []
    if any(p in ("SUPER_FLEX", "SUPERFLEX") for p in positions):
        return 2
    return max(1, positions.count("QB"))


def rank_tier(rank):
    if rank is None:
        return "flat"
    if rank <= 12:
        return "good"
    if rank <= 36:
        return "warning"
    return "critical"


def find_player_by_name(query, all_players):
    """Best-effort name match: exact match first, then substring."""
    q = query.strip().lower()
    if not q:
        return None, None
    for sid, p in all_players.items():
        full = f"{p.get('first_name','')} {p.get('last_name','')}".strip().lower()
        if full == q:
            return sid, p
    for sid, p in all_players.items():
        full = f"{p.get('first_name','')} {p.get('last_name','')}".strip().lower()
        if q in full:
            return sid, p
    return None, None

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


def build_league_teams(league_id, league, all_players, league_users, user_id):
    """All teams in a league with per-position total value + rank 1..N (1=best)."""
    fc_values = get_fantasycalc_values(league_num_qbs(league))
    rosters = get_rosters(league_id)

    team_infos = []
    for r in rosters:
        settings = r.get("settings", {})
        positions = roster_positions(r, all_players)
        pos_value = {}
        for pos in POSITIONS:
            pos_value[pos] = sum(fc_values.get(pid, {}).get("value", 0) for pid, _ in positions.get(pos, []))
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
    return team_infos, fc_values


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
        teams, _ = build_league_teams(league["league_id"], league, all_players, league_users, user_id)
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
    """One team's roster value breakdown -- yours by default, or any
    teammate's if roster_id is given -- plus the full list of teams so the
    page can offer a switcher."""
    all_players = get_all_players()
    user_id, display_name = get_user_id(username)
    leagues = get_leagues(user_id, SEASON)
    league = next((l for l in leagues if l["league_id"] == league_id), None)
    if league is None:
        # user might be looking at a league they're not in; still allow it
        # by fetching league users directly
        league = {"league_id": league_id, "name": "League", "roster_positions": []}

    league_users = {u["user_id"]: u.get("display_name", "?") for u in get_league_users(league_id)}
    teams, fc_values = build_league_teams(league_id, league, all_players, league_users, user_id)

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
            v = fc_values.get(pid, {})
            players.append({
                "sleeper_id": pid, "name": name,
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
        "league_name": league.get("name", "League"),
        "owner_name": target["owner_name"],
        "roster_id": target["roster_id"],
        "columns": columns,
        "num_qbs": num_qbs,
        "teams": team_switcher,
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
    league_id = request.args.get("league_id", "")
    username = request.args.get("u", "")
    all_players = get_all_players()
    p = all_players.get(sid)
    if not p:
        return "Player not found", 404
    fc_values = get_fantasycalc_values(num_qbs)
    v = fc_values.get(sid, {})
    info = {
        "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
        "position": p.get("position", "?"),
        "team": p.get("team") or "Free agent",
        "age": p.get("age"),
        "years_exp": p.get("years_exp"),
        "college": p.get("college"),
        "height": p.get("height"),
        "weight": p.get("weight"),
        "status": p.get("status"),
        "injury_status": p.get("injury_status"),
        "value": v.get("value"),
        "position_rank": v.get("position_rank"),
        "overall_rank": v.get("overall_rank"),
        "redraft_value": v.get("redraft_value"),
        "tier": rank_tier(v.get("overall_rank")),
    }
    return render_template_string(PLAYER_HTML, p=info, league_id=league_id, username=username)


@app.route("/rankings")
def rankings():
    fmt = request.args.get("format", "1qb")
    num_qbs = 2 if fmt == "superflex" else 1
    fc_values = get_fantasycalc_values(num_qbs)
    all_players = get_all_players()
    rows = []
    for sid, v in fc_values.items():
        p = all_players.get(sid)
        if not p or v.get("position") not in POSITIONS:
            continue
        rows.append({
            "sid": sid,
            "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position": v.get("position"), "team": p.get("team") or "FA",
            "value": v.get("value", 0), "overall_rank": v.get("overall_rank") or 9999,
        })
    rows.sort(key=lambda r: r["overall_rank"])
    return render_template_string(RANKINGS_HTML, rows=rows[:100], fmt=fmt)


@app.route("/trade-calculator", methods=["GET", "POST"])
def trade_calculator():
    fmt = request.values.get("format", "1qb")
    num_qbs = 2 if fmt == "superflex" else 1
    send_text = request.values.get("send", "")
    receive_text = request.values.get("receive", "")
    result = None

    if request.method == "POST":
        fc_values = get_fantasycalc_values(num_qbs)
        all_players = get_all_players()

        def parse_side(text):
            items, total, unmatched = [], 0, []
            for raw in [x.strip() for x in text.split(",") if x.strip()]:
                sid, p = find_player_by_name(raw, all_players)
                if not sid:
                    unmatched.append(raw)
                    continue
                v = fc_values.get(sid, {}).get("value", 0)
                items.append({"name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(), "value": v})
                total += v
            return items, total, unmatched

        send_items, send_total, send_unmatched = parse_side(send_text)
        recv_items, recv_total, recv_unmatched = parse_side(receive_text)
        result = {
            "send_items": send_items, "send_total": send_total, "send_unmatched": send_unmatched,
            "recv_items": recv_items, "recv_total": recv_total, "recv_unmatched": recv_unmatched,
            "diff": recv_total - send_total,
        }
    return render_template_string(TRADE_CALC_HTML, result=result, send_text=send_text, receive_text=receive_text, fmt=fmt)


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
  .wrap{ max-width:980px; margin:0 auto; padding:0 24px; }
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
  .btn-ghost{ background:transparent; color:var(--ink); border-color:var(--line-strong); }
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
  .wl{ font-family:"IBM Plex Mono"; font-size:11.5px; color:var(--ink-secondary); width:44px; flex:none; }
  .value-bar{ flex:1; height:22px; border-radius:6px; overflow:hidden; display:flex; background:var(--paper-sunken); }
  .value-bar .seg{ height:100%; display:flex; align-items:center; justify-content:center; color:#fff; font-family:"IBM Plex Mono"; font-size:11px; font-weight:700; min-width:16px; }
  .seg-qb{ background:var(--pos-qb); } .seg-rb{ background:var(--pos-rb); }
  .seg-wr{ background:var(--pos-wr); } .seg-te{ background:var(--pos-te); }
  .legend-row{ display:flex; gap:14px; flex-wrap:wrap; margin-top:14px; padding-top:12px; border-top:1px solid var(--line); }
  .legend-item{ display:flex; align-items:center; gap:6px; font-size:11.5px; color:var(--ink-secondary); font-weight:600; }
  .legend-item i{ width:9px; height:9px; border-radius:2px; display:inline-block; }
  .view-link{ font-size:12.5px; font-weight:700; color:var(--accent-ink); text-decoration:none; }

  .team-switcher{ display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }
  .team-chip{ font-size:12.5px; font-weight:600; padding:6px 12px; border-radius:99px; border:1px solid var(--line-strong); text-decoration:none; color:var(--ink-secondary); }
  .team-chip.active{ background:var(--ink); color:var(--paper-raised); border-color:var(--ink); }
  .team-chip:hover{ border-color:var(--accent); }

  .col-grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:16px; margin-top:14px; }
  .col-head{ font-family:"IBM Plex Mono"; font-size:11px; font-weight:700; letter-spacing:0.05em; text-transform:uppercase; padding:6px 8px; border-radius:6px; margin-bottom:8px; color:#fff; display:flex; align-items:center; justify-content:space-between; }
  .col-head.qb{ background:var(--pos-qb); } .col-head.rb{ background:var(--pos-rb); }
  .col-head.wr{ background:var(--pos-wr); } .col-head.te{ background:var(--pos-te); }
  .col-head .rank-badge-inline{ background:rgba(255,255,255,0.28); border-radius:5px; padding:2px 7px; font-size:11px; }
  .player-row{ display:flex; justify-content:space-between; align-items:center; padding:7px 4px; border-top:1px solid var(--line); font-size:13px; }
  .player-row:first-of-type{ border-top:none; }
  .pname{ font-weight:600; text-decoration:none; color:var(--ink); }
  .pname:hover{ color:var(--accent-ink); text-decoration:underline; }
  .rank-pair{ display:flex; gap:5px; font-family:"IBM Plex Mono"; font-size:11px; flex:none; }
  .rank-pair span{ padding:2px 6px; border-radius:5px; }
  .rank-plain{ color:var(--ink-muted); }
  .rank-badge.good{ background:var(--good-wash); color:var(--good); font-weight:700; }
  .rank-badge.warning{ background:var(--warning-wash); color:var(--warning); font-weight:700; }
  .rank-badge.critical{ background:var(--critical-wash); color:var(--critical); font-weight:700; }
  .rank-badge.flat{ background:var(--paper-sunken); color:var(--ink-muted); font-weight:700; }

  .player-hero{ display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
  .player-hero h2{ font-size:30px; }
  .fact-grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:14px; margin-top:18px; }
  .fact-tile{ background:var(--paper-sunken); border-radius:10px; padding:12px 14px; }
  .fact-tile b{ display:block; font-family:"Big Shoulders Display"; font-size:22px; font-weight:800; }
  .fact-tile span{ font-size:11.5px; color:var(--ink-secondary); font-weight:600; text-transform:uppercase; letter-spacing:0.04em; }

  table.rank-table{ width:100%; border-collapse:collapse; font-size:14px; margin-top:8px; }
  table.rank-table th{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:0.05em; color:var(--ink-muted); padding:6px 8px; border-bottom:1px solid var(--line-strong); }
  table.rank-table td{ padding:9px 8px; border-bottom:1px solid var(--line); }
  .pos-chip{ font-family:"IBM Plex Mono"; font-size:10.5px; font-weight:700; padding:2px 7px; border-radius:99px; color:#fff; }
  .format-toggle{ display:flex; gap:8px; margin-top:10px; }
  .format-toggle a{ font-size:12.5px; font-weight:700; padding:6px 12px; border-radius:99px; border:1px solid var(--line-strong); text-decoration:none; color:var(--ink-secondary); }
  .format-toggle a.active{ background:var(--ink); color:var(--paper-raised); border-color:var(--ink); }

  .trade-cols{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:14px; }
  textarea{ width:100%; min-height:90px; border:1px solid var(--line-strong); border-radius:8px; padding:10px 12px; font-family:inherit; font-size:14px; resize:vertical; }
  .trade-result{ margin-top:18px; padding-top:16px; border-top:1px solid var(--line); }
  .verdict{ font-family:"Big Shoulders Display"; font-size:24px; font-weight:800; }
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

HOME_HTML = BASE_STYLE + make_header("league") + """
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
          <a class="pname" href="/player?sid={{ p.sleeper_id }}&numqbs={{ detail.num_qbs }}&league_id={{ league_id }}&u={{ username }}">{{ p.name }}</a>
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
      <b>How to read this:</b> the badge in each colored header (e.g. "Rank 3") is this team's rank at that position among everyone in the league &mdash; 1 is the strongest. For each player: the small gray number on the left is their rank among all players at their position; the colored badge on the right is their rank among <em>every</em> player league-wide, colored green (top 12), yellow (top 36), or red (below that). Click any player's name for more detail. Values from FantasyCalc.
    </div>
  </div>
</div></main>
"""

PLAYER_HTML = BASE_STYLE + make_header("league") + """
<main><div class="wrap" style="max-width:640px;">
  <a href="javascript:history.back()" class="muted">&larr; Back</a>
  <div class="panel">
    <span class="pos-chip" style="background:var(--pos-{{ p.position.lower() }});">{{ p.position }}</span>
    <div class="player-hero" style="margin-top:10px;">
      <h2>{{ p.name }}</h2>
      <span class="muted">{{ p.team }}</span>
    </div>
    <div class="fact-grid">
      <div class="fact-tile"><b>{{ p.overall_rank or '\u2014' }}</b><span>Overall rank</span></div>
      <div class="fact-tile"><b>{{ p.position_rank or '\u2014' }}</b><span>{{ p.position }} rank</span></div>
      <div class="fact-tile"><b>{{ p.value or '\u2014' }}</b><span>Dynasty value</span></div>
      {% if p.redraft_value %}<div class="fact-tile"><b>{{ p.redraft_value }}</b><span>Redraft value</span></div>{% endif %}
      {% if p.age %}<div class="fact-tile"><b>{{ p.age }}</b><span>Age</span></div>{% endif %}
      {% if p.years_exp is not none %}<div class="fact-tile"><b>{{ p.years_exp }}</b><span>Years exp.</span></div>{% endif %}
    </div>
    <div class="legend-box" style="margin-top:20px;">
      {% if p.college %}<div><b>College:</b> {{ p.college }}</div>{% endif %}
      {% if p.height or p.weight %}<div><b>Size:</b> {{ p.height or '\u2014' }}, {{ p.weight or '\u2014' }} lb</div>{% endif %}
      {% if p.injury_status %}<div><b>Injury status:</b> {{ p.injury_status }}</div>{% endif %}
      <div style="margin-top:8px;">Dynasty and redraft values come from FantasyCalc's public consensus market. We don't currently pull live ADP across platforms or weekly game logs -- ask if you want that added next.</div>
    </div>
  </div>
</div></main>
"""

RANKINGS_HTML = BASE_STYLE + make_header("rankings") + """
<main><div class="wrap">
  <div class="panel">
    <p class="eyebrow">Consensus dynasty rankings</p>
    <h2>Top 100 players</h2>
    <div class="format-toggle">
      <a class="{{ 'active' if fmt=='1qb' else '' }}" href="/rankings?format=1qb">1QB</a>
      <a class="{{ 'active' if fmt=='superflex' else '' }}" href="/rankings?format=superflex">Superflex</a>
    </div>
    <table class="rank-table">
      <tr><th>#</th><th>Player</th><th>Pos</th><th>Team</th><th>Value</th></tr>
      {% for r in rows %}
      <tr>
        <td class="mono">{{ r.overall_rank }}</td>
        <td><a class="pname" href="/player?sid={{ r.sid }}&numqbs={{ 2 if fmt=='superflex' else 1 }}">{{ r.name }}</a></td>
        <td><span class="pos-chip" style="background:var(--pos-{{ r.position.lower() }});">{{ r.position }}</span></td>
        <td>{{ r.team }}</td>
        <td class="mono">{{ r.value }}</td>
      </tr>
      {% endfor %}
    </table>
  </div>
</div></main>
"""

TRADE_CALC_HTML = BASE_STYLE + make_header("trade") + """
<main><div class="wrap">
  <div class="panel">
    <p class="eyebrow">Real dynasty values &middot; FantasyCalc</p>
    <h2>Trade calculator</h2>
    <div class="format-toggle">
      <a class="{{ 'active' if fmt=='1qb' else '' }}" href="#" onclick="document.getElementById('fmt').value='1qb';document.getElementById('tform').submit();return false;">1QB</a>
      <a class="{{ 'active' if fmt=='superflex' else '' }}" href="#" onclick="document.getElementById('fmt').value='superflex';document.getElementById('tform').submit();return false;">Superflex</a>
    </div>
    <form method="post" id="tform" style="margin-top:14px;">
      <input type="hidden" name="format" id="fmt" value="{{ fmt }}">
      <div class="trade-cols">
        <div>
          <label class="muted">You send (comma-separated names)</label>
          <textarea name="send" placeholder="Ja'Marr Chase, 2027 1st">{{ send_text }}</textarea>
        </div>
        <div>
          <label class="muted">You receive</label>
          <textarea name="receive" placeholder="Amon-Ra St. Brown, Trey McBride">{{ receive_text }}</textarea>
        </div>
      </div>
      <button class="btn" type="submit" style="margin-top:14px;">Calculate</button>
    </form>

    {% if result %}
    <div class="trade-result">
      <div class="trade-cols">
        <div>
          <b>You send</b> <span class="mono muted">({{ result.send_total }} pts)</span>
          {% for i in result.send_items %}<div class="player-row"><span>{{ i.name }}</span><span class="mono">{{ i.value }}</span></div>{% endfor %}
          {% for u in result.send_unmatched %}<div class="error">Couldn't find "{{ u }}"</div>{% endfor %}
        </div>
        <div>
          <b>You receive</b> <span class="mono muted">({{ result.recv_total }} pts)</span>
          {% for i in result.recv_items %}<div class="player-row"><span>{{ i.name }}</span><span class="mono">{{ i.value }}</span></div>{% endfor %}
          {% for u in result.recv_unmatched %}<div class="error">Couldn't find "{{ u }}"</div>{% endfor %}
        </div>
      </div>
      <p class="verdict" style="color:{{ 'var(--good)' if result.diff >= 0 else 'var(--critical)' }};">
        {{ 'You gain' if result.diff >= 0 else 'You lose' }} {{ result.diff|abs }} pts of value
      </p>
      <p class="muted">Draft picks aren't matched by name yet (e.g. "2027 1st") -- only rostered NFL players. Ask if you want picks priced too.</p>
    </div>
    {% endif %}
  </div>
</div></main>
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
