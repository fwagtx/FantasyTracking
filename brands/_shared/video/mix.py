"""Original sound design for BAD channel videos, synthesized from scratch (no samples, nothing extracted from any game).
Lays effects and an original music bed under a silent render, then normalizes to -14 LUFS with a -2.6 dBTP ceiling (sharp effects overshoot after AAC encoding; the post limit is -1 dBTP).
Usage: python3 mix.py silent.mp4 out.mp4 <duration> '<cues json [[t, name], ...]>' [voice.wav]
With a voice track: the voice sits on top, music ducks under it (sidechain), effects sit between.
"""
import json, os, re, subprocess, sys, tempfile

FF = os.environ.get("FFMPEG", "ffmpeg")
SR = 48000
# t = seconds since the effect started
SFX = {
    # soft UI tick, like a menu cursor
    "tick":    ("0.5*sin(2*PI*1650*t)*exp(-70*t)+0.25*sin(2*PI*3300*t)*exp(-90*t)", 0.08),
    # card flip: short noise flick + body
    "flip":    ("0.45*(2*random(0)-1)*exp(-55*t)+0.35*sin(2*PI*(520-900*t)*t)*exp(-22*t)", 0.18),
    # map ping: two-note rising blip
    "ping":    ("0.42*sin(2*PI*880*t)*exp(-9*t)*lt(t,0.09)+0.42*sin(2*PI*1320*t)*exp(-6*(t-0.09))*gte(t,0.09)", 0.55),
    # whoosh: shaped noise
    "whoosh":  ("0.38*(2*random(0)-1)*pow(sin(PI*t/0.42),2)", 0.42),
    # reveal stinger: bright chord with a fast attack
    "reveal":  ("(0.22*sin(2*PI*523.25*t)+0.2*sin(2*PI*659.25*t)+0.2*sin(2*PI*783.99*t)+0.12*sin(2*PI*1046.5*t))*min(1,t*60)*exp(-2.6*t)", 1.4),
    # level-up chime: three quick rising notes
    "chime":   ("0.32*sin(2*PI*784*t)*exp(-12*t)*lt(t,0.08)+0.32*sin(2*PI*988*t)*exp(-12*(t-0.08))*between(t,0.08,0.16)+0.38*sin(2*PI*1319*t)*exp(-4*(t-0.16))*gte(t,0.16)", 1.0),
    # locked: low muted thud
    "locked":  ("0.6*sin(2*PI*(160-140*t)*t)*exp(-14*t)+0.12*(2*random(0)-1)*exp(-50*t)", 0.32),
    # news sting: rising sweep into a hit
    "sting":   ("0.25*sin(2*PI*(220*t+900*t*t))*min(1,t*8)*lt(t,0.35)+(0.5*sin(2*PI*(110-40*(t-0.35))*(t-0.35))*exp(-6*(t-0.35))+0.2*(2*random(0)-1)*exp(-30*(t-0.35)))*gte(t,0.35)", 0.9),
    # ticker: tiny data blips
    "blips":   ("0.18*sin(2*PI*2400*t)*exp(-60*mod(t,0.07))*lt(t,0.42)", 0.45),
    # camera shutter for "screenshot this"
    "shutter": ("0.5*(2*random(0)-1)*exp(-60*t)+0.35*(2*random(0)-1)*exp(-70*(t-0.07))*gte(t,0.07)", 0.25),
    # marker scribble on paper
    "marker":  ("0.16*(2*random(0)-1)*(0.6+0.4*sin(2*PI*31*t))*pow(sin(PI*t/0.3),2)", 0.3),
    # soft pop for numbers
    "pop":     ("0.5*sin(2*PI*(420*t+2400*t*t))*exp(-26*t)", 0.15),
    # record scratch-ish stop for a twist beat
    "scratch": ("0.35*(2*random(0)-1)*sin(2*PI*(900-2500*t)*t)*exp(-6*t)", 0.35),
    # in-game notification chime
    "notify":  ("0.3*sin(2*PI*1046.5*t)*exp(-9*t)+0.25*sin(2*PI*1568*t)*exp(-7*(t-0.06))*gte(t,0.06)", 0.6),
}
BPM = 118
B = 60 / BPM
# original music bed: kick, offbeat hat, a 4-bar minor bass line and a soft pad (all synthesized)
BED = ("0.5*sin(2*PI*52*mod(t,{b})+14*(1-exp(-28*mod(t,{b}))))*exp(-8*mod(t,{b}))"
       "+0.06*(2*random(0)-1)*exp(-80*mod(t+{h},{b}))"
       "+0.16*sin(2*PI*(55*(1+0.5*gte(mod(t,{b4}),{b2})-0.25*gte(mod(t,{b4}),{b3})))*t)*(0.55+0.45*exp(-6*mod(t,{h})))"
       "+0.045*(sin(2*PI*220*t)+sin(2*PI*261.63*t)+sin(2*PI*329.63*t))*(0.7+0.3*sin(2*PI*0.5*t))").format(
    b=B, h=B / 2, b2=B * 8, b3=B * 12, b4=B * 16)

