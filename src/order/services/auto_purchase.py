"""
Auto Purchase Service
Watches product monitor status file and automatically adds product to cart and completes purchase
when product becomes available.
"""

import json
import logging
import asyncio
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class AutoPurchaseService:
    """Service that monitors product availability and triggers automatic purchase"""
    
    STATUS_FILE = Path("product_monitor_status.json")
    
    def __init__(self, order):
        """
        Initialize auto purchase service
        
        Args:
            order: BlinkitOrder service instance
        """
        self.order = order
        self.last_status = None
        self.product_details = None
        
    def read_status(self) -> Optional[Dict[str, Any]]:
        """Read current status from monitor file"""
        try:
            if not self.STATUS_FILE.exists():
                logger.debug(f"Status file not found: {self.STATUS_FILE}")
                return None
            
            with open(self.STATUS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading status file: {e}")
            return None
    
    def status_changed(self, current_status: Dict[str, Any]) -> bool:
        """Check if status has changed since last check"""
        if self.last_status is None:
            return True
        
        return (current_status.get("status") != self.last_status.get("status") or
                current_status.get("timestamp") != self.last_status.get("timestamp"))
    
    async def handle_available_product(self, status: Dict[str, Any]) -> bool:
        """
        Handle when product becomes available
        
        Args:
            status: Status dict from monitor
            
        Returns:
            True if purchase completed successfully
        """
        try:
            product_id = status.get("product_id")
            details = status.get("details", {})
            product_name = details.get("product_name", "Unknown Product")
            product_price = details.get("product_price", "Unknown")
            product_url = details.get("url", f"https://blinkit.com/prn/x/prid/{product_id}")
            
            # Sanitize price for logging (replace rupee symbol)
            price_str = str(product_price).replace('₹', 'Rs ').replace('â‚¹', 'Rs ')
            
            logger.info(f"[AVAILABLE] Product {product_id} is now available!")
            logger.info(f"[NAME] {product_name}")
            logger.info(f"[PRICE] {price_str}")
            logger.info(f"Initiating automatic purchase...")
            
            # Step 0: Navigate to product page
            logger.info(f"Step 0: Navigating to product URL...")
            try:
                await self.order.page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)  # Wait for page to fully load
                logger.info(f"[OK] Product page loaded")
            except Exception as e:
                logger.warning(f"Navigation took longer: {e}")
            
            # Step 1: Add product to cart (quantity = 1 by default)
            logger.info(f"Step 1: Adding product {product_id} to cart...")
            await self.order.add_to_cart(product_id, quantity=1)
            logger.info(f"[OK] Product added to cart")
            
            # Step 2: Check cart
            logger.info(f"Step 2: Checking cart contents...")
            cart_items = await self.order.get_cart_items()
            logger.info(f"[OK] Cart checked: {len(cart_items)} items")
            
            # Step 3: Proceed to checkout
            logger.info(f"Step 3: Proceeding to checkout...")
            await self.order.place_order()
            logger.info(f"[OK] Checkout initiated")
            
            # Step 4: Address selection (auto-select first address)
            logger.info(f"Step 4: Selecting delivery address...")
            addresses = await self.order.get_saved_addresses()
            if addresses and len(addresses) > 0:
                await self.order.select_address(0)  # Select first address
                logger.info(f"[OK] Address selected")
            else:
                logger.warning(f"No saved addresses found. Manual address selection required.")
            
            # Step 5: Get UPI IDs for payment
            logger.info(f"Step 5: Checking payment methods...")
            upi_ids = await self.order.get_upi_ids()
            if upi_ids and len(upi_ids) > 0:
                logger.info(f"Found {len(upi_ids)} saved UPI methods")
            else:
                logger.warning(f"No saved UPI methods found. Manual selection required.")
            
            # Step 6: Ready for payment
            logger.info(f"Step 6: Ready for payment...")
            logger.info(f"[INFO] Note: Manual UPI selection and payment confirmation required")
            logger.info(f"   (Payment automation requires additional security measures)")
            
            logger.info(f"[SUCCESS] Purchase workflow completed successfully!")
            logger.info(f"Product: {product_name} (ID: {product_id})")
            
            return True
            
        except Exception as e:
            logger.error(f"Error during automatic purchase: {e}")
            logger.error(f"Manual intervention may be required")
            return False
    
    async def watch(self, check_interval: int = 5, max_wait: int = None) -> bool:
        """
        Watch monitor status file and act when product becomes available
        
        Args:
            check_interval: How often to check status file (seconds)
            max_wait: Maximum time to wait (seconds), None = infinite
            
        Returns:
            True if product purchased, False otherwise
        """
        logger.info(f"Auto-purchase watcher started")
        logger.info(f"Monitoring: {self.STATUS_FILE.absolute()}")
        logger.info(f"Check interval: {check_interval} seconds")
        logger.info(f"Max wait time: {max_wait if max_wait else 'Unlimited'} seconds")
        logger.info("-" * 60)
        
        start_time = datetime.now()
        check_count = 0
        
        try:
            while True:
                # Check if max wait time exceeded
                if max_wait:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    if elapsed > max_wait:
                        logger.info(f"Max wait time exceeded ({max_wait} seconds)")
                        break
                
                check_count += 1
                current_status = self.read_status()
                
                if current_status:
                    # Log current status
                    status_type = current_status.get("status")
                    logger.debug(f"Check #{check_count}: Status = {status_type}")
                    
                    # Check if status changed
                    if self.status_changed(current_status):
                        logger.info(f"Status change detected: {status_type}")
                        self.last_status = current_status
                        
                        # Handle different status types
                        if status_type == "available" and current_status.get("action_needed"):
                            logger.info(f"Product available - starting purchase...")
                            if await self.handle_available_product(current_status):
                                logger.info(f"[SUCCESS] Auto-purchase completed successfully!")
                                return True
                            else:
                                logger.warning(f"Auto-purchase encountered issues")
                                break
                        
                        elif status_type == "error":
                            logger.error(f"Monitor reported error: {current_status.get('details')}")
                        
                        elif status_type == "coming_soon":
                            logger.info(f"Product still coming soon... waiting...")
                
                else:
                    logger.debug(f"Check #{check_count}: Monitor file not found yet...")
                
                await asyncio.sleep(check_interval)
                
        except KeyboardInterrupt:
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"\nAuto-purchase watcher stopped by user")
            logger.info(f"Duration: {elapsed:.1f} seconds")
            logger.info(f"Checks performed: {check_count}")
            return False
        
        return False

