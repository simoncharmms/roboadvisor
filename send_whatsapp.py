"""
send_whatsapp.py
----------------
Daily WhatsApp notification for the roboadvisor pipeline.

Reads today's report + dashboard_data.json, composes a concise
signal summary message, and sends it + the PDF to Simon via
the OpenClaw WhatsApp channel using the `openclaw message` CLI.

Also handles incoming trade execution replies: when called with
--parse-trade "BUY IQQH.DE 10 at 8.80", it updates trades.json,
regenerates the dashboard JSON and PDF, and sends a confirmation.

Usage
-----
  # Daily send (called from cron at 19:00 CET)
  python3 send_whatsapp.py

  # Parse and log a trade reported by Simon
  python3 send_whatsapp.py --parse-trade "BUY IQQH.DE 10 at 8.80"

  # Dry run (print message without sending)
  python3 send_whatsapp.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
SIMON_PHONE  = "+4915789623707"
TRADES_FILE  = PROJECT_ROOT / "trades.json"
DASHBOARD_JSON = PROJECT_ROOT / "dashboard" / "dashboard_data.json"
REPORTS_DIR  = PROJECT_ROOT / "reports"
LOGS_DIR     = PROJECT_ROOT / "logs"
STATE_FILE   = LOGS_DIR / "whatsapp_state.json"

# ---------------------------------------------------------------------------
# Trade parsing
# ---------------------------------------------------------------------------

# Primary: BUY IQQH.DE 10 at €8.80  /  SELL AMUN.PA 2 shares at 73.05
_PRIMARY = re.compile(
    r"^(BUY|SELL)\s+([A-Z0-9.\-]+)\s+([\d.,]+)\s*(?:shares?\s+)?at\s+[€$£]?([\d.,]+)",
    re.IGNORECASE,
)

# Fallback: bought 5 shares of EUN4.DE at 106,31
_FALLBACK = re.compile(
    r"(?P<verb>bought|sold|purchased)\s+(?P<shares>[\d.,]+)\s+(?:shares?\s+of\s+)?(?P<ticker>[A-Z0-9.\-]+)\s+(?:at|for|@)\s+[€$£]?(?P<price>[\d.,]+)",
    re.IGNORECASE,
)


def _norm(s: str) -> float:
    """Normalise a price/shares string (comma or dot decimal) to float."""
    return float(s.replace(",", "."))


def parse_trade(text: str) -> Optional[dict]:
    """Parse a trade execution message from Simon.

    Parameters
    ----------
    text : str
        Free-form trade message, e.g. ``"BUY IQQH.DE 10 at €8.80"``

    Returns
    -------
    dict or None
        ``{action, ticker, shares, price}`` or None if unparseable.
    """
    m = _PRIMARY.match(text.strip())
    if m:
        return {
            "action": m.group(1).upper(),
            "ticker": m.group(2).upper(),
            "shares": _norm(m.group(3)),
            "price":  _norm(m.group(4)),
        }

    m = _FALLBACK.search(text)
    if m:
        verb = m.group("verb").lower()
        return {
            "action": "BUY" if verb in ("bought", "purchased") else "SELL",
            "ticker": m.group("ticker").upper(),
            "shares": _norm(m.group("shares")),
            "price":  _norm(m.group("price")),
        }

    return None


# ---------------------------------------------------------------------------
# trades.json helpers
# ---------------------------------------------------------------------------

def load_trades() -> list:
    """Load existing trades from trades.json."""
    if not TRADES_FILE.exists():
        return []
    with open(TRADES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("executed_trades", [])


def append_trade(trade: dict) -> None:
    """Append a new trade to trades.json.

    Parameters
    ----------
    trade : dict
        Parsed trade dict from :func:`parse_trade`.
    """
    trades = load_trades()
    today = date.today().isoformat()
    entry = {
        "date":            today,
        "ticker":          trade["ticker"],
        "action":          trade["action"],
        "shares":          trade["shares"],
        "price_per_share": trade["price"],
        "price_eur":       trade["price"],   # assume EUR; adjust manually if needed
        "currency":        "EUR",
        "total_eur":       round(trade["shares"] * trade["price"], 2),
        "note":            f"Reported via WhatsApp {today}",
    }
    trades.append(entry)
    with open(TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump({"executed_trades": trades}, f, indent=2)
    print(f"[whatsapp] Trade logged: {entry}")


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------

def build_signal_table(dashboard: dict) -> str:
    """Build a WhatsApp-friendly plain-text signal table.

    Parameters
    ----------
    dashboard : dict
        Parsed ``dashboard_data.json``.

    Returns
    -------
    str
    """
    suggestions = dashboard.get("suggestions", [])
    portfolio   = dashboard.get("portfolio", [])

    # Latest per ticker
    latest: dict[str, dict] = {}
    for s in suggestions:
        latest[s["ticker"]] = s

    lines = []
    for pos in portfolio:
        t = pos["ticker"]
        s = latest.get(t, {})
        q = s.get("quant_signal") or "—"
        l = s.get("llm_recommendation") or "—"
        conflict = q not in ("—", None) and l not in ("—", None) and q != l
        flag = " ⚠️" if conflict else " ✅"
        lines.append(f"{t:<10} Quant: {q:<5} LLM: {l:<5}{flag}")

    return "\n".join(lines) if lines else "(no signals)"


def build_highlights(dashboard: dict) -> str:
    """Extract top 2 LLM rationale bullets from actionable or conflicted signals.

    Parameters
    ----------
    dashboard : dict

    Returns
    -------
    str
    """
    suggestions = dashboard.get("suggestions", [])
    latest: dict[str, dict] = {}
    for s in suggestions:
        latest[s["ticker"]] = s

    candidates = []
    for t, s in latest.items():
        q = s.get("quant_signal", "HOLD")
        l = s.get("llm_recommendation", "HOLD")
        rationale = s.get("llm_rationale", "")
        if not rationale:
            continue
        # Priority: conflicts first, then actionable signals
        priority = 0 if (q != l and q and l) else (1 if l in ("BUY","SELL") else 2)
        candidates.append((priority, t, rationale))

    candidates.sort(key=lambda x: x[0])
    bullets = []
    for _, ticker, rationale in candidates[:2]:
        # First sentence only
        sentence = rationale.split(".")[0].strip() + "."
        bullets.append(f"• {ticker}: \"{sentence}\"")

    return "\n".join(bullets) if bullets else "• No actionable highlights today."


def build_conflicts(dashboard: dict) -> str:
    """List tickers where quant and LLM disagree.

    Parameters
    ----------
    dashboard : dict

    Returns
    -------
    str
    """
    suggestions = dashboard.get("suggestions", [])
    latest: dict[str, dict] = {}
    for s in suggestions:
        latest[s["ticker"]] = s

    conflicts = [
        f"{t} (Quant:{s.get('quant_signal','?')} vs LLM:{s.get('llm_recommendation','?')})"
        for t, s in latest.items()
        if s.get("quant_signal") and s.get("llm_recommendation")
        and s["quant_signal"] != s["llm_recommendation"]
    ]
    return ", ".join(conflicts) if conflicts else "None"


def compose_daily_message(dashboard: dict, today: str) -> str:
    """Compose the daily WhatsApp message.

    Parameters
    ----------
    dashboard : dict
    today     : str  – ISO date

    Returns
    -------
    str
    """
    table      = build_signal_table(dashboard)
    highlights = build_highlights(dashboard)
    conflicts  = build_conflicts(dashboard)

    return (
        f"📈 *Roboadvisor Daily — {today}*\n\n"
        f"*Portfolio Signals:*\n{table}\n\n"
        f"*Highlights:*\n{highlights}\n\n"
        f"🚩 *Conflicts:* {conflicts}\n\n"
        f"_Reply with executed trades in format:_\n"
        f"`BUY TICKER SHARES at PRICE`\n"
        f"or `SELL TICKER SHARES at PRICE`\n"
        f"_to log them and get an updated dashboard PDF._"
    )


# ---------------------------------------------------------------------------
# OpenClaw send helpers
# ---------------------------------------------------------------------------

def _openclaw_send(message: str, pdf_path: Optional[Path] = None, dry_run: bool = False) -> bool:
    """Send a WhatsApp message (and optional PDF) via openclaw CLI.

    Parameters
    ----------
    message  : str
    pdf_path : Path, optional
    dry_run  : bool

    Returns
    -------
    bool  – True on success
    """
    if dry_run:
        print("\n[DRY RUN] Would send to", SIMON_PHONE)
        print("─" * 60)
        print(message)
        if pdf_path:
            print(f"\n[DRY RUN] + PDF attachment: {pdf_path}")
        print("─" * 60)
        return True

    try:
        # Text message
        cmd = [
            "openclaw", "message", "send",
            "--channel", "whatsapp",
            "--to", SIMON_PHONE,
            "--message", message,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"[whatsapp] ERROR sending message: {result.stderr}", file=sys.stderr)
            return False
        print("[whatsapp] Message sent.")

        # PDF attachment
        if pdf_path and pdf_path.exists():
            cmd_pdf = [
                "openclaw", "message", "send",
                "--channel", "whatsapp",
                "--to", SIMON_PHONE,
                "--media", str(pdf_path),
                "--message", f"📄 Full report: {pdf_path.name}",
                "--as-document",
            ]
            r2 = subprocess.run(cmd_pdf, capture_output=True, text=True, timeout=60)
            if r2.returncode != 0:
                print(f"[whatsapp] WARNING: PDF send failed: {r2.stderr}", file=sys.stderr)
            else:
                print(f"[whatsapp] PDF sent: {pdf_path.name}")

        return True

    except Exception as exc:
        print(f"[whatsapp] ERROR: {exc}", file=sys.stderr)
        return False


def _regenerate_dashboard_and_pdf(today: str) -> Optional[Path]:
    """Re-run export_dashboard.py and pdf_report.py.

    Parameters
    ----------
    today : str  – ISO date

    Returns
    -------
    Path or None
        Path to the generated PDF, or None on failure.
    """
    try:
        r1 = subprocess.run(
            [sys.executable, "export_dashboard.py", "--trades", str(TRADES_FILE)],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
        )
        if r1.returncode != 0:
            print(f"[whatsapp] export_dashboard.py failed: {r1.stderr}", file=sys.stderr)

        pdf_path = REPORTS_DIR / f"{today}.pdf"
        r2 = subprocess.run(
            [
                "/opt/homebrew/bin/python3.12", "pdf_report.py",
                "--report", str(REPORTS_DIR / f"{today}.md"),
                "--dashboard-json", str(DASHBOARD_JSON),
                "--out", str(pdf_path),
            ],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
        )
        if r2.returncode != 0:
            print(f"[whatsapp] pdf_report.py failed: {r2.stderr}", file=sys.stderr)
            return None

        return pdf_path if pdf_path.exists() else None

    except Exception as exc:
        print(f"[whatsapp] Regeneration failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# State tracking (idempotency)
# ---------------------------------------------------------------------------

def _already_sent_today(today: str) -> bool:
    """Check if the daily message was already sent today."""
    if not STATE_FILE.exists():
        return False
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    return state.get("last_sent_date") == today


def _mark_sent(today: str) -> None:
    """Record that the daily message was sent today."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_sent_date": today, "sent_at": datetime.utcnow().isoformat()}, f)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Roboadvisor WhatsApp sender")
    parser.add_argument("--parse-trade", metavar="TEXT",
                        help="Parse and log a trade execution message, then send updated PDF")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print message without sending")
    parser.add_argument("--force", action="store_true",
                        help="Send even if already sent today")
    args = parser.parse_args()

    today = date.today().isoformat()

    # ── Trade execution reply mode ──────────────────────────────────────
    if args.parse_trade:
        trade = parse_trade(args.parse_trade)
        if not trade:
            msg = (
                "❓ Couldn't parse trade. Use format:\n"
                "BUY TICKER SHARES at PRICE\n"
                "e.g. BUY IQQH.DE 10 at 8.80"
            )
            print(msg)
            _openclaw_send(msg, dry_run=args.dry_run)
            sys.exit(1)

        append_trade(trade)
        pdf_path = _regenerate_dashboard_and_pdf(today)
        confirm_msg = (
            f"✅ Logged: {trade['action']} {trade['ticker']} "
            f"{trade['shares']} shares @ {trade['price']:.4f}\n"
            f"Dashboard updated. PDF attached."
        )
        _openclaw_send(confirm_msg, pdf_path=pdf_path, dry_run=args.dry_run)
        sys.exit(0)

    # ── Daily push mode ─────────────────────────────────────────────────
    if _already_sent_today(today) and not args.force:
        print(f"[whatsapp] Already sent today ({today}). Use --force to resend.")
        sys.exit(0)

    if not DASHBOARD_JSON.exists():
        print(f"[whatsapp] ERROR: {DASHBOARD_JSON} not found. Run export_dashboard.py first.", file=sys.stderr)
        sys.exit(1)

    with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
        dashboard = json.load(f)

    message = compose_daily_message(dashboard, today)
    pdf_path = REPORTS_DIR / f"{today}.pdf"
    if not pdf_path.exists():
        pdf_path = None

    ok = _openclaw_send(message, pdf_path=pdf_path, dry_run=args.dry_run)

    if ok and not args.dry_run:
        _mark_sent(today)
        print(f"[whatsapp] Daily message sent and state recorded for {today}.")


if __name__ == "__main__":
    main()
