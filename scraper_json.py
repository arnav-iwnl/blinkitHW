#!/usr/bin/env python3
"""
Blinkit Product Web Scraper
- Crawl product URLs from 70000 to 79999
- Extract product name and price
- Filter products containing "hot wheels" keyword
- Export results to Excel
"""

import asyncio
import pandas as pd
import sys
import os
import random
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.auth import BlinkitAuth
from src.order.blinkit_order import BlinkitOrder

# Output file - fixed filename for incremental updates
def get_output_filename(output_format: str = "excel"):
    """Return a Path object for the chosen output format.

    Args:
        output_format: either "excel" or "json". Defaults to excel.
    """
    ext = "xlsx" if output_format == "excel" else "json"
    return Path(f"blinkit_products.{ext}")

class ProductScraper:
    def __init__(self, start_id=746500, end_id=747000, keyword_filter="hot wheels", output_format: str = "json"):
        """
        Initialize the product scraper
        
        Args:
            start_id: Starting product ID (default 70000)
            end_id: Ending product ID (default 79999)
            keyword_filter: Keyword to filter products (e.g., "hot wheels")
            output_format: "excel" or "json" (default json)
        """
        self.start_id = start_id
        self.end_id = end_id
        self.keyword_filter = keyword_filter.lower()
        self.products = []
        self.auth = None
        self.current_id = start_id
        self.output_format = output_format.lower()
        self.output_file = get_output_filename(self.output_format)
        self.base_delay = 0.6
        self.current_delay = self.base_delay
        self.last_request_was_throttled = False
        self.consecutive_throttles = 0

    async def get_product_info(self, page, product_id, verbose=False):
        """Extract product name and price from product page."""
        try:
            url = f"https://blinkit.com/prn/x/prid/{product_id}"
            generic_names = {
                "blinkit.com",
                "blinkit",
                "home | blinkit",
                "blinkit - online grocery shopping",
            }

            def is_generic_name(name):
                if not name:
                    return True
                norm = " ".join(str(name).strip().lower().split())
                return norm in generic_names or norm.startswith("blinkit")

            self.last_request_was_throttled = False
            max_attempts = 4
            response = None
            status = "unknown"

            for attempt in range(1, max_attempts + 1):
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    status = response.status if response else "unknown"
                    current_url = (page.url or "").lower()
                    is_cloudflare_challenge = "__cf_chl_rt_tk=" in current_url

                    if verbose:
                        print(f"  [DEBUG] Attempt {attempt}/{max_attempts} Status: {status}")
                        print(f"  [DEBUG] URL: {page.url}")

                    if status in (429, 403) or is_cloudflare_challenge:
                        self.last_request_was_throttled = True
                        self.consecutive_throttles += 1
                        backoff = min(20.0, (2 ** (attempt - 1)) + (0.5 * self.consecutive_throttles))
                        if verbose:
                            print(f"  [DEBUG] Throttled/challenged. Backing off for {backoff:.1f}s")
                        await asyncio.sleep(backoff)
                        continue

                    if isinstance(status, int) and status >= 400:
                        return None, None, None

                    self.consecutive_throttles = max(0, self.consecutive_throttles - 1)
                    break
                except Exception as e:
                    err = str(e)
                    is_timeout = "Timeout" in err
                    if attempt < max_attempts and is_timeout:
                        self.last_request_was_throttled = True
                        self.consecutive_throttles += 1
                        backoff = min(20.0, (2 ** (attempt - 1)) + (0.5 * self.consecutive_throttles))
                        if verbose:
                            print(f"  [DEBUG] Navigation timeout. Retry after {backoff:.1f}s: {e}")
                        await asyncio.sleep(backoff)
                        continue
                    if verbose:
                        print(f"  [DEBUG] Navigation error: {e}")
                    return None, None, None
            else:
                return None, None, None

            await asyncio.sleep(1.2)

            # Cloudflare challenge pages can still return HTTP 200.
            try:
                page_title = (await page.title() or "").lower()
                body_sample = await page.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText.slice(0, 2000) : ''")
                body_sample_l = (body_sample or "").lower()
                if any(marker in page_title for marker in ["just a moment", "attention required"]) or any(
                    marker in body_sample_l for marker in ["checking your browser", "just a moment", "attention required", "cf-ray"]
                ):
                    self.last_request_was_throttled = True
                    self.consecutive_throttles += 1
                    if verbose:
                        print("  [DEBUG] Cloudflare challenge content detected")
                    return None, None, None
            except Exception:
                pass

            try:
                page_text = await page.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText : ''")
                if not page_text or len(page_text) < 50:
                    if verbose:
                        print(f"  [DEBUG] Page text too short: {len(page_text) if page_text else 0} chars")
                    return None, None, None
            except Exception:
                pass

            product_name = "Unknown"
            price = "N/A"
            image_url = "N/A"

            try:
                selectors = [
                    ".tw-text-500.tw-font-extrabold.tw-line-clamp-50",
                    "h1",
                    ".product-title",
                    ".productName",
                    "[data-testid='product-title']",
                    "[class*='ProductName']",
                ]
                for sel in selectors:
                    text = await page.evaluate(
                        """(selector) => {
                            const el = document.querySelector(selector);
                            return el ? (el.innerText || el.textContent) : null;
                        }""",
                        sel,
                    )
                    if text:
                        candidate = text.strip()
                        if candidate and not is_generic_name(candidate):
                            product_name = candidate
                            if verbose:
                                print(f"  [DEBUG] Got name from selector {sel}: {product_name}")
                            break

                if product_name == "Unknown":
                    meta = await page.evaluate("() => { const m = document.querySelector(\"meta[property='og:title']\"); return m ? m.getAttribute('content') : null; }")
                    if meta and not is_generic_name(meta):
                        product_name = meta.strip()
                        if verbose:
                            print(f"  [DEBUG] Got name from og:title: {product_name}")
            except Exception as e:
                if verbose:
                    print(f"  [DEBUG] Name extraction error: {e}")

            try:
                ld_data = await page.evaluate(
                    """() => {
                        const scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
                        for (const s of scripts) {
                            try {
                                const parsed = JSON.parse(s.textContent || \"null\");
                                const nodes = Array.isArray(parsed) ? parsed : [parsed];
                                for (const node of nodes) {
                                    if (!node || typeof node !== \"object\") continue;
                                    const t = node[\"@type\"];
                                    const types = Array.isArray(t) ? t : [t];
                                    const isProduct = types.filter(Boolean).some(v => String(v).toLowerCase().includes(\"product\"));
                                    if (!isProduct) continue;
                                    const name = node.name || node.title || null;
                                    let ldPrice = null;
                                    const offers = node.offers || null;
                                    if (offers) {
                                        if (Array.isArray(offers) && offers.length > 0) {
                                            ldPrice = offers[0].price || (offers[0].priceSpecification && offers[0].priceSpecification.price) || null;
                                        } else {
                                            ldPrice = offers.price || (offers.priceSpecification && offers.priceSpecification.price) || null;
                                        }
                                    }
                                    const image = Array.isArray(node.image) ? node.image[0] : (node.image || null);
                                    if (name || ldPrice || image) return { name, price: ldPrice, image };
                                }
                            } catch (_) {}
                        }
                        return null;
                    }"""
                )
                if isinstance(ld_data, dict):
                    ld_name = ld_data.get("name")
                    ld_price = ld_data.get("price")
                    ld_image = ld_data.get("image")
                    if (product_name == "Unknown" or is_generic_name(product_name)) and ld_name and not is_generic_name(ld_name):
                        product_name = str(ld_name).strip()
                        if verbose:
                            print(f"  [DEBUG] Got name from JSON-LD: {product_name}")
                    if price == "N/A" and ld_price is not None:
                        price = str(ld_price).strip()
                        if not price.lower().startswith(("rs", "inr", "₹")):
                            price = f"Rs {price}"
                    if image_url == "N/A" and ld_image:
                        image_url = str(ld_image).strip()
            except Exception:
                pass

            try:
                img_src = await page.evaluate(
                    """() => {
                        const container = document.querySelector('[class*="ProductCarousel__ImageContainer"]');
                        if (container) {
                            const img = container.querySelector('img');
                            if (img && img.src) return img.src;
                        }
                        const og = document.querySelector("meta[property='og:image']");
                        return og ? og.getAttribute("content") : null;
                    }"""
                )
                if img_src:
                    image_url = img_src.strip()
            except Exception as e:
                if verbose:
                    print(f"  [DEBUG] Image extraction error: {e}")

            try:
                if price == "N/A":
                    price_selectors = [
                        ".product-price",
                        "[class*='Price']",
                        "[class*='price']",
                        "[data-testid='product-price']",
                    ]
                    for sel in price_selectors:
                        price_text = await page.evaluate(
                            """(selector) => {
                                const el = document.querySelector(selector);
                                return el ? (el.innerText || el.textContent) : null;
                            }""",
                            sel,
                        )
                        if price_text:
                            price = price_text.strip()
                            break

                if price == "N/A":
                    script = "() => { const text = document.documentElement.innerText; const match = text.match(/(?:\\u20B9|Rs\\.?|INR)[\\s]*([0-9]+(?:,[0-9]{3})*(?:\\.[0-9]+)?)/i); return match ? match[0] : null; }"
                    price_match = await page.evaluate(script)
                    if price_match:
                        price = price_match.strip()
            except Exception as e:
                if verbose:
                    print(f"  [DEBUG] Price extraction error: {e}")

            if product_name and product_name != "Unknown" and not is_generic_name(product_name):
                return product_name, price, image_url

            if verbose:
                print(f"  [DEBUG] No valid product name found (final: {product_name})")
            return None, None, None

        except Exception as e:
            if verbose:
                print(f"  [DEBUG] Unexpected error: {e}")
            return None, None, None

    async def scrape_products(self, headless=True, verbose=False):
        """
        Scrape products from the specified range
        
        Args:
            headless: Whether to run browser in headless mode
            verbose: Whether to show debug logs for each product
        """
        print("\n" + "=" * 70)
        print("BLINKIT PRODUCT SCRAPER")
        print("=" * 70)
        print(f"Scraping product IDs from {self.start_id} to {self.end_id}")
        print(f"Filtering for keyword: '{self.keyword_filter}'")
        print(f"Output file: {self.output_file}")
        print(f"Debug mode: {'ON' if verbose else 'OFF'}")
        print("-" * 70)
        
        try:
            print("Initializing browser...")
            self.auth = BlinkitAuth(headless=headless)
            await self.auth.start_browser()
            
            if not await self.auth.is_logged_in():
                print("[ERROR] Not logged in!")
                await self.auth.close()
                return False
            
            print("[OK] Logged in successfully\n")
            
            total_products = self.end_id - self.start_id + 1
            scraped_count = 0
            filtered_count = 0
            
            for product_id in range(self.start_id, self.end_id + 1):
                self.current_id = product_id
                progress = ((product_id - self.start_id + 1) / total_products) * 100
                
                # Enable verbose for first few products even if not requested
                is_verbose = verbose or (scraped_count + filtered_count < 3)
                
                product_name, price, image_url = await self.get_product_info(self.auth.page, product_id, verbose=is_verbose)
                
                if product_name:
                    scraped_count += 1
                    
                    # Check if product matches keyword filter
                    if self.keyword_filter in product_name.lower():
                        self.products.append({
                            "Product ID": product_id,
                            "Image URL": image_url,
                            "Product Name": product_name,
                            "Price": price
                        })
                        filtered_count += 1
                        print(f"[{progress:.1f}%] [MATCH] PID:{product_id} | {product_name} | {price}")
                    else:
                        print(f"[{progress:.1f}%] [FOUND] PID:{product_id} | {product_name} | {price} (no keyword match)")
                else:
                    if self.last_request_was_throttled:
                        print(f"[{progress:.1f}%] [THROTTLED] PID:{product_id} (rate-limited/challenged)")
                    else:
                        print(f"[{progress:.1f}%] [NOT_FOUND] PID:{product_id} (product not available or error)")
                
                # Adaptive delay to reduce Cloudflare/rate-limit blocks.
                if self.last_request_was_throttled:
                    self.current_delay = min(3.0, self.current_delay + 0.5)
                else:
                    self.current_delay = max(self.base_delay, self.current_delay - 0.05)
                await asyncio.sleep(self.current_delay + random.uniform(0.0, 0.35))
            
            print("\n" + "-" * 70)
            print(f"[COMPLETE] Scraping finished")
            print(f"Total products scraped: {scraped_count}")
            print(f"Products with '{self.keyword_filter}': {filtered_count}")
            print("-" * 70)
            
            # Export results
            if self.products:
                if self.output_format == "json":
                    self.export_to_json()
                else:
                    self.export_to_excel()
                print(f"\n[SUCCESS] Results exported to {self.output_file}")
            else:
                print(f"\n[INFO] No products found matching '{self.keyword_filter}'")
            
            return True
            
        except KeyboardInterrupt:
            print("\n[STOPPED] Scraping interrupted by user")
            print(f"Scraped {filtered_count} filtered products so far")
            if self.products:
                self.export_to_excel()
            return False
        
        finally:
            if self.auth:
                try:
                    if hasattr(self.auth, 'close'):
                        await self.auth.close()
                    print("[OK] Browser closed")
                except Exception as e:
                    print(f"[DEBUG] Browser close error: {e}")

    def export_to_excel(self):
        """Export scraped products to Excel file, merging with existing data"""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
            
            # Prepare new data
            df_new = pd.DataFrame(self.products)
            df_new['Product ID'] = df_new['Product ID'].astype(int)
            
            # Reorder columns
            column_order = ['Product ID', 'Product Name', 'Price', 'Image URL']
            df_new = df_new[column_order].set_index('Product ID')
            
            # Load existing data if file exists
            df_merged = None
            if self.output_file.exists():
                try:
                    # Read existing file defensively
                    df_existing = pd.read_excel(self.output_file, dtype=object)

                    # Ensure required columns exist
                    for col in ['Product ID', 'Product Name', 'Price', 'Image URL']:
                        if col not in df_existing.columns:
                            df_existing[col] = pd.NA

                    # Coerce Product ID to numeric, drop invalid rows
                    df_existing['Product ID'] = pd.to_numeric(df_existing['Product ID'], errors='coerce')
                    df_existing = df_existing.dropna(subset=['Product ID'])
                    df_existing['Product ID'] = df_existing['Product ID'].astype(int)
                    df_existing = df_existing.set_index('Product ID')

                    # Merge: new data overwrites old for existing IDs, keep old for IDs not in new data
                    df_merged = df_existing.copy()
                    df_merged.update(df_new)

                    # Add rows that are present in df_new but missing in df_merged
                    new_idx = df_new.index.difference(df_merged.index)
                    if len(new_idx) > 0:
                        df_merged = pd.concat([df_merged, df_new.loc[new_idx]])

                    df_merged = df_merged.sort_index()

                    print(f"[INFO] Merged with {len(df_existing)} existing products")
                    print(f"[INFO] {len(df_new)} new/updated products processed")
                except Exception as e:
                    print(f"[INFO] Could not read existing file, creating new: {e}")
                    df_merged = df_new
            else:
                df_merged = df_new
            
            # Reset index to convert Product ID back to column
            df_merged = df_merged.reset_index()
            
            # Create workbook and worksheet
            wb = Workbook()
            ws = wb.active
            ws.title = 'Products'
            
            # Write headers
            for col_num, col_name in enumerate(column_order, 1):
                cell = ws.cell(row=1, column=col_num)
                cell.value = col_name
            
            # Write data and add hyperlinks to Product IDs
            for row_num, row_data in enumerate(df_merged.values, 2):
                for col_num, value in enumerate(row_data, 1):
                    cell = ws.cell(row=row_num, column=col_num)
                    
                    # Add hyperlink for Product ID column
                    if col_num == 1:  # Product ID column
                        product_id = int(value)
                        product_url = f"https://blinkit.com/prn/x/prid/{product_id}"
                        cell.value = product_id
                        cell.hyperlink = product_url
                        cell.font = Font(color="0563C1", underline="single")
                    else:
                        cell.value = value
            
            # Auto-adjust column widths
            for column in ws.columns:
                max_length = 0
                column_letter = column[0].column_letter
                
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                
                adjusted_width = min(max_length + 2, 50)
                ws.column_dimensions[column_letter].width = adjusted_width
            
            # Format Product ID column as number (no thousand separators)
            for row in ws.iter_rows(min_col=1, max_col=1, min_row=2, max_row=len(df_merged) + 1):
                for cell in row:
                    cell.number_format = '0'
            
            wb.save(str(self.output_file))
            print(f"[INFO] Total products in file: {len(df_merged)}")
            
            return True
        
        except PermissionError as e:
            print(f"[ERROR] Permission denied: {e}")
            print(f"[INFO] Close the Excel file if it's open, or check file permissions")
            return False
        except Exception as e:
            print(f"[ERROR] Failed to export to Excel: {e}")
            return False

    def export_to_json(self):
        """Export scraped products to JSON format, merging with existing data if present."""
        try:
            # Convert list of product dicts to keyed structure by name
            new_data = {}
            for prod in self.products:
                name = prod.get("Product Name") or f"{prod.get('Product ID')}"
                new_data[name] = {
                    "Price": prod.get("Price"),
                    "Url": f"https://blinkit.com/prn/x/prid/{prod.get('Product ID')}",
                    "Id": str(prod.get("Product ID"))
                }

            if self.output_file.exists():
                try:
                    existing_json = pd.read_json(self.output_file, typ='series')
                    existing_data = existing_json.to_dict()
                except Exception:
                    existing_data = {}
            else:
                existing_data = {}

            # merge (new data overrides old entries with same name)
            merged = {**existing_data, **new_data}

            # Write out to file
            with open(self.output_file, 'w', encoding='utf-8') as f:
                import json
                json.dump(merged, f, ensure_ascii=False, indent=4)

            print(f"[INFO] Total products in file: {len(merged)}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to export to JSON: {e}")
            return False


