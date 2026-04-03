# Greek Dev Team Brief — Roboadvisor v2
**Date:** 2026-04-02  
**Orchestrator:** Homer  
**Priority:** 🔴 High — Simon wants this running today

---

## Context

The roboadvisor is a fully-built quant pipeline (Y%-filter, ARIMA, GARCH, LLM/Claude) for Simon's 7-position ETF/crypto/commodity portfolio. The backend runs, the DB is populated, the dashboard exists.

**Repo:** `/Users/homer-service/.openclaw/workspace/roboadvisor/`

**Portfolio:** 7 tickers — AMUN.PA, EUN4.DE, IQQH.DE, BTCE.DE, 4GLD.DE, DFNS.SW, ICOM.L

**Tech stack:** Python 3, SQLite, yfinance, NewsAPI, Anthropic (Claude), vanilla JS dashboard

---

## What's Broken (Fix First)

The existing `roboadvisor-daily` cron (id: `225edc12`) is failing with:
> `Channel is required when multiple channels are configured: telegram, whatsapp`

The cron payload doesn't specify `delivery.channel = "whatsapp"`. This needs patching immediately — see Hector's tasks.

---

## Goal

Two daily touchpoints via WhatsApp + a browser dashboard that auto-loads from cache:

### 1. 🌅 Morning Run (08:30 CET, weekdays)
**Script:** `morning_run.py`

1. Fetch latest prices + news (via existing `data/prices.py`, `data/news.py`)
2. Run quant models + LLM analysis (reuse `run.py` pipeline logic)
3. Save morning snapshot: `snapshots/YYYY-MM-DD-morning.json`
   ```json
   {
     "date": "2026-04-02",
     "run_at": "2026-04-02T06:30:00Z",
     "tickers": {
       "AMUN.PA": {
         "morning_price": 71.23,
         "quant_signal": "HOLD",
         "llm_recommendation": "HOLD",
         "llm_confidence": "MED",
         "llm_rationale": "..."
       }
     }
   }
   ```
4. Export dashboard: run `export_dashboard.py`
5. Send WhatsApp morning briefing to +4915789623707 (see format below)

**Morning WhatsApp format:**
```
☀️ *Roboadvisor Morning — {date}*

*Trade Suggestions:*
{per-ticker: TICKER | Quant: X | LLM: Y (CONF) | flag}

*Top Picks:*
• TICKER: "first sentence of rationale"
• TICKER: "..."

🚩 *Conflicts:* {list or None}

_Reply: BUY TICKER SHARES at PRICE_
_or: SELL TICKER SHARES at PRICE_
```

### 2. 🌆 Evening Run (18:30 CET, weekdays)
**Script:** `evening_run.py`

1. Fetch closing prices for today
2. Load this morning's snapshot from `snapshots/YYYY-MM-DD-morning.json`
3. Grade each morning signal:
   - If no snapshot exists: skip grading, note it
   - For each ticker: compare morning_price vs closing_price
   - BUY suggestion correct if price went UP (close > morning_price)
   - SELL suggestion correct if price went DOWN (close < morning_price)
   - HOLD: correct if abs(change) < 0.5%
   - Grade: ✅ Correct / ❌ Wrong / ➖ Neutral
4. Compute portfolio day P&L (sum across all positions: shares × (close - morning_price))
5. Re-run `export_dashboard.py --trades trades.json`
6. Send WhatsApp evening digest to +4915789623707

**Evening WhatsApp format:**
```
🌆 *Roboadvisor Evening — {date}*

*Today's Performance:*
Portfolio P&L: +€XX.XX (+X.XX%)

*Signal Retrospective:*
TICKER | Morning: BUY | Actual: ▲X.XX% | ✅ Correct
TICKER | Morning: SELL | Actual: ▲X.XX% | ❌ Wrong
TICKER | Morning: HOLD | Actual: ▲X.XX% | ➖ Neutral

*Score: X/7 correct*

_Dashboard updated. Reply with any trades to log them._
```

### 3. 📊 Dashboard Auto-Load
**Files:** `dashboard/index.html`, `dashboard/app.js`

The dashboard currently shows an empty state and requires manual JSON import. Fix it to:

1. On page load: `fetch('./dashboard_data.json')` from same origin
2. If fetch succeeds: load data + save to `localStorage['roboadvisor_cache']` + show timestamp
3. If fetch fails (offline / file missing): try loading from `localStorage['roboadvisor_cache']`
4. If both fail: show existing empty state / import UI (unchanged)
5. Manual import (existing file-import button) should still work and override cache
6. Show a small "📦 Loaded from cache — {date}" badge when using localStorage

The dashboard is opened as a local file (`file://`) so fetch may need a CORS workaround — use `XMLHttpRequest` with a same-directory path, or just try both `fetch('./dashboard_data.json')` and `fetch('../dashboard/dashboard_data.json')`.

---

## File Structure After Implementation

