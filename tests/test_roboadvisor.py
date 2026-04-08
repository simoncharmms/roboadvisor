"""
tests/test_roboadvisor.py
-------------------------
Regression and behaviour tests for the roboadvisor project.

Covers:
  BUG A – upsert_signal COALESCE merge (quant fields preserved after LLM upsert)
  BUG B – backtest trades actually fire on a volatile price series
  BUG C – _compute_sharpe stability (flat curve → 0.0, clamping to [-50, 50])
  BUG D1 – export_dashboard includes `news_by_ticker` key

Author: Odysseus (QA)
"""

from __future__ import annotations

import math
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem_db():
    """In-memory SQLite with roboadvisor schema initialised."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from data.db import init_db, get_connection

    conn = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    yield conn
    conn.close()


def _read_signal(conn, ticker: str, dt: str) -> sqlite3.Row:
    """Helper: fetch a single signals row."""
    row = conn.execute(
        "SELECT * FROM signals WHERE ticker=? AND date=?", (ticker, dt)
    ).fetchone()
    assert row is not None, f"No signal row for {ticker}/{dt}"
    return row


# ===========================================================================
# BUG A — upsert_signal COALESCE merge
# ===========================================================================

class TestUpsertSignalMerge:
    """
    Regression tests for the COALESCE-based upsert introduced to fix BUG A.

    The scenario that was broken:
      1. Morning pipeline writes quant fields (y_filter, ARIMA, GARCH).
      2. LLM pipeline calls upsert_signal with only llm_* fields set.
      3. Old INSERT OR REPLACE would wipe quant fields → all NULL.
      4. Fixed version uses COALESCE → quant fields survive.
    """

    def test_quant_fields_preserved_after_llm_upsert(self, mem_db):
        """Core regression: LLM upsert must NOT overwrite existing quant values."""
        from data.db import upsert_signal

        ticker, dt = "TEST", "2026-04-08"

        # Step 1 – quant pass
        upsert_signal(
            mem_db, ticker, dt,
            y_filter_signal="BUY",
            arima_forecast_1d=1.23,
            arima_forecast_5d=4.56,
            garch_volatility=0.012,
        )
        mem_db.commit()

        # Step 2 – LLM pass (only llm_* fields, everything else None)
        upsert_signal(
            mem_db, ticker, dt,
            llm_recommendation="BUY",
            llm_confidence="HIGH",
            llm_rationale="Looks good",
            llm_quant_agreement="AGREE",
        )
        mem_db.commit()

        row = _read_signal(mem_db, ticker, dt)

        # Quant fields must be preserved
        assert row["y_filter_signal"] == "BUY", "y_filter_signal was wiped by LLM upsert"
        assert abs(row["arima_forecast_1d"] - 1.23) < 1e-9, "arima_forecast_1d was wiped"
        assert abs(row["arima_forecast_5d"] - 4.56) < 1e-9, "arima_forecast_5d was wiped"
        assert abs(row["garch_volatility"] - 0.012) < 1e-9, "garch_volatility was wiped"

        # LLM fields must also be set
        assert row["llm_recommendation"] == "BUY"
        assert row["llm_confidence"] == "HIGH"
        assert row["llm_rationale"] == "Looks good"
        assert row["llm_quant_agreement"] == "AGREE"

    def test_llm_fields_preserved_after_quant_upsert(self, mem_db):
        """Reverse scenario: quant upsert must not wipe existing LLM fields."""
        from data.db import upsert_signal

        ticker, dt = "TEST2", "2026-04-08"

        # Step 1 – LLM pass first
        upsert_signal(
            mem_db, ticker, dt,
            llm_recommendation="SELL",
            llm_confidence="MEDIUM",
            llm_rationale="Overbought",
        )
        mem_db.commit()

        # Step 2 – Quant pass
        upsert_signal(
            mem_db, ticker, dt,
            y_filter_signal="SELL",
            arima_forecast_1d=0.5,
            arima_forecast_5d=0.9,
            garch_volatility=0.02,
        )
        mem_db.commit()

        row = _read_signal(mem_db, ticker, dt)

        assert row["llm_recommendation"] == "SELL", "llm_recommendation was wiped by quant upsert"
        assert row["llm_confidence"] == "MEDIUM", "llm_confidence was wiped"
        assert row["llm_rationale"] == "Overbought", "llm_rationale was wiped"
        assert row["y_filter_signal"] == "SELL"

    def test_upsert_creates_row_on_first_insert(self, mem_db):
        """upsert_signal must create the row when it does not exist yet."""
        from data.db import upsert_signal

        ticker, dt = "NEW", "2026-01-01"
        upsert_signal(mem_db, ticker, dt, y_filter_signal="HOLD", arima_forecast_1d=99.9)
        mem_db.commit()

        row = _read_signal(mem_db, ticker, dt)
        assert row["y_filter_signal"] == "HOLD"
        assert abs(row["arima_forecast_1d"] - 99.9) < 1e-9

    def test_upsert_with_all_none_does_not_overwrite(self, mem_db):
        """
        Calling upsert_signal with all-None fields on an existing row
        must leave every column unchanged (COALESCE semantics).
        """
        from data.db import upsert_signal

        ticker, dt = "COALESCE_TEST", "2026-04-01"
        upsert_signal(
            mem_db, ticker, dt,
            y_filter_signal="HOLD",
            arima_forecast_1d=5.0,
            llm_recommendation="HOLD",
        )
        mem_db.commit()

        # All-None upsert — should be a no-op on existing values
        upsert_signal(mem_db, ticker, dt)
        mem_db.commit()

        row = _read_signal(mem_db, ticker, dt)
        assert row["y_filter_signal"] == "HOLD"
        assert abs(row["arima_forecast_1d"] - 5.0) < 1e-9
        assert row["llm_recommendation"] == "HOLD"

    def test_upsert_overwrites_when_new_non_none_value_provided(self, mem_db):
        """If a non-None value is supplied, it must replace the old value."""
        from data.db import upsert_signal

        ticker, dt = "OVERWRITE", "2026-04-02"
        upsert_signal(mem_db, ticker, dt, y_filter_signal="HOLD")
        mem_db.commit()

        upsert_signal(mem_db, ticker, dt, y_filter_signal="BUY")
        mem_db.commit()

        row = _read_signal(mem_db, ticker, dt)
        assert row["y_filter_signal"] == "BUY", "New non-None value should overwrite old value"

    def test_multiple_tickers_same_date_isolation(self, mem_db):
        """
        Upserting LLM fields for ticker A must not affect ticker B's quant fields
        even when they share the same date.
        """
        from data.db import upsert_signal

        dt = "2026-04-08"
        upsert_signal(mem_db, "AAAA", dt, y_filter_signal="BUY", arima_forecast_1d=10.0)
        upsert_signal(mem_db, "BBBB", dt, y_filter_signal="SELL", arima_forecast_1d=20.0)
        mem_db.commit()

        # LLM upsert for AAAA only
        upsert_signal(mem_db, "AAAA", dt, llm_recommendation="BUY")
        mem_db.commit()

        row_b = _read_signal(mem_db, "BBBB", dt)
        assert row_b["y_filter_signal"] == "SELL", "Ticker B quant fields must not be affected"
        assert abs(row_b["arima_forecast_1d"] - 20.0) < 1e-9


# ===========================================================================
# BUG C — _compute_sharpe stability
# ===========================================================================

class TestComputeSharpe:
    """Tests for the Sharpe ratio calculation in models/backtest.py."""

    @staticmethod
    def _sharpe(curve):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import _compute_sharpe
        return _compute_sharpe(curve)

    def test_flat_equity_curve_returns_zero(self):
        """
        BUG C regression: flat equity curve (all-HOLD strategy) must return 0.0,
        not NaN or an astronomical negative number (~-9.29e16).
        """
        flat = [10_000.0] * 760
        result = self._sharpe(flat)
        assert result == 0.0, f"Expected 0.0 for flat curve, got {result}"

    def test_flat_curve_is_not_nan(self):
        flat = [10_000.0] * 100
        result = self._sharpe(flat)
        assert not math.isnan(result), "Sharpe must not be NaN for flat curve"

    def test_flat_curve_is_not_inf(self):
        flat = [10_000.0] * 100
        result = self._sharpe(flat)
        assert math.isfinite(result), "Sharpe must be finite for flat curve"

    def test_single_element_curve_returns_zero(self):
        """Edge case: only one bar — cannot compute returns."""
        assert self._sharpe([10_000.0]) == 0.0

    def test_empty_curve_returns_zero(self):
        """Edge case: empty curve — must not crash."""
        assert self._sharpe([]) == 0.0

    def test_sharpe_clamped_to_positive_50(self):
        """
        An extremely fast-rising equity curve would produce a massive Sharpe.
        Must be clamped to +50.
        """
        # Equity doubles every step for 100 steps → returns ≈ 100 % per bar
        eq = [10_000.0 * (2.0 ** i) for i in range(100)]
        result = self._sharpe(eq)
        assert result <= 50.0, f"Sharpe not clamped to +50, got {result}"

    def test_sharpe_clamped_to_negative_50(self):
        """
        A catastrophically falling equity curve must be clamped to -50.
        """
        # Near-zero std with tiny negative mean → could produce -9e16 before fix
        eq = [10_000.0] * 500 + [9_999.9]
        result = self._sharpe(eq)
        assert result >= -50.0, f"Sharpe not clamped to -50, got {result}"

    def test_sharpe_in_bounds_for_normal_curve(self):
        """A realistic equity curve should produce Sharpe well within bounds."""
        rng = np.random.default_rng(42)
        returns = rng.normal(loc=0.0008, scale=0.01, size=252)
        eq = list(10_000.0 * np.cumprod(1 + returns))
        result = self._sharpe(eq)
        assert -50.0 <= result <= 50.0, f"Sharpe out of bounds: {result}"
        assert not math.isnan(result)
        assert math.isfinite(result)

    def test_sharpe_near_zero_std_does_not_explode(self):
        """
        Equity curve with near-zero std (759 identical values, 1 tiny deviation)
        — the original bug scenario from BUG C brief.

        The full fix should return 0.0.  The current implementation uses
        _MIN_STD = 1e-8, but for this case std ≈ 7.26e-8 (barely above the
        guard), producing a raw Sharpe of ~-34710 which then clamps to -50.0.

        This test asserts the *minimal* requirement: no explosion (finite,
        in [-50, 50]).  The stricter test below flags the remaining gap.
        """
        curve = [10_000.0] * 759 + [9_999.98]
        result = self._sharpe(curve)
        # Must not be NaN, must not be an astronomical number
        assert math.isfinite(result), f"Sharpe must be finite, got {result}"
        assert -50.0 <= result <= 50.0, f"Sharpe must be in [-50, 50], got {result}"

    def test_sharpe_near_zero_std_ideally_zero(self):
        """
        KNOWN RESIDUAL GAP (BUG C partial fix): for an almost-flat curve where
        std is tiny but just above _MIN_STD = 1e-8, _compute_sharpe returns -50.0
        instead of 0.0.

        The fix prevents the astronomical explosion (-9e16) but _MIN_STD is too
        tight. Recommend raising _MIN_STD to 1e-5 (or making it relative to
        mean equity) so near-flat curves correctly return 0.0.

        This test XFAIL documents the known gap so CI will alert if it's fixed.
        """
        curve = [10_000.0] * 759 + [9_999.98]
        result = self._sharpe(curve)
        # Ideally 0.0 — currently -50.0 due to _MIN_STD being too tight.
        # Remove the xfail marker once _MIN_STD is raised.
        if result != 0.0:
            pytest.xfail(
                f"_MIN_STD too tight: near-flat curve returns {result} instead of 0.0. "
                "Raise _MIN_STD from 1e-8 to 1e-5 to fix this residual gap."
            )


# ===========================================================================
# BUG B — Backtest fires real trades on a volatile series
# ===========================================================================

class TestBacktestTrades:
    """
    Tests that the backtest engine actually executes BUY/SELL trades and
    produces a non-zero total_return on a price series with clear 5%+ swings.
    """

    @staticmethod
    def _make_volatile_prices(n_swings: int = 6, swing_pct: float = 0.10) -> pd.Series:
        """
        Build a synthetic price series with alternating +swing_pct / -swing_pct legs.
        Each leg is 20 bars long so the Y%-filter has time to detect the reversal.
        """
        prices = [100.0]
        direction = 1  # +1 = up, -1 = down
        bars_per_leg = 20
        for _ in range(n_swings):
            for _ in range(bars_per_leg):
                prices.append(prices[-1] * (1 + direction * swing_pct / bars_per_leg))
            direction *= -1

        dates = pd.date_range("2024-01-02", periods=len(prices), freq="B")
        return pd.Series(prices, index=dates)

    def test_volatile_series_produces_trades(self):
        """BUG B regression: at least one trade must fire on a clearly volatile series."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import backtest

        prices = self._make_volatile_prices(n_swings=6, swing_pct=0.12)
        result = backtest("SYNTH", prices, threshold_pct=5.0)

        assert len(result["trades"]) > 0, (
            f"No trades fired despite 12% swings — BUG B may not be fixed. "
            f"total_return={result['total_return_pct']}"
        )

    def test_volatile_series_total_return_nonzero(self):
        """BUG B: total_return_pct must not be exactly 0.0 on a volatile series."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import backtest

        prices = self._make_volatile_prices(n_swings=6, swing_pct=0.12)
        result = backtest("SYNTH", prices, threshold_pct=5.0)

        assert result["total_return_pct"] != 0.0, (
            "total_return_pct is exactly 0.0 despite trades — check backtest logic"
        )

    def test_upward_trending_series_positive_return(self):
        """
        On a series that trends up >5% then down >5% then up again,
        we expect the backtest to catch at least one profitable trade.
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import backtest

        # Strong up/down/up sawtooth with 15% swings
        prices = self._make_volatile_prices(n_swings=8, swing_pct=0.15)
        result = backtest("SYNTH2", prices, threshold_pct=5.0)

        # At minimum, trades should fire
        assert len(result["trades"]) >= 1, "Should fire at least one trade on 15% swings"

    def test_flat_price_series_no_trades(self):
        """Flat prices → no BUY/SELL, zero return (expected behaviour)."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import backtest

        dates = pd.date_range("2024-01-02", periods=50, freq="B")
        prices = pd.Series([100.0] * 50, index=dates)
        result = backtest("FLAT", prices, threshold_pct=5.0)

        assert result["trades"] == [], "No trades expected on flat prices"
        assert result["total_return_pct"] == 0.0

    def test_minimum_price_observations_raises(self):
        """backtest must raise ValueError with fewer than 3 observations."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import backtest

        dates = pd.date_range("2024-01-02", periods=2, freq="B")
        prices = pd.Series([100.0, 105.0], index=dates)
        with pytest.raises(ValueError):
            backtest("SHORT", prices, threshold_pct=5.0)

    def test_equity_curve_length_matches_prices(self):
        """equity_curve must have exactly len(prices) - 1 entries (one per bar after bar 0)."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import backtest

        prices = self._make_volatile_prices(n_swings=4, swing_pct=0.08)
        result = backtest("EC", prices, threshold_pct=5.0)

        # The loop runs from i=1 to len-1, so equity_curve has len-1 entries
        assert len(result["equity_curve"]) == len(prices) - 1

    def test_backtest_result_keys_present(self):
        """Return dict must contain all documented keys."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import backtest

        prices = self._make_volatile_prices(n_swings=2, swing_pct=0.08)
        result = backtest("KEYS", prices, threshold_pct=5.0)

        for key in ("total_return_pct", "sharpe_ratio", "max_drawdown_pct",
                    "win_rate", "trades", "equity_curve"):
            assert key in result, f"Missing key in backtest result: {key}"

    def test_sharpe_finite_after_backtest(self):
        """sharpe_ratio returned by backtest must be finite (not NaN or inf)."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import backtest

        # Both flat (potential Sharpe explosion) and volatile
        for swing in (0.0, 0.12):
            if swing == 0.0:
                dates = pd.date_range("2024-01-02", periods=100, freq="B")
                prices = pd.Series([100.0] * 100, index=dates)
            else:
                prices = self._make_volatile_prices(n_swings=4, swing_pct=swing)
            result = backtest("SR", prices, threshold_pct=5.0)
            assert math.isfinite(result["sharpe_ratio"]), (
                f"sharpe_ratio not finite for swing={swing}: {result['sharpe_ratio']}"
            )

    def test_single_large_swing_up_then_down(self):
        """
        A single 10% up swing followed by a 10% down swing with 5% threshold —
        should capture the BUY at the top of the up leg (or at transition).
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import backtest

        # Build a single up-then-down series: 30 bars up 10%, 30 bars down 10%
        prices_list = [100.0]
        for _ in range(30):
            prices_list.append(prices_list[-1] * (1 + 0.10 / 30))
        for _ in range(30):
            prices_list.append(prices_list[-1] * (1 - 0.10 / 30))

        dates = pd.date_range("2024-01-02", periods=len(prices_list), freq="B")
        prices = pd.Series(prices_list, index=dates)
        result = backtest("SWING1", prices, threshold_pct=5.0)

        # With 10% swing and 5% threshold, y_filter should detect a signal
        # Either trades fired or not — but total_return and sharpe must be sane
        assert math.isfinite(result["total_return_pct"])
        assert math.isfinite(result["sharpe_ratio"])
        assert -50.0 <= result["sharpe_ratio"] <= 50.0


