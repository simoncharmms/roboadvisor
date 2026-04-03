#!/usr/bin/env python3
"""
take_screenshot_de.py
---------------------
Starts a local HTTP server on port 7824 serving the dashboard/ directory,
runs the Puppeteer-based German screenshot script, kills the server,
and returns the path to the generated screenshot.

Usage:
    python3 take_screenshot_de.py [--json dashboard/dashboard_data.json] [--out reports/2026-04-03/de]

Returns (prints to stdout):
    Absolute path to 00_full_dashboard_de.png
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 7824


def find_free_port(preferred: int) -> int:
    """Check if the preferred port is available; return it or raise."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            raise RuntimeError(
                f"Port {preferred} is already in use. "
                f"Kill the process using it or choose a different port."
            )


def start_http_server(port: int, directory: Path) -> subprocess.Popen:
    """Start a background HTTP server serving the given directory."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--directory", str(directory)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(PROJECT_ROOT),
    )
    # Give the server a moment to start
    time.sleep(1.0)
    if proc.poll() is not None:
        raise RuntimeError(f"HTTP server failed to start (exit code {proc.returncode})")
    return proc


def kill_server(proc: subprocess.Popen) -> None:
    """Terminate the HTTP server process."""
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def run_screenshot(json_path: str, out_dir: str, port: int) -> str:
    """Run the Puppeteer screenshot script and return the output image path."""
    script = PROJECT_ROOT / "screenshot_dashboard_de.js"
    if not script.exists():
        raise FileNotFoundError(f"Screenshot script not found: {script}")

    url = f"http://localhost:{port}"
    cmd = [
        "node", str(script),
        "--json", json_path,
        "--out", out_dir,
        "--url", url,
    ]

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        print(f"[screenshot_de] STDERR: {result.stderr}", file=sys.stderr)
        raise RuntimeError(f"Screenshot script failed with exit code {result.returncode}")

    if result.stdout:
        print(result.stdout, end="")

    # Return path to the generated screenshot
    screenshot_path = Path(out_dir) / "00_full_dashboard_de.png"
    if not screenshot_path.is_absolute():
        screenshot_path = PROJECT_ROOT / screenshot_path

    if not screenshot_path.exists():
        raise FileNotFoundError(f"Expected screenshot not found: {screenshot_path}")

    return str(screenshot_path)


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Take a German dashboard screenshot via Puppeteer"
    )
    parser.add_argument(
        "--json",
        default="dashboard/dashboard_data.json",
        help="Path to dashboard_data.json",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory (default: reports/YYYY-MM-DD/de)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"HTTP server port (default: {DEFAULT_PORT})",
    )
    args = parser.parse_args()

    out_dir = args.out or f"reports/{date.today().isoformat()}/de"
    json_path = args.json

    # Resolve relative paths against project root
    if not Path(json_path).is_absolute():
        json_path = str(PROJECT_ROOT / json_path)

    if not Path(json_path).exists():
        print(f"[screenshot_de] ERROR: JSON file not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    dashboard_dir = PROJECT_ROOT / "dashboard"
    if not dashboard_dir.exists():
        print(f"[screenshot_de] ERROR: Dashboard directory not found: {dashboard_dir}", file=sys.stderr)
        sys.exit(1)

    # Ensure port is available
    port = find_free_port(args.port)

    server_proc = None
    try:
        # Start HTTP server
        print(f"[screenshot_de] Starting HTTP server on port {port}...")
        server_proc = start_http_server(port, dashboard_dir)

        # Run screenshot
        print(f"[screenshot_de] Taking screenshot...")
        screenshot_path = run_screenshot(json_path, out_dir, port)

        # Print the path for callers to capture
        print(f"\n[screenshot_de] SUCCESS: {screenshot_path}")

    except Exception as exc:
        print(f"[screenshot_de] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    finally:
        # Always clean up the server
        if server_proc:
            print(f"[screenshot_de] Stopping HTTP server...")
            kill_server(server_proc)


if __name__ == "__main__":
    main()
