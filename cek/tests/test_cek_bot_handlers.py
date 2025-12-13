"""
Tests for CEK Bot Command Handlers
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from cek.bot.bot import (
    command_start_handler,
    command_check_handler,
    command_repeat_handler,
    command_subscribe_handler,
    process_city,
    process_street,
    process_house
)
from common.bot_base import CaptchaState, CheckAddressState

@pytest.mark.unit
class TestCekBotHandlers:
    """Tests for CEK bot command handlers"""
    
    @pytest.fixture
    def message(self):
        msg = AsyncMock(spec=Message)
        msg.from_user = MagicMock()
        msg.from_user.id = 12345
        msg.from_user.first_name = "TestUser"
        msg.text = "/start"
        msg.answer = AsyncMock()
        msg.reply = AsyncMock()
        return msg
    
    @pytest.fixture
    def state(self):
        state = AsyncMock(spec=FSMContext)
        state.get_data.return_value = {}
        state.get_state.return_value = None
        return state

    @pytest.mark.asyncio
    async def test_command_start_handler(self, message, state):
        """Test /start command"""
        with patch('cek.bot.bot.HUMAN_USERS', {}):
            with patch('cek.bot.bot.get_captcha_data') as mock_captcha:
                mock_captcha.return_value = ("2 + 2?", 4)
                
                await command_start_handler(message, state)
                
                state.set_state.assert_called_with(CaptchaState.waiting_for_answer)
                message.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_check_no_args(self, message, state):
        """Test /check command without arguments"""
        message.text = "/check"
        
        # Mock HUMAN_USERS in BOTH cek.bot.bot AND common.handlers where it's checked
        with patch('cek.bot.bot.HUMAN_USERS', {12345: True}):
            with patch('common.handlers.HUMAN_USERS', {12345: True}):
                with patch('common.handlers.get_user_addresses', new=AsyncMock(return_value=[])):
                    await command_check_handler(message, state)
            
                    message.answer.assert_called_once()
                    state.set_state.assert_called_with(CheckAddressState.waiting_for_city)

    @pytest.mark.asyncio
    async def test_command_check_with_args(self, message, state):
        """Test /check command with arguments"""
        message.text = "/check Дніпро, Сонячна, 6"
        
        with patch('cek.bot.bot.HUMAN_USERS', {12345: True}):
            with patch('common.handlers.HUMAN_USERS', {12345: True}):
                with patch('cek.bot.bot.get_shutdowns_data', new=AsyncMock()) as mock_get_data:
                    mock_get_data.return_value = {"schedule": {}, "group": "1"}
                    with patch('cek.bot.bot.db_conn', new=AsyncMock()):
                        with patch('common.handlers.save_user_address', new=AsyncMock()):
                            with patch('common.handlers.get_subscription_count', new=AsyncMock(return_value=0)):
                                with patch('common.handlers.update_user_activity', new=AsyncMock()):
                                    with patch('cek.bot.bot.send_schedule_response', new=AsyncMock()) as mock_send:
                        
                                        await command_check_handler(message, state)
                        
                                        mock_get_data.assert_called_once()
                                        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_subscribe_existing_subscription(self, message, state):
        """
        Test /subscribe when user already has a subscription.
        Regression test for migration 006 - verifies JOIN with addresses table works.
        """
        message.text = "/subscribe 4"  # Match the 4.0 interval in mock
        user_id = 12345
        
        # Mock DB connection and cursor
        mock_db = AsyncMock()
        mock_cursor = AsyncMock()
        mock_db.execute.return_value = mock_cursor
        
        # Setup mock return values for DB queries (updated for migration 006)
        async def mock_execute_side_effect(query, params=None):
            cursor = AsyncMock()
            # Updated for migration 006: user_last_check now needs JOIN with addresses
            if "FROM user_last_check ulc" in query and "JOIN addresses" in query:
                # Returns: city, street, house, hash, address_id, group_name
                cursor.fetchone.return_value = ("Dnipro", "Street", "1", "hash123", 1, "Group 1")
            elif "SELECT last_schedule_hash, interval_hours FROM subscriptions" in query:
                # Subscription exists!
                cursor.fetchone.return_value = ("hash123", 4.0)
            elif "SELECT notification_lead_time FROM subscriptions" in query:
                cursor.fetchone.return_value = (15,)
            elif "SELECT group_name FROM user_last_check" in query:
                cursor.fetchone.return_value = ("Group 1",)
            return cursor

        mock_db.execute.side_effect = mock_execute_side_effect

        with patch('cek.bot.bot.HUMAN_USERS', {user_id: True}):
            with patch('common.handlers.HUMAN_USERS', {user_id: True}):
                with patch('cek.bot.bot.db_conn', mock_db):
                    with patch('common.handlers.update_user_activity', new=AsyncMock()):
                    
                        await command_subscribe_handler(message, state)
                    
                        # Verify that we sent a success message
                        message.answer.assert_called_once()
                        args = message.answer.call_args[0]
                        # Check that the message contains the lead time
                        assert "Сповіщення за **15 хв.**" in args[0]
                        assert "Підписка оформлена" in args[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
