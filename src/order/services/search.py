from .base import BaseService


class SearchService(BaseService):
    async def search_product(self, product_name: str):
        """Searches for a product using the search bar."""
        print(f"Searching for item: {product_name}...")
        if self.manager:
            self.manager.current_query = (
                product_name  # Store current query for state tracking
            )

        try:
            # 1. Activate Search
            if await self.page.is_visible("a[href='/s/']"):
                await self.page.click("a[href='/s/']")
            elif await self.page.is_visible(
                "div[class*='SearchBar__PlaceholderContainer']"
            ):
                await self.page.click("div[class*='SearchBar__PlaceholderContainer']")
            else:
                # Fallback: type directly if input is visible, or click generic search text
                if await self.page.is_visible("input[placeholder*='Search']"):
                    await self.page.click("input[placeholder*='Search']")
                else:
                    await self.page.click("text='Search'", timeout=3000)

            # 2. Type and Submit
            search_input = await self.page.wait_for_selector(
                "input[placeholder*='Search'], input[type='text']",
                state="visible",
                timeout=30000,
            )
            await search_input.fill(product_name)
            await self.page.keyboard.press("Enter")

            # 3. Wait for results
            print("Waiting for results...")
            try:
                # Wait for product cards
                await self.page.wait_for_selector(
                    "div[role='button']:has-text('ADD')", timeout=30000
                )
                print("Search results loaded.")
            except Exception:
                print(
                    "Timed out waiting for product cards. Checking for 'No results'..."
                )
                if await self.page.is_visible("text='No results found'"):
                    print("No results found for this query.")
                else:
                    print("Could not detect standard product cards.")

        except Exception as e:
            print(f"Error during search: {e}")

    async def get_search_results(self, limit=20):
        """Parses search results and returns a list of product details including IDs."""
        results = []
        try:
            cards = (
                self.page.locator("div[role='button']")
                .filter(has_text="ADD")
                .filter(has_text="₹")
            )

            count = await cards.count()
            print(f"Found {count} product cards.")

            for i in range(min(count, limit)):
                card = cards.nth(i)
                text_content = await card.inner_text()

                # Extract ID
                product_id = await card.get_attribute("id")
                if not product_id:
                    product_id = "unknown"

                # Extract Name
                name_locator = card.locator("div[class*='line-clamp-2']")
                if await name_locator.count() > 0:
                    name = await name_locator.first.inner_text()
                else:
                    lines = [line for line in text_content.split("\n") if line.strip()]
                    name = lines[0] if lines else "Unknown Product"

                # Store in known products including the source query
                if product_id != "unknown" and self.manager:
                    self.manager.known_products[product_id] = {
                        "source_query": self.manager.current_query,
                        "name": name,
                    }

                # Extract Price
                price = "Unknown Price"
                if "₹" in text_content:
                    for part in text_content.split("\n"):
                        if "₹" in part:
                            price = part.strip()
                            break

                results.append(
                    {"index": i, "id": product_id, "name": name, "price": price}
                )

        except Exception as e:
            print(f"Error extracting search results: {e}")

        return results
