"""
data/news.py
------------
News fetcher using the Finnhub company-news API.

Finnhub returns ticker-linked news directly, which works well for both
US symbols and European ETF tickers (XETRA, Euronext, etc.).

API endpoint:
    GET https://finnhub.io/api/v1/company-news
        ?symbol={symbol}&from={YYYY-MM-DD}&to={YYYY-MM-DD}&token={api_key}
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from data.db import upsert_news


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_news(
    ticker: str,
    db_conn: sqlite3.Connection,
    days: int = 7,
    api_key: Optional[str] = None,
    max_articles: int = 20,
) -> list:
    """Fetch recent news articles for *ticker* via Finnhub and persist to SQLite.

    Uses Finnhub's ``/api/v1/company-news`` endpoint.

    Parameters
    ----------
    ticker       : str
        Ticker symbol (e.g. ``"IEGE.DE"``, ``"AAPL"``).
    db_conn      : sqlite3.Connection
        An open database connection.
    days         : int
        How many calendar days of news to retrieve.
    api_key      : str, optional
        Finnhub API key.  If omitted, the ``FINNHUB_API_KEY`` environment
        variable is used.
    max_articles : int
        Maximum articles to store per call.

    Returns
    -------
    list of dict
        Raw article dicts as returned by Finnhub (also persisted to SQLite).
    """
    # Resolve API key
    if api_key is None:
        api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        try:
            from utils.config import get_config
            api_key = get_config().finnhub_api_key
        except Exception:
            pass
    if not api_key:
        print(f"[news] WARNING: FINNHUB_API_KEY not configured — skipping news for {ticker}.")
        return []

    to_date = datetime.now(timezone.utc)
    from_date = to_date - timedelta(days=days)
    from_str = from_date.strftime("%Y-%m-%d")
    to_str = to_date.strftime("%Y-%m-%d")

    print(f"[news] {ticker}: querying Finnhub for news from {from_str} to {to_str} …")

    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": ticker,
        "from": from_str,
        "to": to_str,
        "token": api_key,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
    except requests.HTTPError as exc:
        print(f"[news] WARNING: Finnhub HTTP error for {ticker!r}: {exc} — skipping.")
        return []
    except requests.RequestException as exc:
        print(f"[news] WARNING: Finnhub request failed for {ticker!r}: {exc} — skipping.")
        return []

    articles = resp.json()
    if not isinstance(articles, list):
        print(f"[news] WARNING: unexpected Finnhub response for {ticker!r}: {articles!r}")
        return []

    # Trim to max_articles (Finnhub may return many)
    articles = articles[:max_articles]

    stored = 0
    for article in articles:
        # Finnhub `datetime` field is a Unix timestamp (int)
        ts = article.get("datetime")
        if ts:
            try:
                published_at = datetime.utcfromtimestamp(int(ts)).isoformat()
            except (ValueError, TypeError):
                published_at = None
        else:
            published_at = None

        upsert_news(
            db_conn,
            ticker=ticker,
            published_at=published_at,
            headline=article.get("headline"),
            source=article.get("source"),
            url=article.get("url"),
            body=article.get("summary"),
        )
        stored += 1

    db_conn.commit()
    print(f"[news] {ticker}: {len(articles)} articles fetched, {stored} written to DB.")
    return articles
