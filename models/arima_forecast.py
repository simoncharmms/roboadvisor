"""
models/arima_forecast.py
------------------------
ARIMA forecasting with automatic order selection via AIC.

The model exhaustively searches over (p, d, q) ∈ {0,1,2}³, fits each
candidate on the log-price series, and picks the specification with the
lowest AIC.  On any failure (convergence error, singular matrix, etc.) it
falls back to ARIMA(1,1,1).

Forecasts are in the *original price space* (exponential of log-price
forecasts), and the confidence intervals are similarly back-transformed.
"""

from __future__ import annotations

import warnings
from itertools import product
from typing import Optional

import numpy as np
import pandas as pd

try:
    from statsmodels.tsa.arima.model import ARIMA
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
except ImportError as exc:
    raise ImportError("statsmodels is required: pip install statsmodels") from exc


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_P_RANGE = range(0, 3)  # 0, 1, 2
_D_RANGE = range(0, 3)
_Q_RANGE = range(0, 3)
_FALLBACK_ORDER = (1, 1, 1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def arima_forecast(prices: pd.Series, forecast_days: int = 5) -> dict:
    """Fit the best ARIMA model and return a multi-day price forecast.

    Parameters
    ----------
    prices       : pd.Series
        Daily closing prices (any numeric index or DatetimeIndex).  Should
        contain at least 30 observations for reliable estimation.
    forecast_days : int
        Number of calendar days to forecast ahead (default 5).

    Returns
    -------
    dict with keys:
        forecast_1d        – forecast price 1 day ahead
        forecast_5d        – forecast price ``forecast_days`` days ahead
        forecast_series    – list of ``forecast_days`` forecast values
        confidence_lower   – list of lower 95 % CI values
        confidence_upper   – list of upper 95 % CI values
        order              – selected (p, d, q) tuple

    Notes
    -----
    Fitting is done on log-prices for numerical stability; results are
    exponentiated back to the original scale.
    """
    if prices is None or len(prices) < 5:
        raise ValueError("arima_forecast requires at least 5 price observations.")

    prices = prices.dropna().astype(float)

    log_prices = np.log(prices)

    best_order, best_aic = _select_order(log_prices)

    forecast_series, ci_lower, ci_upper = _fit_and_forecast(log_prices, best_order, forecast_days)

    # Back-transform from log-space
    fc_prices = np.exp(forecast_series).tolist()
    ci_lower_prices = np.exp(ci_lower).tolist()
    ci_upper_prices = np.exp(ci_upper).tolist()

    return {
        "forecast_1d": round(fc_prices[0], 4) if fc_prices else None,
        "forecast_5d": round(fc_prices[min(forecast_days - 1, len(fc_prices) - 1)], 4) if fc_prices else None,
        "forecast_series": [round(v, 4) for v in fc_prices],
        "confidence_lower": [round(v, 4) for v in ci_lower_prices],
        "confidence_upper": [round(v, 4) for v in ci_upper_prices],
        "order": best_order,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _select_order(log_prices: pd.Series) -> tuple[tuple, float]:
    """Search over (p,d,q) ∈ {0,1,2}³ and return the order with lowest AIC.

    Falls back to ``_FALLBACK_ORDER`` if no candidate converges.

    Parameters
    ----------
    log_prices : pd.Series
        Log-transformed price series.

    Returns
    -------
    tuple[tuple, float]
        ``(best_order, best_aic)``
    """
    best_order = _FALLBACK_ORDER
    best_aic = np.inf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        warnings.simplefilter("ignore", UserWarning)
        warnings.simplefilter("ignore", RuntimeWarning)

        for p, d, q in product(_P_RANGE, _D_RANGE, _Q_RANGE):
            if p == 0 and d == 0 and q == 0:
                continue
            try:
                model = ARIMA(log_prices, order=(p, d, q))
                result = model.fit(method_kwargs={"warn_convergence": False})
                if result.aic < best_aic:
                    best_aic = result.aic
                    best_order = (p, d, q)
            except Exception:
                continue

    return best_order, best_aic


def _fit_and_forecast(
    log_prices: pd.Series,
    order: tuple,
    forecast_days: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fit ARIMA(*order*) on *log_prices* and return point + CI forecasts.

    Falls back to ``_FALLBACK_ORDER`` if the specified order fails.

    Parameters
    ----------
    log_prices    : pd.Series
    order         : tuple  – (p, d, q)
    forecast_days : int

    Returns
    -------
    tuple[np.ndarray, np.ndarray, np.ndarray]
        ``(forecast, ci_lower, ci_upper)`` — all in log-space.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model = ARIMA(log_prices, order=order)
            result = model.fit(method_kwargs={"warn_convergence": False})
        except Exception as exc:
            print(f"[arima] Order {order} failed ({exc}); falling back to {_FALLBACK_ORDER}.")
            try:
                model = ARIMA(log_prices, order=_FALLBACK_ORDER)
                result = model.fit(method_kwargs={"warn_convergence": False})
            except Exception as exc2:
                # Last resort: return the last known price as a flat forecast
                last_log = float(log_prices.iloc[-1])
                fc = np.full(forecast_days, last_log)
                return fc, fc * 0.95, fc * 1.05

        try:
            forecast_res = result.get_forecast(steps=forecast_days)
            fc = forecast_res.predicted_mean.values
            ci = forecast_res.conf_int(alpha=0.05)
            ci_lower = ci.iloc[:, 0].values
            ci_upper = ci.iloc[:, 1].values
        except Exception as exc:
            print(f"[arima] Forecast extraction failed ({exc}); using flat fallback.")
            last_log = float(log_prices.iloc[-1])
            fc = np.full(forecast_days, last_log)
            ci_lower = fc * 0.95
            ci_upper = fc * 1.05

    return fc, ci_lower, ci_upper
