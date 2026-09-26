#!/usr/bin/env python3
"""
Extrae y organiza los modelos y precios de OpenAI.

Genera:
  - openai_models.html (tabla interactiva filtrable y ordenable)

Datos extraídos dinámicamente desde:
  https://developers.openai.com/api/docs/pricing.md
  (versión Markdown: trae las tablas completas de precios)

Uso:
    python3 gen-openai-models.py
    python3 gen-openai-models.py --out datos
"""

import argparse
import json
import os
import re
import sys

from common import (
    build_header_cells,
    parse_cost,
    parse_family_version,
    resolve_transitive_superseded,
    stamp_updated,
)

PAGE_URL = "https://developers.openai.com/api/docs/pricing"
MD_URL = "https://developers.openai.com/api/docs/pricing.md"

# Palabras que indican precio promocional TEMPORAL
PROMO_RE = re.compile(
    r'promotional pricing|at least through (january|february|march|april|may|june|july|august|september|october|november|december)\s+\d',
    re.IGNORECASE,
)

# Columnas finales (contexto corto como principal + largo como extra)
COLS = [
    ("name", "Modelo"),
    ("condition", "Condición"),
    ("priceEntry", "Entrada ($/M)"),
    ("priceCacheRead", "Entrada Caché ($/M)"),
    ("priceCacheWrite", "Escritura Caché ($/M)"),
    ("priceExit", "Salida ($/M)"),
    ("priceEntryLong", "Entrada >272K ($/M)"),
    ("priceExitLong", "Salida >272K ($/M)"),
    ("notes", "Notas"),
]

HEADER_GROUPS = [
    (None, [("name", "Modelo")]),
    (None, [("condition", "Condición")]),
    ("Precio contexto corto ($/M)", [
        ("priceEntry", "Entrada"),
        ("priceCacheRead", "Entrada Caché"),
        ("priceCacheWrite", "Escritura Caché"),
        ("priceExit", "Salida"),
    ]),
    ("Precio contexto largo >272K ($/M)", [
        ("priceEntryLong", "Entrada"),
        ("priceExitLong", "Salida"),
    ]),
    (None, [("notes", "Notas")]),
]


def parse_md_tables(md: str) -> list[dict]:
    """Parsea la tabla Markdown 'Standard pricing data' de OpenAI."""
    models: list[dict] = []
    lines = md.splitlines()
    i = 0
    in_standard_table = False

    while i < len(lines):
        line = lines[i].strip()
        if re.match(r'###\s+Standard pricing data', line):
            in_standard_table = True
        elif re.match(r'###\s+', line) and in_standard_table:
            in_standard_table = False

        if in_standard_table and line.startswith('|') and i + 1 < len(lines) and re.match(r'^\|[\s:\-|]+\|\s*$', lines[i + 1].strip()):
            headers = [h.strip().lower() for h in line.strip('|').split('|')]
            i += 2
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                row = dict(zip(headers, cells))
                raw_name = row.get('model', '')
                if raw_name and raw_name.lower() != 'model':
                    # Detectar condición (p.ej. "<272K context length")
                    condition = ''
                    m_cond = re.search(r'\((.*?)\)', raw_name)
                    if m_cond:
                        condition = m_cond.group(1).strip()
                    name = re.sub(r'\s*\(.*?\)', '', raw_name).strip()

                    models.append({
                        'name': name,
                        'condition': condition,
                        'priceEntry': row.get('short context input', ''),
                        'priceCacheRead': row.get('short context cached input', ''),
                        'priceCacheWrite': row.get('short context cache writes', ''),
                        'priceExit': row.get('short context output', ''),
                        'priceEntryLong': row.get('long context input', ''),
                        'priceExitLong': row.get('long context output', ''),
                    })
                i += 1
            continue
        i += 1
    return models


