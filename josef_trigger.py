#!/usr/bin/env python3
"""
josef_trigger.py
----------------
Thin wrapper that checks if an inbound WhatsApp message is from Josef,
and if so, delegates to josef_handler.py. Always exits 0 to avoid
disrupting the main agent's flow.

Usage:
    python3 josef_trigger.py --message "Wie läuft mein Depot?" --sender "+49XXXXXXXXXX"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
JOSEF_CONFIG = PROJECT_ROOT / "data" / "josef_config.json"


def is_josef(sender: str) -> bool:
    """Check if the sender matches Josef's configured number.

    Parameters
    ----------
    sender : str
        The sender's phone number.

    Returns
    -------
    bool
    """
    if not JOSEF_CONFIG.exists():
        return False
    try:
        with open(JOSEF_CONFIG, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("whatsapp_number") == sender
    except Exception:
        return False


def main() -> None:
    """Entry point. Always exits 0."""
    parser = argparse.ArgumentParser(
        description="Josef trigger — routes Josef's messages to his handler"
    )
    parser.add_argument("--message", required=True, help="Inbound message text")
    parser.add_argument("--sender", required=True, help="Sender phone number")
    args = parser.parse_args()

    if not is_josef(args.sender):
        # Not Josef — silently ignore
        sys.exit(0)

    # Delegate to josef_handler
    try:
        from josef_handler import handle_josef_message
        handle_josef_message(args.message, args.sender)
    except Exception as exc:
        print(f"[josef_trigger] Error: {exc}", file=sys.stderr)
        # Always exit 0 to avoid disrupting Homer's flow

    sys.exit(0)


if __name__ == "__main__":
    main()
