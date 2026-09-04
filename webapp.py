"""
Dynasty League Explorer (Gridline-styled)
--------------------------------------------
Public site: type any Sleeper username, see every dynasty league, full
standings, and every team's roster. Visual design adapted from the
"Gridline" concept (fonts, color tokens, panel/league-row components).

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


def roster_positions(roster, all_players):
    by_pos = {}
    for pid in roster.get("players") or []:
        p = all_players.get(pid)
        if not p:
            continue
        pos = p.get("position", "?")
        name = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        by_pos.setdefault(pos, []).append(name)
    return by_pos


def position_bar(positions):
    """Return [(pos, pct)] for QB/RB/WR/TE based on real roster counts."""
    counts = {pos: len(positions.get(pos, [])) for pos in ["QB", "RB", "WR", "TE"]}
    total = sum(counts.values()) or 1
    return [(pos, round(100 * c / total, 1)) for pos, c in counts.items()]


def status_label(wins, losses):
    games = wins + losses
    if games == 0:
        return ("Season starting", "warning")
    pct = wins / games
    if pct >= 0.6:
        return ("Contending", "good")
    if pct >= 0.4:
        return ("On the bubble", "warning")
    return ("Rebuilding", "critical")


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
        rosters = get_rosters(league["league_id"])
        league_users = {u["user_id"]: u.get("display_name", "?") for u in get_league_users(league["league_id"])}

        teams = []
        for r in rosters:
            settings = r.get("settings", {})
            points = settings.get("fpts", 0) + settings.get("fpts_decimal", 0) / 100
            positions = roster_positions(r, all_players)
            wins, losses = settings.get("wins", 0), settings.get("losses", 0)
            label, tone = status_label(wins, losses)
            teams.append({
                "roster_id": r["roster_id"],
                "owner_name": league_users.get(r.get("owner_id"), "Unknown"),
                "wins": wins, "losses": losses, "points": round(points, 1),
                "is_you": r.get("owner_id") == user_id,
                "positions": positions,
                "bar": position_bar(positions),
                "status_label": label, "status_tone": tone,
            })
        teams.sort(key=lambda t: (-t["wins"], -t["points"]))

        result.append({
            "league_id": league["league_id"],
            "league_name": league.get("name", "Unnamed League"),
            "teams": teams,
        })

    data = {"display_name": display_name, "leagues": result}
    cache[key] = {"data": data, "time": now}
    return data


def build_context_text(username):
    data = build_leagues_for_user(username)
    lines = []
    for lg in data["leagues"]:
        me = next((t for t in lg["teams"] if t["is_you"]), None)
        if not me:
            continue
        parts = [f"{pos}: {', '.join(names)}" for pos, names in me["positions"].items()]
        lines.append(f"League '{lg['league_name']}' ({me['wins']}-{me['losses']}): " + "; ".join(parts))
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


@app.route("/roster")
def roster_detail():
    league_id = request.args.get("league_id", "")
    roster_id = request.args.get("roster_id", type=int)
    username = request.args.get("u", "")
    try:
        rosters = get_rosters(league_id)
        league_users = {u["user_id"]: u.get("display_name", "?") for u in get_league_users(league_id)}
        all_players = get_all_players()
        r = next((r for r in rosters if r["roster_id"] == roster_id), None)
        if not r:
            return "Team not found", 404
        positions = roster_positions(r, all_players)
        team = {"owner_name": league_users.get(r.get("owner_id"), "Unknown"), "positions": positions}
        return render_template_string(ROSTER_HTML, team=team, username=username)
    except Exception as e:
        return f"Error: {e}", 500

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
                    f"Here are the user's current rosters:\n{context}\n\n"
                    f"The user asks: {question}\n\n"
                    "Give direct, specific advice using the actual players on "
                    "their rosters. Be conversational, under 180 words unless "
                    "genuinely more detail is needed."
                )
                answer = ask_gemini(prompt)
            except Exception as e:
                answer = f"Error: {e}"
    return render_template_string(CHAT_HTML, answer=answer, question=question, username=username)

# ---------------- Design system (adapted from Gridline) ----------------

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
    --pos-qb:#2a78d6; --pos-rb:#eb6834; --pos-wr:#1baf7a; --pos-te:#eda100;
    --shadow: 0 1px 2px rgba(20,32,27,0.06), 0 8px 24px -12px rgba(20,32,27,0.18);
  }
  *{ box-sizing:border-box; }
  body{ margin:0; background:var(--paper); color:var(--ink); font-family:"Source Sans 3",system-ui,sans-serif; -webkit-font-smoothing:antialiased; }
  h1,h2{ font-family:"Big Shoulders Display",system-ui,sans-serif; font-weight:800; text-transform:uppercase; letter-spacing:0.01em; margin:0; line-height:0.95; }
  p{ margin:0; line-height:1.6; }
  a{ color:inherit; }
  .mono{ font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; }
  .wrap{ max-width:900px; margin:0 auto; padding:0 24px; }
  header.site{ position:sticky; top:0; z-index:50; background:color-mix(in srgb, var(--paper-raised) 92%, transparent); backdrop-filter:blur(10px); border-bottom:1px solid var(--line); }
  .nav-row{ display:flex; align-items:center; justify-content:space-between; height:68px; }
  .wordmark{ display:flex; align-items:center; gap:9px; text-decoration:none; }
  .wordmark svg{ width:24px; height:24px; }
  .wordmark span{ font-family:"Big Shoulders Display"; font-weight:800; font-size:20px; letter-spacing:0.03em; text-transform:uppercase; }
  nav.links a{ text-decoration:none; font-size:14px; font-weight:600; color:var(--ink-secondary); }
  nav.links a:hover{ color:var(--ink); }
  main{ padding: 36px 0 80px; }
  .panel{ background:var(--paper-raised); border:1px solid var(--line); border-radius:16px; box-shadow:var(--shadow); padding:22px 24px; margin-top:20px; }
  .panel h2{ font-size:20px; margin-bottom:2px; }
  .eyebrow{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; font-weight:600; letter-spacing:0.1em; text-transform:uppercase; color:var(--accent-ink); }
  .search-row{ display:flex; gap:10px; margin-top:14px; }
  input[type=text],input[type=password]{ flex:1; border:1px solid var(--line-strong); border-radius:8px; padding:12px 14px; font-size:15px; font-family:inherit; background:var(--paper-raised); color:var(--ink); }
  .btn{ display:inline-flex; align-items:center; justify-content:center; gap:8px; font-family:"Source Sans 3"; font-weight:700; font-size:15px; border-radius:8px; padding:12px 22px; text-decoration:none; cursor:pointer; border:1px solid transparent; background:var(--accent); color:var(--accent-on); }
  .btn:hover{ opacity:0.92; }
  .error{ color:var(--critical); font-size:14px; margin-top:10px; }
  .sample-tag{ font-family:"IBM Plex Mono"; font-size:10.5px; font-weight:600; letter-spacing:0.06em; text-transform:uppercase; color:var(--ink-muted); background:var(--paper-sunken); border-radius:5px; padding:3px 7px; }

  .league-row{ display:flex; align-items:center; gap:12px; padding:13px 4px; border-top:1px solid var(--line); }
  .league-row:first-of-type{ border-top:none; }
  .league-name{ font-weight:700; font-size:14.5px; width:170px; flex:none; text-decoration:none; color:var(--ink); }
  .league-name:hover{ color:var(--accent-ink); }
  .wl{ font-family:"IBM Plex Mono"; font-size:12.5px; color:var(--ink-secondary); width:52px; flex:none; }
  .roster-bar{ flex:1; height:10px; border-radius:99px; overflow:hidden; display:flex; background:var(--paper-sunken); }
  .roster-bar span{ height:100%; }
  .status-pill{ display:inline-flex; align-items:center; gap:5px; font-size:11.5px; font-weight:700; padding:4px 9px; border-radius:99px; flex:none; }
  .status-pill.good{ background:var(--good-wash); color:var(--good); }
  .status-pill.warning{ background:var(--warning-wash); color:var(--warning); }
  .status-pill.critical{ background:var(--critical-wash); color:var(--critical); }
  .legend-row{ display:flex; gap:14px; flex-wrap:wrap; margin-top:14px; padding-top:12px; border-top:1px solid var(--line); }
  .legend-item{ display:flex; align-items:center; gap:6px; font-size:11.5px; color:var(--ink-secondary); font-weight:600; }
  .legend-item i{ width:9px; height:9px; border-radius:2px; display:inline-block; }

  .tier-row{ display:flex; align-items:center; gap:12px; padding:10px 0; border-top:1px solid var(--line); }
  .tier-row:first-of-type{ border-top:none; }
  .pos-dot{ width:9px; height:9px; border-radius:2px; flex:none; }
  .tier-row .pname{ font-weight:600; font-size:14.5px; }
  .tier-row .ppos{ font-family:"IBM Plex Mono"; font-size:11px; color:var(--ink-muted); margin-left:4px; }
  .answer{ white-space:pre-wrap; line-height:1.6; margin-top:16px; padding-top:16px; border-top:1px solid var(--line); }
  .muted{ color:var(--ink-muted); font-size:13px; }
</style>
"""

