"""
Telegram Bot Service
- Send product availability notifications to Telegram channel
- Poll for button click callbacks
"""

import logging
import aiohttp
import asyncio

logger = logging.getLogger(__name__)


class TelegramBot:
    """Send messages to Telegram channel and handle button callbacks"""
    
    def __init__(self, bot_token: str, channel_id: str):
        """
        Initialize Telegram bot
        
        Args:
            bot_token: Telegram bot token (from @BotFather)
            channel_id: Telegram channel ID (e.g., @mychannel or -100123456789)
        """
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.last_update_id = 0
        self.callback_handlers = {}
        self.polling_task = None
        self.is_polling = False
    
    async def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send a text message to the Telegram channel
        
        Args:
            message: Message text (supports HTML formatting)
            parse_mode: HTML or Markdown formatting
        
        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.channel_id,
                "text": message,
                "parse_mode": parse_mode
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        logger.info("✓ Telegram message sent successfully")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Telegram send failed (status {response.status}): {error_text}")
                        return False
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False
    
    async def send_product_notification(self, product_name: str, product_url: str, location_name: str, with_buttons: bool = False) -> bool:
        """
        Send a formatted product availability notification
        
        Args:
            product_name: Name of the product
            product_url: URL to the product
            location_name: Delivery location name
            with_buttons: If True, add inline buttons for user actions
        
        Returns:
            True if successful, False otherwise
        """
        # Use only Telegram-supported HTML tags (avoid <h1>, etc.)
        message = (
            f"<b>🎉 Product Available!</b>\n\n"
            f"<b>Product:</b> {product_name}\n\n"
            f"📍<b>Location:</b> <b>{location_name}</b>\n\n"
            f"<b>Link:</b>\n"
            f"<a href=\"{product_url}\">Open on Blinkit</a>"
        )

        if with_buttons:
            result = await self.send_message_with_buttons(message)
        else:
            result = await self.send_message(message)
        
        if not result:
            logger.warning("Telegram notification failed — check bot token, channel id, and that the bot is added to the channel/group.")
        return result
    
    async def send_message_with_buttons(self, message: str, buttons: dict = None) -> bool:
        """
        Send a message with inline buttons
        
        Args:
            message: Message text (supports HTML formatting)
            buttons: Dict of button labels and callback_data (e.g., {"Retry": "retry_watch"})
        
        Returns:
            True if successful, False otherwise
        """
        if buttons is None:
            buttons = {
                "Retry": "retry_watch",
                "Cancel": "cancel_watch"
            }
        
        try:
            url = f"{self.base_url}/sendMessage"
            
            # Build inline keyboard
            inline_keyboard = []
            for label, callback_data in buttons.items():
                inline_keyboard.append({
                    "text": label,
                    "callback_data": callback_data
                })
            
            payload = {
                "chat_id": self.channel_id,
                "text": message,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [inline_keyboard]
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        logger.info("✓ Telegram message with buttons sent successfully")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Telegram send failed (status {response.status}): {error_text}")
                        return False
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False
    
    def register_callback(self, callback_data: str, handler_func):
        """
        Register a callback handler for button clicks
        
        Args:
            callback_data: The callback_data string from button (e.g., "retry_watch")
            handler_func: Async function to call when button is clicked
        """
        self.callback_handlers[callback_data] = handler_func
        logger.debug(f"Registered callback handler for: {callback_data}")
    
    async def get_updates(self):
        """
        Poll Telegram for new updates (callback queries from button clicks)
        
        Returns:
            List of updates from Telegram
        """
        try:
            url = f"{self.base_url}/getUpdates"
            payload = {
                "offset": self.last_update_id + 1,
                "timeout": 10,  # Long polling timeout
                "allowed_updates": ["callback_query"]  # Only get button clicks
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("ok"):
                            return data.get("result", [])
                    return []
        except asyncio.TimeoutError:
            # Long polling timeout is normal
            return []
        except Exception as e:
            logger.debug(f"Error polling updates: {e}")
            return []
    
    async def answer_callback_query(self, callback_query_id: str, text: str = "", show_alert: bool = False):
        """
        Send a notification reply to a button click
        
        Args:
            callback_query_id: The callback query ID from Telegram
            text: Notification text to show user
            show_alert: If True, show as alert popup; if False, show as toast
        """
        try:
            url = f"{self.base_url}/answerCallbackQuery"
            payload = {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        logger.warning(f"Failed to answer callback query: {await response.text()}")
        except Exception as e:
            logger.debug(f"Error answering callback query: {e}")
    
    async def start_polling(self):
        """
        Start polling for button click callbacks (runs in background)
        """
        if self.is_polling:
            logger.warning("Polling already started")
            return
        
        self.is_polling = True
        logger.info("[TELEGRAM] Started polling for button callbacks...")
        
        while self.is_polling:
            try:
                updates = await self.get_updates()
                
                for update in updates:
                    self.last_update_id = update.get("update_id", self.last_update_id)
                    
                    # Check if this is a callback query (button click)
                    callback_query = update.get("callback_query")
                    if callback_query:
                        callback_id = callback_query.get("id")
                        from_user = callback_query.get("from", {})
                        user_name = from_user.get("first_name", "User")
                        callback_data = callback_query.get("data", "")
                        
                        logger.info(f"[TELEGRAM] Button clicked by {user_name}: {callback_data}")
                        
                        # Call registered handler if exists
                        if callback_data in self.callback_handlers:
                            handler = self.callback_handlers[callback_data]
                            try:
                                # Answer the callback query (show feedback to user)
                                if callback_data == "retry_watch":
                                    await self.answer_callback_query(callback_id, "🔄 Restarting watch...", show_alert=False)
                                    logger.info(f"[TELEGRAM] Executing retry handler for {user_name}")
                                elif callback_data == "cancel_watch":
                                    await self.answer_callback_query(callback_id, "❌ Watch cancelled", show_alert=False)
                                    logger.info(f"[TELEGRAM] Executing cancel handler for {user_name}")
                                else:
                                    await self.answer_callback_query(callback_id, "✓ Processing...", show_alert=False)
                                
                                # Execute the handler
                                await handler()
                            except Exception as e:
                                logger.error(f"Error handling callback: {e}")
                                await self.answer_callback_query(callback_id, "Error processing action", show_alert=True)
                        else:
                            logger.warning(f"No handler registered for callback: {callback_data}")
                            await self.answer_callback_query(callback_id, "Handler not found", show_alert=True)
                
                # Small delay to avoid busy waiting
                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")
                await asyncio.sleep(1)  # Wait before retrying
    
    async def stop_polling(self):
        """
        Stop polling for callbacks
        """
        self.is_polling = False
        if self.polling_task:
            self.polling_task.cancel()
        logger.info("[TELEGRAM] Stopped polling for button callbacks")
