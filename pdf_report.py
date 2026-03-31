#!/opt/homebrew/bin/python3.12
"""
pdf_report.py — Generate a styled PDF from a daily Roboadvisor Markdown report.

Usage:
    python3 pdf_report.py [--report reports/2026-03-31.md] \
                          [--dashboard-json dashboard/dashboard_data.json] \
                          [--out reports/2026-03-31.pdf]
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import markdown
from weasyprint import HTML


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, 'Inter', sans-serif; max-width: 900px; margin: 40px auto; color: #1a1a2e; line-height: 1.6; }}
  h1 {{ color: #10b981; border-bottom: 2px solid #10b981; padding-bottom: 8px; }}
  h2 {{ color: #1a1a2e; border-left: 4px solid #10b981; padding-left: 12px; margin-top: 32px; }}
  h3 {{ color: #374151; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 13px; }}
  th {{ background: #f0fdf4; color: #065f46; padding: 8px 12px; text-align: left; border-bottom: 2px solid #10b981; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #e5e7eb; }}
  tr:nth-child(even) {{ background: #f9fafb; }}
  blockquote {{ border-left: 4px solid #f59e0b; margin: 16px 0; padding: 12px 16px; background: #fffbeb; color: #92400e; }}
  code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
  .signal-buy {{ color: #10b981; font-weight: 700; }}
  .signal-sell {{ color: #ef4444; font-weight: 700; }}
  .signal-hold {{ color: #f59e0b; font-weight: 700; }}
  .disclaimer {{ font-size: 11px; color: #9ca3af; border-top: 1px solid #e5e7eb; margin-top: 40px; padding-top: 16px; }}
  .portfolio-summary {{ background: #f0fdf4; border: 1px solid #a7f3d0; border-radius: 8px; padding: 16px; margin-bottom: 24px; }}
  .portfolio-summary h2 {{ border-left: none; padding-left: 0; margin-top: 0; color: #065f46; }}
  .conflict {{ color: #ef4444; font-weight: 700; }}
</style>
</head>
<body>
{portfolio_summary_block}
{content}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Portfolio summary block from dashboard JSON
# ---------------------------------------------------------------------------

def _signal_label(signal: str | None) -> str:
    if not signal:
        return "—"
    s = signal.upper()
    if s == "BUY":
        return "🟢 BUY"
    if s == "SELL":
        return "🔴 SELL"
    if s == "HOLD":
        return "🟡 HOLD"
    return signal


def build_portfolio_summary(dashboard_path: Path) -> str:
    """Read dashboard_data.json and return a styled HTML summary block."""
    try:
        data = json.loads(dashboard_path.read_text())
    except Exception as e:
        print(f"[warn] Could not read dashboard JSON: {e}", file=sys.stderr)
        return ""

    portfolio = data.get("portfolio", [])
    price_history = data.get("price_history", {})
    suggestions = data.get("suggestions", [])

    # Index latest suggestion per ticker (most recent date)
    latest_suggestion: dict[str, dict] = {}
    for s in suggestions:
        ticker = s.get("ticker", "")
        existing = latest_suggestion.get(ticker)
        if existing is None or s.get("date", "") >= existing.get("date", ""):
            latest_suggestion[ticker] = s

    # Build summary rows
    rows_html = []
    total_value = 0.0

    for holding in portfolio:
        ticker = holding.get("ticker", "")
        name = holding.get("name", "")
        shares = holding.get("shares", 0)
        currency = holding.get("currency", "EUR")

        # Latest price from price_history
        history = price_history.get(ticker, [])
        latest_price = None
        if history:
            latest_entry = max(history, key=lambda x: x.get("date", ""))
            latest_price = latest_entry.get("close")

        value = (shares * latest_price) if latest_price is not None else None
        if value is not None:
            total_value += value

        # Signals
        sug = latest_suggestion.get(ticker, {})
        quant_signal = sug.get("quant_signal")
        llm_rec = sug.get("llm_recommendation")
        conflict = (
            quant_signal and llm_rec
            and quant_signal.upper() != llm_rec.upper()
        )

        price_str = f"{latest_price:.4f} {currency}" if latest_price is not None else "—"
        value_str = f"{value:,.2f} {currency}" if value is not None else "—"
        conflict_flag = ' <span class="conflict">⚠ conflict</span>' if conflict else ""

        rows_html.append(
            f"<tr>"
            f"<td><strong>{ticker}</strong><br><small>{name}</small></td>"
            f"<td>{shares}</td>"
            f"<td>{price_str}</td>"
            f"<td><strong>{value_str}</strong></td>"
            f"<td>{_signal_label(quant_signal)}</td>"
            f"<td>{_signal_label(llm_rec)}{conflict_flag}</td>"
            f"</tr>"
        )

    if not rows_html:
        return ""

    total_str = f"{total_value:,.2f} EUR" if total_value else "—"
    generated_at = data.get("meta", {}).get("generated_at", "")
    date_note = f" &mdash; as of {generated_at}" if generated_at else ""

    summary_html = f"""
<div class="portfolio-summary">
  <h2>📊 Portfolio Snapshot{date_note}</h2>
  <p><strong>Total Estimated Value: {total_str}</strong></p>
  <table>
    <thead>
      <tr>
        <th>Ticker / Name</th>
        <th>Shares</th>
        <th>Latest Price</th>
        <th>Value</th>
        <th>Quant Signal</th>
        <th>LLM Recommendation</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
</div>
"""
    return summary_html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def resolve_report_path(report_arg: str | None) -> Path:
    if report_arg:
        p = Path(report_arg)
        if not p.exists():
            sys.exit(f"[error] Report not found: {p}")
        return p

    # Auto-detect today's report
    today = date.today().isoformat()
    candidates = [
        Path(f"reports/{today}.md"),
        Path(f"{today}.md"),
    ]
    for c in candidates:
        if c.exists():
            return c

    sys.exit(
        f"[error] No report path given and today's report not found "
        f"(tried: {', '.join(str(c) for c in candidates)})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a styled PDF from a Roboadvisor Markdown report."
    )
    parser.add_argument(
        "--report",
        metavar="PATH",
        help="Path to the Markdown report (default: reports/YYYY-MM-DD.md for today)",
    )
    parser.add_argument(
        "--dashboard-json",
        metavar="PATH",
        help="Path to dashboard_data.json — embeds portfolio summary at top of PDF",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="Output PDF path (default: same dir/stem as report, .pdf extension)",
    )
    args = parser.parse_args()

    # Resolve input report
    report_path = resolve_report_path(args.report)

    # Resolve output path
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = report_path.with_suffix(".pdf")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Build portfolio summary block
    portfolio_summary_block = ""
    if args.dashboard_json:
        dashboard_path = Path(args.dashboard_json)
        if not dashboard_path.exists():
            print(f"[warn] Dashboard JSON not found: {dashboard_path}", file=sys.stderr)
        else:
            portfolio_summary_block = build_portfolio_summary(dashboard_path)

    # Convert Markdown → HTML
    md_text = report_path.read_text(encoding="utf-8")
    content_html = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br"],
    )

    # Assemble full HTML
    full_html = HTML_TEMPLATE.format(
        portfolio_summary_block=portfolio_summary_block,
        content=content_html,
    )

    # Render PDF
    HTML(string=full_html, base_url=str(report_path.parent)).write_pdf(str(out_path))

    print(out_path)


if __name__ == "__main__":
    main()
