#!/usr/bin/env python3
"""
export_dashboard.py — SQLite + portfolio.json → dashboard JSON exporter.

Reads data from the roboadvisor SQLite database and portfolio.json, then
writes a single JSON file consumable by the browser dashboard at
dashboard/index.html.

Usage:
    python export_dashboard.py [--db roboadvisor.db] [--portfolio portfolio.json]
                               [--out dashboard/dashboard_data.json]
                               [--trades trades.json] [--days 365]
"""

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Export roboadvisor data to a dashboard-compatible JSON file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db",
        default="roboadvisor.db",
        help="Path to the SQLite database file.",
    )
    parser.add_argument(
        "--portfolio",
        default="portfolio.json",
        help="Path to the portfolio JSON file.",
    )
    parser.add_argument(
        "--out",
        default="dashboard/dashboard_data.json",
        help="Output path for the generated dashboard JSON.",
    )
    parser.add_argument(
        "--trades",
        default=None,
        help="Optional path to a trades JSON file to merge into executed_trades.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Number of days of price history to include.",
    )
    return parser


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_portfolio(portfolio_path: Path) -> list[dict]:
    """
    Load the portfolio from a JSON file.

    Returns a list of holding dicts with keys: ticker, name, shares, currency.
    Drops internal fields (e.g. purchase_price) not needed by the dashboard.
    """
    with portfolio_path.open() as f:
        raw = json.load(f)

    holdings = []
    for item in raw.get("portfolio", []):
        holdings.append({
            "ticker": item["ticker"],
            "name": item.get("name", ""),
            "shares": item.get("shares", 0),
            "currency": item.get("currency", ""),
        })
    return holdings


