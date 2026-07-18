"""
Shared constants used across the BlinkitHW project.
"""

from pathlib import Path

# ── Project Paths ─────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"

# Status file written by the watcher to communicate current state
STATUS_FILE = DATA_DIR / "product_status.json"

# Log file for the product watcher
LOG_FILE = LOGS_DIR / "product_watcher.log"

# ── ANSI Color Codes ─────────────────────────────────────────────────────────
PRODUCT_COLOR = "\033[95m"  # Magenta
RESET_COLOR = "\033[0m"

# ── Banner ────────────────────────────────────────────────────────────────────
BANNER = r"""buy"""
