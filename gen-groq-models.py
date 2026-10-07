#!/usr/bin/env python3
"""
Extrae y organiza los modelos y precios de Groq (GroqCloud).

Genera:
  - groq_models.html (tabla interactiva filtrable y ordenable)

Datos extraídos dinámicamente desde:
  https://console.groq.com/docs/models.md
  (versión Markdown: trae las tablas Production / Preview con proveedor,
  velocidad, precio por 1M de tokens, límites del plan Developer y contexto)

Uso:
    python3 gen-groq-models.py
    python3 gen-groq-models.py --out datos
"""

import argparse
import json
import os
import re
import sys

from common import build_header_cells, fetch_html, stamp_updated

PAGE_URL = "https://console.groq.com/docs/models"
MD_URL = "https://console.groq.com/docs/models.md"

COLS = [
    ("status", "Estado"),
    ("name", "Modelo"),
    ("type", "Tipo"),
    ("provider", "Proveedor"),
    ("priceEntry", "Entrada ($/M)"),
    ("priceExit", "Salida ($/M)"),
    ("speed", "Velocidad (tok/s)"),
    ("context", "Contexto (tokens)"),
    ("limits", "Límites (Developer)"),
    ("notes", "Notas"),
]

HEADER_GROUPS = [
    (None, [("status", "Estado")]),
    (None, [("name", "Modelo")]),
    (None, [("type", "Tipo")]),
    (None, [("provider", "Proveedor")]),
    ("Precio ($/M tokens)", [
        ("priceEntry", "Entrada"),
        ("priceExit", "Salida"),
    ]),
    (None, [("speed", "Velocidad (tok/s)")]),
    (None, [("context", "Contexto (tokens)")]),
    (None, [("limits", "Límites (Developer)")]),
    (None, [("notes", "Notas")]),
]

# Celda típica: [![Meta](...logo...)Llama 3.1 8B](/docs/model/...)Enterprisellama-3.1-8b-instant
MODEL_CELL_RE = re.compile(
    r'\[\!\[(?P<prov>[^\]]*)\]\([^)]*\)(?P<display>[^\]]*)\]\([^)]*\)(?P<trail>.*)$'
)
PRICE_INPUT_RE = re.compile(r'\$([0-9]+(?:\.[0-9]+)?)\s*input', re.IGNORECASE)
PRICE_OUTPUT_RE = re.compile(r'\$([0-9]+(?:\.[0-9]+)?)\s*output', re.IGNORECASE)

PROVIDER_MAP = {
    'alibaba cloud': 'Alibaba',
    'canopy labs': 'Canopy',
    'minimaxai': 'MiniMax',
    'minimax': 'MiniMax',
    'meta': 'Meta',
    'openai': 'OpenAI',
}


def groq_tipo(model_id: str, display: str) -> str:
    low = f"{model_id} {display}".lower()
    if 'whisper' in low or 'orpheus' in low:
        return 'Voz'
    if 'prompt-guard' in low or 'prompt guard' in low:
        return 'Clasificación'
    return 'Texto'


def split_md_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip('|').split('|')]


def parse_model_cell(cell: str) -> tuple[str, str, str]:
    """Devuelve (proveedor, nombre visible, id de modelo)."""
    m = MODEL_CELL_RE.match(cell.strip())
    if not m:
        text = re.sub(r'\[|\]|\(.*?\)', '', cell).strip()
        return '', text, ''
    prov = PROVIDER_MAP.get(m.group('prov').strip().lower(),
                            m.group('prov').strip())
    display = m.group('display').strip()
    trail = m.group('trail').strip()
    model_id = trail
    if trail.startswith('Enterprise') and len(trail) > len('Enterprise'):
        model_id = trail[len('Enterprise'):]
    return prov, display, model_id


def clean_md_value(v: str) -> str:
    v = v.replace('\\-', '-').strip()
    if v == '-' or v == '\\-':
        return ''
    # '250K TPM1K RPM' -> '250K TPM 1K RPM'
    v = re.sub(r'(TPM|RPM|ASH)(\d)', r'\1 \2', v)
    return re.sub(r'\s+', ' ', v).strip()


