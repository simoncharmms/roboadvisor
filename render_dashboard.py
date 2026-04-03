#!/usr/bin/env python3
"""
render_dashboard.py
-------------------
Renders the Roboadvisor portfolio dashboard as a PNG image directly
from dashboard_data.json using matplotlib. No browser required.

Usage:
    python3 render_dashboard.py [--json dashboard/dashboard_data.json] [--out reports/YYYY-MM-DD/de]
"""

from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import matplotlib.ticker as mticker
import numpy as np

# ── Config ─────────────────────────────────────────────────────
BG      = "#0f0f0f"
CARD    = "#1a1a1a"
BORDER  = "#2a2a2a"
TEXT    = "#e5e5e5"
MUTED   = "#888888"
GREEN   = "#10b981"
RED     = "#ef4444"
BLUE    = "#3b82f6"
YELLOW  = "#f59e0b"
PURPLE  = "#8b5cf6"
PALETTE = [GREEN, BLUE, YELLOW, RED, PURPLE, "#06b6d4", "#ec4899", "#84cc16"]

SIGNAL_DE = {"BUY": "KAUF", "SELL": "VERKAUF", "HOLD": "HALTEN",
             "HIGH": "HOCH", "MEDIUM": "MITTEL", "MED": "MITTEL", "LOW": "NIEDRIG"}

def sig_de(s: str) -> str:
    return SIGNAL_DE.get((s or "").upper(), s or "—")

def sig_color(s: str) -> str:
    u = (s or "").upper()
    if u == "BUY": return GREEN
    if u == "SELL": return RED
    return YELLOW

def fmt_eur(v, decimals=0):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:,.{decimals}f} €".replace(",", "X").replace(".", ",").replace("X", ".")

def fmt_pct(v, decimals=1):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{decimals}f}%"

