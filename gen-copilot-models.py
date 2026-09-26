#!/usr/bin/env python3
"""
Extrae y organiza los modelos y precios de GitHub Copilot.

Genera:
  - copilot_models.html  (tabla interactiva filtrable y ordenable)

Datos extraídos dinámicamente desde:
  https://docs.github.com/es/copilot/reference/copilot-billing/models-and-pricing

Uso:
    python3 gen-copilot-models.py              # genera en .
    python3 gen-copilot-models.py --out datos  # salida en datos/
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

PAGE_URL = "https://docs.github.com/es/copilot/reference/copilot-billing/models-and-pricing"

# Referencias a notas al pie en las celdas: <a href="...#user-content-fn-xxx">[1]</a> (a veces dentro de <sup>)
FN_LINK_RE = re.compile(r'<a\s[^>]*href="[^"]*user-content-fn[^"]*"[^>]*>[\s\S]*?</a>', re.IGNORECASE)
FN_ID_RE = re.compile(r'user-content-fn(?:ref)?-([\w-]+)', re.IGNORECASE)
SUP_RE = re.compile(r'<sup[^>]*>[\s\S]*?</sup>', re.IGNORECASE)
# Palabras que indican que la nota al pie describe una promoción/descuento
PROMO_RE = re.compile(r'descuento|promo|discount|rebaja|oferta|gratis|free|limited', re.IGNORECASE)


def parse_name_cell(cell_html: str) -> tuple[str, list[str]]:
    """
    Limpia la celda de nombre eliminando las referencias a notas al pie
    (p.ej. enlaces de descuento temporal) para que no queden pegadas al nombre.
    Devuelve (nombre_limpio, ids_de_notas_referenciadas).
    """
    fn_ids = FN_ID_RE.findall(cell_html)
    cleaned = FN_LINK_RE.sub('', cell_html)
    cleaned = SUP_RE.sub('', cleaned)
    return clean_text(cleaned), fn_ids


def extract_footnotes(page_html: str) -> dict[str, str]:
    """Extrae el texto de las notas al pie de la página (id -> texto limpio)."""
    footnotes: dict[str, str] = {}
    for fid, ftxt in re.findall(
        r'<li[^>]*id="user-content-fn-([\w-]+)"[^>]*>([\s\S]*?)</li>',
        page_html, re.IGNORECASE,
    ):
        # Eliminar los enlaces de retorno (↩) dentro de la propia nota
        cleaned = FN_LINK_RE.sub('', ftxt)
        cleaned = SUP_RE.sub('', cleaned)
        footnotes[fid] = clean_text(cleaned)
    return footnotes

# Columnas de salida (sin proveedor: no es dinámico y no aporta valor)
COLS = [
    ("name",             "Nombre"),
    ("level",            "Nivel"),
    ("threshold",        "Umbral"),
    ("intelligenceTier", "Inteligencia"),
    ("priceEntry",       "Entrada ($/M)"),
    ("priceEntryCache",  "Entrada Caché ($/M)"),
    ("writeCache",       "Escritura Caché ($/M)"),
    ("priceExit",        "Salida ($/M)"),
    ("status",           "Estado"),
]

HEADER_GROUPS = [
    (None, [("name", "Nombre")]),
    (None, [("level", "Nivel")]),
    (None, [("threshold", "Umbral")]),
    (None, [("intelligenceTier", "Inteligencia")]),
    ("Precio ($/M)", [
        ("priceEntry", "Entrada"),
        ("priceEntryCache", "Entrada Caché"),
        ("writeCache", "Escritura Caché"),
        ("priceExit", "Salida"),
    ]),
    (None, [("status", "Estado")]),
]


def load_copilot_models() -> list[dict]:
    """Descarga y parsea la página oficial de Copilot."""
    html = fetch_html(PAGE_URL)
    footnotes = extract_footnotes(html)

    tables = re.findall(r'<table[\s\S]*?<\/table>', html, re.IGNORECASE)
    parsed: list[dict] = []

    hdr_map = {
        'nombre': 'name', 'modelo': 'name',
        'inteligencia': 'intelligenceTier', 'razonamiento': 'intelligenceTier',
        'velocidad': 'speedTier', 'speed': 'speedTier',
        'planes': 'plans', 'planes soportados': 'plans',
        'contexto': 'maxContextWindow', 'context': 'maxContextWindow',
        'capaci': 'features', 'capacidades': 'features', 'features': 'features',
        'estado': 'status', 'status': 'status',
        'descrip': 'tagline', 'descripcion': 'tagline', 'tagline': 'tagline',
        'enlace': 'link', 'link': 'link', 'url': 'link',
        'notas': 'notes', 'notes': 'notes',
        'precio': 'price', 'precio entrada': 'priceEntry', 'precio salida': 'priceExit',
        'umbral': 'threshold', 'nivel': 'level',
        'entrada almacenada': 'priceEntryCache', 'entrada': 'priceEntry', 'salida': 'priceExit',
        'escritura en caché': 'writeCache', 'categor': 'category'
    }

    for table in tables:
        ths = re.findall(r'<th[\s\S]*?>([\s\S]*?)<\/th>', table, re.IGNORECASE)
        headers = [clean_text(x).lower() for x in ths]
        if not headers:
            first_row = re.search(r'<tr[\s\S]*?>([\s\S]*?)<\/tr>', table, re.IGNORECASE)
            if first_row:
                tds = re.findall(r'<t[dh][\s\S]*?>([\s\S]*?)<\/t[dh]>', first_row.group(1), re.IGNORECASE)
                headers = [clean_text(x).lower() for x in tds]
        if not headers:
            continue

        rows = re.findall(r'<tr[\s\S]*?>([\s\S]*?)<\/tr>', table, re.IGNORECASE)
        for row_html in rows[1:]:
            tds = re.findall(r'<td[\s\S]*?>([\s\S]*?)<\/td>', row_html, re.IGNORECASE)
            if not tds:
                continue
            obj: dict[str, str] = {}
            promo = ''
            for j, cell in enumerate(tds):
                hdr = headers[j] if j < len(headers) else f'col{j}'
                key = None
                for k_sub, k_map in hdr_map.items():
                    if k_sub in hdr:
                        key = k_map
                        break
                if key is None:
                    key = hdr
                if key == 'name':
                    # La celda nombre puede llevar referencias a notas al pie
                    # (p.ej. descuento temporal): limpiar y capturar el texto.
                    obj[key], fn_ids = parse_name_cell(cell)
                    for fid in fn_ids:
                        ftxt = footnotes.get(fid, '')
                        if ftxt and (PROMO_RE.search(ftxt) or not promo):
                            promo = ftxt
                else:
                    obj[key] = clean_text(cell)

            entry = {
                'name': obj.get('name', obj.get('modelo', '')),
                'category': obj.get('category', ''),
                'level': obj.get('level', ''),
                'threshold': obj.get('threshold', obj.get('umbral', '')),
                'intelligenceTier': obj.get('intelligenceTier', obj.get('inteligencia / razonamiento', obj.get('inteligencia', ''))),
                'status': obj.get('status', obj.get('estado', '')),
                'priceEntry': obj.get('priceEntry', obj.get('precio entrada', obj.get('precio', ''))),
                'priceEntryCache': obj.get('priceEntryCache', obj.get('entrada almacenada', '')),
                'writeCache': obj.get('writeCache', obj.get('escritura en caché', '')),
                'priceExit': obj.get('priceExit', obj.get('precio salida', '')),
                'promo': promo,
            }
            # Ignorar filas sin nombre (ruido de tablas no relacionadas)
            if not entry['name']:
                continue
            parsed.append(entry)

    if not parsed:
        raise RuntimeError(f"No se encontraron tablas parseables en {PAGE_URL}")

    # Detección de modelos superados por versiones superiores con precios <=
    for entry in parsed:
        entry['_isDeprecated'] = False
        entry['_supersededBy'] = ''

    direct_superseded: dict[int, int] = {}
    for i, m1 in enumerate(parsed):
        f1, v1 = parse_family_version(m1.get('name', ''))
        lvl1 = m1.get('level', '')
        e1 = parse_cost(m1.get('priceEntry'))
        x1 = parse_cost(m1.get('priceExit'))
        cr1 = parse_cost(m1.get('priceEntryCache'))
        cw1 = parse_cost(m1.get('writeCache'))

        best_target = None
        best_ver = v1
        for j, m2 in enumerate(parsed):
            if i == j:
                continue
            f2, v2 = parse_family_version(m2.get('name', ''))
            lvl2 = m2.get('level', '')
            if f1 == f2 and lvl1 == lvl2 and v2 > best_ver:
                e2 = parse_cost(m2.get('priceEntry'))
                x2 = parse_cost(m2.get('priceExit'))
                cr2 = parse_cost(m2.get('priceEntryCache'))
                cw2 = parse_cost(m2.get('writeCache'))
                if e2 <= e1 and x2 <= x1 and cr2 <= cr1 and cw2 <= cw1:
                    best_target = j
                    best_ver = v2

        if best_target is not None:
            direct_superseded[i] = best_target

    resolve_transitive_superseded(parsed, direct_superseded)

    print(f"Obtenidos {len(parsed)} modelos desde la web: {PAGE_URL}")
    return parsed


def clean_model(m: dict) -> dict:
    """Devuelve una fila estructurada con cabeceras en español."""
    row: dict[str, str] = {}
    for key, label in COLS:
        v = m.get(key)
        if isinstance(v, list):
            v = " | ".join(str(x) for x in v)
        if isinstance(v, bool):
            v = "Sí" if v else "No"
        if v is None:
            v = ""
        row[label] = v

    cat = (m.get('category') or '').strip().lower()
    cat_map = {
        'powerful': '3+ Powerful',
        'versatile': '2+ Versatile',
        'lightweight': '1+ Lightweight'
    }
    intel = ''
    for k, v in cat_map.items():
        if k in cat:
            intel = v
            break
    if not intel:
        it = (m.get('intelligenceTier') or '').strip()
        if it:
            intel = it
    if intel:
        row['Inteligencia'] = intel

    if 'priceEntry' in m:
        row['Entrada ($/M)'] = m.get('priceEntry', '')
    if 'priceExit' in m:
        row['Salida ($/M)'] = m.get('priceExit', '')

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
<title>Modelos de GitHub Copilot</title>
<style>
  :root { color-scheme: dark light; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 24px; background-color: #0d1117; color: #c9d1d9; }
  h1 { font-size: 1.5rem; margin: 0 0 6px; color: #f0f6fc; display: flex; align-items: center; gap: 10px; }
  .badge-copilot { background: #238636; color: #ffffff; font-size: 11px; padding: 3px 8px; border-radius: 12px; text-transform: uppercase; font-weight: bold; }
  .toolbar { display: flex; gap: 12px; align-items: center; margin: 16px 0; flex-wrap: wrap; }
  input[type=search] { padding: 8px 12px; border: 1px solid #30363d; border-radius: 6px;
                        background: #161b22; color: #f0f6fc; width: 280px; font-size: 13px; outline: none; }
  input[type=search]:focus { border-color: #58a6ff; box-shadow: 0 0 0 3px rgba(56,139,253,0.3); }
  .switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;
            font-size: 13px; color: #c9d1d9; }
  .switch input { display: none; }
  .switch .track { width: 40px; height: 22px; background: #30363d; border-radius: 11px; position: relative;
                   transition: background .2s; flex-shrink: 0; }
  .switch .track::after { content: ''; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px;
                          background: #f0f6fc; border-radius: 50%; transition: transform .2s; }
  .switch input:checked + .track { background: #238636; }
  .switch input:checked + .track::after { transform: translateX(18px); }
  .switch .unit-text { display: inline-flex; flex-direction: column; line-height: 1.15; }
  .switch .unit-text span { opacity: .55; transition: opacity .2s; }
  .switch input:checked ~ .unit-text #unitOn { opacity: 1; font-weight: 600; color: #3fb950; }
  .switch input:not(:checked) ~ .unit-text #unitOff { opacity: 1; font-weight: 600; color: #f0f6fc; }
  .table-wrap { overflow: auto; border: 1px solid #30363d; border-radius: 8px; background: #161b22; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; text-align: left; }
  thead th { position: sticky; background: #21262d; color: #f0f6fc; cursor: pointer;
               user-select: none; padding: 8px 10px; white-space: nowrap; z-index: 1; border-bottom: 1px solid #30363d; }
  thead tr:first-child th { top: 0; }
  thead tr:last-child th { top: 33px; }
  thead th:hover { background: #30363d; }
  thead th .arrow { opacity: .5; font-size: 11px; margin-left: 4px; }
  tbody td { padding: 9px 14px; border-top: 1px solid #21262d; white-space: nowrap; color: #c9d1d9; }
  tbody tr:hover { background: #1f242c; }
  tbody tr.row-deprecated { opacity: 0.55; }
  tbody tr.row-deprecated:hover { opacity: 0.85; background: #261f22; }
  tbody tr.row-deprecated td:first-child { text-decoration: line-through; text-decoration-color: #f85149; text-decoration-thickness: 2px; }
  .tag-intel { padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background: #1f6feb22; color: #58a6ff; border: 1px solid #1f6feb44; }
  .tag-deprecated { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #da363322; color: #f85149; border: 1px solid #da363366; text-decoration: none; vertical-align: middle; }
  .tag-promo { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #bb800922; color: #e3b341; border: 1px solid #bb800966; vertical-align: middle; cursor: help; }
  td.numeric, th.numeric { text-align: right; }
  .tag-status { padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .status-ga { background: #23863622; color: #3fb950; border: 1px solid #23863666; }
  .status-preview { background: #9e6a0322; color: #d29922; border: 1px solid #9e6a0366; }
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
  <svg height="28" width="28" viewBox="0 0 16 16" fill="currentColor" style="vertical-align: middle;">
    <path d="M7.998 15.035c-4.562 0-7.873-2.914-7.998-3.749V9.338c.085-.628.677-1.686 1.588-2.065.013-.07.024-.143.036-.218.029-.183.06-.384.126-.612-.201-.508-.254-1.084-.254-1.656 0-.87.128-1.769.693-2.484.579-.733 1.494-1.124 2.724-1.261 1.206-.134 2.262.034 2.944.765.05.053.096.108.139.165.044-.057.094-.112.143-.165.682-.731 1.738-.899 2.944-.765 1.23.137 2.145.528 2.724 1.261.566.715.693 1.614.693 2.484 0 .572-.053 1.148-.254 1.656.066.228.098.429.126.612.012.076.024.148.037.218.924.385 1.522 1.471 1.591 2.095v1.872c0 .766-3.351 3.795-8.002 3.795Z"></path>
  </svg>
  Modelos de GitHub Copilot
  <span class="badge-copilot">GitHub Copilot</span>
</h1>
<p class="muted">Datos referenciados desde la documentación oficial de <a href="%%PAGE_URL%%" target="_blank">%%PAGE_URL%%</a>.
<br>Actualizado: %%UPDATED%%.<br>Haz clic en cualquier columna para ordenar. Filtra libremente por nombre o capacidades.</p>
<div class="toolbar">
  <input type="search" id="filter" placeholder="Filtrar modelos de Copilot...">
  <label class="switch" id="unitSwitchWrap" title="1 AI credit = $0.01 USD (oficial). Alterna entre dólares y créditos.">
    <input type="checkbox" id="unitSwitch">
    <span class="track"></span>
    <span class="unit-text"><span id="unitOn">AI créditos</span><span id="unitOff">USD $</span></span>
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
let sortKey = 'Nombre';
let sortAsc = true;
let filterText = '';

const numericCols = ['Entrada ($/M)', 'Entrada Caché ($/M)', 'Escritura Caché ($/M)', 'Salida ($/M)'];

// 1 AI credit = $0.01 USD (tasa oficial de GitHub) -> $1 = 100 créditos
const CREDITS_PER_DOLLAR = 100;
let unit = 'usd'; // 'usd' | 'credits'

function fmtCredits(num) {
    const cr = Math.round(num * CREDITS_PER_DOLLAR * 10) / 10;
    return cr % 1 === 0 ? String(cr) : cr.toFixed(1);
}

function fmtPrice(raw) {
    // Precios con un solo decimal se muestran con 2: $0.1 -> $0.10
    const m = String(raw).match(/^([$]?)(\\d+)\\.(\\d)$/);
    return m ? m[1] + m[2] + '.' + m[3] + '0' : String(raw);
}

function cellValue(r, k) {
    const raw = r[k] || '';
    if (k === 'Nombre') {
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
    if (k === 'Inteligencia' && raw) {
        return '<span class="tag-intel">' + raw + '</span>';
    }
    if (k === 'Estado') {
        const st = raw;
        const cls = st.includes('GA') ? 'status-ga' : 'status-preview';
        return '<span class="tag-status ' + cls + '">' + st + '</span>';
    }
    if (numericCols.includes(k)) {
        if (!raw || raw === 'Not applicable' || raw === '-') return '<span class="muted">' + (raw || '-') + '</span>';
        const num = parseFloat(String(raw).replace(/[^0-9.\\-]/g, '')) || 0;
        const disp = unit === 'credits' && num > 0 ? fmtCredits(num) : fmtPrice(raw);
        const sortNum = unit === 'credits' ? num * CREDITS_PER_DOLLAR : num;
        return '<span class="numeric" data-num="' + sortNum + '">' + disp + '</span>';
    }
    return raw;
}

function compareRows(a, b) {
    const ka = sortKey;
    if (ka === 'Inteligencia') {
        const ra = (a[ka] || '').match(/^(\\d+)\\+/); const rb = (b[ka] || '').match(/^(\\d+)\\+/);
        const na = ra ? parseInt(ra[1], 10) : null; const nb = rb ? parseInt(rb[1], 10) : null;
        if (na !== null || nb !== null) {
            const va = na === null ? -1 : na; const vb = nb === null ? -1 : nb;
            return sortAsc ? va - vb : vb - va;
        }
    }
    if (numericCols.includes(ka)) {
        const va = parseFloat((a[ka] || '').toString().replace(/[^0-9.\\-]/g, '')) || 0;
        const vb = parseFloat((b[ka] || '').toString().replace(/[^0-9.\\-]/g, '')) || 0;
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

document.querySelector('#unitSwitch').addEventListener('change', e => {
    unit = e.target.checked ? 'credits' : 'usd';
    document.querySelectorAll('.price-group').forEach(th => {
        th.textContent = unit === 'usd' ? 'Precio ($/M)' : 'Precio (créditos/M)';
    });
    render();
});

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
    parser = argparse.ArgumentParser(description="Modelos y precios de GitHub Copilot -> HTML ordenable")
    parser.add_argument("--out", default=".", help="directorio de salida (por defecto: .)")
    args = parser.parse_args()

    try:
        models = load_copilot_models()
    except Exception as e:
        print('ERROR:', e)
        return 1
    rows = [clean_model(m) for m in models]

    out_dir = args.out.rstrip('/')
    os.makedirs(out_dir, exist_ok=True)
    write_html(rows, f"{out_dir}/copilot_models.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
