#!/usr/bin/env python3
"""
Enricher Groq: extrae velocidad, límites Developer (TPM/RPM), contexto,
max completion y estado desde la doc oficial.

Fuente: https://console.groq.com/docs/models.md
Salida: data/extras/groq.json — lista de {modelId, speed, limits, context,
maxCompletion, maxFile, status, enterprise}
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import fetch_html

MD_URL = "https://console.groq.com/docs/models.md"

MODEL_CELL_RE = re.compile(
    r'\[\!\[(?P<prov>[^\]]*)\]\([^)]*\)(?P<display>[^\]]*)\]\([^)]*\)(?P<trail>.*)$'
)
PROVIDER_MAP = {
    'alibaba cloud': 'Alibaba', 'canopy labs': 'Canopy',
    'minimaxai': 'MiniMax', 'minimax': 'MiniMax',
    'meta': 'Meta', 'openai': 'OpenAI',
}


def split_md_row(line: str):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def parse_model_cell(cell: str):
    m = MODEL_CELL_RE.match(cell.strip())
    if not m:
        text = re.sub(r'\[|\]|\(.*?\)', '', cell).strip()
        return '', text, ''
    prov = PROVIDER_MAP.get(m.group('prov').strip().lower(), m.group('prov').strip())
    display = m.group('display').strip()
    trail = m.group('trail').strip()
    model_id = trail
    enterprise = False
    if trail.startswith('Enterprise') and len(trail) > len('Enterprise'):
        model_id = trail[len('Enterprise'):]
        enterprise = True
    return prov, display, model_id, enterprise


def clean(v: str) -> str:
    v = v.replace('\\-', '-').strip()
    if v in ('-', '\\-'):
        return ''
    v = re.sub(r'(TPM|RPM|ASH)(\d)', r'\1 \2', v)
    return re.sub(r'\s+', ' ', v).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/extras")
    args = ap.parse_args()

    md = fetch_html(MD_URL)
    lines = md.splitlines()
    status = ''
    records = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        low = line.lower()
        if low.startswith('##'):
            if 'production' in low:
                status = 'Producción'
            elif 'preview' in low:
                status = 'Preview'
            elif 'deprecated' in low:
                status = 'Deprecated'
        if line.startswith('|') and i + 1 < len(lines) and re.match(r'^\|[\s:\-|]+\|\s*$', lines[i + 1].strip()):
            headers = [h.strip().lower() for h in split_md_row(line)]
            if not any('model' in h for h in headers):
                i += 1
                continue
            idx = {h: n for n, h in enumerate(headers)}

            def col(row, *names):
                for n in names:
                    for h, pos in idx.items():
                        if n in h and pos < len(row):
                            return clean(row[pos])
                return ''
            i += 2
            while i < len(lines) and lines[i].strip().startswith('|'):
                row = split_md_row(lines[i])
                cell = row[idx[[h for h in idx if 'model' in h][0]]] if row else ''
                try:
                    prov, display, model_id, enterprise = parse_model_cell(cell)
                except Exception:
                    prov, display, model_id, enterprise = '', clean(cell), '', False
                records.append({
                    "modelId": model_id,
                    "display": display,
                    "providerName": prov,
                    "speed": col(row, 'speed'),
                    "limits": col(row, 'rate limit'),
                    "context": col(row, 'context'),
                    "maxCompletion": col(row, 'max completion'),
                    "maxFile": col(row, 'max file'),
                    "status": status,
                    "enterprise": enterprise,
                })
                i += 1
            continue
        i += 1

    if not records:
        raise RuntimeError("No se parsearon modelos de Groq")

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "groq.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print(f"Groq extras: {len(records)} registros -> {path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR enrich_groq: {e}")
        sys.exit(1)
