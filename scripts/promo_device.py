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
#
# Above and below the clip the screen carries on, as a real iPhone's
# does: a status bar (top) and the home-indicator strip (bottom). Both
# are painted from the clip's own edge rows every frame, so they are
# always the colour of whatever the page is showing -- white under the
# light theme's header, navy under a title card.
PHONE = {"canvas": (1080, 1920), "clip": (206, 250, 620, 1102), "frame": "phone.png",
         "top": 73, "bottom": 56,
         "ui": {"ink": "phone-ui-ink.png", "white": "phone-ui-white.png"}}
LAPTOP = {"canvas": (1920, 1080), "clip": (260, 110, 1400, 788), "frame": "laptop.png"}
DEVICE = {"vertical": PHONE, "desktop": LAPTOP}
EDGE_SRC = 10                    # first row under the progress sliver, in the 1080x1920 clip
EDGE = round(EDGE_SRC * PHONE["clip"][3] / 1920)   # the same row once the clip is scaled onto the phone


def duration(path):
    c = cv2.VideoCapture(path)
    return c.get(cv2.CAP_PROP_FRAME_COUNT) / (c.get(cv2.CAP_PROP_FPS) or 30)


def frame_at(path, t):
    c = cv2.VideoCapture(path)
    c.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, f = c.read()
    return f if ok else None


def page_is_light(clip):
    """Whether the page along the clip's top edge is mostly light -- which
    decides if the status bar is drawn in black or white, as iOS does."""
    c = cv2.VideoCapture(clip)
    n = int(c.get(cv2.CAP_PROP_FRAME_COUNT))
    votes = []
    for k in range(1, 10):
        c.set(cv2.CAP_PROP_POS_FRAMES, int(n * k / 10))
        ok, f = c.read()
        if ok:
            top = f[EDGE_SRC:EDGE_SRC + 6].reshape(-1, 3).astype(np.float32).mean(axis=0)   # BGR
            votes.append(0.114 * top[0] + 0.587 * top[1] + 0.299 * top[2] > 150)
    return sum(votes) > len(votes) / 2


def on_device(clip, out, kind):
    d = DEVICE[kind]
    cw, ch = d["canvas"]
    x, y, w, h = d["clip"]
    tmp = out + ".part.mp4"
    top, bottom = d.get("top", 0), d.get("bottom", 0)
    inputs = ["-i", clip,
              "-loop", "1", "-framerate", "30", "-i", os.path.join(ASSETS, d["frame"])]
    graph = (f"color=c={BACKDROP}:s={cw}x{ch}:r=30[bg0];"
             f"[0:v]fps=30,scale={w}:{h}:flags=lanczos,setsar=1[s0];")
    bg, sc = "bg0", "s0"
    if top or bottom:
        # The status bar and home strip: the clip's top and bottom rows,
        # stretched to fill them, frame by frame. The top rows are taken
        # from just under the clip's progress sliver (3 CSS px, 6 here),
        # or the status bar would fill up with it as the clip plays.
        graph += (f"[s0]split=3[s1][t0][b0];"
                  f"[t0]crop={w}:2:0:{EDGE},scale={w}:{top}[t1];"
                  f"[b0]crop={w}:2:0:{h - 2},scale={w}:{bottom}[b1];"
                  f"[bg0][t1]overlay={x}:{y - top}:shortest=1[bg1];"
                  f"[bg1][b1]overlay={x}:{y + h}:shortest=1[bg2];")
        bg, sc = "bg2", "s1"
    graph += (f"[{bg}][{sc}]overlay={x}:{y}:shortest=1[a];"
              f"[a][1:v]overlay=0:0:shortest=1")
    audio = "2:a"
    if d.get("ui"):
        ui = d["ui"]["ink" if page_is_light(clip) else "white"]
        inputs += ["-loop", "1", "-framerate", "30", "-i", os.path.join(ASSETS, ui)]
        graph += "[a2];[a2][2:v]overlay=0:0:shortest=1"
        audio = "3:a"
    graph += ",format=yuv420p[v]"
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        *inputs,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-filter_complex", graph,
        "-map", "[v]", "-map", audio,
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
