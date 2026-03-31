"""
models/backtest.py
------------------
Y%-Filter signal backtest on historical price data.

Strategy
--------
1. Run the Y%-Filter on a rolling window of closing prices.
2. At each bar, determine the *current* signal (BUY / SELL / HOLD).
3. When the signal transitions to BUY → go long (invest full capital).
4. When the signal transitions to SELL → exit (convert back to cash).
5. Compute standard performance metrics at the end.

Metrics
-------
* **total_return_pct** – overall percentage gain from initial_capital
* **sharpe_ratio**     – annualised Sharpe ratio (risk-free rate = 4 %)
* **max_drawdown_pct** – maximum peak-to-trough decline (positive value)
* **win_rate**         – fraction of closed trades that were profitable
* **trades**           – list of closed trade dicts
* **equity_curve**     – list of portfolio values, one per price bar

Notes
-----
The backtest is intentionally simple (no transaction costs, no slippage).
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from models.y_filter import y_filter, _format_date


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RISK_FREE_RATE_ANNUAL = 0.04   # 4 % p.a.
_TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def backtest(
    ticker: str,
    prices: pd.Series,
    threshold_pct: float = 5.0,
    initial_capital: float = 10_000.0,
) -> dict:
    """Backtest the Y%-Filter strategy on a historical price series.

    Parameters
    ----------
    ticker          : str
        Ticker label (used only in the returned trade records).
    prices          : pd.Series
        Daily closing prices indexed by date.  Should contain at least 30
        observations for meaningful results.
    threshold_pct   : float
        Y%-Filter threshold (default 5 %).
    initial_capital : float
        Starting portfolio value in the portfolio's base currency.

    Returns
    -------
    dict with keys:
        total_return_pct   – float
        sharpe_ratio       – float
        max_drawdown_pct   – float  (positive)
        win_rate           – float  (0–1)
        trades             – list of dicts
        equity_curve       – list of floats
    """
    prices = prices.dropna().astype(float)

    if len(prices) < 3:
        raise ValueError(f"backtest requires at least 3 price observations; got {len(prices)}.")

    dates = prices.index
    price_values = prices.values

    equity_curve: list[float] = []
    trades: list[dict] = []

    cash = initial_capital
    shares_held: float = 0.0
    position_open: bool = False
    entry_price: float = 0.0
    entry_date = None

    prev_signal: str = "HOLD"

    for i in range(1, len(price_values)):
        # Compute Y%-Filter signal on all prices up to and including bar i
        window = prices.iloc[: i + 1]
        try:
            result = y_filter(window, threshold_pct=threshold_pct)
            signal = result["signal"]
        except Exception:
            signal = "HOLD"

        current_price = price_values[i]
        current_date = dates[i]

        # Portfolio value at this bar
        portfolio_value = cash + shares_held * current_price

        # Act on signal changes
        if signal == "BUY" and not position_open:
            # Enter long
            shares_held = cash / current_price
            cash = 0.0
            position_open = True
            entry_price = current_price
            entry_date = current_date

        elif signal == "SELL" and position_open:
            # Exit long
            proceeds = shares_held * current_price
            cash = proceeds
            pnl_pct = (current_price - entry_price) / entry_price * 100.0
            trades.append(
                {
                    "ticker": ticker,
                    "entry_date": _format_date(entry_date),
                    "exit_date": _format_date(current_date),
                    "entry_price": round(entry_price, 4),
                    "exit_price": round(current_price, 4),
                    "pnl_pct": round(pnl_pct, 4),
                }
            )
            shares_held = 0.0
            position_open = False

        # Re-compute portfolio value after any trade
        portfolio_value = cash + shares_held * current_price
        equity_curve.append(round(portfolio_value, 4))

        prev_signal = signal

    # Close any open position at the last price
    if position_open and len(price_values) > 0:
        last_price = price_values[-1]
        last_date = dates[-1]
        proceeds = shares_held * last_price
        cash = proceeds
        pnl_pct = (last_price - entry_price) / entry_price * 100.0
        trades.append(
            {
                "ticker": ticker,
                "entry_date": _format_date(entry_date),
                "exit_date": _format_date(last_date),
                "entry_price": round(entry_price, 4),
                "exit_price": round(last_price, 4),
                "pnl_pct": round(pnl_pct, 4),
            }
        )
        shares_held = 0.0
        equity_curve[-1] = round(cash, 4) if equity_curve else round(cash, 4)

    final_value = cash if not position_open else cash + shares_held * price_values[-1]
    if not equity_curve:
        equity_curve = [initial_capital]

    total_return_pct = (final_value - initial_capital) / initial_capital * 100.0
    sharpe = _compute_sharpe(equity_curve)
    max_dd = _compute_max_drawdown(equity_curve)
    win_rate = _compute_win_rate(trades)

    return {
        "total_return_pct": round(total_return_pct, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "win_rate": round(win_rate, 4),
        "trades": trades,
        "equity_curve": equity_curve,
    }


# ---------------------------------------------------------------------------
# Private metric helpers
# ---------------------------------------------------------------------------

def _compute_sharpe(equity_curve: list[float]) -> float:
    """Compute annualised Sharpe ratio from an equity curve.

    Parameters
    ----------
    equity_curve : list[float]
        Portfolio value at each bar.

    Returns
    -------
    float
        Annualised Sharpe ratio, or 0.0 if insufficient data.
    """
    if len(equity_curve) < 2:
        return 0.0

    eq = np.array(equity_curve, dtype=float)
    daily_returns = np.diff(eq) / eq[:-1]

    if len(daily_returns) == 0:
        return 0.0

    rf_daily = _RISK_FREE_RATE_ANNUAL / _TRADING_DAYS_PER_YEAR
    excess = daily_returns - rf_daily
    std = np.std(excess, ddof=1)

    if std == 0 or math.isnan(std):
        return 0.0

    return float(np.mean(excess) / std * math.sqrt(_TRADING_DAYS_PER_YEAR))


def _compute_max_drawdown(equity_curve: list[float]) -> float:
    """Compute maximum peak-to-trough percentage drawdown (positive number).

    Parameters
    ----------
    equity_curve : list[float]

    Returns
    -------
    float
        Maximum drawdown as a positive percentage, e.g. ``15.3`` for −15.3 %.
    """
    if len(equity_curve) < 2:
        return 0.0

    eq = np.array(equity_curve, dtype=float)
    running_max = np.maximum.accumulate(eq)
    drawdowns = (eq - running_max) / running_max * 100.0
    return float(-np.min(drawdowns))


def _compute_win_rate(trades: list[dict]) -> float:
    """Compute the fraction of closed trades with positive PnL.

    Parameters
    ----------
    trades : list[dict]
        Trade records with a ``pnl_pct`` key.

    Returns
    -------
    float
        Win rate in [0, 1], or 0.0 if no trades.
    """
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.get("pnl_pct", 0) > 0)
    return wins / len(trades)
