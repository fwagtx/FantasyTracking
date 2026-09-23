"""A soundtrack for every promo clip, made here rather than borrowed.

Every clip gets an upbeat instrumental bed -- four-on-the-floor drums, a
pumping bass, wide chords and a plucked arpeggio -- synthesised from
scratch in numpy. Nothing is sampled or licensed, so no platform can
mute the post, flag it, or claim it for someone else's catalogue: the
music belongs to StreakPros like the footage does.

There are four arrangements (key, tempo and chord progression); each
clip gets one picked from its name, so the same clip always sounds the
same and a week of posts does not repeat one track three times a day.
The first downbeat lands as the cover fades into the clip (0.5s), and
the track fades out under the end card.

  promo_audio.py DIR                 put a soundtrack on every .mp4 in DIR, in place
  promo_audio.py --wav OUT --name N  write one track to OUT (to listen to)
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import wave
import zlib

import numpy as np

SR = 44100
LEAD_IN = 0.5          # the cover's hold: the drop lands as it fades out
FADE_OUT = 1.4

# (name, key root as a MIDI note for the bass, tempo, progression). A
# progression is four bars of (semitones above the key, minor?).
ARRANGEMENTS = [
    ("anthem", 45, 118, [(9, True), (5, False), (0, False), (7, False)]),   # vi IV I V in C
    ("drive", 43, 124, [(0, False), (7, False), (9, True), (5, False)]),   # I V vi IV in G
    ("night", 40, 112, [(0, True), (8, False), (3, False), (10, False)]),  # i VI III VII in E minor
    ("rally", 42, 128, [(0, True), (5, True), (8, False), (7, False)]),    # i iv VI V in F# minor
]


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def band(noise, lo, hi):
    """Band-limit a noise burst in the frequency domain."""
    f = np.fft.rfftfreq(len(noise), 1 / SR)
    spec = np.fft.rfft(noise)
    spec[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(spec, len(noise))


def saw(freq, n, harmonics=12, phase=0.0):
    """A band-limited sawtooth: warm, no aliasing."""
    t = np.arange(n) / SR
    out = np.zeros(n)
    for k in range(1, harmonics + 1):
        if freq * k > 5000:
            break
        out += np.sin(2 * np.pi * freq * k * t + phase * k) / k
    return out * 0.55


def place(track, sound, at, gain=1.0, pan=0.0):
    i = int(at * SR)
    if i >= track.shape[1]:
        return
    s = sound[: track.shape[1] - i] * gain
    track[0, i:i + len(s)] += s * min(1.0, 1.0 - pan)
    track[1, i:i + len(s)] += s * min(1.0, 1.0 + pan)


def kick():
    n = int(0.42 * SR)
    t = np.arange(n) / SR
    f = 55 + 130 * np.exp(-t / 0.03)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.13)
    click = np.random.default_rng(1).normal(0, 1, n) * np.exp(-t / 0.002) * 0.3
    return body + click


def clap(rng):
    n = int(0.3 * SR)
    t = np.arange(n) / SR
    noise = band(rng.normal(0, 1, n), 900, 5200)
    env = np.exp(-t / 0.075)
    for d in (0.009, 0.018):                   # three hands, slightly apart
        env += np.where(t >= d, np.exp(-(t - d) / 0.006), 0) * 0.6
    return noise * env * 0.9


def hat(rng, open_=False):
    n = int((0.16 if open_ else 0.05) * SR)
    t = np.arange(n) / SR
    return band(rng.normal(0, 1, n), 7000, 16000) * np.exp(-t / (0.045 if open_ else 0.011))


def crash(rng):
    n = int(1.6 * SR)
    t = np.arange(n) / SR
    return band(rng.normal(0, 1, n), 4000, 15000) * np.exp(-t / 0.5)


def riser(rng, length):
    n = int(length * SR)
    t = np.arange(n) / SR
    return band(rng.normal(0, 1, n), 2500, 12000) * (t / length) ** 2.2


def render(name, seconds):
    """The track for the clip called `name`, `seconds` long, stereo float."""
    arr = ARRANGEMENTS[zlib.crc32(name.encode()) % len(ARRANGEMENTS)]
    _, key, bpm, prog = arr
    rng = np.random.default_rng(zlib.crc32(name.encode()))
    beat = 60.0 / bpm
    bar = 4 * beat
    total = int((seconds + 0.2) * SR)
    drums = np.zeros((2, total))
    music = np.zeros((2, total))

    K, C, H, HO = kick(), clap(rng), hat(rng), hat(rng, True)
    place(drums, riser(rng, LEAD_IN), 0, 0.35)
    kicks = []
    t0 = LEAD_IN
    place(drums, crash(rng), t0, 0.45)
    b = 0
    while t0 + b * beat < seconds:
        at = t0 + b * beat
        place(drums, K, at, 0.62)
        kicks.append(at)
        if b % 2 == 1:
            place(drums, C, at, 0.55)
        place(drums, H, at, 0.16, 0.25)
        place(drums, HO, at + beat / 2, 0.24, 0.25)
        if b % 16 == 15:                        # a fill into each fourth bar
            for q in (0.25, 0.5, 0.75):
                place(drums, C, at + q * beat, 0.22)
        b += 1

    # Sidechain: everything musical ducks under the kick, the pump that
    # makes a four-on-the-floor bed feel like it is moving.
    tt = np.arange(total) / SR
    last = np.full(total, -10.0)
    for k in kicks:
        last[int(k * SR):] = k
    pump = 1 - 0.55 * np.exp(-(tt - last) / 0.11)

    bars = int(np.ceil((seconds - t0) / bar)) + 1
    for i in range(bars):
        start = t0 + i * bar
        if start >= seconds:
            break
        root, minor = prog[i % len(prog)]
        notes = [0, 3 if minor else 4, 7]
        n = int(bar * SR)
        t = np.arange(n) / SR
        # Chords: three detuned saws a note, spread across the stereo field.
        env = np.minimum(1, t / 0.02) * (0.85 + 0.15 * np.exp(-t / 0.3))
        env *= np.minimum(1, (t[-1] - t) / 0.02)   # release, so bars do not click
        for iv in notes:
            f = hz(key + 24 + root + iv)
            for det, pan in ((-0.10, -0.7), (0.0, 0.0), (0.10, 0.7)):
                place(music, saw(f * 2 ** (det / 12), n, 10, rng.uniform(0, 6)) * env, start, 0.10, pan)
        # Bass: eighth notes on the root, off-beats a touch louder.
        for e in range(8):
            m = int(beat / 2 * SR)
            te = np.arange(m) / SR
            benv = np.minimum(1, te / 0.004) * np.exp(-te / 0.18)
            place(music, saw(hz(key + root), m, 8) * benv, start + e * beat / 2, 0.24 if e % 2 else 0.19)
        # Arp: sixteenths over the chord, an octave up, soft plucks.
        pattern = [0, 1, 2, 1, 0, 2, 1, 2]
        for s16 in range(16):
            iv = notes[pattern[s16 % 8]] + (12 if s16 % 8 >= 6 else 0)
            m = int(beat / 4 * SR * 1.6)
            tp = np.arange(m) / SR
            f = hz(key + 36 + root + iv)
            pl = (np.sin(2 * np.pi * f * tp) + 0.3 * np.sin(4 * np.pi * f * tp)) * np.exp(-tp / 0.09)
            place(music, pl, start + s16 * beat / 4, 0.16, -0.4 if s16 % 2 else 0.4)

    mix = drums + music * pump
    mix = np.tanh(mix * 1.3) / np.tanh(1.3)
    # Fade in over the cover and out under the end card.
    fade = np.ones(total)
    fi = int(0.05 * SR)
    fade[:fi] = np.linspace(0, 1, fi)
    fo0, fo1 = int((seconds - FADE_OUT) * SR), int(seconds * SR)
    fade[fo0:fo1] = np.linspace(1, 0, fo1 - fo0)
    fade[fo1:] = 0
    mix *= fade
    rms = np.sqrt(np.mean(mix[:, :fo0] ** 2)) or 1
    mix *= 0.17 / rms                           # ~-15 dBFS RMS: loud, not crushed
    peak = np.abs(mix).max()
    if peak > 0.97:
        mix *= 0.97 / peak
    return mix, arr[0]


def write_wav(path, mix):
    pcm = (np.clip(mix, -1, 1).T * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def loudness(path):
    out = subprocess.run(["ffmpeg", "-nostdin", "-i", path, "-map", "0:a", "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[\d.]+) dB", out.stderr)
    return float(m.group(1)) if m else None


def score(clip):
    name = os.path.basename(clip)[:-4]
    secs = duration(clip)
    mix, arr = render(name, secs)
    wav = clip + ".wav"
    write_wav(wav, mix)
    tmp = clip + ".part.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", clip, "-i", wav,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", "-movflags", "+faststart", tmp], check=True)
    os.remove(wav)
    os.replace(tmp, clip)
    vol = loudness(clip)
    if vol is None or vol < -30:
        return arr, f"soundtrack is missing or near-silent ({vol} dB)"
    if abs(duration(clip) - secs) > 0.2:
        return arr, f"length changed to {duration(clip):.2f}s from {secs:.2f}s"
    return arr, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dir", nargs="?")
    ap.add_argument("--wav")
    ap.add_argument("--name", default="streakpros")
    ap.add_argument("--seconds", type=float, default=15)
    a = ap.parse_args()
    if a.wav:
        mix, arr = render(a.name, a.seconds)
        write_wav(a.wav, mix)
        print(f"wrote {a.wav} ({arr}, {a.seconds}s)")
        return
    ok, bad = 0, []
    for clip in sorted(glob.glob(os.path.join(a.dir, "*.mp4"))):
        arr, why = score(clip)
        name = os.path.basename(clip)
        if why:
            bad.append(name)
            print(f"FAIL {name}: {why}")
        else:
            ok += 1
            print(f"ok   {name} ({arr})")
    print(f"{ok} clips have a soundtrack, {len(bad)} failed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
