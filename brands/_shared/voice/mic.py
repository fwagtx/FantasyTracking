"""Make a synthetic voice track sound recorded: warmth and presence EQ, gentle compression,
a very small room, and a faint noise floor (dead-silent gaps are a giveaway).
Usage: python3 mic.py in.wav out.wav
"""
import os, subprocess, sys

FF = os.environ.get("FFMPEG") or subprocess.run([sys.executable, "-c", "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"], capture_output=True, text=True).stdout.strip()
src, out = sys.argv[1], sys.argv[2]
chain = ("[0]aresample=48000,highpass=f=75,"
         "equalizer=f=180:t=q:w=1:g=1.5,equalizer=f=3200:t=q:w=1.2:g=2,equalizer=f=9000:t=q:w=1:g=-1.5,"
         "acompressor=threshold=-22dB:ratio=3:attack=6:release=90:makeup=2,"
         "aecho=0.9:0.55:17|29:0.06|0.04[v];"
         "anoisesrc=color=pink:amplitude=0.0016:sample_rate=48000,highpass=f=120[n];"
         "[v][n]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5")
subprocess.run([FF, "-y", "-loglevel", "error", "-i", src, "-filter_complex", chain, "-ac", "1", out], check=True)
print(out)