def parse_price(raw: str) -> tuple[str, str, str]:
    """Devuelve (entrada, salida, nota). Precios no-token van tal cual a Entrada."""
    text = clean_md_value(raw)
    if not text or 'contact' in text.lower():
        return '', '', 'Precio bajo contacto comercial (Enterprise).'
    mi = PRICE_INPUT_RE.search(text)
    mo = PRICE_OUTPUT_RE.search(text)
    if mi and mo:
        return f"${mi.group(1)}", f"${mo.group(1)}", ''
    if mi:
        return f"${mi.group(1)}", '', ''
    # Precio con otra unidad ($/hora, $/1M caracteres): mostrar tal cual
    return text[:80], '', ''


def parse_md_tables(md: str) -> list[dict]:
    models: list[dict] = []
    lines = md.splitlines()
    status = ''
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

        if line.startswith('|') and i + 1 < len(lines) and re.match(
            r'^\|[\s:\-|]+\|\s*$', lines[i + 1].strip()
        ):
            headers = [h.strip().lower() for h in split_md_row(line)]
            if not any('model' in h for h in headers):
                i += 1
                continue
            idx = {h: n for n, h in enumerate(headers)}

            def col(row: list[str], *names: str) -> str:
                for n in names:
                    for h, pos in idx.items():
                        if n in h and pos < len(row):
                            return row[pos]
                return ''

            i += 2
            while i < len(lines) and lines[i].strip().startswith('|'):
                row = split_md_row(lines[i])
                while len(row) < len(headers):
                    row.append('')
                prov, display, model_id = parse_model_cell(col(row, 'model'))
                if not display and not model_id:
                    i += 1
                    continue
                name = display or model_id
                price_raw = col(row, 'price')
                entry, exit_, price_note = parse_price(price_raw)
                notes: list[str] = []
                if model_id and model_id.lower() != name.lower():
                    notes.append(f"ID: {model_id}")
                if price_note:
                    notes.append(price_note)
                max_completion = clean_md_value(col(row, 'max completion'))
                max_file = clean_md_value(col(row, 'max file'))
                if max_completion:
                    notes.append(f"Máx. salida: {max_completion} tokens")
                if max_file:
                    notes.append(f"Fichero máx.: {max_file}")
                models.append({
                    'status': status or 'Producción',
                    'name': name,
                    'type': groq_tipo(model_id, name),
                    'provider': prov,
                    'priceEntry': entry,
                    'priceExit': exit_,
                    'speed': clean_md_value(col(row, 'speed')),
                    'context': clean_md_value(col(row, 'context')),
                    'limits': clean_md_value(col(row, 'rate limit')),
                    'notes': ' — '.join(notes),
                    '_isDeprecated': status == 'Deprecated',
                    '_supersededBy': '',
                    '_promo': '',
                    '_enterprise': 'contact' in price_raw.lower(),
                })
                i += 1
            continue
        i += 1
    return models


def load_groq_models() -> list[dict]:
    md = fetch_html(MD_URL, timeout=30)
    models = parse_md_tables(md)
    if not models:
        raise RuntimeError(f"No se encontraron tablas parseables en {MD_URL}")
    # Deduplicar por (estado, id/nombre)
    seen: set[str] = set()
    unique: list[dict] = []
    for m in models:
        key = (m['status'] + '|' + m['notes'] + '|' + m['name']).lower()
        if key not in seen:
            seen.add(key)
            unique.append(m)
    print(f"Obtenidos {len(unique)} modelos desde la web: {MD_URL}")
    return unique


