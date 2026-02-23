"""
Perp Dashboard — Bybit V5 Linear Perpetuals
Architecture: Streamlit shell + pure browser-side JS fetch
  - Python/Streamlit only renders the HTML container
  - ALL Bybit API calls happen in the user's browser via fetch()
  - This bypasses Streamlit Cloud's IP block on Bybit/Binance servers
"""
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="Perp Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Hide Streamlit chrome for a clean full-page feel
st.markdown("""
<style>
    #MainMenu, footer, header { visibility: hidden; }
    .block-container { padding: 0 !important; max-width: 100% !important; }
    iframe { border: none !important; }
</style>
""", unsafe_allow_html=True)

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Perp Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Syne:wght@400;600;800&display=swap');

  :root {
    --bg:       #0a0c10;
    --surface:  #111318;
    --border:   #1e2229;
    --muted:    #3a3f4a;
    --text:     #c8cdd8;
    --bright:   #eef0f5;
    --green:    #00c896;
    --red:      #ff4d6a;
    --yellow:   #f5c542;
    --accent:   #5b8af5;
    --mono:     'JetBrains Mono', monospace;
    --display:  'Syne', sans-serif;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  html, body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--mono);
    font-size: 13px;
    height: 100%;
    overflow-x: hidden;
  }

  /* ── Layout ── */
  #app { display: flex; flex-direction: column; height: 100vh; }

  /* ── Top bar ── */
  #topbar {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 10px 20px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }
  #topbar h1 {
    font-family: var(--display);
    font-size: 18px;
    font-weight: 800;
    color: var(--bright);
    white-space: nowrap;
    letter-spacing: -0.5px;
  }
  #topbar h1 span { color: var(--accent); }

  .ctrl-group {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--muted);
  }
  .ctrl-group label { white-space: nowrap; }

  select, input[type=text], input[type=number] {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: var(--mono);
    font-size: 11px;
    padding: 4px 8px;
    border-radius: 4px;
    outline: none;
    transition: border-color .15s;
  }
  select:hover, input:hover,
  select:focus, input:focus { border-color: var(--accent); }

  #search { width: 120px; }
  #topN   { width: 60px; }

  #status-pill {
    margin-left: auto;
    font-size: 10px;
    padding: 3px 10px;
    border-radius: 20px;
    border: 1px solid var(--border);
    color: var(--muted);
    white-space: nowrap;
  }
  #status-pill.ok  { border-color: var(--green); color: var(--green); }
  #status-pill.err { border-color: var(--red);   color: var(--red);   }
  #status-pill.loading { border-color: var(--yellow); color: var(--yellow); animation: pulse 1s infinite; }

  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

  /* ── Progress bar ── */
  #progress-wrap {
    height: 2px;
    background: var(--border);
    overflow: hidden;
  }
  #progress-bar {
    height: 100%;
    width: 0%;
    background: linear-gradient(90deg, var(--accent), var(--green));
    transition: width .3s ease;
  }

  /* ── Table container ── */
  #table-wrap {
    flex: 1;
    overflow: auto;
    padding: 0 8px 8px;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
  }

  thead {
    position: sticky;
    top: 0;
    z-index: 10;
    background: var(--surface);
  }

  th {
    padding: 8px 6px;
    text-align: right;
    font-size: 10px;
    font-weight: 600;
    color: var(--muted);
    letter-spacing: .5px;
    text-transform: uppercase;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    cursor: pointer;
    user-select: none;
    transition: color .15s;
  }
  th:hover { color: var(--bright); }
  th.sort-asc::after  { content: ' ▲'; color: var(--accent); }
  th.sort-desc::after { content: ' ▼'; color: var(--accent); }
  th:first-child { text-align: left; }

  /* Column groups header */
  .group-th {
    text-align: center !important;
    border-left: 1px solid var(--border);
    border-right: 1px solid var(--border);
    color: var(--accent) !important;
    font-size: 9px !important;
    letter-spacing: 1px;
    cursor: default !important;
    padding: 4px 0 !important;
  }
  .group-th:hover { color: var(--accent) !important; }

  td {
    padding: 5px 6px;
    text-align: right;
    border-bottom: 1px solid #13161b;
    font-size: 11px;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
    transition: background .1s;
  }
  td:first-child { text-align: left; font-size: 12px; font-weight: 600; color: var(--bright); }

  tr:hover td { background: #14171e; }

  .pos  { color: var(--green); }
  .neg  { color: var(--red); }
  .zero { color: var(--muted); }
  .na   { color: #2a2e38; }

  /* Intensity background tint */
  .hi3 { background: rgba(0,200,150,.12) !important; }
  .hi2 { background: rgba(0,200,150,.06) !important; }
  .lo3 { background: rgba(255,77,106,.12) !important; }
  .lo2 { background: rgba(255,77,106,.06) !important; }

  /* Symbol badge */
  .sym-base { color: var(--bright); }
  .sym-quote { color: var(--muted); font-size: 10px; }

  /* ── Footer ── */
  #footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 5px 20px;
    background: var(--surface);
    border-top: 1px solid var(--border);
    font-size: 10px;
    color: var(--muted);
    flex-wrap: wrap;
    gap: 8px;
  }
  #footer a { color: var(--accent); text-decoration: none; }
  #footer a:hover { text-decoration: underline; }
  #next-refresh { color: var(--text); font-weight: 600; }

  /* ── Error banner ── */
  #error-banner {
    display: none;
    background: rgba(255,77,106,.1);
    border: 1px solid var(--red);
    color: var(--red);
    padding: 10px 20px;
    font-size: 12px;
    margin: 8px;
    border-radius: 6px;
  }

  /* ── Skeleton rows ── */
  .skel td { background: var(--surface) !important; }
  .skel td span {
    display: inline-block;
    width: 50px;
    height: 10px;
    border-radius: 3px;
    background: linear-gradient(90deg, var(--border) 25%, #1e2535 50%, var(--border) 75%);
    background-size: 200% 100%;
    animation: shimmer 1.2s infinite;
  }
  .skel td:first-child span { width: 90px; }
  @keyframes shimmer { 0%{background-position:200%} 100%{background-position:-200%} }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--muted); border-radius: 3px; }
