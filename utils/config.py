"""
utils/config.py
---------------
Configuration loader for the roboadvisor.

Loads environment variables from a .env file, validates required keys,
and exposes a typed Config object for the rest of the application.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:
    print("ERROR: python-dotenv is not installed. Run: pip install python-dotenv", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """Holds all application configuration values loaded from the environment."""

    finance_api_key: str
    news_api_key: str
    anthropic_api_key: Optional[str]

    # Optional tuning knobs with sensible defaults
    db_path: str = field(default="roboadvisor.db")
    log_level: str = field(default="INFO")


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = ["FINANCE_API_KEY", "NEWS_API_KEY"]
_OPTIONAL_KEYS = ["ANTHROPIC_API_KEY"]


def load_config(env_file: Optional[str] = None) -> Config:
    """Load and validate configuration from environment / .env file.

    Parameters
    ----------
    env_file : str, optional
        Path to the .env file to load.  Defaults to ``.env`` in the current
        working directory, then in the project root (parent of ``utils/``).

    Returns
    -------
    Config
        Populated and validated configuration object.

    Raises
    ------
    SystemExit
        If any required environment variable is missing.
    """
    # Determine .env location
    if env_file is None:
        candidates = [
            Path(".env"),
            Path(__file__).resolve().parent.parent / ".env",
        ]
        for candidate in candidates:
            if candidate.exists():
                env_file = str(candidate)
                break

    if env_file and Path(env_file).exists():
        load_dotenv(dotenv_path=env_file, override=False)
    else:
        # Still try to read from actual environment without a file
        load_dotenv(override=False)

    # Validate required keys
    missing = [k for k in _REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        print("=" * 60, file=sys.stderr)
        print("CONFIGURATION ERROR: Missing required environment variables:", file=sys.stderr)
        for key in missing:
            print(f"  ✗  {key}", file=sys.stderr)
        print("", file=sys.stderr)
        print("Please copy .env.example to .env and fill in the values:", file=sys.stderr)
        print("  cp .env.example .env && $EDITOR .env", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys.exit(1)

    # Warn about optional keys
    for key in _OPTIONAL_KEYS:
        if not os.getenv(key):
            print(f"[config] WARNING: Optional key {key!r} not set — LLM features will be disabled.")

    return Config(
        finance_api_key=os.environ["FINANCE_API_KEY"],
        news_api_key=os.environ["NEWS_API_KEY"],
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        db_path=os.getenv("DB_PATH", "roboadvisor.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


# Module-level singleton — lazily loaded so import alone does not validate.
_config: Optional[Config] = None


def get_config() -> Config:
    """Return the module-level Config singleton, loading it on first call.

    Returns
    -------
    Config
        The application configuration.
    """
    global _config
    if _config is None:
        _config = load_config()
    return _config
