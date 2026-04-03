/* ============================================================
   ROBOADVISOR DASHBOARD — app.de.js (German localized)
   ============================================================ */

'use strict';

// ── State ──────────────────────────────────────────────────────
let state = {
  portfolio: [],
  priceHistory: {},      // { ticker: [{date, close}] }
  suggestions: [],
  backtestResults: [],
  executedTrades: [],
  meta: {}
};

let charts = {};
let activeRange = '1M';

// ── Palette ────────────────────────────────────────────────────
const PALETTE = [
  '#10b981', '#3b82f6', '#f59e0b', '#ef4444',
  '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16'
];

// ── German translations for signals/badges ─────────────────────
const SIGNAL_DE = {
  'BUY': 'KAUF', 'SELL': 'VERKAUF', 'HOLD': 'HALTEN',
  'HIGH': 'HOCH', 'MEDIUM': 'MITTEL', 'MED': 'MITTEL', 'LOW': 'NIEDRIG'
};

function translateSignal(s) {
  if (!s) return s;
  return SIGNAL_DE[s.toUpperCase()] || s;
}

// ── Init ───────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('file-import').addEventListener('change', handleFileImport);
  document.getElementById('btn-load-demo').addEventListener('click', loadDemo);
  document.getElementById('btn-load-demo2').addEventListener('click', loadDemo);

  document.querySelectorAll('[data-range]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-range]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeRange = btn.dataset.range;
      renderPerformanceChart();
    });
  });

  document.getElementById('suggestion-filter').addEventListener('change', renderSuggestionTable);

  // Auto-load dashboard data
  autoLoad();
});

// ── Auto-Load ──────────────────────────────────────────────────
function autoLoad() {
  const paths = ['./dashboard_data.json', '../dashboard/dashboard_data.json'];

  function tryFetch(index) {
    if (index >= paths.length) {
      const cached = localStorage.getItem('roboadvisor_cache_de');
      if (cached) {
        try {
          const data = JSON.parse(cached);
          loadData(data);
          showCacheBadge(data.meta?.generated_at);
        } catch(e) { /* ignore corrupt cache */ }
      }
      return;
    }
    fetch(paths[index])
      .then(r => { if (!r.ok) throw new Error('not found'); return r.json(); })
      .then(data => {
        localStorage.setItem('roboadvisor_cache_de', JSON.stringify(data));
        loadData(data);
        showCacheBadge(data.meta?.generated_at, 'live');
      })
      .catch(() => tryFetch(index + 1));
  }

  tryFetch(0);
}

function showCacheBadge(dateStr, source) {
  const el = document.getElementById('last-updated');
  if (el) {
    const src = source === 'live' ? '🟢 Aktuell' : '📦 Zwischengespeichert';
    el.textContent = `${src} — ${dateStr || 'unbekanntes Datum'}`;
  }
}

// ── File Import ────────────────────────────────────────────────
function handleFileImport(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => {
    try {
      const data = JSON.parse(ev.target.result);
      loadData(data);
    } catch (err) {
      alert('Ungültiges JSON: ' + err.message);
    }
  };
  reader.readAsText(file);
  e.target.value = '';
}

function loadData(data) {
  state.portfolio       = data.portfolio        || [];
  state.priceHistory    = data.price_history    || {};
  state.suggestions     = data.suggestions      || [];
  state.backtestResults = data.backtest_results || [];
  state.executedTrades  = data.executed_trades  || [];
  state.meta            = data.meta             || {};

  // Sort everything by date asc
  state.suggestions     = state.suggestions.sort((a,b) => a.date.localeCompare(b.date));
  state.executedTrades  = state.executedTrades.sort((a,b) => a.date.localeCompare(b.date));
  Object.keys(state.priceHistory).forEach(t => {
    state.priceHistory[t] = state.priceHistory[t].sort((a,b) => a.date.localeCompare(b.date));
  });

  document.getElementById('empty-state').style.display  = 'none';
  document.getElementById('dashboard').style.display    = '';

  const ts = state.meta.generated_at || new Date().toISOString().slice(0,10);
  document.getElementById('last-updated').textContent = `Zuletzt aktualisiert: ${ts}`;

  renderAll();
}