</style>
</head>
<body>
<div id="app">

  <!-- Top bar -->
  <div id="topbar">
    <h1>📊 <span>Perp</span>Dashboard</h1>

    <div class="ctrl-group">
      <label>Top</label>
      <input type="number" id="topN" value="50" min="5" max="200" step="5"/>
      <label>symbols</label>
    </div>

    <div class="ctrl-group">
      <label>Refresh</label>
      <select id="refreshSel">
        <option value="10">10s</option>
        <option value="20" selected>20s</option>
        <option value="30">30s</option>
        <option value="60">60s</option>
        <option value="120">120s</option>
      </select>
    </div>

    <div class="ctrl-group">
      <label>Sort</label>
      <select id="sortSel">
        <option value="vol">Volume</option>
        <option value="price_5m">P% 5m</option>
        <option value="price_15m">P% 15m</option>
        <option value="price_1h">P% 1h</option>
        <option value="price_2h">P% 2h</option>
        <option value="price_4h">P% 4h</option>
        <option value="price_1d">P% 1d</option>
        <option value="oi_5m">OI% 5m</option>
        <option value="oi_15m">OI% 15m</option>
        <option value="oi_1h">OI% 1h</option>
        <option value="oi_2h">OI% 2h</option>
        <option value="oi_4h">OI% 4h</option>
        <option value="oi_1d">OI% 1d</option>
        <option value="fr_last">FR Last</option>
        <option value="fr_delta">FR Δ</option>
      </select>
      <select id="sortDir">
        <option value="desc">▼ Desc</option>
        <option value="asc">▲ Asc</option>
      </select>
    </div>

    <div class="ctrl-group">
      <input type="text" id="search" placeholder="🔍 Search…"/>
    </div>

    <div id="status-pill" class="loading">Connecting…</div>
  </div>

  <!-- Progress -->
  <div id="progress-wrap"><div id="progress-bar"></div></div>

  <!-- Error -->
  <div id="error-banner"></div>

  <!-- Table -->
  <div id="table-wrap">
    <table id="main-table">
      <thead id="thead"></thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>

  <!-- Footer -->
  <div id="footer">
    <span>Bybit V5 API · USDT Linear Perpetuals · All requests from your browser</span>
    <span>Next refresh: <span id="next-refresh">—</span></span>
    <span id="last-updated">—</span>
  </div>

