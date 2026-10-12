/* Frontend genérico: tabla unificada multi-proveedor.
   Datos: data/all.json {generatedAt, providers, total, rows}, data/errors.json */
'use strict';

let DATA = [];
let META = {};
let ERRORS = [];
let LOGOS = {};        // provider -> svg markup (currentColor)
let sortKey = 'name';
let sortAsc = true;
let filterText = '';
let provFilter = '';   // '' = Todos
let typeFilter = '';
let planFilter = '';
let budgetFilter = false;
let unit = 'usd';      // 'usd' | 'credits' (solo copilot)
let showDeprecated = false;
let showEst = false;
let estIn = 20000;     // tokens de entrada por petición
let estOut = 1000;     // tokens de salida por petición
let estCacheHit = 0.85; // tasa de acierto de caché (0..1)
let estBudget = 10;    // presupuesto si el modelo no declara límite mensual

const CREDITS_PER_DOLLAR = 100; // 1 AI credit = $0.01 (tasa oficial GitHub)

/* Color de marca para los logos monocromos (currentColor). */
const BRAND = {
  'opencode-go': '#58a6ff',
  'github-copilot': '#3fb950',
  'anthropic': '#d97757',
  'openai': '#10a37f',
  'google': '#4285f4',
  'mistral': '#ff7000',
  'groq': '#f55036',
  'xai': '#f0f6fc',
  'nvidia': '#76b900',
};

const NUMERIC = ['input', 'cacheRead', 'cacheWrite', 'output', 'context', 'estReq'];

const COLS = [
  ['providerName', 'Proveedor'],
  ['name', 'Modelo'],
  ['type', 'Tipo'],
  ['input', 'Entrada ($/M)'],
  ['cacheRead', 'Lectura Caché ($/M)'],
  ['cacheWrite', 'Escritura Caché ($/M)'],
  ['output', 'Salida ($/M)'],
  ['context', 'Contexto'],
  ['status', 'Estado'],
];

/* ---- formato ---- */
function fmtPrice(v) {
  if (v === null || v === undefined || v === '') return '<span class="muted">-</span>';
  return '$' + Number(v).toFixed(2);
}
function fmtCtx(v) {
  if (v === null || v === undefined || v === '') return '<span class="muted">-</span>';
  const n = Number(v);
  if (n >= 1048576) return (n / 1048576).toFixed(n % 1048576 ? 1 : 0) + 'M';
  if (n >= 1024) return Math.round(n / 1024) + 'K';
  return String(n);
}
function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escapeAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}

/* ---- estimación de peticiones ----
   Coste por petición = (tokensIn * precioEntradaEfectivo + tokensOut * precioSalida) / 1e6
   Precio efectivo de entrada = mezcla del precio con caché y sin caché según
   la tasa de acierto (el caché abarata mucho, por eso "cuenta más").
   Peticiones = presupuesto mensual / coste por petición. */
function planBudgets(r, plan) {
  const ex = r.extras || {};
  const vals = [];
  const collect = (b) => {
    const v = parseFloat(String(b || '').replace(/[^0-9.]/g, ''));
    if (v > 0) vals.push(v);
  };
  if (Array.isArray(ex.plans) && ex.plans.length) {
    ex.plans.forEach(p => { if (!plan || p.plan === plan) collect(p.monthlyBudget); });
  } else if (!plan || (ex.plan || '') === plan || (ex.plan || '').includes(plan)) {
    collect(ex.monthlyBudget);
  }
  return vals;
}

function modelBudget(r) {
  // Si hay un plan seleccionado (Go / Go Plus), solo cuenta su budget;
  // si no, el máximo entre planes.
  const vals = planBudgets(r, planFilter || '');
  if (vals.length) return Math.max.apply(null, vals);
  return 0;
}