function renderAll() {
  renderKPIs();
  renderPerformanceChart();
  renderAllocationChart();
  renderSignalsList();
  renderBacktestGrid();
  renderTickerCharts();
  renderTradesTable();
  renderSuggestionFilter();
  renderSuggestionTable();
}

// ── Helpers ────────────────────────────────────────────────────
function fmt(n, decimals=2, prefix='') {
  if (n == null || isNaN(n)) return '—';
  return prefix + Number(n).toFixed(decimals);
}

function fmtPct(n, decimals=1) {
  if (n == null || isNaN(n)) return '—';
  const v = Number(n);
  return (v >= 0 ? '+' : '') + v.toFixed(decimals) + '%';
}

function badgeHtml(signal) {
  if (!signal || signal === '—') return '<span class="badge badge-na">—</span>';
  const cls = signal.toUpperCase() === 'BUY'  ? 'badge-buy'
            : signal.toUpperCase() === 'SELL' ? 'badge-sell'
            :                                   'badge-hold';
  return `<span class="badge ${cls}">${translateSignal(signal)}</span>`;
}

/** Get the last price from priceHistory for a ticker */
function lastPrice(ticker) {
  const hist = state.priceHistory[ticker];
  if (!hist || !hist.length) return null;
  return hist[hist.length - 1].close;
}

/** Compute portfolio total value */
function computePortfolioValue() {
  return state.portfolio.reduce((sum, pos) => {
    const p = lastPrice(pos.ticker);
    return p ? sum + p * pos.shares : sum;
  }, 0);
}

/** Build a daily portfolio-value series by summing across all tickers */
function buildPortfolioSeries() {
  const allDates = new Set();
  Object.values(state.priceHistory).forEach(arr => arr.forEach(d => allDates.add(d.date)));
  const dates = [...allDates].sort();

  const maps = {};
  state.portfolio.forEach(pos => {
    maps[pos.ticker] = new Map((state.priceHistory[pos.ticker]||[]).map(d=>[d.date,d.close]));
  });

  return dates.map(date => {
    let total = 0;
    state.portfolio.forEach(pos => {
      const p = maps[pos.ticker]?.get(date);
      if (p) total += p * pos.shares;
    });
    return { date, value: total };
  }).filter(d => d.value > 0);
}

/** Filter a series by time range */
function filterByRange(series, range) {
  if (range === 'ALL') return series;
  const now = new Date();
  const cutoff = new Date(now);
  if (range === '1M') cutoff.setMonth(now.getMonth()-1);
  if (range === '3M') cutoff.setMonth(now.getMonth()-3);
  if (range === '6M') cutoff.setMonth(now.getMonth()-6);
  const cutStr = cutoff.toISOString().slice(0,10);
  return series.filter(d => d.date >= cutStr);
}

/** Destroy a chart if it exists */
function destroyChart(key) {
  if (charts[key]) { charts[key].destroy(); delete charts[key]; }
}

