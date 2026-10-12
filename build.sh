#!/usr/bin/env bash
# Orquestador resiliente: base models.dev + enrichers opcionales.
# Un proveedor que falla aporta cero extras pero NO tumba el build.
# Los errores se registran en data/build_errors.json y acaban en el banner rojo.
# Uso: ./build.sh
set -uo pipefail
cd "$(dirname "$0")"

DATA="data"
OUT="public"
mkdir -p "$DATA" "$OUT"
BUILD_ERRORS="$DATA/build_errors.json"
echo "[]" > "$BUILD_ERRORS"
NOW=$(date -u +%FT%TZ)

record_error() {
  # $1 provider, $2 message
  python3 - "$1" "$2" "$NOW" "$BUILD_ERRORS" << 'PY'
import json, sys
prov, msg, now, path = sys.argv[1:5]
try:
    errs = json.load(open(path, encoding="utf-8"))
except Exception:
    errs = []
errs.append({"provider": prov, "message": msg, "date": now})
json.dump(errs, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PY
}

echo "== 1/4 fetch models.dev =="
if python3 fetch_models.py --out "$DATA"; then
  echo "fetch OK"
else
  record_error "*" "falló descarga de models.dev api.json"
fi

echo "== 2/4 enrichers (opcionales) =="
if python3 enrich_opencode_go.py --out "$DATA/extras"; then
  echo "opencode-go extras OK"
else
  record_error "opencode-go" "enricher falló (doc Go inaccesible o cambió)"
fi
if python3 enrich_groq.py --out "$DATA/extras"; then
  echo "groq extras OK"
else
  record_error "groq" "enricher falló (docs Groq inaccesibles o cambiaron)"
fi

echo "== 3/4 ensamblar datos =="
if ! python3 build_data.py --data "$DATA" --out "$DATA"; then
  echo "ERROR catastrófico en build_data"
  exit 1
fi

echo "== 4/4 componer página =="
if ! python3 build_page.py --data "$DATA" --out "$OUT"; then
  echo "ERROR catastrófico en build_page"
  exit 1
fi

echo "== errores registrados =="
cat "$DATA/build_errors.json"
ls -lh "$OUT"/ "$OUT"/data/ "$OUT"/assets/
exit 0
