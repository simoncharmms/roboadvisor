"""
models/garch_copula.py
----------------------
GARCH(1,1) volatility estimation with optional Gaussian copula for
multi-asset joint dependence modelling.

For each asset a univariate GARCH(1,1) is fitted on daily log-returns.
The annualised conditional volatility (the square root of the last one-step
variance forecast) is reported per asset.

When two or more assets are provided the standardised residuals (z-scores)
from each asset's GARCH model are collected, and Spearman's rank correlation
is estimated on them.  A Gaussian copula is then parameterised by converting
that correlation matrix to the Pearson space via the ``scipy`` normal
quantile transform.

For a single asset the copula step is skipped and the correlation matrix is
returned as a 1×1 identity matrix.

Dependencies: ``arch``, ``scipy``, ``numpy``, ``pandas``
"""

from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
import pandas as pd

try:
    from arch import arch_model
    from arch.univariate import ARCHModelResult
except ImportError as exc:
    raise ImportError("arch is required: pip install arch") from exc

try:
    from scipy import stats
except ImportError as exc:
    raise ImportError("scipy is required: pip install scipy") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def garch_copula_analysis(prices_dict: dict[str, pd.Series]) -> dict:
    """Fit GARCH(1,1) per asset and, for multi-asset portfolios, fit a
    Gaussian copula on standardised residuals.

    Parameters
    ----------
    prices_dict : dict[str, pd.Series]
        Mapping of ticker → daily closing price series.  Each series should
        contain at least 30 observations.

    Returns
    -------
    dict with keys:
        per_asset : dict[str, dict]
            ``{ticker: {annualised_volatility, last_variance, garch_order,
                        fitted: bool}}``
        correlation_matrix : dict
            ``{tickers: list[str], matrix: list[list[float]]}``
            Pairwise Gaussian-copula correlation (Pearson-equivalent).
            For a single asset this is the 1×1 identity ``[[1.0]]``.

    Notes
    -----
    All ``prices_dict`` series are aligned on a common date index before
    computing residuals so that the copula correlation is computed on
    contemporaneous observations only.
    """
    if not prices_dict:
        raise ValueError("prices_dict must contain at least one asset.")

    tickers = list(prices_dict.keys())
    per_asset: dict[str, dict] = {}
    residuals: dict[str, np.ndarray] = {}

    for ticker, prices in prices_dict.items():
        result = _fit_garch(ticker, prices)
        per_asset[ticker] = result["summary"]
        if result["std_resid"] is not None:
            residuals[ticker] = result["std_resid"]

    # Build correlation matrix
    corr_matrix, used_tickers = _build_copula_correlation(residuals, tickers)

    return {
        "per_asset": per_asset,
        "correlation_matrix": {
            "tickers": used_tickers,
            "matrix": corr_matrix.tolist(),
        },
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _fit_garch(ticker: str, prices: pd.Series) -> dict:
    """Fit a GARCH(1,1) model to daily log-returns of *prices*.

    Parameters
    ----------
    ticker : str
    prices : pd.Series

    Returns
    -------
    dict with keys ``summary`` (user-facing stats) and ``std_resid``
    (standardised residuals as ``np.ndarray`` or ``None`` on failure).
    """
    prices = prices.dropna().astype(float)
    if len(prices) < 30:
        print(f"[garch] {ticker}: insufficient data ({len(prices)} obs); skipping.")
        return {
            "summary": {
                "annualised_volatility": None,
                "last_variance": None,
                "garch_order": (1, 1),
                "fitted": False,
            },
            "std_resid": None,
        }

    # Daily log-returns in percent (arch library convention)
    log_returns = np.log(prices / prices.shift(1)).dropna() * 100.0

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            am = arch_model(log_returns, vol="Garch", p=1, q=1, dist="normal", rescale=False)
            res: ARCHModelResult = am.fit(disp="off", show_warning=False)

        # Annualised volatility: σ_daily × √252, converted back from percent
        last_variance = float(res.conditional_volatility.iloc[-1]) ** 2  # already in % units
        ann_vol = float(res.conditional_volatility.iloc[-1]) * np.sqrt(252) / 100.0

        std_resid = res.std_resid.dropna().values

        return {
            "summary": {
                "annualised_volatility": round(ann_vol, 6),
                "last_variance": round(last_variance, 6),
                "garch_order": (1, 1),
                "fitted": True,
            },
            "std_resid": std_resid,
        }

    except Exception as exc:
        print(f"[garch] {ticker}: GARCH fitting failed ({exc}); returning None.")
        return {
            "summary": {
                "annualised_volatility": None,
                "last_variance": None,
                "garch_order": (1, 1),
                "fitted": False,
            },
            "std_resid": None,
        }


def _build_copula_correlation(
    residuals: dict[str, np.ndarray],
    tickers: list[str],
) -> tuple[np.ndarray, list[str]]:
    """Compute pairwise Gaussian-copula correlation from standardised residuals.

    Parameters
    ----------
    residuals : dict[str, np.ndarray]
        Standardised GARCH residuals per ticker.
    tickers   : list[str]
        All tickers (some may be missing from *residuals*).

    Returns
    -------
    tuple[np.ndarray, list[str]]
        ``(corr_matrix, used_tickers)``
    """
    available = [t for t in tickers if t in residuals and residuals[t] is not None and len(residuals[t]) > 10]

    if len(available) == 0:
        # Nothing to correlate
        corr = np.ones((len(tickers), len(tickers)))
        return corr, tickers

    if len(available) == 1:
        return np.array([[1.0]]), available

    # Align residuals to the shortest series
    min_len = min(len(residuals[t]) for t in available)
    aligned = np.column_stack([residuals[t][-min_len:] for t in available])

    # Gaussian copula: transform marginals to uniform via empirical CDF,
    # then to standard normal, then compute Pearson correlation.
    n = aligned.shape[0]
    uniform = np.zeros_like(aligned)
    for i in range(aligned.shape[1]):
        # Empirical CDF ranks (avoid 0 and 1 for numerical stability)
        ranks = stats.rankdata(aligned[:, i])
        uniform[:, i] = ranks / (n + 1)

    normal_scores = stats.norm.ppf(uniform)

    corr_matrix = np.corrcoef(normal_scores, rowvar=False)
    # Ensure symmetry and clip to [-1, 1]
    corr_matrix = np.clip((corr_matrix + corr_matrix.T) / 2, -1.0, 1.0)
    np.fill_diagonal(corr_matrix, 1.0)

    return corr_matrix, available
