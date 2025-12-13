"""
Tests for /unsubscribe command and callbacks.

Tests both address and group subscription removal.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, CallbackQuery


@pytest.mark.asyncio
async def test_unsubscribe_callback_group_subscription():
    """
    Test that group subscription unsubscribe callback works correctly.
    
    This would have caught the missing import bug:
    NameError: name 'remove_group_subscription' is not defined
    """
    from common.handlers import handle_callback_unsubscribe
    from common.bot_base import BotContext
    
    # Setup mock callback
    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = MagicMock()
    callback.from_user.id = 123456
    callback.data = "unsub:group:42"  # Group subscription ID 42
    callback.answer = AsyncMock()
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()
    
    # Setup mock context
    ctx = MagicMock(spec=BotContext)
    ctx.logger = None
    ctx.provider_code = "dtek"
    
    # Setup mock DB
    mock_db = AsyncMock()
    ctx.db_conn = mock_db
    
    # Mock get_user_subscriptions - returns one group subscription
    async def mock_get_subs(conn, user_id, provider):
        return [{
            'type': 'group',
            'id': 42,
            'group_name': '3.1',
            'interval_hours': 6.0,
            'notification_lead_time': 15,
            'provider': 'dtek',
            'city': None,
            'street': None,
            'house': None
        }]
    
    # Mock remove_group_subscription
    async def mock_remove_group(conn, sub_id):
        return True  # Success
    
    with patch('common.handlers.get_user_subscriptions', mock_get_subs):
        with patch('common.handlers.remove_group_subscription', mock_remove_group):
            await handle_callback_unsubscribe(callback, ctx)
    
    # Should answer the callback
    callback.answer.assert_called_once()
    
    # Should edit message with success
    callback.message.edit_text.assert_called_once()
    args = callback.message.edit_text.call_args[0]
    assert "Підписку скасовано" in args[0]
    assert "3.1" in args[0] or "3\\.1" in args[0]  # Может быть escaped для Markdown


@pytest.mark.asyncio
async def test_unsubscribe_callback_address_subscription():
    """Test address subscription removal via callback."""
    from common.handlers import handle_callback_unsubscribe
    from common.bot_base import BotContext
    
    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = MagicMock()
    callback.from_user.id = 123456
    callback.data = "unsub:99"  # Address subscription ID 99
    callback.answer = AsyncMock()
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()
    
    ctx = MagicMock(spec=BotContext)
    ctx.logger = None
    ctx.provider_code = "dtek"
    mock_db = AsyncMock()
    ctx.db_conn = mock_db
    
    # Mock get_user_subscriptions - returns one address subscription
    async def mock_get_subs(conn, user_id, provider):
        return [{
            'type': 'address',
            'id': 99,
            'city': 'м. Дніпро',
            'street': 'вул. Сонячна',
            'house': '6',
            'interval_hours': 6.0,
            'notification_lead_time': 15,
            'group_name': '3.2'
        }]
    
    async def mock_remove_addr(conn, user_id, sub_id):
        return ('м. Дніпро', 'вул. Сонячна', '6')
    
    with patch('common.handlers.get_user_subscriptions', mock_get_subs):
        with patch('common.handlers.remove_subscription_by_id', mock_remove_addr):
            await handle_callback_unsubscribe(callback, ctx)
    
    callback.answer.assert_called_once()
    callback.message.edit_text.assert_called_once()
    args = callback.message.edit_text.call_args[0]
    assert "Підписку скасовано" in args[0]
    assert "Сонячна" in args[0]


@pytest.mark.asyncio
async def test_unsubscribe_callback_all_subscriptions():
    """Test removing all subscriptions via callback."""
    from common.handlers import handle_callback_unsubscribe
    from common.bot_base import BotContext
    
    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = MagicMock()
    callback.from_user.id = 123456
    callback.data = "unsub:all"
    callback.answer = AsyncMock()
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()
    
    ctx = MagicMock(spec=BotContext)
    ctx.logger = None
    ctx.provider_code = "dtek"
    mock_db = AsyncMock()
    ctx.db_conn = mock_db
    
    # Mock DB execute for deleting group subscriptions
    mock_cursor = AsyncMock()
    mock_cursor.rowcount = 2  # 2 group subscriptions deleted
    mock_db.execute.return_value = mock_cursor
    mock_db.commit = AsyncMock()
    
    async def mock_remove_all(conn, user_id):
        return 3  # 3 address subscriptions deleted
    
    with patch('common.handlers.remove_all_subscriptions', mock_remove_all):
        await handle_callback_unsubscribe(callback, ctx)
    
    callback.answer.assert_called_once()
    callback.message.edit_text.assert_called_once()
    args = callback.message.edit_text.call_args[0]
    assert "Всі підписки скасовано" in args[0]
    assert "5" in args[0]  # 3 addr + 2 group = 5 total


@pytest.mark.asyncio
async def test_unsubscribe_callback_imports():
    """
    Test that all necessary functions are imported.
    
    This test verifies that the bug we found doesn't happen again.
    """
    from common import handlers
    
    # Verify all unsubscribe-related functions are available
    assert hasattr(handlers, 'handle_callback_unsubscribe')
    assert hasattr(handlers, 'get_user_subscriptions')
    assert hasattr(handlers, 'remove_subscription_by_id')
    assert hasattr(handlers, 'remove_all_subscriptions')
    assert hasattr(handlers, 'remove_group_subscription')  # This was MISSING!


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