def refresh_dashboard_data(json_path: str) -> None:
    """Run export_dashboard.py to ensure dashboard_data.json is up-to-date."""
    import subprocess, sys
    script = Path(__file__).parent / "export_dashboard.py"
    result = subprocess.run(
        [sys.executable, str(script), "--out", json_path],
        cwd=str(Path(__file__).parent),
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        print(f"[render] WARNING: export_dashboard failed:\n{result.stderr[:300]}", file=sys.stderr)
    else:
        print("[render] Dashboard data refreshed.")

def load_data(json_path: str) -> dict:
    with open(json_path, "r") as f:
        return json.load(f)

def last_price(price_history: dict, ticker: str) -> float | None:
    hist = price_history.get(ticker, [])
    if not hist:
        return None
    return hist[-1]["close"]

def compute_portfolio(data: dict) -> list[dict]:
    positions = []
    for pos in data.get("portfolio", []):
        ticker = pos["ticker"]
        shares = pos.get("shares", 0)
        price = last_price(data.get("price_history", {}), ticker)
        cost = pos.get("cost_basis_eur")
        value = price * shares if price else None
        pnl = (value - cost) if (value is not None and cost is not None) else None
        pnl_pct = (pnl / cost * 100) if (pnl is not None and cost) else None
        positions.append({
            "ticker": ticker,
            "name": pos.get("name", ticker),
            "shares": shares,
            "price": price,
            "value": value,
            "cost": cost,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
        })
    return positions

def get_recent_signals(data: dict, n: int = 10) -> list[dict]:
    sigs = sorted(data.get("suggestions", []), key=lambda x: x.get("date", ""), reverse=True)
    return sigs[:n]

def get_perf_series(data: dict, ticker: str, days: int = 90) -> tuple[list, list]:
    hist = data.get("price_history", {}).get(ticker, [])
    if not hist:
        return [], []
    hist = sorted(hist, key=lambda x: x["date"])[-days:]
    dates = [h["date"] for h in hist]
    closes = [h["close"] for h in hist]
    return dates, closes

# ── Rendering ──────────────────────────────────────────────────

def render(data: dict, out_path: str) -> None:
    positions = compute_portfolio(data)
    total_value = sum(p["value"] for p in positions if p["value"] is not None)
    total_cost  = sum(p["cost"]  for p in positions if p["cost"]  is not None)
    total_pnl   = (total_value - total_cost) if (total_value and total_cost) else None
    total_pnl_pct = (total_pnl / total_cost * 100) if (total_pnl is not None and total_cost) else None

    recent_signals = get_recent_signals(data, 7)
    generated_at = data.get("meta", {}).get("generated_at", date.today().isoformat())

    n_positions = len(positions)
    fig_height = 6 + n_positions * 0.55 + 4  # dynamic height

    fig = plt.figure(figsize=(16, fig_height), facecolor=BG)
    fig.patch.set_facecolor(BG)

    # Layout: header | KPIs | [table | pie] | signals | charts
    gs = gridspec.GridSpec(
        5, 2,
        figure=fig,
        height_ratios=[0.6, 0.8, max(n_positions * 0.55, 3), 2, 3],
        hspace=0.45,
        wspace=0.08,
        left=0.04, right=0.97, top=0.97, bottom=0.03,
    )

    # ── Header ────────────────────────────────────────────────
    ax_header = fig.add_subplot(gs[0, :])
    ax_header.set_facecolor(BG)
    ax_header.axis("off")
    ax_header.text(0.0, 0.7, "📈 Roboadvisor", fontsize=22, color=TEXT,
                   fontweight="bold", transform=ax_header.transAxes)
    ax_header.text(0.0, 0.1, f"Portfolio-Dashboard  •  Stand: {generated_at}",
                   fontsize=11, color=MUTED, transform=ax_header.transAxes)

    # ── KPI Cards ─────────────────────────────────────────────
    ax_kpi = fig.add_subplot(gs[1, :])
    ax_kpi.set_facecolor(BG)
    ax_kpi.axis("off")

    # Pull meta checkpoints for start date display
    meta = data.get("meta", {})
    start_val = meta.get("total_invested_eur") or total_cost or 0
    start_date = (meta.get("portfolio_checkpoints") or [{}])[0].get("date", "—")

    kpis = [
        ("Portfoliowert", fmt_eur(total_value), TEXT),
        ("Eingesetzt", fmt_eur(start_val), MUTED),
        ("Gewinn/Verlust", fmt_eur(total_pnl) if total_pnl is not None else "—",
         GREEN if (total_pnl or 0) >= 0 else RED),
        ("Rendite", fmt_pct(total_pnl_pct) if total_pnl_pct is not None else "—",
         GREEN if (total_pnl_pct or 0) >= 0 else RED),
        ("Seit", start_date, MUTED),
    ]
    for idx, (label, value, color) in enumerate(kpis):
        x = idx / len(kpis)
        w = 0.85 / len(kpis)
        rect = FancyBboxPatch((x + 0.01, 0.05), w, 0.88,
                              boxstyle="round,pad=0.02", linewidth=1,
                              edgecolor=BORDER, facecolor=CARD,
                              transform=ax_kpi.transAxes)
        ax_kpi.add_patch(rect)
        ax_kpi.text(x + 0.01 + w/2, 0.68, value, fontsize=14, color=color,
                    fontweight="bold", ha="center", va="center",
                    transform=ax_kpi.transAxes)
        ax_kpi.text(x + 0.01 + w/2, 0.22, label, fontsize=9, color=MUTED,
                    ha="center", va="center", transform=ax_kpi.transAxes)

    # ── Position Table ────────────────────────────────────────
    ax_table = fig.add_subplot(gs[2, 0])
    ax_table.set_facecolor(CARD)
    ax_table.axis("off")
    ax_table.text(0.03, 0.97, "Positionen", fontsize=12, color=TEXT,
                  fontweight="bold", va="top", transform=ax_table.transAxes)

    headers = ["Ticker", "Kurs", "Stück", "Wert", "Wert %", ""]
    col_x   = [0.02, 0.18, 0.33, 0.48, 0.65, 0.85]
    row_h   = 0.82 / max(n_positions + 1, 2)

    for ci, (h, x) in enumerate(zip(headers, col_x)):
        ax_table.text(x, 0.88, h, fontsize=8, color=MUTED, fontweight="bold",
                      transform=ax_table.transAxes)

    for ri, pos in enumerate(positions):
        y = 0.84 - (ri + 1) * row_h
        bg_color = "#1e1e1e" if ri % 2 == 0 else CARD
        rect = FancyBboxPatch((0.0, y - 0.01), 1.0, row_h,
                              boxstyle="square,pad=0",
                              edgecolor="none", facecolor=bg_color,
                              transform=ax_table.transAxes)
        ax_table.add_patch(rect)

        price_str = f"{pos['price']:.2f}" if pos['price'] else "—"
        shares_str = f"{pos['shares']:.3f}"
        value_str = fmt_eur(pos['value'])
        # Show portfolio weight % instead of individual P&L (cost basis is estimated)
        weight_pct = f"{pos['value'] / total_value * 100:.1f}%" if (pos['value'] and total_value) else "—"

        row_data = [pos['ticker'], price_str, shares_str, value_str, weight_pct, ""]
        row_colors = [TEXT, TEXT, MUTED, TEXT, BLUE, TEXT]

        for ci, (val, x, col) in enumerate(zip(row_data, col_x, row_colors)):
            ax_table.text(x, y + row_h * 0.3, val, fontsize=8, color=col,
                          transform=ax_table.transAxes)

    # ── Allocation Pie ────────────────────────────────────────
    ax_pie = fig.add_subplot(gs[2, 1])
    ax_pie.set_facecolor(CARD)

    valid_positions = [p for p in positions if p["value"] and p["value"] > 0]
    if valid_positions:
        values = [p["value"] for p in valid_positions]
        labels = [p["ticker"] for p in valid_positions]
        colors = PALETTE[:len(valid_positions)]
        wedges, texts, autotexts = ax_pie.pie(
            values, labels=labels, colors=colors,
            autopct="%1.1f%%", startangle=90,
            textprops={"color": TEXT, "fontsize": 8},
            pctdistance=0.75,
        )
        for at in autotexts:
            at.set_fontsize(7)
            at.set_color(BG)
        ax_pie.set_title("Allokation", color=TEXT, fontsize=12, pad=8, fontweight="bold")
    else:
        ax_pie.text(0.5, 0.5, "Keine Daten", ha="center", va="center",
                    color=MUTED, fontsize=11, transform=ax_pie.transAxes)
        ax_pie.set_title("Allokation", color=TEXT, fontsize=12, pad=8)
    ax_pie.set_facecolor(CARD)

    # ── Recent Signals ────────────────────────────────────────
    ax_signals = fig.add_subplot(gs[3, :])
    ax_signals.set_facecolor(CARD)
    ax_signals.axis("off")
    ax_signals.text(0.01, 0.93, "Letzte Empfehlungen", fontsize=12, color=TEXT,
                    fontweight="bold", va="top", transform=ax_signals.transAxes)

    if recent_signals:
        col_w = 1.0 / max(len(recent_signals), 1)
        for i, sig in enumerate(recent_signals):
            x = i * col_w + 0.01
            ticker = sig.get("ticker", "")
            signal = sig.get("signal", "")
            conf   = sig.get("llm_confidence", "")
            d      = sig.get("date", "")
            sc = sig_color(signal)
            ax_signals.text(x + col_w * 0.5, 0.72, ticker,
                            fontsize=10, color=TEXT, fontweight="bold",
                            ha="center", transform=ax_signals.transAxes)
            ax_signals.text(x + col_w * 0.5, 0.45, sig_de(signal),
                            fontsize=9, color=sc, fontweight="bold",
                            ha="center", transform=ax_signals.transAxes)
            ax_signals.text(x + col_w * 0.5, 0.20, f"{sig_de(conf)} • {d}",
                            fontsize=7, color=MUTED,
                            ha="center", transform=ax_signals.transAxes)
    else:
        ax_signals.text(0.5, 0.5, "Keine Signale verfügbar",
                        ha="center", va="center", color=MUTED, fontsize=11,
                        transform=ax_signals.transAxes)

    # ── Price Charts ──────────────────────────────────────────
    n_charts = min(len(positions), 7)
    ax_charts = fig.add_subplot(gs[4, :])
    ax_charts.set_facecolor(BG)
    ax_charts.axis("off")
    ax_charts.text(0.01, 0.97, "Kursverlauf (90 Tage)", fontsize=12, color=TEXT,
                   fontweight="bold", va="top", transform=ax_charts.transAxes)

    if n_charts > 0:
        inner = gridspec.GridSpecFromSubplotSpec(
            1, n_charts, subplot_spec=gs[4, :], wspace=0.3
        )
        for i, pos in enumerate(positions[:n_charts]):
            ax = fig.add_subplot(inner[0, i])
            ax.set_facecolor(CARD)
            dates, closes = get_perf_series(data, pos["ticker"], 90)
            if dates and closes:
                color = PALETTE[i % len(PALETTE)]
                ax.plot(range(len(closes)), closes, color=color, linewidth=1.5)
                ax.fill_between(range(len(closes)), closes,
                                min(closes), alpha=0.15, color=color)
                ax.set_xlim(0, len(closes) - 1)
                ax.tick_params(labelsize=6, colors=MUTED, length=2)
                ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f"))
                ax.set_xticks([])
            else:
                ax.text(0.5, 0.5, "—", ha="center", va="center", color=MUTED)

            ax.set_facecolor(CARD)
            for spine in ax.spines.values():
                spine.set_edgecolor(BORDER)
            ax.tick_params(colors=MUTED)
            ax.set_title(pos["ticker"], fontsize=8, color=TEXT, pad=4)

    plt.savefig(out_path, dpi=120, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    print(f"\n✅ Dashboard gespeichert: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Roboadvisor dashboard as PNG")
    parser.add_argument("--json", default="dashboard/dashboard_data.json")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    out_dir = args.out or f"reports/{date.today().isoformat()}/de"
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    json_path = args.json
    if not Path(json_path).is_absolute():
        json_path = str(Path(__file__).parent / json_path)

    # Always refresh before rendering
    refresh_dashboard_data(json_path)

    if not Path(json_path).exists():
        print(f"ERROR: JSON not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    data = load_data(json_path)
    out_path = str(Path(out_dir) / "00_full_dashboard_de.png")
    render(data, out_path)
    print(out_path)


if __name__ == "__main__":
    main()
