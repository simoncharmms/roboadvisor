"""
data/db.py
----------
SQLite database setup and data-access layer for the roboadvisor.

Schema
------
prices           – daily OHLCV data per ticker
news             – news articles per ticker
signals          – aggregated model signals per ticker / date
backtest_results – historical backtest performance metrics
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generator, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = "roboadvisor.db"


def get_connection(db_path: str = _DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with sensible defaults.

    Row factory is set to :class:`sqlite3.Row` so columns are accessible by
    name.  Foreign-key enforcement is enabled.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.  Created if it does not exist.

    Returns
    -------
    sqlite3.Connection
        An open database connection.
    """
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def managed_connection(db_path: str = _DEFAULT_DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields an auto-committing / auto-closing connection.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.

    Yields
    ------
    sqlite3.Connection
    """
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS prices (
    ticker  TEXT NOT NULL,
    date    DATE NOT NULL,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  INTEGER,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS news (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT        NOT NULL,
    published_at DATETIME,
    headline     TEXT,
    source       TEXT,
    url          TEXT,
    body         TEXT,
    fetched_at   DATETIME    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_news_ticker_pub
    ON news (ticker, published_at DESC);

CREATE TABLE IF NOT EXISTS signals (
    ticker               TEXT NOT NULL,
    date                 DATE NOT NULL,
    y_filter_signal      TEXT,
    arima_forecast_1d    REAL,
    arima_forecast_5d    REAL,
    garch_volatility     REAL,
    llm_recommendation   TEXT,
    llm_confidence       TEXT,
    llm_rationale        TEXT,
    llm_quant_agreement  TEXT,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date      DATE    NOT NULL,
    model_version TEXT,
    ticker        TEXT    NOT NULL,
    total_return  REAL,
    sharpe_ratio  REAL,
    max_drawdown  REAL,
    win_rate      REAL
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not already exist.

    Parameters
    ----------
    conn : sqlite3.Connection
        An open database connection.
    """
    conn.executescript(_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def upsert_price(
    conn: sqlite3.Connection,
    ticker: str,
    date: str,
    open_: Optional[float],
    high: Optional[float],
    low: Optional[float],
    close: Optional[float],
    volume: Optional[int],
) -> None:
    """Insert or replace a single OHLCV row.

    Parameters
    ----------
    conn    : sqlite3.Connection
    ticker  : str   – e.g. ``"AAPL"``
    date    : str   – ISO-format date ``"YYYY-MM-DD"``
    open_   : float
    high    : float
    low     : float
    close   : float
    volume  : int
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (ticker, date, open_, high, low, close, volume),
    )


def upsert_news(
    conn: sqlite3.Connection,
    ticker: str,
    published_at: Optional[str],
    headline: Optional[str],
    source: Optional[str],
    url: Optional[str],
    body: Optional[str],
) -> None:
    """Insert a news article if the same URL has not been stored already.

    Duplicate detection is done on (ticker, url) to avoid storing the same
    article twice across runs.

    Parameters
    ----------
    conn         : sqlite3.Connection
    ticker       : str
    published_at : str  – ISO datetime string
    headline     : str
    source       : str
    url          : str
    body         : str
    """
    fetched_at = datetime.utcnow().isoformat()
    # Deduplicate on (ticker, url)
    existing = conn.execute(
        "SELECT id FROM news WHERE ticker = ? AND url = ?", (ticker, url)
    ).fetchone()
    if existing:
        return
    conn.execute(
        """
        INSERT INTO news (ticker, published_at, headline, source, url, body, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (ticker, published_at, headline, source, url, body, fetched_at),
    )


def upsert_signal(
    conn: sqlite3.Connection,
    ticker: str,
    date: str,
    y_filter_signal: Optional[str] = None,
    arima_forecast_1d: Optional[float] = None,
    arima_forecast_5d: Optional[float] = None,
    garch_volatility: Optional[float] = None,
    llm_recommendation: Optional[str] = None,
    llm_confidence: Optional[str] = None,
    llm_rationale: Optional[str] = None,
    llm_quant_agreement: Optional[str] = None,
) -> None:
    """Insert or replace a signals row for a ticker / date pair.

    Parameters
    ----------
    conn                : sqlite3.Connection
    ticker              : str
    date                : str  – ISO date ``"YYYY-MM-DD"``
    y_filter_signal     : str  – ``"BUY"``, ``"SELL"``, or ``"HOLD"``
    arima_forecast_1d   : float
    arima_forecast_5d   : float
    garch_volatility    : float
    llm_recommendation  : str
    llm_confidence      : str
    llm_rationale       : str
    llm_quant_agreement : str
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO signals (
            ticker, date,
            y_filter_signal, arima_forecast_1d, arima_forecast_5d,
            garch_volatility, llm_recommendation, llm_confidence,
            llm_rationale, llm_quant_agreement
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker, date,
            y_filter_signal, arima_forecast_1d, arima_forecast_5d,
            garch_volatility, llm_recommendation, llm_confidence,
            llm_rationale, llm_quant_agreement,
        ),
    )


def log_backtest_result(
    conn: sqlite3.Connection,
    ticker: str,
    total_return: float,
    sharpe_ratio: float,
    max_drawdown: float,
    win_rate: float,
    model_version: str = "1.0",
    run_date: Optional[str] = None,
) -> None:
    """Append a backtest result row.

    Parameters
    ----------
    conn          : sqlite3.Connection
    ticker        : str
    total_return  : float  – percentage
    sharpe_ratio  : float
    max_drawdown  : float  – percentage (positive value)
    win_rate      : float  – fraction 0–1
    model_version : str
    run_date      : str    – ISO date; defaults to today (UTC)
    """
    if run_date is None:
        run_date = datetime.utcnow().date().isoformat()
    conn.execute(
        """
        INSERT INTO backtest_results
            (run_date, model_version, ticker, total_return, sharpe_ratio, max_drawdown, win_rate)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_date, model_version, ticker, total_return, sharpe_ratio, max_drawdown, win_rate),
    )


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_prices(
    conn: sqlite3.Connection,
    ticker: str,
    start_date: str,
    end_date: str,
) -> List[sqlite3.Row]:
    """Retrieve price rows for a ticker within a date range (inclusive).

    Parameters
    ----------
    conn       : sqlite3.Connection
    ticker     : str
    start_date : str  – ISO date ``"YYYY-MM-DD"``
    end_date   : str  – ISO date ``"YYYY-MM-DD"``

    Returns
    -------
    list of sqlite3.Row
        Columns: ticker, date, open, high, low, close, volume
    """
    return conn.execute(
        """
        SELECT ticker, date, open, high, low, close, volume
        FROM   prices
        WHERE  ticker = ? AND date BETWEEN ? AND ?
        ORDER  BY date ASC
        """,
        (ticker, start_date, end_date),
    ).fetchall()


def get_last_price_date(conn: sqlite3.Connection, ticker: str) -> Optional[str]:
    """Return the most recent date stored for a ticker, or None.

    Parameters
    ----------
    conn   : sqlite3.Connection
    ticker : str

    Returns
    -------
    str or None
        ISO date string ``"YYYY-MM-DD"``.
    """
    row = conn.execute(
        "SELECT MAX(date) AS last_date FROM prices WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    return row["last_date"] if row else None


def get_news(
    conn: sqlite3.Connection,
    ticker: str,
    days: int = 7,
) -> List[sqlite3.Row]:
    """Retrieve news articles for a ticker from the last *days* days.

    Parameters
    ----------
    conn   : sqlite3.Connection
    ticker : str
    days   : int  – look-back window in calendar days

    Returns
    -------
    list of sqlite3.Row
        Columns: id, ticker, published_at, headline, source, url, body, fetched_at
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    return conn.execute(
        """
        SELECT id, ticker, published_at, headline, source, url, body, fetched_at
        FROM   news
        WHERE  ticker = ? AND published_at >= ?
        ORDER  BY published_at DESC
        """,
        (ticker, cutoff),
    ).fetchall()