async def main():
    """Main entry point"""
    print("\n" + "=" * 70)
    print("BLINKIT PRODUCT SCRAPER SETUP")
    print("=" * 70)
    
    # Get user input for product ID range
    try:
        start_input = input("\nEnter starting product ID (default 746500): ").strip()
        start_id = int(start_input) if start_input else 746500
    except ValueError:
        start_id = 746500
    
    try:
        end_input = input("Enter ending product ID (default 747000): ").strip()
        end_id = int(end_input) if end_input else 747000
    except ValueError:
        end_id = 747000
    
    keyword = input("Enter keyword to filter (default 'hot wheels'): ").strip()
    keyword = keyword if keyword else "hot wheels"
    
    headless_input = input("Run browser in headless mode? (y/N): ").strip().lower()
    headless = headless_input in ('y', 'yes')
    
    debug_input = input("Enable debug/verbose mode? (y/N): ").strip().lower()
    debug = debug_input in ('y', 'yes')
    
    # output format selection
    fmt_input = input("Output format? (excel/json, default excel): ").strip().lower()
    output_format = fmt_input if fmt_input in ('excel', 'json') else 'excel'
    
    print(f"\n[INFO] Starting product ID: {start_id}")
    print(f"[INFO] Ending product ID: {end_id}")
    print(f"[INFO] Keyword filter: '{keyword}'")
    print(f"[INFO] Headless mode: {headless}")
    print(f"[INFO] Debug mode: {debug}")
    print(f"[INFO] Output format: {output_format}")
    
    # Validate range
    if start_id >= end_id:
        print("[ERROR] Starting ID must be less than ending ID")
        return
    
    if end_id - start_id > 10000:
        confirm = input(f"\n[WARNING] This will scrape {end_id - start_id + 1} products. Continue? (y/N): ").strip().lower()
        if confirm not in ('y', 'yes'):
            print("[CANCELLED] Scraping cancelled")
            return
    
    # Start scraping
    scraper = ProductScraper(start_id, end_id, keyword, output_format=output_format)
    success = await scraper.scrape_products(headless=headless, verbose=debug)
    
    if success:
        print("\n[SUCCESS] Scraping completed successfully!")
    else:
        print("\n[INFO] Scraping stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[ERROR] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