def clean_model(m: dict) -> dict:
    row: dict[str, str] = {}
    for key, label in COLS:
        v = m.get(key, '')
        if key == 'type' and not v:
            v = 'Texto'
        if v is None:
            v = ''
        row[label] = v
    row['_isDeprecated'] = m.get('_isDeprecated', False)
    row['_supersededBy'] = m.get('_supersededBy', '')
    row['_promo'] = m.get('_promo', '')
    row['_enterprise'] = m.get('_enterprise', False)
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
<title>Modelos y Precios de Groq</title>
<style>
  :root { color-scheme: dark light; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 24px; background-color: #0d1117; color: #c9d1d9; }
  h1 { font-size: 1.5rem; margin: 0 0 6px; color: #f0f6fc; display: flex; align-items: center; gap: 10px; }
  .badge-groq { background: #f55036; color: #ffffff; font-size: 11px; padding: 3px 8px; border-radius: 12px; text-transform: uppercase; font-weight: bold; }
  .toolbar { display: flex; gap: 12px; align-items: center; margin: 16px 0; flex-wrap: wrap; }
  input[type=search] { padding: 8px 12px; border: 1px solid #30363d; border-radius: 6px;
                        background: #161b22; color: #f0f6fc; width: 280px; font-size: 13px; outline: none; }
  input[type=search]:focus { border-color: #58a6ff; box-shadow: 0 0 0 3px rgba(56,139,253,0.3); }
  select#typeFilter { padding: 8px 12px; border: 1px solid #30363d; border-radius: 6px;
                        background: #161b22; color: #f0f6fc; font-size: 13px; outline: none; }
  select#typeFilter:focus { border-color: #58a6ff; }
  .tag-type { padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600;
              background: #6e768122; color: #c9d1d9; border: 1px solid #6e768166; white-space: nowrap; }
  .tag-status { padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; white-space: nowrap; }
  .st-prod { background: #23863622; color: #3fb950; border: 1px solid #23863666; }
  .st-prev { background: #9e6a0322; color: #e3b341; border: 1px solid #9e6a0366; }
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
  tbody tr.row-enterprise td:nth-child(2) { color: #e3b341; }
  .tag-deprecated { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #da363322; color: #f85149; border: 1px solid #da363366; text-decoration: none; vertical-align: middle; }
  .tag-promo { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #bb800922; color: #e3b341; border: 1px solid #bb800966; vertical-align: middle; cursor: help; }
  .tag-enterprise { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #9e6a0322; color: #e3b341; border: 1px solid #9e6a0366; vertical-align: middle; }
  td.numeric, th.numeric { text-align: right; }
  td.wrap { white-space: normal; min-width: 200px; max-width: 420px; color: #8b949e; font-size: 12px; }
  td.model { white-space: normal; min-width: 180px; max-width: 300px; }
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
  <svg height="28" width="28" viewBox="0 0 24 24" fill="currentColor" style="vertical-align: middle; color: #f55036;">
    <path d="M13 2 4.5 13.5H11L9.5 22 19 10h-6.5L13 2z"/>
  </svg>
  Modelos y Precios de Groq
  <span class="badge-groq">Groq</span>
</h1>
<p class="muted">Datos referenciados desde la documentación oficial de <a href="%%PAGE_URL%%" target="_blank">%%PAGE_URL%%</a>.
<br>Actualizado: %%UPDATED%%. Modelos servidos en LPUs: precio por millón de tokens, velocidad, contexto y límites del plan Developer.
<br>Haz clic en cualquier columna para ordenar. Filtra libremente por nombre, proveedor o ID.</p>
<div class="toolbar">
  <input type="search" id="filter" placeholder="Filtrar modelos de Groq...">
  <select id="typeFilter" title="Filtrar por tipo de modelo"><option value="">Todos los tipos</option></select>
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
let typeFilter = '';

const numericCols = ['Entrada ($/M)', 'Salida ($/M)', 'Velocidad (tok/s)', 'Contexto (tokens)'];

function fmtPrice(raw) {
    // Precios con un solo decimal se muestran con 2: $0.1 -> $0.10
    const m = String(raw).match(/^([$]?)(\\d+)\\.(\\d)$/);
    return m ? m[1] + m[2] + '.' + m[3] + '0' : String(raw);
}

function numOf(raw) {
    return parseFloat(String(raw).replace(/[^0-9.\\-]/g, '')) || 0;
}

function cellValue(r, k) {
    const raw = r[k] || '';
    if (k === 'Modelo') {
        let nameHtml = raw;
        if (r._isDeprecated) {
            nameHtml += ' <span class="tag-deprecated" title="Modelo descatalogado">Descatalogado</span>';
        }
        if (r._promo) {
            const tip = String(r._promo).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
            nameHtml += ' <span class="tag-promo" title="' + tip + '">Promo</span>';
        }
        if (r._enterprise) {
            nameHtml += ' <span class="tag-enterprise" title="Precio bajo contacto comercial">Enterprise</span>';
        }
        return nameHtml;
    }
    if (k === 'Estado') {
        const cls = raw === 'Producción' ? 'st-prod' : 'st-prev';
        return '<span class="tag-status ' + cls + '">' + (raw || '-') + '</span>';
    }
    if (k === 'Notas') {
        if (!raw) return '<span class="muted">-</span>';
        return '<span class="wrap">' + raw + '</span>';
    }
    if (numericCols.includes(k)) {
        if (!raw) return '<span class="muted">-</span>';
        return '<span class="numeric" data-num="' + numOf(raw) + '">' + fmtPrice(raw) + '</span>';
    }
    if (k === 'Tipo' && raw) {
        return '<span class="tag-type">' + raw + '</span>';
    }
    return raw || '<span class="muted">-</span>';
}

function compareRows(a, b) {
    const ka = sortKey;
    if (numericCols.includes(ka)) {
        const va = numOf(a[ka] || '');
        const vb = numOf(b[ka] || '');
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
    if (typeFilter) rows = rows.filter(r => (r['Tipo'] || '') === typeFilter);
    rows.sort(compareRows);
    document.querySelector('#count').textContent = rows.length + ' modelos encontrados';
    tbody.innerHTML = rows.map(r => '<tr class="' + (r._isDeprecated ? 'row-deprecated' : '') + (r._enterprise ? ' row-enterprise' : '') + '">' + HEADERS.map(k => '<td class="' + (k === 'Modelo' ? 'model' : (k === 'Notas' ? 'wrap' : '')) + '">' + cellValue(r, k) + '</td>').join('') + '</tr>').join('');
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

(function initTypeFilter() {
  const sel = document.querySelector('#typeFilter');
  if (!sel) return;
  const types = [...new Set(DATA.map(r => r['Tipo'] || '').filter(Boolean))].sort((a, b) => a.localeCompare(b, 'es'));
  for (const t of types) {
    const opt = document.createElement('option');
    opt.value = t; opt.textContent = t;
    sel.appendChild(opt);
  }
  if (types.length <= 1) sel.style.display = 'none';
  if (types.includes('Texto')) { sel.value = 'Texto'; typeFilter = 'Texto'; }
  sel.addEventListener('change', e => { typeFilter = e.target.value; render(); });
})();

render();
fixSticky();
window.addEventListener('resize', fixSticky);
</script>
</body>
</html>
"""
    html = html.replace("%%HEADER_CELLS%%", header_cells)
    html = html.replace("%%PAGE_URL%%", PAGE_URL)
    html = stamp_updated(html)
    html = html.replace("%%DATA%%", data)
    html = html.replace("%%HEADERS%%", json.dumps(headers, ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print("HTML ->", path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Modelos y precios de Groq -> HTML")
    parser.add_argument("--out", default=".", help="directorio de salida (por defecto: .)")
    args = parser.parse_args()

    try:
        models = load_groq_models()
    except Exception as e:
        print("ERROR:", e)
        return 1

    rows = [clean_model(m) for m in models]
    out_dir = args.out.rstrip("/")
    os.makedirs(out_dir, exist_ok=True)
    write_html(rows, f"{out_dir}/groq_models.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
