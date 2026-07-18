"""
Configuration loading for BlinkitHW.

Handles:
- Loading watcher_config.json
- Loading .env environment variables
- Providing project-level defaults
"""

import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from src.constants import PROJECT_ROOT

logger = logging.getLogger(__name__)


def load_env() -> None:
    """Load environment variables from the project's .env file."""
    env_path = PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=env_path)


def load_watcher_config(config_filename: str = "watcher_config.json") -> dict:
    """Load and return the watcher configuration from a JSON file.

    Returns an empty dict if the file doesn't exist or can't be parsed.
    """
    config_path = PROJECT_ROOT / config_filename
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            return json.load(config_file)
    except Exception as exc:
        logger.warning(f"Failed to load watcher config: {exc}")
        return {}