def run(args):
    subprocess.run([FF, "-y", "-loglevel", "error", *args], check=True)

def main():
    silent, out, dur, cues = sys.argv[1], sys.argv[2], float(sys.argv[3]), json.loads(sys.argv[4])
    voice = sys.argv[5] if len(sys.argv) > 5 else None
    tmp = tempfile.mkdtemp()
    used = sorted({c[1] for c in cues})
    for n in used:
        expr, d = SFX[n]
        run(["-f", "lavfi", "-i", f"aevalsrc=exprs='{expr}':s={SR}:d={d}", os.path.join(tmp, n + ".wav")])
    bed = os.path.join(tmp, "bed.wav")
    run(["-f", "lavfi", "-i", f"aevalsrc=exprs='{BED}':s={SR}:d={dur}", "-af", f"lowpass=f=5000,afade=t=in:d=0.3,afade=t=out:st={dur-1.0}:d=1.0", bed])
    inputs = ["-i", silent, "-i", bed]
    for n in used:
        inputs += ["-i", os.path.join(tmp, n + ".wav")]
    idx = {n: i + 2 for i, n in enumerate(used)}
    parts, labels = ["[1]volume=0.32[b]"], ["[b]"]
    for n in used:
        times = [c[0] for c in cues if c[1] == n]
        outs = "".join(f"[{n}{j}]" for j in range(len(times)))
        parts.append(f"[{idx[n]}]asplit={len(times)}{outs}" if len(times) > 1 else f"[{idx[n]}]anull{outs}")
        for j, t in enumerate(times):
            ms = int(t * 1000)
            parts.append(f"[{n}{j}]adelay={ms}|{ms},apad[{n}d{j}]")
            labels.append(f"[{n}d{j}]")
    if voice:
        inputs += ["-i", voice]
        vi = len(used) + 2
        # voice: gentle cleanup and presence; music: ducked by the voice; effects: a bit lower than without voice
        parts.append(f"[{vi}]aresample={SR},highpass=f=80,acompressor=threshold=-20dB:ratio=3:attack=5:release=80,equalizer=f=3000:t=q:w=1.2:g=2.5,volume=1.0,asplit=2[vo][vokey]")
        parts[0] = "[1]volume=0.55[bed0]"
        parts.append("[bed0][vokey]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=250[b]")
        sfx = labels[1:]
        if sfx:
            parts.append(f"{''.join(sfx)}amix=inputs={len(sfx)}:normalize=0:duration=longest,volume=0.6[fx]")
            labels = ["[b]", "[fx]", "[vo]"]
        else:
            labels = ["[b]", "[vo]"]
    parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first,atrim=0:{dur}[pre]")
    mixed = os.path.join(tmp, "mixed.wav")
    run([*inputs, "-filter_complex", ";".join(parts), "-map", "[pre]", "-ac", "2", mixed])
    # two-pass loudness normalization to -14 LUFS integrated, -2.6 dBTP; linear mode can still overshoot the ceiling,
    # so a limiter at -3.1 dBFS catches what is left before AAC encoding adds its own overshoot
    p = subprocess.run([FF, "-hide_banner", "-i", mixed, "-af", "loudnorm=I=-14:TP=-2.6:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    m = json.loads(re.findall(r"\{[^{}]+\}", p.stderr)[-1])
    ln = (f"loudnorm=I=-14:TP=-2.6:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
          f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    run(["-i", silent, "-i", mixed, "-filter_complex", f"[1]{ln},alimiter=limit=0.7:level=false,aresample={SR}[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", out])

main()
