"""
Custom logging configuration for BlinkitHW.

Provides:
- OrdinalDateFormatter: colorized console output with ordinal dates
- setup_logging(): configures root logger with file + stream handlers
"""

import logging
from datetime import datetime
from pathlib import Path

from src.constants import LOG_FILE, LOGS_DIR


class OrdinalDateFormatter(logging.Formatter):
    """Custom formatter with ordinal dates and colored output."""

    # ANSI color codes
    LEVEL_COLORS = {
        "DEBUG": {
            "time": "\033[36m",       # Cyan for time
            "level": "\033[36m",      # Cyan for level
            "message": "\033[36m",    # Cyan for message
        },
        "INFO": {
            "time": "\033[94m",       # Blue for time
            "level": "\033[92m",      # Green for level
            "message": "\033[92m",    # Green for message
        },
        "WARNING": {
            "time": "\033[94m",       # Blue for time
            "level": "\033[93m",      # Yellow for level
            "message": "\033[93m",    # Yellow for message
        },
        "ERROR": {
            "time": "\033[94m",       # Blue for time
            "level": "\033[91m",      # Red for level
            "message": "\033[91m",    # Red for message
        },
        "CRITICAL": {
            "time": "\033[94m",       # Blue for time
            "level": "\033[95m",      # Magenta for level
            "message": "\033[95m",    # Magenta for message
        },
    }

    RESET = "\033[0m"

    def format(self, record):
        # Convert timestamp to ordinal date format
        dt = datetime.fromtimestamp(record.created)
        day = dt.day
        month = dt.strftime("%b")
        year = dt.year
        time_str = dt.strftime("%I:%M:%S %p")

        # Add ordinal suffix to day
        if 10 <= day % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

        ordinal_date = f"{day}{suffix} {month} {year} {time_str}"

        # Get colors for this level
        colors = self.LEVEL_COLORS.get(record.levelname, self.LEVEL_COLORS["INFO"])

        # Apply colors to each component
        colored_time = f"{colors['time']}{ordinal_date}{self.RESET}"
        colored_level = f"{colors['level']}{record.levelname}{self.RESET}"
        colored_message = f"{colors['message']}{record.getMessage()}{self.RESET}"

        # Format: [colored_time] - colored_level - colored_message
        log_message = f"{colored_time} - {colored_level} - {colored_message}"

        return log_message


def setup_logging() -> logging.Logger:
    """Configure and return the application logger.

    Sets up both a colorized stream handler (console) and a plain-text
    file handler (``logs/product_watcher.log``).
    """
    # Ensure the logs directory exists
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(OrdinalDateFormatter())

    file_handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    file_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%d %b %Y %I:%M %p"
    )
    file_handler.setFormatter(file_formatter)

    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, stream_handler],
        force=True,
    )

    return logging.getLogger("blinkithw")
