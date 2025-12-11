"""
Middleware for aiogram to set user context for logging.
Automatically extracts user_id from Telegram messages and sets it in logging context.
"""

from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from common.log_context import set_user_context, clear_user_context


class UserContextMiddleware(BaseMiddleware):
    """
    Middleware that sets user_id in logging context for each message/callback.
    This allows automatic user_id injection in all log messages.
    """
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """
        Set user context before handling the event, clear after.
        
        Args:
            handler: Next handler in the chain
            event: Telegram event (Message, CallbackQuery, etc.)
            data: Additional data
            
        Returns:
            Handler result
        """
        # Extract user_id from different event types
        user_id = None
        
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
        
        # Set user context if user_id is available
        if user_id:
            set_user_context(user_id)
        
        try:
            # Call the next handler
            return await handler(event, data)
        finally:
            # Always clear context after handling
            clear_user_context()
