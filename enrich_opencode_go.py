#!/usr/bin/env python3
"""
Enricher OpenCode Go: extrae planes, límites mensuales, estimaciones de
peticiones, modelId y retención desde la consola y la doc oficial.

Fuentes (2026-11):
- https://opencode.ai/console/go — tabla "Limits" con id de modelo, nota,
  estimación de peticiones/5 h y límite mensual por plan (Go / Go Plus).
- https://opencode.ai/docs/console/go.md — tabla de retención de datos.

Salida: data/extras/opencode-go.json — lista de registros
{name, norm, plan, condition, monthlyBudget, req5h, reqWeekly, reqMonthly,
 modelId, retention, temporal, promo}

La doc reparte los topes así: 5 h = 20% del mensual y semana = 50%, de modo
que reqWeekly/reqMonthly se derivan de las peticiones/5 h publicadas.

Si falla, exit != 0 y el orquestador lo registra en errors.json
(proveedor aporta cero extras, pero la base models.dev sigue visible).
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import clean_text, fetch_html, normalize_name

CONSOLE_URL = "https://opencode.ai/console/go"
DOCS_URL = "https://opencode.ai/docs/console/go.md"

PLANS = ("Go", "Go Plus")
UNLIMITED = "Ilimitado"

PAIR_RE = re.compile(r"(Go(?: Plus)?):\s*([^;]+?)\s*(?:;|$)")


def parse_plan_values(text: str) -> dict:
    """'Go: 98; Go Plus: 392' -> {'Go': '98', 'Go Plus': '392'}."""
    return {m.group(1): m.group(2).strip() for m in PAIR_RE.finditer(text)}


def parse_console(html: str) -> dict:
    """Devuelve {modelId: {name, note, requests, monthly}}."""
    table = re.search(
        r'<table[^>]*class="console-go-models-table"[\s\S]*?</table>', html, re.IGNORECASE
    )
    if not table:
        raise RuntimeError("No se encontró la tabla de modelos en la consola de Go")

    def sr_only(chunk: str) -> str:
        m = re.search(r'<span class="sr-only">([^<]*)</span>', chunk)
        return clean_text(m.group(1)) if m else ""

    models = {}
    rows = re.finditer(
        r'<tr\b[^>]*data-model="([^"]+)"[^>]*>([\s\S]*?)</tr>', table.group(0), re.IGNORECASE
    )
    for m in rows:
        mid, row = m.group(1), m.group(2)
        name = re.search(r'data-layer="model-name"[^>]*title="([^"]*)"', row)
        note = re.search(r'data-layer="model-note"[^>]*>([^<]*)<', row)
        req_cell = re.search(
            r'<td[^>]*data-allowance-type="requests"[^>]*>([\s\S]*?)</td>', row, re.IGNORECASE
        )
        use_cell = re.search(
            r'<td[^>]*data-allowance-type="usage"[^>]*>([\s\S]*?)</td>', row, re.IGNORECASE
        )
        models[mid] = {
            "name": clean_text(name.group(1)) if name else mid,
            "note": clean_text(note.group(1)) if note else "",
            "requests": parse_plan_values(sr_only(req_cell.group(1)) if req_cell else ""),
            "monthly": parse_plan_values(sr_only(use_cell.group(1)) if use_cell else ""),
        }
    if not models:
        raise RuntimeError("La tabla de la consola no contiene filas de modelos")
    return models


def parse_retention(md: str) -> dict:
    """Tabla 'Model | Data retention' del markdown -> {norm: retención}."""
    out = {}
    for line in md.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 2 or cells[0].lower() == "model":
            continue
        if set(cells[0]) <= set("-: "):
            continue
        retention = re.sub(r"\\", "", cells[1]).rstrip("*").strip()
        out[normalize_name(cells[0])] = retention
    return out


def lookup_retention(name: str, table: dict) -> str:
    """Busca por nombre exacto y, si no, ignorando un sufijo numérico de versión
    ("DeepSeek V4.1 Flash 1017" frente a "DeepSeek V4.1 Flash" en la doc)."""
    norm = normalize_name(name)
    if norm in table:
        return table[norm]
    longer = [v for k, v in table.items() if norm.startswith(k) and norm[len(k):].isdigit()]
    shorter = [v for k, v in table.items() if k.startswith(norm) and k[len(norm):].isdigit()]
    candidates = longer or shorter
    return candidates[0] if len(candidates) == 1 else ""


def request_fields(value: str) -> tuple[str, str, str]:
    """Peticiones 5 h publicadas -> (5 h, semana, mes), derivando 50% y 100%."""
    if not value:
        return "", "", ""
    digits = value.replace(",", "")
    if not digits.isdigit():
        return value, value, value
    monthly = int(digits) * 5
    weekly = round(monthly * 0.5)
    return f"{int(digits):,}", f"{weekly:,}", f"{monthly:,}"


def normalize_budget(value: str) -> str:
    if value in ("∞", "unlimited", "Unlimited"):
        return UNLIMITED
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/extras")
    args = ap.parse_args()

    models = parse_console(fetch_html(CONSOLE_URL))
    try:
        retention = parse_retention(fetch_html(DOCS_URL))
    except Exception as e:
        print(f"retención: omitida ({e})")
        retention = {}

    records = []
    for mid, m in models.items():
        note = m["note"]
        temporal = bool(re.search(r"limited time|tiempo limitado", note, re.IGNORECASE))
        condition = re.sub(r"\s*·?\s*limited time\b", "", note, flags=re.IGNORECASE).strip(" ·")
        norm = normalize_name(m["name"])
        for plan in PLANS:
            monthly = m["monthly"].get(plan, "")
            if not monthly:
                continue
            req5h, reqWeekly, reqMonthly = request_fields(m["requests"].get(plan, ""))
            records.append({
                "name": m["name"],
                "norm": norm,
                "plan": plan,
                "condition": condition,
                "monthlyBudget": normalize_budget(monthly),
                "req5h": req5h,
                "reqWeekly": reqWeekly,
                "reqMonthly": reqMonthly,
                "modelId": mid,
                "retention": lookup_retention(m["name"], retention),
                "temporal": temporal,
                "promo": "",
            })

    if not records:
        raise RuntimeError("No se pudieron parsear límites de OpenCode Go")

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "opencode-go.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print(f"OpenCode Go extras: {len(records)} registros -> {path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR enrich_opencode_go: {e}")
        sys.exit(1)
