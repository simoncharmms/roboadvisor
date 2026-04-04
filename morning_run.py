#!/usr/bin/env python3
"""
morning_run.py
--------------
Morning pipeline: fetch prices + news, run quant models + LLM,
save snapshot, export dashboard, send WhatsApp briefing.

Usage::

    python3 morning_run.py [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_config
from data.db import get_connection, init_db, get_prices, get_news as db_get_news
from data.prices import fetch_prices
from data.news import fetch_news
from run import load_portfolio, analyse_ticker, persist_signals, persist_backtest, prices_to_series
from llm.analyzer import LLMAnalyzer

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SNAPSHOTS_DIR = PROJECT_ROOT / "snapshots"
LOGS_DIR = PROJECT_ROOT / "logs"
STATE_FILE = LOGS_DIR / "morning_state.json"
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
    """Send a WhatsApp message via openclaw CLI."""
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
            print(f"[morning] WARNING: WhatsApp send failed (rc={result.returncode}): {result.stderr}")
        else:
            print("[morning] WhatsApp message sent.")
    except Exception as exc:
        print(f"[morning] ERROR sending WhatsApp: {exc}")


def _check_idempotency(force: bool) -> bool:
    """Return True if we should skip (already ran today)."""
    if force:
        return False
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            if state.get("date") == _today_str():
                print(f"[morning] Already ran today ({_today_str()}). Use --force to re-run.")
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


def _save_snapshot(today_str: str, ticker_data: dict) -> Path:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "date": today_str,
        "run_at": _now_iso(),
        "tickers": ticker_data,
    }
    path = SNAPSHOTS_DIR / f"{today_str}-morning.json"
    # Write atomically: temp file → rename, so a crash mid-write never leaves
    # a corrupt snapshot that the evening run silently fails to parse.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    tmp.replace(path)
    print(f"[morning] Snapshot saved: {path}")
    return path


def _export_dashboard() -> None:
    try:
        result = subprocess.run(
            [sys.executable, "export_dashboard.py", "--trades", "trades.json"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"[morning] WARNING: export_dashboard failed: {result.stderr[:300]}")
        else:
            print("[morning] Dashboard exported.")
    except Exception as exc:
        print(f"[morning] ERROR exporting dashboard: {exc}")


def _get_quant_signal(result: dict) -> str:
    """Extract the quant signal from analyse_ticker output."""
    yf = result.get("y_filter") or {}
    return yf.get("signal", "HOLD")


def _compose_morning_message(
    today_str: str,
    portfolio: list[dict],
    ticker_data: dict,
) -> str:
    """Build the morning WhatsApp message."""
    lines = [f"☀️ *Roboadvisor Morning — {today_str}*", ""]
    lines.append("*Trade Suggestions:*")

    conflicts = []
    top_picks = []

    for entry in portfolio:
        ticker = entry["ticker"]
        td = ticker_data.get(ticker, {})
        q_signal = td.get("quant_signal", "—")
        l_rec = td.get("llm_recommendation")
        l_conf = td.get("llm_confidence")
        l_rationale = td.get("llm_rationale", "")

        if l_rec:
            aligned = q_signal.upper() == l_rec.upper()
            flag = "✅" if aligned else "⚠️"
            if not aligned:
                conflicts.append(ticker)
            conf_str = f" ({l_conf})" if l_conf else ""
            lines.append(f"{ticker}  | Quant: {q_signal} | LLM: {l_rec}{conf_str} | {flag}")
        else:
            lines.append(f"{ticker}  | Quant: {q_signal} | LLM: — | —")

        # Collect top picks: prioritise BUY/SELL or conflicts
        if l_rationale and (
            q_signal.upper() in ("BUY", "SELL")
            or (l_rec and l_rec.upper() in ("BUY", "SELL"))
            or ticker in conflicts
        ):
            first_sentence = l_rationale.split(". ")[0].rstrip(".")
            top_picks.append((ticker, first_sentence))

    lines.append("")
    lines.append("*Top Picks:*")
    if top_picks:
        for ticker, rationale in top_picks[:2]:
            lines.append(f'• {ticker}: "{rationale}"')
    else:
        lines.append("• No strong signals today")

    lines.append("")
    conflict_str = ", ".join(conflicts) if conflicts else "None"
    lines.append(f"🚩 *Conflicts:* {conflict_str}")
    lines.append("")
    lines.append("_Reply: BUY TICKER SHARES at PRICE_")
    lines.append("_or: SELL TICKER SHARES at PRICE_")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Roboadvisor morning pipeline")
    parser.add_argument("--force", action="store_true", help="Force re-run even if already ran today")
    parser.add_argument("--dry-run", action="store_true", help="Skip WhatsApp send, print message instead")
    parser.add_argument("--skip-news", action="store_true", help="Skip news fetching")
    parser.add_argument("--db", type=str, default="roboadvisor.db", help="SQLite DB path")
    args = parser.parse_args()

    today_str = _today_str()
    print(f"\n{'='*60}")
    print(f"  Roboadvisor Morning Run — {today_str}")
    print(f"{'='*60}\n")

    # Idempotency check
    if _check_idempotency(args.force):
        return

    # Load config
    try:
        cfg = load_config()
    except SystemExit:
        _send_whatsapp("⚠️ Morning pipeline failed: config error. Check logs.", dry_run=args.dry_run)
        raise

    # Database
    conn = get_connection(args.db)
    init_db(conn)

    # Portfolio
    if not PORTFOLIO_PATH.exists():
        print(f"ERROR: portfolio not found: {PORTFOLIO_PATH}", file=sys.stderr)
        sys.exit(1)
    portfolio = load_portfolio(PORTFOLIO_PATH)
    print(f"[morning] Loaded {len(portfolio)} positions\n")

    all_results: list[dict] = []
    ticker_data: dict = {}

    for entry in portfolio:
        ticker = entry.get("ticker")
        if not ticker:
            continue

        print(f"\n{'─'*50}")
        print(f"  Processing: {ticker}  ({entry.get('name', '')})")
        print(f"{'─'*50}")

        # Fetch prices
        try:
            fetch_prices(ticker, conn)
        except Exception as exc:
            print(f"[morning] ERROR fetching prices for {ticker}: {exc}")

        # Fetch news
        if not args.skip_news:
            try:
                fetch_news(ticker, conn, days=7, api_key=cfg.news_api_key)
            except Exception as exc:
                print(f"[morning] WARNING: news fetch failed for {ticker}: {exc}")

        # Run quant models
        try:
            result = analyse_ticker(ticker, conn, today_str)
        except Exception as exc:
            print(f"[morning] ERROR analysing {ticker}: {exc}")
            traceback.print_exc()
            continue
        all_results.append(result)

        # Persist signals & backtest
        try:
            persist_signals(conn, ticker, today_str, result)
        except Exception as exc:
            print(f"[morning] WARNING: persist signals failed for {ticker}: {exc}")

        bt = result.get("backtest")
        if bt:
            try:
                persist_backtest(conn, ticker, today_str, bt)
            except Exception as exc:
                print(f"[morning] WARNING: persist backtest failed for {ticker}: {exc}")

        # Build ticker snapshot data
        quant_signal = _get_quant_signal(result)
        td = {
            "morning_price": result.get("latest_price"),
            "quant_signal": quant_signal,
        }
        ticker_data[ticker] = td

    # LLM analysis
    llm_results: dict = {}
    if cfg.anthropic_api_key:
        print(f"\n[morning] Running LLM analysis...")
        try:
            analyzer = LLMAnalyzer(api_key=cfg.anthropic_api_key)
            news_by_ticker: dict[str, list] = {}
            for entry in portfolio:
                ticker = entry.get("ticker", "")
                if ticker:
                    news_by_ticker[ticker] = db_get_news(conn, ticker, days=7)

            total_value = sum(
                (r.get("latest_price") or 0) * next(
                    (e.get("shares", 0) for e in portfolio if e.get("ticker") == r["ticker"]), 0
                )
                for r in all_results
            )

            llm_results = analyzer.analyze_portfolio(
                portfolio=portfolio,
                all_quant_results=all_results,
                news_by_ticker=news_by_ticker,
                total_portfolio_value=total_value,
            )

            # Merge LLM results into ticker_data
            for ticker, llm_r in llm_results.items():
                if ticker in ticker_data and not llm_r.get("error"):
                    ticker_data[ticker]["llm_recommendation"] = llm_r.get("recommendation")
                    ticker_data[ticker]["llm_confidence"] = llm_r.get("confidence")
                    ticker_data[ticker]["llm_rationale"] = llm_r.get("rationale", "")

                    # Persist LLM signal to DB
                    try:
                        from data.db import upsert_signal
                        upsert_signal(
                            conn, ticker=ticker, date=today_str,
                            llm_recommendation=llm_r.get("recommendation"),
                            llm_confidence=llm_r.get("confidence"),
                            llm_rationale=llm_r.get("rationale"),
                            llm_quant_agreement=llm_r.get("quant_agreement"),
                        )
                        conn.commit()
                    except Exception as exc:
                        print(f"[morning] WARNING: persist LLM signal failed for {ticker}: {exc}")
        except Exception as exc:
            print(f"[morning] ERROR during LLM analysis: {exc}")
            traceback.print_exc()
    else:
        print("[morning] Skipping LLM analysis (ANTHROPIC_API_KEY not set).")

    conn.close()

    # Save snapshot
    _save_snapshot(today_str, ticker_data)

    # Export dashboard
    _export_dashboard()

    # Compose and send WhatsApp
    message = _compose_morning_message(today_str, portfolio, ticker_data)
    _send_whatsapp(message, dry_run=args.dry_run)

    # Save state (idempotency)
    _save_state()

    print(f"\n[morning] Done.\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[morning] FATAL: {exc}", file=sys.stderr)
        traceback.print_exc()
        # Try to notify
        try:
            _send_whatsapp(f"⚠️ Morning pipeline failed. Check logs.\n{exc}")
        except Exception:
            pass
        sys.exit(1)
