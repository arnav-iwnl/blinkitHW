"""
Simple Product Watcher
- Ask user for product URL
- Monitor if product is "Coming Soon" or available
- Auto-purchase when available
"""

import asyncio
import json
import logging
import re
import sys
import os
import difflib
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file (specify absolute path)
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# Platform-specific sound alert
if sys.platform == "win32":
    import winsound

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

banner = r"""







██████╗ ██╗   ██╗██╗   ██╗    ████████╗██╗  ██╗ █████╗ ████████╗    ███████╗██╗  ██╗██╗████████╗    ██╗██╗
██╔══██╗██║   ██║╚██╗ ██╔╝    ╚══██╔══╝██║  ██║██╔══██╗╚══██╔══╝    ██╔════╝██║  ██║██║╚══██╔══╝    ██║██║
██████╔╝██║   ██║ ╚████╔╝        ██║   ███████║███████║   ██║       ███████╗███████║██║   ██║       ██║██║
██╔══██╗██║   ██║  ╚██╔╝         ██║   ██╔══██║██╔══██║   ██║       ╚════██║██╔══██║██║   ██║       ╚═╝╚═╝
██████╔╝╚██████╔╝   ██║          ██║   ██║  ██║██║  ██║   ██║       ███████║██║  ██║██║   ██║       ██╗██╗
╚═════╝  ╚═════╝    ╚═╝          ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝       ╚══════╝╚═╝  ╚═╝╚═╝   ╚═╝       ╚═╝╚═╝
                                                                                                          
   
   
                                                                                                   
"""


from src.auth import BlinkitAuth
from src.order.blinkit_order import BlinkitOrder
from src.telegram.service import TelegramBot

# Status file location
STATUS_FILE = Path("product_status.json")

# ANSI color code for product names
PRODUCT_COLOR = '\033[95m'  # Magenta
RESET_COLOR = '\033[0m'

def colorize_product(name):
    """Wrap product name with color codes"""
    return f"{PRODUCT_COLOR}{name}{RESET_COLOR}"


def normalize_product_name(name: str) -> str:
    """Reduce product title by stripping price/extra text.

    Many Blinkit pages append price or promotional text to the title (e.g.
    "Product Name Price - Buy Online at ₹167 in India").  This helper keeps
    only the base product name by cutting at common markers.
    """
    if not name:
        return name
    # cut at 'Price' keyword or currency symbols
    import re
    # split on 'Price' word or currency symbols or pipe characters
    parts = re.split(r"\bPrice\b|₹|Rs\.?|\|", name)
    return parts[0].strip()

def play_alert_sound():
    """Play an alert sound when product is available"""
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
            print('\a', end='', flush=True)
    except Exception as e:
        logger.debug(f"Failed to play alert sound: {e}")


class OrdinalDateFormatter(logging.Formatter):
    """Custom formatter with ordinal dates and colored output"""
    
    # ANSI color codes
    LEVEL_COLORS = {
        'DEBUG': {
            'time': '\033[36m',      # Cyan for time
            'level': '\033[36m',     # Cyan for level
            'message': '\033[36m'    # Cyan for message
        },
        'INFO': {
            'time': '\033[94m',      # Blue for time
            'level': '\033[92m',     # Green for level
            'message': '\033[92m'    # Green for message
        },
        'WARNING': {
            'time': '\033[94m',      # Blue for time
            'level': '\033[93m',     # Yellow for level
            'message': '\033[93m'    # Yellow for message
        },
        'ERROR': {
            'time': '\033[94m',      # Blue for time
            'level': '\033[91m',     # Red for level
            'message': '\033[91m'    # Red for message
        },
        'CRITICAL': {
            'time': '\033[94m',      # Blue for time
            'level': '\033[95m',     # Magenta for level
            'message': '\033[95m'    # Magenta for message
        }
    }
    
    RESET = '\033[0m'
    
    def format(self, record):
        # Convert timestamp to ordinal date format
        dt = datetime.fromtimestamp(record.created)
        day = dt.day
        month = dt.strftime('%b')
        year = dt.year
        time_str = dt.strftime('%I:%M:%S %p')
        
        # Add ordinal suffix to day
        if 10 <= day % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        
        ordinal_date = f"{day}{suffix} {month} {year} {time_str}"
        
        # Get colors for this level
        colors = self.LEVEL_COLORS.get(record.levelname, self.LEVEL_COLORS['INFO'])
        
        # Apply colors to each component
        colored_time = f"{colors['time']}{ordinal_date}{self.RESET}"
        colored_level = f"{colors['level']}{record.levelname}{self.RESET}"
        colored_message = f"{colors['message']}{record.getMessage()}{self.RESET}"
        
        # Format: [colored_time] - colored_level - colored_message
        log_message = f"{colored_time} - {colored_level} - {colored_message}"
        
        return log_message


# Configure logging with custom formatter
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(OrdinalDateFormatter())

file_handler = logging.FileHandler('product_watcher.log', encoding='utf-8')
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%d %b %Y %I:%M %p')
file_handler.setFormatter(file_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler],
    force=True
)
logger = logging.getLogger(__name__)