// ── KPIs ───────────────────────────────────────────────────────
function renderKPIs() {
  const totalValue = computePortfolioValue();

  const invested = state.executedTrades.reduce((sum, t) => {
    if (!t.total_eur) return sum;
    return t.action?.toUpperCase() === 'BUY' ? sum + t.total_eur : sum - t.total_eur;
  }, 0);
  const totalFees = state.executedTrades.reduce((sum, t) => sum + (t.fee_eur || 0), 0);
  const totalCost = invested + totalFees;
  const returnPct = totalCost > 0 ? ((totalValue - totalCost) / totalCost * 100) : null;

  setKPI('kpi-total-value', `€${totalValue.toFixed(2)}`, 'aktueller Marktwert', null);
  setKPI('kpi-total-return', fmtPct(returnPct), 'seit erstem Trade', returnPct != null && returnPct >= 0 ? 'positive' : returnPct != null ? 'negative' : null);
  setKPI('kpi-positions', state.portfolio.length.toString(), state.portfolio.map(p=>p.ticker).join(' · '), null);

  const sharpes = state.backtestResults.map(b=>b.sharpe_ratio).filter(n=>n!=null);
  const avgSharpe = sharpes.length ? sharpes.reduce((a,b)=>a+b,0)/sharpes.length : null;
  setKPI('kpi-sharpe', fmt(avgSharpe,2), 'Backtest', null);

  const dds = state.backtestResults.map(b=>b.max_drawdown_pct).filter(n=>n!=null);
  const worstDD = dds.length ? Math.min(...dds) : null;
  setKPI('kpi-max-dd', fmtPct(worstDD), 'schlechteste Position', worstDD != null && worstDD < 0 ? 'negative' : null);

  // Latest signals
  const latestDate = state.suggestions.length ? state.suggestions[state.suggestions.length-1].date : null;
  if (latestDate) {
    const todaySigs = state.suggestions.filter(s=>s.date===latestDate);
    const buys  = todaySigs.filter(s=>['BUY'].includes((s.llm_recommendation||s.quant_signal||'').toUpperCase())).length;
    const sells = todaySigs.filter(s=>['SELL'].includes((s.llm_recommendation||s.quant_signal||'').toUpperCase())).length;
    document.getElementById('kpi-signals-val').textContent = `${buys}× KAUF / ${sells}× VERKAUF`;
    document.getElementById('kpi-signals-sub').textContent = latestDate;
  }
}

function setKPI(id, value, sub, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  const valEl = el.querySelector('.kpi-value');
  const subEl = el.querySelector('.kpi-sub');
  if (!valEl) return;
  valEl.textContent = value;
  valEl.className = 'kpi-value' + (cls ? ' ' + cls : '');
  if (sub && subEl) subEl.textContent = sub;
}

// ── Performance Chart ──────────────────────────────────────────
function renderPerformanceChart() {
  destroyChart('performance');
  const full = buildPortfolioSeries();
  const series = filterByRange(full, activeRange);
  if (!series.length) return;

  const labels = series.map(d => d.date);
  const values = series.map(d => d.value);

  // Build trade annotations
  const annotations = {};
  state.executedTrades.forEach((t, i) => {
    if (!series.find(d=>d.date===t.date)) return;
    const isBuy = t.action?.toUpperCase() === 'BUY';
    annotations[`trade_${i}`] = {
      type: 'line',
      xMin: t.date, xMax: t.date,
      borderColor: isBuy ? 'rgba(16,185,129,0.7)' : 'rgba(239,68,68,0.7)',
      borderWidth: 2,
      borderDash: [4,3],
      label: {
        display: true,
        content: `${isBuy?'▲':'▼'} ${t.ticker}`,
        position: isBuy ? 'start' : 'end',
        backgroundColor: isBuy ? 'rgba(16,185,129,0.85)' : 'rgba(239,68,68,0.85)',
        color: '#fff',
        font: { size: 10, weight: '700' },
        padding: { x: 6, y: 3 },
        borderRadius: 4,
        yAdjust: isBuy ? -10 : 10
      }
    };
  });

  // Gradient fill
  const ctx = document.getElementById('chart-performance').getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, 380);
  grad.addColorStop(0, 'rgba(16,185,129,0.2)');
  grad.addColorStop(1, 'rgba(16,185,129,0.01)');

  charts['performance'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Portfoliowert (€)',
        data: values,
        borderColor: '#10b981',
        borderWidth: 2.5,
        backgroundColor: grad,
        fill: true,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: '#10b981',
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#16161e',
          borderColor: '#2a2a3a',
          borderWidth: 1,
          titleColor: '#9090b0',
          bodyColor: '#f0f0f8',
          bodyFont: { weight: '700', size: 14 },
          callbacks: {
            label: ctx => `€${ctx.raw.toFixed(2)}`
          }
        },
        annotation: { annotations }
      },
      scales: {
        x: {
          grid: { color: 'rgba(42,42,58,0.5)' },
          ticks: { color: '#5a5a78', font: { size: 11 }, maxTicksLimit: 8, maxRotation: 0 }
        },
        y: {
          grid: { color: 'rgba(42,42,58,0.5)' },
          ticks: { color: '#5a5a78', font: { size: 11 }, callback: v => '€'+v.toFixed(0) }
        }
      }
    }
  });
}