</div>

<script>
// ═══════════════════════════════════════════════
// CONFIG
// ═══════════════════════════════════════════════
const BYBIT = 'https://api.bybit.com';
const CAT   = 'linear';

const TIMEFRAMES = ['5m','15m','1h','2h','4h','1d'];

// Kline interval param (Bybit uses minutes or "D")
const TF_KLINE = { '5m':'5','15m':'15','1h':'60','2h':'120','4h':'240','1d':'D' };

// OI intervalTime — no native 2h; we derive it from 1h
const TF_OI = { '5m':'5min','15m':'15min','1h':'1h','2h':null,'4h':'4h','1d':'1d' };

const CONCURRENCY = 8;   // max parallel symbol fetches
const OI_LIMIT    = 4;   // need index 0 and 2 for 2h derivation

// ═══════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════
let rows       = [];      // [{symbol, vol, price_5m, …, oi_5m, …, fr_last, fr_delta}]
let sortCol    = 'vol';
let sortDir    = 'desc';
let searchQ    = '';
let refreshSec = 20;
let topN       = 50;
let refreshTimer, countdownTimer, countdownVal;

// ═══════════════════════════════════════════════
// FETCH HELPERS
// ═══════════════════════════════════════════════
async function apiFetch(path, params={}) {
  const url = new URL(BYBIT + path);
  Object.entries(params).forEach(([k,v]) => url.searchParams.set(k,v));
  const r = await fetch(url.toString(), { signal: AbortSignal.timeout(12000) });
  if (!r.ok) throw new Error(`HTTP ${r.status} ${path}`);
  const j = await r.json();
  if (j.retCode !== 0) throw new Error(`Bybit ${j.retCode}: ${j.retMsg} (${path})`);
  return j.result;
}

function pct(newV, oldV) {
  const n = parseFloat(newV), o = parseFloat(oldV);
  if (!isFinite(n) || !isFinite(o) || o === 0) return null;
  return (n - o) / Math.abs(o) * 100;
}

// ═══════════════════════════════════════════════
// TOP-N SYMBOLS BY VOLUME
// ═══════════════════════════════════════════════
async function fetchTopSymbols(n) {
  const res = await apiFetch('/v5/market/tickers', { category: CAT });
  const list = (res.list || [])
    .filter(t => t.symbol.endsWith('USDT') && !t.symbol.includes('-'))
    .sort((a,b) => parseFloat(b.turnover24h||0) - parseFloat(a.turnover24h||0));
  return list.slice(0, n).map(t => ({ symbol: t.symbol, vol: parseFloat(t.turnover24h||0) }));
}

