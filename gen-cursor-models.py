#!/usr/bin/env python3
"""
Extrae y organiza los modelos y precios de Cursor.

Genera:
  - cursor_models.html (tabla interactiva filtrable y ordenable)

Datos extraídos dinámicamente desde:
  https://cursor.com/docs/models-and-pricing.md
  (versión Markdown: trae las tablas completas con proveedor y notas,
  a diferencia del HTML que oculta filas tras "Show more models")

Uso:
    python3 gen-cursor-models.py
    python3 gen-cursor-models.py --out datos
"""

import argparse
import json
import os
import re
import sys

from common import (
    build_header_cells,
    clean_text,
    fetch_html,
    parse_cost,
    parse_family_version,
    resolve_transitive_superseded,
    stamp_updated,
)

PAGE_URL = "https://cursor.com/es/docs/models-and-pricing"
MD_URL = "https://cursor.com/docs/models-and-pricing.md"

# Columnas finales
COLS = [
    ("pool", "Pool"),
    ("name", "Modelo"),
    ("provider", "Proveedor"),
    ("priceEntry", "Entrada ($/M)"),
    ("priceCacheWrite", "Escritura Caché ($/M)"),
    ("priceCacheRead", "Lectura Caché ($/M)"),
    ("priceExit", "Salida ($/M)"),
    ("hidden", "Oculto"),
    ("notes", "Notas"),
]

HEADER_GROUPS = [
    (None, [("pool", "Pool")]),
    (None, [("name", "Modelo")]),
    (None, [("provider", "Proveedor")]),
    ("Precio ($/M)", [
        ("priceEntry", "Entrada"),
        ("priceCacheWrite", "Escritura Caché"),
        ("priceCacheRead", "Lectura Caché"),
        ("priceExit", "Salida"),
    ]),
    (None, [("hidden", "Oculto")]),
    (None, [("notes", "Notas")]),
]

# Palabras que indican precio promocional TEMPORAL en las notas
# (no valen descuentos estructurales como "90% discount on cached input")
PROMO_RE = re.compile(r'promotional pricing|limited time|through (january|february|march|april|may|june|july|august|september|october|november|december)\s+\d', re.IGNORECASE)


def md_link_text(cell: str) -> str:
    """Extrae el texto visible de un enlace Markdown [texto](url)."""
    m = re.match(r'\[(.*?)\]\(.*?\)\s*$', cell.strip())
    return m.group(1).strip() if m else cell.strip()


def parse_md_tables(md: str) -> list[dict]:
    """Parsea las tablas Markdown de modelos (Cursor Models + Other Models)."""
    models: list[dict] = []
    lines = md.splitlines()
    i = 0
    current_pool = ''
    while i < len(lines):
        line = lines[i].strip()
        if re.match(r'##\s+Cursor Models', line):
            current_pool = 'Cursor'
        elif re.match(r'##\s+(Other Models|Otros modelos)', line):
            current_pool = 'Otros'
        if line.startswith('|') and i + 1 < len(lines) and re.match(r'^\|[\s:\-|]+\|\s*$', lines[i + 1].strip()):
            headers = [h.strip().lower() for h in line.strip('|').split('|')]
            i += 2
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                row = dict(zip(headers, cells))
                name = md_link_text(row.get('model', ''))
                if name and current_pool in ('Cursor', 'Otros'):
                    notes = row.get('notes', '')
                    promo = notes if PROMO_RE.search(notes) else ''
                    # Extract "Hidden by default" flag and clean from notes
                    hidden = 'hidden by default' in notes.lower()
                    notes = re.sub(r'Hidden by default;?\s*', '', notes).strip()
                    if notes == '-':
                        notes = ''
                    models.append({
                        'pool': current_pool,
                        'name': name,
                        'provider': row.get('provider', ''),
                        'priceEntry': row.get('input', ''),
                        'priceCacheWrite': row.get('cache write', ''),
                        'priceCacheRead': row.get('cache read', ''),
                        'priceExit': row.get('output', ''),
                        'notes': notes,
                        'promo': promo,
                        '_hidden': hidden,
                    })
                i += 1
            continue
        i += 1
    return models


