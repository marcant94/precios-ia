#!/usr/bin/env python3
"""
Extrae y organiza los modelos y precios de Mistral AI.

Genera:
  - mistral_models.html (tabla interactiva filtrable y ordenable)

Datos extraídos dinámicamente desde:
  https://mistral.ai/pricing/api/
  (tarjetas HTML con <mistral-atom-text-price data-prices='{"priceUsd":...}'>)

Uso:
    python3 gen-mistral-models.py
    python3 gen-mistral-models.py --out datos
"""

import argparse
import html as _html
import json
import os
import re
import subprocess
import sys

from common import build_header_cells, stamp_updated

PAGE_URL = "https://mistral.ai/pricing/api/"

COLS = [
    ("name", "Modelo"),
    ("type", "Tipo"),
    ("license", "Licencia"),
    ("freePlan", "Plan"),
    ("category", "Categoría"),
    ("priceEntry", "Entrada ($/M)"),
    ("priceExit", "Salida ($/M)"),
    ("notes", "Notas"),
]

HEADER_GROUPS = [
    (None, [("name", "Modelo")]),
    (None, [("type", "Tipo")]),
    (None, [("license", "Licencia")]),
    (None, [("freePlan", "Plan")]),
    (None, [("category", "Categoría")]),
    ("Precio ($/M tokens)", [
        ("priceEntry", "Entrada"),
        ("priceExit", "Salida"),
    ]),
    (None, [("notes", "Notas")]),
]

# Tipos principales (una etiqueta por modelo para el desplegable).
# Prioridad: OCR > transcripción > voz > embeddings > clasificación > herramientas > texto.
def mistral_tipo(cats: list[str], name: str, labels: list[str]) -> str:
    low_cats = [c.lower() for c in cats]
    low_name = name.lower()
    low_labels = " ".join(labels).lower()
    if "ocr" in low_cats or "ocr" in low_name or re.search(r"\bocr\b", low_labels):
        return "OCR / Documento"
    if "transcription" in low_cats or "transcri" in low_name:
        return "Transcripción"
    if "voice" in low_cats or "tts" in low_name or "audio generation" in low_labels:
        return "Voz"
    if "embedding" in low_cats or "embed" in low_name:
        return "Embeddings"
    if "classifier-apis" in low_cats or "moderation" in low_name or "classifier" in low_name:
        return "Clasificación"
    if "tools" in low_cats:
        return "Herramientas"
    return "Texto"

TITLE_RE = re.compile(r'<p class="text-h5[^>]*>(.*?)</p>', re.S)
DESC_RE = re.compile(r'<p class="text-body-base text-current">(.*?)</p>', re.S)
BADGE_RE = re.compile(r'(<p class="text-eyebrow[^>]*>.*?</p>)', re.S)
PRICE_RE = re.compile(
    r'<p class="text-body-base text-current relative">(.*?)</p>\s*'
    r'<mistral-atom-text-price([^>]*)>',
    re.S,
)
PRICES_ATTR_RE = re.compile(r'data-prices="([^"]+)"')


