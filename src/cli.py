"""
CLI entrypoint for BlinkitHW Product Watcher.

Handles argument parsing, interactive configuration, and the main watch loop.
Extracted from auto_watcher.py.
"""

import argparse
import asyncio
import logging
import os
import sys

from src.config import load_env, load_watcher_config
from src.constants import DATA_DIR, LOGS_DIR
from src.utils.logging import setup_logging
from src.utils.terminal import choose_config_option
from src.watcher.product_watcher import ProductWatcher

logger = logging.getLogger(__name__)


def parse_cli_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Blinkit product watcher")
    parser.add_argument(
        "--headless", action="store_true", help="Run the browser in headless mode"
    )
    return parser.parse_args(argv)


async def main():
    """Main entry point."""
    # Ensure runtime directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Load environment & configure logging
    load_env()
    logger_root = setup_logging()

    args = parse_cli_args()
    headless_mode = args.headless

    print("\n" + "=" * 70)
    print("BLINKIT PRODUCT WATCHER")
    print("=" * 70)
    print("\nThis script will:")
    print("1. Monitor a product URL for availability in your SPECIFIC LOCATION")
    print("2. Wait if it's 'Coming Soon'")
    print("3. Auto-purchase when available")
    print("\nExample URL: https://blinkit.com/prn/x/prid/746548")
    print("-" * 70)

    config = load_watcher_config()
    products = config.get("products", [])
    addresses = config.get("addresses", [])
    auth_sessions = config.get("auth", [])
    defaults = config.get("defaults", {})

    # ── Product selection ─────────────────────────────────────────────────────
    if products:
        selected_product = choose_config_option(products, "Saved products:")
        product_url = selected_product.get("url", "").strip()
        print(f"Selected product: {selected_product.get('name', product_url)}")
    else:
        product_url = input("\nEnter product URL: ").strip()

    if not product_url.startswith("http"):
        logger.error("Invalid URL. Must start with http")
        return
    if "blinkit.com" not in product_url:
        logger.error("Invalid URL. Must be a Blinkit product URL")
        return

    # ── Address selection ─────────────────────────────────────────────────────
    print(
        "\nUsing site UI to select a saved address via the site UI. "
        "No latitude/longitude input required."
    )
    if addresses:
        selected_address = choose_config_option(
            addresses, "Saved addresses:", default_label_key="label"
        )
        location_label = selected_address.get("label", "Home").strip() or "Home"
        print(f"Selected address: {location_label}")
    else:
        location_label = (
            input("\nEnter saved address label to select (default 'Home'): ").strip()
            or "Home"
        )

    # ── Auth session selection ────────────────────────────────────────────────
    phone_number = None
    account_name = None
    if auth_sessions:
        selected_auth = choose_config_option(
            auth_sessions, "Saved authentication accounts:", default_label_key="name"
        )
        phone_number = selected_auth.get("phone", "").strip()
        account_name = selected_auth.get("name", "").strip()
        print(f"Selected account: {account_name}")
    else:
        phone_input = input(
            "\nEnter phone number for login (or press Enter to skip): "
        ).strip()
        if phone_input:
            phone_number = phone_input

    # ── Check interval ────────────────────────────────────────────────────────
    check_interval = defaults.get("check_interval", 5)
    try:
        check_interval = int(
            input(f"\nEnter check interval in seconds (default {check_interval}): ").strip()
            or str(check_interval)
        )
    except ValueError:
        check_interval = defaults.get("check_interval", 5)

    # ── Quantity ──────────────────────────────────────────────────────────────
    quantity = defaults.get("quantity", 1)
    try:
        quantity = int(
            input(f"\nEnter quantity (default {quantity}): ").strip() or str(quantity)
        )
    except ValueError:
        quantity = defaults.get("quantity", 1)

    # ── Continue on out-of-stock ──────────────────────────────────────────────
    continue_on_oos_default = defaults.get("continue_on_oos", True)
    continue_in = input(
        f"\nContinue refreshing if product goes out of stock? "
        f"(y/N, default {'Y' if continue_on_oos_default else 'N'}): "
    ).strip().lower()
    if continue_in == "":
        continue_on_oos = continue_on_oos_default
    else:
        continue_on_oos = continue_in in ("y", "yes")

    # ── Automate checkout ─────────────────────────────────────────────────────
    automate_checkout_default = defaults.get("automate_checkout", False)
    automate_in = input(
        f"\nAutomate checkout steps (Proceed to Pay, Select Payment, Pay Now)? "
        f"(y/N, default {'Y' if automate_checkout_default else 'N'}): "
    ).strip().lower()
    if automate_in == "":
        automate_checkout = automate_checkout_default
    else:
        automate_checkout = automate_in in ("y", "yes")

    # ── Payment method ────────────────────────────────────────────────────────
    preferred_payment = defaults.get("preferred_payment", "cash").lower()
    if automate_checkout:
        pay_choice = input(
            f"\nPreferred payment method? (cash/upi/mobikwik, default '{preferred_payment}'): "
        ).strip().lower()
        if pay_choice in ("upi", "cash", "mobi", "mobikwik"):
            preferred_payment = pay_choice
        logger.info(f"Payment method: {preferred_payment.upper()}")

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_channel_id = os.getenv("TELEGRAM_CHANNEL_ID")

    logger.info(f"Product URL: {product_url}")
    logger.info(f"Location: using site-saved address ('{location_label}') via UI")
    if account_name:
        logger.info(f"Account: {account_name}")
    if phone_number:
        logger.info(f"Phone: {phone_number}")
    logger.info(f"Check interval: {check_interval} seconds")
    logger.info(f"Browser mode: {'headless' if headless_mode else 'visible'}")
    logger.info(
        f"Continue on out-of-stock: "
        f"{'YES - will keep refreshing' if continue_on_oos else 'NO - will stop'}"
    )
    logger.info(
        f"Automate checkout: "
        f"{'YES - will auto proceed through checkout' if automate_checkout else 'NO - will stop after adding to cart'}"
    )

    use_telegram_callbacks = False
    if telegram_bot_token and telegram_channel_id:
        use_telegram_callbacks = (
            input("\nEnable Telegram Retry/Cancel buttons? (y/N): ").strip().lower()
            in ("y", "yes")
        )
        logger.info(
            f"Telegram callbacks: "
            f"{'ENABLED' if use_telegram_callbacks else 'DISABLED (notify only)'}"
        )
        logger.info(f"Telegram notifications: ENABLED (Channel: {telegram_channel_id})")
    else:
        logger.info(
            "Telegram notifications: DISABLED "
            "(set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID in .env)"
        )

    # ── Watch loop with retry support ─────────────────────────────────────────
    retry_count = 0
    while True:
        logger.info(f"\n{'=' * 70}")
        if retry_count > 0:
            logger.info(f"RETRY #{retry_count} - Starting new watch cycle with same parameters")
        logger.info(f"{'=' * 70}\n")

        watcher = ProductWatcher(
            product_url,
            None,
            None,
            check_interval,
            location_label,
            continue_on_oos,
            telegram_bot_token=telegram_bot_token,
            telegram_channel_id=telegram_channel_id,
            automate_checkout=automate_checkout,
            preferred_payment=preferred_payment,
            quantity=quantity,
            use_telegram_callbacks=use_telegram_callbacks,
            phone_number=phone_number,
            account_name=account_name,
            headless=headless_mode,
        )
        success = await watcher.watch(max_checks=None)

        if success:
            logger.info("\n[SUCCESS] Product purchased successfully!")
            break
        else:
            logger.info("\n[INFO] Watcher stopped.")

        # Check if Telegram Retry button was clicked
        if watcher.telegram_retry_event.is_set():
            logger.info("[TELEGRAM] Retry button clicked - automatically restarting watch cycle")
            retry_count += 1
            logger.info(f"Restarting watch cycle (Retry #{retry_count})...")
            watcher.telegram_retry_event.clear()
            await asyncio.sleep(2)
            continue

        # Check if Telegram Cancel button was clicked
        if watcher.telegram_cancel_event.is_set():
            logger.info("[TELEGRAM] Cancel button clicked - exiting")
            watcher.telegram_cancel_event.clear()
            break

        # Otherwise, auto-retry
        retry_count += 1
        logger.info(f"Restarting watch cycle (Retry #{retry_count})...")
        await asyncio.sleep(2)
        continue