```
roboadvisor/
├── morning_run.py          ← NEW: morning pipeline + WhatsApp
├── evening_run.py          ← NEW: evening retrospective + WhatsApp
├── run.py                  ← unchanged (shared pipeline)
├── export_dashboard.py     ← unchanged
├── send_whatsapp.py        ← unchanged (trade parsing, used by both)
├── snapshots/              ← NEW directory
│   └── YYYY-MM-DD-morning.json
├── dashboard/
│   ├── index.html          ← MODIFY: auto-load logic
│   ├── app.js              ← MODIFY: auto-load + cache
│   └── dashboard_data.json ← auto-updated by export
└── logs/
    ├── morning.log
    └── evening.log
```

---

## Constraints & Notes

- **All scripts must run from the project root:** `cd /Users/homer-service/.openclaw/workspace/roboadvisor`
- **Env vars** are in `.env` — loaded by `utils/config.py` via `load_config()`. Use it.
- **WhatsApp delivery:** Use `openclaw message send --channel whatsapp --to +4915789623707 --message "..."` via subprocess (same pattern as `send_whatsapp.py`)
- **Idempotency:** Guard morning and evening runs with state files in `logs/` — don't double-send
- **No schema changes to DB needed** — snapshots are JSON files, not DB rows
- **Error handling:** If morning run fails, notify Simon with `⚠️ Morning pipeline failed. Check logs.`
- **Market closed check:** If it's a weekday but prices haven't updated (last price date < today), note it but don't crash
- **Timezone:** All date logic uses `Europe/Berlin` (CET/CEST)
- **Python version:** 3.11+ on arm64 Mac
- **DO NOT modify** `run.py`, `export_dashboard.py`, `send_whatsapp.py` internals — only add new files and modify dashboard JS/HTML

---

## Agent Assignments

### Achilles — Implementation
Build `morning_run.py` and `evening_run.py` and update the dashboard auto-load. This is the core deliverable. Reuse existing pipeline functions — do not duplicate logic from `run.py`. Import from `data/`, `models/`, `llm/`, `utils/` as needed.

Key implementation notes for `morning_run.py`:
- It's basically `run.py` but: saves the snapshot, formats morning WhatsApp msg, sends it
- Factor out a shared `_send_whatsapp_text(msg)` helper using subprocess
- Snapshot dir: `PROJECT_ROOT / "snapshots"` — create if not exists

Key implementation notes for `evening_run.py`:
- Only needs to re-fetch prices (not full re-run of models — too slow for evening)
- Actually: DO re-run `run.py` as a subprocess with today's date for fresh signals
- Then load snapshot, compute grades, compose message, send

Dashboard auto-load: add to `app.js` a `loadDataFromUrl()` function that fires on `DOMContentLoaded`, tries fetch, falls back to localStorage. The existing `loadData(jsonData)` function already handles rendering — just call it.

### Odysseus — Testing
Test the existing pipeline first (run `python3 run.py --skip-news` in the project dir and report what happens). Then write edge case analysis covering:
1. No morning snapshot exists when evening runs
2. Market closed / stale prices (last_price_date < today)
3. All signals are HOLD — evening score calculation
4. Ticker with no price data in DB
5. Dashboard fetch fails (offline mode)
6. WhatsApp send subprocess failure

For each: document the expected behavior, verify the implementation handles it, note gaps.

### Agamemnon — Code Review
Review `morning_run.py` and `evening_run.py` once Achilles delivers. Apply BLOCK/WARN/NIT:
- BLOCK: hardcoded paths, missing error handling on subprocess calls, score calculation bugs, snapshot corruption risk
- WARN: no idempotency guard, missing timezone handling, float precision issues in P&L
- NIT: style, naming, dead imports

Also review the dashboard JS changes for correctness.

### Hector — Ops
**Do this immediately (doesn't wait for Achilles):**

1. Fix the broken `roboadvisor-daily` cron (id: `225edc12-b38d-486b-a547-c87636ef5e82`):
   - Add `delivery.channel = "whatsapp"` and `delivery.to = "+4915789623707"`
   - This is blocking today's evening run

2. Once Achilles delivers `morning_run.py` and `evening_run.py`, update the cron setup:
   - **Morning cron** (new): `0 8 * * 1-5` CET — runs `morning_run.py`
   - **Evening cron** (existing `225edc12`): update payload to run `evening_run.py` instead
   - Both should: `cd /Users/homer-service/.openclaw/workspace/roboadvisor && python3 {script}.py >> logs/{morning|evening}.log 2>&1`
   - `delivery.channel = "whatsapp"`, `delivery.to = "+4915789623707"`

3. After crons are live: do a test dry-run of both scripts to confirm they work end-to-end

---

## Definition of Done

- [ ] `morning_run.py` runs without error, saves snapshot, sends correct WhatsApp format
- [ ] `evening_run.py` runs without error, loads snapshot, grades signals correctly, sends correct WhatsApp format
- [ ] Dashboard auto-loads `dashboard_data.json` on page open without user interaction
- [ ] Broken cron fixed (no more channel error)
- [ ] Morning cron at 08:30 CET live
- [ ] Evening cron at 18:30 CET live
- [ ] Both scripts handle all Odysseus edge cases gracefully
- [ ] Agamemnon signs off (no BLOCKs)