function estRequests(r) {
  const inp = Number(r.input) || 0;
  const out = Number(r.output) || 0;
  const cr = (r.cacheRead === null || r.cacheRead === undefined) ? inp : Number(r.cacheRead);
  const effIn = estCacheHit * cr + (1 - estCacheHit) * inp;
  const perReq = (estIn * effIn + estOut * out) / 1e6;
  if (perReq <= 0) return null;
  // Límite mensual del modelo si lo conocemos (respetando el plan activo
  // Go / Go Plus); si no, el presupuesto indicado por el usuario.
  const budget = modelBudget(r) || estBudget;
  if (budget <= 0) return null;
  return Math.floor(budget / perReq);
}

function isDeprecated(r) {
  return /deprecated|retired|shut ?down/i.test(r.status || (r.extras || {}).groqStatus || '');
}

function rowType(r) {
  const mods = ((r.modalities || {}).input || []).join(' ').toLowerCase();
  if (/audio/.test(mods) && !/text|image|video/.test(mods)) return 'Voz';
  if (/image|video/.test(mods) && /audio/.test(mods)) return 'Multimodal';
  if (/image|video/.test(mods)) return 'Visión';
  if (r.reasoning) return 'Razonamiento';
  return 'Texto';
}

function logoHtml(provider) {
  const svg = LOGOS[provider];
  if (!svg) return '';
  const color = BRAND[provider] || '#f0f6fc';
  return '<span class="prov-logo" style="color:' + color + '">' + svg + '</span>';
}

/* ---- celdas ---- */
function cellValue(r, k) {
  if (k === 'providerName') {
    return '<span class="prov-cell">' + logoHtml(r.provider) + escapeHtml(r.providerName) + '</span>';
  }
  if (k === 'name') {
    let h = escapeHtml(r.name);
    const ex = r.extras || {};
    if (r.supersededBy) {
      h += ' <span class="tag-superseded" title="Superado por ' + escapeAttr(r.supersededBy) + ' en prestaciones/precio">↑ ' + escapeHtml(r.supersededBy) + '</span>';
    }
    if (ex.promo) h += ' <span class="tag-promo" title="' + escapeAttr(ex.promo) + '">Promo</span>';
    if (ex.temporal) h += ' <span class="tag-promo">Temporal</span>';
    if (isDeprecated(r)) h += ' <span class="tag-deprecated">Obsoleto</span>';
    return h;
  }
  if (k === 'type') return '<span class="tag-type">' + escapeHtml(rowType(r)) + '</span>';
  if (['input', 'cacheRead', 'cacheWrite', 'output'].includes(k)) {
    const raw = r[k];
    if (raw === null || raw === undefined || raw === '') return '<span class="muted">-</span>';
    const n = Number(raw);
    let disp;
    if (unit === 'credits' && r.provider === 'github-copilot' && n > 0) {
      const cr = Math.round(n * CREDITS_PER_DOLLAR * 10) / 10;
      disp = (cr % 1 === 0 ? String(cr) : cr.toFixed(1));
    } else {
      disp = fmtPrice(n);
    }
    return '<span class="numeric" data-num="' + n + '">' + disp + '</span>';
  }
  if (k === 'context') return '<span class="numeric">' + fmtCtx(r.context) + '</span>';
  if (k === 'estReq') {
    const e = estRequests(r);
    return e === null ? '<span class="muted">-</span>' : '<span class="numeric">' + e.toLocaleString('es') + '</span>';
  }
  if (k === 'status') {
    const st = r.status || (r.extras || {}).groqStatus || '';
    if (!st) return '<span class="muted">-</span>';
    const cls = /preview|beta|temporal/i.test(st) ? 'status-preview' : 'status-ga';
    return '<span class="tag-status ' + cls + '">' + escapeHtml(st) + '</span>';
  }
  return escapeHtml(r[k] || '');
}

