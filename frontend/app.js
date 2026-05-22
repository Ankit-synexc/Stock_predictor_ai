/* ══════════════════════════════════════════════════════════
   StockSense — app.js
   Chart tab: loads CSV → renders candlestick + volume + overlays
   Predict / Live / History tabs: calls FastAPI backend
   ══════════════════════════════════════════════════════════ */

'use strict';

// ─── Config ──────────────────────────────────────────────────────────────────
const API_BASE = 'http://127.0.0.1:8000';
// Path to default CSV (served from the same origin or use a relative fetch)
// When opened via file://, we load via <input> upload. When served via HTTP we try to fetch.
const DEFAULT_CSV_PATH = '../Data/AAPL_indicators_1980-12-12_to_2026-05-18.csv';

// ─── State ───────────────────────────────────────────────────────────────────
let allRows      = [];          // all parsed CSV rows
let filteredRows = [];          // rows in current period view
let candleSeries = null;
let volumeSeries = null;
let smaSeries    = null;
let emaSeries    = null;
let bbUpperSeries = null;
let bbLowerSeries = null;
let mainChart    = null;
let volChart     = null;
let currentPeriod = 'ALL';

// ─── DOM refs ─────────────────────────────────────────────────────────────────
const D = id => document.getElementById(id);

// ══════════════════ NAVIGATION ══════════════════════════════════════════════
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    const tab = item.dataset.tab;
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    item.classList.add('active');
    D(`tab-${tab}`).classList.add('active');

    const titles = {
      chart:   ['Candlestick Chart',       'AAPL · 1980–2026 Historical Data'],
      predict: ['Manual OHLCV Prediction', 'POST /api/v1/predict'],
      live:    ['Live Prediction',          'GET /api/v1/predict/live'],
      history: ['Prediction History',       'GET /api/v1/history'],
      trading: ['Paper Trading Agent',      'POST /api/v1/trading/start'],
    };
    D('pageTitle').textContent = titles[tab][0];
    D('pageSub').textContent   = titles[tab][1];

    if (tab === 'chart') setTimeout(() => { if (mainChart) mainChart.timeScale().fitContent(); }, 50);
  });
});

// ══════════════════ CLOCK ════════════════════════════════════════════════════
function updateClock() {
  const now = new Date();
  D('clock').textContent = now.toLocaleTimeString('en-IN', { hour12: false, timeZone: 'Asia/Kolkata' }) + ' IST';
}
setInterval(updateClock, 1000);
updateClock();

// ══════════════════ API STATUS CHECK ═════════════════════════════════════════
async function checkAPIStatus() {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(4000) });
    if (res.ok) {
      D('statusDot').className = 'status-dot online';
      D('statusText').textContent = 'API Online';
    } else throw new Error();
  } catch {
    D('statusDot').className = 'status-dot offline';
    D('statusText').textContent = 'API Offline';
  }
}
checkAPIStatus();
setInterval(checkAPIStatus, 30000);

// ══════════════════ TOAST ════════════════════════════════════════════════════
function toast(msg, type = 'info', duration = 3500) {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  const icons = { success: '✅', error: '❌', info: 'ℹ️', warn: '⚠️' };
  el.innerHTML = `<span>${icons[type] || '•'}</span><span>${msg}</span>`;
  D('toastContainer').appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ══════════════════ CSV PARSING & LOADING ════════════════════════════════════
function parseCSV(csvText, filename) {
  const result = Papa.parse(csvText, { header: true, skipEmptyLines: true, dynamicTyping: true });
  if (result.errors.length) console.warn('CSV parse warnings:', result.errors.slice(0, 3));

  // Filter rows with valid OHLCV and dates
  allRows = result.data.filter(r =>
    r.Date && r.Open > 0 && r.High > 0 && r.Low > 0 && r.Close > 0
  ).map(r => ({
    date:   r.Date,
    open:   +r.Open,
    high:   +r.High,
    low:    +r.Low,
    close:  +r.Close,
    volume: +r.Volume || 0,
    sma20:  r.SMA_20   != null ? +r.SMA_20   : null,
    ema20:  r.EMA_20   != null ? +r.EMA_20   : null,
    bbUp:   r.BB_Upper != null ? +r.BB_Upper : null,
    bbLow:  r.BB_Lower != null ? +r.BB_Lower : null,
    rsi:    r.RSI      != null ? +r.RSI      : null,
    macd:   r.MACD     != null ? +r.MACD     : null,
  })).sort((a, b) => a.date.localeCompare(b.date));

  D('csvFileName').textContent = filename || DEFAULT_CSV_PATH.split('/').pop();
  D('csvRows').textContent = `${allRows.length.toLocaleString()} rows`;
  toast(`Loaded ${allRows.length.toLocaleString()} candles`, 'success');

  // detect ticker
  const ticker = result.data[0]?.Ticker || 'AAPL';
  D('tickerBadge').textContent = ticker;

  applyPeriod(currentPeriod);
}

function tryFetchDefaultCSV() {
  // Only works when served over HTTP
  if (location.protocol === 'file:') {
    // Show instructions; user must upload
    toast('Open via HTTP server, or use "Upload Different CSV" to load data', 'warn', 6000);
    D('chartLoader').classList.remove('hidden');
    return;
  }
  fetch(DEFAULT_CSV_PATH)
    .then(r => { if (!r.ok) throw new Error(r.status); return r.text(); })
    .then(text => parseCSV(text, null))
    .catch(() => toast('Could not auto-load CSV — use Upload button', 'warn', 5000));
}

// File upload
D('csvUpload').addEventListener('change', e => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => parseCSV(ev.target.result, file.name);
  reader.readAsText(file);
});

