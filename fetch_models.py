#!/usr/bin/env python3
"""
Descarga https://models.dev/api.json (descarga única, ~5,4 MB) y particiona
por proveedor controlado a data/providers/<id>.json + data/meta.json.
Cachea logos a data/logos/<provider>.svg.

Uso:
    python3 fetch_models.py --out data
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import API_URL, LOGO_URL, PROVIDERS

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def fetch(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as e:
            last_err = e
            print(f"fetch: intento {attempt}/{retries} fallido para {url}: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"No se pudo descargar {url} tras {retries} intentos: {last_err}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--providers", default=",".join(PROVIDERS))
    args = ap.parse_args()

    wanted = [p.strip() for p in args.providers.split(",") if p.strip()]
    os.makedirs(args.out, exist_ok=True)
    prov_dir = os.path.join(args.out, "providers")
    os.makedirs(prov_dir, exist_ok=True)
    logo_dir = os.path.join(args.out, "logos")
    os.makedirs(logo_dir, exist_ok=True)

    raw = fetch(API_URL)
    digest = hashlib.sha256(raw).hexdigest()
    full = json.loads(raw.decode("utf-8"))
    print(f"api.json: {len(raw)} bytes, {len(full)} providers, sha256={digest[:12]}")

    errors = []
    saved = {}
    for pid in wanted:
        pdata = full.get(pid)
        if not pdata:
            errors.append({"provider": pid, "message": "no existe en api.json", "date": datetime.now(timezone.utc).isoformat()})
            print(f"AVISO: {pid} no está en api.json")
            continue
        path = os.path.join(prov_dir, f"{pid}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pdata, f, ensure_ascii=False)
        saved[pid] = len((pdata.get("models") or {}))
        print(f"  {pid}: {saved[pid]} modelos -> {path}")
        # logo (opcional, no bloquea)
        try:
            svg = fetch(LOGO_URL.format(provider=pid), timeout=20, retries=2)
            if svg.lstrip().startswith(b"<"):
                with open(os.path.join(logo_dir, f"{pid}.svg"), "wb") as f:
                    f.write(svg)
        except Exception as e:
            print(f"  logo {pid}: no disponible ({e})")

    meta = {
        "source": API_URL,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "bytes": len(raw),
        "sha256": digest,
        "providers": saved,
        "errors": errors,
    }
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "errors.json"), "w", encoding="utf-8") as f:
        json.dump(errors, f, ensure_ascii=False, indent=1)
    print(f"meta -> {args.out}/meta.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