class ProductWatcher:
    def __init__(self, product_url, latitude, longitude, check_interval=30, location_label="Home", continue_on_out_of_stock=True, telegram_bot_token=None, telegram_channel_id=None, automate_checkout=False):
        """
        Initialize the product watcher
        
        Args:
            product_url: Full Blinkit product URL
            latitude: Delivery location latitude
            longitude: Delivery location longitude
            check_interval: Time between checks in seconds
            location_label: Saved address label to select (default 'Home')
            continue_on_out_of_stock: Keep monitoring if product goes out of stock (default False)
            telegram_bot_token: Telegram bot token for notifications
            telegram_channel_id: Telegram channel ID for notifications
            automate_checkout: If True, automatically proceed with checkout steps (default False)
        """
        self.product_url = product_url
        self.latitude = latitude
        self.longitude = longitude
        self.check_interval = check_interval
        self.query_count = 0
        self.auth = None
        self.order = None
        self.product_id = self.extract_product_id(product_url)
        self.expected_product_name = None
        # Label of the saved address to select via site UI (e.g., 'Home')
        self.location_label = location_label or "Home"
        # Continue refreshing if product goes out of stock
        self.continue_on_out_of_stock = continue_on_out_of_stock
        # Automatically proceed with checkout steps
        self.automate_checkout = automate_checkout
        
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
            "telegram_channel_id": telegram_channel_id
        }

    def extract_product_id(self, url):
        """Extract product ID from URL"""
        try:
            return url.split("prid/")[-1]
        except:
            return None

    async def get_product_title(self, page):
        """Try multiple selectors/meta tags to extract a reliable product title."""
        # Use page.evaluate to read DOM properties directly to avoid locator wait issues
        try:
            try:
                meta = await page.evaluate("() => { const m = document.querySelector(\"meta[property='og:title']\"); return m ? m.getAttribute('content') : null; }")
                if meta:
                    return meta.strip()
            except Exception:
                pass

            selectors = ["h1", ".product-title", ".productName", "[data-testid='product-title']", ".pdp__title"]
            for sel in selectors:
                try:
                    script = f"() => {{ const el = document.querySelector('{sel}'); return el ? (el.innerText || el.textContent) : null; }}"
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
            # Any unexpected Playwright errors should not crash the watcher
            logger.debug("get_product_title: extraction failed, returning Unknown")

        return "Unknown"

    async def check_stock_status(self, page):
        """Check if product is out of stock"""
        try:
            # Common out of stock indicators
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
                "[class*='sold-out' i]"
            ]
            
            for indicator in out_of_stock_indicators:
                try:
                    if await page.is_visible(indicator):
                        return True
                except Exception:
                    continue
            
            # Also check if ADD button is disabled
            try:
                add_button = await page.query_selector("text=ADD")
                if add_button:
                    is_disabled = await add_button.evaluate("el => el.disabled || el.getAttribute('disabled') !== null || String(el.className).includes('disabled')")
                    if is_disabled:
                        return True
            except Exception:
                pass
            
            return False
        except Exception as e:
            logger.debug(f"check_stock_status error: {e}")
            return False

    async def get_cart_product_name(self, page):
        """Extract product name from cart view"""
        try:
            # Try common cart item selectors - including DefaultProductCard pattern from Blinkit
            selectors = [
                "[class*='DefaultProductCard__ProductTitle']",  # Main pattern from Blinkit cart
                ".cart-item-name",
                ".product-name",
                "[data-testid='cart-item-name']",
                ".CartItem__ProductName",
                ".CartItemCard__ProductName",
                ".cart-product-title",
                ".item-name",
                ".productName",
                "[class*='ProductTitle']"  # Generic product title class
            ]
            
            for sel in selectors:
                try:
                    script = f"() => {{ const el = document.querySelector('{sel}'); return el ? (el.innerText || el.textContent) : null; }}"
                    text = await page.evaluate(script)
                    if text:
                        cleaned = text.strip()
                        # Avoid returning generic buttons or links
                        if cleaned and len(cleaned) > 5 and not cleaned.lower() in ['view more details', 'add', 'remove', 'qty']:
                            return cleaned
                except Exception:
                    continue
            
            # Try getting product info from cart item container - more flexible approach
            try:
                script = """() => {
                    // Look for DefaultProductCard containers first (most reliable for Blinkit)
                    let containers = document.querySelectorAll('[class*="DefaultProductCard__Container"]');
                    
                    if (containers.length === 0) {
                        // Fallback to other cart patterns
                        containers = [
                            ...document.querySelectorAll('[class*="CartItem"]:not([class*="Divider"])'),
                            ...document.querySelectorAll('[class*="cart-item"]'),
                            ...document.querySelectorAll('[data-testid*="cart"]'),
                        ];
                    }
                    
                    if (containers.length > 0) {
                        const container = containers[0];
                        
                        // Try to find product name in various places
                        // 1. Look for DefaultProductCard title first (most reliable)
                        let titleEl = container.querySelector('[class*="DefaultProductCard__ProductTitle"]');
                        if (titleEl) {
                            const text = (titleEl.innerText || titleEl.textContent).trim();
                            if (text && text.length > 5) return text;
                        }
                        
                        // 2. Look for headings
                        let heading = container.querySelector('h1, h2, h3, h4, h5, h6');
                        if (heading) {
                            const text = (heading.innerText || heading.textContent).trim();
                            if (text && text.length > 5) return text;
                        }
                        
                        // 3. Look for text in strong/bold elements
                        let strong = container.querySelector('strong, [class*="title"], [class*="name"]');
                        if (strong) {
                            const text = (strong.innerText || strong.textContent).trim();
                            if (text && text.length > 5) return text;
                        }
                        
                        // 4. Get all text and extract first meaningful line
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
            
            # Last resort: try to find any product-related text in the page
            try:
                script = """() => {
                    // Look for DefaultProductCard__ProductTitle first (most reliable)
                    const titleEls = document.querySelectorAll('[class*="DefaultProductCard__ProductTitle"]');
                    if (titleEls.length > 0) {
                        const text = (titleEls[0].innerText || titleEls[0].textContent).trim();
                        if (text && text.length > 5) return text;
                    }
                    
                    // Look for any element containing product-like text
                    const allElements = document.querySelectorAll('[class*="product"], [class*="ProductTitle"], [class*="item"], [class*="cart"]');
                    for (let el of allElements) {
                        const text = (el.innerText || el.textContent || '').trim();
                        // Look for text that's likely a product name (reasonable length, not generic words)
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
        import re

        def normalize(s):
            s = s.lower()
            s = re.sub(r'\b\d+\s*(g|kg|ml|l|gm|ltr|pcs|pack)\b', '', s)
            return set(re.findall(r'[a-z]+', s))

        tokens_a = normalize(a)
        tokens_b = normalize(b)
        if not tokens_a or not tokens_b:
            return 0.0
        return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)  # Jaccard
    # async def purge_wrong_cart_items(self, expected_name=None):
    #     """Remove any cart items whose name does not match the expected product.

    #     This method will open the cart if necessary, scrape the list of item ids
    #     and titles, and then invoke the order service to remove anything that
    #     looks like a mismatch (based on fuzzy similarity). It returns a list of
    #     removed product ids for logging.
    #     """
    #     removed_ids = []
    #     try:
    #         # ensure cart is visible before scraping
    #         if not await self.order.page.is_visible("text=My Cart"):
    #             # try clicking cart button if not open
    #             if await self.order.page.is_visible("div[class*='CartButton']"):
    #                 await self.order.page.click("div[class*='CartButton']")
    #                 await asyncio.sleep(1)

    #         # gather id/name pairs from the cart DOM
    #         script = """() => {
    #             const result = [];
    #             const containers = document.querySelectorAll('[id]');
    #             containers.forEach(c => {
    #                 const id = c.id;
    #                 if (!id) return;
    #                 // heuristic: container is part of a cart item
    #                 if (c.closest('[class*="Cart"]') || String(c.className).includes("DefaultProductCard__Container")) {
    #                     let nameEl = c.querySelector('[class*="ProductTitle"], .cart-item-name, .product-name, [data-testid="cart-item-name"]');
    #                     let name = nameEl ? nameEl.innerText.trim() : '';
    #                     if (name) result.push({id, name});
    #                 }
    #             });
    #             return result;
    #         }"""
    #         items = await self.order.page.evaluate(script)

    #         for item in items:
    #             name = item.get("name", "")
    #             pid = item.get("id")
    #             if expected_name and name:
    #                 # remove if name differs (case-insensitive)
    #                 if name.strip().lower() != expected_name.strip().lower():
    #                     logger.warning(f"[CLEANUP] Removing mismatched cart item '{name}' (id={pid}) expected '{expected_name}'")
    #                     try:
    #                         await self.order.remove_from_cart(pid, quantity=10)
    #                         removed_ids.append(pid)
    #                     except Exception as exc:
    #                         logger.error(f"Failed to remove {pid}: {exc}")
    #     except Exception as e:
    #         logger.error(f"purge_wrong_cart_items error: {e}")
    #     return removed_ids

    async def purge_wrong_cart_items(self, expected_name=None):
        """
        Remove cart items that don't match expected_name.
        Works without product IDs by clicking minus buttons directly
        from the scraped DOM — matches actual Blinkit cart HTML structure.
        """
        removed_names = []
        try:
        # --- 1. Ensure cart panel is open ---
            cart_open = await self.order.page.is_visible("text=My Cart")
            if not cart_open:
                cart_btn = self.order.page.locator("[class*='CartButton']").first
                if await cart_btn.count() > 0:
                    await cart_btn.click()
                    await asyncio.sleep(1)

        # --- 2. Scrape using ACTUAL Blinkit class names from your HTML ---
        # No id-walking needed — grab name + qty directly from card structure
            script = """() => {
            const results = [];

            // Each cart row is wrapped in CartProduct__Container
            const cards = document.querySelectorAll(
                '[class*="CartProduct__Container"]'
            );

            cards.forEach((card, index) => {
                // Product title: DefaultProductCard__ProductTitle
                const nameEl = card.querySelector('[class*="ProductTitle"]');
                const name = nameEl ? nameEl.innerText.trim() : '';

                // Quantity: the text node between the two AddToCart StyledDivs
                // It sits as a direct text child inside UpdatedButtonContainer
                const btnContainer = card.querySelector(
                    '[class*="UpdatedButtonContainer"]'
                );
                let qty = 1;
                if (btnContainer) {
                    // childNodes includes raw text nodes — qty is the middle one
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
            logger.info(
                f"[CLEANUP] Found {len(items)} cart item(s): {[i['name'] for i in items]}"
            )

            if not items:
                logger.warning(
                "[CLEANUP] No cart items found — selectors may need updating")
                return removed_names

        # --- 3. Iterate in REVERSE so index positions stay stable after removal ---
            for item in reversed(items):
                name = item.get("name", "").strip()
                idx = item.get("index")
                qty = item.get("qty", 1)

                if not name:
                    continue

                similarity = self._name_similarity(name, expected_name or "")
                is_match = similarity >= 0.6

                if is_match:
                    logger.info(
                        f"[CLEANUP] Keeping  '{name}' (similarity={similarity:.2f})")
                    continue

                logger.warning(
                f"[CLEANUP] Removing '{name}' "
                f"(similarity={similarity:.2f}, qty={qty})"
                )

            # --- 4. Re-locate the card at this index and click minus qty times ---
                try:
                    cards_live = self.order.page.locator(
                        '[class*="CartProduct__Container"]')
                    card = cards_live.nth(idx)

                # Minus button = first StyledDiv inside UpdatedButtonContainer
                    minus_btn = card.locator(
                        '[class*="UpdatedButtonContainer"] > [class*="StyledDiv"]').first

                    if not await minus_btn.is_visible():
                        logger.warning(
                        f"[CLEANUP] Minus button not visible for '{name}', skipping")
                        continue

                    for _ in range(qty):
                        await minus_btn.click()
                        await asyncio.sleep(0.4)

                    # Stop early if ADD button reappears (item fully removed)
                        add_btn = card.locator(
                            '[class*="UpdatedButtonContainer"]').filter(has_text="ADD")
                        if await add_btn.count() > 0:
                            break

                    removed_names.append(name)
                    logger.info(f"[CLEANUP] ✓ Removed '{name}'")
                # let cart DOM settle before next removal
                    await asyncio.sleep(0.5)

                except Exception as exc:
                    logger.error(f"[CLEANUP] ✗ Exception removing '{name}': {exc}")

            # Wait for all removals to complete and DOM to stabilize
            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"purge_wrong_cart_items error: {e}")

        return removed_names
    def write_status(self, status, details=None):
        """Write status to JSON file"""
        status_data = {
            "product_url": self.product_url,
            "product_id": self.product_id,
            "location": {
                "latitude": self.latitude,
                "longitude": self.longitude
            },
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "query_count": self.query_count,
            "details": details or {},
            "action_needed": status == "available"
        }
        
        try:
            with open(STATUS_FILE, 'w') as f:
                json.dump(status_data, f, indent=2)
            logger.info(f"Status: {status}")
            return True
        except Exception as e:
            logger.error(f"Error writing status file: {e}")
            return False

    async def check_product_status(self):
        """Check if product is available or coming soon"""
        try:
            self.query_count += 1
            logger.info(f"[CHECK #{self.query_count}] Navigating to product URL...")
            
            # Navigate to product URL
            try:
                await self.order.page.goto(self.product_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                logger.warning(f"Navigation took longer: {e}")
            
            await asyncio.sleep(2)
            
            # Get product details from page
            product_name = "Unknown"
            coming_soon_status = "Unknown"
            
            try:
                # Extract product name using robust extractor
                try:
                    raw_name = await self.get_product_title(self.order.page)
                    product_name = normalize_product_name(raw_name)
                    logger.info(f"[PRODUCT] Name: {colorize_product(product_name)}")
                    # Store for verification during purchase if not already set
                    if not self.expected_product_name:
                        self.expected_product_name = product_name
                        logger.info(f"[EXPECTED] Remembered product name for verification: {colorize_product(self.expected_product_name)}")
                except Exception:
                    # Ignore extraction errors and continue to status checks
                    pass

                # Check for "Coming Soon" text
                if await self.order.page.is_visible("text=Coming Soon"):
                    coming_soon_status = "Coming Soon"
                else:
                    coming_soon_status = "Available"
                    
                logger.info(f"[STATUS] {coming_soon_status}")
                
            except Exception as e:
                logger.warning(f"Error extracting product details: {e}")
            
            # Check if product is AVAILABLE (no Coming Soon, has ADD button)
            is_coming_soon = coming_soon_status == "Coming Soon"
            is_add_to_cart = await self.order.page.is_visible("text=ADD")
            
            logger.info(f"Coming Soon visible: {is_coming_soon}, Add button visible: {is_add_to_cart}")
            
            if is_add_to_cart and not is_coming_soon:
                # Product is AVAILABLE
                logger.info(f"[AVAILABLE] Product {colorize_product(product_name)} is now AVAILABLE!")
                
                # Play alert sound
                play_alert_sound()
                
                self.write_status("available", {
                    "message": "Product is available for purchase!",
                    "product_name": product_name,
                    "product_id": self.product_id,
                    "found_at": datetime.now().isoformat()
                })
                return True
                    
            elif is_coming_soon:
                # Still coming soon
                logger.info(f"[WAITING] Product {colorize_product(product_name)} is still Coming Soon...")
                self.write_status("coming_soon", {
                    "message": "Product still Coming Soon in your location",
                    "product_name": product_name,
                    "last_checked": datetime.now().isoformat()
                })
                return False
            else:
                logger.warning("[UNKNOWN] Could not determine product status")
                self.write_status("unknown", {
                    "message": "Could not determine if product is available or coming soon",
                    "product_name": product_name
                })
                return False
                
        except Exception as e:
            logger.error(f"Error checking product: {e}")
            self.write_status("error", {"error": str(e)})
            return False

    async def watch(self, max_checks=None):
        """
        Monitor product until available
        
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
            if self.latitude and self.longitude:
                logger.info(f"Using location: Latitude {self.latitude}, Longitude {self.longitude}")
            else:
                logger.info("No coordinates provided — will select saved address via site UI (Home)")
            self.auth = BlinkitAuth(headless=True) # Show browser
            await self.auth.start_browser()

            # If coordinates not provided, try selecting saved 'Home' address via the location bar UI
            if not (self.latitude and self.longitude):
                try:
                    # Selector for the location bar container (two classes)
                    loc_sel = "div.LocationBar__Container-sc-x8ezho-6.gcLVHe"
                    # Fallback: partial class match
                    if await self.auth.page.is_visible(loc_sel):
                        await self.auth.page.click(loc_sel)
                        await asyncio.sleep(1)
                        # Look for a saved address labeled per user preference
                        location_selector = f"text={self.location_label}"
                        if await self.auth.page.is_visible(location_selector):
                            await self.auth.page.click(location_selector)
                            await asyncio.sleep(2)
                            logger.info(f"Selected saved address: {self.location_label}")
                            # Move cursor to My Cart button to dismiss location selector
                            if await self.auth.page.is_visible("text=My Cart"):
                                await self.auth.page.click("text=My Cart")
                                await asyncio.sleep(1)
                                logger.info("Moved to My Cart")
                        else:
                            logger.info("'Home' address not found in location options")
                    else:
                        # Try a broader selector
                        try:
                            broad = "[class*='LocationBar__Container']"
                            if await self.auth.page.is_visible(broad):
                                await self.auth.page.click(broad)
                                await asyncio.sleep(1)
                                location_selector = f"text={self.location_label}"
                                if await self.auth.page.is_visible(location_selector):
                                    await self.auth.page.click(location_selector)
                                    await asyncio.sleep(2)
                                    logger.info(f"Selected saved address: {self.location_label} (broad selector)")
                                    # Move cursor to My Cart button to dismiss location selector
                                    if await self.auth.page.is_visible("text=My Cart"):
                                        await self.auth.page.click("text=My Cart")
                                        await asyncio.sleep(1)
                                        logger.info("Moved to My Cart")
                                else:
                                    logger.info("'Home' address not found after opening location bar")
                        except Exception:
                            logger.debug("Broad location selector failed")
                except Exception as e:
                    logger.warning(f"Location UI selection failed: {e}")
            else:
                # If coordinates provided, set geolocation in context
                try:
                    if self.auth.context:
                        await self.auth.context.set_geolocation({"latitude": self.latitude, "longitude": self.longitude})
                        await self.auth.context.grant_permissions(["geolocation"])
                except Exception as e:
                    logger.warning(f"Failed to set geolocation: {e}")
            
            if not await self.auth.is_logged_in():
                logger.error("Not logged in!")
                await self.auth.close()
                return False
            
            logger.info("[OK] Logged in successfully")
            logger.info(f"[OK] Location set to: Lat {self.latitude}, Lon {self.longitude}")
            self.order = BlinkitOrder(self.auth.page)
            
            # Start Telegram polling if configured
            if self.telegram_bot:
                logger.info("[TELEGRAM] Starting polling for button callbacks...")
                
                # Register callback handlers
                async def on_retry():
                    logger.info("[TELEGRAM] Retry button clicked - will restart watch after current action")
                    self.telegram_retry_event.set()
                
                async def on_cancel():
                    logger.info("[TELEGRAM] Cancel button clicked - stopping watch")
                    self.telegram_cancel_event.set()
                
                self.telegram_bot.register_callback("retry_watch", on_retry)
                self.telegram_bot.register_callback("cancel_watch", on_cancel)
                
                # Start polling in background
                self.telegram_bot.polling_task = asyncio.create_task(self.telegram_bot.start_polling())
            
        except Exception as e:
            logger.error(f"Failed to initialize: {e}")
            return False
        
        # Initial status
        self.write_status("monitoring", {"started_at": datetime.now().isoformat()})
        
        check_num = 0
        start_time = datetime.now()
        
        try:
            while True:
                # Check if cancel was requested via Telegram button
                if self.telegram_cancel_event.is_set():
                    logger.info("[TELEGRAM] Cancel requested - stopping watch")
                    self.write_status("stopped", {"reason": "Cancelled via Telegram"})
                    return False
                
                if max_checks and check_num >= max_checks:
                    logger.info(f"Max checks ({max_checks}) reached. Stopping.")
                    self.write_status("stopped", {"reason": "Max checks reached"})
                    return False
                
                check_num += 1
                
                # Check product status
                is_available = await self.check_product_status()
                
                if is_available:
                    elapsed = datetime.now() - start_time
                    logger.info(f"[SUCCESS] Product became available after {elapsed} ({check_num} checks)")
                    
                    # Check if product is in stock before attempting purchase
                    is_out_of_stock = await self.check_stock_status(self.order.page)
                    
                    if is_out_of_stock:
                        logger.warning("[OUT OF STOCK] Product is out of stock!")
                        if self.continue_on_out_of_stock:
                            logger.info("[CONTINUE MODE] Product out of stock but continuing to monitor...")
                            self.write_status("out_of_stock_monitoring", {
                                "message": "Product went out of stock but continuing to monitor",
                                "product_name": self.expected_product_name,
                                "timestamp": datetime.now().isoformat(),
                                "checks_so_far": check_num
                            })
                            # Wait before next check
                            logger.info(f"Waiting {self.check_interval} seconds before next check...")
                            await asyncio.sleep(self.check_interval)
                            continue  # Skip auto-purchase and go to next check
                        else:
                            logger.error("[ABORT] Stopping due to product going out of stock")
                            self.write_status("out_of_stock", {
                                "message": "Product went out of stock",
                                "product_nam e": self.expected_product_name,
                                "timestamp": datetime.now().isoformat()
                            })
                            return False
                    
                    # Product is in stock - proceed with auto-purchase
                    logger.info("Starting auto-purchase...")
                    success = await self.auto_purchase()
                    
                    if success:
                        logger.info("[COMPLETE] Purchase completed!")
                        self.write_status("purchased", {
                            "completed_at": datetime.now().isoformat(),
                            "total_checks": check_num
                        })
                        return True
                    else:
                        logger.warning("Auto-purchase failed. Manual intervention needed.")
                        self.write_status("available", {
                            "message": "Product available but auto-purchase failed",
                            "action": "Manual purchase needed"
                        })
                        return False
                
                # Wait before next check
                logger.info(f"Waiting {self.check_interval} seconds before next check...")
                await asyncio.sleep(self.check_interval)
                
        except KeyboardInterrupt:
            logger.info("\n[STOPPED] Watcher stopped by user")
            elapsed = datetime.now() - start_time
            self.write_status("stopped", {
                "reason": "User interrupted",
                "checks_performed": check_num,
                "duration": str(elapsed)
            })
            return False
        
        finally:
            # Stop Telegram polling if running
            if self.telegram_bot and self.telegram_bot.is_polling:
                await self.telegram_bot.stop_polling()
            
            if self.auth:
                try:
                    if hasattr(self.auth, 'close'):
                        await self.auth.close()
                    logger.info("Browser closed")
                except Exception as e:
                    logger.debug(f"Browser close error: {e}")

    async def auto_purchase(self):
        """Automatically add to cart and proceed to checkout"""
        try:
            logger.info("Step 1: Verifying product details before adding to cart...")
            
            # Check if telegram bot is configured
            if self.telegram_bot:
                logger.info(f"[INFO] Telegram bot is configured and ready")
            else:
                logger.info(f"[INFO] Telegram bot is NOT configured")
            
            # product name from page will be our expected value
            raw_name = await self.get_product_title(self.order.page)
            product_name = normalize_product_name(raw_name)
            logger.info(f"[VERIFY] Product on screen: {colorize_product(product_name)}")
            # store it so other methods can reference if needed
            self.expected_product_name = product_name
            
            logger.info("Step 2: Adding product to cart...")
            
            # Make sure we're clicking the right ADD button for this product
            add_selectors = ["text=ADD", "text=Add", ".add-to-cart", "button.add", "button:has-text('Add')"]
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
            # Navigate to cart or open cart drawer
            if await self.order.page.is_visible("text=My Cart"):
                await self.order.page.click("text=My Cart")
                await asyncio.sleep(2)
                logger.info("[OK] Cart opened")
            
            # Clean up any previous items that don't match the product title
            logger.info("Step 3a: Cleaning cart of mismatched items...")
            removed = await self.purge_wrong_cart_items(product_name)
            logger.info(f"[CLEANUP] Cart cleanup completed. Removed items: {removed if removed else 'None'}")
            if removed:
                    logger.info(f"[CLEANUP] Removed {len(removed)} mismatched item(s) from cart: {removed}")
                # give the cart a moment to settle and then re-open if necessary
                    await asyncio.sleep(1)
            if await self.order.page.is_visible("text=My Cart"):
                    await self.order.page.click("text=My Cart")
                    await asyncio.sleep(1)

            logger.info("Step 3b: Verifying product in cart...")
            # Extract product name from cart and verify it matches expected product
            cart_product_name_raw = await self.get_cart_product_name(self.order.page)
            cart_product_name = normalize_product_name(cart_product_name_raw)
            logger.info(f"[CART] Product in cart: {colorize_product(cart_product_name)}")
            
            if removed:
                    logger.info(f"[CLEANUP] Removed {len(removed)} mismatched item(s) from cart: {removed}")
                # give the cart a moment to settle and then re-open if necessary
                    await asyncio.sleep(1)
            # verify cart item equals page product name
            if cart_product_name != "Unknown":
                if cart_product_name.strip().lower() != product_name.strip().lower():
                    logger.warning(f"[MISMATCH] Cart product '{cart_product_name}' does not equal page product '{product_name}'")
                    
                    # Try cleanup again
                    removed = await self.purge_wrong_cart_items(product_name)
                    logger.info(f"[CLEANUP] Post-mismatch cleanup completed. Removed items: {removed}")
                    
                    # Ensure cart is open for re-verification
                    if not await self.order.page.is_visible("text=My Cart"):
                        await self.order.page.click("text=My Cart")
                        await asyncio.sleep(2)
                    
                    # Re-check after cleanup
                    cart_product_name_raw = await self.get_cart_product_name(self.order.page)
                    cart_product_name = normalize_product_name(cart_product_name_raw)
                    logger.info(f"[CART] Product in cart after cleanup: {colorize_product(cart_product_name)}")
                    
                    if cart_product_name.strip().lower() != product_name.strip().lower():
                        logger.warning("Auto-purchase failed. Manual intervention needed.")
                        self.write_status("available", {"message": "Product added but cart contains wrong item after cleanup"})
                        return False
                else:
                    logger.info("[OK] Cart product matches page product")
            
            # Send Telegram notification only after product is verified to be correct
            if self.telegram_bot:
                logger.info("Step 3b: Sending Telegram notification with action buttons...")
                product_name = self.expected_product_name or cart_product_name or "Unknown Product"
                
                try:
                    telegram_success = await self.telegram_bot.send_product_notification(
                        product_name=product_name,
                        product_url=self.product_url,
                        location_name=self.location_label,
                        with_buttons=True
                    )
                    
                    if telegram_success:
                        logger.info("[OK] Telegram notification with buttons sent successfully")
                        logger.info("[INFO] User can now click 'Retry' button to restart the watch process")
                    else:
                        logger.warning("[WARN] Telegram notification failed to send")
                except Exception as e:
                    logger.error(f"[ERROR] Telegram notification error: {e}")
            
            logger.info("[SUCCESS] Product successfully added to cart!")
            print("\n" + "=" * 70)
            print("✓ PRODUCT ADDED TO CART")
            print("=" * 70)
            print(f"Product: {colorize_product(cart_product_name)}")
            print("=" * 70)
            print("\033[92m" + banner + "\033[0m")  # green
            # Check if user wants to automate checkout
            if not self.automate_checkout:
                logger.info("[USER] Automate checkout disabled - waiting for manual completion or Telegram callback")
                print("\nManually complete the checkout at your convenience.")
                print("Awaiting Telegram callback (Retry/Cancel) or manual completion...")
                print("=" * 70 + "\n")
                
                self.write_status("added_to_cart", {
                    "message": "Product successfully added to cart",
                    "product_name": cart_product_name,
                    "added_at": datetime.now().isoformat()
                })
                
                # Wait for Telegram callbacks (retry or cancel)
                # Check every 5 seconds for Telegram events
                max_wait_time = 600  # 10 minutes max wait
                elapsed = 0
                
                while elapsed < max_wait_time:
                    # Check if user clicked Retry button
                    if self.telegram_retry_event.is_set():
                        logger.info("[TELEGRAM] Retry button clicked - restarting watch")
                        return False
                    
                    # Check if user clicked Cancel button
                    if self.telegram_cancel_event.is_set():
                        logger.info("[TELEGRAM] Cancel button clicked - stopping watch")
                        return False
                    
                    await asyncio.sleep(5)
                    elapsed += 5
                
                logger.info("[INFO] Max wait time reached - assuming manual payment completion")
                return True
            
            logger.info("[USER] Proceeding with automated checkout steps")
            
            logger.info("Step 4: Clicking on cart button to open cart drawer...")
            # Click on the cart button with class CartButton__Container-sc-1fuy2nj-3 eOczDn
            cart_button_selectors = [
                ".CartButton__Container-sc-1fuy2nj-3",
                "[class*='CartButton__Container']",
                "button[class*='CartButton']"
            ]
            
            cart_button_clicked = False
            for sel in cart_button_selectors:
                try:
                    if await self.order.page.is_visible(sel):
                        await self.order.page.click(sel)
                        await asyncio.sleep(2)
                        logger.info(f"[OK] Clicked cart button: {sel}")
                        cart_button_clicked = True
                        break
                except Exception as e:
                    logger.debug(f"Cart button selector {sel} failed: {e}")
                    continue
            
            if not cart_button_clicked:
                logger.warning("Could not click cart button")
            
            # Check for Telegram retry/cancel interrupts
            if self.telegram_cancel_event.is_set():
                logger.info("[TELEGRAM] Cancel requested - stopping checkout")
                return False
            if self.telegram_retry_event.is_set():
                logger.info("[TELEGRAM] Retry requested - aborting checkout")
                return False
            
            logger.info("Step 5: Clicking Proceed to pay button to go to checkout...")
            
            # Scroll down to ensure the Proceed to pay button is visible
            try:
                await self.order.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1)
                logger.info("[OK] Scrolled to bottom of page")
            except Exception as e:
                logger.debug(f"Scroll failed: {e}")
            
            # Click "Proceed to pay" button which redirects to checkout
            # Try various selectors for the button
            proceed_to_pay_selectors = [
                "button:has-text('Proceed to Pay')",
            ]

            proceed_clicked = False
            for sel in proceed_to_pay_selectors:
                try:
                    if await self.order.page.is_visible(sel):
                        # Scroll the element into view before clicking
                        await self.order.page.locator(sel).first.scroll_into_view_if_needed()
                        await asyncio.sleep(0.5)
                        await self.order.page.click(sel)
                        await asyncio.sleep(3)
                        logger.info(f"[OK] Clicked: {sel}")
                        proceed_clicked = True
                        break
                except Exception as e:
                    logger.debug(f"Selector {sel} failed: {e}")
                    continue

            if not proceed_clicked:
                logger.warning("Could not click Proceed to pay button - trying to find all buttons on page")
                # Try to find any button with payment-related text
                try:
                    all_buttons = await self.order.page.query_selector_all("button")
                    logger.info(f"[DEBUG] Found {len(all_buttons)} buttons on page")
                    for i, btn in enumerate(all_buttons):
                        btn_text = await btn.text_content()
                        logger.info(f"[DEBUG] Button {i}: {btn_text}")
                except Exception as e:
                    logger.debug(f"Could not enumerate buttons: {e}")

            # Wait for redirect and ensure we're on checkout page
            await asyncio.sleep(2)
            current_url = self.order.page.url
            logger.info(f"Current page URL: {current_url}")
            
            # Check for Telegram retry/cancel interrupts before payment
            if self.telegram_cancel_event.is_set():
                logger.info("[TELEGRAM] Cancel requested - stopping before payment")
                return False
            if self.telegram_retry_event.is_set():
                logger.info("[TELEGRAM] Retry requested - aborting checkout before payment")
                return False

            logger.info("Step 6: Selecting Cash payment method...")
            # Try common selectors for Cash / COD payment option
            # Based on HTML: <div role="button" aria-label="Cash" title="Cash">
            cash_selectors = [
                "[aria-label='Cash']",
                "div[role='button'][aria-label='Cash']",
                "[title='Cash']",
                "h5:has-text('Cash')",
                "text=Cash",
                "[class*='cod']",
                "[class*='cash']"
            ]

            cash_clicked = False
            for sel in cash_selectors:
                try:
                    if await self.order.page.is_visible(sel):
                        await self.order.page.click(sel)
                        await asyncio.sleep(1)
                        logger.info(f"[OK] Selected payment option: {sel}")
                        cash_clicked = True
                        break
                except Exception as e:
                    logger.debug(f"Cash selector {sel} failed: {e}")
                    continue

            if not cash_clicked:
                logger.warning("Could not automatically select Cash payment option")
            
            # Check for Telegram retry/cancel interrupts before final payment
            if self.telegram_cancel_event.is_set():
                logger.info("[TELEGRAM] Cancel requested - stopping before paying")
                return False
            if self.telegram_retry_event.is_set():
                logger.info("[TELEGRAM] Retry requested - aborting before payment")
                return False

            logger.info("Step 7: Clicking Pay Now button...")
            # Try to click Pay Now / Pay now button
            pay_selectors = [
                "button:has-text('Pay Now')",
                "button:has-text('Pay now')",
                "text=Pay Now",
                "text=Pay now",
                "button:has-text('Place Order')",
                "text=Place Order"
            ]

            pay_clicked = False
            for sel in pay_selectors:
                try:
                    if await self.order.page.is_visible(sel):
                        await self.order.page.click(sel)
                        await asyncio.sleep(2)
                        logger.info(f"[OK] Clicked payment button: {sel}")
                        pay_clicked = True
                        break
                except Exception:
                    continue

            if not pay_clicked:
                logger.warning("Pay button not found — please complete payment manually on the checkout page")
                # Give user some time to complete manual payment, but check for Telegram interrupts every 5 seconds
                logger.info("Waiting 120 seconds for you to complete payment manually...")
                for wait_iteration in range(24):  # 24 * 5 = 120 seconds
                    # Check for Telegram retry/cancel before continuing wait
                    if self.telegram_cancel_event.is_set():
                        logger.info("[TELEGRAM] Cancel requested - stopping during manual payment wait")
                        return False
                    if self.telegram_retry_event.is_set():
                        logger.info("[TELEGRAM] Retry requested - aborting manual payment wait")
                        return False
                    
                    await asyncio.sleep(5)  # Wait 5 seconds before next check
            else:
                logger.info("Payment button clicked; waiting briefly for confirmation...")
                await asyncio.sleep(5)

            logger.info("[SUCCESS] Checkout steps attempted/completed")
            return True
            
        except Exception as e:
            logger.error(f"Auto-purchase error: {e}")
            return False