def fetch_html(url: str, timeout: int = 30) -> str:
    result = subprocess.run(
        ["curl", "-sL", "-H", "User-Agent: Mozilla/5.0", url],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed: {result.stderr[:200]}")
    return result.stdout


def clean_text(s: str) -> str:
    # Quitar tooltips incrustados en las etiquetas de precio
    # (<mistral-atom-button-tooltip ...>...</...> y <div id="tooltip-...">...</div>)
    s = re.sub(
        r"<mistral-atom-button-tooltip[\s\S]*?</mistral-atom-button-tooltip>",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r'<div[^>]*id="tooltip-[^"]*"[\s\S]*?</div>\s*</div>',
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"<.*?>", " ", s, flags=re.S)
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()


def fmt_usd(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    # Evitar "$0.30000000000000004": recortar ceros
    s = f"{f:.6f}".rstrip("0").rstrip(".")
    return f"${s}"


def parse_card(title: str, seg: str) -> dict | None:
    name = clean_text(title)
    if not name or name.lower() == "enterprise apis":
        return None

    cats = re.findall(r'data-category-slug="([^"]+)"', seg)
    # La tarjeta de filtros "Enterprise APIs" lista todas las categorías
    if len(cats) > 10:
        return None

    descs = [clean_text(d) for d in DESC_RE.findall(seg)]
    description = descs[0] if descs else ""

    # Licencia: badges eyebrow SIN data-category-slug (Open/Premier/Labs/New/...)
    # Se prefiere la insignia con pinta de licencia; si no hay, la primera.
    license_ = ""
    fallback_ = ""
    for b in BADGE_RE.findall(seg):
        if "data-category-slug" in b:
            continue
        txt = clean_text(b)
        if not txt:
            continue
        if not fallback_:
            fallback_ = txt
        low = txt.lower()
        if re.search(r"open|premier|labs|apache|mit\b|mit |cc[ -]?by|bsd|mogul|mrl", low):
            license_ = txt
            break
    if not license_:
        license_ = fallback_

    # Licencias de pesos abiertos usables con cuenta gratuita.
    is_open_license = bool(re.search(
        r"\bopen\b|apache|mit\b|mit |cc[ -]?by|bsd|\blabs\b", license_, re.IGNORECASE
    ))

    if "third-party" in cats and license_.lower() not in ("open", "premier", "labs"):
        license_ = "Terceros"

    prices: list[tuple[str, str]] = []
    for m in PRICE_RE.finditer(seg):
        label = clean_text(m.group(1))
        attrs = m.group(2)
        pm = PRICES_ATTR_RE.search(attrs)
        if not pm:
            continue
        try:
            pdata = json.loads(_html.unescape(pm.group(1)))
        except (ValueError, TypeError):
            continue
        usd = pdata.get("priceUsd")
        suffix = (pdata.get("suffix") or "").strip()
        amount = fmt_usd(usd)
        if not amount:
            continue
        if suffix:
            amount = f"{amount} {suffix}".strip()
        prices.append((label, amount))

    entry = exit_ = ""
    extra_prices: list[str] = []
    for label, amount in prices:
        low = label.lower()
        if low == "input (/m tokens)":
            entry = amount
        elif low == "output (/m tokens)":
            exit_ = amount
        else:
            extra_prices.append(f"{label}: {amount}")

    # Plan gratuito: etiqueta "Free" en la tarjeta (precio Free sin data-prices)
    # o licencia de pesos abiertos / experimental (Open, Apache, MIT, CC-BY, Labs).
    # Los Labs llevan además marca temporal (acceso por periodo limitado).
    has_free_label = bool(re.search(r'>\s*Free\s*<', seg, re.IGNORECASE))
    has_limited = bool(re.search(r'limited\s+period|limited\s+time|highly\s+accessible', seg, re.IGNORECASE))
    is_labs = "labs" in license_.lower()
    is_free = has_free_label or is_open_license or is_labs
    is_temporal = has_limited or is_labs

    tipo = mistral_tipo(cats, name, [p[0] for p in prices])
    # Los modelos con etiqueta "Free" entran aunque no traigan precio
    # (endpoint gratuito sin data-prices).
    if has_free_label and not prices:
        entry = "Gratis"
    # Texto exige entrada+salida por token; el resto muestra su precio
    # específico (OCR por páginas, voz por min/carácter, etc.).
    if tipo == "Texto":
        if entry == "Gratis":
            pass
        elif not entry or not exit_:
            return None
    elif not entry and not exit_:
        if not prices:
            return None
        entry = "; ".join(extra_prices[:3])[:160]
    elif extra_prices and (not entry or not exit_):
        entry = entry or "; ".join(extra_prices[:3])[:160]

    seg_text = clean_text(seg)
    notes_parts: list[str] = []
    if description:
        notes_parts.append(description[:160] + ("..." if len(description) > 160 else ""))
    m_ep = re.search(r"Available on\s*(/v1/\S+)", seg_text)
    if m_ep:
        notes_parts.append(f"Disponible en {m_ep.group(1)}")

    category = ", ".join(cats) if cats else ""

    if is_free and not entry and not exit_ and has_free_label:
        entry = "Gratis"
    return {
        "name": name,
        "type": tipo,
        "license": license_,
        "freePlan": "Gratuito" if is_free else "Suscripción",
        "category": category,
        "priceEntry": entry,
        "priceExit": exit_,
        "notes": " — ".join(notes_parts),
        "_isDeprecated": False,
        "_supersededBy": "",
        "_promo": "",
        "_isFree": is_free,
        "_isTemporal": is_temporal,
        "_requiresPaid": not is_free,
    }


def load_mistral_models() -> list[dict]:
    html = fetch_html(PAGE_URL)
    titles = list(TITLE_RE.finditer(html))
    if not titles:
        raise RuntimeError(f"No se encontraron tarjetas parseables en {PAGE_URL}")

    by_name: dict[str, dict] = {}
    for idx, tm in enumerate(titles):
        start = tm.start()
        end = titles[idx + 1].start() if idx + 1 < len(titles) else start + 20000
        card = parse_card(tm.group(1), html[start:end])
        if not card:
            continue
        key = card["name"].lower()
        prev = by_name.get(key)
        # Preferir la tarjeta con categorías y más precios (las destacadas
        # iniciales son duplicados sin categorías).
        if prev is None:
            by_name[key] = card
        else:
            score = lambda c: (len(c["category"]), len(c["priceEntry"]) + len(c["priceExit"]))
            if score(card) > score(prev):
                by_name[key] = card

    models = list(by_name.values())
    if not models:
        raise RuntimeError(f"No se encontraron tarjetas parseables en {PAGE_URL}")
    print(f"Obtenidos {len(models)} modelos desde la web: {PAGE_URL}")
    return models


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
    row["_isFree"] = m.get("_isFree", False)
    row["_isTemporal"] = m.get("_isTemporal", False)
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
<title>Modelos y Precios de Mistral AI</title>
<style>
  :root { color-scheme: dark light; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 24px; background-color: #0d1117; color: #c9d1d9; }
  h1 { font-size: 1.5rem; margin: 0 0 6px; color: #f0f6fc; display: flex; align-items: center; gap: 10px; }
  .badge-mistral { background: #ff7000; color: #ffffff; font-size: 11px; padding: 3px 8px; border-radius: 12px; text-transform: uppercase; font-weight: bold; }
  .toolbar { display: flex; gap: 12px; align-items: center; margin: 16px 0; flex-wrap: wrap; }
  input[type=search] { padding: 8px 12px; border: 1px solid #30363d; border-radius: 6px;
                        background: #161b22; color: #f0f6fc; width: 280px; font-size: 13px; outline: none; }
  input[type=search]:focus { border-color: #58a6ff; box-shadow: 0 0 0 3px rgba(56,139,253,0.3); }
  select#typeFilter { padding: 8px 12px; border: 1px solid #30363d; border-radius: 6px;
                        background: #161b22; color: #f0f6fc; font-size: 13px; outline: none; }
  select#typeFilter:focus { border-color: #58a6ff; }
  .tag-type { padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600;
              background: #6e768122; color: #c9d1d9; border: 1px solid #6e768166; white-space: nowrap; }
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
  tbody tr.row-deprecated td:nth-child(1) { text-decoration: line-through; text-decoration-color: #f85149; text-decoration-thickness: 2px; }
  .tag-deprecated { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #da363322; color: #f85149; border: 1px solid #da363366; text-decoration: none; vertical-align: middle; }
  .tag-promo { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #bb800922; color: #e3b341; border: 1px solid #bb800966; vertical-align: middle; cursor: help; }
  tbody tr.row-paid td:nth-child(1) { color: #e3b341; }
  .tag-temporal { display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #9e6a0322; color: #e3b341; border: 1px solid #9e6a0366; vertical-align: middle; }
  .tag-plan-free { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background: #23863622; color: #3fb950; border: 1px solid #23863666; }
  .tag-plan-requires { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background: #9e6a0322; color: #e3b341; border: 1px solid #9e6a0366; }
  .tag-lic { padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .lic-open { background: #ff700022; color: #ffa657; border: 1px solid #ff700066; }
  .lic-premier { background: #1f6feb22; color: #58a6ff; border: 1px solid #1f6feb66; }
  .lic-labs { background: #6e768122; color: #8b949e; border: 1px solid #6e768166; }
  td.numeric, th.numeric { text-align: right; }
  td.wrap { white-space: normal; min-width: 180px; max-width: 380px; color: #8b949e; font-size: 12px; }
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
  <svg height="28" width="28" viewBox="0 0 24 24" fill="currentColor" style="vertical-align: middle; color: #ff7000;">
    <path d="M4 4h4l4 8 4-8h4v16h-4v-9l-4 7-4-7v9H4V4z"/>
  </svg>
  Modelos y Precios de Mistral AI
  <span class="badge-mistral">Mistral</span>
</h1>
<p class="muted">Datos referenciados desde la documentación oficial de <a href="%%PAGE_URL%%" target="_blank">%%PAGE_URL%%</a>.
<br>Actualizado: %%UPDATED%%. Todos los tipos de modelo (filtra por Tipo). Los de texto traen precio por millón de tokens. Plan Gratuito: modelos Open/Labs o con precio Free en la web oficial.
<br>Haz clic en cualquier columna para ordenar. Filtra libremente por nombre, licencia o categoría.</p>
<div class="toolbar">
  <input type="search" id="filter" placeholder="Filtrar modelos de Mistral...">
  <select id="typeFilter" title="Filtrar por tipo de modelo"><option value="">Todos los tipos</option></select>
  <label class="muted" style="font-size:13px"><input type="checkbox" id="onlyFree" style="vertical-align:middle"> Solo gratuitos</label>
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
let onlyFree = false;

const numericCols = ['Entrada ($/M)', 'Salida ($/M)'];

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
        if (r._isTemporal) {
            nameHtml += ' <span class="tag-temporal" title="Acceso gratuito por periodo limitado">Temporal</span>';
        }
        return nameHtml;
    }
    if (k === 'Plan') {
        return raw === 'Gratuito' ? '<span class="tag-plan-free">Gratuito</span>' : '<span class="tag-plan-requires" title="Requiere suscripción o pago por uso">Suscripci\u00f3n</span>';
    }
    if (k === 'Licencia') {
        if (!raw) return '<span class="muted">-</span>';
        const low = raw.toLowerCase();
        const cls = low.includes('open') ? 'lic-open' : low.includes('premier') ? 'lic-premier' : 'lic-labs';
        return '<span class="tag-lic ' + cls + '">' + raw + '</span>';
    }
    if (k === 'Notas' || k === 'Categor\u00eda') {
        if (!raw) return '<span class="muted">-</span>';
        return '<span class="wrap">' + raw + '</span>';
    }
    if (numericCols.includes(k)) {
        if (!raw || raw === '-') return '<span class="muted">-</span>';
        const num = parseFloat(String(raw).replace(/[^0-9.\\-]/g, '')) || 0;
        return '<span class="numeric" data-num="' + num + '">' + fmtPrice(raw) + '</span>';
    }
    if (k === 'Tipo' && raw) {
        return '<span class="tag-type">' + raw + '</span>';
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
    if (typeFilter) rows = rows.filter(r => (r['Tipo'] || '') === typeFilter);
    if (onlyFree) rows = rows.filter(r => !r._requiresPaid);
    rows.sort(compareRows);
    document.querySelector('#count').textContent = rows.length + ' modelos encontrados';
    tbody.innerHTML = rows.map(r => '<tr class="' + (r._isDeprecated ? 'row-deprecated' : '') + (r._requiresPaid ? ' row-paid' : '') + '">' + HEADERS.map(k => '<td class="' + ((k === 'Notas' || k === 'Categor\u00eda') ? 'wrap' : '') + '">' + cellValue(r, k) + '</td>').join('') + '</tr>').join('');
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
    parser = argparse.ArgumentParser(description="Modelos y precios de Mistral AI -> HTML")
    parser.add_argument("--out", default=".", help="directorio de salida (por defecto: .)")
    args = parser.parse_args()

    try:
        models = load_mistral_models()
    except Exception as e:
        print("ERROR:", e)
        return 1

    rows = [clean_model(m) for m in models]
    out_dir = args.out.rstrip("/")
    os.makedirs(out_dir, exist_ok=True)
    write_html(rows, f"{out_dir}/mistral_models.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