def load_price_history(conn: sqlite3.Connection, tickers: list[str], days: int) -> dict[str, list[dict]]:
    """
    Load close prices for the given tickers from the prices table.

    Args:
        conn: Open SQLite connection.
        tickers: List of ticker symbols to query.
        days: Number of calendar days back from today to include.

    Returns:
        Dict mapping ticker → list of {date, close} dicts, sorted ascending by date.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    history: dict[str, list[dict]] = {t: [] for t in tickers}

    placeholders = ",".join("?" * len(tickers))
    query = f"""
        SELECT ticker, date, close
        FROM prices
        WHERE ticker IN ({placeholders})
          AND date >= ?
        ORDER BY ticker, date ASC
    """
    cursor = conn.execute(query, (*tickers, cutoff))
    for row in cursor.fetchall():
        ticker, dt, close = row
        if ticker in history:
            history[ticker].append({"date": dt, "close": close})

    return history


def load_signals(conn: sqlite3.Connection, tickers: list[str]) -> list[dict]:
    """
    Load the most recent signal row per ticker from the signals table.

    Maps DB column names to the dashboard's expected field names:
      y_filter_signal → quant_signal

    Args:
        conn: Open SQLite connection.
        tickers: List of ticker symbols to query.

    Returns:
        List of signal dicts, one per ticker (latest date only).
    """
    placeholders = ",".join("?" * len(tickers))
    # Grab the latest signal per ticker via a subquery
    query = f"""
        SELECT s.ticker,
               s.date,
               s.y_filter_signal,
               s.arima_forecast_1d,
               s.arima_forecast_5d,
               s.garch_volatility,
               s.llm_recommendation,
               s.llm_confidence,
               s.llm_rationale
        FROM signals s
        INNER JOIN (
            SELECT ticker, MAX(date) AS max_date
            FROM signals
            WHERE ticker IN ({placeholders})
            GROUP BY ticker
        ) latest ON s.ticker = latest.ticker AND s.date = latest.max_date
        ORDER BY s.ticker
    """
    cursor = conn.execute(query, tuple(tickers))
    rows = cursor.fetchall()

    suggestions = []
    for row in rows:
        (ticker, dt, quant_signal, arima_1d, arima_5d,
         garch_vol, llm_rec, llm_conf, llm_rat) = row
        suggestions.append({
            "date": dt,
            "ticker": ticker,
            "quant_signal": quant_signal,
            "llm_recommendation": llm_rec,
            "llm_confidence": llm_conf,
            "llm_rationale": llm_rat,
            "arima_forecast_1d": arima_1d,
            "arima_forecast_5d": arima_5d,
            "garch_volatility": garch_vol,
        })
    return suggestions


def load_backtest_results(conn: sqlite3.Connection, tickers: list[str]) -> list[dict]:
    """
    Load the latest backtest result per ticker from the backtest_results table.

    Maps DB column names to the dashboard's expected field names:
      total_return → total_return_pct
      max_drawdown → max_drawdown_pct

    Args:
        conn: Open SQLite connection.
        tickers: List of ticker symbols to query.

    Returns:
        List of backtest result dicts, one per ticker (latest run_date only).
    """
    placeholders = ",".join("?" * len(tickers))
    query = f"""
        SELECT br.run_date,
               br.ticker,
               br.total_return,
               br.sharpe_ratio,
               br.max_drawdown,
               br.win_rate
        FROM backtest_results br
        INNER JOIN (
            SELECT ticker, MAX(run_date) AS max_date
            FROM backtest_results
            WHERE ticker IN ({placeholders})
            GROUP BY ticker
        ) latest ON br.ticker = latest.ticker AND br.run_date = latest.max_date
        ORDER BY br.ticker
    """
    cursor = conn.execute(query, tuple(tickers))
    rows = cursor.fetchall()

    results = []
    for row in rows:
        run_date, ticker, total_return, sharpe, max_dd, win_rate = row
        results.append({
            "run_date": run_date,
            "ticker": ticker,
            "total_return_pct": total_return,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": max_dd,
            "win_rate": win_rate,
        })
    return results


def load_trades(trades_path: Path) -> list[dict]:
    """
    Load executed trades from a JSON file.

    The file is expected to be a JSON array of trade dicts, or a dict with
    a top-level "executed_trades" key containing such an array.

    Args:
        trades_path: Path to the trades JSON file.

    Returns:
        List of trade dicts (may be empty if the file is missing or malformed).
    """
    if not trades_path.exists():
        print(f"[warn] Trades file not found: {trades_path} — skipping.")
        return []

    with trades_path.open() as f:
        raw = json.load(f)

    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("executed_trades", [])

    print(f"[warn] Unexpected trades file format in {trades_path} — skipping.")
    return []


# ---------------------------------------------------------------------------
# Assembly & output
# ---------------------------------------------------------------------------

def build_export(
    portfolio: list[dict],
    price_history: dict[str, list[dict]],
    suggestions: list[dict],
    backtest_results: list[dict],
    executed_trades: list[dict],
) -> dict:
    """
    Assemble the full dashboard export dict.

    Args:
        portfolio: List of portfolio holding dicts.
        price_history: Dict of ticker → list of {date, close}.
        suggestions: List of signal/suggestion dicts.
        backtest_results: List of backtest result dicts.
        executed_trades: List of executed trade dicts.

    Returns:
        Complete export dict ready to be serialised to JSON.
    """
    return {
        "meta": {
            "generated_at": date.today().isoformat(),
            "version": "1.0",
        },
        "portfolio": portfolio,
        "price_history": price_history,
        "suggestions": suggestions,
        "backtest_results": backtest_results,
        "executed_trades": executed_trades,
    }


def print_summary(
    tickers: list[str],
    price_history: dict[str, list[dict]],
    suggestions: list[dict],
    backtest_results: list[dict],
    out_path: Path,
) -> None:
    """
    Print a human-readable summary of the exported data to stdout.

    Args:
        tickers: List of ticker symbols in the portfolio.
        price_history: Dict of ticker → price rows.
        suggestions: List of signal dicts.
        backtest_results: List of backtest result dicts.
        out_path: Path where the JSON was written.
    """
    print("\n── Export summary ──────────────────────────────────")
    print(f"  Tickers    : {', '.join(tickers) if tickers else '(none)'}")

    # Date range across all tickers
    all_dates = [row["date"] for rows in price_history.values() for row in rows]
    if all_dates:
        print(f"  Price range: {min(all_dates)} → {max(all_dates)}")
    else:
        print("  Price range: (no price data)")

    print(f"  Signals    : {len(suggestions)} row(s)")
    print(f"  Backtest   : {len(backtest_results)} row(s)")
    print(f"  Output     : {out_path.resolve()}")
    print("────────────────────────────────────────────────────\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point: parse args, load data, write export JSON."""
    parser = build_parser()
    args = parser.parse_args()

    db_path = Path(args.db)
    portfolio_path = Path(args.portfolio)
    out_path = Path(args.out)
    trades_path = Path(args.trades) if args.trades else None

    # Validate required inputs
    if not db_path.exists():
        print(f"[error] Database not found: {db_path.resolve()}")
        print("        Run the roboadvisor pipeline first to populate the database.")
        sys.exit(1)

    if not portfolio_path.exists():
        print(f"[error] Portfolio file not found: {portfolio_path.resolve()}")
        sys.exit(1)

    # Load portfolio
    portfolio = load_portfolio(portfolio_path)
    tickers = [h["ticker"] for h in portfolio]

    if not tickers:
        print("[warn] Portfolio is empty — output will have no data.")

    # Ensure output directory exists
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Load DB data
    conn = sqlite3.connect(str(db_path))
    try:
        price_history = load_price_history(conn, tickers, args.days)
        suggestions = load_signals(conn, tickers)
        backtest_results = load_backtest_results(conn, tickers)
    finally:
        conn.close()

    # Load trades if provided
    executed_trades: list[dict] = []
    if trades_path:
        executed_trades = load_trades(trades_path)

    # Assemble and write
    export = build_export(portfolio, price_history, suggestions, backtest_results, executed_trades)

    with out_path.open("w") as f:
        json.dump(export, f, indent=2, default=str)

    print_summary(tickers, price_history, suggestions, backtest_results, out_path)


if __name__ == "__main__":
    main()
