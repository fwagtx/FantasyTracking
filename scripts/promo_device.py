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
# light theme's header, navy under a title card. The clock, signal,
# Wi-Fi, battery and home indicator are drawn over them in black or
# white, whichever the strip under them needs at that moment, as iOS
# does. (Both strip heights are even: yuv420p rounds an odd one down,
# which left a one-pixel seam of backdrop under the status bar.)
PHONE = {"canvas": (1080, 1920), "clip": (206, 250, 620, 1102), "frame": "phone.png",
         "top": 74, "bottom": 56, "ui": "phone-ui.png"}
# The laptop has no app furniture over it (these go to YouTube as
# ordinary videos), so its screen takes most of the frame.
LAPTOP = {"canvas": (1920, 1080), "clip": (176, 52, 1568, 882), "frame": "laptop.png"}
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
        # The status bar and home strip: the colour of the page at the
        # clip's top-left and bottom-left corner, frame by frame -- the
        # page's gutter, which is its background even when a list has
        # scrolled up under the header. (Stretching the whole top row
        # streaked the status bar with whatever scrolled under it; its
        # average turned a row of player photos brown.) The top rows are taken
        # from just under the clip's progress sliver (3 CSS px, 6 here),
        # or the status bar would fill up with it as the clip plays.
        graph += (f"[s0]split=3[s1][t0][b0];"
                  f"[t0]crop=4:2:2:{EDGE},scale=1:1:flags=area,scale={w}:{top},split=2[t1][t2];"
                  f"[b0]crop=4:2:2:{h - 2},scale=1:1:flags=area,scale={w}:{bottom},split=2[b1][b2];"
                  f"[bg0][t1]overlay={x}:{y - top}:shortest=1[bg1];"
                  f"[bg1][b1]overlay={x}:{y + h}:shortest=1[bg2];")
        bg, sc = "bg2", "s1"
    graph += (f"[{bg}][{sc}]overlay={x}:{y}:shortest=1[a];"
              f"[a][1:v]overlay=0:0:shortest=1")
    audio = "2:a"
    if d.get("ui"):
        # The iOS furniture: its shapes are the alpha of phone-ui.png;
        # its colour is black wherever the strip under it is light and
        # white wherever it is dark, worked out frame by frame.
        inputs += ["-loop", "1", "-framerate", "30", "-i", os.path.join(ASSETS, d["ui"])]
        audio = "3:a"
        lum = "format=gray,lut=y='if(gt(val,150),0,255)',format=rgba"
        graph += (f"[a2];[2:v]format=rgba,split=2[u0][u1];"
                  f"[u0]crop={w}:{top}:{x}:{y - top},alphaextract[ua];"
                  f"[u1]crop={w}:{bottom}:{x}:{y + h},alphaextract[ub];"
                  f"[t2]{lum}[tc];[b2]{lum}[bc];"
                  f"[tc][ua]alphamerge[ti];[bc][ub]alphamerge[bi];"
                  f"[a2][ti]overlay={x}:{y - top}:shortest=1[a3];"
                  f"[a3][bi]overlay={x}:{y + h}:shortest=1")
    graph += ",format=yuv420p[v]"
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        *inputs,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-filter_complex", graph,
        "-map", "[v]", "-map", audio,
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
        "-preset", "medium", "-crf", "16",
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
