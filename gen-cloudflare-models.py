#!/usr/bin/env python3
"""
Extrae y organiza los modelos y precios de Cloudflare Workers AI.

Genera:
  - cloudflare_models.html (tabla interactiva filtrable y ordenable)

Datos extraídos dinámicamente desde:
  https://developers.cloudflare.com/workers-ai/platform/pricing/index.md
  (versión Markdown: trae las tablas LLM / Embeddings / Imagen / Audio / Otros)

Detecta los modelos que requieren método de pago (no funcionan con el
plan gratuito) a partir de la nota oficial:
  "Some models require a paid billing method. This applies to `@cf/...`, ..."
y los marca con la columna "Plan gratuito" = "Requiere pago".

Uso:
    python3 gen-cloudflare-models.py
    python3 gen-cloudflare-models.py --out datos
"""

import argparse
import html as _html
import json
import os
import re
import subprocess
import sys

from common import build_header_cells, stamp_updated

PAGE_URL = "https://developers.cloudflare.com/workers-ai/platform/pricing/"
MD_URL = "https://developers.cloudflare.com/workers-ai/platform/pricing/index.md"

COLS = [
    ("name", "Modelo"),
    ("freePlan", "Plan gratuito"),
    ("priceEntry", "Entrada ($/M)"),
    ("priceCache", "Caché entrada ($/M)"),
    ("priceExit", "Salida ($/M)"),
    ("priceNeurons", "Precio (neuronas)"),
    ("notes", "Notas"),
]

HEADER_GROUPS = [
    (None, [("name", "Modelo")]),
    (None, [("freePlan", "Plan gratuito")]),
    ("Precio ($/M tokens)", [
        ("priceEntry", "Entrada"),
        ("priceCache", "Caché entrada"),
        ("priceExit", "Salida"),
    ]),
    (None, [("priceNeurons", "Precio neuronas")]),
    (None, [("notes", "Notas")]),
]

# Solo generación de texto, como Claude/Copilot/Cursor:
# únicamente la sección "LLM model pricing" con precio de
# entrada y salida por millón de tokens.
WANT_SECTION = "LLM"

CATEGORY_MAP = [
    ("llm", "LLM"),
    ("embedding", "Embeddings"),
    ("image", "Imagen"),
    ("audio", "Audio"),
    ("other", "Otros"),
]

PAID_NOTE_RE = re.compile(
    r"Some models require a paid billing method\.(.*?)(?:\n\n|\n## )",
    re.IGNORECASE | re.S,
)

PRICE_M_RE = re.compile(
    r"\$([0-9]+(?:\.[0-9]+)?)\s*per M (cached input|input|output) tokens",
    re.IGNORECASE,
)


