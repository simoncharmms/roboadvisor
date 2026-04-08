# Greek Dev Team Brief — Roboadvisor Bug Fixes
**Date:** 2026-04-08  
**Repo:** `/Users/homer-service/.openclaw/workspace/roboadvisor/`  
**Priority:** High — 4 confirmed bugs, all reproducible

---

## Bug Inventory

### BUG A — LLM upsert silently wipes ARIMA/quant signals ⚡ CRITICAL
**File:** `data/db.py` → `upsert_signal()`  
**Root cause:** `upsert_signal` uses `INSERT OR REPLACE` which deletes the entire row and re-inserts. When the LLM step (in `morning_run.py` ~line 230) calls `upsert_signal(llm_recommendation=..., llm_confidence=..., ...)` it passes `None` for all quant fields → the full row is replaced with NULLs for `y_filter_signal`, `arima_forecast_1d`, `arima_forecast_5d`, `garch_volatility`.  
**Confirmed:** DB query shows today's signals have `y_filter_signal=None, arima_forecast_1d=None` despite ARIMA running and producing results. The dashboard therefore displays no ARIMA forecast line.

**Fix:** Change `upsert_signal` to a proper merge upsert using `INSERT INTO ... ON CONFLICT(ticker, date) DO UPDATE SET` with `COALESCE` so existing non-null values are preserved. Specifically: quant fields should only be updated if the incoming value is not None; LLM fields should only be updated if the incoming value is not None.

```sql
-- Pattern to use:
INSERT INTO signals (ticker, date, y_filter_signal, arima_forecast_1d, ...)
VALUES (?, ?, ?, ?, ...)
ON CONFLICT(ticker, date) DO UPDATE SET
  y_filter_signal    = COALESCE(excluded.y_filter_signal, y_filter_signal),
  arima_forecast_1d  = COALESCE(excluded.arima_forecast_1d, arima_forecast_1d),
  arima_forecast_5d  = COALESCE(excluded.arima_forecast_5d, arima_forecast_5d),
  garch_volatility   = COALESCE(excluded.garch_volatility, garch_volatility),
  llm_recommendation = COALESCE(excluded.llm_recommendation, llm_recommendation),
  llm_confidence     = COALESCE(excluded.llm_confidence, llm_confidence),
  llm_rationale      = COALESCE(excluded.llm_rationale, llm_rationale),
  llm_quant_agreement= COALESCE(excluded.llm_quant_agreement, llm_quant_agreement)
```

---

### BUG B — Backtest total_return always 0.0
**File:** `models/backtest.py`  
**Root cause:** The Y%-filter strategy with threshold_pct=5.0 likely never generates a BUY signal on these broad-market ETFs over the available price history, OR the backtest loop's per-bar `y_filter()` call doesn't produce BUY/SELL because the cumulative window at each step reflects current state not transition state. If no BUY ever fires, `shares_held` stays 0, `position_open` stays False, and `final_value = initial_capital` → `total_return = 0.0` always.

**Debugging steps for Achilles:**
1. Run a standalone test: call `backtest("AMUN.PA", prices_series)` and add logging to print what signal is returned at each bar. Check if BUY/SELL ever fires.
2. Check `models/y_filter.py` — confirm the signal output for a simple case.
3. If the issue is that `y_filter()` only returns HOLD for all cumulative windows:
   - The fix might be to run y_filter once on the full series and get a per-date signal mapping, then use that in the backtest loop (O(n) instead of O(n²))
   - Alternatively: lower the threshold to 2.0% for backtest to get meaningful results on low-volatility ETFs

**Additional fix needed:** The `backtest_results` table uses `INSERT` (not UPSERT), so each run appends a new row. This is causing table bloat (100+ rows for the same tickers). Add deduplication — either `INSERT OR REPLACE` with a unique constraint on (ticker, run_date), or clean up stale rows (keep only the latest per ticker per date). Check `data/db.py` → `log_backtest_result`.

---

### BUG C — Sharpe ratio numerical explosion (~-9.29e16)
**File:** `models/backtest.py` → `_compute_sharpe()`  
**Root cause:** When the equity curve is almost entirely flat (all-HOLD strategy = 0 trades), `daily_returns` is all zeros EXCEPT possibly the last 1-2 bars if a position was opened right at the end. This produces:
- `mean(excess)` ≈ `-rf_daily` ≈ -0.000159 (tiny negative)
- `std(excess, ddof=1)` ≈ near-zero (e.g. 1e-12 when 759 values are identical and 1 is slightly different)
- `Sharpe = mean/std * sqrt(252)` → astronomical negative

**Fix:**
```python
MIN_STD = 1e-8  # guard against near-zero std
if std < MIN_STD or math.isnan(std):
    return 0.0
# Also clamp output to a reasonable range
sharpe = float(np.mean(excess) / std * math.sqrt(_TRADING_DAYS_PER_YEAR))
return max(-50.0, min(50.0, sharpe))  # sane finance range
```

---

### BUG D — No news in dashboard (position charts + suggestion history)
**Two sub-issues:**

**D1 — News not exported:** `export_dashboard.py` queries prices, signals, backtest but NEVER exports `news` table data. The dashboard JSON has no `news` field.

**Fix in `export_dashboard.py`:** Add a `load_news()` function that fetches the latest N articles per ticker (last 7 days, max 5 per ticker). Add to the export dict as `"news_by_ticker": {ticker: [{published_at, headline, source, body}]}`.

