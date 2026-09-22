#!/usr/bin/env python3
"""
Extrae y organiza los modelos, precios y límites de uso de OpenCode Go.

Genera:
  - opencode_go_models.html (tabla interactiva filtrable y ordenable)

Datos extraídos dinámicamente desde:
  https://opencode.ai/docs/es/go/#límites-de-uso

Uso:
    python3 gen-opencode-go-models.py
    python3 gen-opencode-go-models.py --out datos
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
    normalize_name,
    parse_cost,
    parse_family_version,
    resolve_transitive_superseded,
    stamp_updated,
)

PAGE_URL = "https://opencode.ai/docs/es/go/"
# El banner de promociones no está en la doc, sino en la landing de Go.
# Se prueban varias URLs por si el banner se mueve de ubicación.
BANNER_URLS = [
    "https://opencode.ai/go",
    "https://opencode.ai/zen",
]

# Columnas finales (sin proveedor: heurístico no dinámico, se elimina)
COLS = [
    ("name",            "Modelo"),
    ("condition",       "Condición / Umbral"),
    ("priceEntry",      "Entrada ($/M)"),
    ("priceCacheRead",  "Entrada Caché ($/M)"),
    ("priceCacheWrite", "Escritura Caché ($/M)"),
    ("priceExit",       "Salida ($/M)"),
    ("monthlyBudget",   "Límite Mensual Incluido"),
    ("req5h",           "Peticiones / 5h"),
    ("reqWeekly",       "Peticiones / Semana"),
    ("reqMonthly",      "Peticiones / Mes"),
    ("modelId",         "ID Modelo OpenCode"),
    ("retention",       "Retención"),
]

HEADER_GROUPS = [    (None, [("name", "Modelo")]),
    (None, [("condition", "Condición")]),
    ("Precio ($/M)", [
        ("priceEntry", "Entrada"),
        ("priceCacheRead", "Entrada Caché"),
        ("priceCacheWrite", "Escritura Caché"),
        ("priceExit", "Salida"),
    ]),
    (None, [("monthlyBudget", "Límite Incluido")]),
    ("Estimación Peticiones", [
        ("req5h", "5 horas"),
        ("reqWeekly", "Semanal"),
        ("reqMonthly", "Mensual"),
    ]),
    (None, [("modelId", "ID Modelo")]),
    (None, [("retention", "Retención")]),
]


def fetch_promo_banners() -> list[str]:
    """
    Extrae el texto de TODOS los banners de anuncios
    (data-component="desktop-app-banner") de las landings candidatas, p.ej.:
    'New GLM-5.3-Flash gets 2× usage limits for a limited time'.
    Devuelve [] si no hay banners en ninguna (no es crítico).
    """
    banners: list[str] = []
    for url in BANNER_URLS:
        try:
            html = fetch_html(url)
        except Exception:
            continue
        for m in re.finditer(r'data-component="desktop-app-banner"', html):
            chunk = html[m.start():m.start() + 2000]
            tm = re.search(r'data-slot="text"[^>]*>([\s\S]*?)</span>', chunk)
            if tm:
                text = clean_text(tm.group(1))
                if text and text not in banners:
                    banners.append(text)
    return banners


def load_opencode_go_models() -> list[dict]:
    """Descarga y parsea las tablas de modelos y precios de OpenCode Go."""
    html = fetch_html(PAGE_URL)

    tables = re.findall(r'<table[\s\S]*?<\/table>', html, re.IGNORECASE)
    if not tables:
        raise RuntimeError(f"No se encontraron tablas en {PAGE_URL}")

    requests_map: dict[str, dict] = {}
    price_rows: list[dict] = []
    endpoints_map: dict[str, str] = {}
    privacy_map: dict[str, str] = {}

    for table in tables:
        ths = re.findall(r'<th[\s\S]*?>([\s\S]*?)<\/th>', table, re.IGNORECASE)
        headers = [clean_text(x).lower() for x in ths]
        rows = re.findall(r'<tr[\s\S]*?>([\s\S]*?)<\/tr>', table, re.IGNORECASE)
        if not rows:
            continue

        if any('5 horas' in h or '5 hours' in h for h in headers):
            for r in rows[1:]:
                tds = [clean_text(x) for x in re.findall(r'<td[\s\S]*?>([\s\S]*?)<\/td>', r, re.IGNORECASE)]
                if len(tds) >= 4:
                    requests_map[normalize_name(tds[0])] = {
                        'req5h': tds[1],
                        'reqWeekly': tds[2],
                        'reqMonthly': tds[3],
                    }
        elif any('lectura en cach' in h or 'cached' in h for h in headers) or any('uso' in h or 'usage' in h for h in headers):
            for r in rows[1:]:
                tds = [clean_text(x) for x in re.findall(r'<td[\s\S]*?>([\s\S]*?)<\/td>', r, re.IGNORECASE)]
                if len(tds) >= 5:
                    full_name = tds[0]
                    cond = ''
                    m_cond = re.search(r'\((.*?)\)', full_name)
                    if m_cond:
                        cond = m_cond.group(1).strip()
                    base_name = re.sub(r'\s*\(.*?\)', '', full_name).strip()
                    price_rows.append({
                        'raw_full_name': full_name,
                        'name': base_name,
                        'condition': cond,
                        'priceEntry': tds[1] if len(tds) > 1 else '',
                        'priceExit': tds[2] if len(tds) > 2 else '',
                        'priceCacheRead': tds[3] if len(tds) > 3 else '',
                        'priceCacheWrite': tds[4] if len(tds) > 4 else '',
                        'monthlyBudget': tds[5] if len(tds) > 5 else '',
                    })
        elif any('id del modelo' in h or 'model id' in h for h in headers):
            for r in rows[1:]:
                tds = [clean_text(x) for x in re.findall(r'<td[\s\S]*?>([\s\S]*?)<\/td>', r, re.IGNORECASE)]
                if len(tds) >= 2:
                    endpoints_map[normalize_name(tds[0])] = tds[1]
        elif any('retenci' in h or 'retention' in h for h in headers):
            for r in rows[1:]:
                tds = [clean_text(x) for x in re.findall(r'<td[\s\S]*?>([\s\S]*?)<\/td>', r, re.IGNORECASE)]
                if len(tds) >= 3:
                    privacy_map[normalize_name(tds[0])] = tds[2]

    results: list[dict] = []
    for pr in price_rows:
        norm = normalize_name(pr['name'])
        req_info = requests_map.get(norm, {})
        results.append({
            'name': pr['name'],
            'condition': pr['condition'],
            'priceEntry': pr['priceEntry'],
            'priceCacheRead': pr['priceCacheRead'],
            'priceCacheWrite': pr['priceCacheWrite'],
            'priceExit': pr['priceExit'],
            'monthlyBudget': pr['monthlyBudget'],
            'req5h': req_info.get('req5h', ''),
            'reqWeekly': req_info.get('reqWeekly', ''),
            'reqMonthly': req_info.get('reqMonthly', ''),
            'modelId': endpoints_map.get(norm, ''),
            'retention': privacy_map.get(norm, ''),
            '_isDeprecated': False,
            '_supersededBy': '',
        })

    # Detección de modelos superados (misma familia + condición, versión superior, coste <=)
    direct_superseded: dict[int, int] = {}
    for i, r1 in enumerate(results):
        f1, v1 = parse_family_version(r1['name'])
        c1 = (r1['condition'] or '').strip()
        e1 = parse_cost(r1['priceEntry'])
        x1 = parse_cost(r1['priceExit'])
        cr1 = parse_cost(r1['priceCacheRead'])
        cw1 = parse_cost(r1['priceCacheWrite'])
        b1 = parse_cost(r1['monthlyBudget'])

        best_target = None
        best_ver = v1
        for j, r2 in enumerate(results):
            if i == j:
                continue
            f2, v2 = parse_family_version(r2['name'])
            c2 = (r2['condition'] or '').strip()
            if f1 == f2 and c1 == c2 and v2 > best_ver:
                e2 = parse_cost(r2['priceEntry'])
                x2 = parse_cost(r2['priceExit'])
                cr2 = parse_cost(r2['priceCacheRead'])
                cw2 = parse_cost(r2['priceCacheWrite'])
                b2 = parse_cost(r2['monthlyBudget'])
                if e2 <= e1 and x2 <= x1 and cr2 <= cr1 and cw2 <= cw1 and b2 >= b1:
                    best_target = j
                    best_ver = v2

        if best_target is not None:
            direct_superseded[i] = best_target

    resolve_transitive_superseded(results, direct_superseded)

    # Promos: los banners de las landings anuncian ofertas tipo 'X gets 2x
    # usage limits for a limited time'. Marcar los modelos que aparecen en ellos.
    for banner in fetch_promo_banners():
        # Comparar el nombre del modelo (normalizado) contra todos los
        # n-gramas de palabras consecutivas del banner. Así 'GLM-5.3' no
        # matchea dentro de 'GLM-5.3-Flash' (glm53 != glm53flash), pero
        # nombres de varias palabras como 'Grok 4.6' (grok46) sí matchean.
        words = banner.split()
        banner_names = {
            normalize_name(' '.join(words[i:i + size]))
            for size in range(1, min(7, len(words) + 1))
            for i in range(len(words) - size + 1)
        }
        for r in results:
            if not r.get('_promo') and normalize_name(r['name']) in banner_names:
                r['_promo'] = banner
                print(f"Promo detectada para {r['name']}: {banner}")

    if not results:
        raise RuntimeError("No se pudieron parsear filas de precios de OpenCode Go")

    print(f"Obtenidos {len(results)} registros de modelos desde {PAGE_URL}")
    return results


def clean_row(m: dict) -> dict:
    row: dict[str, str] = {}
    for key, label in COLS:
        v = m.get(key, '')
        if v == '-' or v == 'None' or v is None:
            v = '-'
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
<title>Modelos y Precios de OpenCode Go ($10/mes)</title>
<style>
  :root { color-scheme: dark light; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 24px; background-color: #0d1117; color: #c9d1d9; }
  h1 { font-size: 1.5rem; margin: 0 0 6px; color: #f0f6fc; display: flex; align-items: center; gap: 10px; }
  .badge-go { background: #1f6feb; color: #ffffff; font-size: 11px; padding: 3px 8px; border-radius: 12px; text-transform: uppercase; font-weight: bold; }
  .badge-price { background: #238636; color: #ffffff; font-size: 11px; padding: 3px 8px; border-radius: 12px; font-weight: bold; }
  .toolbar { display: flex; gap: 12px; align-items: center; margin: 16px 0; flex-wrap: wrap; }
  input[type=search] { padding: 8px 12px; border: 1px solid #30363d; border-radius: 6px;
                        background: #161b22; color: #f0f6fc; width: 280px; font-size: 13px; outline: none; }
  input[type=search]:focus { border-color: #58a6ff; box-shadow: 0 0 0 3px rgba(56,139,253,0.3); }
  .table-wrap { overflow: auto; border: 1px solid #30363d; border-radius: 8px; background: #161b22; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; text-align: left; }
  thead th { position: sticky; background: #21262d; color: #f0f6fc; cursor: pointer;
               user-select: none; padding: 8px 10px; white-space: nowrap; z-index: 1; border-bottom: 1px solid #30363d; }
  thead th:hover { background: #30363d; }
  thead tr:first-child th { top: 0; }
  thead th .arrow { opacity: .5; font-size: 11px; margin-left: 4px; }
  tbody td { padding: 8px 10px; border-top: 1px solid #21262d; white-space: nowrap; color: #c9d1d9; }
  tbody tr:hover { background: #1f242c; }
  tbody tr.row-deprecated { opacity: 0.55; }
  tbody tr.row-deprecated:hover { opacity: 0.85; background: #261f22; }
  tbody tr.row-deprecated td:first-child { text-decoration: line-through; text-decoration-color: #f85149; text-decoration-thickness: 2px; }
  .tag-deprecated { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #da363322; color: #f85149; border: 1px solid #da363366; text-decoration: none; vertical-align: middle; }
  .tag-promo { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #bb800922; color: #e3b341; border: 1px solid #bb800966; vertical-align: middle; cursor: help; }
  td.numeric, th.numeric { text-align: right; }
  .tag-budget { padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background: #23863622; color: #3fb950; border: 1px solid #23863666; }
  .tag-code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; font-size: 11px; background: #161b22; padding: 2px 5px; border-radius: 4px; border: 1px solid #30363d; color: #79c0ff; }
  .muted { color: #8b949e; }
  a { color: #58a6ff; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .back-link { display: inline-flex; align-items: center; gap: 6px; margin-bottom: 14px; font-size: 13px;
               color: #8b949e; padding: 5px 12px; border: 1px solid #30363d; border-radius: 6px; background: #161b22; }
  .back-link:hover { border-color: #58a6ff; color: #58a6ff; text-decoration: none; }
  .switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;
            font-size: 13px; color: #c9d1d9; }
  .switch input { display: none; }
  .switch .track { width: 40px; height: 22px; background: #30363d; border-radius: 11px; position: relative;
                   transition: background .2s; flex-shrink: 0; }
  .switch .track::after { content: ''; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px;
                          background: #f0f6fc; border-radius: 50%; transition: transform .2s; }
  .switch input:checked + .track { background: #238636; }
  .switch input:checked + .track::after { transform: translateX(18px); }
</style>
</head>
<body>
<a class="back-link" href="./index.html">&#8592; Volver al índice</a>
<h1>
  <svg height="28" width="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;">
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
  </svg>
  Modelos y Precios de OpenCode Go
  <span class="badge-go">OpenCode Go</span>
  <span class="badge-price">$10 / mes (hasta $60 en uso)</span>
</h1>
<p class="muted">Datos referenciados desde la documentación oficial de <a href="%%PAGE_URL%%" target="_blank">%%PAGE_URL%%</a>.
<br>Actualizado: %%UPDATED%%.<br>Incluye límites de uso (5h: $12, semanal: $30, mensual: $60) y estimación de peticiones.</p>
<div class="toolbar">
  <input type="search" id="filter" placeholder="Filtrar modelos de OpenCode Go...">
  <label class="switch" title="Mostrar solo modelos con límite mensual ≥ $60">
    <input type="checkbox" id="budgetSwitch">
    <span class="track"></span>
    <span style="font-size:13px">≥ $60/mes</span>
  </label>
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
let budgetFilter = false;

const numericCols = [
    'Entrada ($/M)', 'Entrada Caché ($/M)', 'Escritura Caché ($/M)', 'Salida ($/M)',
    'Límite Mensual Incluido', 'Peticiones / 5h', 'Peticiones / Semana', 'Peticiones / Mes'
];

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
    if (k === 'Límite Mensual Incluido' && raw) {
        return '<span class="tag-budget">' + raw + '</span>';
    }
    if (k === 'ID Modelo OpenCode' && raw) {
        return '<span class="tag-code">opencode-go/' + raw + '</span>';
    }
    if (numericCols.includes(k)) {
        if (!raw || raw === '-') return '<span class="muted">-</span>';
        const num = parseFloat(String(raw).replace(/,/g, '').replace(/[^0-9.\\-]/g, '')) || 0;
        return '<span class="numeric" data-num="' + num + '">' + raw + '</span>';
    }
    return raw || '';
}

function compareRows(a, b) {
    const ka = sortKey;
    if (numericCols.includes(ka)) {
        const va = parseFloat(String(a[ka] || '').replace(/,/g, '').replace(/[^0-9.\\-]/g, '')) || 0;
        const vb = parseFloat(String(b[ka] || '').replace(/,/g, '').replace(/[^0-9.\\-]/g, '')) || 0;
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
    if (budgetFilter) {
        rows = rows.filter(r => {
            const raw = r['Límite Mensual Incluido'] || '';
            const num = parseFloat(String(raw).replace(/,/g, '').replace(/[^0-9.]/g, '')) || 0;
            return num >= 60;
        });
    }
    rows.sort(compareRows);
    document.querySelector('#count').textContent = rows.length + ' modelos / variantes encontrados';
    tbody.innerHTML = rows.map(r => '<tr class="' + (r._isDeprecated ? 'row-deprecated' : '') + '">' + HEADERS.map(k => '<td>' + cellValue(r, k) + '</td>').join('') + '</tr>').join('');
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

document.querySelector('#budgetSwitch').addEventListener('change', e => { budgetFilter = e.target.checked; render(); });

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
    parser = argparse.ArgumentParser(description="Modelos y precios de OpenCode Go -> HTML")
    parser.add_argument("--out", default=".", help="directorio de salida (por defecto: .)")
    args = parser.parse_args()

    try:
        models = load_opencode_go_models()
    except Exception as e:
        print('ERROR:', e)
        return 1

    rows = [clean_row(m) for m in models]
    out_dir = args.out.rstrip('/')
    os.makedirs(out_dir, exist_ok=True)
    write_html(rows, f"{out_dir}/opencode_go_models.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
