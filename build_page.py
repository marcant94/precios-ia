#!/usr/bin/env python3
"""Compone public/: index.html + assets/ + data/ desde assets/ y data/.

Espera en --data: all.json, errors.json, meta.json (generados por build_data.py
con --out apuntando al mismo directorio) y opcionalmente build_errors.json
y logos/.

Uso: python3 build_page.py --data data --out public
"""

import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="public")
    args = ap.parse_args()

    template = os.path.join(ROOT, "assets", "template.html")
    if not os.path.exists(template):
        print("ERROR catastrófico: falta assets/template.html")
        return 1
    if not os.path.exists(os.path.join(args.data, "all.json")):
        print("ERROR catastrófico: no existe all.json en", args.data)
        return 1

    os.makedirs(args.out, exist_ok=True)
    shutil.copy(template, os.path.join(args.out, "index.html"))

    assets_dst = os.path.join(args.out, "assets")
    if os.path.exists(assets_dst):
        shutil.rmtree(assets_dst)
    shutil.copytree(os.path.join(ROOT, "assets"), assets_dst,
                    ignore=shutil.ignore_patterns("template.html"))

    data_dst = os.path.join(args.out, "data")
    os.makedirs(data_dst, exist_ok=True)
    for name in ("all.json", "errors.json", "meta.json"):
        src = os.path.join(args.data, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(data_dst, name))

    # logos: data/logos -> public/assets/logos
    logo_src = os.path.join(args.data, "logos")
    if os.path.isdir(logo_src):
        logo_dst = os.path.join(assets_dst, "logos")
        os.makedirs(logo_dst, exist_ok=True)
        for f in os.listdir(logo_src):
            if f.endswith(".svg"):
                shutil.copy(os.path.join(logo_src, f), os.path.join(logo_dst, f))

    # fusionar errores del build (fetch/enrich) en public/data/errors.json
    build_err_path = os.path.join(args.data, "build_errors.json")
    if os.path.exists(build_err_path):
        with open(build_err_path, encoding="utf-8") as f:
            extra = json.load(f)
        err_path = os.path.join(data_dst, "errors.json")
        current = []
        if os.path.exists(err_path):
            with open(err_path, encoding="utf-8") as f:
                current = json.load(f)
        seen = {(e.get("provider"), e.get("message")) for e in current}
        for e in extra:
            if (e.get("provider"), e.get("message")) not in seen:
                current.append(e)
        with open(err_path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=1)

    print(f"page -> {args.out}/index.html (+ assets/, data/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