```python
def load_news(conn, tickers, days=7, max_per_ticker=5):
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    result = {}
    for ticker in tickers:
        rows = conn.execute("""
            SELECT ticker, published_at, headline, source, url, body
            FROM news
            WHERE ticker = ? AND published_at >= ?
            ORDER BY published_at DESC LIMIT ?
        """, (ticker, cutoff, max_per_ticker)).fetchall()
        result[ticker] = [dict(r) for r in rows]
    return result
```

**D2 — Dashboard doesn't display news:** `dashboard/app.js` has no news rendering logic. The ticker chart shows price history + ARIMA forecast but no news markers or sentiment display.

**Fix in `dashboard/app.js`:** In `renderTickerCharts()`, after loading `latestSug`, also load `newsByTicker[ticker]` from `state.newsByTicker`. Add news headlines below each chart card as a scrollable list:
```html
<div class="ticker-news">
  <div class="news-item">📰 2026-04-07 · (Reuters) Headline text</div>
  ...
</div>
```

Also update `loadData()` to set `state.newsByTicker = data.news_by_ticker || {}`.

In `dashboard/styles.css`: add styles for `.ticker-news` and `.news-item`.

---

### BUG E — Mobile UI overflow / illegible charts
**Files:** `dashboard/styles.css`, `dashboard/app.js`  

**Observed issues:**
- Charts overflow their card boundaries on mobile
- Text (ticker labels, axis labels) is hard to read
- Some sections have no mobile breakpoint handling

**Fixes needed:**

**In `styles.css`:**
```css
/* Ensure charts never overflow cards */
.ticker-chart-card { overflow: hidden; }
.ticker-chart-canvas-wrap { 
  position: relative; 
  width: 100%; 
  overflow: hidden;
}
canvas { max-width: 100% !important; display: block; }

/* Better mobile breakpoint (extend @media 480px block) */
@media (max-width: 480px) {
  .app-header { padding: 0 12px; }
  .logo-text { font-size: 15px; }
  .content-wrap { padding: 12px 10px; }
  .kpi-strip { grid-template-columns: repeat(2, 1fr); gap: 8px; }
  .kpi-card { padding: 12px; }
  .kpi-value { font-size: 18px; }
  .section-title { font-size: 14px; }
  .ticker-chart-card { padding: 10px; }
  .ticker-chart-canvas-wrap { height: 160px !important; }
  table { font-size: 11px; }
  th, td { padding: 6px 4px; }
}
```

**In `app.js` Chart.js config blocks:** For all `responsive: true` charts, also add:
```js
maintainAspectRatio: false,
plugins: {
  legend: { labels: { font: { size: window.innerWidth < 480 ? 10 : 12 }, boxWidth: 12 } }
},
scales: {
  x: { ticks: { font: { size: window.innerWidth < 480 ? 9 : 11 }, maxRotation: 45 } },
  y: { ticks: { font: { size: window.innerWidth < 480 ? 9 : 11 } } }
}
```

---

## Task Assignments

### Achilles — Implementation
Fix all 5 bugs in order (A → B → C → D → E):
1. `data/db.py`: Fix `upsert_signal` to use COALESCE upsert
2. `models/backtest.py`: Debug BUG B (add print logging, investigate y_filter), fix Sharpe clamp (BUG C), fix backtest_results deduplication
3. `export_dashboard.py`: Add `load_news()` + include news in export JSON (BUG D1)
4. `dashboard/app.js`: Add news rendering in `renderTickerCharts()`, update `loadData()` (BUG D2)
5. `dashboard/styles.css` + `app.js`: Mobile overflow fixes (BUG E)

After fixing BUG A, re-run the morning pipeline to re-populate correct data:
```bash
cd /Users/homer-service/.openclaw/workspace/roboadvisor
python3 morning_run.py --force --skip-news 2>&1 | tail -30
python3 export_dashboard.py --trades trades.json
```

### Odysseus — Testing
Write/extend tests for:
1. `upsert_signal` merge behavior: quant fields preserved when LLM update fires (BUG A test)
2. `_compute_sharpe` with flat equity curve → 0.0, not NaN or explosion (BUG C test)
3. Backtest with synthetic price series that HAS 5%+ swings → verify trades fire and total_return != 0 (BUG B test)
4. Dashboard JSON export includes `news_by_ticker` key (BUG D1 test)
Add tests to `tests/test_roboadvisor.py` (create file if it doesn't exist).

### Agamemnon — Review
Review all Achilles changes with BLOCK/WARN/NIT system. Key things to check:
- COALESCE SQL is correct and doesn't accidentally preserve stale NULL values
- Sharpe clamp bounds are reasonable (finance convention: -50 to +50 is fine)
- News export doesn't leak sensitive data or blow up JSON size
- Mobile CSS doesn't break desktop layout

### Hector — Ops
After all fixes:
1. Run the full pipeline: `python3 morning_run.py --force 2>&1`
2. Deploy dashboard: `python3 export_dashboard.py --trades trades.json && bash deploy_dashboard.sh`
3. Commit all changes to main: `git add -A && git commit -m "fix: 5 roboadvisor bugs (upsert merge, backtest, sharpe, news export, mobile CSS)"`
4. Push: `git push origin main`
5. Report final status

---

## Context
- Repo: `/Users/homer-service/.openclaw/workspace/roboadvisor/`
- Python env: system python3, deps in requirements.txt
- Dashboard live at: https://simoncharmms.github.io/roboadvisor/
- GitHub PAT in keychain: `security find-generic-password -s github-pat -w`
- `.env` file has valid ANTHROPIC_API_KEY, NEWS_API_KEY is still invalid (skip-news for now)
- DB: `roboadvisor.db` — 760+ price rows per ticker, signals table has today's rows with NULL quant fields (BUG A confirmed)
