# WhatsApp Agent — Design Doc

> **Status:** Design only — not yet implemented. This document describes how Homer should orchestrate the daily WhatsApp flow with Simon once the integration is wired up.

---

## Overview

After `run.py` completes each trading day, Homer:
1. Reads the daily report and `dashboard_data.json`
2. Formats a concise WhatsApp message with signals and highlights
3. Sends it to Simon at **+4915789623707**
4. Listens for trade execution replies
5. Parses confirmed trades, updates `trades.json`, re-runs the dashboard, regenerates the PDF, and sends it back

---

## 1. Daily Push Flow

**Trigger:** `run.py` exits successfully (exit code 0) on a trading day.

**Steps:**
1. Load `dashboard/dashboard_data.json` (latest suggestions and portfolio state)
2. For each ticker, extract: quant signal, LLM recommendation, conflict flag (quant ≠ LLM), and the top 1–2 sentences of LLM rationale
3. Run `pdf_report.py --report reports/YYYY-MM-DD.md --dashboard-json dashboard/dashboard_data.json` to generate the PDF
4. Compose the WhatsApp message (see format below)
5. Send message + PDF attachment to Simon via the WhatsApp plugin

**Conflict detection:** A conflict exists when `quant_signal` and `llm_recommendation` differ and both are non-null.

---

## 2. Message Format (Daily Suggestion)

Use **exactly** this template:

```
📈 *Roboadvisor Daily — {date}*

*Portfolio Signals:*
{per-ticker table as text}

*Highlights:*
{top 2 LLM rationale bullets}

🚩 *Conflicts:* {list tickers where quant ≠ LLM, or "None"}

_Reply with executed trades in format:_
`BUY TICKER SHARES at PRICE`
or `SELL TICKER SHARES at PRICE`
_to log them and get an updated dashboard PDF._
```

**Per-ticker table (plain text, WhatsApp-friendly — no markdown tables):**
```
AMUN.PA  | Quant: HOLD | LLM: HOLD | ✅ Aligned
EUN4.DE  | Quant: BUY  | LLM: HOLD | ⚠️ Conflict
```

**Top 2 LLM rationale bullets** — extract the first 1–2 sentences of `llm_rationale` for each ticker where the LLM signal is actionable (BUY or SELL), or the most conflicted ticker. Format as:
```
• AMUN.PA: "The Y%-Filter confirms an upward trend..."
• EUN4.DE: "GARCH volatility is elevated at 21.6%, suggesting caution..."
```

---

## 3. Execution Report Flow

**Trigger:** Simon replies to Homer's WhatsApp message with a trade execution.

**Steps:**
1. Parse the incoming message with the regex patterns below
2. If parsing succeeds, append to `trades.json`:
   ```json
   {
     "date": "YYYY-MM-DD",
     "ticker": "IQQH.DE",
     "action": "BUY",
     "shares": 10,
     "price": 8.80,
     "currency": "EUR",
     "source": "whatsapp"
   }
   ```
3. Re-run: `python3 export_dashboard.py --trades trades.json`
4. Re-run: `python3 pdf_report.py --report reports/YYYY-MM-DD.md --dashboard-json dashboard/dashboard_data.json`
5. Send updated PDF back to Simon with a confirmation message:
   ```
   ✅ Logged: BUY IQQH.DE 10 shares @ €8.80
   Dashboard updated. PDF attached.
   ```
6. If parsing fails, reply:
   ```
   ❓ Couldn't parse trade. Use format:
   BUY TICKER SHARES at PRICE
   e.g. BUY IQQH.DE 10 at 8.80
   ```

---

## 4. Parsing Rules (Regex)

Match case-insensitively. Support both `.` and `,` as decimal separator.

### Primary pattern (strict)
```
^(BUY|SELL)\s+([A-Z0-9.]+)\s+([\d.,]+)\s+(?:shares?\s+)?at\s+[€$£]?([\d.,]+)
```

**Named groups:**
- Group 1: `action` → `BUY` or `SELL`
- Group 2: `ticker` → e.g. `IQQH.DE`
- Group 3: `shares` → e.g. `10` or `1.5`
- Group 4: `price` → e.g. `8.80` or `8,80`

**Normalize price/shares:** replace `,` with `.` before `float()` conversion.

### Fallback pattern (natural language)
```
(?:bought|sold|purchased)\s+([\d.,]+)\s+(?:shares?\s+of\s+)?([A-Z0-9.]+)\s+(?:at|for|@)\s+[€$£]?([\d.,]+)
```

**Named groups:**
- Group 1: `shares`
- Group 2: `ticker`
- Group 3: `price`
- `action` inferred from `bought/purchased` → `BUY`, `sold` → `SELL`

### Example matches
| Input | Action | Ticker | Shares | Price |
|-------|--------|--------|--------|-------|
| `BUY IQQH.DE 10 at €8.80` | BUY | IQQH.DE | 10 | 8.80 |
| `SELL AMUN.PA 2 shares at 73.05` | SELL | AMUN.PA | 2 | 73.05 |
| `bought 5 shares of EUN4.DE at 106,31` | BUY | EUN4.DE | 5 | 106.31 |
| `sold 1.5 AMUN.PA at €72` | SELL | AMUN.PA | 1.5 | 72.00 |

---

## 5. Cron Schedule

All times in **CET (Europe/Berlin)**. Only run on trading days (Mon–Fri, excluding German public holidays — skip implementation of holiday detection initially; filter to weekdays only).

```cron
# Run roboadvisor pipeline at 18:30 CET on weekdays
30 18 * * 1-5  cd /Users/homer-service/.openclaw/workspace/roboadvisor && python3 run.py >> logs/run.log 2>&1

# Generate PDF immediately after (18:35 CET buffer)
35 18 * * 1-5  cd /Users/homer-service/.openclaw/workspace/roboadvisor && python3 pdf_report.py --dashboard-json dashboard/dashboard_data.json >> logs/pdf.log 2>&1

# Send WhatsApp message by 19:00 CET
# (Implemented via Homer OpenClaw skill or script that reads today's report + dashboard JSON)
00 19 * * 1-5  cd /Users/homer-service/.openclaw/workspace/roboadvisor && python3 send_whatsapp.py >> logs/whatsapp.log 2>&1
```

> **Note:** `send_whatsapp.py` does not yet exist — it is the next implementation step. It should use the OpenClaw WhatsApp plugin (or `whatsapp-web.js` / Meta Cloud API) to send the formatted message and PDF.

---

## 6. Implementation Notes

- **WhatsApp plugin:** Homer has WhatsApp channel access via OpenClaw. The `message` tool with `target="+4915789623707"` should work for outbound messages. For inbound parsing, a webhook or polling loop on the WhatsApp channel is needed.
- **Idempotency:** Guard against sending duplicate daily messages. Track last-sent date in `logs/whatsapp_state.json`.
- **PDF attachment:** Send as a document (not image) to avoid compression. Use `media=` or `buffer=` parameter with the PDF path.
- **Error handling:** If `run.py` fails, do not send a WhatsApp message. Optionally send an error ping: `⚠️ Roboadvisor pipeline failed today. Check logs.`
- **Timezone:** All date calculations use `Europe/Berlin`. Use `pytz` or `zoneinfo` (Python 3.9+).

---

## 7. Future Enhancements

- [ ] Holiday calendar integration (skip German Feiertage)
- [ ] Multi-ticker order parsing in a single message
- [ ] Two-way confirmation: Simon can reply `confirm` to approve a suggested trade automatically
- [ ] Weekly summary on Sundays (portfolio performance recap)
- [ ] Alert thresholds: immediate ping if a position drops >5% intraday