def load_openai_models() -> list[dict]:
    """Descarga el Markdown oficial de OpenAI y lo parsea."""
    import subprocess

    def fetch(url: str) -> str:
        result = subprocess.run(
            ['curl', '-sL', '-H', 'User-Agent: Mozilla/5.0', url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"curl failed: {result.stderr[:200]}")
        return result.stdout

    md = fetch(MD_URL)
    models = parse_md_tables(md)
    if not models:
        raise RuntimeError(f"No se encontraron tablas parseables en {MD_URL}")

    # Detectar promos: patrón "MODEL's promotional pricing is available..."
    promo_names = {}
    for m in re.finditer(r"([A-Za-z][\w.\- ]+?)['\u2019]s promotional pricing", md):
        # Normalizar: 'GPT-5.6 Sol' -> 'gpt-5.6-sol'
        norm = re.sub(r'[\s]+', '-', m.group(1).strip().lower())
        promo_names[norm] = 'Precio promocional temporal'

    for entry in models:
        entry['_isDeprecated'] = False
        entry['_supersededBy'] = ''
        entry['_promo'] = ''
        if entry['name'].lower() in promo_names:
            entry['_promo'] = promo_names[entry['name'].lower()]

    # Detección de modelos superados (misma familia, versión superior, coste <=)
    direct_superseded: dict[int, int] = {}
    for i, m1 in enumerate(models):
        f1, v1 = parse_family_version(m1.get('name', ''))
        e1 = parse_cost(m1.get('priceEntry'))
        x1 = parse_cost(m1.get('priceExit'))
        cr1 = parse_cost(m1.get('priceCacheRead'))

        best_target = None
        best_ver = v1
        for j, m2 in enumerate(models):
            if i == j:
                continue
            f2, v2 = parse_family_version(m2.get('name', ''))
            if f1 == f2 and v2 > best_ver:
                e2 = parse_cost(m2.get('priceEntry'))
                x2 = parse_cost(m2.get('priceExit'))
                cr2 = parse_cost(m2.get('priceCacheRead'))
                if e2 <= e1 and x2 <= x1 and cr2 <= cr1:
                    best_target = j
                    best_ver = v2

        if best_target is not None:
            direct_superseded[i] = best_target

    resolve_transitive_superseded(models, direct_superseded)

    print(f"Obtenidos {len(models)} modelos desde la web: {MD_URL}")
    return models


def clean_model(m: dict) -> dict:
    """Devuelve una fila estructurada con cabeceras en español."""
    row: dict[str, str] = {}
    for key, label in COLS:
        v = m.get(key, '')
        if v is None:
            v = ''
        row[label] = v
    row['_isDeprecated'] = m.get('_isDeprecated', False)
    row['_supersededBy'] = m.get('_supersededBy', '')
    row['_promo'] = m.get('_promo', '')
    return row


def write_html(rows: list[dict], path: str) -> None:
    headers = [h for _, h in COLS]
    data = json.dumps(rows, ensure_ascii=False)
    header_cells = build_header_cells(HEADER_GROUPS, COLS)

    html = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Modelos y Precios de OpenAI</title>
<style>
  :root { color-scheme: dark light; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 24px; background-color: #0d1117; color: #c9d1d9; }
  h1 { font-size: 1.5rem; margin: 0 0 6px; color: #f0f6fc; display: flex; align-items: center; gap: 10px; }
  .badge-openai { background: #10a37f; color: #ffffff; font-size: 11px; padding: 3px 8px; border-radius: 12px; text-transform: uppercase; font-weight: bold; }
  .toolbar { display: flex; gap: 12px; align-items: center; margin: 16px 0; flex-wrap: wrap; }
  input[type=search] { padding: 8px 12px; border: 1px solid #30363d; border-radius: 6px;
                        background: #161b22; color: #f0f6fc; width: 280px; font-size: 13px; outline: none; }
  input[type=search]:focus { border-color: #58a6ff; box-shadow: 0 0 0 3px rgba(56,139,253,0.3); }
  .table-wrap { overflow: auto; border: 1px solid #30363d; border-radius: 8px; background: #161b22; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; text-align: left; }
  thead th { position: sticky; background: #21262d; color: #f0f6fc; cursor: pointer;
               user-select: none; padding: 8px 10px; white-space: nowrap; z-index: 1; border-bottom: 1px solid #30363d; }
  thead tr:first-child th { top: 0; }
  thead tr:last-child th { top: 33px; }
  thead th:hover { background: #30363d; }
  thead th .arrow { opacity: .5; font-size: 11px; margin-left: 4px; }
  tbody td { padding: 8px 10px; border-top: 1px solid #21262d; white-space: nowrap; color: #c9d1d9; }
  tbody tr:hover { background: #1f242c; }
  tbody tr.row-deprecated { opacity: 0.55; }
  tbody tr.row-deprecated:hover { opacity: 0.85; background: #261f22; }
  tbody tr.row-deprecated td:nth-child(2) { text-decoration: line-through; text-decoration-color: #f85149; text-decoration-thickness: 2px; }
  .tag-deprecated { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #da363322; color: #f85149; border: 1px solid #da363366; text-decoration: none; vertical-align: middle; }
  .tag-promo { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #bb800922; color: #e3b341; border: 1px solid #bb800966; vertical-align: middle; cursor: help; }
  td.numeric, th.numeric { text-align: right; }
  td.notes { white-space: normal; min-width: 160px; max-width: 300px; color: #8b949e; font-size: 12px; }
  .muted { color: #8b949e; }
  a { color: #58a6ff; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .back-link { display: inline-flex; align-items: center; gap: 6px; margin-bottom: 14px; font-size: 13px;
               color: #8b949e; padding: 5px 12px; border: 1px solid #30363d; border-radius: 6px; background: #161b22; }
  .back-link:hover { border-color: #58a6ff; color: #58a6ff; text-decoration: none; }
</style>
</head>
<body>
<a class="back-link" href="./index.html">&#8592; Volver al índice</a>
<h1>
  <svg height="28" width="28" viewBox="0 0 24 24" fill="currentColor" style="vertical-align: middle;">
    <path d="M22.28 9.82a5.98 5.98 0 0 0-.52-4.91 6.05 6.05 0 0 0-6.51-2.9A6.07 6.07 0 0 0 4.98 4.18a5.98 5.98 0 0 0-4 2.9 6.05 6.05 0 0 0 .75 7.1 5.98 5.98 0 0 0 .51 4.91 6.05 6.05 0 0 0 6.51 2.9A5.98 5.98 0 0 0 13.26 24a6.06 6.06 0 0 0 5.77-4.21 5.99 5.99 0 0 0 4-2.9 6.06 6.06 0 0 0-.75-7.07zm-9.02 12.61a4.48 4.48 0 0 1-2.88-1.04l.14-.08 4.78-2.76a.79.79 0 0 0 .39-.68v-6.74l2.02 1.17a.07.07 0 0 1 .04.05v5.58a4.5 4.5 0 0 1-4.49 4.5zm-9.66-4.13a4.47 4.47 0 0 1-.54-3.01l.14.09 4.78 2.76a.77.77 0 0 0 .78 0l5.84-3.37v2.33a.08.08 0 0 1-.03.06L9.74 19.95a4.5 4.5 0 0 1-6.14-1.65zM2.34 7.9a4.49 4.49 0 0 1 2.37-1.97v5.68a.77.77 0 0 0 .39.68l5.83 3.37-2.02 1.16a.08.08 0 0 1-.07 0L4 14.03a4.5 4.5 0 0 1-1.66-6.14zm16.6 3.86-5.83-3.39 2.01-1.16a.08.08 0 0 1 .07 0l4.83 2.79a4.49 4.49 0 0 1-.68 8.1v-5.68a.79.79 0 0 0-.4-.66zm2.01-3.02-.14-.09-4.78-2.78a.78.78 0 0 0-.78 0L9.41 9.24V6.91a.07.07 0 0 1 .03-.06l4.83-2.79a4.49 4.49 0 0 1 6.68 4.66zm-12.64 4.13-2.02-1.16a.08.08 0 0 1-.04-.06V6.1a4.49 4.49 0 0 1 7.37-3.45l-.14.08L8.06 5.5a.79.79 0 0 0-.39.68zm1.1-2.37 2.6-1.5 2.6 1.5v3l-2.6 1.5-2.6-1.5z"/>
  </svg>
  Modelos y Precios de OpenAI
  <span class="badge-openai">OpenAI</span>
</h1>
<p class="muted">Datos referenciados desde la documentación oficial de <a href="%%PAGE_URL%%" target="_blank">%%PAGE_URL%%</a>.
<br>Actualizado: %%UPDATED%%. Haz clic en cualquier columna para ordenar. Filtra libremente por nombre o condición.</p>
<div class="toolbar">
  <input type="search" id="filter" placeholder="Filtrar modelos de OpenAI...">
  <span class="muted" id="count"></span>
</div>
<div class="table-wrap">
<table id="tbl">
    <thead>%%HEADER_CELLS%%</thead>
  <tbody></tbody>
</table>
</div>
<script>
const DATA = %%DATA%%;
const HEADERS = %%HEADERS%%;
let sortKey = 'Modelo';
let sortAsc = true;
let filterText = '';

const numericCols = ['Entrada ($/M)', 'Entrada Caché ($/M)', 'Escritura Caché ($/M)', 'Salida ($/M)', 'Entrada >272K ($/M)', 'Salida >272K ($/M)'];

function fmtPrice(raw) {
    // Precios con un solo decimal se muestran con 2: $0.1 -> $0.10
    const m = String(raw).match(/^([$]?)(\\d+)\\.(\\d)$/);
    return m ? m[1] + m[2] + '.' + m[3] + '0' : String(raw);
}

function cellValue(r, k) {
    const raw = r[k] || '';
    if (k === 'Modelo') {
        let nameHtml = raw;
        if (r._isDeprecated) {
            nameHtml += ' <span class="tag-deprecated" title="Superado en prestaciones/precio por ' + (r._supersededBy || 'versión superior') + '">Superado por ' + (r._supersededBy || 'versión más reciente') + '</span>';
        }
        if (r._promo) {
            const tip = String(r._promo).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
            nameHtml += ' <span class="tag-promo" title="' + tip + '">Promo</span>';
        }
        return nameHtml;
    }
    if (k === 'Notas') {
        if (!raw) return '<span class="muted">-</span>';
        return '<span class="notes">' + raw + '</span>';
    }
    if (numericCols.includes(k)) {
        if (!raw || raw === '-') return '<span class="muted">-</span>';
        const num = parseFloat(String(raw).replace(/[^0-9.\\-]/g, '')) || 0;
        return '<span class="numeric" data-num="' + num + '">' + fmtPrice(raw) + '</span>';
    }
    return raw || '';
}

function compareRows(a, b) {
    const ka = sortKey;
    if (numericCols.includes(ka)) {
        const va = parseFloat(String(a[ka] || '').replace(/[^0-9.\\-]/g, '')) || 0;
        const vb = parseFloat(String(b[ka] || '').replace(/[^0-9.\\-]/g, '')) || 0;
        return sortAsc ? va - vb : vb - va;
    }
    const va = String(a[ka] || '').toLowerCase();
    const vb = String(b[ka] || '').toLowerCase();
    if (va < vb) return sortAsc ? -1 : 1;
    if (va > vb) return sortAsc ? 1 : -1;
    return 0;
}

function render() {
    const tbody = document.querySelector('#tbl tbody');
    let rows = DATA.filter(r => Object.values(r).some(v => String(v).toLowerCase().includes(filterText)));
    rows.sort(compareRows);
    document.querySelector('#count').textContent = rows.length + ' modelos encontrados';
    tbody.innerHTML = rows.map(r => '<tr class="' + (r._isDeprecated ? 'row-deprecated' : '') + '">' + HEADERS.map(k => '<td class="' + (k === 'Notas' ? 'notes' : '') + '">' + cellValue(r, k) + '</td>').join('') + '</tr>').join('');
    document.querySelectorAll('thead th[data-k]').forEach(th => {
        th.querySelector('.arrow').textContent = th.dataset.k === sortKey ? (sortAsc ? '\\u25b2' : '\\u25bc') : '';
        if (numericCols.includes(th.dataset.k)) th.classList.add('numeric'); else th.classList.remove('numeric');
    });
}

function fixSticky() {
    const head = document.querySelector('#tbl thead');
    if (!head) return;
    const r1 = head.querySelector('tr:first-child');
    const r2 = head.querySelector('tr:last-child');
    if (r1 && r2 && r1 !== r2) {
        const h = r1.offsetHeight;
        r2.querySelectorAll('th').forEach(th => th.style.top = h + 'px');
    }
}

document.querySelectorAll('thead th[data-k]').forEach(th => th.addEventListener('click', () => {
    if (th.dataset.k === sortKey) { sortAsc = !sortAsc; }
    else { sortKey = th.dataset.k; sortAsc = true; }
    render();
}));

document.querySelector('#filter').addEventListener('input', e => { filterText = e.target.value.trim().toLowerCase(); render(); });

render();
fixSticky();
window.addEventListener('resize', fixSticky);
</script>
</body>
</html>
"""
    html = html.replace('%%HEADER_CELLS%%', header_cells)
    html = html.replace('%%PAGE_URL%%', PAGE_URL)
    html = stamp_updated(html)
    html = html.replace('%%DATA%%', data)
    html = html.replace('%%HEADERS%%', json.dumps(headers, ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print("HTML ->", path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Modelos y precios de OpenAI -> HTML")
    parser.add_argument("--out", default=".", help="directorio de salida (por defecto: .)")
    args = parser.parse_args()

    try:
        models = load_openai_models()
    except Exception as e:
        print('ERROR:', e)
        return 1

    rows = [clean_model(m) for m in models]
    out_dir = args.out.rstrip('/')
    os.makedirs(out_dir, exist_ok=True)
    write_html(rows, f"{out_dir}/openai_models.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