/* ---- detalle ---- */
function planBlock(p, idx) {
  const items = [];
  const add = (k, v) => { if (v !== '' && v !== null && v !== undefined && v !== false) items.push('<dt>' + k + '</dt><dd>' + escapeHtml(String(v)) + '</dd>'); };
  add('Condición', p.condition);
  if (p.monthlyBudget) items.push('<dt>Límite mensual</dt><dd><span class="tag-budget">' + escapeHtml(p.monthlyBudget) + '</span></dd>');
  add('Peticiones 5h', p.req5h);
  add('Peticiones semana', p.reqWeekly);
  add('Peticiones mes', p.reqMonthly);
  add('ID OpenCode', p.ocModelId);
  add('Retención', p.retention);
  return '<div class="plan-block"><div class="plan-title">' + escapeHtml(p.plan || ('Plan ' + (idx + 1))) + '</div><dl>' + items.join('') + '</dl></div>';
}

function detailHtml(r) {
  const ex = r.extras || {};
  const items = [];
  const add = (k, v) => { if (v !== '' && v !== null && v !== undefined && v !== false) items.push('<dt>' + k + '</dt><dd>' + escapeHtml(String(v)) + '</dd>'); };
  add('ID', r.modelId);
  if (r.description) items.push('<dt>Descripción</dt><dd>' + escapeHtml(r.description) + '</dd>');
  add('Familia', r.family);
  add('Límite salida', r.outputLimit ? Number(r.outputLimit).toLocaleString('es') : '');
  add('ID canónico', r.canonicalId);
  add('Actualizado', r.lastUpdated);
  add('Razonamiento', r.reasoning ? 'Sí' : '');
  add('Tool call', r.toolCall ? 'Sí' : '');
  add('Open weights', r.openWeights ? 'Sí' : '');
  if (r.supersededBy) items.push('<dt>Superado por</dt><dd>' + escapeHtml(r.supersededBy) + '</dd>');
  add('Velocidad', ex.speed ? ex.speed + ' tok/s' : '');
  add('Límites Developer', ex.limits);
  add('Max completion', ex.maxCompletion);
  add('Max fichero', ex.maxFile);
  if (ex.enterprise) items.push('<dt>Enterprise</dt><dd>Solo contacto comercial</dd>');

  let html = '';
  if (items.length) html += '<dl>' + items.join('') + '</dl>';
  if (Array.isArray(ex.plans) && ex.plans.length) {
    html += '<div class="plans">' + ex.plans.map(planBlock).join('') + '</div>';
  }
  if (!html) return '';
  return '<tr class="detail-row" style="display:none"><td colspan="' + (COLS.length + (showEst ? 1 : 0)) + '">' + html + '</td></tr>';
}

/* ---- filtrado y orden ---- */
function matchPlan(r, want) {
  const ex = r.extras || {};
  if (Array.isArray(ex.plans)) return ex.plans.some(p => p.plan === want);
  return (ex.plan || '') === want;
}

function filteredRows() {
  let rows = DATA.filter(r => {
    if (!showDeprecated && isDeprecated(r)) return false;
    if (provFilter && r.provider !== provFilter) return false;
    if (planFilter && !matchPlan(r, planFilter)) return false;
    if (budgetFilter && !(modelBudget(r) >= 60)) return false;
    if (typeFilter && rowType(r) !== typeFilter) return false;
    if (filterText) {
      const hay = (r.name + ' ' + r.modelId + ' ' + r.providerName + ' ' + (r.description || '')).toLowerCase();
      if (!hay.includes(filterText)) return false;
    }
    return true;
  });
  rows.sort(compareRows);
  return rows;
}

function compareRows(a, b) {
  let va, vb;
  if (sortKey === 'type') { va = rowType(a); vb = rowType(b); }
  else if (sortKey === 'estReq') { va = estRequests(a) || 0; vb = estRequests(b) || 0; }
  else if (NUMERIC.includes(sortKey)) { va = Number(a[sortKey]) || 0; vb = Number(b[sortKey]) || 0; }
  else { va = String(a[sortKey] || '').toLowerCase(); vb = String(b[sortKey] || '').toLowerCase(); }
  if (va < vb) return sortAsc ? -1 : 1;
  if (va > vb) return sortAsc ? 1 : -1;
  return 0;
}

