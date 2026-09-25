"""Check every recorded promo clip before anything schedules it.

A clip passes only if all of these hold:

  - the right size (1080x1920), long enough, and filling the frame --
    no gray padding, which is what a broken device-scale setting gives;
  - it never sits on one frame for more than 8 seconds (a stalled take);
  - it does not open on a blank white flash (a light-theme page is
    fine; an empty white screen is not);
  - every beat of its scene landed (the recorder's missed-beat list);
  - no frame shows a sign-in prompt, the Start/Bench/Cut vote popup, or
    one of the site's empty-list messages. This is read off the frames
    with OCR, every frame that differs visibly from the one before --
    the one bug that got past a sampled review was two frames long.

Writes qa.json: {file: {"ok": bool, "reasons": [...]}}.
"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
import argparse  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from multiprocessing import Pool  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

SIGN_IN = re.compile(r"sign ?in\b|sign ?up\b|log ?in\b|create (a )?(free )?account|unlock|join free|"
                     r"streakpros\+|see streakpros|see plans", re.I)
POPUP = re.compile(r"your thoughts\?|know all of these", re.I)
EMPTY = re.compile(r"\bno [a-z ]{0,40}\b(yet|match)\b|no injury designations|nothing here", re.I)


def frames_to_read(path):
    c = cv2.VideoCapture(path)
    fps = c.get(cv2.CAP_PROP_FPS) or 30
    last, i = None, -1
    while True:
        ok, f = c.read()
        i += 1
        if not ok:
            return
        g = cv2.cvtColor(cv2.resize(f, (108, 192)), cv2.COLOR_BGR2GRAY).astype(np.float32)
        # Rows 0-5 are the progress sliver, which moves every frame.
        if last is not None and np.abs(g[6:] - last[6:]).mean() < 3.0:
            continue
        last = g
        yield i / fps, f


def check(job):
    path, missed = job
    name = os.path.basename(path)
    reasons = []
    c = cv2.VideoCapture(path)
    n, fps = int(c.get(7)), c.get(5) or 30
    w, h = int(c.get(3)), int(c.get(4))
    if (w, h) != (1080, 1920):
        reasons.append(f"{w}x{h}, not 1080x1920")
    if n / fps < 6:
        reasons.append(f"only {n / fps:.1f}s long")
    gray, first, blank = 0.0, None, False
    for k in range(10):
        c.set(1, int(k * (n - 1) / 9))
        ok, f = c.read()
        if not ok:
            continue
        if first is None:
            first = float(f.mean())
            blank = first > 235 and float(f.std()) < 6
        q = f[h // 2:, w // 2:]
        gray = max(gray, float((np.abs(q.astype(int) - 128).max(axis=2) <= 2).mean()))
    if gray > 0.3:
        reasons.append("picture does not fill the frame (gray padding)")
    if blank:
        reasons.append("opens on a blank white frame")
    if missed:
        reasons.append("a beat did not land while recording")

    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR(intra_op_num_threads=1)
    sbc = "start-bench-cut" in name
    seen = set()
    # A stalled take films one frame for as long as the stall lasted --
    # 26 seconds of it, once, while a tap waited on a page that kept
    # loading. No beat holds that long on purpose.
    last_change, longest = 0.0, 0.0
    for t, f in frames_to_read(path):
        longest = max(longest, t - last_change)
        last_change = t
        res, _ = ocr(cv2.resize(f, (540, 960)))
        for _box, txt, _conf in res or []:
            flat = txt.replace(" ", "")
            for label, rx in (("sign-in prompt", SIGN_IN), ("vote popup", POPUP), ("empty list", EMPTY)):
                if label == "vote popup" and sbc:
                    continue
                if (rx.search(txt) or rx.search(flat)) and (label, txt) not in seen:
                    seen.add((label, txt))
                    reasons.append(f'{label} on screen at {t:.1f}s: "{txt}"')
    longest = max(longest, n / fps - last_change)
    if longest > 8:
        reasons.append(f"picture frozen for {longest:.0f}s")
    return name, {"ok": not reasons, "reasons": reasons}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dir")
    ap.add_argument("--missed", default="", help="file listing clip slugs whose recording missed a beat")
    ap.add_argument("--out", default="qa.json")
    a = ap.parse_args()
    missed = set()
    if a.missed and os.path.exists(a.missed):
        missed = {line.strip() for line in open(a.missed) if line.strip()}
    files = sorted(glob.glob(os.path.join(a.dir, "*.mp4")))
    jobs = [(f, os.path.basename(f)[:-len("-vertical.mp4")] in missed) for f in files]
    with Pool(max(1, min(4, os.cpu_count() or 1))) as pool:
        res = dict(pool.map(check, jobs))
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    bad = {k: v["reasons"] for k, v in res.items() if not v["ok"]}
    print(f"{len(res) - len(bad)} of {len(res)} clips passed")
    for k, r in bad.items():
        print(f"FAIL {k}")
        for x in r:
            print(f"     {x}")


if __name__ == "__main__":
    main()
