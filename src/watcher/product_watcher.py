"""
ProductWatcher — monitors a Blinkit product URL for availability and auto-purchases.

Extracted from the monolithic auto_watcher.py into a focused module.
"""

import asyncio
import json
import logging
import re
import sys
from datetime import datetime

from src.constants import STATUS_FILE, DATA_DIR, BANNER
from src.utils.product import colorize_product, normalize_product_name, play_alert_sound
from src.auth import BlinkitAuth
from src.order.blinkit_order import BlinkitOrder
from src.order.services.checkout import CheckoutService
from src.telegram.service import TelegramBot

# Platform-specific sound alert
if sys.platform == "win32":
    import winsound

logger = logging.getLogger(__name__)


class ProductWatcher:
    def __init__(
        self,
        product_url,
        latitude,
        longitude,
        check_interval=30,
        location_label="Home",
        continue_on_out_of_stock=True,
        telegram_bot_token=None,
        telegram_channel_id=None,
        automate_checkout=False,
        preferred_payment="cash",
        quantity=1,
        use_telegram_callbacks=False,
        phone_number=None,
        account_name=None,
        headless=False,
    ):
        """
        Initialize the product watcher.

        Args:
            product_url: Full Blinkit product URL
            latitude: Delivery location latitude
            longitude: Delivery location longitude
            check_interval: Time between checks in seconds
            location_label: Saved address label to select (default 'Home')
            continue_on_out_of_stock: Keep monitoring if product goes out of stock
            telegram_bot_token: Telegram bot token for notifications
            telegram_channel_id: Telegram channel ID for notifications
            automate_checkout: If True, automatically proceed with checkout steps
            preferred_payment: Payment method preference (cash/upi/mobi)
            quantity: Number of items to purchase
            use_telegram_callbacks: Enable Telegram Retry/Cancel buttons
            phone_number: Phone number for login (optional)
            account_name: Account name for session storage (optional)
            headless: Run browser in headless mode
        """
        self.product_url = product_url
        self.latitude = latitude
        self.longitude = longitude
        self.check_interval = check_interval
        self.query_count = 0
        self.auth = None
        self.order = None
        self.product_id = self._extract_product_id(product_url)
        self.expected_product_name = None
        self.location_label = location_label or "Home"
        self.phone_number = phone_number
        self.account_name = account_name
        self.continue_on_out_of_stock = continue_on_out_of_stock
        self.automate_checkout = automate_checkout
        self.preferred_payment = preferred_payment.lower()
        self.quantity = max(1, int(quantity))
        self.use_telegram_callbacks = use_telegram_callbacks
        self.headless = headless

        # Telegram bot configuration
        self.telegram_bot = None
        if telegram_bot_token and telegram_channel_id:
            self.telegram_bot = TelegramBot(telegram_bot_token, telegram_channel_id)

        # Event to signal retry request from Telegram button
        self.telegram_retry_event = asyncio.Event()
        self.telegram_cancel_event = asyncio.Event()

        # Store original parameters for retry functionality
        self.original_params = {
            "product_url": product_url,
            "latitude": latitude,
            "longitude": longitude,
            "check_interval": check_interval,
            "location_label": location_label,
            "continue_on_out_of_stock": continue_on_out_of_stock,
            "telegram_bot_token": telegram_bot_token,
            "telegram_channel_id": telegram_channel_id,
            "phone_number": phone_number,
            "account_name": account_name,
        }
        self.inventory_data = {}

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_product_id(url):
        """Extract product ID from URL."""
        try:
            return url.split("prid/")[-1]
        except Exception:
            return None

    async def get_product_title(self, page):
        """Try multiple selectors/meta tags to extract a reliable product title."""
        try:
            try:
                meta = await page.evaluate(
                    "() => { const m = document.querySelector(\"meta[property='og:title']\"); "
                    "return m ? m.getAttribute('content') : null; }"
                )
                if meta:
                    return meta.strip()
            except Exception:
                pass

            selectors = [
                "h1",
                ".product-title",
                ".productName",
                "[data-testid='product-title']",
                ".pdp__title",
            ]
            for sel in selectors:
                try:
                    script = (
                        f"() => {{ const el = document.querySelector('{sel}'); "
                        f"return el ? (el.innerText || el.textContent) : null; }}"
                    )
                    text = await page.evaluate(script)
                    if text:
                        return text.strip()
                except Exception:
                    continue

            try:
                title = await page.title()
                if title:
                    return title.strip()
            except Exception:
                pass

        except Exception:
            logger.debug("get_product_title: extraction failed, returning Unknown")

        return "Unknown"

    async def get_inventory_status(self, page) -> dict | None:
        """Intercept Blinkit's own product API call to extract inventory data."""

        result = {}

        async def handle_response(response):
            try:
                if "/v1/layout/product/" in response.url and response.request.method == "POST":
                    data = await response.json()
                    snippets = data.get("response", {}).get("snippets", [])

                    for snippet in snippets:
                        attrs = snippet.get("tracking", {}).get("common_attributes", {})
                        if "inventory" in attrs:
                            result["inventory"] = attrs.get("inventory")
                            result["inventory_text"] = attrs.get("inventory_text")
                            result["state"] = attrs.get("state")
                            result["price"] = attrs.get("price")
                            result["mrp"] = attrs.get("mrp")
                            logger.info(
                                f"[INVENTORY] state={result['state']} | "
                                f"inventory={result['inventory']} | "
                                f"text='{result['inventory_text']}' | "
                                f"price=₹{result['price']}"
                            )
                            break
            except Exception as e:
                logger.warning(f"[INVENTORY] Response parse error: {e}")

        page.on("response", handle_response)
        try:
            await page.goto(self.product_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
        finally:
            page.remove_listener("response", handle_response)

        if not result:
            logger.warning("[INVENTORY] No inventory data intercepted — API call may not have fired")
            logger.warning({"Product N"})

        return result or None

    async def check_stock_status(self, page):
        """Check if product is out of stock."""
        try:
            out_of_stock_indicators = [
                "text=Out of Stock",
                "text=Out of stock",
                "text=Sold Out",
                "text=Sold out",
                "text=Not Available",
                "text=Not available",
                "[class*='outofstock' i]",
                "[class*='out-of-stock' i]",
                "[class*='soldout' i]",
                "[class*='sold-out' i]",
            ]

            for indicator in out_of_stock_indicators:
                try:
                    if await page.is_visible(indicator):
                        return True
                except Exception:
                    continue

            try:
                add_button = await page.query_selector("text=ADD")
                if add_button:
                    is_disabled = await add_button.evaluate(
                        "el => el.disabled || el.getAttribute('disabled') !== null "
                        "|| String(el.className).includes('disabled')"
                    )
                    if is_disabled:
                        return True
            except Exception:
                pass

            return False
        except Exception as e:
            logger.debug(f"check_stock_status error: {e}")
            return False

    async def get_cart_product_name(self, page):
        """Extract product name from cart view."""
        try:
            selectors = [
                "[class*='DefaultProductCard__ProductTitle']",
                ".cart-item-name",
                ".product-name",
                "[data-testid='cart-item-name']",
                ".CartItem__ProductName",
                ".CartItemCard__ProductName",
                ".cart-product-title",
                ".item-name",
                ".productName",
                "[class*='ProductTitle']",
            ]

            for sel in selectors:
                try:
                    script = (
                        f"() => {{ const el = document.querySelector('{sel}'); "
                        f"return el ? (el.innerText || el.textContent) : null; }}"
                    )
                    text = await page.evaluate(script)
                    if text:
                        cleaned = text.strip()
                        if (
                            cleaned
                            and len(cleaned) > 5
                            and cleaned.lower() not in ["view more details", "add", "remove", "qty"]
                        ):
                            return cleaned
                except Exception:
                    continue

            # Try getting product info from cart item container
            try:
                script = """() => {
                    let containers = document.querySelectorAll('[class*="DefaultProductCard__Container"]');
                    if (containers.length === 0) {
                        containers = [
                            ...document.querySelectorAll('[class*="CartItem"]:not([class*="Divider"])'),
                            ...document.querySelectorAll('[class*="cart-item"]'),
                            ...document.querySelectorAll('[data-testid*="cart"]'),
                        ];
                    }
                    if (containers.length > 0) {
                        const container = containers[0];
                        let titleEl = container.querySelector('[class*="DefaultProductCard__ProductTitle"]');
                        if (titleEl) {
                            const text = (titleEl.innerText || titleEl.textContent).trim();
                            if (text && text.length > 5) return text;
                        }
                        let heading = container.querySelector('h1, h2, h3, h4, h5, h6');
                        if (heading) {
                            const text = (heading.innerText || heading.textContent).trim();
                            if (text && text.length > 5) return text;
                        }
                        let strong = container.querySelector('strong, [class*="title"], [class*="name"]');
                        if (strong) {
                            const text = (strong.innerText || strong.textContent).trim();
                            if (text && text.length > 5) return text;
                        }
                        let allText = container.innerText || container.textContent;
                        if (allText) {
                            let lines = allText.split('\\n').filter(l => {
                                const trimmed = l.trim();
                                return trimmed.length > 10 && !['add', 'remove', 'qty', 'quantity', 'view more details'].includes(trimmed.toLowerCase());
                            });
                            if (lines.length > 0) return lines[0].trim();
                        }
                    }
                    return null;
                }"""
                text = await page.evaluate(script)
                if text and text != "null":
                    return text.strip()
            except Exception as e:
                logger.debug(f"Container extraction failed: {e}")

            # Last resort
            try:
                script = """() => {
                    const titleEls = document.querySelectorAll('[class*="DefaultProductCard__ProductTitle"]');
                    if (titleEls.length > 0) {
                        const text = (titleEls[0].innerText || titleEls[0].textContent).trim();
                        if (text && text.length > 5) return text;
                    }
                    const allElements = document.querySelectorAll('[class*="product"], [class*="ProductTitle"], [class*="item"], [class*="cart"]');
                    for (let el of allElements) {
                        const text = (el.innerText || el.textContent || '').trim();
                        if (text.length > 10 && text.length < 500 &&
                            !['add', 'remove', 'qty', 'quantity', 'view more details'].includes(text.toLowerCase()) &&
                            !text.match(/^\\d+\\s*(x|\\+|Rs|₹)/i)) {
                            return text.split('\\n')[0].trim();
                        }
                    }
                    return null;
                }"""
                text = await page.evaluate(script)
                if text and text != "null":
                    return text.strip()
            except Exception as e:
                logger.debug(f"Last resort extraction failed: {e}")

        except Exception as e:
            logger.debug(f"get_cart_product_name: extraction failed: {e}")

        return "Unknown"

    @staticmethod
    def _name_similarity(a: str, b: str) -> float:
        """Simple token-overlap similarity — no external deps needed.
        Strips weight suffixes (100g, 1kg, 500ml…) before comparing.
        """

        def normalize(s):
            s = s.lower()
            s = re.sub(r"\b\d+\s*(g|kg|ml|l|gm|ltr|pcs|pack)\b", "", s)
            return set(re.findall(r"[a-z]+", s))

        tokens_a = normalize(a)
        tokens_b = normalize(b)
        if not tokens_a or not tokens_b:
            return 0.0
        return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)  # Jaccard

    async def purge_wrong_cart_items(self, expected_name=None):
        """Remove cart items that don't match expected_name."""
        removed_names = []
        try:
            cart_open = await self.order.page.is_visible("text=My Cart")
            if not cart_open:
                cart_btn = self.order.page.locator("[class*='CartButton']").first
                if await cart_btn.count() > 0:
                    await cart_btn.click()
                    await asyncio.sleep(1)

            script = """() => {
            const results = [];
            const cards = document.querySelectorAll('[class*="CartProduct__Container"]');
            cards.forEach((card, index) => {
                const nameEl = card.querySelector('[class*="ProductTitle"]');
                const name = nameEl ? nameEl.innerText.trim() : '';
                const btnContainer = card.querySelector('[class*="UpdatedButtonContainer"]');
                let qty = 1;
                if (btnContainer) {
                    const textNode = [...btnContainer.childNodes].find(
                        n => n.nodeType === Node.TEXT_NODE && n.textContent.trim() !== ''
                    );
                    qty = textNode ? parseInt(textNode.textContent.trim(), 10) || 1 : 1;
                }
                if (name) results.push({ index, name, qty });
            });
            return results;
            }"""

            items = await self.order.page.evaluate(script)
            logger.info(f"[CLEANUP] Found {len(items)} cart item(s): {[i['name'] for i in items]}")

            if not items:
                logger.warning("[CLEANUP] No cart items found — selectors may need updating")
                return removed_names

            for item in reversed(items):
                name = item.get("name", "").strip()
                idx = item.get("index")
                qty = item.get("qty", 1)

                if not name:
                    continue

                similarity = self._name_similarity(name, expected_name or "")
                is_match = similarity >= 0.6

                if is_match:
                    logger.info(f"[CLEANUP] Keeping  '{name}' (similarity={similarity:.2f})")
                    continue

                logger.warning(
                    f"[CLEANUP] Removing '{name}' (similarity={similarity:.2f}, qty={qty})"
                )

                try:
                    cards_live = self.order.page.locator('[class*="CartProduct__Container"]')
                    card = cards_live.nth(idx)

                    minus_btn = card.locator(
                        '[class*="UpdatedButtonContainer"] > [class*="StyledDiv"]'
                    ).first

                    if not await minus_btn.is_visible():
                        logger.warning(f"[CLEANUP] Minus button not visible for '{name}', skipping")
                        continue

                    for _ in range(qty):
                        await minus_btn.click()
                        await asyncio.sleep(0.4)

                        add_btn = card.locator(
                            '[class*="UpdatedButtonContainer"]'
                        ).filter(has_text="ADD")
                        if await add_btn.count() > 0:
                            break

                    removed_names.append(name)
                    logger.info(f"[CLEANUP] ✓ Removed '{name}'")
                    await asyncio.sleep(3)

                except Exception as exc:
                    logger.error(f"[CLEANUP] ✗ Exception removing '{name}': {exc}")

        except Exception as e:
            logger.error(f"purge_wrong_cart_items error: {e}")

        return removed_names

    # ── Status File ───────────────────────────────────────────────────────────

    def write_status(self, status, details=None):
        """Write status to JSON file."""
        # Ensure data directory exists
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        status_data = {
            "product_url": self.product_url,
            "product_id": self.product_id,
            "location": {
                "latitude": self.latitude,
                "longitude": self.longitude,
            },
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "query_count": self.query_count,
            "details": details or {},
            "action_needed": status == "available",
        }

        try:
            with open(STATUS_FILE, "w") as f:
                json.dump(status_data, f, indent=2)
            logger.info(f"Status: {status}")
            return True
        except Exception as e:
            logger.error(f"Error writing status file: {e}")
            return False

    # ── Core Loop ─────────────────────────────────────────────────────────────

    async def check_product_status(self):
        """Check if product is available or coming soon."""
        try:
            self.query_count += 1
            logger.info(f"[CHECK #{self.query_count}] Navigating to product URL...")

            inventory_data = {}

            try:
                async with self.order.page.expect_response(
                    lambda r: "/v1/layout/product/" in r.url and r.status == 200,
                    timeout=15000,
                ) as response_info:
                    await self.order.page.goto(
                        self.product_url, wait_until="domcontentloaded", timeout=30000
                    )

                response = await response_info.value
                body = await response.body()

                if body:
                    data = json.loads(body.decode("utf-8", errors="ignore"))
                    snippets = data.get("response", {}).get("snippets", [])
                    for snippet in snippets:
                        attrs = snippet.get("tracking", {}).get("common_attributes", {})
                        if "inventory" in attrs:
                            inventory_data["inventory"] = attrs.get("inventory")
                            inventory_data["inventory_text"] = attrs.get("inventory_text")
                            inventory_data["state"] = attrs.get("state")
                            inventory_data["price"] = attrs.get("price")
                            inventory_data["mrp"] = attrs.get("mrp")
                            logger.info(
                                f"[INVENTORY] state={inventory_data['state']} | "
                                f"inventory={inventory_data['inventory']} | "
                                f"₹{inventory_data['price']}"
                            )
                            break

            except asyncio.TimeoutError:
                logger.warning("[INVENTORY] API response timed out — falling back to UI detection")
            except Exception as e:
                logger.warning(f"[INVENTORY] Intercept error: {e}")

            await asyncio.sleep(2)

            # ── Product name ──
            product_name = "Unknown"
            coming_soon_status = "Unknown"

            try:
                raw_name = await self.get_product_title(self.order.page)
                product_name = normalize_product_name(raw_name)
                logger.info(f"[PRODUCT] Name: {colorize_product(product_name)}")

                if not self.expected_product_name:
                    self.expected_product_name = product_name
                    logger.info(
                        f"[EXPECTED] Remembered: {colorize_product(self.expected_product_name)}"
                    )
            except Exception:
                pass

            # ── Log inventory ──
            state = None
            if inventory_data:
                state = inventory_data.get("state")
                inventory = inventory_data.get("inventory")
                price = inventory_data.get("price")
                logger.info(f"[INVENTORY] {inventory} | State: {state} | ₹{price}")
                logger.warning(f"[ LOCATION ] {(self.location_label)}")
                self.inventory_data = inventory_data

                if state == "out_of_stock":
                    logger.warning("[INVENTORY] Product is out of stock per API — skipping")
                    self.write_status(
                        "out_of_stock",
                        {
                            "message": "Product is out of stock per API",
                            "last_checked": datetime.now().isoformat(),
                        },
                    )
                    return False
            else:
                logger.warning("[INVENTORY] No inventory data intercepted")

            # ── Coming Soon check ──
            try:
                if await self.order.page.is_visible("text=Coming Soon"):
                    coming_soon_status = "Coming Soon"
                else:
                    coming_soon_status = "Available"
                logger.info(f"[STATUS] \033[93m{coming_soon_status}\033[0m")
            except Exception as e:
                logger.warning(f"Error checking coming soon status: {e}")

            # ── Availability decision ──
            is_coming_soon = coming_soon_status == "Coming Soon"
            is_add_to_cart = await self.order.page.is_visible("text=ADD")

            logger.info(f"Coming Soon visible: {is_coming_soon}, Add button visible: {is_add_to_cart}")

            if is_add_to_cart and not is_coming_soon:
                logger.info(f"[AVAILABLE] Product {colorize_product(product_name)} is now AVAILABLE!")
                play_alert_sound()
                self.write_status(
                    "available",
                    {
                        "message": "Product is available for purchase!",
                        "product_name": product_name,
                        "product_id": self.product_id,
                        "found_at": datetime.now().isoformat(),
                    },
                )
                return True

            elif is_coming_soon:
                logger.info(
                    f"[WAITING] Product {colorize_product(product_name)} is still Coming Soon..."
                )
                self.write_status(
                    "coming_soon",
                    {
                        "message": "Product still Coming Soon in your location",
                        "product_name": product_name,
                        "last_checked": datetime.now().isoformat(),
                    },
                )
                return False

            else:
                logger.warning("[UNKNOWN] Could not determine product status")
                self.write_status(
                    "unknown",
                    {
                        "message": "Could not determine if product is available or coming soon",
                        "product_name": product_name,
                    },
                )
                return False

        except Exception as e:
            logger.error(f"Error checking product: {e}")
            self.write_status("error", {"error": str(e)})
            return False

    async def watch(self, max_checks=None):
        """
        Monitor product until available.

        Args:
            max_checks: Max checks before giving up (None = infinite)
        """
        logger.info("=" * 70)
        logger.info("PRODUCT WATCHER - Wait for Coming Soon to be Available")
        logger.info("=" * 70)
        logger.info(f"Product URL: {self.product_url}")
        logger.info(f"Check interval: {self.check_interval} seconds")
        logger.info(f"Max checks: {max_checks if max_checks else 'Unlimited'}")
        logger.info("-" * 70)

        # Initialize browser and auth
        try:
            logger.info("Initializing Blinkit authentication...")
            logger.info(f"Account name: {self.account_name}, Phone: {self.phone_number}")
            if self.latitude and self.longitude:
                logger.info(f"Using location: Latitude {self.latitude}, Longitude {self.longitude}")
            else:
                logger.info("No coordinates provided — will select saved address via site UI (Home)")
            self.auth = BlinkitAuth(
                headless=self.headless,
                phone_number=self.phone_number,
                account_name=self.account_name,
            )
            logger.info(f"Browser mode: {'headless' if self.headless else 'visible'}")
            logger.info("Starting browser...")
            await self.auth.start_browser()
            logger.info("Browser started successfully")

            # If coordinates not provided, try selecting saved address via location bar UI
            if not (self.latitude and self.longitude):
                try:
                    loc_sel = "div.LocationBar__Container-sc-x8ezho-6.gcLVHe"
                    if await self.auth.page.is_visible(loc_sel):
                        await self.auth.page.click(loc_sel)
                        await asyncio.sleep(1)
                        location_selector = f"text={self.location_label}"
                        if await self.auth.page.is_visible(location_selector):
                            try:
                                await self.auth.page.click(location_selector)
                            except Exception as e:
                                logger.debug(f"Direct click failed: {e}. Trying JS click and overlay workaround.")
                                try:
                                    await self.auth.page.wait_for_selector("div.LocationDropDown__LocationOverlay-sc-bx29pc-1", state="hidden", timeout=3000)
                                    await self.auth.page.click(location_selector)
                                except Exception:
                                    try:
                                        script = "(label) => { const el = Array.from(document.querySelectorAll('*')).find(n => n.innerText && n.innerText.trim() === label); if (el) { el.click(); return true; } return false; }"
                                        ok = await self.auth.page.evaluate(script, self.location_label)
                                        if not ok:
                                            await self.auth.page.evaluate("() => { const o = document.querySelector('div.LocationDropDown__LocationOverlay-sc-bx29pc-1'); if (o) o.style.pointerEvents = 'none'; }")
                                            await self.auth.page.click(location_selector)
                                    except Exception as ex2:
                                        logger.warning(f"Failed to click location via JS fallback: {ex2}")
                            await asyncio.sleep(2)
                            logger.info(f"Selected saved address: {self.location_label}")
                            if await self.auth.page.is_visible("text=My Cart"):
                                await self.auth.page.click("text=My Cart")
                                await asyncio.sleep(1)
                                logger.info("Moved to My Cart")
                        else:
                            logger.info("'Home' address not found in location options")
                    else:
                        try:
                            broad = "[class*='LocationBar__Container']"
                            if await self.auth.page.is_visible(broad):
                                await self.auth.page.click(broad)
                                await asyncio.sleep(1)
                                location_selector = f"text={self.location_label}"
                                if await self.auth.page.is_visible(location_selector):
                                    try:
                                        await self.auth.page.click(location_selector)
                                    except Exception as e:
                                        logger.debug(f"Direct click (broad) failed: {e}. Trying JS click and overlay workaround.")
                                        try:
                                            await self.auth.page.wait_for_selector("div.LocationDropDown__LocationOverlay-sc-bx29pc-1", state="hidden", timeout=3000)
                                            await self.auth.page.click(location_selector)
                                        except Exception:
                                            try:
                                                script = "(label) => { const el = Array.from(document.querySelectorAll('*')).find(n => n.innerText && n.innerText.trim() === label); if (el) { el.click(); return true; } return false; }"
                                                ok = await self.auth.page.evaluate(script, self.location_label)
                                                if not ok:
                                                    await self.auth.page.evaluate("() => { const o = document.querySelector('div.LocationDropDown__LocationOverlay-sc-bx29pc-1'); if (o) o.style.pointerEvents = 'none'; }")
                                                    await self.auth.page.click(location_selector)
                                            except Exception as ex2:
                                                logger.warning(f"Failed to click location via JS fallback (broad): {ex2}")
                                    await asyncio.sleep(2)
                                    logger.info(
                                        f"Selected saved address: {self.location_label} (broad selector)"
                                    )
                                    if await self.auth.page.is_visible("text=My Cart"):
                                        await self.auth.page.click("text=My Cart")
                                        await asyncio.sleep(1)
                                        logger.info("Moved to My Cart")
                                else:
                                    logger.info(
                                        "'Home' address not found after opening location bar"
                                    )
                        except Exception:
                            logger.debug("Broad location selector failed")
                except Exception as e:
                    logger.warning(f"Location UI selection failed: {e}")
            else:
                try:
                    if self.auth.context:
                        await self.auth.context.set_geolocation(
                            {"latitude": self.latitude, "longitude": self.longitude}
                        )
                        await self.auth.context.grant_permissions(["geolocation"])
                except Exception as e:
                    logger.warning(f"Failed to set geolocation: {e}")

            if not await self.auth.is_logged_in():
                if self.phone_number:
                    logger.info(f"Not logged in. Attempting login with phone: {self.phone_number}")
                    await self.auth.login(self.phone_number)
                    await asyncio.sleep(2)
                    otp = input("Enter OTP from your phone: ").strip()
                    if otp:
                        await self.auth.enter_otp(otp)
                        await asyncio.sleep(3)
                else:
                    logger.error("Not logged in and no phone number provided!")
                    await self.auth.close()
                    return False

            if not await self.auth.is_logged_in():
                logger.error("Login failed!")
                await self.auth.close()
                return False

            try:
                logger.info("Saving browser session to disk...")
                await self.auth.save_session()
                logger.info(f"Session saved: {self.auth.session_path}")
            except Exception as e:
                logger.warning(f"Failed to save session after login: {e}")

            logger.info("[OK] Logged in successfully")
            logger.info(f"[OK] Location set to: Lat {self.latitude}, Lon {self.longitude}")
            self.order = BlinkitOrder(self.auth.page)

            if self.telegram_bot:
                logger.info("[TELEGRAM] Starting polling for button callbacks...")

                if self.use_telegram_callbacks:

                    async def on_retry():
                        logger.info(
                            "[TELEGRAM] Retry button clicked - will restart watch after current action"
                        )
                        self.telegram_retry_event.set()

                    async def on_cancel():
                        logger.info("[TELEGRAM] Cancel button clicked - stopping watch")
                        self.telegram_cancel_event.set()

                    self.telegram_bot.register_callback("retry_watch", on_retry)
                    self.telegram_bot.register_callback("cancel_watch", on_cancel)
                    logger.info("[TELEGRAM] Retry/Cancel callbacks registered")
                else:
                    logger.info("[TELEGRAM] Retry/Cancel callbacks DISABLED by user")

                self.telegram_bot.polling_task = asyncio.create_task(
                    self.telegram_bot.start_polling()
                )

        except Exception as e:
            logger.error(f"Failed to initialize: {e}")
            return False

        # Initial status
        self.write_status("monitoring", {"started_at": datetime.now().isoformat()})

        check_num = 0
        start_time = datetime.now()

        try:
            while True:
                if self.use_telegram_callbacks and self.telegram_cancel_event.is_set():
                    logger.info("[TELEGRAM] Cancel requested - stopping watch")
                    self.write_status("stopped", {"reason": "Cancelled via Telegram"})
                    return False

                if max_checks and check_num >= max_checks:
                    logger.info(f"Max checks ({max_checks}) reached. Stopping.")
                    self.write_status("stopped", {"reason": "Max checks reached"})
                    return False

                check_num += 1

                is_available = await self.check_product_status()

                if is_available:
                    elapsed = datetime.now() - start_time
                    logger.info(
                        f"[SUCCESS] Product became available after {elapsed} ({check_num} checks)"
                    )

                    is_out_of_stock = await self.check_stock_status(self.order.page)

                    if is_out_of_stock:
                        logger.warning("[OUT OF STOCK] Product is out of stock!")
                        if self.continue_on_out_of_stock:
                            logger.info(
                                "[CONTINUE MODE] Product out of stock but continuing to monitor..."
                            )
                            self.write_status(
                                "out_of_stock_monitoring",
                                {
                                    "message": "Product went out of stock but continuing to monitor",
                                    "product_name": self.expected_product_name,
                                    "timestamp": datetime.now().isoformat(),
                                    "checks_so_far": check_num,
                                },
                            )
                            logger.info(
                                f"Waiting {self.check_interval} seconds before next check..."
                            )
                            await asyncio.sleep(self.check_interval)
                            continue
                        else:
                            logger.error("[ABORT] Stopping due to product going out of stock")
                            self.write_status(
                                "out_of_stock",
                                {
                                    "message": "Product went out of stock",
                                    "product_name": self.expected_product_name,
                                    "timestamp": datetime.now().isoformat(),
                                },
                            )
                            return False

                    # Product is in stock - proceed with auto-purchase
                    logger.info("Starting auto-purchase...")
                    if self.telegram_bot:
                        logger.info(
                            "Available Notification: Sending Telegram notification of availability..."
                        )
                        product_name = self.expected_product_name or "Unknown Product"

                        try:
                            telegram_success = (
                                await self.telegram_bot.send_product_notification(
                                    product_name=product_name,
                                    product_url=self.product_url,
                                    location_name=self.location_label,
                                    with_buttons=self.use_telegram_callbacks,
                                    product_inventory=self.inventory_data.get("inventory"),
                                )
                            )

                            if telegram_success:
                                logger.info("[OK] Telegram notification sent successfully")
                            else:
                                logger.warning("[WARN] Telegram notification failed to send")
                        except Exception as e:
                            logger.error(f"[ERROR] Telegram notification error: {e}")

                    success = await self.auto_purchase()

                    if success:
                        logger.info("[COMPLETE] Purchase completed!")
                        self.write_status(
                            "purchased",
                            {
                                "completed_at": datetime.now().isoformat(),
                                "total_checks": check_num,
                            },
                        )
                        return True
                    else:
                        logger.warning("Auto-purchase failed. Manual intervention needed.")
                        self.write_status(
                            "available",
                            {
                                "message": "Product available but auto-purchase failed",
                                "action": "Manual purchase needed",
                            },
                        )
                        return False

                logger.info(f"Waiting {self.check_interval} seconds before next check...")
                await asyncio.sleep(self.check_interval)

        except KeyboardInterrupt:
            logger.info("\n[STOPPED] Watcher stopped by user")
            elapsed = datetime.now() - start_time
            self.write_status(
                "stopped",
                {
                    "reason": "User interrupted",
                    "checks_performed": check_num,
                    "duration": str(elapsed),
                },
            )
            return False

        finally:
            if self.telegram_bot and self.telegram_bot.is_polling:
                await self.telegram_bot.stop_polling()

            if self.auth:
                try:
                    if hasattr(self.auth, "close"):
                        await self.auth.close()
                    logger.info("Browser closed")
                except Exception as e:
                    logger.debug(f"Browser close error: {e}")

    # ── Auto Purchase ─────────────────────────────────────────────────────────

    async def auto_purchase(self):
        """Automatically add to cart and proceed to checkout."""
        try:
            logger.info("Step 1: Verifying product details before adding to cart...")

            raw_name = await self.get_product_title(self.order.page)
            product_name = normalize_product_name(raw_name)

            logger.info(f"[VERIFY] Product on screen: {colorize_product(product_name)}")
            self.expected_product_name = product_name

            logger.info("Step 2: Adding product to cart...")

            add_selectors = [
                "role=button[name='Add to cart']",
                "text='Add to cart'",
                "text=Add to cart",
                ".add-to-cart",
                "button:has-text('Add to cart')",
            ]
            clicked = False
            for sel in add_selectors:
                try:
                    if await self.order.page.is_visible(sel):
                        await self.order.page.click(sel)
                        await asyncio.sleep(2)
                        logger.info(f"[OK] Clicked ADD selector: {sel}")
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                logger.error("ADD button not found with known selectors")
                return False

            logger.info("Step 3: Opening cart...")
            if await self.order.page.is_visible("text=My Cart"):
                await self.order.page.click("text=My Cart")
                await asyncio.sleep(2)
                logger.info("[OK] Cart opened")

            # Clean up any previous items that don't match the product title
            logger.info("Step 3a: Cleaning cart of mismatched items...")
            removed = await self.purge_wrong_cart_items(product_name)
            logger.info(
                f"[CLEANUP] Cart cleanup completed. Removed items: {removed if removed else 'None'}"
            )
            if removed:
                logger.info(
                    f"[CLEANUP] Removed {len(removed)} mismatched item(s) from cart: {removed}"
                )
                await asyncio.sleep(3)
                if await self.order.page.is_visible("text=My Cart"):
                    await self.order.page.click("text=My Cart")
                    await asyncio.sleep(3)

            logger.info("Step 3b: Verifying product in cart...")
            cart_product_name_raw = await self.get_cart_product_name(self.order.page)
            cart_product_name = normalize_product_name(cart_product_name_raw)
            logger.info(f"[CART] Product in cart: {colorize_product(cart_product_name)}")

            if removed:
                logger.info(
                    f"[CLEANUP] Removed {len(removed)} mismatched item(s) from cart: {removed}"
                )
                await asyncio.sleep(2)

            # Increment quantity if needed
            if self.quantity > 1:
                await asyncio.sleep(1)
                logger.info(f"[QTY] Incrementing quantity to {self.quantity} inside cart...")
                for i in range(self.quantity - 1):
                    try:
                        card = self.order.page.locator(
                            '[class*="CartProduct__Container"]'
                        ).first

                        plus_btn = card.locator(
                            '[class*="UpdatedButtonContainer"] > [class*="StyledDiv"]'
                        ).last

                        if await plus_btn.is_visible():
                            await plus_btn.click()
                            await asyncio.sleep(1)
                            logger.info(f"[QTY] Incremented to {i + 2}/{self.quantity}")
                        else:
                            logger.warning(f"[QTY] + button not visible — stopped at {i + 1}")
                            break

                        limit_msg = self.order.page.get_by_text(
                            "Sorry, you can't add more of this item"
                        )
                        try:
                            if await limit_msg.is_visible(timeout=500):
                                logger.warning(f"[QTY] Quantity limit reached at {i + 1}")
                                break
                        except Exception:
                            pass

                    except Exception as e:
                        logger.error(f"[QTY] Error incrementing quantity: {e}")
                        break

                logger.info(f"[QTY] Done — final quantity: {self.quantity}")

            # Verify cart product matches
            if cart_product_name != "Unknown":
                if cart_product_name.strip().lower() != product_name.strip().lower():
                    logger.warning(
                        f"[MISMATCH] Cart product '{cart_product_name}' does not equal "
                        f"page product '{product_name}'"
                    )

                    removed = await self.purge_wrong_cart_items(product_name)
                    logger.info(
                        f"[CLEANUP] Post-mismatch cleanup completed. Removed items: {removed}"
                    )

                    if not await self.order.page.is_visible("text=My Cart"):
                        await self.order.page.click("text=My Cart")
                        await asyncio.sleep(2)

                    cart_product_name_raw = await self.get_cart_product_name(self.order.page)
                    cart_product_name = normalize_product_name(cart_product_name_raw)
                    logger.info(
                        f"[CART] Product in cart after cleanup: {colorize_product(cart_product_name)}"
                    )

                    if cart_product_name.strip().lower() != product_name.strip().lower():
                        logger.warning("Auto-purchase failed. Manual intervention needed.")
                        self.write_status(
                            "available",
                            {"message": "Product added but cart contains wrong item after cleanup"},
                        )
                        return False
                else:
                    logger.info("[OK] Cart product matches page product")

            logger.info("[SUCCESS] Product successfully added to cart!")
            print("\n" + "=" * 70)
            print("✓ PRODUCT ADDED TO CART")
            print("=" * 70)
            print(f"Product: {colorize_product(cart_product_name)}")
            print("=" * 70)
            print("\033[92m" + BANNER + "\033[0m")  # green

            # Check if user wants to automate checkout
            if not self.automate_checkout:
                logger.info(
                    "[USER] Automate checkout disabled - waiting for manual completion "
                    "or Telegram callback"
                )
                print("\nManually complete the checkout at your convenience.")
                print("Awaiting Telegram callback (Retry/Cancel) or manual completion...")
                print("=" * 70 + "\n")

                self.write_status(
                    "added_to_cart",
                    {
                        "message": "Product successfully added to cart",
                        "product_name": cart_product_name,
                        "added_at": datetime.now().isoformat(),
                    },
                )

                if self.use_telegram_callbacks:
                    max_wait_time = 600  # 10 minutes max wait
                    elapsed = 0

                    while elapsed < max_wait_time:
                        if self.use_telegram_callbacks and self.telegram_retry_event.is_set():
                            logger.info("[TELEGRAM] Retry button clicked - restarting watch")
                            return False

                        if self.use_telegram_callbacks and self.telegram_cancel_event.is_set():
                            logger.info("[TELEGRAM] Cancel button clicked - stopping watch")
                            return False
                        await asyncio.sleep(5)
                        elapsed += 5
                else:
                    logger.info(
                        "[INFO] Telegram callbacks disabled — not waiting for Retry/Cancel"
                    )
                return True

            logger.info("[USER] Proceeding with automated checkout steps")

            # ── Step 4+5: Open cart drawer → click Proceed to Pay ──
            logger.info("Step 4+5: Opening cart and proceeding to pay via CheckoutService...")
            checkout = CheckoutService(self.order.page)
            await checkout.place_order()
            logger.info("[CHECKOUT] place_order done — now on payment page")

            if self.use_telegram_callbacks and self.telegram_cancel_event.is_set():
                return False
            if self.use_telegram_callbacks and self.telegram_retry_event.is_set():
                return False

            # ── Step 6: Select payment method ──
            logger.info(
                f"Step 6: Selecting payment method: {self.preferred_payment.upper()}..."
            )
            payment_result = None

            if self.preferred_payment == "upi":
                payment_result = await checkout.select_upi_payment()
                logger.info(f"[CHECKOUT] Payment selection result: {payment_result}")

                logger.info(f"[NOTIFY] telegram_bot set: {self.telegram_bot is not None}")
                logger.info(
                    f"[NOTIFY] payment_result is dict: {isinstance(payment_result, dict)}"
                )
                logger.info(
                    f"[NOTIFY] upi_url present: "
                    f"{payment_result.get('upi_url') if isinstance(payment_result, dict) else 'NOT FOUND'}"
                )
                logger.info(
                    f"[NOTIFY] amount present: "
                    f"{payment_result.get('amount') if isinstance(payment_result, dict) else 'Found'}"
                )

                if (
                    self.telegram_bot
                    and isinstance(payment_result, dict)
                    and payment_result.get("upi_url")
                    and payment_result.get("amount")
                ):
                    logger.info("[NOTIFY] Sending UPI payment notification...")
                    success = await self.telegram_bot.send_upi_payment_notification(
                        product_name=self.expected_product_name or "Unknown Product",
                        product_url=self.product_url,
                        quantity=self.quantity,
                        product_inventory=self.inventory_data.get("inventory"),
                        amount=payment_result["amount"],
                        upi_url=payment_result["upi_url"],
                        location_name=self.location_label,
                    )
                    logger.info(f"[NOTIFY] Notification sent: {success}")
                else:
                    logger.warning(
                        "[NOTIFY] Skipped — one or more conditions failed (see above)"
                    )

            elif self.preferred_payment == "mobi":
                payment_result = await checkout.select_mobikwik_payment()
                logger.info(f"[CHECKOUT] Payment selection result: {payment_result}")
                if "Packing your order" in str(payment_result):
                    logger.info("[SUCCESS] Order already confirmed during payment selection")
                    return True

            elif self.preferred_payment == "cash":
                payment_result = await checkout.select_cash_payment()
                logger.info(f"[CHECKOUT] Payment selection result: {payment_result}")
                if "Packing your order" in str(payment_result):
                    logger.info("[SUCCESS] Order already confirmed during payment selection")
                    return True

            if self.use_telegram_callbacks and self.telegram_cancel_event.is_set():
                return False
            if self.use_telegram_callbacks and self.telegram_retry_event.is_set():
                return False

            # ── Step 7: Click Pay Now ──
            logger.info("Step 7: Clicking Pay Now via CheckoutService...")
            try:
                pay_result = await checkout.select_cash_payment()
                logger.info(f"[CHECKOUT] select_cash_payment result: {pay_result}")
            except Exception as e:
                logger.warning(
                    f"[CHECKOUT] select_cash_payment threw exception "
                    f"(page may have already navigated): {e}"
                )
                current_url = self.order.page.url
                if "account/orders/track" in current_url:
                    logger.info(
                        "[SUCCESS] Already on order tracking page - payment was successful"
                    )
                    return True
                pay_result = f"Error: {e}"

            if "Could not find" in str(pay_result) or "Error" in str(pay_result):
                max_wait_seconds = 600
                interval = 10

                logger.warning(
                    f"Pay Now could not be clicked — waiting up to {max_wait_seconds}s "
                    f"for manual completion..."
                )
                for elapsed in range(0, max_wait_seconds, interval):
                    if self.use_telegram_callbacks and self.telegram_cancel_event.is_set():
                        logger.info("[TELEGRAM] Cancel during manual payment wait")
                        return False
                    if self.use_telegram_callbacks and self.telegram_retry_event.is_set():
                        logger.info("[TELEGRAM] Retry during manual payment wait")
                        return False
                    remaining = max_wait_seconds - elapsed
                    logger.info(f"[WAIT] {elapsed}s elapsed, {remaining}s remaining...")
                    await asyncio.sleep(interval)
            else:
                await asyncio.sleep(2)

            logger.info("[SUCCESS] Checkout steps completed")
            return True

        except Exception as e:
            logger.error(f"Auto-purchase error: {e}")
            return False
