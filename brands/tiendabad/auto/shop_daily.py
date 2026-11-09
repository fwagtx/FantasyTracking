"""LA TIENDITA BAD daily Item Shop video (Spanish), start to finish, for the autopilot.

    python3 brands/tiendabad/auto/shop_daily.py build [YYYY-MM-DD]
        Waits for the live shop to be that day (default: today in UTC), picks the three facts
        (shop/shop_pick.py, es-419), records the voice, renders the video and a review sheet of stills
        (shop/<day>/qa/review-sheet.png). Then a reviewer looks at the sheet and writes
        shop/<day>/review.json ({"status": "PASS" or "FAIL", "notes": "..."}).
    python3 brands/tiendabad/auto/shop_daily.py ship [YYYY-MM-DD]
        Runs the pre-post checklist in autopilot mode and stops unless it says READY TO POST.
        Makes the upload copy and the cover (the opening frame), uploads both for Metricool to import,
        and writes shop/<day>/posts.json: one ready createScheduledPost call per network, posting
        about 12 minutes from now.
    python3 brands/tiendabad/auto/shop_daily.py done [YYYY-MM-DD]
        After every post in posts.json is scheduled: deletes the uploaded copies (Metricool keeps
        its own) and archives the day's shop for the weekly long video.

Exit code 0 means the step worked; anything else means stop: post nothing for that day.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BRAND, MX, QA, SHOP, STORE, VIDEO, cover, mx_label, post_calls, review_sheet, run, upload_copy, write_posts  # noqa: E402

MES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def day_arg():
    a = [x for x in sys.argv[2:] if not x.startswith("-")]
    return a[0] if a else datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def build(day):
    for attempt in range(25):            # the live shop can lag the reset by a few minutes
        r = subprocess.run([sys.executable, "shop_pick.py", day], cwd=SHOP, capture_output=True, text=True)
        if r.returncode == 0:
            print(r.stdout)
            break
        if "Live shop is" in (r.stderr + r.stdout) and attempt < 24:
            print(f"live shop isn't {day} yet; waiting a minute")
            time.sleep(60)
            continue
        sys.stderr.write(r.stdout[-2000:] + r.stderr[-2000:])
        raise SystemExit("FAILED: shop_pick.py")
    D = os.path.join(SHOP, day)
    run([sys.executable, os.path.join(BRAND, "_shared", "voice", "vo.py"), "vo.json", "vo.wav", "timings.json"], D, quiet=True)
    vo = json.load(open(os.path.join(D, "timings.json")))
    open(os.path.join(D, "timings.js"), "w").write("window.VO=" + json.dumps(vo, ensure_ascii=False) + ";\n")
    open(os.path.join(D, "data.js"), "w").write(open(os.path.join(D, "plan.js")).read() + open(os.path.join(D, "timings.js")).read())
    rel = lambda p: os.path.relpath(p, VIDEO)
    mp4 = os.path.join(D, f"latienditabad-shop-{day}.mp4")
    run(["node", "render.mjs", rel(os.path.join(SHOP, "scene.js")), rel(mp4), "--data", rel(os.path.join(D, "data.js")), "--voice", rel(os.path.join(D, "vo.wav"))], VIDEO, quiet=True)
    # review sheet: the opening, each beat, the end card
    plan = json.load(open(os.path.join(D, "plan.json")))
    P = vo["phrases"]
    role = lambda r: P[plan["roles"].index(r)] if r in plan["roles"] else None
    times = [1.2] + [role(r)["t0"] + dt for r, dt in (("deal_price", 0.6), ("saving", 0.5), ("og_era", 1.0), ("last", 1.0), ("code", 0.6), ("thanks", 0.6)) if role(r)]
    stills = os.path.join(D, "qa", "stills")
    shutil.rmtree(stills, ignore_errors=True)
    run(["node", "render.mjs", rel(os.path.join(SHOP, "scene.js")), "x.mp4", "--data", rel(os.path.join(D, "data.js")),
         "--stills", ",".join(f"{t:.2f}" for t in times), "--stillsDir", stills], VIDEO, quiet=True)
    review_sheet(stills, os.path.join(D, "qa", "review-sheet.png"), 400)
    print(json.dumps({"day": day, "video": os.path.relpath(mp4, BRAND), "duration": round(P[-1]["t1"] + 0.75, 1),
                      "review_sheet": os.path.relpath(os.path.join(D, "qa", "review-sheet.png"), BRAND), "voice": plan["phrases"]}, indent=1, ensure_ascii=False))
    print("NEXT: look at the review sheet, write review.json in the day's folder, then run ship.")


def num(n):
    return f"{n:,}".replace(",", " ") if n >= 10000 else str(n)


def captions(p):
    """Hook first, one question to pull comments, the code ask, and #EpicPartner as the only disclosure."""
    d = datetime.date.fromisoformat(p["date"])
    long_day, short_day = f"{d.day} de {MES[d.month - 1]}", f"{d.day} {MES[d.month - 1][:3]}"
    deal, fin, reg = p["deal"]["name"], p["deal"]["final"], p["deal"]["regular"]
    price = f"{num(fin)} pavos" + (f" en vez de {num(reg)}" if reg > fin else "")
    og = [o["name"] for o in p["og"]["outfits"]]
    era = f"Capítulo {p['og']['chapter']}, Temporada {p['og']['season']}"
    og_line = f"{' y '.join(og)} ({era}) {'están' if len(og) > 1 else 'está'} en la tienda"
    n = p["last"]["count"]
    last = f" y {n} skin{'s están' if n != 1 else ' está'} en su último día ⏳" if n else ""
    body = f"{deal}: {price} 👀\n{og_line}{last}"
    ask = "¿Cuál te vas a llevar? 👇"
    code = "Usa el código BAD en la tienda de objetos 💛"
    head = f"#EpicPartner Esta es la tienda de objetos de hoy, {long_day} 🛒"
    yt_title = f"Tienda de Fortnite HOY ({long_day}) 👀 {deal} por {num(fin)} pavos #Shorts"
    if len(yt_title) > 100:
        yt_title = f"Tienda de Fortnite HOY ({long_day}) 👀 #Shorts"
    return {
        "tiktok": f"{head}\n{body}\n{ask}\n{code}\n#fortnite #tiendafortnite #fortniteespañol",
        "tiktokTitle": f"Tienda de hoy · {short_day}",
        "instagram": f"{head}\n{body}\n{ask}\n{code}\n#fortnite #tiendafortnite #fortniteespañol",
        "facebook": f"{head}\n{body}\n{ask}\n{code}",
        "facebookTitle": f"Tienda de hoy · {short_day}",
        "youtube": f"{head}\n{body}\n{ask}\n{code}\n#Fortnite #TiendaFortnite #Shorts",
        "youtubeTitle": yt_title,
        "threads": f"{head}\n{body}\n{ask}\n{code}\n#fortnite #tiendafortnite",
        "tags": ["fortnite", "tienda fortnite", "tienda de objetos fortnite", "tienda fortnite hoy", "fortnite español", f"tienda fortnite {long_day}"],
    }