// ══════════════════ PERIOD FILTERING ═════════════════════════════════════════
const PERIOD_DAYS = {
  '1D': 1, '2D': 2, '1W': 7, '1M': 30,
  '3M': 90, '6M': 182, '1Y': 365, '5Y': 1825, 'ALL': Infinity
};

function applyPeriod(period) {
  currentPeriod = period;
  if (!allRows.length) return;

  if (period === 'ALL') {
    filteredRows = allRows;
  } else {
    const days = PERIOD_DAYS[period];
    const lastDate = new Date(allRows[allRows.length - 1].date);
    const cutoff = new Date(lastDate);
    cutoff.setDate(cutoff.getDate() - days);
    const cutoffStr = cutoff.toISOString().split('T')[0];
    filteredRows = allRows.filter(r => r.date >= cutoffStr);
    // For short periods (1D/2D) that may have 0-1 rows, clamp to last few
    if (filteredRows.length < 2 && (period === '1D' || period === '2D')) {
      filteredRows = allRows.slice(-Math.max(2, PERIOD_DAYS[period] * 2));
    }
  }

  renderChart();
}

document.querySelectorAll('.period-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    applyPeriod(btn.dataset.period);
  });
});

// ══════════════════ CHART RENDERING ══════════════════════════════════════════
function dateToTimestamp(dateStr) {
  // lightweight-charts needs Unix seconds for "time"
  return Math.floor(new Date(dateStr + 'T00:00:00Z').getTime() / 1000);
}