def load_cursor_models() -> list[dict]:
    """Descarga el Markdown oficial de Cursor y lo parsea."""
    md = fetch_html(MD_URL)
    models = parse_md_tables(md)
    if not models:
        raise RuntimeError(f"No se encontraron tablas parseables en {MD_URL}")

    for entry in models:
        entry['_isDeprecated'] = False
        entry['_supersededBy'] = ''

    # Detección de modelos superados (misma familia + pool, versión superior, coste <=)
    direct_superseded: dict[int, int] = {}
    for i, m1 in enumerate(models):
        f1, v1 = parse_family_version(m1.get('name', ''))
        p1 = m1.get('pool', '')
        e1 = parse_cost(m1.get('priceEntry'))
        x1 = parse_cost(m1.get('priceExit'))
        cr1 = parse_cost(m1.get('priceCacheRead'))
        cw1 = parse_cost(m1.get('priceCacheWrite'))

        best_target = None
        best_ver = v1
        for j, m2 in enumerate(models):
            if i == j:
                continue
            f2, v2 = parse_family_version(m2.get('name', ''))
            p2 = m2.get('pool', '')
            if f1 == f2 and p1 == p2 and v2 > best_ver:
                e2 = parse_cost(m2.get('priceEntry'))
                x2 = parse_cost(m2.get('priceExit'))
                cr2 = parse_cost(m2.get('priceCacheRead'))
                cw2 = parse_cost(m2.get('priceCacheWrite'))
                if e2 <= e1 and x2 <= x1 and cr2 <= cr1 and cw2 <= cw1:
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
    row['_promo'] = m.get('promo', '')
    row['_hidden'] = m.get('_hidden', False)
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
<title>Modelos y Precios de Cursor</title>
<style>
  :root { color-scheme: dark light; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 24px; background-color: #0d1117; color: #c9d1d9; }
  h1 { font-size: 1.5rem; margin: 0 0 6px; color: #f0f6fc; display: flex; align-items: center; gap: 10px; }
  .badge-cursor { background: #000000; color: #ffffff; font-size: 11px; padding: 3px 8px; border-radius: 12px; text-transform: uppercase; font-weight: bold; border: 1px solid #ffffff44; }
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
  .tag-pool { padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .pool-cursor { background: #1f6feb22; color: #58a6ff; border: 1px solid #1f6feb66; }
  .pool-otros { background: #6e768122; color: #8b949e; border: 1px solid #6e768166; }
  td.numeric, th.numeric { text-align: right; }
  td.notes { white-space: normal; min-width: 220px; max-width: 420px; color: #8b949e; font-size: 12px; }
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
    <path d="M11.9 2.6 3.5 8.2v7.6l8.4 5.6 8.4-5.6V8.2L11.9 2.6zm0 2.3 6.3 4.2-6.3 4.2-6.3-4.2 6.3-4.2zM5.7 10.7l4 2.6v5.2l-4-2.6v-5.2zm8.6 7.8v-5.2l4-2.6v5.2l-4 2.6z"/>
  </svg>
  Modelos y Precios de Cursor
  <span class="badge-cursor">Cursor</span>
</h1>
<p class="muted">Datos referenciados desde la documentación oficial de <a href="%%PAGE_URL%%" target="_blank">%%PAGE_URL%%</a>.
<br>Actualizado: %%UPDATED%%.<br>Haz clic en cualquier columna para ordenar. Filtra libremente por nombre, proveedor o notas.</p>
<div class="toolbar">
  <input type="search" id="filter" placeholder="Filtrar modelos de Cursor...">
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

const numericCols = ['Entrada ($/M)', 'Escritura Caché ($/M)', 'Lectura Caché ($/M)', 'Salida ($/M)'];

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
    if (k === 'Pool') {
        const cls = raw === 'Cursor' ? 'pool-cursor' : 'pool-otros';
        return '<span class="tag-pool ' + cls + '">' + raw + '</span>';
    }
    if (k === 'Oculto') {
        return r._hidden ? '<span style="color:#58a6ff;font-size:15px" title="Hidden by default">☑</span>' : '<span style="color:#30363d;font-size:15px">☐</span>';
    }
    if (k === 'Notas') {
        if (!raw || raw === '-') return '<span class="muted">-</span>';
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
        th.querySelector('.arrow').textContent = th.dataset.k === sortKey ? (sortAsc ? '▲' : '▼') : '';
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
    parser = argparse.ArgumentParser(description="Modelos y precios de Cursor -> HTML")
    parser.add_argument("--out", default=".", help="directorio de salida (por defecto: .)")
    args = parser.parse_args()

    try:
        models = load_cursor_models()
    except Exception as e:
        print('ERROR:', e)
        return 1

    rows = [clean_model(m) for m in models]
    out_dir = args.out.rstrip('/')
    os.makedirs(out_dir, exist_ok=True)
    write_html(rows, f"{out_dir}/cursor_models.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