async def _run_quick_test():
    """Minimal demonstration of cart cleanup logic without opening a browser."""

    class DummyPage:
        def __init__(self):
            self.url = ""

        async def evaluate(self, script):
            return [
                {"id": "wrong123", "name": "Some Other Product"},
                {"id": "good456", "name": "Expected Product"},
            ]

        async def is_visible(self, selector):
            return False

        async def click(self, selector):
            pass

    class DummyOrder:
        def __init__(self):
            self.page = DummyPage()

        async def remove_from_cart(self, product_id, quantity=1):
            print(f"[dummy] remove_from_cart called for {product_id} x{quantity}")

    watcher = ProductWatcher(
        product_url="https://blinkit.com/prn/x/prid/TEST",
        latitude=None,
        longitude=None,
        check_interval=30,
        location_label="Home",
        continue_on_out_of_stock=False,
        telegram_bot_token=None,
        telegram_channel_id=None,
        automate_checkout=False,
    )
    watcher.order = DummyOrder()
    watcher.expected_product_name = "Expected Product"
    removed = await watcher.purge_wrong_cart_items(watcher.expected_product_name)
    print("Removed items:", removed)


def run():
    """Entry function called from main.py."""
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("test", "quick", "auto"):
        try:
            asyncio.run(_run_quick_test())
        except Exception as e:
            logger.error(f"Quick test failed: {e}", exc_info=True)
            sys.exit(1)
    else:
        try:
            asyncio.run(main())
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            sys.exit(1)
