#!/usr/bin/env python3
"""
evening_run.py
--------------
Evening pipeline: fetch closing prices, grade morning signals,
compute P&L, export dashboard, send WhatsApp digest.

Usage::

    python3 evening_run.py [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_config
from data.db import get_connection, init_db, get_prices
from data.prices import fetch_prices
from run import load_portfolio, prices_to_series

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SNAPSHOTS_DIR = PROJECT_ROOT / "snapshots"
LOGS_DIR = PROJECT_ROOT / "logs"
STATE_FILE = LOGS_DIR / "evening_state.json"
PORTFOLIO_PATH = PROJECT_ROOT / "portfolio.json"
WHATSAPP_TO = "+4915789623707"

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

BERLIN = ZoneInfo("Europe/Berlin")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_str() -> str:
    return datetime.now(BERLIN).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _send_whatsapp(message: str, dry_run: bool = False) -> None:
    if dry_run:
        print("\n--- DRY RUN: WhatsApp message ---")
        print(message)
        print("--- END ---\n")
        return
    try:
        result = subprocess.run(
            [
                "openclaw", "message", "send",
                "--channel", "whatsapp",
                "--target", WHATSAPP_TO,
                "--message", message,
            ],
            check=False, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"[evening] WARNING: WhatsApp send failed (rc={result.returncode}): {result.stderr}")
        else:
            print("[evening] WhatsApp message sent.")
    except Exception as exc:
        print(f"[evening] ERROR sending WhatsApp: {exc}")


def _check_idempotency(force: bool) -> bool:
    if force:
        return False
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            if state.get("date") == _today_str():
                print(f"[evening] Already ran today ({_today_str()}). Use --force to re-run.")
                return True
        except Exception:
            pass
    return False


def _save_state() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "date": _today_str(),
        "run_at": _now_iso(),
    }, indent=2))


def _export_dashboard() -> None:
    try:
        result = subprocess.run(
            [sys.executable, "export_dashboard.py", "--trades", "trades.json"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"[evening] WARNING: export_dashboard failed: {result.stderr[:300]}")
        else:
            print("[evening] Dashboard exported.")
    except Exception as exc:
        print(f"[evening] ERROR exporting dashboard: {exc}")


def _load_morning_snapshot(today_str: str) -> Optional[dict]:
    path = SNAPSHOTS_DIR / f"{today_str}-morning.json"
    if not path.exists():
        print(f"[evening] No morning snapshot found at {path}")
        return None
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        print(f"[evening] ERROR reading morning snapshot: {exc}")
        return None


def _get_latest_price(conn, ticker: str, today_str: str) -> tuple[Optional[float], Optional[str]]:
    """Get the latest closing price from DB for a ticker.

    Returns (price, date_str).  *date_str* may differ from *today_str*
    on market holidays / weekends.
    """
    rows = get_prices(conn, ticker, "2000-01-01", today_str)
    if not rows:
        return None, None
    row = rows[-1]
    return float(row["close"]), row["date"]


def _grade_signal(signal: str, pct_change: float) -> str:
    """Grade a morning signal against actual price change."""
    signal = signal.upper()
    if signal == "BUY":
        if pct_change > 0:
            return "✅ Correct"
        elif pct_change < 0:
            return "❌ Wrong"
        else:
            return "➖ Neutral"
    elif signal == "SELL":
        if pct_change < 0:
            return "✅ Correct"
        elif pct_change > 0:
            return "❌ Wrong"
        else:
            return "➖ Neutral"
    else:  # HOLD
        if abs(pct_change) < 0.5:
            return "✅ Correct"
        else:
            return "❌ Wrong"


def _compose_evening_message(
    today_str: str,
    portfolio: list[dict],
    closing_prices: dict[str, float],
    snapshot: Optional[dict],
    total_pnl: float,
    total_pnl_pct: float,
    stale_price_dates: Optional[dict[str, str]] = None,
    pnl_is_estimated: bool = False,
) -> str:
    """Build the evening WhatsApp message."""
    stale_price_dates = stale_price_dates or {}
    lines = [f"🌆 *Roboadvisor Evening — {today_str}*", ""]

    pnl_sign = "+" if total_pnl >= 0 else ""
    pnl_pct_sign = "+" if total_pnl_pct >= 0 else ""
    pnl_prefix = "~" if pnl_is_estimated else ""
    lines.append(f"*Portfolio Day P&L:* {pnl_prefix}{pnl_sign}€{total_pnl:.2f} ({pnl_pct_sign}{total_pnl_pct:.2f}%)")
    if pnl_is_estimated:
        lines.append("_(no morning snapshot — estimated from prev. close)_")
    lines.append("")

    tickers_data = snapshot.get("tickers", {}) if snapshot else {}
    correct_count = 0
    total_graded = 0
    conflicts_today = 0

    if tickers_data:
        lines.append("*Signal Retrospective:*")
        for entry in portfolio:
            ticker = entry["ticker"]
            td = tickers_data.get(ticker, {})
            morning_price = td.get("morning_price")
            closing_price = closing_prices.get(ticker)

            # Determine morning signal (prefer LLM, fallback to quant)
            morning_signal = td.get("quant_signal", "HOLD")
            llm_rec = td.get("llm_recommendation")

            # Check for conflicts
            if llm_rec and llm_rec.upper() != morning_signal.upper():
                conflicts_today += 1

            # Grade the signal the user actually saw: LLM if available, else quant
            effective_signal = llm_rec if llm_rec else morning_signal

            # Stale price annotation
            stale_note = f" (est. {stale_price_dates[ticker]})" if ticker in stale_price_dates else ""

            if morning_price and closing_price and morning_price > 0:
                pct_change = ((closing_price - morning_price) / morning_price) * 100
                grade = _grade_signal(effective_signal, pct_change)
                pct_str = f"{'+' if pct_change >= 0 else ''}{pct_change:.2f}%"
                lines.append(f"{ticker}{stale_note}  | ☀️ {effective_signal} | Actual: {pct_str} | {grade}")
                total_graded += 1
                if "Correct" in grade:
                    correct_count += 1
            else:
                lines.append(f"{ticker}{stale_note}  | ☀️ {effective_signal} | Actual: N/A | ➖ Neutral")
    else:
        lines.append("_No morning snapshot — skipping grading_")

    lines.append("")
    if total_graded > 0:
        lines.append(f"*Score: {correct_count}/{total_graded} correct* ({conflicts_today} conflicts today)")
    else:
        lines.append(f"*Score: N/A* ({conflicts_today} conflicts today)")

    lines.append("")
    lines.append("_Dashboard updated. Reply with trades to log._")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Roboadvisor evening pipeline")
    parser.add_argument("--force", action="store_true", help="Force re-run even if already ran today")
    parser.add_argument("--dry-run", action="store_true", help="Skip WhatsApp send, print message instead")
    parser.add_argument("--db", type=str, default="roboadvisor.db", help="SQLite DB path")
    args = parser.parse_args()

    today_str = _today_str()
    print(f"\n{'='*60}")
    print(f"  Roboadvisor Evening Run — {today_str}")
    print(f"{'='*60}\n")

    # Idempotency
    if _check_idempotency(args.force):
        return

    # Config
    try:
        cfg = load_config()
    except SystemExit:
        _send_whatsapp("⚠️ Evening pipeline failed: config error. Check logs.", dry_run=args.dry_run)
        raise

    # Database
    conn = get_connection(args.db)
    init_db(conn)

    # Portfolio
    if not PORTFOLIO_PATH.exists():
        print(f"ERROR: portfolio not found: {PORTFOLIO_PATH}", file=sys.stderr)
        sys.exit(1)
    portfolio = load_portfolio(PORTFOLIO_PATH)
    print(f"[evening] Loaded {len(portfolio)} positions\n")

    # Fetch closing prices
    closing_prices: dict[str, float] = {}
    stale_price_dates: dict[str, str] = {}  # ticker -> actual date when price != today
    for entry in portfolio:
        ticker = entry.get("ticker")
        if not ticker:
            continue
        print(f"[evening] Fetching prices for {ticker}...")
        try:
            fetch_prices(ticker, conn)
        except Exception as exc:
            print(f"[evening] WARNING: price fetch failed for {ticker}: {exc}")

        price, price_date = _get_latest_price(conn, ticker, today_str)
        if price is not None:
            closing_prices[ticker] = price
            stale_tag = "" if price_date == today_str else f" (est. {price_date})"
            # Track stale dates so the message can annotate them
            if price_date != today_str:
                stale_price_dates[ticker] = price_date
            print(f"[evening]   Latest price: {price:.4f}{stale_tag}")
        else:
            print(f"[evening]   No price data for {ticker}")

    # Load morning snapshot
    snapshot = _load_morning_snapshot(today_str)
    tickers_data = snapshot.get("tickers", {}) if snapshot else {}

    # Compute P&L
    total_pnl = 0.0
    total_morning_value = 0.0
    pnl_is_estimated = False

    for entry in portfolio:
        ticker = entry.get("ticker")
        shares = entry.get("shares", 0)
        if not ticker or not shares:
            continue

        closing_price = closing_prices.get(ticker)
        morning_price = tickers_data.get(ticker, {}).get("morning_price") if tickers_data else None

        # Fall back to previous close if no morning snapshot
        if morning_price is None and closing_price is not None:
            pnl_is_estimated = True
            # Use the second-to-last price as a rough fallback
            rows = get_prices(conn, ticker, "2000-01-01", today_str)
            if len(rows) >= 2:
                morning_price = float(rows[-2]["close"])

        if morning_price is not None and closing_price is not None:
            ticker_pnl = shares * (closing_price - morning_price)
            total_pnl += ticker_pnl
            total_morning_value += shares * morning_price

    total_pnl = round(total_pnl, 2)
    total_pnl_pct = (total_pnl / total_morning_value * 100) if total_morning_value > 0 else 0.0

    conn.close()

    # Export dashboard
    _export_dashboard()

    # Compose and send message
    message = _compose_evening_message(
        today_str, portfolio, closing_prices, snapshot, total_pnl, total_pnl_pct,
        stale_price_dates=stale_price_dates, pnl_is_estimated=pnl_is_estimated,
    )
    _send_whatsapp(message, dry_run=args.dry_run)

    # Save state
    _save_state()

    print(f"\n[evening] Done.\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[evening] FATAL: {exc}", file=sys.stderr)
        traceback.print_exc()
        try:
            _send_whatsapp(f"⚠️ Evening pipeline failed. Check logs.\n{exc}")
        except Exception:
            pass
        sys.exit(1)