// ── Allocation Donut ───────────────────────────────────────────
function renderAllocationChart() {
  destroyChart('allocation');
  const totalValue = computePortfolioValue();
  if (!totalValue) return;

  const labels = state.portfolio.map(p => p.ticker);
  const values = state.portfolio.map(p => {
    const price = lastPrice(p.ticker);
    return price ? price * p.shares : 0;
  });

  const ctx = document.getElementById('chart-allocation').getContext('2d');
  charts['allocation'] = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: PALETTE,
        borderColor: '#0a0a0f',
        borderWidth: 3,
        hoverBorderColor: '#16161e'
      }]
    },
    options: {
      responsive: true,
      cutout: '68%',
      plugins: { legend: { display: false }, tooltip: {
        backgroundColor: '#16161e',
        borderColor: '#2a2a3a',
        borderWidth: 1,
        bodyColor: '#f0f0f8',
        callbacks: { label: ctx => `${ctx.label}: €${ctx.raw.toFixed(2)} (${(ctx.raw/totalValue*100).toFixed(1)}%)` }
      }}
    }
  });

  const legend = document.getElementById('allocation-legend');
  legend.innerHTML = state.portfolio.map((pos, i) => {
    const val = values[i];
    const pct = (val / totalValue * 100).toFixed(1);
    const isinWkn = [pos.isin, pos.wkn].filter(Boolean).join(' · ');
    return `<div class="donut-legend-item">
      <div class="donut-legend-dot" style="background:${PALETTE[i%PALETTE.length]}"></div>
      <span class="donut-legend-name" title="${pos.name}${isinWkn ? '\n' + isinWkn : ''}">${pos.ticker}</span>
      <span class="donut-legend-pct" style="color:${PALETTE[i%PALETTE.length]}">${pct}%</span>
    </div>`;
  }).join('');
}

// ── Signals List ───────────────────────────────────────────────
function renderSignalsList() {
  const container = document.getElementById('signals-list');
  if (!state.suggestions.length) {
    container.innerHTML = '<p style="color:var(--text-muted);font-size:13px">Keine Empfehlungen geladen.</p>';
    return;
  }

  // Get latest suggestion per ticker
  const latestByTicker = {};
  state.suggestions.forEach(s => { latestByTicker[s.ticker] = s; });

  const html = Object.values(latestByTicker).map(s => {
    const pos = state.portfolio.find(p=>p.ticker===s.ticker);
    const qSignal = s.quant_signal || '—';
    const lSignal = s.llm_recommendation || '—';
    const agree = qSignal !== '—' && lSignal !== '—' && qSignal.toUpperCase() === lSignal.toUpperCase();
    const conflict = qSignal !== '—' && lSignal !== '—' && !agree;
    const conf = s.llm_confidence ? translateSignal(s.llm_confidence) : '';

    return `<div class="signal-row">
      <span class="signal-ticker">${s.ticker}</span>
      <span class="signal-name" title="${pos?.name||''}">${pos?.name||''}</span>
      <div class="signal-badges">
        <span title="Quant-Signal">${badgeHtml(qSignal)} <span class="badge badge-na badge-quant">Q</span></span>
        <span title="KI-Empfehlung">${badgeHtml(lSignal)} <span class="badge badge-na badge-quant">KI</span></span>
        ${conflict ? '<span class="signal-conflict" title="Quant und KI sind sich uneinig">🚩</span>' : ''}
      </div>
      <span class="signal-conf">${conf}</span>
    </div>`;
  }).join('');

  container.innerHTML = html;
}

