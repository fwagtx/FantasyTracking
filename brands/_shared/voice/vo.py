"""Voiceover with word timings, using Kokoro-82M (Apache-2.0, runs offline).

Usage: python3 vo.py <script.json> <out.wav> <out-timings.json>
script.json: {"voice": "af_heart", "speed": 1.1, "lang": "en-us", "lead": 0.25, "mode": "flow",
              "phrases": [{"text": "There's a Pac-Man hidden on the map.", "pause": 0.18}, ...]}
"text" is what the captions show; the voice says it through PRONOUNCE (or a phrase's own "say").
Timings keep the same shape in both modes: phrases[] (t0, t1) and words[] (w, t0, t1, p), so scenes
that key their beats to P[i].t0 work unchanged.

mode "flow" (default): the script is spoken in long takes (several sentences per model pass, split only at
sentence ends), so the intonation runs on across phrases the way a person reads a paragraph: no fresh
sentence-start and no full-stop fall on every fragment. The model leaves its own pauses at punctuation;
each phrase boundary is found in the audio at the quiet gap nearest where the phoneme count predicts it,
then that gap is topped up or trimmed to the "pause" the script asks for. Word times inside a phrase are
split in proportion to phoneme count, as before.
mode "phrases": the old renderer, one model pass per phrase (kept for comparison).
"""
import json, os, re, sys
import numpy as np, soundfile as sf
from kokoro_onnx import Kokoro

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 24000
MODEL = os.environ.get("KOKORO_MODEL", os.path.join(HERE, "models/model_quantized.onnx"))

# How words are SAID, when that differs from how they're SHOWN in captions.
# The model reads all-caps words as acronyms ("BAD" -> "B, A, D"), so the code is fed in lowercase.
# Keys are matched case-sensitively on the bare word; punctuation around the word is kept.
PRONOUNCE = {
    "BAD": "bad",
    "CODE": "code",
    "v42.40": "version forty-two point four oh",
    "FNCS": "F N C S",
    "LTM": "limited time mode",
    "A$AP": "A-sap",
}
SENTENCE_END = ".!?…"
TAKE_PHONEMES = 380        # one model pass holds up to 510 phonemes; shorter takes align more precisely
GAP_FLOOR = 0.035          # a quiet run at least this long counts as a pause the model left
QUIET_DB = -38             # relative to the loudest frame of the take
FRAME = 0.005
SNAP = 0.4                 # how far (s) a phrase boundary may move from its predicted spot to land in a gap
SLACK = 0.05               # the model's own pause may run this much past the script's "pause" before it is cut (cfg "slack")


def spoken(tok):
    m = re.match(r"^([^\w]*)(.*?)([^\w]*)$", tok)
    lead, core, tail = m.groups()
    return lead + PRONOUNCE.get(core, core) + tail


def say_text(ph):
    return ph.get("say") or " ".join(spoken(t) for t in ph["text"].split())


def trim(a, thr=0.012, pad=0.035):
    idx = np.where(np.abs(a) > thr)[0]
    if not len(idx):
        return a
    s, e = max(0, idx[0] - int(pad * SR)), min(len(a), idx[-1] + int(pad * SR))
    return a[s:e]


def load_voice(k, voice):
    # a blend like "af_heart:0.6+af_bella:0.4" mixes voices into one that no stock voice matches
    if "+" in voice or ":" in voice:
        parts = [v.split(":") for v in voice.split("+")]
        return sum(float(w) * k.voices[n] for n, w in parts) / sum(float(w) for _, w in parts)
    return voice


def phoneme_weights(k, toks, lang):
    """Spoken length of each caption word, by phoneme count (floor for short words)."""
    w = []
    for tok in toks:
        core = re.sub(r"[^\w'\-]", "", spoken(tok))
        try:
            n = len(k.tokenizer.phonemize(core, lang)) if core else 1
        except Exception:
            n = len(core)
        w.append(max(2, n))
    return w


