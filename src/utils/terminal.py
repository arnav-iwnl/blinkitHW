"""
Terminal UI utilities for interactive menus.

Provides arrow-key navigable menus for the CLI entrypoint.
Extracted from auto_watcher.py.
"""

import os
import sys


def _get_single_key() -> str:
    """Read a single keypress (blocking) from stdin."""
    if os.name == "nt":
        import msvcrt
        key = msvcrt.getwch()
        return key
    else:
        import tty
        import termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            key = sys.stdin.read(1)
            return key
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def select_from_menu(options: list, title: str, default: int = 0) -> int:
    """Display an interactive arrow-key menu. Returns the selected index."""
    labels = [
        item.get("name") or item.get("label") or item.get("url") or ""
        for item in options
    ]
    index = default if 0 <= default < len(options) else 0
    print(f"\n{title}")
    print("Use ↑/↓ arrows and Enter to select. Press q to cancel.")
    for i, label in enumerate(labels):
        prefix = "→" if i == index else " "
        print(f" {prefix} {i + 1}. {label}")

    while True:
        key = _get_single_key()
        if key in ("\r", "\n"):
            print()
            return index

        if os.name == "nt":
            if key in ("\x00", "\xe0"):
                arrow = _get_single_key()
                if arrow == "H":
                    index = (index - 1) % len(options)
                elif arrow == "P":
                    index = (index + 1) % len(options)
            elif key.lower() == "q":
                raise KeyboardInterrupt
        else:
            if key == "\x1b":
                second = sys.stdin.read(1)
                third = sys.stdin.read(1)
                if second == "[":
                    if third == "A":
                        index = (index - 1) % len(options)
                    elif third == "B":
                        index = (index + 1) % len(options)
            elif key.lower() == "q":
                raise KeyboardInterrupt

        # Redraw only the option rows, leaving the title and help text intact
        lines_to_move_up = len(options)
        sys.stdout.write(f"\x1b[{lines_to_move_up}A")
        sys.stdout.flush()
        for i, label in enumerate(labels):
            prefix = "→" if i == index else " "
            sys.stdout.write(f" {prefix} {i + 1}. {label}\n")
        sys.stdout.flush()


def choose_config_option(
    options: list, title: str, default_label_key: str = "name"
) -> dict:
    """Present a menu of config options and return the user's selection."""
    if not options:
        raise ValueError("No configuration options available")
    try:
        choice_index = select_from_menu(options, title, default=0)
    except Exception:
        # Fallback to typed selection if interactive input is unavailable
        print(f"\n{title}")
        for index, item in enumerate(options, start=1):
            label = (
                item.get(default_label_key)
                or item.get("label")
                or item.get("url")
                or "Unknown"
            )
            extra = f" - {item.get('url')}" if item.get("url") else ""
            print(f"  {index}. {label}{extra}")
        labels = [
            item.get(default_label_key) or item.get("label") or item.get("url") or ""
            for item in options
        ]
        while True:
            choice = input(
                f"Select an option [1-{len(options)}] or name (default 1): "
            ).strip()
            if not choice:
                return options[0]
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    return options[idx]
            normalized = choice.lower()
            for idx, label in enumerate(labels):
                if normalized == label.lower() or normalized in label.lower():
                    return options[idx]
            print("Invalid selection. Enter the option number or type the name/label.")
    return options[choice_index]