def ship(day):
    D = os.path.join(SHOP, day)
    rv = os.path.join(D, "review.json")
    if not os.path.exists(rv) or json.load(open(rv)).get("status") != "PASS":
        raise SystemExit("STOP: review.json is missing or not PASS.")
    mp4 = os.path.join(D, f"latienditabad-shop-{day}.mp4")
    run([sys.executable, QA, os.path.join(SHOP, "scene.js"), mp4, D, os.path.join(D, "qa"), "--review", rv, "--data", os.path.join(D, "data.js"), "--autopilot"], BRAND)
    verdict = json.load(open(os.path.join(D, "qa", "qa-log.json")))["verdict"]
    if verdict != "READY TO POST":
        raise SystemExit(f"STOP: checklist says {verdict}. See {os.path.relpath(os.path.join(D, 'qa', 'qa-log.md'), BRAND)}.")
    # the shop ends at the next 00:00 UTC; a post with under 3 hours left isn't worth it
    end = datetime.datetime.combine(datetime.date.fromisoformat(day) + datetime.timedelta(days=1), datetime.time(0), datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    if end - now < datetime.timedelta(hours=3):
        raise SystemExit("STOP: this shop ends in under 3 hours; skip it.")
    out = os.path.join(D, "out")
    os.makedirs(out, exist_ok=True)
    upload_copy(mp4, os.path.join(out, "video.mp4"))
    cover(mp4, 1.2, os.path.join(out, "cover.jpg"))
    video = run([sys.executable, STORE, "put", os.path.join(out, "video.mp4"), f"shop-{day}/video.mp4"], BRAND, quiet=True).strip()
    cov = run([sys.executable, STORE, "put", os.path.join(out, "cover.jpg"), f"shop-{day}/cover.jpg"], BRAND, quiet=True).strip()
    when = (now + datetime.timedelta(minutes=12)).astimezone(MX)
    when = when.replace(second=0, microsecond=0) + datetime.timedelta(minutes=(5 - when.minute % 5) % 5)
    plan = json.load(open(os.path.join(D, "plan.json")))
    calls = post_calls(when, video, cov, captions(plan))
    write_posts(os.path.join(D, "posts.json"), f"shop-{day}", when, calls)
    print(json.dumps({"verdict": verdict, "post_at": mx_label(when), "posts": os.path.relpath(os.path.join(D, "posts.json"), BRAND),
                      "networks": [c["network"] for c in calls]}, indent=1))
    print("NEXT: call createScheduledPost once per entry in posts.json calls (blogId, date, info exactly as given), then run done.")


def done(day):
    D = os.path.join(SHOP, day)
    for name in ("video.mp4", "cover.jpg"):
        run([sys.executable, STORE, "rm", f"shop-{day}/{name}"], BRAND, quiet=True)
    # a small archive of the whole shop (names, prices, dates) for the weekly shop video
    shop = json.load(open(os.path.join(D, "shop.json")))["data"]
    rows = []
    for e in shop["entries"]:
        items = e.get("brItems") or []
        if not items:
            continue
        rows.append({"bundle": (e.get("bundle") or {}).get("name"), "items": [[i["name"].strip(), i["type"]["value"], i["rarity"]["value"]] for i in items],
                     "final": e["finalPrice"], "regular": e["regularPrice"], "in": e["inDate"][:10], "out": e["outDate"][:10]})
    os.makedirs(os.path.join(SHOP, "archive"), exist_ok=True)
    json.dump({"date": day, "entries": rows}, open(os.path.join(SHOP, "archive", f"{day}.json"), "w"), separators=(",", ":"), ensure_ascii=False)
    print(f"cleaned up shop-{day}; archived {len(rows)} entries")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    {"build": build, "ship": ship, "done": done}.get(step, lambda d: sys.exit(__doc__))(day_arg())
