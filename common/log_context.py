"""
Context-aware logging utilities.
Provides user_id context for all log messages within a request.
"""

import contextvars
import logging
from typing import Optional

# Context variable to store current user_id
current_user_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    'current_user_id', default=None
)


def set_user_context(user_id: int) -> None:
    """Set user_id in current context."""
    current_user_id.set(user_id)


def get_user_context() -> Optional[int]:
    """Get user_id from current context."""
    return current_user_id.get()


def clear_user_context() -> None:
    """Clear user_id from current context."""
    current_user_id.set(None)


class UserContextFilter(logging.Filter):
    """
    Logging filter that adds user_id from context to log records.
    This allows automatic inclusion of user_id in all log messages.
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        """Add user_id to log record if available in context."""
        user_id = get_user_context()
        # Format user_id with prefix when available, empty string otherwise
        if user_id is not None:
            record.user_id = f"user_{user_id} | "
        else:
            record.user_id = ""
        return True