// ── Backtest Grid ──────────────────────────────────────────────
function renderBacktestGrid() {
  const grid = document.getElementById('backtest-grid');
  if (!state.backtestResults.length) {
    grid.innerHTML = '<p style="color:var(--text-muted);font-size:13px">Keine Backtesting-Ergebnisse geladen.</p>';
    return;
  }

  // Latest per ticker
  const latest = {};
  state.backtestResults.forEach(b => { latest[b.ticker] = b; });

  grid.innerHTML = Object.values(latest).map(b => {
    const ret  = b.total_return_pct;
    const sr   = b.sharpe_ratio;
    const dd   = b.max_drawdown_pct;
    const wr   = b.win_rate;
    const pos = state.portfolio.find(p=>p.ticker===b.ticker);
    return `<div class="backtest-card">
      <div class="backtest-ticker">
        <span style="font-size:18px">📊</span>
        <div>
          <div>${b.ticker}</div>
          <div style="font-size:11px;color:var(--text-muted);font-weight:400">${pos?.name||''}</div>
        </div>
      </div>
      <div class="backtest-metrics">
        <div class="backtest-metric">
          <span class="metric-label">Gesamtrendite</span>
          <span class="metric-value ${ret>=0?'positive':'negative'}">${fmtPct(ret)}</span>
        </div>
        <div class="backtest-metric">
          <span class="metric-label">Sharpe-Ratio</span>
          <span class="metric-value ${sr>=1?'positive':sr>=0?'neutral':'negative'}">${fmt(sr,2)}</span>
        </div>
        <div class="backtest-metric">
          <span class="metric-label">Max. Drawdown</span>
          <span class="metric-value negative">${fmtPct(dd)}</span>
        </div>
        <div class="backtest-metric">
          <span class="metric-label">Trefferquote</span>
          <span class="metric-value ${wr>=0.5?'positive':'neutral'}">${wr!=null?(wr*100).toFixed(0)+'%':'—'}</span>
        </div>
        <div class="backtest-metric">
          <span class="metric-label">Laufdatum</span>
          <span class="metric-value neutral" style="font-weight:500">${b.run_date||'—'}</span>
        </div>
      </div>
    </div>`;
  }).join('');
}