function buildCharts() {
  const chartOpts = {
    layout:  { background: { color: 'transparent' }, textColor: '#8a9bb5' },
    grid:    { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: 'rgba(255,255,255,0.07)', scaleMargins: { top: 0.1, bottom: 0.05 } },
    timeScale: {
      borderColor: 'rgba(255,255,255,0.07)',
      timeVisible: true,
      rightOffset: 5,
      barSpacing: 3,           // tighter default spacing so more candles fit
      minBarSpacing: 0.5,       // allow zooming out very far
      fixLeftEdge: false,
      fixRightEdge: false,
    },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale:  { mouseWheel: true, axisPressedMouseMove: { time: true, price: true }, pinch: true },
  };

  // Main chart
  const mainEl = D('candleChart');
  mainEl.innerHTML = '';
  mainChart = LightweightCharts.createChart(mainEl, { ...chartOpts, width: mainEl.clientWidth, height: mainEl.clientHeight });

  candleSeries = mainChart.addCandlestickSeries({
    upColor:      '#3ecf8e',
    downColor:    '#f87171',
    borderUpColor:   '#3ecf8e',
    borderDownColor: '#f87171',
    wickUpColor:     '#3ecf8e',
    wickDownColor:   '#f87171',
  });

  smaSeries = mainChart.addLineSeries({ color: '#60a5fa', lineWidth: 1.5, priceLineVisible: false, title: 'SMA20' });
  emaSeries = mainChart.addLineSeries({ color: '#fbbf24', lineWidth: 1.5, priceLineVisible: false, title: 'EMA20', visible: false });
  bbUpperSeries = mainChart.addLineSeries({ color: 'rgba(168,85,247,0.6)', lineWidth: 1, priceLineVisible: false, title: 'BB↑', visible: false });
  bbLowerSeries = mainChart.addLineSeries({ color: 'rgba(168,85,247,0.6)', lineWidth: 1, priceLineVisible: false, title: 'BB↓', visible: false });

  // Volume chart
  const volEl = D('volumeChart');
  volEl.innerHTML = '';
  volChart = LightweightCharts.createChart(volEl, {
    ...chartOpts,
    width: volEl.clientWidth,
    height: volEl.clientHeight,
    rightPriceScale: { ...chartOpts.rightPriceScale, scaleMargins: { top: 0.1, bottom: 0 } },
    timeScale: { ...chartOpts.timeScale, visible: false },
  });

  volumeSeries = volChart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'volume',
  });
  volChart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.1, bottom: 0 } });

  // Sync time scales
  mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (range) volChart.timeScale().setVisibleLogicalRange(range);
  });
  volChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (range) mainChart.timeScale().setVisibleLogicalRange(range);
  });

  // Crosshair sync → update stat cards
  mainChart.subscribeCrosshairMove(param => {
    if (!param.time) return;
    const d = candleSeries.dataByIndex(param.logical);
    if (!d) return;
    const row = allRows.find(r => dateToTimestamp(r.date) === param.time);
    if (!row) return;
    updateStatCards(row);
  });

  // Resize observer
  const ro = new ResizeObserver(() => {
    mainChart.applyOptions({ width: mainEl.clientWidth });
    volChart.applyOptions({ width: volEl.clientWidth });
  });
  ro.observe(mainEl);
  ro.observe(volEl);
}

function renderChart() {
  if (!mainChart) buildCharts();

  const candles = filteredRows.map(r => ({
    time:  dateToTimestamp(r.date),
    open:  r.open,
    high:  r.high,
    low:   r.low,
    close: r.close,
  }));

  const vols = filteredRows.map(r => ({
    time:  dateToTimestamp(r.date),
    value: r.volume,
    color: r.close >= r.open ? 'rgba(62,207,142,0.45)' : 'rgba(248,113,113,0.45)',
  }));

  const smaData  = filteredRows.filter(r => r.sma20 != null).map(r => ({ time: dateToTimestamp(r.date), value: r.sma20 }));
  const emaData  = filteredRows.filter(r => r.ema20 != null).map(r => ({ time: dateToTimestamp(r.date), value: r.ema20 }));
  const bbUpData = filteredRows.filter(r => r.bbUp != null).map(r => ({ time: dateToTimestamp(r.date), value: r.bbUp }));
  const bbLwData = filteredRows.filter(r => r.bbLow != null).map(r => ({ time: dateToTimestamp(r.date), value: r.bbLow }));

  candleSeries.setData(candles);
  volumeSeries.setData(vols);
  smaSeries.setData(smaData);
  emaSeries.setData(emaData);
  bbUpperSeries.setData(bbUpData);
  bbLowerSeries.setData(bbLwData);

  mainChart.timeScale().fitContent();

  // Update stat cards with last row
  const last = filteredRows[filteredRows.length - 1];
  if (last) updateStatCards(last);

  // Hide loader
  D('chartLoader').classList.add('hidden');
}

function updateStatCards(row) {
  const fmt = v => v != null ? v.toFixed(2) : '—';
  const fmtVol = v => {
    if (!v) return '—';
    if (v >= 1e9) return (v/1e9).toFixed(2) + 'B';
    if (v >= 1e6) return (v/1e6).toFixed(2) + 'M';
    if (v >= 1e3) return (v/1e3).toFixed(0) + 'K';
    return v.toString();
  };
  D('valClose').textContent = fmt(row.close);
  D('valOpen').textContent  = fmt(row.open);
  D('valHigh').textContent  = fmt(row.high);
  D('valLow').textContent   = fmt(row.low);
  D('valVol').textContent   = fmtVol(row.volume);
  D('valRSI').textContent   = row.rsi != null ? row.rsi.toFixed(1) : '—';
  D('valMACD').textContent  = row.macd != null ? row.macd.toFixed(4) : '—';

  // RSI color
  const rsiEl = D('valRSI');
  if (row.rsi > 70)      { rsiEl.style.color = '#f87171'; }
  else if (row.rsi < 30) { rsiEl.style.color = '#3ecf8e'; }
  else                   { rsiEl.style.color = ''; }
}

