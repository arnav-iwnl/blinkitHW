#!/usr/bin/env python3
"""
BlinkitHW — Blinkit Product Watcher & Auto-Purchaser

Thin entrypoint that delegates to the CLI module.
"""

import sys
import os

# Ensure project root is on sys.path for clean imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.cli import run

if __name__ == "__main__":
    run()
