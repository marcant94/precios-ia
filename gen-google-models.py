#!/usr/bin/env python3
"""
Extrae y organiza los modelos y precios de Google Gemini API.

Genera:
  - google_models.html (tabla interactiva filtrable y ordenable)

Datos extraídos dinámicamente desde:
  https://ai.google.dev/gemini-api/docs/pricing

Uso:
    python3 gen-google-models.py
    python3 gen-google-models.py --out datos
"""

import argparse
import json
import os
import re
import subprocess
import sys

from common import (
    build_header_cells,
    clean_text,
    parse_cost,
    parse_family_version,
    resolve_transitive_superseded,
    stamp_updated,
)


def fetch_html(url: str, timeout: int = 30) -> str:
    """Descarga HTML usando curl (urllib falla con redirects de Google)."""
    result = subprocess.run(
        ['curl', '-sL', '-H', f'User-Agent: Mozilla/5.0', url],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed: {result.stderr[:200]}")
    return result.stdout

PAGE_URL = "https://ai.google.dev/gemini-api/docs/pricing?hl=es-419"

# Palabras clave para excluir modelos no relevantes (video, música, embeddings, etc.)
EXCLUDE_KEYWORDS = re.compile(
    r'veo|lyria|embedding|robotics|computer use|gemma|imagen(?!\s)',
    re.IGNORECASE,
)

# Palabras que indican precio promocional TEMPORAL
PROMO_RE = re.compile(
    r'through (january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}',
    re.IGNORECASE,
)

# Columnas finales
COLS = [
    ("name", "Modelo"),
    ("description", "Descripción"),
    ("priceInput", "Entrada ($/M)"),
    ("priceOutput", "Salida ($/M)"),
    ("priceCacheRead", "Lectura Caché ($/M)"),
    ("priceCacheStorage", "Almac. Caché ($/M·h)"),
    ("freeTier", "Gratuito"),
    ("notes", "Notas"),
]

HEADER_GROUPS = [
    (None, [("name", "Modelo")]),
    (None, [("description", "Descripción")]),
    ("Precio ($/M)", [
        ("priceInput", "Entrada"),
        ("priceOutput", "Salida"),
    ]),
    ("Caché ($/M)", [
        ("priceCacheRead", "Lectura"),
        ("priceCacheStorage", "Almac. /h"),
    ]),
    (None, [("freeTier", "Gratuito")]),
    (None, [("notes", "Notas")]),
]


def parse_price_cell(text: str) -> str:
    """Extrae el primer precio '$X.XX' o 'USD X.XX' de una celda de texto."""
    m = re.search(r'[$]([0-9]+(?:\.[0-9]+)?)', text)
    if m:
        return f"${m.group(1)}"
    m = re.search(r'usd\s*([0-9]+(?:\.[0-9]+)?)', text, re.IGNORECASE)
    if m:
        return f"${m.group(1)}"
    return ""


def extract_section_prices(section_text: str) -> dict:
    """Extrae precios de la sección de un modelo (Standard tier preferido)."""
    result = {
        'priceInput': '',
        'priceOutput': '',
        'priceCacheRead': '',
        'priceCacheStorage': '',
        'freeTier': '',
        'notes': '',
    }

    lines = section_text.split('\n')
    current_field = None

    for line in lines:
        stripped = line.strip()

        # Detectar líneas de campo de precio (EN + ES)
        low = stripped.lower()

        if ('input price' in low or 'precio de entrada' in low) and 'context caching' not in low and 'almacenamiento' not in low:
            current_field = 'priceInput'
        elif ('output price' in low or 'precio de salida' in low) and 'context caching' not in low and 'almacenamiento' not in low:
            current_field = 'priceOutput'
        elif 'context caching price' in low or 'cache read' in low or 'precio del almacenamiento de contexto en caché' in low or 'precio de lectura de caché' in low:
            current_field = 'priceCacheRead'
        elif 'storage' in low and ('cache' in low or 'caché' in low) or 'almacenamiento' in low and 'caché' in low:
            current_field = 'priceCacheStorage'

        if current_field and ('$' in stripped or 'usd' in low):
            price = parse_price_cell(stripped)
            if price and not result[current_field]:
                result[current_field] = price
            current_field = None

        # Detectar si es gratuito (EN + ES)
        if ('free of charge' in low or 'sin costo' in low) and not result['freeTier']:
            result['freeTier'] = 'Sí'

    # Si no encontramos "$" en las líneas de precio, intentar con tablas
    if not result['priceInput'] and not result['priceOutput']:
        # Buscar patrones como "| Input price | Free of charge | $0.75 |"
        for line in lines:
            low = line.lower()
            if '|' in line and ('input price' in low or 'precio de entrada' in low):
                m = re.search(r'[$]([0-9]+(?:\.[0-9]+)?)', line)
                if not m:
                    m = re.search(r'usd\s*([0-9]+(?:\.[0-9]+)?)', line)
                if m:
                    result['priceInput'] = f"${m.group(1)}"
            if '|' in line and ('output price' in low or 'precio de salida' in low):
                m = re.search(r'[$]([0-9]+(?:\.[0-9]+)?)', line)
                if not m:
                    m = re.search(r'usd\s*([0-9]+(?:\.[0-9]+)?)', line)
                if m:
                    result['priceOutput'] = f"${m.group(1)}"
            if '|' in line and ('context caching price' in low or 'cache read' in low or 'precio del almacenamiento de contexto en caché' in low or 'precio de lectura de caché' in low):
                m = re.search(r'[$]([0-9]+(?:\.[0-9]+)?)', line)
                if not m:
                    m = re.search(r'usd\s*([0-9]+(?:\.[0-9]+)?)', line)
                if m:
                    result['priceCacheRead'] = f"${m.group(1)}"
            if '|' in line and ('storage' in low and 'hour' in low or 'almacenamiento' in low and 'hora' in low):
                m = re.search(r'[$]([0-9]+(?:\.[0-9]+)?)', line)
                if not m:
                    m = re.search(r'usd\s*([0-9]+(?:\.[0-9]+)?)', line)
                if m:
                    result['priceCacheStorage'] = f"${m.group(1)}"

    return result


def parse_google_models(html: str) -> list[dict]:
    """Parsea la página HTML de precios de Google Gemini."""
    models = []

    # Encontrar secciones de modelos: cada H2 seguido de contenido
    # La página tiene H2 headers como "## Gemini 3.8 Flash"
    sections = re.split(r'<h2[^>]*>', html)

    for section in sections[1:]:  # Saltar la primera parte (antes del primer H2)
        # Obtener nombre del modelo del H2
        h2_end = section.find('</h2>')
        if h2_end == -1:
            continue
        h2_text = clean_text(section[:h2_end])

        # Limpiar el nombre (quitar links markdown, etc.)
        name = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', h2_text).strip()
        name = re.sub(r'\s+', ' ', name)

        # Filtrar secciones que no son modelos de chat/generación de texto
        if not name or EXCLUDE_KEYWORDS.search(name):
            continue
        if any(kw in name.lower() for kw in ['pricing for tools', 'pricing for agents', 'notes', 'additional links', 'sitemap', 'precios de las herramientas', 'precios para agentes', 'notas', 'enlaces adicionales']):
            continue

        # Obtener descripción del modelo
        # Estructura: ...models-section</div>\n\n<p>descripción</p>
        desc_match = re.search(r'</div>\s*\n\s*\n\s*<p>([\s\S]*?)</p>', section[h2_end:])
        if desc_match:
            description = clean_text(desc_match.group(1))
        else:
            description = ''
        # Limpiar links residuales
        description = re.sub(r'\[.*?\]\(.*?\)', '', description).strip()

        # Extraer precios de la sección (después del H2)
        section_content = section[h2_end:]
        # Limitar al siguiente H2 o fin
        next_h2 = section_content.find('<h2')
        if next_h2 > 0:
            section_content = section_content[:next_h2]

        prices = extract_section_prices(section_content)

        # Determinar promo
        promo = ''
        if PROMO_RE.search(section_content):
            promo_match = PROMO_RE.search(section_content)
            promo = f"Precio promocional temporal: {promo_match.group(0)}"

        models.append({
            'name': name,
            'description': description[:120] + ('...' if len(description) > 120 else ''),
            'priceInput': prices['priceInput'],
            'priceOutput': prices['priceOutput'],
            'priceCacheRead': prices['priceCacheRead'],
            'priceCacheStorage': prices['priceCacheStorage'],
            'freeTier': prices['freeTier'] or 'No',
            'notes': promo,
        })

    if not models:
        raise RuntimeError(f"No se encontraron tablas parseables en {PAGE_URL}")

    # Detección de modelos superados
    for entry in models:
        entry['_isDeprecated'] = False
        entry['_supersededBy'] = ''

    direct_superseded: dict[int, int] = {}
    for i, m1 in enumerate(models):
        f1, v1 = parse_family_version(m1.get('name', ''))
        e1 = parse_cost(m1.get('priceInput'))
        x1 = parse_cost(m1.get('priceOutput'))

        best_target = None
        best_ver = v1
        for j, m2 in enumerate(models):
            if i == j:
                continue
            f2, v2 = parse_family_version(m2.get('name', ''))
            if f1 == f2 and v2 > best_ver:
                e2 = parse_cost(m2.get('priceInput'))
                x2 = parse_cost(m2.get('priceOutput'))
                if e2 <= e1 and x2 <= x1:
                    best_target = j
                    best_ver = v2

        if best_target is not None:
            direct_superseded[i] = best_target

    resolve_transitive_superseded(models, direct_superseded)

    print(f"Obtenidos {len(models)} modelos desde la web: {PAGE_URL}")
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
<title>Modelos y Precios de Google Gemini</title>
<style>
  :root { color-scheme: dark light; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 24px; background-color: #0d1117; color: #c9d1d9; }
  h1 { font-size: 1.5rem; margin: 0 0 6px; color: #f0f6fc; display: flex; align-items: center; gap: 10px; }
  .badge-google { background: #4285f4; color: #ffffff; font-size: 11px; padding: 3px 8px; border-radius: 12px; text-transform: uppercase; font-weight: bold; }
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
  .tag-free { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background: #23863622; color: #3fb950; border: 1px solid #23863666; }
  .tag-paid { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background: #30363d22; color: #8b949e; border: 1px solid #30363d66; }
  td.numeric, th.numeric { text-align: right; }
  td.desc { white-space: normal; min-width: 180px; max-width: 320px; color: #8b949e; font-size: 12px; }
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
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
  </svg>
  Modelos y Precios de Google Gemini
  <span class="badge-google">Google</span>
</h1>
<p class="muted">Datos referenciados desde la documentación oficial de <a href="%%PAGE_URL%%" target="_blank">%%PAGE_URL%%</a>.
<br>Actualizado: %%UPDATED%%.<br>Haz clic en cualquier columna para ordenar. Filtra libremente por nombre o descripción.</p>
<div class="toolbar">
  <input type="search" id="filter" placeholder="Filtrar modelos de Gemini...">
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

const numericCols = ['Entrada ($/M)', 'Salida ($/M)', 'Lectura Caché ($/M)', 'Almac. Caché ($/M\\u00b7h)'];

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
    if (k === 'Descripci\\u00f3n') {
        if (!raw) return '<span class="muted">-</span>';
        return '<span class="desc">' + raw + '</span>';
    }
    if (k === 'Gratuito') {
        return raw === 'S\\u00ed' ? '<span class="tag-free">S\\u00ed</span>' : '<span class="tag-paid">No</span>';
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
    tbody.innerHTML = rows.map(r => '<tr class="' + (r._isDeprecated ? 'row-deprecated' : '') + '">' + HEADERS.map(k => '<td class="' + (k === 'Descripci\\u00f3n' ? 'desc' : k === 'Notas' ? 'notes' : '') + '">' + cellValue(r, k) + '</td>').join('') + '</tr>').join('');
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
    parser = argparse.ArgumentParser(description="Modelos y precios de Google Gemini -> HTML")
    parser.add_argument("--out", default=".", help="directorio de salida (por defecto: .)")
    args = parser.parse_args()

    try:
        models = parse_google_models(fetch_html(PAGE_URL))
    except Exception as e:
        print('ERROR:', e)
        return 1

    rows = [clean_model(m) for m in models]
    out_dir = args.out.rstrip('/')
    os.makedirs(out_dir, exist_ok=True)
    write_html(rows, f"{out_dir}/google_models.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
