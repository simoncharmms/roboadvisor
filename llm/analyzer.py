"""
llm/analyzer.py
---------------
LLM-powered news analysis and recommendation layer.

Uses the Anthropic API (Claude) to produce structured BUY/SELL/HOLD
recommendations for each portfolio position, combining recent news with
the output of the quantitative models.

The prompt template lives in ``llm/prompts/v1_analysis.txt`` and is
versioned so that prompt changes can be tracked independently of code.

Usage
-----
::

    from llm.analyzer import LLMAnalyzer

    analyzer = LLMAnalyzer(api_key="sk-ant-...")
    result = analyzer.analyze(
        ticker="AMUN.PA",
        quant_result=...,   # dict from analyse_ticker()
        news_rows=...,      # list of sqlite3.Row from get_news()
        portfolio_entry=...,# dict from portfolio.json
        total_portfolio_value=1234.56,
    )
    # result: {"recommendation": "HOLD", "confidence": "HIGH", ...}
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Anthropic import — soft dependency
# ---------------------------------------------------------------------------
try:
    import anthropic as _anthropic_lib
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "v1_analysis.txt"
PROMPT_VERSION = "v1"

# Model to use — claude-sonnet is the right balance of quality/cost for daily runs
DEFAULT_MODEL = "claude-sonnet-4-6"

# Max tokens to request in the response (the output is short and structured)
MAX_TOKENS = 512

# Rate-limit: seconds to wait between API calls when processing multiple tickers
INTER_CALL_DELAY = 1.0

# How many news articles to include in the prompt (most recent first)
MAX_NEWS_IN_PROMPT = 8


# ---------------------------------------------------------------------------
# LLMAnalyzer
# ---------------------------------------------------------------------------

class LLMAnalyzer:
    """Wraps the Anthropic API and handles prompt building, calling, and parsing.

    Parameters
    ----------
    api_key : str
        Anthropic API key. Must start with ``sk-ant-``.
    model : str, optional
        Claude model identifier (default: ``claude-sonnet-4-6``).
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        if not _ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic package is not installed. Run: pip install anthropic"
            )
        if not api_key:
            raise ValueError("Anthropic API key must not be empty.")

        self._client = _anthropic_lib.Anthropic(api_key=api_key)
        self._model = model
        self._prompt_template = self._load_prompt_template()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def analyze(
        self,
        ticker: str,
        quant_result: dict,
        news_rows: list,
        portfolio_entry: dict,
        total_portfolio_value: float,
    ) -> dict:
        """Run LLM analysis for a single ticker.

        Parameters
        ----------
        ticker               : str
        quant_result         : dict  – output of ``run.analyse_ticker()``
        news_rows            : list  – sqlite3.Row list from ``data.db.get_news()``
        portfolio_entry      : dict  – the ticker's entry in portfolio.json
        total_portfolio_value: float – sum of all position values (for weight calc)

        Returns
        -------
        dict with keys:
            recommendation  – ``"BUY"`` | ``"SELL"`` | ``"HOLD"``
            confidence      – ``"HIGH"`` | ``"MED"`` | ``"LOW"``
            quant_agreement – ``"agree"`` | ``"disagree"`` | ``"neutral"``
            rationale       – str, 2–4 sentence explanation
            prompt_version  – str, e.g. ``"v1"``
            model           – str, Claude model used
            error           – str or None (set if API call failed)
        """
        prompt = self._build_prompt(
            ticker=ticker,
            quant_result=quant_result,
            news_rows=news_rows,
            portfolio_entry=portfolio_entry,
            total_portfolio_value=total_portfolio_value,
        )

        raw_response = self._call_api(prompt)

        if raw_response is None:
            return self._error_result("API call returned no response.")

        parsed = self._parse_response(raw_response)
        parsed["prompt_version"] = PROMPT_VERSION
        parsed["model"] = self._model
        return parsed

    def analyze_portfolio(
        self,
        portfolio: list[dict],
        all_quant_results: list[dict],
        news_by_ticker: dict[str, list],
        total_portfolio_value: float,
    ) -> dict[str, dict]:
        """Run LLM analysis for all tickers in the portfolio.

        Parameters
        ----------
        portfolio             : list[dict]  – from portfolio.json
        all_quant_results     : list[dict]  – one per ticker
        news_by_ticker        : dict        – {ticker: [news_rows]}
        total_portfolio_value : float

        Returns
        -------
        dict
            Mapping of ``{ticker: analysis_result}``.
        """
        results: dict[str, dict] = {}

        for i, entry in enumerate(portfolio):
            ticker = entry.get("ticker", "")
            if not ticker:
                continue

            quant_result = next(
                (r for r in all_quant_results if r.get("ticker") == ticker), {}
            )
            news_rows = news_by_ticker.get(ticker, [])

            print(f"[llm] Analyzing {ticker} ({i+1}/{len(portfolio)})...")

            try:
                result = self.analyze(
                    ticker=ticker,
                    quant_result=quant_result,
                    news_rows=news_rows,
                    portfolio_entry=entry,
                    total_portfolio_value=total_portfolio_value,
                )
            except Exception as exc:
                print(f"[llm] ERROR for {ticker}: {exc}")
                result = self._error_result(str(exc))

            results[ticker] = result

            # Respect rate limits between calls
            if i < len(portfolio) - 1:
                time.sleep(INTER_CALL_DELAY)

        return results

    # ------------------------------------------------------------------ #
    # Prompt building                                                      #
    # ------------------------------------------------------------------ #

    def _build_prompt(
        self,
        ticker: str,
        quant_result: dict,
        news_rows: list,
        portfolio_entry: dict,
        total_portfolio_value: float,
    ) -> str:
        """Fill the prompt template with live data.

        Parameters
        ----------
        ticker               : str
        quant_result         : dict
        news_rows            : list of sqlite3.Row
        portfolio_entry      : dict
        total_portfolio_value: float

        Returns
        -------
        str
            The fully rendered prompt string.
        """
        # ── Portfolio weight ──────────────────────────────────────────
        shares = portfolio_entry.get("shares", 0)
        currency = portfolio_entry.get("currency", "EUR")
        latest_price = quant_result.get("latest_price")
        position_value = (latest_price or 0) * shares
        weight_pct = (
            round(position_value / total_portfolio_value * 100, 1)
            if total_portfolio_value > 0 else 0.0
        )

        # ── Quant signals ─────────────────────────────────────────────
        yf = quant_result.get("y_filter") or {}
        ar = quant_result.get("arima") or {}
        ga = quant_result.get("garch") or {}
        per_asset = ga.get("per_asset", {}).get(ticker, {})

        y_filter_signal = yf.get("signal", "N/A")
        current_trend = yf.get("current_trend", "N/A")
        pct_from_tp = yf.get("pct_from_turning_point", "N/A")
        arima_1d = _fmt_price(ar.get("forecast_1d"), currency)
        arima_5d = _fmt_price(ar.get("forecast_5d"), currency)
        garch_vol = (
            f"{per_asset.get('annualised_volatility', 0)*100:.2f}%"
            if per_asset.get("annualised_volatility") is not None
            else "N/A"
        )

        # ── News block ────────────────────────────────────────────────
        news_items = list(news_rows)[:MAX_NEWS_IN_PROMPT]
        news_count = len(news_items)

        if news_items:
            news_lines = []
            for item in news_items:
                # sqlite3.Row supports dict-style access
                date_str = _row_get(item, "published_at", "")[:10]
                headline = _row_get(item, "headline", "No headline")
                source = _row_get(item, "source", "Unknown")
                body = _row_get(item, "body", "") or ""
                # Truncate body to 300 chars to keep prompt compact
                snippet = body[:300].replace("\n", " ").strip()
                if snippet:
                    news_lines.append(f"[{date_str}] ({source}) {headline}\n  → {snippet}...")
                else:
                    news_lines.append(f"[{date_str}] ({source}) {headline}")
            news_block = "\n\n".join(news_lines)
        else:
            news_block = "(No recent news articles available for this ticker.)"

        # ── Fill template ─────────────────────────────────────────────
        prompt = self._prompt_template.format(
            ticker=ticker,
            weight_pct=weight_pct,
            shares=shares,
            latest_price=_fmt_price(latest_price, currency),
            currency=currency,
            y_filter_signal=y_filter_signal,
            current_trend=current_trend,
            pct_from_turning_point=pct_from_tp,
            arima_1d=arima_1d,
            arima_5d=arima_5d,
            garch_vol=garch_vol,
            news_count=news_count,
            news_days=7,
            news_block=news_block,
        )
        return prompt

    # ------------------------------------------------------------------ #
    # API call                                                             #
    # ------------------------------------------------------------------ #

    def _call_api(self, prompt: str) -> Optional[str]:
        """Send the prompt to the Anthropic API and return the raw text response.

        Parameters
        ----------
        prompt : str

        Returns
        -------
        str or None
            The text content of the first content block, or None on failure.
        """
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            if message.content and len(message.content) > 0:
                return message.content[0].text.strip()
            return None
        except Exception as exc:
            print(f"[llm] Anthropic API error: {exc}")
            return None

    # ------------------------------------------------------------------ #
    # Response parsing                                                     #
    # ------------------------------------------------------------------ #

    def _parse_response(self, raw: str) -> dict:
        """Parse the structured LLM response into a dict.

        Expected format::

            RECOMMENDATION: BUY
            CONFIDENCE: HIGH
            QUANT_AGREEMENT: agree
            RATIONALE: Some text here.

        Parameters
        ----------
        raw : str
            Raw text from the API response.

        Returns
        -------
        dict with keys: recommendation, confidence, quant_agreement, rationale, error
        """
        result = {
            "recommendation": None,
            "confidence": None,
            "quant_agreement": None,
            "rationale": None,
            "error": None,
        }

        def extract(label: str, text: str) -> Optional[str]:
            """Extract a labelled field from the response."""
            pattern = rf"^{label}:\s*(.+)$"
            match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
            return match.group(1).strip() if match else None

        rec = extract("RECOMMENDATION", raw)
        conf = extract("CONFIDENCE", raw)
        agreement = extract("QUANT_AGREEMENT", raw)
        rationale = extract("RATIONALE", raw)

        # Validate and normalise recommendation
        valid_recs = {"BUY", "SELL", "HOLD"}
        if rec and rec.upper() in valid_recs:
            result["recommendation"] = rec.upper()
        else:
            result["recommendation"] = "HOLD"  # safe default
            result["error"] = f"Could not parse RECOMMENDATION from: {raw[:200]}"

        # Validate confidence
        valid_confs = {"HIGH", "MED", "LOW"}
        if conf and conf.upper() in valid_confs:
            result["confidence"] = conf.upper()
        else:
            result["confidence"] = "LOW"

        # Validate agreement
        valid_agreements = {"agree", "disagree", "neutral"}
        if agreement and agreement.lower() in valid_agreements:
            result["quant_agreement"] = agreement.lower()
        else:
            result["quant_agreement"] = "neutral"

        result["rationale"] = rationale or raw[:500]

        return result

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _load_prompt_template() -> str:
        """Load the prompt template from disk.

        Returns
        -------
        str

        Raises
        ------
        FileNotFoundError
            If the prompt file does not exist.
        """
        if not PROMPT_PATH.exists():
            raise FileNotFoundError(
                f"Prompt template not found at: {PROMPT_PATH}\n"
                f"Expected: llm/prompts/v1_analysis.txt"
            )
        return PROMPT_PATH.read_text(encoding="utf-8")

    @staticmethod
    def _error_result(message: str) -> dict:
        """Return a safe default result with an error message.

        Parameters
        ----------
        message : str

        Returns
        -------
        dict
        """
        return {
            "recommendation": "HOLD",
            "confidence": "LOW",
            "quant_agreement": "neutral",
            "rationale": f"LLM analysis unavailable: {message}",
            "prompt_version": PROMPT_VERSION,
            "model": DEFAULT_MODEL,
            "error": message,
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _fmt_price(value, currency: str = "EUR") -> str:
    """Format a price value for display in the prompt.

    Parameters
    ----------
    value    : float or None
    currency : str

    Returns
    -------
    str
    """
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.4f} {currency}"
    except (TypeError, ValueError):
        return str(value)


def _row_get(row, key: str, default="") -> str:
    """Safely get a value from a sqlite3.Row or dict.

    Parameters
    ----------
    row     : sqlite3.Row or dict
    key     : str
    default : any

    Returns
    -------
    str
    """
    try:
        val = row[key]
        return val if val is not None else default
    except (KeyError, IndexError):
        return default
