#!/usr/bin/env bash
# Gets a fresh container ready to make LA TIENDITA BAD videos. Safe to run every time: it skips whatever is
# already there. Run from anywhere: bash brands/tiendabad/auto/setup.sh
set -euo pipefail
cd "$(dirname "$0")/../.."          # brands/
python3 -c "import kokoro_onnx, soundfile, imageio_ffmpeg, spellchecker, boto3, spylls, PIL" 2>/dev/null ||
  python3 -m pip install -q kokoro-onnx soundfile imageio-ffmpeg pyspellchecker boto3 spylls pillow 2>&1 | grep -v "running pip as the 'root' user" || true
python3 -c "import kokoro_onnx, soundfile, imageio_ffmpeg, spellchecker, boto3, spylls, PIL"
if [ ! -f _shared/voice/models/model_quantized.onnx ] || [ ! -f _shared/voice/models/voices.npz ]; then
  bash _shared/voice/get_models.sh
fi
# full Spanish spelling dictionary for the checklist (Hunspell, from npm "dictionary-es")
if [ ! -f _shared/qa/dict/es/index.dic ]; then
  tmp=$(mktemp -d)
  (cd "$tmp" && npm pack dictionary-es@4.0.0 --silent >/dev/null && tar xzf dictionary-es-4.0.0.tgz)
  mkdir -p _shared/qa/dict/es && cp "$tmp"/package/index.aff "$tmp"/package/index.dic _shared/qa/dict/es/ && rm -rf "$tmp"
fi
node -e "try{require('playwright')}catch(e){require(require('child_process').execSync('npm root -g').toString().trim()+'/playwright')}"
python3 -c "import imageio_ffmpeg; imageio_ffmpeg.get_ffmpeg_exe()" >/dev/null
[ -f tiendabad/sources/map-labels.png ] || python3 tiendabad/auto/fetch_sources.py >/dev/null
# hosting settings: TB_R2_BUCKET / TB_R2_ACCOUNT_ID, or the owner's BrandPros checkout (one file is enough)
# (a read-only sparse copy goes in ~/.cache, never over an existing checkout)
CACHE="$HOME/.cache/latienditabad-bp"
if [ -z "${TB_R2_BUCKET:-}" ] && [ ! -f "${BRANDPROS_DIR:-/home/user/fwagtx/brandpros}/brand/_shared/store.py" ] && [ ! -f "$CACHE/brand/_shared/store.py" ]; then
  rm -rf "$CACHE" && git clone -q --depth 1 --filter=blob:none --sparse -b claude/eloquent-lovelace-vu2quk https://github.com/fwagtx/brandpros "$CACHE" &&
    git -C "$CACHE" sparse-checkout set brand/_shared || echo "STOP: no hosting settings (TB_R2_* or a BrandPros checkout)"
fi
python3 _shared/store.py ls >/dev/null
python3 _shared/store.py sweep 48
echo "setup ok"
