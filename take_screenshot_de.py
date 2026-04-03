#!/usr/bin/env python3
"""
take_screenshot_de.py
---------------------
Renders the German dashboard as a PNG using render_dashboard.py (matplotlib).
No browser/Puppeteer required.

Usage:
    python3 take_screenshot_de.py [--json dashboard/dashboard_data.json] [--out reports/2026-04-03/de]

Returns (prints to stdout):
    Absolute path to 00_full_dashboard_de.png
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Render German dashboard PNG")
    parser.add_argument("--json", default="dashboard/dashboard_data.json")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    out_dir = args.out or f"reports/{date.today().isoformat()}/de"
    json_path = args.json

    # Resolve relative paths against project root
    if not Path(json_path).is_absolute():
        json_path = str(PROJECT_ROOT / json_path)

    if not Path(json_path).exists():
        print(f"[screenshot_de] ERROR: JSON file not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    render_script = PROJECT_ROOT / "render_dashboard.py"
    if not render_script.exists():
        print(f"[screenshot_de] ERROR: render_dashboard.py not found", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        [sys.executable, str(render_script), "--json", json_path, "--out", out_dir],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )

    if result.returncode != 0:
        print(f"[screenshot_de] ERROR:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Last line of stdout is the path
    lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
    screenshot_path = lines[-1] if lines else ""

    if not screenshot_path or not Path(screenshot_path).exists():
        print(f"[screenshot_de] ERROR: Expected screenshot not found at: {screenshot_path}", file=sys.stderr)
        sys.exit(1)

    print(result.stdout, end="")
    print(f"\n[screenshot_de] SUCCESS: {screenshot_path}")


if __name__ == "__main__":
    main()
