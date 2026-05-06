from .base import BaseService
import logging
import base64
import io
from PIL import Image
from urllib.parse import urlparse, parse_qs
from pyzbar.pyzbar import decode as pyzbar_decode
logger = logging.getLogger(__name__)

class CheckoutService(BaseService):
    async def place_order(self):
        """Proceeds to checkout."""

        if await self._is_store_closed():
            return "CRITICAL: Store is closed."

        try:
            proceed_btn = (
                self.page.locator("button, div").filter(has_text="Proceed").last
            )

            # If Proceed not visible, try opening the cart first
            if not await proceed_btn.is_visible():
                print("Proceed button not visible. Attempting to open Cart drawer...")
                cart_btn = self.page.locator(
                    "div[class*='CartButton__Button'], div[class*='CartButton__Container']"
                )
                if await cart_btn.count() > 0:
                    await cart_btn.first.click()
                    print("Clicked 'My Cart' button.")
                    await self.page.wait_for_timeout(2000)
                else:
                    print("Could not find 'My Cart' button.")

            # Try clicking Proceed again
            if await proceed_btn.is_visible():
                await proceed_btn.click()
                print(
                    "Cart checkout successfully.\nYou can select the payment method and proceed to pay."
                )
                await self.page.wait_for_timeout(3000)
            else:
                print(
                    "Proceed button not visible. Cart might be empty or Store Unavailable."
                )

        except Exception as e:
            print(f"Error placing order: {e}")

    # async def select_payment_method(self):
    #     """Checks for Cash availability, selects it if available, else falls back to UPI QR."""
    #     print("Selecting payment method (Cash or UPI QR)...")
    #     try:
    #         iframe_element = await self.page.wait_for_selector(
    #             "#payment_widget", timeout=30000
    #         )
    #         if not iframe_element:
    #             print("Payment widget iframe not found.")
    #             return "Payment widget not found."

    #         frame = await iframe_element.content_frame()
    #         if not frame:
    #             return "Payment widget frame content not found."

    #         await frame.wait_for_load_state("networkidle")

    #         # Check Cash
    #         cash_panel = frame.locator("div[title='Cash']")
    #         if await cash_panel.count() > 0:
    #             # Check if it has a disabled attribute on the panel itself
    #             is_disabled_attr = (
    #                 await cash_panel.first.get_attribute("disabled") is not None
    #             )

    #             # Check aria-disabled on the inner button
    #             cash_button = cash_panel.locator("div[role='button']")
    #             is_aria_disabled = False
    #             if await cash_button.count() > 0:
    #                 is_aria_disabled = (
    #                     await cash_button.first.get_attribute("aria-disabled") == "true"
    #                 )

    #             if not is_disabled_attr and not is_aria_disabled:
    #                 print("Cash is available. Selecting Cash on Delivery...")
    #                 if await cash_button.count() > 0:
    #                     await cash_button.first.click()
    #                 else:
    #                     await cash_panel.first.click()
    #                 return "Selected Cash on Delivery. Call pay_now to finalize."
    #             else:
    #                 print("Cash is unavailable or disabled.")
    #         else:
    #             print("Cash option not found.")

    #         # If Cash is disabled or doesn't exist, try UPI -> Generate QR
    #         print("Selecting UPI and generating QR code...")
    #         upi_panel = frame.locator("div[title='UPI']")
    #         if await upi_panel.count() > 0:
    #             upi_button = upi_panel.locator("div[role='button']")
    #             if await upi_button.count() > 0:
    #                 # Check if already open
    #                 is_open = (
    #                     await upi_button.first.get_attribute("aria-expanded") == "true"
    #                 )
    #                 if not is_open:
    #                     await upi_button.first.click()
    #                     print("Clicked UPI.")
    #             else:
    #                 await upi_panel.first.click()

    #             await self.page.wait_for_timeout(1000)

    #             # Click Generate QR
    #             generate_qr_btn = frame.locator("button:has-text('Generate QR')")
    #             if await generate_qr_btn.count() > 0:
    #                 await generate_qr_btn.first.click()
    #                 print("Generated QR code. Please show the QR code to the customer.")
    #                 await self.page.wait_for_timeout(2000)  # Wait for QR to load

    #                 try:
    #                     qr_img_locator = frame.locator(
    #                         "div[class*='QrImageWrapper'] img"
    #                     )
    #                     await qr_img_locator.wait_for(state="visible", timeout=5000)
    #                     qr_src = await qr_img_locator.first.get_attribute("src")
    #                     if qr_src and qr_src.startswith("data:image/"):
    #                         base64_data = qr_src.split(",")[1]
    #                         return {
    #                             "status": "UPI QR Code generated successfully. Show it to the customer.",
    #                             "qr_base64": base64_data,
    #                             "format": qr_src.split(";")[0].split("/")[1],
    #                         }
    #                 except Exception as qr_e:
    #                     print(f"Failed to extract QR Code image: {qr_e}")

    #                 return (
    #                     "UPI QR Code generated successfully. Show it to the customer."
    #                 )
    #             else:
    #                 print("Generate QR button not found within UPI.")
    #                 return "UPI section opened but 'Generate QR' button not found."
    #         else:
    #             print("UPI option not found.")
    #             return "UPI option not found in payment widget."

    #     except Exception as e:
    #         print(f"Error selecting payment method: {e}")
    #         return f"Error: {str(e)}"
    
    async def click_pay_now(self):
        """Clicks the final Pay Now button."""
        try:
            # Strategy 1: Specific class partial match
            pay_btn_specific = self.page.locator(
                "div[class*='Zpayments__Button']:has-text('Pay Now')"
            )
            if (
                await pay_btn_specific.count() > 0
                and await pay_btn_specific.first.is_visible()
            ):
                await pay_btn_specific.first.click()
                print("Clicked 'Pay Now'. Please approve the payment on your UPI app.")
                return "Clicked Pay Now."

            # Strategy 2: Text match on page
            pay_btn_text = (
                self.page.locator("div, button").filter(has_text="Pay Now").last
            )
            if await pay_btn_text.count() > 0 and await pay_btn_text.is_visible():
                await pay_btn_text.click()
                print("Clicked 'Pay Now'.")
                return "Clicked Pay Now."

            # Strategy 3: Check inside iframe
            iframe_element = await self.page.query_selector("#payment_widget")
            if iframe_element:
                frame = await iframe_element.content_frame()
                if frame:
                    frame_btn = frame.locator("text='Pay Now', text='Place Order'")
                    if await frame_btn.count() > 0:
                        await frame_btn.first.click()
                        print("Clicked payment button inside iframe.")
                        return "Clicked payment button inside iframe."

            print("Could not find 'Pay Now' button (timeout or not in DOM).")
            return "Could not find Pay Now button."

        except Exception as e:
            print(f"Error clicking Pay Now: {e}")
            return f"Error: {str(e)}"
        
    async def select_cash_payment(self):
     """Select Cash on Delivery inside the payment iframe."""
     logger.info("Attempting to select Cash on Delivery...")
     try:
        iframe_element = await self.page.wait_for_selector("#payment_widget", timeout=30000)
        if not iframe_element:
            return "ERROR: Payment widget iframe not found."

        frame = await iframe_element.content_frame()
        if not frame:
            return "ERROR: Could not access payment iframe content."

        await frame.wait_for_load_state("networkidle")

        cash_panel = frame.locator("div[title='Cash']")
        if await cash_panel.count() == 0:
            return "ERROR: Cash option not found in payment widget."

        cash_button = cash_panel.locator("div[role='button']")
        is_aria_disabled = False
        if await cash_button.count() > 0:
            is_aria_disabled = await cash_button.first.get_attribute("aria-disabled") == "true"

        if is_aria_disabled:
            return "ERROR: Cash is disabled for this order."

        if await cash_button.count() > 0:
            await cash_button.first.click()
        else:
            await cash_panel.first.click()

        print("Selected Cash on Delivery.")
        return "OK: Cash on Delivery selected."

     except Exception as e:
        return f"ERROR: {str(e)}"


    async def select_upi_payment(self):
     """Select UPI and generate QR code inside the payment iframe."""
     logger.info("Attempting to select UPI and generate QR...")
     try:
        iframe_element = await self.page.wait_for_selector("#payment_widget", timeout=30000)
        if not iframe_element:
            return "ERROR: Payment widget iframe not found."

        frame = await iframe_element.content_frame()
        if not frame:
            return "ERROR: Could not access payment iframe content."

        await frame.wait_for_load_state("networkidle")

        upi_panel = frame.locator("div[title='UPI']")
        if await upi_panel.count() == 0:
            return "ERROR: UPI option not found in payment widget."

        upi_button = upi_panel.locator("div[role='button']")
        if await upi_button.count() > 0:
            is_open = await upi_button.first.get_attribute("aria-expanded") == "true"
            if not is_open:
                await upi_button.first.click()
                await self.page.wait_for_timeout(1000)
        else:
            await upi_panel.first.click()
            await self.page.wait_for_timeout(1000)

        # Click Generate QR
        generate_qr_btn = frame.locator("button:has-text('Generate QR')")
        if await generate_qr_btn.count() == 0:
            return "ERROR: Generate QR button not found."

        await generate_qr_btn.first.click()
        await self.page.wait_for_timeout(2000)

        # Try to extract QR base64
        try:
            qr_img_locator = frame.locator("div[class*='QrImageWrapper'] img")
            await qr_img_locator.wait_for(state="visible", timeout=5000)
            qr_src = await qr_img_locator.first.get_attribute("src")
            if qr_src and qr_src.startswith("data:image/"):
                b64 = qr_src.split(",")[1]
                fmt = qr_src.split(";")[0].split("/")[1]
                
                logger.info(f"[QR] b64 length: {len(b64)}, format: {fmt}")
                # ── Decode QR to get UPI URL ──
                upi_url = self.decode_qr_base64(b64)
                logger.info(f"[QR] Decoded URL: {upi_url}")
                amount = self.parse_upi_amount(upi_url) if upi_url else None
                if upi_url:
                    logger.info(f"[QR] Decoded UPI URL: {upi_url}")
                if amount:
                    logger.info(f"[QR] Amount parsed: ₹{amount}")
       
            # ── Print QR in terminal ──
                # self.print_qr_in_terminal(b64)
                logger.info("QR code displayed in terminal — scan with your UPI app")
                return {
                    "status": "OK: UPI QR generated — scan to pay.",
                    "qr_base64": b64,
                    "format": fmt,
                    "upi_url": upi_url,      # ← new
                    "amount": amount,     
                }
        except Exception as qr_e:
            print(f"QR image extraction failed: {qr_e}")

        return "OK: UPI selected and QR generated (could not extract image)."

     except Exception as e:
        return f"ERROR: {str(e)}"
    
    @staticmethod
    def decode_qr_base64(b64: str) -> str | None:
        """Decode a base64 QR image and return the embedded text/URL."""
        try:
            img_bytes = base64.b64decode(b64)
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")  # convert to grayscale
            logger.info(f"[QR] Image size: {img.size}, mode: {img.mode}")
            
            if img.width < 300 or img.height < 300:
                scale = (300 // min(img.width, img.height)) + 1
                img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
                logger.info(f"[QR] Upscaled to {img.size} (scale {scale}x)")
            results = pyzbar_decode(img)
            logger.info(f"[QR] pyzbar results count: {len(results)}")
            if results:
                decoded = results[0].data.decode("utf-8")
                logger.info(f"[QR] Decoded text: {decoded}")
                return decoded
        except Exception as e:
            logger.warning(f"[QR] Decode failed: {e}")
        return None

    @staticmethod
    def parse_upi_amount(upi_url: str) -> str | None:
        """Extract the 'am' (amount) parameter from a UPI URL."""
        try:
            params = parse_qs(urlparse(upi_url).query)
            return params.get("am", [None])[0]
        except Exception:
            return None

    def print_qr_in_terminal(self, qr_base64: str):
     """Render a base64 PNG QR code in the terminal using Unicode block characters."""
     try:
        from PIL import Image
     except ImportError:
        logger.warning("Pillow not installed — run: pip install Pillow")
        return

     img_bytes = base64.b64decode(qr_base64)
     img = Image.open(io.BytesIO(img_bytes)).convert("1")  # # convert to 1-bit black/white

     width, height = img.size
     # Scale down if too large — terminal chars are ~2:1 height:width ratio
     if width < 100:
        img = img.resize((width * 2, height * 2), Image.NEAREST)
     width, height = img.size
     pixels = img.load()

     # Each terminal char = 2 vertical pixels using half-block chars
    # ▀ = top black, bottom white
    # ▄ = top white, bottom black
    # █ = both black
    #   = both white
     print()
     for y in range(0, height - 1, 2):
        row = "  "
        for x in range(width):
            top    = pixels[x, y] == 0
            bottom = pixels[x, y + 1] == 0
            if top and bottom:
                row += "█"
            elif top:
                row += "▀"
            elif bottom:
                row += "▄"
            else:
                row += " "
        print(row)
     print()
     logger.info("Scan the QR code above with your UPI app")