def quiet_runs(a):
    """(start_s, end_s) of every quiet run in the take that is long enough to be a pause."""
    f = int(FRAME * SR)
    n = len(a) // f
    rms = np.sqrt((a[: n * f].reshape(n, f) ** 2).mean(1))
    quiet = rms <= rms.max() * 10 ** (QUIET_DB / 20)
    runs, start = [], None
    for i, q in enumerate(list(quiet) + [False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * FRAME >= GAP_FLOOR:
                runs.append((start * FRAME, i * FRAME))
            start = None
    return runs


def _match(preds, cands, skip_cost):
    """Ordered best fit of predicted boundary times to candidate gap times.

    Dynamic programme over (boundary, candidate): each boundary takes a later candidate than the one before,
    or none at a fixed cost. Returns one candidate index (or None) per boundary.
    """
    B, G = len(preds), len(cands)
    INF = float("inf")
    # cost[i][j]: best cost placing boundaries 0..i-1 using candidates < j
    cost = [[INF] * (G + 1) for _ in range(B + 1)]
    back = [[None] * (G + 1) for _ in range(B + 1)]
    for j in range(G + 1):
        cost[0][j] = 0.0
    for i in range(1, B + 1):
        for j in range(G + 1):
            # skip boundary i-1
            c, b = cost[i - 1][j] + skip_cost, ("skip", j)
            # or take candidate j-1 for it
            if j > 0:
                take = cost[i - 1][j - 1] + abs(cands[j - 1] - preds[i - 1])
                if take < c:
                    c, b = take, ("take", j - 1)
                if cost[i][j - 1] < c:      # or leave candidate j-1 unused
                    c, b = cost[i][j - 1], ("pass", j - 1)
            cost[i][j], back[i][j] = c, b
    out, i, j = [None] * B, B, G
    while i > 0:
        kind, jj = back[i][j]
        if kind == "take":
            out[i - 1] = jj; i, j = i - 1, jj
        elif kind == "skip":
            i = i - 1
        else:
            j = jj
    return out


def align_take(k, a, phrases, lang):
    """Find where each phrase of a take starts and ends inside its audio.

    Sentence-ending phrases must end in one of the long quiet runs (the model always pauses at a full stop),
    matched in order by best fit to where the phoneme count predicts them. Inside each sentence the
    clause boundaries are predicted again from that sentence's real span and matched to the shorter runs.
    Returns per-phrase (start, end) in seconds plus, per boundary, the quiet gap (start, end) it sits in.
    """
    dur = len(a) / SR
    n = len(phrases)
    ends = [ph["text"].rstrip()[-1:] for ph in phrases]
    weights = [sum(phoneme_weights(k, ph["text"].split(), lang)) + (6 if e in SENTENCE_END else 3 if e in ",;:" else 0)
               for ph, e in zip(phrases, ends)]
    gaps = [g for g in quiet_runs(a) if g[0] > 0.05 and g[1] < dur - 0.05]
    centers = [(g[0] + g[1]) / 2 for g in gaps]

    def predict(lo_t, hi_t, idx):
        """Boundary times after each of idx[:-1], spreading [lo_t, hi_t] by phoneme weight."""
        tot = sum(weights[i] for i in idx)
        out, acc = [], 0
        for i in idx[:-1]:
            acc += weights[i]
            out.append(lo_t + (hi_t - lo_t) * acc / tot)
        return out

    bounds = [None] * (n - 1)
    # level 1: sentence ends against long gaps
    sent_b = [i for i in range(n - 1) if ends[i] in SENTENCE_END]
    long_idx = [j for j, g in enumerate(gaps) if g[1] - g[0] >= 0.15]
    pred_all = predict(0.0, dur, list(range(n)))
    got = _match([pred_all[i] for i in sent_b], [centers[j] for j in long_idx], skip_cost=0.8)
    for i, m in zip(sent_b, got):
        if m is not None:
            bounds[i] = gaps[long_idx[m]]
    # level 2: inside each sentence, clause boundaries against the gaps in that stretch
    anchors = [(-1, (0.0, 0.0))] + [(i, bounds[i]) for i in sent_b if bounds[i] is not None] + [(n - 1, (dur, dur))]
    for (i0, g0), (i1, g1) in zip(anchors, anchors[1:]):
        idx = list(range(i0 + 1, i1 + 1))
        inner = [i for i in idx[:-1] if bounds[i] is None]
        if not inner:
            continue
        lo_t, hi_t = g0[1], g1[0]
        preds = predict(lo_t, hi_t, idx)
        cand = [j for j, g in enumerate(gaps) if lo_t < g[0] and g[1] < hi_t]
        want = [preds[idx.index(i)] for i in inner]
        got = _match(want, [centers[j] for j in cand], skip_cost=0.3)
        for i, m, p in zip(inner, got, want):
            bounds[i] = gaps[cand[m]] if m is not None else (p, p)
    for i in range(n - 1):
        if bounds[i] is None:
            bounds[i] = (pred_all[i], pred_all[i])
    spans, s = [], 0.0
    for b in bounds:
        spans.append((s, b[0]))
        s = b[1]
    spans.append((s, dur))
    return spans, bounds


def render_flow(k, cfg, voice_arr):
    speed, lang = cfg.get("speed", 1.1), cfg.get("lang", "en-us")
    P = cfg["phrases"]
    slack = cfg.get("slack", SLACK)
    # takes: consecutive phrases, broken only at sentence ends, kept under TAKE_PHONEMES phonemes
    takes, cur, size = [], [], 0
    for i, ph in enumerate(P):
        n = len(k.tokenizer.phonemize(say_text(ph), lang))
        if cur and size + n > TAKE_PHONEMES and P[cur[-1]]["text"].rstrip()[-1:] in SENTENCE_END:
            takes.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n + 1
    if cur:
        takes.append(cur)

    t = cfg.get("lead", 0.25)
    chunks, words, phrases = [np.zeros(int(t * SR), dtype=np.float32)], [], []
    for take in takes:
        text = " ".join(say_text(P[i]) for i in take)
        a, _ = k.create(text, voice=voice_arr, speed=speed, lang=lang, trim=True)
        a = trim(a.astype(np.float32), pad=0.02)
        spans, bounds = align_take(k, a, [P[i] for i in take], lang)
        missed = sum(1 for gs, ge in bounds if ge == gs)
        if missed:
            print(f"  take of {len(take)} phrases: {missed} boundary(ies) not found in a pause, placed by estimate")
        # adjust each internal gap to the pause the script asks for, working from the end so earlier times hold
        pieces, cursor = [], 0
        shifts = []  # (position_s_in_original, delta_s) applied, to move later spans
        for (gs, ge), i in zip(bounds, take[:-1]):
            want = P[i].get("pause", 0.12)
            have = ge - gs
            if have == 0:
                shifts.append((gs, 0.0))
                continue
            mid = int(((gs + ge) / 2) * SR)
            if have < want:
                pieces.append(a[cursor:mid]); pieces.append(np.zeros(int((want - have) * SR), dtype=np.float32))
                cursor = mid
                shifts.append((gs, want - have))
            elif have > want + slack:
                cut = have - want - slack
                half = int(cut / 2 * SR)
                pieces.append(a[cursor:mid - half])
                cursor = mid + half
                shifts.append((gs, -cut))
            else:
                shifts.append((gs, 0.0))
        pieces.append(a[cursor:])
        a2 = np.concatenate(pieces)

        def moved(x):
            d = 0.0
            for pos, delta in shifts:
                if x > pos + 1e-6:
                    d += delta
            return x + d

        for (s0, s1), i in zip(spans, take):
            ph = P[i]
            ps, pe = t + moved(s0), t + moved(s1 - 1e-6)
            toks = ph["text"].split()
            w = phoneme_weights(k, toks, lang)
            total, acc = sum(w), 0.0
            # the last word of a phrase carries the punctuation's silence: give the words 94% of the span
            span = max(0.05, (pe - ps) * 0.94)
            for tok, wi in zip(toks, w):
                ws = ps + span * acc / total
                acc += wi
                words.append({"w": tok, "t0": round(ws, 3), "t1": round(ps + span * acc / total, 3), "p": i})
            phrases.append({"text": ph["text"], "t0": round(ps, 3), "t1": round(pe, 3)})
        chunks.append(a2)
        gap = P[take[-1]].get("pause", 0.12)
        chunks.append(np.zeros(int(gap * SR), dtype=np.float32))
        t += len(a2) / SR + gap
    return chunks, words, phrases


def render_phrases(k, cfg, voice_arr):
    speed, lang = cfg.get("speed", 1.1), cfg.get("lang", "en-us")
    t = cfg.get("lead", 0.25)
    chunks, words, phrases = [np.zeros(int(t * SR), dtype=np.float32)], [], []
    for i, ph in enumerate(cfg["phrases"]):
        a, _ = k.create(say_text(ph), voice=voice_arr, speed=ph.get("speed", speed), lang=lang)
        a = trim(a.astype(np.float32))
        dur = len(a) / SR
        toks = ph["text"].split()
        w = phoneme_weights(k, toks, lang)
        total, acc = sum(w), 0.0
        lead_in, tail = 0.04, 0.06
        span = max(0.05, dur - lead_in - tail)
        for tok, wi in zip(toks, w):
            s = t + lead_in + span * acc / total
            acc += wi
            words.append({"w": tok, "t0": round(s, 3), "t1": round(t + lead_in + span * acc / total, 3), "p": i})
        phrases.append({"text": ph["text"], "t0": round(t, 3), "t1": round(t + dur, 3)})
        chunks.append(a)
        gap = ph.get("pause", 0.12)
        chunks.append(np.zeros(int(gap * SR), dtype=np.float32))
        t += dur + gap
    return chunks, words, phrases


def main():
    script, out_wav, out_json = sys.argv[1:4]
    cfg = json.load(open(script))
    k = Kokoro(MODEL, os.path.join(HERE, "models/voices.npz"))
    voice = cfg.get("voice", "af_heart")
    voice_arr = load_voice(k, voice)
    mode = cfg.get("mode", "flow")
    chunks, words, phrases = (render_phrases if mode == "phrases" else render_flow)(k, cfg, voice_arr)
    audio = np.concatenate(chunks)
    peak = float(np.abs(audio).max()) or 1.0
    audio = audio * (0.89 / peak)
    sf.write(out_wav, audio, SR)
    json.dump({"voice": voice, "speed": cfg.get("speed", 1.1), "mode": mode, "duration": round(len(audio) / SR, 3), "phrases": phrases, "words": words}, open(out_json, "w"), indent=1)
    print(f"{out_wav}: {len(audio) / SR:.2f}s, {len(words)} words, voice {voice}, mode {mode}")


main()