async def main():
    """Main entry point"""
    
    # Ask user for product URL and location
    print("\n" + "=" * 70)
    print("BLINKIT PRODUCT WATCHER")
    print("=" * 70)
    print("\nThis script will:")
    print("1. Monitor a product URL for availability in your SPECIFIC LOCATION")
    print("2. Wait if it's 'Coming Soon'")
    print("3. Auto-purchase when available")
    print("\nExample URL: https://blinkit.com/prn/x/prid/746548")
    print("-" * 70)
    
    product_url = input("\nEnter product URL: ").strip()
    
    if not product_url.startswith("http"):
        logger.error("Invalid URL. Must start with http")
        return
    
    if "blinkit.com" not in product_url:
        logger.error("Invalid URL. Must be a Blinkit product URL")
        return
    
    # Use site UI to select saved address instead of asking for coordinates
    print("\nUsing site UI to select a saved address via the site UI. No latitude/longitude input required.")

    # Ask for the saved-address label to select (default: Home)
    location_label = input("\nEnter saved address label to select (default 'Home'): ").strip() or "Home"

    # Ask for check interval
    try:
        check_interval = int(input("\nEnter check interval in seconds (default 5): ").strip() or "5")
    except ValueError:
        check_interval = 5

    # Ask if user wants to keep monitoring even if product goes out of stock
    continue_on_oos = input("\nContinue refreshing if product goes out of stock? (y/N): ").strip().lower() in ('y', 'yes') or "y"

    # Ask if user wants to automate checkout steps (ask once, applies to all retries)
    automate_checkout = input("\nAutomate checkout steps (Proceed to Pay, Select Payment, Pay Now)? (y/N): ").strip().lower() in ('y', 'yes') or "n"

    # Load Telegram credentials from environment variables
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_channel_id = os.getenv("TELEGRAM_CHANNEL_ID")

    logger.info(f"Product URL: {product_url}")
    logger.info(f"Location: using site-saved address ('{location_label}') via UI")
    logger.info(f"Check interval: {check_interval} seconds")
    logger.info(f"Continue on out-of-stock: {'YES - will keep refreshing' if continue_on_oos else 'NO - will stop'}")
    logger.info(f"Automate checkout: {'YES - will auto proceed through checkout' if automate_checkout else 'NO - will stop after adding to cart'}")
    if telegram_bot_token and telegram_channel_id:
        logger.info(f"Telegram notifications: ENABLED (Channel: {telegram_channel_id})")
    else:
        logger.info("Telegram notifications: DISABLED (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID in .env)")

    # Run watcher in a loop to support retry via Telegram
    retry_count = 0
    while True:
        logger.info(f"\n{'='*70}")
        if retry_count > 0:
            logger.info(f"RETRY #{retry_count} - Starting new watch cycle with same parameters")
        logger.info(f"{'='*70}\n")
        
        # Start watching (no coordinates provided — watcher will try to select given saved address)
        watcher = ProductWatcher(
            product_url, 
            None, 
            None, 
            check_interval, 
            location_label, 
            continue_on_oos,
            telegram_bot_token=telegram_bot_token,
            telegram_channel_id=telegram_channel_id,
            automate_checkout=automate_checkout
        )
        success = await watcher.watch(max_checks=None)  # Infinite checks
        
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
                watcher.telegram_retry_event.clear()  # Clear the event for next cycle
                await asyncio.sleep(2)  # Brief pause before restart
                continue
            
            # Check if Telegram Cancel button was clicked
        if watcher.telegram_cancel_event.is_set():
                logger.info("[TELEGRAM] Cancel button clicked - exiting")
                watcher.telegram_cancel_event.clear()
                break
            
            # Otherwise, ask user if they want to retry (terminal fallback)
        retry_choice = input("\nWould you like to retry watching this product? (y/N): ").strip().lower()
        if retry_choice in ('y', 'yes'):
                retry_count += 1
                logger.info(f"Restarting watch cycle (Retry #{retry_count})...")
                await asyncio.sleep(2)  # Brief pause before restart
                continue
        else:
                logger.info("User chose not to retry. Exiting.")
                break


async def _run_quick_test():
    """Minimal demonstration of cart cleanup logic without opening a browser."""
    # build dummy watcher with fake order/page objects
    class DummyPage:
        def __init__(self):
            self.url = ""
        async def evaluate(self, script):
            # ignore script, return simulated cart items
            # first item is mismatched, second is correct
            return [{"id": "wrong123", "name": "Some Other Product"},
                    {"id": "good456", "name": "Expected Product"}]
        async def is_visible(self, selector):
            return False
        async def click(self, selector):
            pass

    class DummyOrder:
        def __init__(self):
            self.page = DummyPage()
        async def remove_from_cart(self, product_id, quantity=1):
            print(f"[dummy] remove_from_cart called for {product_id} x{quantity}")

    # construct watcher and call cleanup
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


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("test", "quick", "auto"):
        # run built-in quick test harness
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