def fetch_md(url: str, timeout: int = 30) -> str:
    result = subprocess.run(
        ["curl", "-sL", "-H", "User-Agent: Mozilla/5.0", url],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed: {result.stderr[:200]}")
    return result.stdout


def detect_paid_models(md: str) -> set[str]:
    """Extrae el set de modelos que exigen pago desde la nota oficial."""
    paid: set[str] = set()
    m = PAID_NOTE_RE.search(md)
    scope = m.group(1) if m else md[:4000]
    for model in re.findall(r"@cf/[A-Za-z0-9\-/_.]+", scope):
        paid.add(model.strip().lower())
    return paid


def clean_cell(cell: str) -> str:
    cell = cell.replace("<br>", " | ").replace("<br/>", " | ").replace("<br />", " | ")
    cell = re.sub(r"<.*?>", "", cell)
    cell = _html.unescape(cell)
    return re.sub(r"\s+", " ", cell).strip()


def extract_m_prices(price_tokens: str) -> tuple[str, str, str]:
    entry = cache = exit_ = ""
    for m in PRICE_M_RE.finditer(price_tokens):
        amount, kind = f"${m.group(1)}", m.group(2).lower()
        if "cached" in kind and not cache:
            cache = amount
        elif kind == "input" and not entry:
            entry = amount
        elif kind == "output" and not exit_:
            exit_ = amount
    return entry, cache, exit_


def category_of(heading: str) -> str:
    low = heading.lower()
    for key, label in CATEGORY_MAP:
        if key in low:
            return label
    return heading.strip() or "Otros"


def parse_md_tables(md: str, paid: set[str]) -> list[dict]:
    models: list[dict] = []
    lines = md.splitlines()
    current_section = "Otros"
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m_head = re.match(r"##\s+(.*)", line)
        if m_head:
            current_section = category_of(m_head.group(1))
        if line.startswith("|") and i + 1 < len(lines) and re.match(
            r"^\|[\s:\-|]+\|\s*$", lines[i + 1].strip()
        ):
            headers = [h.strip().lower() for h in line.strip("|").split("|")]
            # Solo tablas con columna Model
            if not any("model" in h for h in headers):
                i += 1
                continue
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                # Rellenar si faltan celdas
                while len(cells) < len(headers):
                    cells.append("")
                row = dict(zip(headers, cells))
                raw_name = clean_cell(row.get("model", ""))
                if not raw_name or raw_name.lower() == "model":
                    i += 1
                    continue
                price_tokens = clean_cell(row.get("price in tokens", ""))
                price_neurons = clean_cell(row.get("price in neurons", ""))
                # Solo LLM (texto); el resto de secciones se ignoran
                if current_section != WANT_SECTION:
                    i += 1
                    continue
                entry, cache, exit_ = extract_m_prices(price_tokens)
                # Solo modelos con precio de entrada y salida por M tokens
                if not entry or not exit_:
                    i += 1
                    continue
                requires_paid = raw_name.lower() in paid
                notes = (
                    "Requiere Workers Paid o AI Gateway credits (no disponible con cuota gratuita)."
                    if requires_paid
                    else ""
                )
                models.append({
                    "name": raw_name,
                    "freePlan": "Requiere pago" if requires_paid else "Sí",
                    "priceEntry": entry,
                    "priceCache": cache,
                    "priceExit": exit_,
                    "priceNeurons": price_neurons,
                    "notes": notes,
                    "_requiresPaid": requires_paid,
                    "_isDeprecated": False,
                    "_supersededBy": "",
                    "_promo": "",
                })
                i += 1
            continue
        i += 1
    return models


def load_cloudflare_models() -> tuple[list[dict], set[str]]:
    md = fetch_md(MD_URL)
    paid = detect_paid_models(md)
    models = parse_md_tables(md, paid)
    if not models:
        raise RuntimeError(f"No se encontraron tablas parseables en {MD_URL}")
    print(f"Obtenidos {len(models)} modelos desde la web: {MD_URL}")
    print(f"Modelos que requieren pago: {len(paid)}")
    return models, paid


def clean_model(m: dict) -> dict:
    row: dict[str, str] = {}
    for key, label in COLS:
        v = m.get(key, "")
        if v is None:
            v = ""
        row[label] = v
    row["_isDeprecated"] = m.get("_isDeprecated", False)
    row["_supersededBy"] = m.get("_supersededBy", "")
    row["_promo"] = m.get("_promo", "")
    row["_requiresPaid"] = m.get("_requiresPaid", False)
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
<title>Modelos y Precios de Cloudflare Workers AI</title>
<style>
  :root { color-scheme: dark light; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 24px; background-color: #0d1117; color: #c9d1d9; }
  h1 { font-size: 1.5rem; margin: 0 0 6px; color: #f0f6fc; display: flex; align-items: center; gap: 10px; }
  .badge-cloudflare { background: #f6821f; color: #ffffff; font-size: 11px; padding: 3px 8px; border-radius: 12px; text-transform: uppercase; font-weight: bold; }
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
  tbody tr.row-paid td:nth-child(2) { color: #e3b341; }
  .tag-deprecated { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #da363322; color: #f85149; border: 1px solid #da363366; text-decoration: none; vertical-align: middle; }
  .tag-promo { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #bb800922; color: #e3b341; border: 1px solid #bb800966; vertical-align: middle; cursor: help; }
  .tag-paid { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #9e6a0322; color: #e3b341; border: 1px solid #9e6a0366; vertical-align: middle; }
  .tag-free { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background: #23863622; color: #3fb950; border: 1px solid #23863666; }
  .tag-requires { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background: #9e6a0322; color: #e3b341; border: 1px solid #9e6a0366; }
  .tag-cat { padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; background: #1f6feb22; color: #58a6ff; border: 1px solid #1f6feb66; }
  td.numeric, th.numeric { text-align: right; }
  td.wrap { white-space: normal; min-width: 200px; max-width: 420px; color: #8b949e; font-size: 12px; }
  td.model { white-space: normal; min-width: 220px; max-width: 380px; word-break: break-all; }
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
    <path d="M7 18a4.5 4.5 0 1 1 .6-8.96A5.5 5.5 0 0 1 18.3 10H19a3.5 3.5 0 0 1 .5 6.96L7 18z"/>
  </svg>
  Modelos y Precios de Cloudflare Workers AI
  <span class="badge-cloudflare">Cloudflare</span>
</h1>
<p class="muted">Datos referenciados desde la documentación oficial de <a href="%%PAGE_URL%%" target="_blank">%%PAGE_URL%%</a>.
<br>Actualizado: %%UPDATED%%. Solo modelos LLM de generación de texto. Cuota gratuita: 10.000 Neuronas/día ($0.011 / 1.000 Neuronas por encima).
<br>Haz clic en cualquier columna para ordenar. Filtra libremente por nombre.</p>
<div class="toolbar">
  <input type="search" id="filter" placeholder="Filtrar modelos de Cloudflare...">
  <label class="muted" style="font-size:13px"><input type="checkbox" id="onlyFree" style="vertical-align:middle"> Solo plan gratuito</label>
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
let onlyFree = false;

const numericCols = ['Entrada ($/M)', 'Cach\\u00e9 entrada ($/M)', 'Salida ($/M)'];

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
            nameHtml += ' <span class="tag-deprecated" title="Superado en prestaciones/precio por ' + (r._supersededBy || 'versi\\u00f3n superior') + '">Superado por ' + (r._supersededBy || 'versi\\u00f3n m\\u00e1s reciente') + '</span>';
        }
        if (r._promo) {
            const tip = String(r._promo).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
            nameHtml += ' <span class="tag-promo" title="' + tip + '">Promo</span>';
        }
        if (r._requiresPaid) {
            nameHtml += ' <span class="tag-paid" title="Requiere Workers Paid o AI Gateway credits">Requiere pago</span>';
        }
        return nameHtml;
    }
    if (k === 'Plan gratuito') {
        return raw === 'S\u00ed' ? '<span class="tag-free">S\u00ed</span>' : '<span class="tag-requires" title="Requiere Workers Paid o AI Gateway credits">Requiere pago</span>';
    }
    if (k === 'Precio (neuronas)' || k === 'Notas') {
        if (!raw) return '<span class="muted">-</span>';
        return '<span class="wrap">' + raw + '</span>';
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
    if (onlyFree) rows = rows.filter(r => !r._requiresPaid);
    rows.sort(compareRows);
    document.querySelector('#count').textContent = rows.length + ' modelos encontrados';
    tbody.innerHTML = rows.map(r => '<tr class="' + (r._isDeprecated ? 'row-deprecated' : '') + (r._requiresPaid ? ' row-paid' : '') + '">' + HEADERS.map(k => '<td class="' + (k === 'Modelo' ? 'model' : (k === 'Precio (neuronas)' || k === 'Notas' ? 'wrap' : '')) + '">' + cellValue(r, k) + '</td>').join('') + '</tr>').join('');
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
document.querySelector('#onlyFree').addEventListener('change', e => { onlyFree = e.target.checked; render(); });

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
    parser = argparse.ArgumentParser(description="Modelos y precios de Cloudflare Workers AI -> HTML")
    parser.add_argument("--out", default=".", help="directorio de salida (por defecto: .)")
    args = parser.parse_args()

    try:
        models, _ = load_cloudflare_models()
    except Exception as e:
        print("ERROR:", e)
        return 1

    rows = [clean_model(m) for m in models]
    out_dir = args.out.rstrip("/")
    os.makedirs(out_dir, exist_ok=True)
    write_html(rows, f"{out_dir}/cloudflare_models.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