function render() {
  const rows = filteredRows();
  document.querySelector('#count').textContent = rows.length + ' modelos';
  const cols = showEst ? COLS.concat([['estReq', 'Peticiones est.']]) : COLS;
  const tbody = document.querySelector('#tbl tbody');
  tbody.innerHTML = rows.map(r =>
    '<tr data-key="' + escapeAttr(r.provider + '|' + r.modelId) + '">'
    + cols.map(([k]) => '<td>' + cellValue(r, k) + '</td>').join('')
    + '</tr>' + detailHtml(r)
  ).join('');

  const hidden = DATA.filter(isDeprecated).length;
  const depWrap = document.querySelector('#depWrap');
  depWrap.title = hidden + ' modelos obsoletos ocultos';

  document.querySelectorAll('thead th[data-k]').forEach(th => {
    th.querySelector('.arrow').textContent = th.dataset.k === sortKey ? (sortAsc ? '▲' : '▼') : '';
  });

  const isCopilot = provFilter === 'github-copilot';
  document.querySelector('#unitSwitchWrap').style.display = isCopilot ? '' : 'none';
  const isGo = provFilter === 'opencode-go';
  document.querySelector('#planWrap').style.display = isGo ? '' : 'none';
  document.querySelector('#budgetWrap').style.display = isGo ? '' : 'none';
  if (!isCopilot) unit = 'usd';
  if (!isGo) { planFilter = ''; budgetFilter = false; }
}

function buildHead() {
  const cols = showEst ? COLS.concat([['estReq', 'Peticiones est.']]) : COLS;
  document.querySelector('#tbl thead').innerHTML =
    '<tr><th rowspan="2" data-k="providerName">Proveedor <span class="arrow"></span></th>'
    + '<th rowspan="2" data-k="name">Modelo <span class="arrow"></span></th>'
    + '<th rowspan="2" data-k="type">Tipo <span class="arrow"></span></th>'
    + '<th colspan="4" class="price-group" id="priceGroupLabel">Precio ($/M)</th>'
    + '<th rowspan="2" data-k="context">Contexto <span class="arrow"></span></th>'
    + '<th rowspan="2" data-k="status">Estado <span class="arrow"></span></th>'
    + (showEst ? '<th rowspan="2" data-k="estReq">Peticiones est. <span class="arrow"></span></th>' : '')
    + '</tr><tr>'
    + '<th data-k="input">Entrada <span class="arrow"></span></th>'
    + '<th data-k="cacheRead">Lectura Caché <span class="arrow"></span></th>'
    + '<th data-k="cacheWrite">Escritura Caché <span class="arrow"></span></th>'
    + '<th data-k="output">Salida <span class="arrow"></span></th>'
    + '</tr>';
  document.querySelector('#priceGroupLabel').textContent = unit === 'usd' ? 'Precio ($/M)' : 'Precio (créditos/M)';
  document.querySelectorAll('thead th[data-k]').forEach(th => th.addEventListener('click', () => {
    if (th.dataset.k === sortKey) sortAsc = !sortAsc;
    else { sortKey = th.dataset.k; sortAsc = true; }
    render();
  }));
}

function buildFilters() {
  const wrap = document.querySelector('#provBtns');
  const counts = {};
  DATA.forEach(r => { if (showDeprecated || !isDeprecated(r)) counts[r.provider] = (counts[r.provider] || 0) + 1; });
  const visibleTotal = Object.values(counts).reduce((a, b) => a + b, 0);
  const provs = [...new Set(DATA.map(r => r.provider))].sort();
  wrap.innerHTML = '<button class="prov-btn' + (provFilter === '' ? ' active' : '') + '" data-p="">Todos (' + visibleTotal + ')</button>'
    + provs.map(p => {
      const label = (DATA.find(r => r.provider === p) || {}).providerName || p;
      return '<button class="prov-btn' + (provFilter === p ? ' active' : '') + '" data-p="' + p + '">'
        + logoHtml(p) + escapeHtml(label) + ' (' + (counts[p] || 0) + ')</button>';
    }).join('');
  wrap.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
    provFilter = b.dataset.p;
    buildFilters();
    render();
  }));
  const sel = document.querySelector('#typeFilter');
  const cur = sel.value;
  const types = [...new Set(DATA.filter(r => (showDeprecated || !isDeprecated(r)) && (!provFilter || r.provider === provFilter)).map(rowType))].sort();
  sel.innerHTML = '<option value="">Todos los tipos</option>' + types.map(t => '<option' + (t === cur ? ' selected' : '') + '>' + t + '</option>').join('');
  if (!types.includes(cur)) { sel.value = ''; typeFilter = ''; }
}

