from .base import BaseService

class CartService(BaseService):
    async def add_to_cart(self, product_id: str, quantity: int = 1):
        """Adds a product to the cart by its unique ID. Supports multiple quantities."""
        print(f"Adding product with ID {product_id} to cart (Quantity: {quantity})...")
        try:
            # Target the specific card by ID
            card = self.page.locator(f"div[id='{product_id}']")

            if await card.count() == 0:
                print(f"Product ID {product_id} not found on current page.")

                # Check if we know this product from a previous search
                if self.manager and product_id in self.manager.known_products:
                    print("Product found in history.")
                    product_info = self.manager.known_products[product_id]
                    source_query = product_info.get("source_query")

                    if source_query:
                        print(
                            f"Navigating back to search results for '{source_query}'..."
                        )
                        # Delegate search back to manager/search service
                        if hasattr(self.manager, "search_product"):
                            await self.manager.search_product(source_query)

                        # Re-locate the card after search
                        card = self.page.locator(f"div[id='{product_id}']")
                        if await card.count() == 0:
                            print(
                                f"CRITICAL: Product {product_id} still not found after re-search."
                            )
                            return
                    else:
                        print("No source query found for this product.")
                        return
                else:
                    print("Product ID unknown and not on current page.")
                    return

            # Find the ADD button specifically inside the card
            add_btn = card.locator("div").filter(has_text="ADD").last

            items_to_add = quantity

            # If ADD button is visible, click it once to start
            if await add_btn.is_visible():
                await add_btn.click()
                print(f"Clicked ADD button for {product_id} (1/{quantity}).")
                items_to_add -= 1
                # Wait for the counter to appear
                await self.page.wait_for_timeout(500)

            # Use increment button for remaining quantity
            if items_to_add > 0:
                # Wait for the counter to initialize
                await self.page.wait_for_timeout(1000)

                # Robust strategy to find the + button
                plus_btn = card.locator(".icon-plus").first
                if await plus_btn.count() > 0:
                    plus_btn = plus_btn.locator("..")
                else:
                    plus_btn = card.locator("text='+'").first

                if await plus_btn.is_visible():
                    for i in range(items_to_add):
                        await plus_btn.click()
                        print(
                            f"Incrementing quantity for {product_id} ({quantity - items_to_add + i + 1}/{quantity})."
                        )
                        # Check for limit reached
                        try:
                            limit_msg = self.page.get_by_text(
                                "Sorry, you can't add more of this item"
                            )
                            if await limit_msg.is_visible(timeout=1000):
                                print(f"Quantity limit reached for {product_id}.")
                                break
                        except Exception:
                            pass

                        await self.page.wait_for_timeout(500)
                else:
                    print(
                        f"Could not find '+' button to add remaining quantity for {product_id}."
                    )

            await self.page.wait_for_timeout(1000)

            # Check for "Store Unavailable" modal
            if await self.page.is_visible(
                "div:has-text('Sorry, can\\'t take your order')"
            ):
                print("WARNING: Store is unavailable (Modal detected).")
                return

        except Exception as e:
            print(f"Error adding to cart: {e}")

    async def remove_from_cart(self, product_id: str, quantity: int = 1):
        """Removes a specific quantity of a product from the cart."""
        print(f"Removing {quantity} of product ID {product_id} from cart...")
        try:
            # Target the specific card by ID
            card = self.page.locator(f"div[id='{product_id}']")

            if await card.count() == 0:
                # Attempt recovery via search if known
                if self.manager and product_id in self.manager.known_products:
                    product_info = self.manager.known_products[product_id]
                    source_query = product_info.get("source_query")
                    if source_query:
                        if hasattr(self.manager, "search_product"):
                            await self.manager.search_product(source_query)
                        card = self.page.locator(f"div[id='{product_id}']")
                        if await card.count() == 0:
                            print(
                                f"Product {product_id} not found after recovery search."
                            )
                            return
                else:
                    print(f"Product ID {product_id} not found and unknown.")
                    return

            # Check for decrement button
            minus_btn = card.locator(".icon-minus").first
            if await minus_btn.count() > 0:
                minus_btn = minus_btn.locator("..")
            else:
                minus_btn = card.locator("text='-'").first

            if await minus_btn.is_visible():
                for i in range(quantity):
                    await minus_btn.click()
                    print(
                        f"Decrementing quantity for {product_id} ({i + 1}/{quantity})."
                    )
                    await self.page.wait_for_timeout(500)

                    # If ADD button reappears, item is fully removed
                    if (
                        await card.locator("div")
                        .filter(has_text="ADD")
                        .last.is_visible()
                    ):
                        print(f"Item {product_id} completely removed from cart.")
                        break
            else:
                print(f"Item {product_id} is not in cart (no '-' button found).")

        except Exception as e:
            print(f"Error removing from cart: {e}")

    async def get_cart_items(self):
        """Returns a list of cart items currently visible in the cart drawer.

        Each item is represented as a dict containing at least ``id`` and ``name``
        keys. The method will open the cart if it's not already visible and then
        execute a small script against the DOM to collect information from the
        item containers. In error cases it will return an empty list so that callers
        can safely use ``len()`` without having to check the type.
        """
        items = []
        try:
            cart_btn = self.page.locator(
                "div[class*='CartButton__Button'], div[class*='CartButton__Container']"
            )

            if await cart_btn.count() > 0:
                await cart_btn.first.click()

                # give drawer a moment to appear
                await self.page.wait_for_timeout(2000)

                # quick availability checks; if the store is unavailable treat as empty
                if (
                    await self.page.is_visible("text=Sorry, can't take your order")
                    or await self.page.is_visible("text=Currently unavailable")
                    or await self.page.is_visible("text=High Demand")
                ):
                    return []

                if await self._is_store_closed():
                    return []

                # scrape item entries from the cart drawer
                script = """() => {
                    const results = [];
                    const containers = document.querySelectorAll('[id]');
                    containers.forEach(c => {
                        const id = c.id;
                        if (!id) return;
                        // heuristics: only consider containers that look like cart items
                        if (c.closest('[class*="Cart"]') || String(c.className).includes("DefaultProductCard__Container")) {
                            let nameEl = c.querySelector('[class*="ProductTitle"], .cart-item-name, .product-name, [data-testid="cart-item-name"]');
                            let name = nameEl ? nameEl.innerText.trim() : '';
                            if (name) {
                                results.push({id, name});
                            }
                        }
                    });
                    return results;
                }"""
                parsed = await self.page.evaluate(script)
                if isinstance(parsed, list):
                    items = parsed

            # return list (possibly empty) so callers can use len()
            return items
        except Exception as e:
            print(f"Error getting cart items: {e}")
            return []