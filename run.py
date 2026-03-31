"""
run.py
------
Main entry point for the roboadvisor quant pipeline.

Workflow
--------
1. Load ``portfolio.json``.
2. For each ticker:
   a. Fetch / update OHLCV prices from yfinance.
   b. Fetch / update news from NewsAPI.
   c. Run Y%-Filter, ARIMA, GARCH, backtest.
   d. Persist signals to SQLite.
3. Generate a dated Markdown report in ``reports/YYYY-MM-DD.md``.

Usage
-----
::

    python run.py [--portfolio portfolio.json] [--db roboadvisor.db]

Environment variables (see .env.example):
    NEWS_API_KEY    – required for news fetching
    FINANCE_API_KEY – required (validated on startup)
    OPENAI_API_KEY  – optional, enables LLM recommendations
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Bootstrap: ensure the project root is on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Internal imports (after path setup)
# ---------------------------------------------------------------------------
from utils.config import load_config
from data.db import (
    get_connection,
    init_db,
    get_prices,
    get_last_price_date,
    upsert_signal,
    log_backtest_result,
)
from data.prices import fetch_prices
from data.news import fetch_news
from models.y_filter import y_filter
from models.arima_forecast import arima_forecast
from models.garch_copula import garch_copula_analysis
from models.backtest import backtest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_VERSION = "1.0"
REPORTS_DIR = PROJECT_ROOT / "reports"
DEFAULT_PORTFOLIO = PROJECT_ROOT / "portfolio.json"
DISCLAIMER = (
    "> **Disclaimer:** This report is generated automatically by a quantitative "
    "model and is provided for informational purposes only. It does not constitute "
    "financial advice. Past performance is not indicative of future results. "
    "Always consult a qualified financial advisor before making investment decisions."
)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def load_portfolio(path: Path) -> list[dict]:
    """Load the portfolio configuration from a JSON file.

    Parameters
    ----------
    path : Path
        Path to ``portfolio.json``.

    Returns
    -------
    list of dict
        Each entry has keys: ``ticker``, ``name``, ``shares``, etc.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("portfolio", [])


def prices_to_series(rows) -> pd.Series:
    """Convert SQLite price rows to a pandas Series of closing prices.

    Parameters
    ----------
    rows : list of sqlite3.Row

    Returns
    -------
    pd.Series
        Indexed by date string, values are close prices.
    """
    dates = [row["date"] for row in rows]
    closes = [row["close"] for row in rows]
    series = pd.Series(closes, index=pd.to_datetime(dates), dtype=float)
    return series.dropna().sort_index()


def analyse_ticker(ticker: str, conn, today_str: str) -> dict:
    """Run all quant models for a single ticker.

    Parameters
    ----------
    ticker    : str
    conn      : sqlite3.Connection
    today_str : str  – ISO date ``"YYYY-MM-DD"``

    Returns
    -------
    dict
        Aggregated results from all models.
    """
    results: dict = {"ticker": ticker, "errors": []}

    # Load prices from DB
    from_date = "2000-01-01"
    rows = get_prices(conn, ticker, from_date, today_str)
    if not rows:
        msg = f"No price data available for {ticker}."
        print(f"[run] WARNING: {msg}")
        results["errors"].append(msg)
        return results

    prices = prices_to_series(rows)
    results["price_count"] = len(prices)
    results["latest_price"] = round(float(prices.iloc[-1]), 4) if len(prices) else None
    results["latest_date"] = prices.index[-1].date().isoformat() if len(prices) else None

    # --- Y%-Filter ---
    try:
        yf_result = y_filter(prices, threshold_pct=5.0)
        results["y_filter"] = yf_result
    except Exception as exc:
        results["y_filter"] = None
        results["errors"].append(f"Y-Filter: {exc}")

    # --- ARIMA ---
    try:
        arima_result = arima_forecast(prices, forecast_days=5)
        results["arima"] = arima_result
    except Exception as exc:
        results["arima"] = None
        results["errors"].append(f"ARIMA: {exc}")

    # --- GARCH (single-asset) ---
    try:
        garch_result = garch_copula_analysis({ticker: prices})
        results["garch"] = garch_result
    except Exception as exc:
        results["garch"] = None
        results["errors"].append(f"GARCH: {exc}")

    # --- Backtest ---
    try:
        bt_result = backtest(ticker, prices, threshold_pct=5.0)
        results["backtest"] = bt_result
    except Exception as exc:
        results["backtest"] = None
        results["errors"].append(f"Backtest: {exc}")

    return results