// ── Individual Ticker Charts ───────────────────────────────────
function renderTickerCharts() {
  const container = document.getElementById('ticker-charts');
  container.innerHTML = '';

  state.portfolio.forEach((pos, idx) => {
    const hist = state.priceHistory[pos.ticker] || [];
    if (!hist.length) {
      const card = document.createElement('div');
      card.className = 'ticker-chart-card';
      card.innerHTML = `<div class="ticker-chart-header"><div><div class="ticker-chart-title">${pos.ticker}</div><div style="font-size:11px;color:var(--text-muted);margin-top:2px">${pos.name}</div></div></div><div style="color:var(--text-muted);font-size:13px;padding:24px 0;text-align:center">Keine Kursdaten verfügbar</div>`;
      container.appendChild(card);
      return;
    }

    const divId = `ticker-chart-${idx}`;
    const card = document.createElement('div');
    card.className = 'ticker-chart-card';

    const lastClose = hist[hist.length-1]?.close;
    const prevClose = hist[hist.length-2]?.close;
    const changePct = (prevClose && lastClose) ? ((lastClose - prevClose)/prevClose*100) : null;
    const changeDir = (changePct != null && changePct >= 0) ? 'up' : changePct != null ? 'down' : '';

    // Latest ARIMA forecasts for this ticker
    const latestSug = [...state.suggestions].filter(s=>s.ticker===pos.ticker).pop();

    card.innerHTML = `
      <div class="ticker-chart-header">
        <div>
          <div class="ticker-chart-title">${pos.ticker}</div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${pos.name}</div>
        </div>
        <div class="ticker-chart-price">
          <div class="ticker-current-price">€${lastClose?.toFixed(2)||'—'}</div>
          <div class="ticker-price-change ${changeDir}">${changePct!=null?fmtPct(changePct):'—'} heute</div>
        </div>
      </div>
      <div style="font-size:10px;color:var(--text-muted);margin-top:2px;font-family:monospace">${[pos.isin,pos.wkn].filter(Boolean).join(' · ')}</div>
      <div class="ticker-chart-canvas-wrap">
        <canvas id="${divId}"></canvas>
      </div>`;
    container.appendChild(card);

    const labels = hist.map(d=>d.date);
    const prices  = hist.map(d=>d.close);
    const color   = PALETTE[idx % PALETTE.length];

    // Forecast overlay (extend labels)
    let forecastLabels = [], forecastData = [], forecastLower = [], forecastUpper = [];
    if (latestSug?.arima_forecast_1d != null) {
      const lastDate = new Date(hist[hist.length-1].date);
      [1, 2, 3, 4, 5].forEach(n => {
        const d = new Date(lastDate);
        d.setDate(d.getDate()+n);
        forecastLabels.push(d.toISOString().slice(0,10));
      });
      const f1 = latestSug.arima_forecast_1d;
      const f5 = latestSug.arima_forecast_5d || f1;
      const step = (f5-f1)/4;
      forecastData = [lastClose, f1, f1+step, f1+step*2, f1+step*3, f5];
      forecastLabels = [hist[hist.length-1].date, ...forecastLabels];
    }

    const allLabels = [...labels, ...forecastLabels.slice(1)];
    const totalLen  = allLabels.length;

    const histPadded  = [...prices,      ...Array(totalLen - prices.length).fill(null)];
    const forecastPad = forecastData.length
      ? [...Array(Math.max(0, labels.length - 1)).fill(null), ...forecastData]
      : Array(totalLen).fill(null);

    destroyChart(divId);
    const ctx2 = document.getElementById(divId).getContext('2d');
    const grad2 = ctx2.createLinearGradient(0,0,0,200);
    grad2.addColorStop(0, color+'30');
    grad2.addColorStop(1, color+'05');

    charts[divId] = new Chart(ctx2, {
      type: 'line',
      data: {
        labels: allLabels,
        datasets: [
          {
            label: 'Kurs',
            data: histPadded,
            borderColor: color,
            borderWidth: 2,
            backgroundColor: grad2,
            fill: true,
            tension: 0.3,
            pointRadius: 0,
          },
          ...(forecastData.length ? [{
            label: 'ARIMA-Prognose',
            data: forecastPad,
            borderColor: color,
            borderWidth: 2,
            borderDash: [5,4],
            backgroundColor: 'transparent',
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            pointStyle: 'circle',
          }] : [])
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#16161e',
            borderColor: '#2a2a3a',
            borderWidth: 1,
            bodyColor: '#f0f0f8',
            callbacks: { label: ctx => `${ctx.dataset.label}: €${ctx.raw?.toFixed(2)||'—'}` }
          }
        },
        scales: {
          x: {
            grid: { color: 'rgba(42,42,58,0.4)' },
            ticks: { color: '#5a5a78', font: { size: 10 }, maxTicksLimit: 6, maxRotation: 0 }
          },
          y: {
            grid: { color: 'rgba(42,42,58,0.4)' },
            ticks: { color: '#5a5a78', font: { size: 10 }, callback: v => '€'+v.toFixed(1) }
          }
        }
      }
    });
  });
}

