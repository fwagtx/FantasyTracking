"""
Dynasty League Explorer
--------------------------
A PUBLIC website: anyone can type a Sleeper username and see every dynasty
league it's in, full standings, and every team's roster in that league --
no login needed, since Sleeper data is public.

The AI Trade Chat page stays password-protected (it costs API quota, so
it's kept just for you).

Runs on free services: Sleeper's public API, Google Gemini's free tier,
and Render's free web service tier.

Required environment variables:
  GEMINI_API_KEY, SITE_PASSWORD
Optional:
  SLEEPER_USERNAME (used to prefill your own username on the chat page)
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


def get_trending_adds(limit=75):
    r = requests.get(
        f"{SLEEPER_BASE}/players/nfl/trending/add",
        params={"lookback_hours": 48, "limit": limit},
    )
    r.raise_for_status()
    return [item["player_id"] for item in r.json()]


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


def build_leagues_for_user(username, cache={}):
    """All leagues + full standings for a given Sleeper username. Cached 10 min per username."""
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
            teams.append({
                "roster_id": r["roster_id"],
                "owner_name": league_users.get(r.get("owner_id"), "Unknown"),
                "wins": settings.get("wins", 0),
                "losses": settings.get("losses", 0),
                "points": round(points, 1),
                "is_you": r.get("owner_id") == user_id,
                "positions": roster_positions(r, all_players),
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
        team = {
            "owner_name": league_users.get(r.get("owner_id"), "Unknown"),
            "positions": roster_positions(r, all_players),
        }
        return render_template_string(ROSTER_HTML, team=team, username=username)
    except Exception as e:
        return f"Error: {e}", 500

# ---------------- Private chat (password protected) ----------------

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

# ---------------- Templates ----------------

BASE_STYLE = """
<style>
  :root {
    --bg: #10160f; --panel: #182018; --line: #2b3830; --gold: #c9a227;
    --sage: #7fa383; --text: #ece8dc; --muted: #93a396; --alert: #cc6a41;
  }
  * { box-sizing: border-box; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system,"Segoe UI",Roboto,sans-serif; margin: 0; padding: 0 16px 60px; }
  header { display: flex; justify-content: space-between; align-items: baseline; padding: 28px 0 18px; border-bottom: 1px solid var(--line); max-width: 820px; margin: 0 auto; }
  header h1 { font-size: 22px; letter-spacing: 0.02em; margin: 0; color: var(--gold); font-weight: 700; }
  header a { color: var(--muted); text-decoration: none; font-size: 13px; }
  main { max-width: 820px; margin: 0 auto; }
  .card { border: 1px solid var(--line); background: var(--panel); padding: 18px 20px; margin-top: 18px; }
  .card h2 { margin: 0 0 12px; font-size: 17px; color: var(--gold); }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; color: var(--sage); font-weight: 600; padding: 6px 8px; border-bottom: 1px solid var(--line); }
  td { padding: 8px; border-bottom: 1px solid var(--line); }
  tr.you td { color: var(--gold); font-weight: 600; }
  a.team-link { color: inherit; text-decoration: underline; text-decoration-color: var(--line); }
  a.team-link:hover { text-decoration-color: var(--gold); }
  .pos-row { display: flex; gap: 10px; margin: 4px 0; font-size: 14px; }
  .pos-label { color: var(--sage); width: 32px; flex-shrink: 0; font-weight: 600; }
  form.q { display: flex; gap: 10px; margin-top: 8px; }
  input[type=text], input[type=password] { flex: 1; background: var(--panel); border: 1px solid var(--line); color: var(--text); padding: 12px 14px; font-size: 15px; }
  button { background: var(--gold); color: #10160f; border: none; padding: 12px 20px; font-weight: 700; cursor: pointer; }
  button:hover { opacity: 0.9; }
  .answer { white-space: pre-wrap; line-height: 1.5; margin-top: 16px; }
  .error { color: var(--alert); font-size: 14px; margin-top: 10px; }
  .muted { color: var(--muted); font-size: 13px; }
</style>
"""

HOME_HTML = BASE_STYLE + """
<header><h1>Dynasty League Explorer</h1><a href="/chat">My Trade Chat</a></header>
<main>
  <div class="card">
    <h2>Look up any Sleeper dynasty manager</h2>
    <form method="get" class="q">
      <input type="text" name="u" placeholder="Sleeper username" value="{{ username }}" autofocus>
      <button type="submit">Search</button>
    </form>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
  </div>

  {% if data %}
  <p class="muted" style="margin-top:20px;">Showing leagues for <strong style="color:var(--gold);">{{ data.display_name }}</strong></p>
  {% for lg in data.leagues %}
  <div class="card">
    <h2>{{ lg.league_name }}</h2>
    <table>
      <tr><th>Team</th><th>W-L</th><th>Points</th></tr>
      {% for t in lg.teams %}
      <tr class="{{ 'you' if t.is_you else '' }}">
        <td><a class="team-link" href="/roster?league_id={{ lg.league_id }}&roster_id={{ t.roster_id }}&u={{ username }}">{{ t.owner_name }}</a></td>
        <td>{{ t.wins }}-{{ t.losses }}</td>
        <td>{{ t.points }}</td>
      </tr>
      {% endfor %}
    </table>
  </div>
  {% endfor %}
  {% endif %}
</main>
"""

ROSTER_HTML = BASE_STYLE + """
<header><h1>Dynasty League Explorer</h1><a href="/?u={{ username }}">Back to leagues</a></header>
<main>
  <div class="card">
    <h2>{{ team.owner_name }}</h2>
    {% for pos in ["QB","RB","WR","TE"] %}
    <div class="pos-row">
      <span class="pos-label">{{ pos }}</span>
      <span>{{ team.positions.get(pos) | join(", ") if team.positions.get(pos) else "(none)" }}</span>
    </div>
    {% endfor %}
  </div>
</main>
"""

LOGIN_HTML = BASE_STYLE + """
<main style="max-width:340px; margin:100px auto; text-align:center;">
  <h1 style="color:var(--gold);">Trade Chat (private)</h1>
  <form method="post" class="q" style="margin-top:24px;">
    <input type="password" name="password" placeholder="Password" autofocus>
    <button type="submit">Enter</button>
  </form>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
</main>
"""

CHAT_HTML = BASE_STYLE + """
<header><h1>Dynasty League Explorer</h1><a href="/?u={{ username }}">Public dashboard</a></header>
<main>
  <div class="card">
    <h2>Ask about a trade or your roster</h2>
    <form method="post" class="q">
      <input type="hidden" name="u" value="{{ username }}">
      <input type="text" name="question" placeholder="Should I trade Kelce for a 2nd?" value="{{ question }}" autofocus>
      <button type="submit">Ask</button>
    </form>
    {% if answer %}<div class="answer">{{ answer }}</div>{% endif %}
  </div>
</main>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
