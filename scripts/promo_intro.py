"""Open every promo clip on its own cover.

A custom cover only takes on some accounts: TikTok uses it for Business
accounts only, and YouTube Shorts only on verified channels. Everywhere
else the platform picks a frame from the video itself -- by default the
first one, which in these clips is the dark frame before the page loads.
So the cover goes into the video: held for half a second as the very
first frames, then faded into the clip. Whatever frame a platform picks
from the start, it is the cover.

  promo_intro.py CLIPS_DIR COVERS_DIR OUT_DIR

<slug>-vertical.mp4 opens on <slug>-cover.jpg; <slug>-desktop.mp4 on
<slug>-cover-wide.jpg. A clip with no cover is left out (and named).
Each result is checked: its first frame must be the cover, and it must
be the clip's length plus the hold. OUT_DIR may be CLIPS_DIR.
"""
import glob
import os
import subprocess
import sys

import cv2
import numpy as np

HOLD = 0.5    # seconds the cover is on screen alone
FADE = 0.25   # then this long a crossfade into the clip

SIZE = {"vertical": (1080, 1920), "desktop": (1920, 1080)}
COVER = {"vertical": "cover.jpg", "desktop": "cover-wide.jpg"}


def duration(path):
    c = cv2.VideoCapture(path)
    n, fps = c.get(cv2.CAP_PROP_FRAME_COUNT), c.get(cv2.CAP_PROP_FPS) or 30
    return n / fps


def first_frame(path):
    ok, f = cv2.VideoCapture(path).read()
    return f if ok else None


def add_intro(clip, cover, out, kind):
    w, h = SIZE[kind]
    fit = f"scale={w}:{h}:flags=lanczos,setsar=1,fps=30,settb=AVTB,format=yuv420p"
    tmp = out + ".part.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        "-loop", "1", "-framerate", "30", "-t", f"{HOLD + FADE}", "-i", cover,
        "-i", clip,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-filter_complex",
        f"[0:v]{fit}[a];[1:v]{fit}[b];"
        f"[a][b]xfade=transition=fade:duration={FADE}:offset={HOLD},format=yuv420p[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
        "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        "-movflags", "+faststart", tmp,
    ], check=True)
    os.replace(tmp, out)


def check(clip_len, cover, out, kind):
    w, h = SIZE[kind]
    want = cv2.resize(cv2.imread(cover), (w, h)).astype(np.float32)
    got = first_frame(out)
    if got is None or got.shape[:2] != (h, w):
        return "unreadable or wrong size"
    diff = float(np.abs(got.astype(np.float32) - want).mean())
    if diff > 6:
        return f"first frame is not the cover (mean diff {diff:.1f})"
    extra = duration(out) - clip_len
    if abs(extra - HOLD) > 0.2:
        return f"length changed by {extra:.2f}s, expected {HOLD}s"
    return None


def main():
    clips_dir, covers_dir, out_dir = sys.argv[1:4]
    os.makedirs(out_dir, exist_ok=True)
    done, bad = 0, []
    for clip in sorted(glob.glob(os.path.join(clips_dir, "*.mp4"))):
        name = os.path.basename(clip)[:-4]
        slug, _, kind = name.rpartition("-")
        if kind not in SIZE:
            continue
        cover = os.path.join(covers_dir, f"{slug}-{COVER[kind]}")
        if not os.path.exists(cover):
            print(f"no cover for {name} -- left out")
            continue
        clip_len = duration(clip)
        out = os.path.join(out_dir, name + ".mp4")
        add_intro(clip, cover, out, kind)
        why = check(clip_len, cover, out, kind)
        if why:
            bad.append((name, why))
            print(f"FAIL {name}: {why}")
        else:
            done += 1
            print(f"ok   {name} ({clip_len:.1f}s -> {duration(out):.1f}s)")
    print(f"{done} clips open on their cover, {len(bad)} failed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