// ── Trades Table ───────────────────────────────────────────────
function renderTradesTable() {
  const tbody = document.getElementById('trades-tbody');
  if (!state.executedTrades.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="color:var(--text-muted);text-align:center;padding:24px">Keine ausgeführten Trades geladen.</td></tr>';
    return;
  }

  tbody.innerHTML = [...state.executedTrades].reverse().map(t => {
    const isBuy = t.action?.toUpperCase() === 'BUY';
    const actionDE = isBuy ? 'KAUF' : (t.action?.toUpperCase() === 'SELL' ? 'VERKAUF' : (t.action?.toUpperCase() || '—'));
    return `<tr>
      <td>${t.date}</td>
      <td>${t.ticker}</td>
      <td style="font-family:monospace;font-size:11px;color:var(--text-muted)">${t.isin||'—'}</td>
      <td style="font-family:monospace;font-size:11px;color:var(--text-muted)">${t.wkn||'—'}</td>
      <td class="${isBuy?'action-buy':'action-sell'}">${actionDE}</td>
      <td>${t.shares!=null?t.shares:'—'}</td>
      <td>${t.price_per_share!=null?'€'+Number(t.price_per_share).toFixed(2):'—'}</td>
      <td>${t.total_eur!=null?'€'+Number(t.total_eur).toFixed(2):'—'}</td>
      <td style="color:${t.fee_eur?'var(--sell)':'var(--text-muted)'}">${t.fee_eur!=null?'€'+Number(t.fee_eur).toFixed(2):'—'}</td>
      <td style="color:var(--text-muted);font-size:12px">${t.note||''}</td>
    </tr>`;
  }).join('');
}

// ── Suggestion Filter & Table ──────────────────────────────────
function renderSuggestionFilter() {
  const sel = document.getElementById('suggestion-filter');
  const tickers = [...new Set(state.suggestions.map(s=>s.ticker))];
  sel.innerHTML = '<option value="ALL">Alle Ticker</option>' +
    tickers.map(t=>`<option value="${t}">${t}</option>`).join('');
}

function renderSuggestionTable() {
  const filter = document.getElementById('suggestion-filter').value;
  const tbody  = document.getElementById('suggestions-tbody');
  const rows   = [...state.suggestions]
    .filter(s => filter==='ALL' || s.ticker===filter)
    .reverse();

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="9" style="color:var(--text-muted);text-align:center;padding:24px">Keine Empfehlungen geladen.</td></tr>';
    return;
  }

  tbody.innerHTML = rows.map(s => {
    const qSignal = s.quant_signal || '—';
    const lSignal = s.llm_recommendation || '—';
    const conflict = qSignal !== '—' && lSignal !== '—' && qSignal.toUpperCase() !== lSignal.toUpperCase();
    const conf = s.llm_confidence || '—';
    const confDE = translateSignal(conf);
    return `<tr>
      <td>${s.date}</td>
      <td>${s.ticker}</td>
      <td>${badgeHtml(qSignal)}</td>
      <td>${badgeHtml(lSignal)} ${conflict?'🚩':''}</td>
      <td><span style="color:${conf==='HIGH'?'var(--buy)':conf==='LOW'?'var(--sell)':'var(--hold)'}">${confDE}</span></td>
      <td>${s.arima_forecast_1d!=null?'€'+Number(s.arima_forecast_1d).toFixed(2):'—'}</td>
      <td>${s.arima_forecast_5d!=null?'€'+Number(s.arima_forecast_5d).toFixed(2):'—'}</td>
      <td>${s.garch_volatility!=null?(s.garch_volatility*100).toFixed(2)+'%':'—'}</td>
      <td><span class="rationale-cell" title="${(s.llm_rationale||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}">${s.llm_rationale||'—'}</span></td>
    </tr>`;
  }).join('');
}

