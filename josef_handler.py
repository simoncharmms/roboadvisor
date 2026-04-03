#!/usr/bin/env python3
"""
josef_handler.py
----------------
WhatsApp message handler for Josef (Simon's father).
Processes inbound German messages, detects intent, and responds
via WhatsApp with portfolio status, backtest explanations, or
friendly acknowledgments — all in German.

Usage:
    python3 josef_handler.py --message "Wie läuft mein Portfolio?" --sender "+49XXXXXXXXXX"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
DASHBOARD_JSON = PROJECT_ROOT / "dashboard" / "dashboard_data.json"
JOSEF_CONFIG = PROJECT_ROOT / "data" / "josef_config.json"
EXPORT_SCRIPT = PROJECT_ROOT / "export_dashboard.py"
SCREENSHOT_SCRIPT = PROJECT_ROOT / "take_screenshot_de.py"

# Cache threshold: don't re-export if dashboard_data.json is younger than this
EXPORT_CACHE_SECONDS = 60

# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

# Keywords mapped to intents (all lowercase, German)
INTENT_KEYWORDS = {
    "STATUS_REQUEST": [
        "portfolio", "aktien", "depot", "stand", "aktuell",
        "wie läuft", "performance", "übersicht", "status",
        "wie steht", "wie sieht", "wert", "rendite",
    ],
    "EXPLAIN_BACKTEST": [
        "backtest", "backtesting", "modell", "wie funktioniert",
        "erklär", "was bedeutet", "was ist", "erklärung",
        "sharpe", "drawdown", "trefferquote",
    ],
    "THANKS": [
        "alles okay", "danke", "danke schön", "dankeschön",
        "ok", "super", "toll", "prima", "klasse", "perfekt",
        "vielen dank", "passt", "alles klar", "top",
    ],
}


def detect_intent(message: str) -> str:
    """Detect the intent of a German message using keyword matching.

    Parameters
    ----------
    message : str
        The inbound WhatsApp message text.

    Returns
    -------
    str
        One of: STATUS_REQUEST, EXPLAIN_BACKTEST, THANKS, FALLBACK
    """
    if not message:
        return "FALLBACK"
    msg_lower = message.lower().strip()

    # Score each intent by counting keyword matches
    scores = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in msg_lower)
        if score > 0:
            scores[intent] = score

    if not scores:
        return "FALLBACK"

    # Return the intent with the highest score
    return max(scores, key=scores.get)


# ---------------------------------------------------------------------------
# German signal translation
# ---------------------------------------------------------------------------

SIGNAL_DE = {
    "BUY": "KAUFEN", "SELL": "VERKAUFEN", "HOLD": "HALTEN",
    "HIGH": "HOCH", "MEDIUM": "MITTEL", "MED": "MITTEL", "LOW": "NIEDRIG",
}

SIGNAL_EMOJI = {
    "BUY": "📈", "SELL": "📉", "HOLD": "✅",
    "KAUFEN": "📈", "VERKAUFEN": "📉", "HALTEN": "✅",
}


def translate_signal(s: str) -> str:
    """Translate an English signal to German."""
    if not s:
        return s
    return SIGNAL_DE.get(s.upper(), s)


# ---------------------------------------------------------------------------
# Josef config
# ---------------------------------------------------------------------------

def load_josef_number() -> Optional[str]:
    """Load Josef's WhatsApp number from config file."""
    if not JOSEF_CONFIG.exists():
        print(f"[josef] Config not found: {JOSEF_CONFIG}", file=sys.stderr)
        return None
    with open(JOSEF_CONFIG, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config.get("whatsapp_number")


# ---------------------------------------------------------------------------
# Dashboard data helpers
# ---------------------------------------------------------------------------

def ensure_fresh_dashboard() -> bool:
    """Run export_dashboard.py if dashboard_data.json is stale (>60s old).

    Returns True if the data file exists after the attempt.
    """
    if DASHBOARD_JSON.exists():
        age = time.time() - DASHBOARD_JSON.stat().st_mtime
        if age < EXPORT_CACHE_SECONDS:
            print(f"[josef] Dashboard data is fresh ({age:.0f}s old), skipping export.")
            return True

    if not EXPORT_SCRIPT.exists():
        print(f"[josef] Export script not found: {EXPORT_SCRIPT}", file=sys.stderr)
        return DASHBOARD_JSON.exists()

    print("[josef] Exporting fresh dashboard data...")
    try:
        result = subprocess.run(
            [sys.executable, str(EXPORT_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            print(f"[josef] Export failed: {result.stderr}", file=sys.stderr)
    except Exception as exc:
        print(f"[josef] Export error: {exc}", file=sys.stderr)

    return DASHBOARD_JSON.exists()


def take_german_screenshot() -> Optional[str]:
    """Take a German dashboard screenshot. Returns path to the image or None."""
    if not SCREENSHOT_SCRIPT.exists():
        print(f"[josef] Screenshot script not found: {SCREENSHOT_SCRIPT}", file=sys.stderr)
        return None

    today = date.today().isoformat()
    out_dir = f"reports/{today}/de"

    try:
        result = subprocess.run(
            [
                sys.executable, str(SCREENSHOT_SCRIPT),
                "--json", str(DASHBOARD_JSON),
                "--out", out_dir,
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"[josef] Screenshot failed: {result.stderr}", file=sys.stderr)
            return None

        # Extract the screenshot path from output
        screenshot_path = PROJECT_ROOT / out_dir / "00_full_dashboard_de.png"
        if screenshot_path.exists():
            return str(screenshot_path)

        print(f"[josef] Screenshot file not found at expected path: {screenshot_path}",
              file=sys.stderr)
        return None

    except Exception as exc:
        print(f"[josef] Screenshot error: {exc}", file=sys.stderr)
        return None


def compose_german_summary(data: dict) -> str:
    """Compose a German text summary of the portfolio status.

    Parameters
    ----------
    data : dict
        Parsed dashboard_data.json content.

    Returns
    -------
    str
        WhatsApp-formatted German summary.
    """
    today = date.today().strftime("%d.%m.%Y")

    portfolio = data.get("portfolio", [])
    price_history = data.get("price_history", {})
    suggestions = data.get("suggestions", [])
    backtest_results = data.get("backtest_results", [])
    executed_trades = data.get("executed_trades", [])

    # Compute total portfolio value
    total_value = 0.0
    for pos in portfolio:
        ticker = pos.get("ticker", "")
        shares = pos.get("shares") or 0
        hist = price_history.get(ticker, [])
        if hist:
            last_price = hist[-1].get("close", 0)
            total_value += last_price * shares

    # Compute total return
    invested = 0.0
    total_fees = 0.0
    for t in executed_trades:
        action = (t.get("action") or "").upper()
        total_eur = t.get("total_eur") or 0.0
        fee = t.get("fee_eur") or 0.0
        if action == "BUY":
            invested += total_eur
        elif action == "SELL":
            invested -= total_eur
        total_fees += fee

    total_cost = invested + total_fees
    return_pct = ((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0.0

    # Latest signals per ticker
    latest_signals = {}
    for s in suggestions:
        latest_signals[s["ticker"]] = s

    signal_lines = []
    for pos in portfolio:
        ticker = pos.get("ticker", "")
        sig = latest_signals.get(ticker, {})
        llm_rec = sig.get("llm_recommendation") or sig.get("quant_signal") or "—"
        rec_de = translate_signal(llm_rec)
        emoji = SIGNAL_EMOJI.get(llm_rec.upper(), "➡️") if llm_rec != "—" else "❓"
        signal_lines.append(f"• {ticker} — {rec_de} {emoji}")

    signals_text = "\n".join(signal_lines) if signal_lines else "• Keine Signale verfügbar"

    # Backtest averages
    sharpes = [b.get("sharpe_ratio") for b in backtest_results if b.get("sharpe_ratio") is not None]
    avg_sharpe = sum(sharpes) / len(sharpes) if sharpes else 0.0
    drawdowns = [b.get("max_drawdown_pct") for b in backtest_results if b.get("max_drawdown_pct") is not None]
    worst_dd = min(drawdowns) if drawdowns else 0.0

    summary = (
        f"📊 *Dein Portfolio — {today}*\n"
        f"\n"
        f"*Gesamtwert:* {total_value:,.2f} €\n"
        f"*Gesamtrendite:* {return_pct:+.1f}%\n"
        f"\n"
        f"*Heutige Empfehlungen:*\n"
        f"{signals_text}\n"
        f"\n"
        f"*Backtesting-Durchschnitt:*\n"
        f"• Sharpe-Ratio: {avg_sharpe:.2f}\n"
        f"• Max. Rückgang: {worst_dd:.1f}%\n"
        f"\n"
        f"_Das Modell analysiert täglich 7 ETFs und Rohstoffe._"
    )

    return summary


# ---------------------------------------------------------------------------
# Response texts
# ---------------------------------------------------------------------------

EXPLAIN_BACKTEST_TEXT = (
    "📈 *Was ist Backtesting?*\n"
    "\n"
    "Backtesting bedeutet, dass wir unser Modell auf *historischen Kursdaten* "
    "testen — also schauen, wie es in der Vergangenheit abgeschnitten hätte.\n"
    "\n"
    "*Die wichtigsten Kennzahlen:*\n"
    "\n"
    "🔹 *Sharpe-Ratio:* Misst Rendite im Verhältnis zum Risiko. "
    "Über 1.0 ist gut, über 2.0 ist sehr gut.\n"
    "\n"
    "🔹 *Maximaler Rückgang (Max Drawdown):* Der größte Verlust vom "
    "Höchststand bis zum Tiefpunkt. Zum Beispiel -10% bedeutet, der Wert "
    "ist zwischenzeitlich um 10% gefallen.\n"
    "\n"
    "🔹 *Trefferquote (Win Rate):* Wie oft lag das Modell richtig? "
    "Bei 60% war die Vorhersage in 6 von 10 Fällen korrekt.\n"
    "\n"
    "Das Modell nutzt statistische Methoden (ARIMA für Kursvorhersagen, "
    "GARCH für Volatilität) und Künstliche Intelligenz, um "
    "Kauf/Verkauf/Halten-Empfehlungen zu geben.\n"
    "\n"
    "_Fragen? Einfach schreiben!_ 😊"
)

THANKS_RESPONSES = [
    "Gerne! Wenn du noch Fragen hast, melde dich einfach. 😊",
    "Immer gerne! Bei Fragen einfach schreiben. 👍",
    "Kein Problem! Ich bin hier, wenn du was brauchst. 😊",
]

FALLBACK_TEXT = (
    "Hallo! Ich kann dir bei Folgendem helfen:\n"
    "\n"
    "📊 *Portfolio-Status* — Schreib z.B. \"Wie läuft mein Depot?\" "
    "und du bekommst eine aktuelle Übersicht mit Grafik.\n"
    "\n"
    "📈 *Backtesting erklärt* — Frag z.B. \"Was bedeutet Backtesting?\" "
    "für eine verständliche Erklärung der Kennzahlen.\n"
    "\n"
    "_Einfach schreiben, ich helfe gerne!_ 😊"
)


# ---------------------------------------------------------------------------
# WhatsApp send helper
# ---------------------------------------------------------------------------

def send_whatsapp(to: str, message: str, media: Optional[str] = None) -> bool:
    """Send a WhatsApp message (and optional image) via openclaw CLI.

    Parameters
    ----------
    to : str
        Recipient phone number.
    message : str
        Message text.
    media : str, optional
        Path to an image file to send as document.

    Returns
    -------
    bool
        True on success.
    """
    try:
        if media and Path(media).exists():
            # Send image first as document (to avoid compression)
            cmd_media = [
                "openclaw", "message", "send",
                "--channel", "whatsapp",
                "--to", to,
                "--media", media,
                "--message", "📊 Portfolio-Dashboard",
                "--as-document",
            ]
            r = subprocess.run(cmd_media, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                print(f"[josef] Image send failed: {r.stderr}", file=sys.stderr)
            else:
                print(f"[josef] Image sent to {to}")

        # Send text message
        cmd_text = [
            "openclaw", "message", "send",
            "--channel", "whatsapp",
            "--to", to,
            "--message", message,
        ]
        r = subprocess.run(cmd_text, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"[josef] Text send failed: {r.stderr}", file=sys.stderr)
            return False
        print(f"[josef] Text sent to {to}")
        return True

    except Exception as exc:
        print(f"[josef] Send error: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle_josef_message(message: str, sender_number: str) -> None:
    """Process an inbound WhatsApp message from Josef.

    Detects intent and responds in German via WhatsApp.

    Parameters
    ----------
    message : str
        The inbound message text.
    sender_number : str
        The sender's phone number.
    """
    # Verify sender is Josef
    josef_number = load_josef_number()
    if not josef_number:
        print("[josef] Could not load Josef's number from config.", file=sys.stderr)
        return

    if sender_number != josef_number:
        print(f"[josef] Ignoring message from unknown sender: {sender_number}")
        return

    # Detect intent
    intent = detect_intent(message)
    print(f"[josef] Intent: {intent} (message: {message!r})")

    if intent == "STATUS_REQUEST":
        _handle_status_request(josef_number)

    elif intent == "EXPLAIN_BACKTEST":
        send_whatsapp(josef_number, EXPLAIN_BACKTEST_TEXT)

    elif intent == "THANKS":
        import random
        response = random.choice(THANKS_RESPONSES)
        send_whatsapp(josef_number, response)

    else:  # FALLBACK
        send_whatsapp(josef_number, FALLBACK_TEXT)


def _handle_status_request(recipient: str) -> None:
    """Handle a STATUS_REQUEST intent: export, screenshot, summarize, send.

    Parameters
    ----------
    recipient : str
        Josef's WhatsApp number.
    """
    # 1. Ensure dashboard data is fresh
    data_ok = ensure_fresh_dashboard()

    # 2. Take German screenshot (best effort)
    screenshot_path = None
    if data_ok:
        screenshot_path = take_german_screenshot()

    # 3. Compose German text summary
    summary = ""
    if DASHBOARD_JSON.exists():
        try:
            with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            summary = compose_german_summary(data)
        except Exception as exc:
            print(f"[josef] Summary composition error: {exc}", file=sys.stderr)
            summary = (
                "📊 *Portfolio-Status*\n\n"
                "Leider konnte die Zusammenfassung nicht erstellt werden. "
                "Bitte versuche es später noch einmal.\n\n"
                "_Bei Problemen: Simon kontaktieren._"
            )
    else:
        summary = (
            "📊 *Portfolio-Status*\n\n"
            "Die Portfolio-Daten sind gerade nicht verfügbar. "
            "Bitte versuche es später noch einmal.\n\n"
            "_Bei Problemen: Simon kontaktieren._"
        )

    # 4. Send via WhatsApp (image + text)
    send_whatsapp(recipient, summary, media=screenshot_path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Josef WhatsApp handler — processes German messages"
    )
    parser.add_argument(
        "--message",
        required=True,
        help="The inbound WhatsApp message text",
    )
    parser.add_argument(
        "--sender",
        required=True,
        help="The sender's phone number (e.g. +49XXXXXXXXXX)",
    )
    args = parser.parse_args()

    handle_josef_message(args.message, args.sender)


if __name__ == "__main__":
    main()
