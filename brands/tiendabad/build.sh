#!/usr/bin/env bash
# TIENDA BAD: build one day's Spanish shop video.
# Run from a BrandPros checkout with this folder copied to brand/tiendabad (it uses brand/_shared).
#   bash brand/tiendabad/build.sh [YYYY-MM-DD]
set -euo pipefail
BRAND=$(cd "$(dirname "$0")/.." && pwd)
DATE=${1:-$(date -u +%F)}
DIR=$BRAND/tiendabad/shop/$DATE
python3 "$BRAND/tiendabad/shop/shop_pick.py" "$DATE"
cd "$DIR"
python3 "$BRAND/_shared/voice/vo.py" vo.json vo.wav timings.json
python3 -c "import json; open('timings.js','w').write('window.VO='+json.dumps(json.load(open('timings.json')), ensure_ascii=False)+';\n')"
cat plan.js timings.js > data.js
cd "$BRAND/_shared/video"
node render.mjs ../../tiendabad/shop/scene.js "$DIR/tiendabad-tienda-$DATE.mp4" --data "../../tiendabad/shop/$DATE/data.js" --voice "../../tiendabad/shop/$DATE/vo.wav"
