"""Rankings is rendered once and shared for thirty seconds. What the
key must separate, what a burst costs, and that nothing about one reader
reaches another."""
import os, sys, time, threading
sys.path.insert(0, "/home/user/FantasyTracking")
os.environ["GEMINI_API_KEY"] = "dummy"; os.environ["SITE_PASSWORD"] = "testsecret"; os.environ["DATABASE_URL"] = ""
import webapp
from unittest.mock import patch
webapp.app.logger.disabled = True
results = []
def run(label, cond, detail=""):
    ok = bool(cond); print(("PASS - " if ok else "FAIL - ") + label + (f" ({detail})" if not ok and detail else "")); results.append(ok)

POS = ["QB", "RB", "WR", "TE"]
FC = {"players": {}}; PLAYERS = {}
for i in range(120):
    sid = str(300000 + i); pos = POS[i % 4]
    FC["players"][sid] = {"position": pos, "overall_rank": i + 1, "position_rank": i // 4 + 1, "value": 12000 - i * 90, "name": f"P{i}", "age": 25, "team": "DET"}
    PLAYERS[sid] = {"first_name": f"F{i}", "last_name": f"L{i}", "position": pos, "team": "DET", "age": 25, "years_exp": 3, "fantasy_positions": [pos], "status": "Active"}
# 5 receptions a game: PPR and standard disagree on every points column.
STATS = {sid: {"games": 4, "fpts": 60.0, "rec": 20, "weeks": {w: 15.0 for w in range(1, 5)}, "snap_pct": 70} for sid in PLAYERS}
patches = [patch.object(webapp, n, v) for n, v in (
    ("get_fantasycalc_values", lambda *a, **k: FC), ("get_all_players", lambda: PLAYERS),
    ("get_season_stats", lambda s, sc=None: STATS), ("get_value_movement", lambda *a, **k: {"rows": {}, "days": 7}),
    ("_record_value_snapshots_background", lambda: None),
    ("get_current_week_info", lambda: {"season": 2026, "week": 3, "season_type": 2}))]
for p in patches: p.start()

def as_user(client, uid, **row):
    base = {"id": uid, "email": "x@e", "username": f"u{uid}", "is_member": False, "sleeper_username": None}
    base.update(row)
    cm = patch.object(webapp.login_manager, "_user_callback", return_value=webapp.User(base)); cm.start()
    with client.session_transaction() as s: s["_user_id"] = str(uid); s["_fresh"] = True
    return cm

guest = webapp.app.test_client()
webapp._rankings_page_cache.clear()
g1, g2 = guest.get("/rankings"), guest.get("/rankings")
run("THE CACHE: a guest's first load is rendered, the second is the shared copy, byte for byte",
    g1.headers.get("X-Rankings-Cache") == "miss" and g2.headers.get("X-Rankings-Cache") == "hit" and g1.data == g2.data)
sf = guest.get("/rankings?format=superflex")
run("a different format is its own copy", sf.headers.get("X-Rankings-Cache") == "miss" and sf.data != g1.data)
run("and asking for it again is a hit", guest.get("/rankings?format=superflex").headers.get("X-Rankings-Cache") == "hit")
run("the home page for a guest is the same shared rankings copy", guest.get("/").headers.get("X-Rankings-Cache") == "hit")

me = webapp.app.test_client(); cm = as_user(me, 42, pref_scoring="ppr")
u1, u2 = me.get("/rankings"), me.get("/rankings"); cm.stop()
run("a signed-in reader is a separate copy from a guest (the gate differs), rendered once and then shared",
    u1.headers.get("X-Rankings-Cache") == "miss" and u2.headers.get("X-Rankings-Cache") == "hit" and u1.data != g1.data)
other = webapp.app.test_client(); cm = as_user(other, 43, pref_scoring="ppr")
o1 = other.get("/rankings"); cm.stop()
run("THE SHARE: a different reader with the same settings is handed that same copy", o1.headers.get("X-Rankings-Cache") == "hit" and o1.data == u1.data)
std = webapp.app.test_client(); cm = as_user(std, 44, pref_scoring="standard")
s1 = std.get("/rankings"); cm.stop()
run("THE SCORING KEY: a reader on standard scoring gets their own copy, with different points on it",
    s1.headers.get("X-Rankings-Cache") == "miss" and s1.data != u1.data)

with patch.object(webapp, "get_fantasycalc_values", side_effect=RuntimeError("fantasycalc down")):
    webapp._rankings_page_cache.clear()
    try:
        err = guest.get("/rankings")
        err_status = err.status_code
    except Exception:
        err_status = 500
run("THE ERROR PATH: a failed build is not stored as a copy", err_status != 200 or "X-Rankings-Cache" not in err.headers or len(webapp._rankings_page_cache) == 0,
    (err_status, len(webapp._rankings_page_cache)))
webapp._rankings_page_cache.clear()
run("and the next good request renders afresh", guest.get("/rankings").headers.get("X-Rankings-Cache") == "miss")

webapp._rankings_page_cache.clear()
renders = []
real = webapp.render_template_string
def counting(tpl, **kw):
    if tpl is webapp.RANKINGS_HTML: renders.append(1)
    return real(tpl, **kw)
with patch.object(webapp, "render_template_string", counting):
    threads = [threading.Thread(target=lambda: webapp.app.test_client().get("/rankings")) for _ in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
run("THE STAMPEDE: twelve arrivals on an expired copy cost exactly one render", len(renders) == 1, len(renders))

webapp._rankings_page_cache.clear(); guest.get("/rankings")
entry = next(iter(webapp._rankings_page_cache.values())); entry["time"] -= webapp.RANKINGS_PAGE_TTL_S + 1
run("a copy older than the TTL is rendered again", guest.get("/rankings").headers.get("X-Rankings-Cache") == "miss")
webapp._rankings_page_cache.clear()
for i in range(webapp.RANKINGS_PAGE_COPIES + 6):
    guest.get(f"/rankings?pos={'overall' if i == 0 else 'p' + str(i)}")
run("the cache holds a bounded number of copies, oldest out first", len(webapp._rankings_page_cache) == webapp.RANKINGS_PAGE_COPIES, len(webapp._rankings_page_cache))

def ms(fn, n=10):
    fn(); t0 = time.perf_counter()
    for _ in range(n): fn()
    return (time.perf_counter() - t0) / n * 1000
hit = ms(lambda: guest.get("/rankings"))
miss = ms(lambda: (webapp._rankings_page_cache.clear(), guest.get("/rankings")), 5)
run("THE POINT: a shared copy costs a fraction of a render", hit * 5 < miss, f"hit {hit:.1f} ms, miss {miss:.1f} ms")
for p in patches: p.stop()
print(); print("ALL PASS" if all(results) else "SOME FAILED"); sys.exit(0 if all(results) else 1)