// ═══════════════════════════════════════════════
// PER-SYMBOL FETCH  (all TFs in parallel)
// ═══════════════════════════════════════════════
async function fetchSymbol(symbol) {
  const row = { symbol };

  // -- Build all requests concurrently --
  const klinePromises = TIMEFRAMES.map(tf =>
    apiFetch('/v5/market/kline', {
      category: CAT, symbol, interval: TF_KLINE[tf], limit: 3
    }).catch(() => null)
  );

  const oiPromises = TIMEFRAMES.map(tf => {
    if (tf === '2h') {
      return apiFetch('/v5/market/open-interest', {
        category: CAT, symbol, intervalTime: '1h', limit: OI_LIMIT
      }).catch(() => null);
    }
    return apiFetch('/v5/market/open-interest', {
      category: CAT, symbol, intervalTime: TF_OI[tf], limit: 3
    }).catch(() => null);
  });

  const frPromise = apiFetch('/v5/market/funding/history', {
    category: CAT, symbol, limit: 2
  }).catch(() => null);

  const [klineRes, oiRes, frRes] = await Promise.all([
    Promise.all(klinePromises),
    Promise.all(oiPromises),
    frPromise,
  ]);

  // -- Parse kline (price %) --
  // Bybit kline list[0] = newest, list[1] = previous
  // Each entry: [startTime, open, high, low, close, volume, turnover]
  TIMEFRAMES.forEach((tf, i) => {
    try {
      const list = klineRes[i]?.list;
      row[`price_${tf}`] = (list && list.length >= 2)
        ? pct(list[0][4], list[1][4])
        : null;
    } catch { row[`price_${tf}`] = null; }
  });

  // -- Parse OI % --
  TIMEFRAMES.forEach((tf, i) => {
    try {
      const list = oiRes[i]?.list;
      if (!list || list.length < 2) { row[`oi_${tf}`] = null; return; }
      if (tf === '2h') {
        // derive: index 0 (now) vs index 2 (2h ago)
        row[`oi_${tf}`] = list.length >= 3
          ? pct(list[0].openInterest, list[2].openInterest)
          : null;
      } else {
        row[`oi_${tf}`] = pct(list[0].openInterest, list[1].openInterest);
      }
    } catch { row[`oi_${tf}`] = null; }
  });

  // -- Parse Funding Rate --
  try {
    const list = frRes?.list;
    if (list && list.length >= 1) {
      const last = parseFloat(list[0].fundingRate) * 100;
      row.fr_last  = last;
      row.fr_delta = list.length >= 2
        ? last - parseFloat(list[1].fundingRate) * 100
        : null;
    } else {
      row.fr_last = row.fr_delta = null;
    }
  } catch { row.fr_last = row.fr_delta = null; }

  return row;
}

// ═══════════════════════════════════════════════
// CONCURRENT BATCH  (semaphore pattern)
// ═══════════════════════════════════════════════
async function batchFetch(symbols) {
  const results = new Array(symbols.length).fill(null);
  let idx = 0, done = 0;
  const total = symbols.length;
  setProgress(0);

  async function worker() {
    while (true) {
      const i = idx++;
      if (i >= total) break;
      try {
        results[i] = await fetchSymbol(symbols[i].symbol);
        if (results[i]) results[i].vol = symbols[i].vol;
      } catch(e) {
        results[i] = { symbol: symbols[i].symbol, vol: symbols[i].vol };
      }
      done++;
      setProgress(Math.round(done / total * 100));
    }
  }

  await Promise.all(Array.from({ length: CONCURRENCY }, worker));
  setProgress(100);
  return results.filter(Boolean);
}

// ═══════════════════════════════════════════════
// MAIN REFRESH CYCLE
// ═══════════════════════════════════════════════
async function refresh() {
  setStatus('loading', 'Fetching…');
  hideError();

  try {
    const topSymbols = await fetchTopSymbols(topN);
    rows = await batchFetch(topSymbols);
    renderTable();
    const now = new Date();
    document.getElementById('last-updated').textContent =
      `Updated ${now.toLocaleTimeString()}`;
    setStatus('ok', `✓ ${rows.length} symbols`);
  } catch(e) {
    showError(e.message);
    setStatus('err', '✗ Error');
  }

  scheduleRefresh();
}

function scheduleRefresh() {
  clearInterval(refreshTimer);
  clearInterval(countdownTimer);
  countdownVal = refreshSec;
  updateCountdown();
  countdownTimer = setInterval(() => {
    countdownVal--;
    updateCountdown();
    if (countdownVal <= 0) {
      clearInterval(countdownTimer);
      refresh();
    }
  }, 1000);
}

