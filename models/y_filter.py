"""
models/y_filter.py
------------------
Y%-Filter trend-detection model.

The Y%-Filter (also called the *percentage filter rule*) marks a new turning
point whenever the price moves more than ``threshold_pct`` percent away from
the last confirmed turning point.

* If price rises ≥ threshold_pct % above the last LOW  → new turning point (UP, signal BUY)
* If price falls ≥ threshold_pct % below the last HIGH → new turning point (DOWN, signal SELL)
* Otherwise → HOLD (current trend continues unchanged)

References
----------
Alexander, S. S. (1961). Price Movements in Speculative Markets: Trends or
Random Walks. *Industrial Management Review*, 2(2), 7–26.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def y_filter(prices: pd.Series, threshold_pct: float = 5.0) -> dict:
    """Apply the Y%-Filter to a price series and return the current signal.

    Parameters
    ----------
    prices        : pd.Series
        Daily closing prices indexed by date (or any ordered index).  Must
        contain at least 2 observations.
    threshold_pct : float
        The percentage threshold that triggers a new turning point (default 5 %).

    Returns
    -------
    dict with keys:
        signal                 – ``"BUY"``, ``"SELL"``, or ``"HOLD"``
        last_turning_point_date  – date of the last confirmed turning point
        last_turning_point_price – price at that turning point
        current_trend          – ``"UP"`` or ``"DOWN"``
        pct_from_turning_point – % change from turning point to the latest price

    Raises
    ------
    ValueError
        If *prices* is empty or contains fewer than 2 data points.
    """
    if prices is None or len(prices) < 2:
        raise ValueError("y_filter requires at least 2 price observations.")

    prices = prices.dropna()
    if len(prices) < 2:
        raise ValueError("y_filter requires at least 2 non-NaN price observations.")

    threshold = threshold_pct / 100.0

    # Initialise with the first price as the first turning point
    turning_point_price: float = float(prices.iloc[0])
    turning_point_date = prices.index[0]
    current_trend: str = "UP"  # assume uptrend initially; will be corrected below
    signal: str = "HOLD"

    # Track extremes within each trend leg for proper filter behaviour.
    # In an UP trend we track the running high; in a DOWN trend the running low.
    # A turning point fires when price reverses by threshold_pct from that extreme.
    extreme_price: float = turning_point_price

    # Run through the full series to find the current turning point and trend
    for date, price in prices.items():
        price = float(price)

        if current_trend == "UP":
            # Track the running high
            if price > extreme_price:
                extreme_price = price
            # Check for reversal: drop of threshold_pct from the high
            pct_change = (price - extreme_price) / extreme_price
            if pct_change <= -threshold:
                turning_point_price = extreme_price
                turning_point_date = date
                current_trend = "DOWN"
                extreme_price = price  # reset extreme to current (new low tracker)
                signal = "SELL"
        else:  # current_trend == "DOWN"
            # Track the running low
            if price < extreme_price:
                extreme_price = price
            # Check for reversal: rally of threshold_pct from the low
            pct_change = (price - extreme_price) / extreme_price
            if pct_change >= threshold:
                turning_point_price = extreme_price
                turning_point_date = date
                current_trend = "UP"
                extreme_price = price  # reset extreme to current (new high tracker)
                signal = "BUY"

    # Current price
    latest_price = float(prices.iloc[-1])
    pct_from_turning_point = (latest_price - turning_point_price) / turning_point_price * 100.0

    # Format date for JSON-serializability
    tp_date_str = _format_date(turning_point_date)

    return {
        "signal": signal,
        "last_turning_point_date": tp_date_str,
        "last_turning_point_price": round(turning_point_price, 4),
        "current_trend": current_trend,
        "pct_from_turning_point": round(pct_from_turning_point, 4),
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _format_date(d) -> str:
    """Convert various date types to an ISO string."""
    if hasattr(d, "isoformat"):
        return d.isoformat()[:10]
    return str(d)[:10]
