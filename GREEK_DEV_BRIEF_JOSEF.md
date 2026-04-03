# Greek Dev Team Brief — Roboadvisor Josef Feature
**Date:** 2026-04-03  
**Orchestrator:** Homer  
**Priority:** 🔴 High  
**Requester:** Simon

---

## User Story

> Als Simon's Papa (Josef), der nur deutsch spricht, möchte ich, wenn ich per WhatsApp nachfrage, den aktuellen Stand des Aktien Portfolios als Grafik bekommen. Die Grafik soll so aussehen, wie das Aktien Dashboard, das Dashboard muss aber auf Deutsch sein und zwar vollständig. Ich möchte auf Rückfragen eine verständliche Erklärung des Backtestings erhalten, außerdem die Einschätzung zur Portfolio Performance vom Roboadvisor per Chat, alles auf Deutsch.

**Translation:** Josef (Simon's father, German-speaking only) wants to:
1. Ask for portfolio status via WhatsApp and receive a dashboard image (chart/screenshot)
2. The dashboard shown must be **fully in German**
3. When asking follow-up questions: receive a plain-language explanation of backtesting
4. Receive AI assessment of portfolio performance — all in German

---

## Project Structure

**Repo:** `/Users/homer-service/.openclaw/workspace/roboadvisor/`

**Key files:**
- `dashboard/index.html` — main dashboard HTML (currently English)
- `dashboard/app.js` — dashboard JS (currently English labels/strings)
- `dashboard/styles.css` — styles
- `screenshot_dashboard.js` — Puppeteer screenshot script (node)
- `morning_run.py` / `evening_run.py` — daily pipeline scripts
- `export_dashboard.py` — exports `dashboard/dashboard_data.json`
- `send_whatsapp.py` — WhatsApp send helper
- `roboadvisor.db` — SQLite DB with prices, signals, backtest data
- `portfolio.json` — 7-position portfolio
- `dashboard/dashboard_data.json` — generated JSON for dashboard

**WhatsApp routing (OpenClaw):**
- Simon: +4915789623707 → routes to main session (Homer)
- Josef needs to be added as an authorized sender — his number will need to be configured
- Outbound sends via: `openclaw message send --channel whatsapp --to <number> --message <msg>`
- Image/media via: `--media <filepath>` or `--buffer <base64>` parameter

---

## What Needs to Be Built

### Feature 1: German Dashboard (Achilles)

Create a **German localized version** of the dashboard:

**File:** `dashboard/index.de.html` (clone of `index.html`, all strings German)
**File:** `dashboard/app.de.js` (clone of `app.js`, all visible strings German)

All labels, headings, KPI titles, button texts, table headers, tooltips, chart labels must be translated to German:

| English | German |
|---------|--------|
| Portfolio Value | Portfoliowert |
| Total Return | Gesamtrendite |
| Positions | Positionen |
| Avg Sharpe | Ø Sharpe-Ratio |
| Max Drawdown | Max. Drawdown |
| Today's Signals | Heutige Signale |
| Portfolio Performance | Portfolio-Performance |
| Overall value over time · markers show executed trades | Gesamtwert im Zeitverlauf · Markierungen zeigen ausgeführte Trades |
| Allocation | Aufteilung |
| Current portfolio weights | Aktuelle Portfolio-Gewichtung |
| Latest Signals | Aktuelle Signale |
| Most recent daily recommendations | Neueste tägliche Empfehlungen |
| Backtesting Results | Backtesting-Ergebnisse |
| Statistical model performance on historical data | Statistische Modellperformance auf historischen Daten |
| Individual Positions | Einzelne Positionen |
| Price history with ARIMA forecast overlay | Kursverlauf mit ARIMA-Prognose |
| Executed Trades | Ausgeführte Trades |
| Your manually logged portfolio adjustments | Manuell erfasste Portfolio-Anpassungen |
| Suggestion History | Empfehlungsverlauf |
| All daily recommendations — quant + LLM | Alle täglichen Empfehlungen — Quant + KI |
| Date | Datum |
| Ticker | Ticker |
| Action | Aktion |
| Shares | Anteile |
| Price | Kurs |
| Total (€) | Gesamt (€) |
| Fee (€) | Gebühren (€) |
| Note | Notiz |
| Quant | Quant |
| LLM | KI |
| Confidence | Konfidenz |
| ARIMA 1D | ARIMA 1T |
| ARIMA 5D | ARIMA 5T |
| Vol (GARCH) | Vol. (GARCH) |
| Rationale | Begründung |
| No data loaded | Keine Daten geladen |
| Import JSON | JSON importieren |
| Load Demo | Demo laden |
| Import your portfolio data | Portfolio-Daten importieren |
| BUY | KAUF |
| SELL | VERKAUF |
| HOLD | HALTEN |
| HIGH | HOCH |
| MEDIUM | MITTEL |
| LOW | NIEDRIG |
| Aligned | Übereinstimmend |
| Conflict | Konflikt |
| win rate | Trefferquote |
| since first trade | seit erstem Trade |
| backtested | Backtest |

Also translate the status/signal badges (BUY→KAUF, SELL→VERKAUF, HOLD→HALTEN, HIGH→HOCH, etc.)

**Important:** The German dashboard (`index.de.html` + `app.de.js`) must auto-load `./dashboard_data.json` from the same relative path — identical data format, just different language.

### Feature 2: German Screenshot Script (Achilles)

Create `screenshot_dashboard_de.js` (based on `screenshot_dashboard.js`) that:
1. Loads `dashboard/index.de.html` instead of English version
2. Serves from a local HTTP server on port 7824 (to avoid collision with port 7823)
3. All output goes to `reports/YYYY-MM-DD/de/` subdirectory
4. Takes a full-page screenshot: `00_full_dashboard_de.png`

**CLI:**
```
node screenshot_dashboard_de.js --json dashboard/dashboard_data.json [--out reports/2026-04-03/de]
```

**Screenshot approach:** Use `python3 -m http.server 7824` or a simple Node HTTP server to serve the dashboard folder, then Puppeteer navigates to it. Important: the `serve_and_screenshot.py` helper script must:
1. Start a simple HTTP server in background pointing to `dashboard/`
2. Run the Puppeteer script
3. Kill the server after

Actually, create a simple combined script: `take_screenshot_de.py`:
```python
# Starts a local HTTP server, takes screenshot via Node, returns path to image
```
This script should:
1. `python3 -m http.server 7824 --directory dashboard/` in background
2. `node screenshot_dashboard_de.js --json dashboard/dashboard_data.json --out <outdir> --url http://localhost:7824`
3. Kill server
4. Return the path to `00_full_dashboard_de.png`

### Feature 3: Josef WhatsApp Handler (Achilles)

Create `josef_handler.py` — the main entry point that processes incoming WhatsApp messages from Josef.

**Interface:**
```python
def handle_josef_message(message: str, sender_number: str) -> None:
    """
    Process an inbound WhatsApp message from Josef.
    Detects intent and responds in German via WhatsApp.
    """
```

**Intent detection (keyword-based, case-insensitive German):**

| Keywords | Intent | Action |
|----------|--------|--------|
| portfolio, aktien, depot, stand, aktuell, wie läuft, performance, übersicht | STATUS_REQUEST | Take screenshot, send image + text summary |
| backtest, backtesting, modell, wie funktioniert, erklär, was bedeutet | EXPLAIN_BACKTEST | Send German explanation text |
| alles okay, danke, danke schön, ok, super, toll | THANKS | Short friendly acknowledgment |
| (any other message) | FALLBACK | "Ich helfe bei..." message |

**For STATUS_REQUEST:**
1. Run `export_dashboard.py` to ensure `dashboard_data.json` is fresh (with 60s cache — don't re-run if file is <60s old)
2. Run `take_screenshot_de.py` to generate the German dashboard screenshot
3. Load `dashboard_data.json` and compute a short German text summary via `_compose_german_summary(data)`
4. Send image via WhatsApp (media parameter)
5. Send text summary as follow-up message

**German text summary format:**
```
📊 *Dein Portfolio — {datum}*

*Gesamtwert:* {wert} €
*Gesamtrendite:* {rendite}%

*Heutige Empfehlungen:*
• AMUN.PA — HALTEN ✅
• BTCE.DE — KAUFEN 📈

*Backtesting-Durchschnitt:*
• Sharpe-Ratio: {sharpe}
• Max. Rückgang: {drawdown}%

_Das Modell analysiert täglich 7 ETFs und Rohstoffe._
```

**For EXPLAIN_BACKTEST:**
Send this fixed German text (can be expanded):
```
📈 *Was ist Backtesting?*

Backtesting bedeutet, dass wir unser Modell auf *historischen Kursdaten* testen — also schauen, wie es in der Vergangenheit abgeschnitten hätte.

*Die wichtigsten Kennzahlen:*

🔹 *Sharpe-Ratio:* Misst Rendite im Verhältnis zum Risiko. Über 1.0 ist gut, über 2.0 ist sehr gut.

🔹 *Maximaler Rückgang (Max Drawdown):* Der größte Verlust vom Höchststand bis zum Tiefpunkt. Zum Beispiel -10% bedeutet, der Wert ist zwischenzeitlich um 10% gefallen.

🔹 *Trefferquote (Win Rate):* Wie oft lag das Modell richtig? Bei 60% war die Vorhersage in 6 von 10 Fällen korrekt.

Das Modell nutzt statistische Methoden (ARIMA für Kursvorhersagen, GARCH für Volatilität) und Künstliche Intelligenz, um Kauf/Verkauf/Halten-Empfehlungen zu geben.

_Fragen? Einfach schreiben!_ 😊
```

**Sender authorization:**
- Josef's number must be configured in a simple config: `data/josef_config.json`
  ```json
  { "whatsapp_number": "+49XXXXXXXXXX" }
  ```
- If sender number not matching Josef's config, ignore silently (return without action)

**Note:** Josef's phone number is not known yet. The config file should be created with a placeholder. Simon will update it.

### Feature 4: OpenClaw WhatsApp Routing (Hector)

The existing OpenClaw config must route Josef's WhatsApp messages to the handler. However, since Josef's number is not yet known, the routing approach is:

**Option A (preferred):** Create a cron job that runs `josef_handler.py` from the main session when a message arrives. This is handled by Homer (main agent) catching the inbound message and calling `python3 josef_handler.py --message "$MSG" --sender "$SENDER"`.

Actually, the cleanest approach: **Homer (main session) is already receiving all WhatsApp messages**. When a message arrives from Josef's number, Homer should invoke `josef_handler.py`.

BUT: this requires Homer to detect Josef's messages. A cleaner approach:

**Create a `josef_trigger.py` wrapper:**
```python
# josef_trigger.py --message "..." --sender "+49XXXXXXXXXX"
# If sender matches josef_config.json, run josef_handler
# Exit 0 always (don't crash Homer's flow)
```

Hector's actual tasks:
1. Verify that `openclaw message send --channel whatsapp --to <number> --media <filepath>` works for image sending (check CLI help / existing codebase usage)
2. Update `send_whatsapp.py` to support `--media` parameter (image attachment)
3. Add to MEMORY.md: Josef handler is set up, his number needs to be configured in `roboadvisor/data/josef_config.json`
4. Check that `python3 -m http.server` approach works on this machine or suggest `http-server` npm alternative
5. Git add/commit all new files

### Feature 5: Tests (Odysseus)

Write `tests/test_josef_handler.py`:
- Test intent detection: each keyword category maps to correct intent
- Test German text summary composition with mock dashboard_data.json
- Test that unknown sender returns without sending
- Test backtest explanation contains key German terms
- Mock `subprocess.run` for WhatsApp send calls (verify correct args)
- Test screenshot path resolution

### Feature 6: Review (Agamemnon)

Review all new code for:
- BLOCK: Any hardcoded phone numbers in production code (must use config file)
- BLOCK: Any English strings leaking into German dashboard (`index.de.html`/`app.de.js`)
- BLOCK: Screenshot script not cleaning up HTTP server on error (must use try/finally)
- WARN: Missing error handling in `josef_handler.py` (failed screenshot = fallback text only)
- WARN: Language detection: ensure German keywords are lowercase-normalized
- NIT: Code comments should be in English (per codebase convention)

---

## Agent Task Summary

| Agent | Tasks |
|-------|-------|
| **Achilles** | 1. `dashboard/index.de.html` + `dashboard/app.de.js` (German dashboard) 2. `screenshot_dashboard_de.js` 3. `take_screenshot_de.py` 4. `josef_handler.py` with full logic |
| **Odysseus** | Write `tests/test_josef_handler.py` with edge cases |
| **Agamemnon** | Review all Achilles output: BLOCK/WARN/NIT |
| **Hector** | `send_whatsapp.py` media support, `data/josef_config.json`, git commit |

---

## Constraints

- **German-only responses:** Josef speaks no English. Every response must be 100% German.
- **No new dependencies** unless absolutely necessary (use stdlib + existing packages)
- **Puppeteer** is already installed in `roboadvisor/node_modules/`
- **Port 7824** for the German dashboard HTTP server (avoid collision with 7823)
- **Image format:** PNG screenshot, sent as document to avoid WhatsApp compression
- **Error resilience:** If screenshot fails, still send the text summary
- **Josef's number placeholder:** `+49XXXXXXXXXX` in `data/josef_config.json` — Simon will set the real number

---

## Deliverables

After all agents complete, Homer will:
1. Integrate outputs, resolve any conflicts
2. Run `python3 tests/test_josef_handler.py` 
3. Test the screenshot pipeline manually
4. Update MEMORY.md with Josef handler details
5. Notify Simon via WhatsApp that it's done

---

## Architecture Overview

```
WhatsApp inbound message from Josef
          ↓
Homer (main session) receives it
          ↓  
python3 josef_trigger.py --message "..." --sender "+49XXXXXXXXXX"
          ↓
josef_handler.py: detect intent
          ↓
  ┌─────────────────────────────────────┐
  │ STATUS_REQUEST                       │
  │  export_dashboard.py (if stale)      │
  │  take_screenshot_de.py               │
  │  _compose_german_summary(data)       │
  │  send image + text via WhatsApp      │
  └─────────────────────────────────────┘
  OR
  ┌─────────────────────────────────────┐
  │ EXPLAIN_BACKTEST                    │
  │  send fixed German explanation text │
  └─────────────────────────────────────┘
```