def persist_signals(conn, ticker: str, today_str: str, results: dict) -> None:
    """Write model outputs to the signals table.

    Parameters
    ----------
    conn      : sqlite3.Connection
    ticker    : str
    today_str : str
    results   : dict  – output of :func:`analyse_ticker`
    """
    yf = results.get("y_filter") or {}
    ar = results.get("arima") or {}
    ga = results.get("garch") or {}
    per_asset = ga.get("per_asset", {}).get(ticker, {})

    upsert_signal(
        conn,
        ticker=ticker,
        date=today_str,
        y_filter_signal=yf.get("signal"),
        arima_forecast_1d=ar.get("forecast_1d"),
        arima_forecast_5d=ar.get("forecast_5d"),
        garch_volatility=per_asset.get("annualised_volatility"),
    )
    conn.commit()


def persist_backtest(conn, ticker: str, today_str: str, bt: dict) -> None:
    """Write backtest results to the backtest_results table.

    Parameters
    ----------
    conn      : sqlite3.Connection
    ticker    : str
    today_str : str
    bt        : dict  – output of :func:`models.backtest.backtest`
    """
    log_backtest_result(
        conn,
        ticker=ticker,
        total_return=bt.get("total_return_pct", 0.0),
        sharpe_ratio=bt.get("sharpe_ratio", 0.0),
        max_drawdown=bt.get("max_drawdown_pct", 0.0),
        win_rate=bt.get("win_rate", 0.0),
        model_version=MODEL_VERSION,
        run_date=today_str,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _fmt(value, decimals: int = 4, suffix: str = "") -> str:
    """Format a numeric value for Markdown, handling None gracefully."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def generate_report(portfolio: list[dict], all_results: list[dict], today_str: str) -> str:
    """Render the analysis results as a Markdown report string.

    Parameters
    ----------
    portfolio   : list[dict]  – portfolio entries from portfolio.json
    all_results : list[dict]  – one result dict per ticker
    today_str   : str         – report date

    Returns
    -------
    str
        Full Markdown report text.
    """
    lines = []

    # ---- Header ----
    lines.append(f"# Roboadvisor Daily Report — {today_str}\n")
    lines.append(f"*Generated at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*\n")
    lines.append("---\n")

    # ---- Portfolio summary ----
    lines.append("## Portfolio Summary\n")
    lines.append("| Ticker | Name | Shares | Latest Price | Currency |")
    lines.append("|--------|------|--------|-------------|----------|")
    for entry in portfolio:
        ticker = entry.get("ticker", "")
        name = entry.get("name", "")
        shares = entry.get("shares", "")
        currency = entry.get("currency", "")
        # Find latest price from results
        latest = next(
            (r.get("latest_price") for r in all_results if r.get("ticker") == ticker), None
        )
        lines.append(f"| {ticker} | {name} | {shares} | {_fmt(latest, 2)} | {currency} |")
    lines.append("")

    # ---- Per-ticker sections ----
    for result in all_results:
        ticker = result["ticker"]
        lines.append(f"---\n")
        lines.append(f"## {ticker}\n")

        errors = result.get("errors", [])
        if errors:
            lines.append("**Errors encountered during analysis:**\n")
            for err in errors:
                lines.append(f"- ⚠️ {err}")
            lines.append("")

        # Price metadata
        lines.append(f"**Latest close:** {_fmt(result.get('latest_price'), 4)}  ")
        lines.append(f"**As of:** {result.get('latest_date', 'N/A')}  ")
        lines.append(f"**Price observations:** {result.get('price_count', 'N/A')}\n")

        # Y%-Filter
        lines.append("### Y%-Filter Signal\n")
        yf = result.get("y_filter")
        if yf:
            signal = yf.get("signal", "N/A")
            signal_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal, "⚪")
            lines.append(f"| Metric | Value |")
            lines.append(f"|--------|-------|")
            lines.append(f"| **Signal** | {signal_emoji} **{signal}** |")
            lines.append(f"| Current Trend | {yf.get('current_trend', 'N/A')} |")
            lines.append(f"| Last Turning Point Date | {yf.get('last_turning_point_date', 'N/A')} |")
            lines.append(f"| Last Turning Point Price | {_fmt(yf.get('last_turning_point_price'), 4)} |")
            lines.append(f"| % from Turning Point | {_fmt(yf.get('pct_from_turning_point'), 2, '%')} |")
        else:
            lines.append("*No Y%-Filter result available.*")
        lines.append("")

        # ARIMA
        lines.append("### ARIMA Forecast\n")
        ar = result.get("arima")
        if ar:
            lines.append(f"| Metric | Value |")
            lines.append(f"|--------|-------|")
            lines.append(f"| ARIMA Order | {ar.get('order', 'N/A')} |")
            lines.append(f"| Forecast +1d | {_fmt(ar.get('forecast_1d'), 4)} |")
            lines.append(f"| Forecast +5d | {_fmt(ar.get('forecast_5d'), 4)} |")
            # 5-day series
            fc_series = ar.get("forecast_series", [])
            ci_lo = ar.get("confidence_lower", [])
            ci_hi = ar.get("confidence_upper", [])
            if fc_series:
                lines.append(f"\n**5-Day Forecast Series:**\n")
                lines.append("| Day | Forecast | 95% CI Lower | 95% CI Upper |")
                lines.append("|-----|----------|-------------|-------------|")
                for i, fc in enumerate(fc_series, 1):
                    lo = ci_lo[i - 1] if i - 1 < len(ci_lo) else None
                    hi = ci_hi[i - 1] if i - 1 < len(ci_hi) else None
                    lines.append(f"| +{i}d | {_fmt(fc, 4)} | {_fmt(lo, 4)} | {_fmt(hi, 4)} |")
        else:
            lines.append("*No ARIMA result available.*")
        lines.append("")

        # GARCH
        lines.append("### GARCH Volatility\n")
        ga = result.get("garch")
        if ga:
            pa = ga.get("per_asset", {}).get(ticker, {})
            lines.append(f"| Metric | Value |")
            lines.append(f"|--------|-------|")
            lines.append(f"| Annualised Volatility | {_fmt(pa.get('annualised_volatility'), 4)} |")
            lines.append(f"| Fitted | {pa.get('fitted', False)} |")
        else:
            lines.append("*No GARCH result available.*")
        lines.append("")

        # Backtest
        lines.append("### Backtest (Y%-Filter Strategy, threshold=5%)\n")
        bt = result.get("backtest")
        if bt:
            lines.append(f"| Metric | Value |")
            lines.append(f"|--------|-------|")
            lines.append(f"| Total Return | {_fmt(bt.get('total_return_pct'), 2, '%')} |")
            lines.append(f"| Sharpe Ratio | {_fmt(bt.get('sharpe_ratio'), 4)} |")
            lines.append(f"| Max Drawdown | {_fmt(bt.get('max_drawdown_pct'), 2, '%')} |")
            lines.append(f"| Win Rate | {_fmt(bt.get('win_rate'), 2)} |")
            lines.append(f"| Total Trades | {len(bt.get('trades', []))} |")
        else:
            lines.append("*No backtest result available.*")
        lines.append("")

        # LLM placeholder
        lines.append("### LLM Recommendation\n")
        lines.append("> *LLM analysis not yet available for this run.*  ")
        lines.append("> Configure `OPENAI_API_KEY` and integrate `llm/` module to enable AI-driven commentary.\n")

    # ---- Disclaimer ----
    lines.append("---\n")
    lines.append("## Disclaimer\n")
    lines.append(DISCLAIMER)
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Main entry point — parse arguments and run the pipeline."""
    parser = argparse.ArgumentParser(description="Roboadvisor quant pipeline")
    parser.add_argument(
        "--portfolio", type=Path, default=DEFAULT_PORTFOLIO,
        help="Path to portfolio.json (default: portfolio.json)",
    )
    parser.add_argument(
        "--db", type=str, default="roboadvisor.db",
        help="SQLite database path (default: roboadvisor.db)",
    )
    parser.add_argument(
        "--skip-news", action="store_true",
        help="Skip news fetching (useful when NEWS_API_KEY is unavailable)",
    )
    args = parser.parse_args()

    # Load config (validates env vars)
    try:
        cfg = load_config()
    except SystemExit:
        # Config printed the error; re-raise to exit
        raise

    today_str = date.today().isoformat()
    print(f"\n{'='*60}")
    print(f"  Roboadvisor — {today_str}")
    print(f"{'='*60}\n")

    # Database
    conn = get_connection(args.db)
    init_db(conn)

    # Portfolio
    if not args.portfolio.exists():
        print(f"ERROR: portfolio file not found: {args.portfolio}", file=sys.stderr)
        sys.exit(1)
    portfolio = load_portfolio(args.portfolio)
    print(f"[run] Loaded {len(portfolio)} positions from {args.portfolio}\n")

    all_results: list[dict] = []

    for entry in portfolio:
        ticker = entry.get("ticker")
        if not ticker:
            print("[run] WARNING: portfolio entry without ticker; skipping.")
            continue

        print(f"\n{'─'*50}")
        print(f"  Processing: {ticker}  ({entry.get('name', '')})")
        print(f"{'─'*50}")

        # 1. Prices
        try:
            fetch_prices(ticker, conn)
        except Exception as exc:
            print(f"[run] ERROR fetching prices for {ticker}: {exc}")

        # 2. News
        if not args.skip_news:
            try:
                fetch_news(ticker, conn, days=7, api_key=cfg.news_api_key)
            except Exception as exc:
                print(f"[run] WARNING: news fetch failed for {ticker}: {exc}")
        else:
            print(f"[run] {ticker}: skipping news (--skip-news flag set).")

        # 3. Models
        result = analyse_ticker(ticker, conn, today_str)
        all_results.append(result)

        # 4. Persist signals
        try:
            persist_signals(conn, ticker, today_str, result)
        except Exception as exc:
            print(f"[run] WARNING: could not persist signals for {ticker}: {exc}")

        # 5. Persist backtest
        bt = result.get("backtest")
        if bt:
            try:
                persist_backtest(conn, ticker, today_str, bt)
            except Exception as exc:
                print(f"[run] WARNING: could not persist backtest for {ticker}: {exc}")

    # 6. Multi-asset GARCH copula (only if we have 2+ tickers with data)
    tickers_with_data = [
        r["ticker"] for r in all_results if r.get("price_count", 0) > 30
    ]
    if len(tickers_with_data) >= 2:
        print(f"\n[run] Running multi-asset GARCH copula for: {tickers_with_data}")
        try:
            prices_dict = {}
            for r in all_results:
                if r["ticker"] in tickers_with_data:
                    rows = get_prices(conn, r["ticker"], "2000-01-01", today_str)
                    prices_dict[r["ticker"]] = prices_to_series(rows)
            copula_result = garch_copula_analysis(prices_dict)
            print("[run] Copula correlation matrix:")
            tickers_c = copula_result["correlation_matrix"]["tickers"]
            matrix = copula_result["correlation_matrix"]["matrix"]
            for i, row in enumerate(matrix):
                row_str = "  ".join(f"{v:7.4f}" for v in row)
                print(f"  {tickers_c[i]}: {row_str}")
        except Exception as exc:
            print(f"[run] WARNING: multi-asset GARCH copula failed: {exc}")

    # 7. Generate report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{today_str}.md"
    try:
        report_text = generate_report(portfolio, all_results, today_str)
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(report_text)
        print(f"\n[run] Report written to: {report_path}")
    except Exception as exc:
        print(f"[run] ERROR generating report: {exc}")
        traceback.print_exc()

    conn.close()
    print(f"\n[run] Done.\n")


if __name__ == "__main__":
    main()