function updateCountdown() {
  document.getElementById('next-refresh').textContent = `${countdownVal}s`;
}

// ═══════════════════════════════════════════════
// TABLE RENDERING
// ═══════════════════════════════════════════════
function buildHeader() {
  const thead = document.getElementById('thead');
  thead.innerHTML = '';

  // Group row
  const gr = document.createElement('tr');
  gr.innerHTML = `
    <th rowspan="2" style="width:110px">Symbol</th>
    <th colspan="6" class="group-th" style="border-left:1px solid var(--border)">PRICE %</th>
    <th colspan="6" class="group-th" style="border-left:1px solid var(--border)">OPEN INTEREST %</th>
    <th colspan="2" class="group-th" style="border-left:1px solid var(--border)">FUNDING</th>
  `;
  thead.appendChild(gr);

  // Sub-header row
  const sr = document.createElement('tr');
  const tfCols = TIMEFRAMES.map(tf =>
    `<th data-col="price_${tf}" style="width:72px">${tf}</th>`
  ).join('');
  const oiCols = TIMEFRAMES.map(tf =>
    `<th data-col="oi_${tf}" style="width:72px">${tf}${tf==='2h'?'*':''}</th>`
  ).join('');
  sr.innerHTML =
    tfCols +
    `<th data-col="oi_5m" style="width:72px;border-left:1px solid var(--border)">` +
    oiCols.replace('<th data-col="oi_5m"', '<th data-col="oi_5m"') +
    `<th data-col="fr_last" style="width:80px;border-left:1px solid var(--border)">Last</th>` +
    `<th data-col="fr_delta" style="width:72px">Δ</th>`;

  // Rebuild cleanly
  sr.innerHTML = '';
  TIMEFRAMES.forEach(tf => {
    const th = document.createElement('th');
    th.dataset.col = `price_${tf}`;
    th.style.width = '72px';
    if (tf === TIMEFRAMES[0]) th.style.borderLeft = '1px solid var(--border)';
    th.textContent = tf;
    sr.appendChild(th);
  });
  TIMEFRAMES.forEach(tf => {
    const th = document.createElement('th');
    th.dataset.col = `oi_${tf}`;
    th.style.width = '72px';
    if (tf === TIMEFRAMES[0]) th.style.borderLeft = '1px solid var(--border)';
    th.textContent = tf === '2h' ? '2h*' : tf;
    sr.appendChild(th);
  });
  ['fr_last','fr_delta'].forEach((col,i) => {
    const th = document.createElement('th');
    th.dataset.col = col;
    th.style.width = i === 0 ? '80px' : '72px';
    if (i === 0) th.style.borderLeft = '1px solid var(--border)';
    th.textContent = i === 0 ? 'Last' : 'Δ';
    sr.appendChild(th);
  });

  thead.appendChild(sr);
  highlightSortCol();
  thead.querySelectorAll('[data-col]').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (sortCol === col) sortDir = sortDir === 'desc' ? 'asc' : 'desc';
      else { sortCol = col; sortDir = 'desc'; }
      document.getElementById('sortSel').value = sortCol;
      document.getElementById('sortDir').value = sortDir;
      renderTable();
    });
  });
}

