"""
Product-related utility functions.

Extracted from auto_watcher.py to avoid duplicating these helpers
across watcher entry-points.
"""

import re
import sys
import logging

from src.constants import PRODUCT_COLOR, RESET_COLOR

logger = logging.getLogger(__name__)


def colorize_product(name: str) -> str:
    """Wrap product name with ANSI magenta color codes."""
    return f"{PRODUCT_COLOR}{name}{RESET_COLOR}"


def normalize_product_name(name: str) -> str:
    """Reduce product title by stripping price/extra text.

    Many Blinkit pages append price or promotional text to the title (e.g.
    "Product Name Price - Buy Online at ₹167 in India").  This helper keeps
    only the base product name by cutting at common markers.
    """
    if not name:
        return name
    # split on 'Price' word or currency symbols or pipe characters
    parts = re.split(r"\bPrice\b|₹|Rs\.?|\|", name)
    return parts[0].strip()


def play_alert_sound() -> None:
    """Play an alert sound when product is available."""
    try:
        if sys.platform == "win32":
            # Windows: play a beep at 1000 Hz for 1 second
            # winsound.Beep(1000, 500)
            # Play it twice for emphasis
            import time
            time.sleep(0.2)
            # winsound.Beep(1000, 1000)
        else:
            # On other platforms, use system beep
            print("\a", end="", flush=True)
    except Exception as e:
        logger.debug(f"Failed to play alert sound: {e}")