// ── Demo Data ──────────────────────────────────────────────────
function loadDemo() {
  function genPrices(ticker, startPrice, volatility) {
    const dates = [];
    const d = new Date('2026-01-01');
    const end = new Date('2026-03-31');
    let price = startPrice;
    while (d <= end) {
      const day = d.getDay();
      if (day !== 0 && day !== 6) {
        price = price * (1 + (Math.random()-0.48) * volatility);
        dates.push({ date: d.toISOString().slice(0,10), close: +price.toFixed(2) });
      }
      d.setDate(d.getDate()+1);
    }
    return dates;
  }

  const amunPrices = genPrices('AMUN.PA', 42.0, 0.012);
  const iegePrices = genPrices('EUN4.DE', 28.0, 0.004);

  const demo = {
    meta: { generated_at: '2026-03-31', version: '1.0' },
    portfolio: [
      { ticker: 'AMUN.PA', name: 'Amundi MSCI World III UCITS ETF Dis', shares: 1.62338, currency: 'EUR' },
      { ticker: 'EUN4.DE', name: 'iShares Germany Government Bonds UCITS ETF EUR Dist', shares: 3, currency: 'EUR' }
    ],
    price_history: {
      'AMUN.PA': amunPrices,
      'EUN4.DE': iegePrices
    },
    suggestions: [
      { date: '2026-03-28', ticker: 'AMUN.PA', quant_signal: 'HOLD', llm_recommendation: 'HOLD', llm_confidence: 'HIGH', llm_rationale: 'Die breiten Aktienmärkte stehen weiter unter dem Druck des Ölpreis-Schocks; MSCI World hält sich stabil.', arima_forecast_1d: amunPrices.at(-1).close * 1.003, arima_forecast_5d: amunPrices.at(-1).close * 1.011, garch_volatility: 0.013 },
      { date: '2026-03-28', ticker: 'EUN4.DE', quant_signal: 'HOLD', llm_recommendation: 'SELL', llm_confidence: 'MED', llm_rationale: 'Steigende Inflationserwartungen durch Ölschock könnten Anleihepreise unter Druck setzen; Duration reduzieren erwägen.', arima_forecast_1d: iegePrices.at(-1).close * 0.998, arima_forecast_5d: iegePrices.at(-1).close * 0.994, garch_volatility: 0.004 },
      { date: '2026-03-31', ticker: 'AMUN.PA', quant_signal: 'BUY', llm_recommendation: 'HOLD', llm_confidence: 'MED', llm_rationale: 'Quant-Signal hat KAUF ausgelöst, aber makroökonomische Unsicherheit besteht weiter. KI empfiehlt Abwarten.', arima_forecast_1d: amunPrices.at(-1).close * 1.005, arima_forecast_5d: amunPrices.at(-1).close * 1.018, garch_volatility: 0.014 },
      { date: '2026-03-31', ticker: 'EUN4.DE', quant_signal: 'HOLD', llm_recommendation: 'HOLD', llm_confidence: 'HIGH', llm_rationale: 'Staatsanleihen bleiben defensiver Anker; Position halten.', arima_forecast_1d: iegePrices.at(-1).close * 0.999, arima_forecast_5d: iegePrices.at(-1).close * 0.997, garch_volatility: 0.003 }
    ],
    backtest_results: [
      { run_date: '2026-03-31', ticker: 'AMUN.PA', total_return_pct: 6.4, sharpe_ratio: 0.87, max_drawdown_pct: -4.2, win_rate: 0.58 },
      { run_date: '2026-03-31', ticker: 'EUN4.DE', total_return_pct: 1.1, sharpe_ratio: 0.42, max_drawdown_pct: -1.8, win_rate: 0.51 }
    ],
    executed_trades: [
      { date: '2026-01-15', ticker: 'AMUN.PA', action: 'BUY', shares: 1.62338, price_per_share: 42.10, total_eur: 68.35, note: 'Erstposition' },
      { date: '2026-01-15', ticker: 'EUN4.DE', action: 'BUY', shares: 3, price_per_share: 27.80, total_eur: 83.40, note: 'Erstposition' }
    ]
  };

  loadData(demo);
}
