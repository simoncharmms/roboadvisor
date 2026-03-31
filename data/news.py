"""
data/news.py
------------
News fetcher using the NewsAPI (newsapi-python).

ETF tickers are mapped to human-readable fund / company names for better
search queries, since typing "AMUN.PA" into a news API gives poor results.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

try:
    from newsapi import NewsApiClient
    from newsapi.newsapi_exception import NewsAPIException
except ImportError as exc:
    raise ImportError("newsapi-python is required: pip install newsapi-python") from exc

from data.db import upsert_news


# ---------------------------------------------------------------------------
# Ticker → search query mapping
# ---------------------------------------------------------------------------

#: Maps ticker symbols to a more descriptive search query that yields better
#: news results.  Add entries as your portfolio grows.
TICKER_TO_QUERY: dict[str, str] = {
    # Amundi ETFs
    "AMUN.PA":  "Amundi MSCI World ETF",
    "AEEM.PA":  "Amundi MSCI Emerging Markets ETF",
    "AUEM.PA":  "Amundi MSCI USA ETF",

    # iShares / BlackRock
    "IEGE.DE":  "iShares Germany Government Bonds ETF",
    "IWDA.AS":  "iShares MSCI World ETF",
    "CSPX.L":   "iShares S&P 500 ETF",
    "VWCE.DE":  "Vanguard FTSE All-World ETF",
    "EUNL.DE":  "iShares Core MSCI World UCITS ETF",

    # US large-caps / common tickers
    "SPY":   "S&P 500 ETF SPDR",
    "QQQ":   "Invesco QQQ Nasdaq ETF",
    "VTI":   "Vanguard Total Stock Market ETF",
    "BND":   "Vanguard Total Bond Market ETF",
    "GLD":   "SPDR Gold Trust ETF",
    "TLT":   "iShares 20+ Year Treasury Bond ETF",
    "AAPL":  "Apple Inc",
    "MSFT":  "Microsoft Corporation",
    "GOOGL": "Alphabet Google",
    "AMZN":  "Amazon",
    "TSLA":  "Tesla",
    "NVDA":  "Nvidia",
}


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
    """Fetch recent news articles for *ticker* and persist them to SQLite.

    Uses NewsAPI's ``/v2/everything`` endpoint.  The search query is taken from
    :data:`TICKER_TO_QUERY` when available; otherwise the raw ticker symbol is
    used.

    Parameters
    ----------
    ticker       : str
        Ticker symbol (e.g. ``"IEGE.DE"``).
    db_conn      : sqlite3.Connection
        An open database connection.
    days         : int
        How many calendar days of news to retrieve (max 30 for free tier).
    api_key      : str, optional
        NewsAPI key.  If omitted, the ``NEWS_API_KEY`` environment variable is
        used (loaded via :mod:`utils.config`).
    max_articles : int
        Maximum articles to fetch per call (capped at 100 by NewsAPI).

    Returns
    -------
    list of dict
        Raw article dicts as returned by NewsAPI (also persisted to SQLite).

    Raises
    ------
    RuntimeError
        On NewsAPI errors (bad key, rate-limit, etc.).
    """
    # Resolve API key
    if api_key is None:
        try:
            from utils.config import get_config
            api_key = get_config().news_api_key
        except SystemExit:
            raise RuntimeError("NEWS_API_KEY is not configured.")

    client = NewsApiClient(api_key=api_key)

    query = TICKER_TO_QUERY.get(ticker, ticker)
    from_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    print(f"[news] {ticker}: querying NewsAPI for '{query}' since {from_date} …")

    try:
        response = client.get_everything(
            q=query,
            from_param=from_date,
            language="en",
            sort_by="publishedAt",
            page_size=min(max_articles, 100),
        )
    except NewsAPIException as exc:
        raise RuntimeError(f"NewsAPI error for {ticker!r}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Unexpected error fetching news for {ticker!r}: {exc}") from exc

    articles = response.get("articles", [])
    stored = 0
    for article in articles:
        source_name = (article.get("source") or {}).get("name")
        published_raw = article.get("publishedAt")
        # Normalise ISO datetime
        if published_raw:
            try:
                published_at = datetime.strptime(published_raw, "%Y-%m-%dT%H:%M:%SZ").isoformat()
            except ValueError:
                published_at = published_raw
        else:
            published_at = None

        upsert_news(
            db_conn,
            ticker=ticker,
            published_at=published_at,
            headline=article.get("title"),
            source=source_name,
            url=article.get("url"),
            body=article.get("description") or article.get("content"),
        )
        stored += 1

    db_conn.commit()
    print(f"[news] {ticker}: {len(articles)} articles fetched, {stored} written to DB.")
    return articles