function renderErrors() {
  const banner = document.querySelector('#errorBanner');
  if (!ERRORS.length) { banner.style.display = 'none'; return; }
  banner.style.display = '';
  banner.innerHTML = '<strong>No se pudieron recuperar datos de:</strong> '
    + ERRORS.map(e => escapeHtml(e.provider) + ' <span class="muted">(' + escapeHtml(e.message || 'error') + ')</span>').join(' · ');
}

async function loadLogos() {
  const provs = [...new Set(DATA.map(r => r.provider))];
  await Promise.all(provs.map(async p => {
    try {
      const r = await fetch('assets/logos/' + p + '.svg');
      if (!r.ok) return;
      let svg = await r.text();
      svg = svg.replace(/<svg([^>]*?)width="[^"]*"/, '<svg$1width="16"')
               .replace(/<svg([^>]*?)height="[^"]*"/, '<svg$1height="16"');
      LOGOS[p] = svg;
    } catch (e) { /* sin logo: se muestra solo el nombre */ }
  }));
}

async function init() {
  const [all, errs] = await Promise.all([
    fetch('data/all.json').then(r => r.json()),
    fetch('data/errors.json').then(r => r.json()).catch(() => []),
  ]);
  DATA = all.rows || [];
  META = all;
  ERRORS = errs || [];
  document.querySelector('#updated').textContent = 'Actualizado: ' + (META.generatedAt || '').slice(0, 16).replace('T', ' ');
  await loadLogos();

  document.querySelector('#filter').addEventListener('input', e => { filterText = e.target.value.trim().toLowerCase(); render(); });
  document.querySelector('#typeFilter').addEventListener('change', e => { typeFilter = e.target.value; render(); });
  document.querySelector('#planFilter').addEventListener('change', e => { planFilter = e.target.value; render(); });
  document.querySelector('#budgetSwitch').addEventListener('change', e => { budgetFilter = e.target.checked; render(); });
  document.querySelector('#depSwitch').addEventListener('change', e => {
    showDeprecated = e.target.checked;
    buildFilters();
    render();
  });
  document.querySelector('#unitSwitch').addEventListener('change', e => {
    unit = e.target.checked ? 'credits' : 'usd';
    document.querySelector('#priceGroupLabel').textContent = unit === 'usd' ? 'Precio ($/M)' : 'Precio (créditos/M)';
    render();
  });
  document.querySelector('#estSwitch').addEventListener('change', e => {
    showEst = e.target.checked;
    document.querySelector('#estBar').style.display = showEst ? '' : 'none';
    buildHead();
    render();
  });
  document.querySelector('#estIn').addEventListener('input', e => { estIn = parseFloat(e.target.value) || 0; render(); });
  document.querySelector('#estOut').addEventListener('input', e => { estOut = parseFloat(e.target.value) || 0; render(); });
  document.querySelector('#estCache').addEventListener('input', e => { estCacheHit = (parseFloat(e.target.value) || 0) / 100; render(); });
  document.querySelector('#estBudget').addEventListener('input', e => { estBudget = parseFloat(e.target.value) || 0; render(); });
  document.querySelector('#tbl tbody').addEventListener('click', e => {
    const tr = e.target.closest('tr[data-key]');
    if (!tr) return;
    const det = tr.nextElementSibling;
    if (det && det.classList.contains('detail-row')) {
      det.style.display = det.style.display === 'none' ? '' : 'none';
    }
  });
  buildHead();
  buildFilters();
  renderErrors();
  render();
}

document.addEventListener('DOMContentLoaded', init);
