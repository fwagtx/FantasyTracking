"""Put every promo clip on a device: vertical clips play on a phone,
desktop clips on a laptop, each on a dark backdrop.

A bare screen recording reads as "a web page"; the same recording on a
phone reads as "an app I could have in my pocket", and the laptop shows
the desktop site is a real, full-size thing too. The recording itself is
untouched -- scaled down and framed, nothing added to the page.

  promo_device.py CLIPS_DIR OUT_DIR

<slug>-vertical.mp4 -> phone, 1080x1920; <slug>-desktop.mp4 -> laptop,
1920x1080. The frames (scripts/promo_assets/*.png) are drawn by
promo_device_frames.py from the geometry below: a full-canvas image,
opaque everywhere except a hole exactly where the clip goes.
"""
import glob
import os
import subprocess
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "promo_assets")
BACKDROP = "0x141417"

# Canvas, then where the clip sits inside it (x, y, w, h).
#
# The phone is kept inside the part of a Reel / TikTok / Short that the
# app leaves clear: below the top tabs (~200px), above the caption and
# username (~1430px), and left of the like/comment/share column (~925px).
# Sized to the full canvas, the caption and buttons sat on the phone.
PHONE = {"canvas": (1080, 1920), "clip": (206, 250, 620, 1102), "frame": "phone.png"}
LAPTOP = {"canvas": (1920, 1080), "clip": (260, 110, 1400, 788), "frame": "laptop.png"}
DEVICE = {"vertical": PHONE, "desktop": LAPTOP}


def duration(path):
    c = cv2.VideoCapture(path)
    return c.get(cv2.CAP_PROP_FRAME_COUNT) / (c.get(cv2.CAP_PROP_FPS) or 30)


def frame_at(path, t):
    c = cv2.VideoCapture(path)
    c.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, f = c.read()
    return f if ok else None


def on_device(clip, out, kind):
    d = DEVICE[kind]
    cw, ch = d["canvas"]
    x, y, w, h = d["clip"]
    tmp = out + ".part.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        "-i", clip,
        "-loop", "1", "-framerate", "30", "-i", os.path.join(ASSETS, d["frame"]),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-filter_complex",
        f"color=c={BACKDROP}:s={cw}x{ch}:r=30[bg];"
        f"[0:v]fps=30,scale={w}:{h}:flags=lanczos,setsar=1[s];"
        f"[bg][s]overlay={x}:{y}:shortest=1[a];"
        f"[a][1:v]overlay=0:0:shortest=1,format=yuv420p[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
        "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        "-movflags", "+faststart", tmp,
    ], check=True)
    os.replace(tmp, out)


def check(clip, out, kind):
    """The clip must be playing, whole and unchanged, inside the screen."""
    d = DEVICE[kind]
    cw, ch = d["canvas"]
    x, y, w, h = d["clip"]
    n = duration(clip)
    if abs(duration(out) - n) > 0.2:
        return f"length {duration(out):.2f}s, clip is {n:.2f}s"
    t = n / 2
    a, b = frame_at(clip, t), frame_at(out, t)
    if b is None or b.shape[:2] != (ch, cw):
        return "unreadable or wrong size"
    want = cv2.resize(a, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)
    # Inset a little: the screen's rounded corners cover the clip's.
    m = 80
    got = b[y + m:y + h - m, x + m:x + w - m].astype(np.float32)
    diff = float(np.abs(got - want[m:-m, m:-m]).mean())
    if diff > 8:
        return f"screen does not show the clip (mean diff {diff:.1f})"
    return None


def main():
    clips_dir, out_dir = sys.argv[1:3]
    os.makedirs(out_dir, exist_ok=True)
    ok, bad = 0, []
    for clip in sorted(glob.glob(os.path.join(clips_dir, "*.mp4"))):
        name = os.path.basename(clip)[:-4]
        kind = name.rpartition("-")[2]
        if kind not in DEVICE:
            continue
        out = os.path.join(out_dir, name + ".mp4")
        src = clip
        if os.path.abspath(out) == os.path.abspath(clip):
            src = clip + ".raw.mp4"
            os.replace(clip, src)
        on_device(src, out, kind)
        why = check(src, out, kind)
        if src != clip:
            os.remove(src)
        if why:
            bad.append(name)
            print(f"FAIL {name}: {why}")
        else:
            ok += 1
            print(f"ok   {name} on a {'phone' if kind == 'vertical' else 'laptop'}")
    print(f"{ok} clips on a device, {len(bad)} failed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
