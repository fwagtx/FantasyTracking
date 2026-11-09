#!/usr/bin/env bash
# Fetches the Kokoro-82M voice model (Apache-2.0) from npm, since Hugging Face is blocked in some sandboxes.
#   model:  npm "kokoro-q8-shards" = onnx-community/Kokoro-82M-v1.0-ONNX model_quantized.onnx, split in 6 (checksum below)
#   voices: npm "kokoro-js" (published by hexgrad, Kokoro's author) ships every voice file
# Output: models/model_quantized.onnx and models/voices.npz (not committed; ~95 MB)
set -euo pipefail
cd "$(dirname "$0")"
SHA=fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478
mkdir -p models && tmp=$(mktemp -d)
(cd "$tmp" && npm pack kokoro-q8-shards@1.0.0 kokoro-js@1.2.1 --silent >/dev/null && for f in *.tgz; do mkdir -p "${f%.tgz}" && tar xzf "$f" -C "${f%.tgz}"; done)
cat "$tmp"/kokoro-q8-shards-1.0.0/package/kokoro-q8.part{0,1,2,3,4,5}.bin > models/model_quantized.onnx
echo "$SHA  models/model_quantized.onnx" | sha256sum -c -
python3 -I - "$tmp/kokoro-js-1.2.1/package/voices" models/voices.npz <<'PY'
import glob, os, sys, numpy as np
v = {os.path.basename(f)[:-4]: np.fromfile(f, dtype=np.float32).reshape(510, 1, 256) for f in sorted(glob.glob(sys.argv[1] + "/*.bin"))}
np.savez(sys.argv[2], **v); print(len(v), "voices")
PY
rm -rf "$tmp"