// ─── Overlay toggle ───────────────────────────────────────────────────────────
D('chk-sma').addEventListener('change', e => { if (smaSeries) smaSeries.applyOptions({ visible: e.target.checked }); });
D('chk-ema').addEventListener('change', e => { if (emaSeries) emaSeries.applyOptions({ visible: e.target.checked }); });
D('chk-bb').addEventListener('change', e => {
  const v = e.target.checked;
  if (bbUpperSeries) bbUpperSeries.applyOptions({ visible: v });
  if (bbLowerSeries) bbLowerSeries.applyOptions({ visible: v });
});

// ══════════════════ PREDICT (Manual) ═════════════════════════════════════════
D('predictForm').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = D('predictBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-icon">⏳</span> Predicting…';

  const payload = {
    ticker: D('inp-ticker').value.trim().toUpperCase(),
    open:   parseFloat(D('inp-open').value),
    high:   parseFloat(D('inp-high').value),
    low:    parseFloat(D('inp-low').value),
    close:  parseFloat(D('inp-close').value),
    volume: parseFloat(D('inp-volume').value),
  };

  try {
    const res = await fetch(`${API_BASE}/api/v1/predict`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'API error');
    showPredictResult(data, 'predictResult', 'resultDirection', 'resultMeta', 'confidenceFill', 'confidencePct', 'resultRaw');
    toast('Prediction complete!', 'success');
  } catch (err) {
    toast(`Error: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">🔮</span> Run Prediction';
  }
});

// ══════════════════ LIVE PREDICT ═════════════════════════════════════════════
D('liveForm').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = D('liveBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-icon">⏳</span> Fetching…';

  const ticker   = D('live-ticker').value.trim().toUpperCase();
  const exchange = D('live-exchange').value;

  try {
    const res = await fetch(`${API_BASE}/api/v1/predict/live?ticker=${encodeURIComponent(ticker)}&exchange=${encodeURIComponent(exchange)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'API error');
    showPredictResult(data, 'liveResult', 'liveDirection', 'liveMeta', 'liveConfidenceFill', 'liveConfidencePct', 'liveRaw');
    toast('Live prediction ready!', 'success');
  } catch (err) {
    toast(`Error: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">⚡</span> Fetch & Predict Live';
  }
});

function showPredictResult(data, panelId, dirId, metaId, fillId, pctId, rawId) {
  const panel = D(panelId);
  panel.style.display = '';

  const dirEl = D(dirId);
  dirEl.textContent = data.direction === 'UP' ? '▲ UP' : '▼ DOWN';
  dirEl.className = 'result-direction ' + (data.direction === 'UP' ? 'up' : 'down');

  D(metaId).textContent = `Ticker: ${data.ticker}`;

  const conf = data.confidence != null ? data.confidence : 0.5;
  const pct  = (conf * 100).toFixed(1);
  const fill = D(fillId);
  fill.className = 'confidence-fill' + (data.direction === 'DOWN' ? ' red' : '');
  setTimeout(() => { fill.style.width = pct + '%'; }, 50);
  D(pctId).textContent = pct + '%';

  D(rawId).textContent = JSON.stringify(data, null, 2);
}

// ══════════════════ HISTORY ══════════════════════════════════════════════════
async function fetchHistory(ticker = '') {
  const url = ticker
    ? `${API_BASE}/api/v1/history?ticker=${encodeURIComponent(ticker)}`
    : `${API_BASE}/api/v1/history`;

  D('historyEmpty').style.display = '';
  D('historyTable').style.display = 'none';
  D('historyEmpty').querySelector('p').textContent = 'Loading…';

  try {
    const res  = await fetch(url);
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || 'API error');

    const data = json.data || [];
    if (!data.length) {
      D('historyEmpty').querySelector('p').textContent = 'No records found.';
      D('historyFooter').textContent = '';
      return;
    }

    const tbody = D('historyBody');
    tbody.innerHTML = '';
    data.forEach(r => {
      const tr = document.createElement('tr');
      const ohlcv = r.ohlcv || {};
      const ts = r.timestamp ? new Date(r.timestamp).toLocaleString('en-IN') : '—';
      const conf = r.confidence != null ? (r.confidence * 100).toFixed(1) + '%' : '—';
      tr.innerHTML = `
        <td>${r.ticker || '—'}</td>
        <td class="${r.direction === 'UP' ? 'badge-up' : 'badge-down'}">${r.direction === 'UP' ? '▲ UP' : '▼ DOWN'}</td>
        <td>${conf}</td>
        <td>${ohlcv.open?.toFixed(2)  ?? '—'}</td>
        <td>${ohlcv.high?.toFixed(2)  ?? '—'}</td>
        <td>${ohlcv.low?.toFixed(2)   ?? '—'}</td>
        <td>${ohlcv.close?.toFixed(2) ?? '—'}</td>
        <td>${ohlcv.volume?.toLocaleString() ?? '—'}</td>
        <td>${ts}</td>
      `;
      tbody.appendChild(tr);
    });

    D('historyEmpty').style.display = 'none';
    D('historyTable').style.display = '';
    D('historyFooter').textContent = `Showing ${data.length} record${data.length !== 1 ? 's' : ''} · Total: ${json.count}`;
    toast(`Loaded ${data.length} records`, 'success');
  } catch (err) {
    D('historyEmpty').querySelector('p').textContent = `Failed: ${err.message}`;
    toast(`History error: ${err.message}`, 'error');
  }
}

D('fetchHistoryBtn').addEventListener('click', () => {
  fetchHistory(D('historyFilter').value.trim().toUpperCase());
});
D('historyFilter').addEventListener('keydown', e => {
  if (e.key === 'Enter') fetchHistory(e.target.value.trim().toUpperCase());
});

// ══════════════════ INIT ═════════════════════════════════════════════════════
(function init() {
  buildCharts();
  tryFetchDefaultCSV();
})();



// ══════════════════════════════════════════════════════════════════════════════
// PAPER TRADING TAB (MULTI-AGENT DASHBOARD)
// ══════════════════════════════════════════════════════════════════════════════

let statusPollTimer = null;
const agentCards = {};

// ── Start agent ───────────────────────────────────────────────────────────────
D('tradeStartForm').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = D('tradeStartBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-icon">⏳</span> Launching…';

  const ticker   = D('trade-ticker').value.trim().toUpperCase();
  const capital  = parseFloat(D('trade-capital').value);
  const pct      = parseFloat(D('trade-pct').value) / 100;
  const interval = parseInt(D('trade-interval').value);
  const agentId  = `agent_${ticker}`;

  try {
    const res  = await fetch(`${API_BASE}/api/v1/trading/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker,
        starting_capital: capital,
        trade_pct: pct,
        interval_seconds: interval,
        agent_id: agentId,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'API error');

    toast(`Agent ${agentId} launched!`, 'success');
    loadAgentsList();
  } catch (err) {
    toast(`Launch error: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">🚀</span> Start Agent';
  }
});

// ── Stop All Agents ───────────────────────────────────────────────────────────
D('stopAllAgentsBtn').addEventListener('click', async () => {
  try {
    const res  = await fetch(`${API_BASE}/api/v1/trading/stop-all`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'API error');
    toast(data.message, 'success');
    loadAgentsList();
  } catch (err) {
    toast(`Stop All error: ${err.message}`, 'error');
  }
});

// ── Delete All Agents ─────────────────────────────────────────────────────────
D('deleteAllAgentsBtn').addEventListener('click', async () => {
  if (!confirm("Are you sure you want to delete ALL agents and their trade histories permanently?")) return;
  try {
    const res  = await fetch(`${API_BASE}/api/v1/trading/agents`, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'API error');
    toast(data.message, 'success');
    
    // Clear UI state
    D('multiAgentsContainer').innerHTML = '';
    for (const key in agentCards) delete agentCards[key];
    
    loadAgentsList();
  } catch (err) {
    toast(`Delete All error: ${err.message}`, 'error');
  }
});

// ── Auto-polling ──────────────────────────────────────────────────────────────
function startMasterPolling() {
  if (statusPollTimer) clearInterval(statusPollTimer);
  // Poll everything every 5 seconds
  statusPollTimer = setInterval(() => {
    loadAgentsList(true); // silent refresh
  }, 5000);
}

// Start polling on load
startMasterPolling();

// ── Fetch + Render All Agents ─────────────────────────────────────────────────
D('fetchAgentsBtn').addEventListener('click', () => loadAgentsList(false));

async function loadAgentsList(silent = false) {
  try {
    const res  = await fetch(`${API_BASE}/api/v1/trading/agents`);
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || 'API error');

    const agents = json.agents || [];
    renderAgentsTable(agents);
    
    // Process active cards
    const activeAgents = agents.filter(a => a.status === 'running');
    
    for (const a of activeAgents) {
      const cardCtx = getOrCreateAgentCard(a);
      await refreshAgentData(a.agent_id, cardCtx);
    }
    
    if (!silent) toast('Refreshed agents', 'success');
  } catch (err) {
    if (!silent) toast(`Agents error: ${err.message}`, 'error');
  }
}

function renderAgentsTable(agents) {
  if (!agents.length) {
    D('agentsEmpty').style.display = '';
    D('agentsTable').style.display = 'none';
    return;
  }
  const tbody = D('agentsBody');
  tbody.innerHTML = '';
  agents.forEach(a => {
    const tr   = document.createElement('tr');
    const pnl  = a.total_pnl ?? 0;
    const lc   = a.last_cycle_at ? new Date(a.last_cycle_at).toLocaleString('en-IN') : 'Never';
    const statusColor = a.status === 'running' ? 'var(--accent)' : 'var(--text-muted)';
    tr.innerHTML = `
      <td style="font-family:monospace;font-size:12px">${a.agent_id}</td>
      <td>${a.ticker}</td>
      <td style="color:${statusColor};font-weight:600">${a.status?.toUpperCase()}</td>
      <td>$${Number(a.portfolio_value ?? a.starting_capital).toLocaleString()}</td>
      <td class="${pnl >= 0 ? 'badge-up' : 'badge-down'}">${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toFixed(2)}</td>
      <td class="${pnl >= 0 ? 'badge-up' : 'badge-down'}">${(a.total_pnl_pct ?? 0).toFixed(2)}%</td>
      <td>${a.shares ?? 0}</td>
      <td style="color:${a.last_action?.startsWith('BUY') ? 'var(--accent)' : a.last_action?.includes('SELL') ? 'var(--red)' : 'var(--text-muted)'}">${a.last_action ?? '—'}</td>
      <td>${a.cycle_count ?? 0}</td>
      <td style="font-size:11px;color:var(--text-muted)">${lc}</td>
    `;
    tbody.appendChild(tr);
  });
  D('agentsEmpty').style.display = 'none';
  D('agentsTable').style.display = '';
}

// ── Dashboard Card Management ────────────────────────────────────────────────
function getOrCreateAgentCard(agent) {
  if (agentCards[agent.agent_id]) return agentCards[agent.agent_id];
  
  const template = D('agentCardTemplate').content.cloneNode(true);
  const card = template.querySelector('.agent-dashboard-card');
  card.id = `card_${agent.agent_id}`;
  
  // Set static info
  card.querySelector('.agent-title').textContent = agent.ticker;
  card.querySelector('.agent-id-badge').textContent = agent.agent_id;

  // Delete agent handler
  card.querySelector('.delete-agent-btn').onclick = async () => {
    if (!confirm(`Are you sure you want to delete ${agent.agent_id}?`)) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/trading/agent/${agent.agent_id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('API error');
      toast(`Agent ${agent.agent_id} deleted`, 'success');
      card.remove();
      delete agentCards[agent.agent_id];
      loadAgentsList(true);
    } catch(e) {
      toast(`Delete error: ${e.message}`, 'error');
    }
  };
  
  D('multiAgentsContainer').prepend(card);
  
  // Init chart
  const chartWrapper = card.querySelector('.tradingChartWrapper');
  const chartInstance = LightweightCharts.createChart(chartWrapper, {
    layout: { background: { color: 'transparent' }, textColor: '#8a9bb5' },
    grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: 'rgba(255,255,255,0.07)' },
    timeScale: { borderColor: 'rgba(255,255,255,0.07)', timeVisible: true, rightOffset: 5 },
    width: chartWrapper.clientWidth || 800,
    height: chartWrapper.clientHeight || 320,
  });

  const candleSeries = chartInstance.addCandlestickSeries({
    upColor: '#3ecf8e', downColor: '#f87171', borderUpColor: '#3ecf8e', borderDownColor: '#f87171',
    wickUpColor: '#3ecf8e', wickDownColor: '#f87171',
  });

  const ro = new ResizeObserver(() => {
    if (chartWrapper.clientWidth > 0 && chartWrapper.clientHeight > 0) {
      chartInstance.applyOptions({ 
        width: chartWrapper.clientWidth,
        height: chartWrapper.clientHeight
      });
    }
  });
  ro.observe(chartWrapper);
  
  const ctx = { card, chartInstance, candleSeries };
  agentCards[agent.agent_id] = ctx;
  return ctx;
}

async function refreshAgentData(agentId, ctx) {
  try {
    // 1. Fetch Status
    const resStatus  = await fetch(`${API_BASE}/api/v1/trading/${agentId}/status`);
    if (resStatus.ok) {
      const d = await resStatus.json();
      renderCardStatus(ctx.card, d);
    }
    
    // 2. Fetch Trades
    let trades = [];
    const resTrades = await fetch(`${API_BASE}/api/v1/trading/${agentId}/trades`);
    if (resTrades.ok) {
      const j = await resTrades.json();
      trades = j.trades || [];
      renderCardTrades(ctx.card, trades);
    }
    
    // 3. Fetch Chart Data
    const resChart = await fetch(`${API_BASE}/api/v1/trading/${agentId}/chart`);
    if (resChart.ok) {
      const j = await resChart.json();
      ctx.candleSeries.setData(j.data);
      
      if (trades.length) {
        const markers = trades.map(t => {
          const time = Math.floor(new Date(t.timestamp).getTime() / 1000);
          const isBuy = t.action.startsWith('BUY');
          return {
            time: time,
            position: isBuy ? 'belowBar' : 'aboveBar',
            color: isBuy ? '#3ecf8e' : '#f87171',
            shape: isBuy ? 'arrowUp' : 'arrowDown',
            text: t.action
          };
        }).sort((a, b) => a.time - b.time);
        ctx.candleSeries.setMarkers(markers);
      }
    }
  } catch(e) {
    console.warn('Refresh error for', agentId, e);
  }
}

function renderCardStatus(card, d) {
  const fmt  = (v, dec = 2) => v != null ? `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec })}` : '—';
  const pnlColor = v => v >= 0 ? 'green' : 'red';
  const pnl  = d.total_pnl ?? 0;
  const pct  = d.total_pnl_pct ?? 0;

  const isRunning = d.status === 'running' && d.task_is_alive;
  const statusBadge = isRunning
    ? '<span class="agent-pulse"></span><span style="color:var(--accent)">RUNNING</span>'
    : '<span style="color:var(--red)">STOPPED</span>';

  card.querySelector('.agent-status-badge').innerHTML = statusBadge;

  const sign = pnl >= 0 ? '+' : '';
  card.querySelector('.t-pnlTotal').textContent = `${sign}$${Math.abs(pnl).toFixed(2)}`;
  card.querySelector('.t-pnlTotal').className = `pnl-value ${pnlColor(pnl)} t-pnlTotal`;
  card.querySelector('.t-pnlPct').textContent = `${sign}${pct.toFixed(2)}%`;
  card.querySelector('.t-pnlPct').className = `pnl-value ${pnlColor(pnl)} t-pnlPct`;
  card.querySelector('.t-pnlPortfolio').textContent = fmt(d.portfolio_value);
  card.querySelector('.t-pnlCash').textContent = fmt(d.cash);
  card.querySelector('.t-pnlShares').textContent = d.shares ?? 0;
  card.querySelector('.t-pnlCycles').textContent = d.cycle_count ?? 0;
}

function renderCardTrades(card, trades) {
  const empty = card.querySelector('.t-tradeLogEmpty');
  const table = card.querySelector('.t-tradeLogTable');
  const tbody = card.querySelector('.t-tradeLogBody');
  
  if (!trades.length) {
    empty.style.display = '';
    table.style.display = 'none';
    return;
  }
  
  empty.style.display = 'none';
  table.style.display = '';
  tbody.innerHTML = '';
  
  trades.forEach(t => {
    const tr   = document.createElement('tr');
    const rpnl = t.realized_pnl ?? 0;
    tr.innerHTML = `
      <td>${t.trade_date ?? '—'}</td>
      <td class="${t.action.startsWith('BUY') ? 'badge-up' : 'badge-down'}">${t.action.startsWith('BUY') ? '▲ ' : '▼ '}${t.action}</td>
      <td>$${Number(t.price).toFixed(2)}</td>
      <td>${t.shares}</td>
      <td>$${Number(t.value).toLocaleString()}</td>
      <td class="${rpnl >= 0 ? 'badge-up' : 'badge-down'}">${rpnl >= 0 ? '+' : ''}$${rpnl.toFixed(2)}</td>
    `;
    tbody.appendChild(tr);
  });
}
