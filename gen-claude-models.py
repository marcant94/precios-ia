#!/usr/bin/env python3
"""
Extrae y organiza los modelos y precios de Anthropic Claude.

Genera:
  - claude_models.html (tabla interactiva filtrable y ordenable)

Datos extraídos dinámicamente desde:
  https://platform.claude.com/docs/en/about-claude/pricing.md
  (versión Markdown: trae la tabla completa de modelos)

Uso:
    python3 gen-claude-models.py
    python3 gen-claude-models.py --out datos
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

PAGE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
MD_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"

# Palabras que indican precio promocional TEMPORAL
PROMO_RE = re.compile(
    r'introductory pricing|limited time|through (january|february|march|april|may|june|july|august|september|october|november|december)\s+\d',
    re.IGNORECASE,
)

# Columnas finales
COLS = [
    ("name", "Modelo"),
    ("status", "Estado"),
    ("priceEntry", "Entrada ($/M)"),
    ("priceCacheWrite5m", "Escritura Caché 5m ($/M)"),
    ("priceCacheWrite1h", "Escritura Caché 1h ($/M)"),
    ("priceCacheRead", "Lectura Caché ($/M)"),
    ("priceExit", "Salida ($/M)"),
    ("notes", "Notas"),
]

HEADER_GROUPS = [
    (None, [("name", "Modelo")]),
    (None, [("status", "Estado")]),
    ("Precio ($/M)", [
        ("priceEntry", "Entrada"),
        ("priceCacheWrite5m", "Escritura 5m"),
        ("priceCacheWrite1h", "Escritura 1h"),
        ("priceCacheRead", "Lectura Caché"),
        ("priceExit", "Salida"),
    ]),
    (None, [("notes", "Notas")]),
]


def md_link_text(cell: str) -> str:
    """Extrae el texto visible de un enlace Markdown [texto](url)."""
    m = re.match(r'\[(.*?)\]\(.*?\)\s*$', cell.strip())
    return m.group(1).strip() if m else cell.strip()


def parse_md_tables(md: str) -> list[dict]:
    """Parsea la tabla Markdown de modelos de Claude."""
    models: list[dict] = []
    lines = md.splitlines()
    i = 0
    in_model_table = False

    while i < len(lines):
        line = lines[i].strip()
        if re.match(r'##\s+Model pricing', line):
            in_model_table = True
        elif re.match(r'##\s+', line) and in_model_table:
            in_model_table = False

        if in_model_table and line.startswith('|') and i + 1 < len(lines) and re.match(r'^\|[\s:\-|]+\|\s*$', lines[i + 1].strip()):
            headers = [h.strip().lower() for h in line.strip('|').split('|')]
            i += 2
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                row = dict(zip(headers, cells))
                raw_name = row.get('model', '')
                name = md_link_text(raw_name)
                if name and name.lower() != 'model':
                    # Detectar estado (retired / limited availability)
                    status = ''
                    if 'retired' in raw_name.lower():
                        status = 'Retirado'
                    elif 'limited availability' in raw_name.lower():
                        status = 'Disponibilidad limitada'

                    # Limpiar nombre: quitar paréntesis con estado y residuos
                    clean_name = re.sub(r'\s*\[.*?\]\(.*?\)', '', raw_name)
                    clean_name = re.sub(r'\s*\(.*?\)', '', clean_name)
                    clean_name = re.sub(r'\s+', ' ', clean_name).strip()

                    def clean_price(v: str) -> str:
                        """Convierte '$10 / MTok' -> '$10'."""
                        m = re.search(r'\$([0-9]+(?:\.[0-9]+)?)', v)
                        return f"${m.group(1)}" if m else ''

                    # Notas al pie: "1" al final de la celda de cache hits
                    cache_read = row.get('cache hits and refreshes', '')
                    fn = ''
                    m_fn = re.search(r'(\d+)\s*$', cache_read)
                    if m_fn:
                        fn = m_fn.group(1)
                        cache_read = re.sub(r'\s*\d+\s*$', '', cache_read).strip()

                    models.append({
                        'name': clean_name,
                        'status': status,
                        'priceEntry': clean_price(row.get('base input tokens', '')),
                        'priceCacheWrite5m': clean_price(row.get('5m cache writes', '')),
                        'priceCacheWrite1h': clean_price(row.get('1h cache writes', '')),
                        'priceCacheRead': clean_price(cache_read),
                        'priceExit': clean_price(row.get('output tokens', '')),
                        'footnote': fn,
                        '_isRetired': status == 'Retirado',
                    })
                i += 1
            continue
        i += 1
    return models


def load_claude_models() -> list[dict]:
    """Descarga el Markdown oficial de Anthropic y lo parsea."""
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

    # Extraer la nota al pie 1 (cache hits 0.025x en Fable/Mythos 5.1)
    footnote1 = ''
    m = re.search(r'\*1\s+(.*?)(?:\n\n|\n\*|\n<)', md, re.S)
    if m:
        footnote1 = re.sub(r'\s+', ' ', m.group(1)).strip()

    # Nota de pricing introductorio (Sonnet 5)
    promo_note = ''
    m = re.search(r'<Note id="claude-sonnet-5-introductory-pricing">\s*(.*?)\s*</Note>', md, re.S)
    if m:
        promo_note = re.sub(r'\s+', ' ', m.group(1)).strip()

    for entry in models:
        entry['_isDeprecated'] = entry.get('_isRetired', False)
        entry['_supersededBy'] = ''
        entry['_promo'] = ''
        notes = []
        if entry.get('footnote') == '1' and footnote1:
            notes.append(footnote1)
        if promo_note and 'Sonnet 5' in entry['name'] and '4.' not in entry['name']:
            entry['_promo'] = promo_note
        entry['notes'] = '; '.join(notes)

    # Detección de modelos superados (misma familia, versión superior, coste <=)
    direct_superseded: dict[int, int] = {}
    for i, m1 in enumerate(models):
        f1, v1 = parse_family_version(m1.get('name', ''))
        e1 = parse_cost(m1.get('priceEntry'))
        x1 = parse_cost(m1.get('priceExit'))
        cr1 = parse_cost(m1.get('priceCacheRead'))
        cw1 = parse_cost(m1.get('priceCacheWrite5m'))

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
                cw2 = parse_cost(m2.get('priceCacheWrite5m'))
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
<title>Modelos y Precios de Claude</title>
<style>
  :root { color-scheme: dark light; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 24px; background-color: #0d1117; color: #c9d1d9; }
  h1 { font-size: 1.5rem; margin: 0 0 6px; color: #f0f6fc; display: flex; align-items: center; gap: 10px; }
  .badge-claude { background: #d97757; color: #ffffff; font-size: 11px; padding: 3px 8px; border-radius: 12px; text-transform: uppercase; font-weight: bold; }
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
  .tag-status { padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .status-ga { background: #23863622; color: #3fb950; border: 1px solid #23863666; }
  .status-limited { background: #9e6a0322; color: #d29922; border: 1px solid #9e6a0366; }
  .status-retired { background: #da363322; color: #f85149; border: 1px solid #da363366; }
  td.numeric, th.numeric { text-align: right; }
  td.notes { white-space: normal; min-width: 200px; max-width: 400px; color: #8b949e; font-size: 12px; }
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
    <path d="M17.3 9.2c-.5-1.2-1.6-2-2.9-2.1-.4-1.3-1.6-2.2-3-2.2s-2.6.9-3 2.2c-1.3.1-2.4.9-2.9 2.1-.6.2-1.1.6-1.5 1.1-.9 1.2-.7 2.9.4 3.9l4.5 4.5c.7.7 1.6 1 2.5 1s1.8-.3 2.5-1l4.5-4.5c1.1-1 1.3-2.7.4-3.9-.4-.5-.9-.9-1.5-1.1z"/>
  </svg>
  Modelos y Precios de Claude
  <span class="badge-claude">Anthropic</span>
</h1>
<p class="muted">Datos referenciados desde la documentación oficial de <a href="%%PAGE_URL%%" target="_blank">%%PAGE_URL%%</a>.
<br>Actualizado: %%UPDATED%%. Haz clic en cualquier columna para ordenar. Filtra libremente por nombre o estado.</p>
<div class="toolbar">
  <input type="search" id="filter" placeholder="Filtrar modelos de Claude...">
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

const numericCols = ['Entrada ($/M)', 'Escritura Caché 5m ($/M)', 'Escritura Caché 1h ($/M)', 'Lectura Caché ($/M)', 'Salida ($/M)'];

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
    if (k === 'Estado') {
        if (!raw) return '<span class="tag-status status-ga">GA</span>';
        const cls = raw === 'Retirado' ? 'status-retired' : 'status-limited';
        return '<span class="tag-status ' + cls + '">' + raw + '</span>';
    }
    if (k === 'Notas') {
        if (!raw) return '<span class="muted">-</span>';
        return '<span class="notes">' + raw + '</span>';
    }
    if (numericCols.includes(k)) {
        if (!raw || raw === '-') return '<span class="muted">-</span>';
        const num = parseFloat(String(raw).replace(/[^0-9.\\-]/g, '')) || 0;
        return '<span class="numeric" data-num="' + num + '">' + raw + '</span>';
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
    parser = argparse.ArgumentParser(description="Modelos y precios de Claude -> HTML")
    parser.add_argument("--out", default=".", help="directorio de salida (por defecto: .)")
    args = parser.parse_args()

    try:
        models = load_claude_models()
    except Exception as e:
        print('ERROR:', e)
        return 1

    rows = [clean_model(m) for m in models]
    out_dir = args.out.rstrip('/')
    os.makedirs(out_dir, exist_ok=True)
    write_html(rows, f"{out_dir}/claude_models.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
