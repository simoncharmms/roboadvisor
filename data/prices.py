"""
data/prices.py
--------------
Incremental daily OHLCV price fetcher backed by yfinance.

The function checks the local SQLite database for the most recently stored
date for the given ticker and only downloads data that is newer, avoiding
redundant API calls.
"""

import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

try:
    import yfinance as yf
except ImportError as exc:
    raise ImportError("yfinance is required: pip install yfinance") from exc

from data.db import get_last_price_date, upsert_price


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How far back to fetch if no data exists at all
_DEFAULT_LOOKBACK_DAYS = 365 * 3  # 3 years of history on first run


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_prices(ticker: str, db_conn: sqlite3.Connection) -> pd.DataFrame:
    """Fetch daily OHLCV prices for *ticker* and persist new rows to SQLite.

    Uses an incremental strategy: if prices already exist in the database the
    download starts from the day after the last stored date.  On the first run
    the last ``_DEFAULT_LOOKBACK_DAYS`` days are fetched.

    Parameters
    ----------
    ticker  : str
        The ticker symbol understood by yfinance (e.g. ``"AMUN.PA"``).
    db_conn : sqlite3.Connection
        An open database connection (the caller owns the connection lifecycle).

    Returns
    -------
    pd.DataFrame
        DataFrame with columns ``[Open, High, Low, Close, Volume]`` indexed by
        ``Date``.  Contains *all* newly fetched rows (may be empty if already
        up-to-date).

    Raises
    ------
    RuntimeError
        If yfinance returns no data for the requested range and ticker.
    """
    last_stored = get_last_price_date(db_conn, ticker)

    if last_stored:
        # Start from the day after the last stored date
        start_dt = (datetime.strptime(last_stored, "%Y-%m-%d") + timedelta(days=1)).date()
    else:
        start_dt = date.today() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)

    end_dt = date.today()

    if start_dt > end_dt:
        print(f"[prices] {ticker}: already up-to-date (last stored: {last_stored})")
        return pd.DataFrame()

    print(f"[prices] {ticker}: fetching {start_dt} → {end_dt} …")

    try:
        raw = yf.download(
            ticker,
            start=start_dt.isoformat(),
            end=(end_dt + timedelta(days=1)).isoformat(),  # yfinance end is exclusive
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
    except Exception as exc:
        raise RuntimeError(f"yfinance download failed for {ticker!r}: {exc}") from exc

    if raw.empty:
        print(f"[prices] {ticker}: yfinance returned no data for range {start_dt} → {end_dt}")
        return pd.DataFrame()

    # Flatten multi-level columns if present (yfinance >= 0.2 may return them)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    # Normalise column names
    raw = raw.rename(columns={"Open": "open", "High": "high", "Low": "low",
                               "Close": "close", "Volume": "volume"})

    inserted = 0
    for idx, row in raw.iterrows():
        row_date = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
        upsert_price(
            db_conn,
            ticker=ticker,
            date=row_date,
            open_=_safe_float(row.get("open")),
            high=_safe_float(row.get("high")),
            low=_safe_float(row.get("low")),
            close=_safe_float(row.get("close")),
            volume=_safe_int(row.get("volume")),
        )
        inserted += 1

    db_conn.commit()
    print(f"[prices] {ticker}: stored {inserted} new rows.")
    return raw


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _safe_float(value) -> Optional[float]:
    """Convert *value* to float, returning None for NaN / None."""
    try:
        result = float(value)
        import math
        return None if math.isnan(result) else result
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> Optional[int]:
    """Convert *value* to int, returning None for NaN / None."""
    try:
        import math
        f = float(value)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None
