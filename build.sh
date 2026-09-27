#!/usr/bin/env bash
# Genera todos los HTML en public/ (lo mismo en local y en CI).
# Uso: ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

OUT="public"
mkdir -p "$OUT"
python3 gen-copilot-models.py --out "$OUT"
python3 gen-opencode-go-models.py --out "$OUT"
python3 gen-cursor-models.py --out "$OUT"
python3 gen-google-models.py --out "$OUT"
python3 gen-claude-models.py --out "$OUT"
python3 gen-openai-models.py --out "$OUT"
python3 gen-mistral-models.py --out "$OUT"
python3 gen-cloudflare-models.py --out "$OUT"
cp index.html "$OUT/index.html"
ls -lh "$OUT"/