LOGO_SVG = """<svg viewBox="0 0 26 26" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <rect x="1" y="1" width="24" height="24" rx="5" fill="var(--ink)"/>
  <path d="M6 13H20M6 8H14M6 18H14" stroke="var(--paper)" stroke-width="2" stroke-linecap="round"/>
</svg>"""

HEADER = f"""
<header class="site"><div class="wrap nav-row">
  <a class="wordmark" href="/">{LOGO_SVG}<span>Dynasty Explorer</span></a>
  <nav class="links"><a href="/chat">My Trade Chat</a></nav>
</div></header>
"""

HOME_HTML = BASE_STYLE + HEADER + """
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
  <p class="muted" style="margin-top:18px;">Showing leagues for <strong style="color:var(--accent-ink);">{{ data.display_name }}</strong></p>
  {% for lg in data.leagues %}
  <div class="panel">
    <div style="display:flex; justify-content:space-between; align-items:baseline;">
      <h2>{{ lg.league_name }}</h2>
      <span class="sample-tag">Live data</span>
    </div>
    {% for t in lg.teams %}
    <div class="league-row">
      <a class="league-name" href="/roster?league_id={{ lg.league_id }}&roster_id={{ t.roster_id }}&u={{ username }}">{{ t.owner_name }}{% if t.is_you %} &#9733;{% endif %}</a>
      <span class="wl">{{ t.wins }}-{{ t.losses }}</span>
      <div class="roster-bar" role="img" aria-label="Roster mix">
        {% for pos, pct in t.bar %}<span style="width:{{ pct }}%; background:var(--pos-{{ pos.lower() }})"></span>{% endfor %}
      </div>
      <span class="status-pill {{ t.status_tone }}">{{ t.status_label }}</span>
    </div>
    {% endfor %}
    <div class="legend-row">
      <span class="legend-item"><i style="background:var(--pos-qb)"></i>QB</span>
      <span class="legend-item"><i style="background:var(--pos-rb)"></i>RB</span>
      <span class="legend-item"><i style="background:var(--pos-wr)"></i>WR</span>
      <span class="legend-item"><i style="background:var(--pos-te)"></i>TE</span>
    </div>
  </div>
  {% endfor %}
  {% endif %}
</div></main>
"""

ROSTER_HTML = BASE_STYLE + HEADER + """
<main><div class="wrap">
  <a href="/?u={{ username }}" class="muted">&larr; Back to leagues</a>
  <div class="panel">
    <h2>{{ team.owner_name }}</h2>
    {% for pos in ["QB","RB","WR","TE"] %}
      {% for name in team.positions.get(pos, []) %}
      <div class="tier-row">
        <span class="pos-dot" style="background:var(--pos-{{ pos.lower() }})"></span>
        <span class="pname">{{ name }}<span class="ppos">{{ pos }}</span></span>
      </div>
      {% endfor %}
    {% endfor %}
  </div>
</div></main>
"""

LOGIN_HTML = BASE_STYLE + HEADER + """
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

CHAT_HTML = BASE_STYLE + HEADER + """
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