function highlightSortCol() {
  document.querySelectorAll('th[data-col]').forEach(th => {
    th.classList.remove('sort-asc','sort-desc');
    if (th.dataset.col === sortCol) {
      th.classList.add(sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
    }
  });
}

function fmtPct(v) {
  if (v === null || v === undefined || !isFinite(v)) return null;
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}

function pctCell(v, extraStyle='') {
  const txt = fmtPct(v);
  if (txt === null) return `<td class="na">—</td>`;
  const cls = v > 2 ? 'pos hi3' : v > 0 ? 'pos hi2' : v < -2 ? 'neg lo3' : v < 0 ? 'neg lo2' : 'zero';
  return `<td class="${cls}" style="${extraStyle}">${txt}</td>`;
}

function frCell(v, extraStyle='') {
  const txt = fmtPct(v);
  if (txt === null) return `<td class="na" style="${extraStyle}">—</td>`;
  const cls = v > 0.01 ? 'pos' : v < -0.01 ? 'neg' : 'zero';
  return `<td class="${cls}" style="${extraStyle}">${txt}</td>`;
}

function renderTable() {
  const q = searchQ.toLowerCase();
  let data = rows.filter(r => r.symbol.toLowerCase().includes(q));

  // Sort
  data.sort((a, b) => {
    let av = a[sortCol], bv = b[sortCol];
    av = (av === null || av === undefined) ? (sortDir==='desc' ? -Infinity : Infinity) : av;
    bv = (bv === null || bv === undefined) ? (sortDir==='desc' ? -Infinity : Infinity) : bv;
    return sortDir === 'desc' ? bv - av : av - bv;
  });

  const tbody = document.getElementById('tbody');
  if (data.length === 0) {
    tbody.innerHTML = `<tr><td colspan="15" style="text-align:center;padding:40px;color:var(--muted)">No symbols found</td></tr>`;
    return;
  }

  const borderL = 'border-left:1px solid var(--border)';

  tbody.innerHTML = data.map(r => {
    const base  = r.symbol.replace(/USDT$/, '');
    const sym   = `<span class="sym-base">${base}</span><span class="sym-quote">USDT</span>`;

    const priceCells = TIMEFRAMES.map((tf,i) =>
      pctCell(r[`price_${tf}`], i===0 ? borderL : '')
    ).join('');

    const oiCells = TIMEFRAMES.map((tf,i) =>
      pctCell(r[`oi_${tf}`], i===0 ? borderL : '')
    ).join('');

    return `<tr>
      <td>${sym}</td>
      ${priceCells}
      ${oiCells}
      ${frCell(r.fr_last, borderL)}
      ${frCell(r.fr_delta)}
    </tr>`;
  }).join('');

  highlightSortCol();
}

// ═══════════════════════════════════════════════
// SKELETON LOADER
// ═══════════════════════════════════════════════
function showSkeleton(n=20) {
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = Array.from({length:n}, () =>
    `<tr class="skel">${Array.from({length:15}, () => '<td><span></span></td>').join('')}</tr>`
  ).join('');
}

// ═══════════════════════════════════════════════
// UI HELPERS
// ═══════════════════════════════════════════════
function setStatus(type, msg) {
  const el = document.getElementById('status-pill');
  el.className = type;
  el.textContent = msg;
}
function setProgress(pct) {
  document.getElementById('progress-bar').style.width = pct + '%';
}
function showError(msg) {
  const el = document.getElementById('error-banner');
  el.style.display = 'block';
  el.textContent = '⚠ ' + msg;
}
function hideError() {
  document.getElementById('error-banner').style.display = 'none';
}

// ═══════════════════════════════════════════════
// CONTROLS
// ═══════════════════════════════════════════════
document.getElementById('topN').addEventListener('change', e => {
  topN = Math.max(5, Math.min(200, parseInt(e.target.value)||50));
  clearInterval(countdownTimer);
  refresh();
});
document.getElementById('refreshSel').addEventListener('change', e => {
  refreshSec = parseInt(e.target.value);
  scheduleRefresh();
});
document.getElementById('sortSel').addEventListener('change', e => {
  sortCol = e.target.value;
  renderTable();
});
document.getElementById('sortDir').addEventListener('change', e => {
  sortDir = e.target.value;
  renderTable();
});
document.getElementById('search').addEventListener('input', e => {
  searchQ = e.target.value;
  renderTable();
});

// ═══════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════
buildHeader();
showSkeleton(25);
refresh();
</script>
</body>
</html>
"""

components.html(DASHBOARD_HTML, height=820, scrolling=False)