# ===========================================================================
# BUG D1 — Dashboard JSON export includes `news_by_ticker`
# ===========================================================================

class TestDashboardNewsExport:
    """Tests that export_dashboard.py includes `news_by_ticker` in the JSON output."""

    @pytest.fixture()
    def tmp_export_env(self, tmp_path):
        """
        Create a minimal but complete environment for export_dashboard:
        - An in-memory-style temp SQLite db with schema + some data
        - A minimal portfolio.json
        - No trades file
        Returns (db_path, portfolio_path, out_path).
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from data.db import init_db

        db_path = tmp_path / "test_roboadvisor.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        init_db(conn)

        # Insert a price row
        conn.execute(
            "INSERT INTO prices (ticker, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("TST", "2026-04-07", 100.0, 101.0, 99.0, 100.5, 1000),
        )

        # Insert a signal row
        conn.execute(
            "INSERT INTO signals (ticker, date, y_filter_signal, arima_forecast_1d) "
            "VALUES (?, ?, ?, ?)",
            ("TST", "2026-04-07", "HOLD", 100.9),
        )

        # Insert a news row with published_at within last 7 days
        today = date.today().isoformat()
        conn.execute(
            "INSERT INTO news (ticker, published_at, headline, source, url, body, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("TST", today, "Test Headline", "Reuters", "http://example.com/1",
             "Test body text", today),
        )
        conn.commit()
        conn.close()

        portfolio_path = tmp_path / "portfolio.json"
        portfolio_path.write_text(
            '{"portfolio": [{"ticker": "TST", "name": "Test Corp", "shares": 10, "currency": "EUR"}]}'
        )

        out_path = tmp_path / "dashboard" / "dashboard_data.json"

        return db_path, portfolio_path, out_path

    def test_export_includes_news_by_ticker_key(self, tmp_export_env):
        """BUG D1 regression: exported JSON must contain the 'news_by_ticker' key."""
        import sys, json
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from export_dashboard import build_export, load_portfolio, load_price_history, \
            load_signals, load_backtest_results, load_news

        db_path, portfolio_path, out_path = tmp_export_env

        portfolio = load_portfolio(portfolio_path)
        tickers = [h["ticker"] for h in portfolio]

        conn = sqlite3.connect(str(db_path))
        try:
            price_history = load_price_history(conn, tickers, days=365)
            suggestions = load_signals(conn, tickers)
            backtest_results = load_backtest_results(conn, tickers)
            news = load_news(conn, tickers)
        finally:
            conn.close()

        export = build_export(portfolio, price_history, suggestions, backtest_results, [], news)

        assert "news_by_ticker" in export, (
            "Dashboard export is missing 'news_by_ticker' key — BUG D1 not fixed"
        )

    def test_export_news_by_ticker_contains_ticker_data(self, tmp_export_env):
        """news_by_ticker must map ticker → list of article dicts."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from export_dashboard import build_export, load_portfolio, load_price_history, \
            load_signals, load_backtest_results, load_news

        db_path, portfolio_path, out_path = tmp_export_env

        portfolio = load_portfolio(portfolio_path)
        tickers = [h["ticker"] for h in portfolio]

        conn = sqlite3.connect(str(db_path))
        try:
            price_history = load_price_history(conn, tickers, days=365)
            suggestions = load_signals(conn, tickers)
            backtest_results = load_backtest_results(conn, tickers)
            news = load_news(conn, tickers)
        finally:
            conn.close()

        export = build_export(portfolio, price_history, suggestions, backtest_results, [], news)

        news_by_ticker = export["news_by_ticker"]
        assert isinstance(news_by_ticker, dict), "news_by_ticker must be a dict"
        assert "TST" in news_by_ticker, "news_by_ticker must have an entry for 'TST'"
        assert isinstance(news_by_ticker["TST"], list), "news_by_ticker['TST'] must be a list"
        assert len(news_by_ticker["TST"]) >= 1, "Expected at least 1 article for TST"

    def test_export_news_article_has_required_fields(self, tmp_export_env):
        """Each news article dict must contain the documented keys."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from export_dashboard import build_export, load_portfolio, load_price_history, \
            load_signals, load_backtest_results, load_news

        db_path, portfolio_path, out_path = tmp_export_env

        portfolio = load_portfolio(portfolio_path)
        tickers = [h["ticker"] for h in portfolio]

        conn = sqlite3.connect(str(db_path))
        try:
            price_history = load_price_history(conn, tickers, days=365)
            suggestions = load_signals(conn, tickers)
            backtest_results = load_backtest_results(conn, tickers)
            news = load_news(conn, tickers)
        finally:
            conn.close()

        export = build_export(portfolio, price_history, suggestions, backtest_results, [], news)

        articles = export["news_by_ticker"].get("TST", [])
        assert articles, "No articles found for TST"
        article = articles[0]
        for field in ("published_at", "headline", "source"):
            assert field in article, f"Article missing field: {field}"

    def test_export_news_by_ticker_is_empty_dict_when_no_news(self, tmp_path):
        """When there are no news rows, news_by_ticker must be {} not absent."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from data.db import init_db
        from export_dashboard import build_export, load_portfolio, load_price_history, \
            load_signals, load_backtest_results, load_news

        db_path = tmp_path / "empty_news.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        init_db(conn)
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES (?, ?, ?)",
            ("NONEWS", "2026-04-07", 50.0),
        )
        conn.commit()

        portfolio = [{"ticker": "NONEWS", "name": "No News Corp",
                      "shares": 1, "currency": "EUR",
                      "isin": "", "wkn": ""}]

        price_history = load_price_history(conn, ["NONEWS"], days=365)
        suggestions = load_signals(conn, ["NONEWS"])
        backtest_results = load_backtest_results(conn, ["NONEWS"])
        news = load_news(conn, ["NONEWS"])
        conn.close()

        export = build_export(portfolio, price_history, suggestions, backtest_results, [], news)

        assert "news_by_ticker" in export
        assert export["news_by_ticker"] == {"NONEWS": []}, (
            "Should return empty list for ticker with no news, "
            f"got: {export['news_by_ticker']}"
        )

    def test_build_export_without_news_arg_still_has_key(self):
        """
        build_export with news_by_ticker=None (old call signature) must still
        produce the key with an empty dict — defensive backward-compat test.
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from export_dashboard import build_export

        export = build_export(
            portfolio=[],
            price_history={},
            suggestions=[],
            backtest_results=[],
            executed_trades=[],
            news_by_ticker=None,
        )
        assert "news_by_ticker" in export
        assert export["news_by_ticker"] == {}


# ===========================================================================
# Adversarial / edge-case extras
# ===========================================================================

class TestAdversarialEdgeCases:
    """Extra edge cases Achilles might have missed."""

    def test_upsert_signal_duplicate_insert_does_not_duplicate_rows(self, mem_db):
        """Calling upsert twice must not create two rows — only one row per (ticker, date)."""
        from data.db import upsert_signal

        ticker, dt = "DUPL", "2026-04-08"
        upsert_signal(mem_db, ticker, dt, y_filter_signal="BUY")
        upsert_signal(mem_db, ticker, dt, y_filter_signal="SELL")
        mem_db.commit()

        count = mem_db.execute(
            "SELECT COUNT(*) FROM signals WHERE ticker=? AND date=?", (ticker, dt)
        ).fetchone()[0]
        assert count == 1, f"Expected 1 row, found {count} — INSERT OR REPLACE introduced a dupe"

    def test_sharpe_two_element_curve(self):
        """Two-element equity curve: one return, one element std → should return 0 or valid."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import _compute_sharpe

        result = _compute_sharpe([10_000.0, 10_100.0])
        assert math.isfinite(result), f"Two-element curve gave non-finite Sharpe: {result}"
        assert -50.0 <= result <= 50.0

    def test_sharpe_all_identical_except_last_is_clamped(self):
        """
        The pathological case from BUG C: 759 identical values + 1 tiny deviation.

        Before the fix this gave ~-9.29e16 (astronomical explosion).
        After the fix: std ≈ 5.8e-7, above _MIN_STD=1e-8, raw Sharpe ≈ -4339,
        clamped to -50.0.

        The clamping *prevents* the explosion, which is the core BUG C fix.
        The result is still -50.0 (not ideal 0.0) — that's a known residual gap
        documented separately.  This test asserts: no explosion, in bounds.
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import _compute_sharpe

        curve = [10_000.0] * 759 + [9_999.84]  # approx -rf_daily contribution
        result = _compute_sharpe(curve)
        assert math.isfinite(result), f"Must be finite, got {result}"
        assert -50.0 <= result <= 50.0, f"Must be in [-50, 50], got {result}"
        # Before fix: result would be ~-9.29e16 — verify it's not
        assert result > -1e10, f"Sharpe exploded (pre-fix behaviour returned): {result}"

    def test_backtest_nanfree_equity_curve(self):
        """equity_curve must contain no NaN or inf values."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import backtest

        prices_list = [100.0]
        for i in range(120):
            prices_list.append(prices_list[-1] * (1 + (0.08 if i % 20 < 10 else -0.08) / 10))
        dates = pd.date_range("2024-01-02", periods=len(prices_list), freq="B")
        prices = pd.Series(prices_list, index=dates)

        result = backtest("NANCHECK", prices, threshold_pct=5.0)
        for i, v in enumerate(result["equity_curve"]):
            assert math.isfinite(v), f"equity_curve[{i}] is not finite: {v}"

    def test_upsert_signal_garch_volatility_not_wiped(self, mem_db):
        """
        Specifically test garch_volatility preservation — the field most likely
        to be forgotten in a partial upsert.
        """
        from data.db import upsert_signal

        ticker, dt = "GARCH_TEST", "2026-04-08"
        upsert_signal(mem_db, ticker, dt, garch_volatility=0.025)
        mem_db.commit()

        # Simulate LLM step setting only llm fields
        upsert_signal(mem_db, ticker, dt, llm_recommendation="HOLD", llm_confidence="LOW")
        mem_db.commit()

        row = _read_signal(mem_db, ticker, dt)
        assert abs(row["garch_volatility"] - 0.025) < 1e-9, (
            f"garch_volatility was wiped, got {row['garch_volatility']}"
        )

    def test_y_filter_signal_returns_valid_values(self):
        """y_filter must only return BUY, SELL, or HOLD — not arbitrary strings."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.y_filter import y_filter

        dates = pd.date_range("2024-01-02", periods=60, freq="B")
        # Volatile series
        values = [100.0 * (1 + 0.02 * (1 if i % 10 < 5 else -1)) for i in range(60)]
        prices = pd.Series(values, index=dates)

        result = y_filter(prices, threshold_pct=5.0)
        assert result["signal"] in ("BUY", "SELL", "HOLD"), (
            f"y_filter returned unexpected signal: {result['signal']}"
        )

    def test_backtest_win_rate_between_0_and_1(self):
        """win_rate must be in [0, 1] regardless of outcome."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from models.backtest import backtest

        prices_list = [100.0]
        direction = 1
        for _ in range(10):
            for _ in range(15):
                prices_list.append(prices_list[-1] * (1 + direction * 0.008))
            direction *= -1
        dates = pd.date_range("2024-01-02", periods=len(prices_list), freq="B")
        prices = pd.Series(prices_list, index=dates)

        result = backtest("WINRATE", prices, threshold_pct=5.0)
        assert 0.0 <= result["win_rate"] <= 1.0, (
            f"win_rate out of range: {result['win_rate']}"
        )
