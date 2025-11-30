"""
Tests for DTEK Bot Command Handlers
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from dtek.bot.bot import (
    command_start_handler,
    command_check_handler,
    command_repeat_handler,
    command_subscribe_handler,
    cmd_alert,
    process_city,
    process_street,
    process_house
)
from common.bot_base import CaptchaState, CheckAddressState

@pytest.mark.unit
class TestDtekBotHandlers:
    """Tests for DTEK bot command handlers"""
    
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
        # Mock HUMAN_USERS to be empty
        with patch('dtek.bot.bot.HUMAN_USERS', {}):
            with patch('dtek.bot.bot.get_captcha_data') as mock_captcha:
                mock_captcha.return_value = ("2 + 2?", 4)
                
                await command_start_handler(message, state)
                
                # Should set state to CaptchaState.waiting_for_answer
                state.set_state.assert_called_with(CaptchaState.waiting_for_answer)
                # Should save captcha answer
                state.update_data.assert_called_with(captcha_answer=4)
                # Should send welcome message with captcha
                message.answer.assert_called_once()
                args = message.answer.call_args[0]
                assert "2 + 2?" in args[0]

    @pytest.mark.asyncio
    async def test_command_check_no_args(self, message, state):
        """Test /check command without arguments"""
        message.text = "/check"
        
        # Mock HUMAN_USERS to include user
        with patch('dtek.bot.bot.HUMAN_USERS', {12345: True}):
            await command_check_handler(message, state)
            
            message.answer.assert_called_once()
            assert "Введіть назву міста" in message.answer.call_args[0][0] or "введіть назву міста" in message.answer.call_args[0][0]
            state.set_state.assert_called_with(CheckAddressState.waiting_for_city)

    @pytest.mark.asyncio
    async def test_command_check_with_args(self, message, state):
        """Test /check command with arguments"""
        message.text = "/check Дніпро, Сонячна, 6"
        
        with patch('dtek.bot.bot.HUMAN_USERS', {12345: True}):
            with patch('dtek.bot.bot.get_shutdowns_data', new=AsyncMock()) as mock_get_data:
                mock_get_data.return_value = {"schedule": {}}
                with patch('dtek.bot.bot.db_conn', new=AsyncMock()):
                    with patch('dtek.bot.bot.send_schedule_response', new=AsyncMock()) as mock_send:
                        
                        await command_check_handler(message, state)
                        
                        mock_get_data.assert_called_once()
                        mock_send.assert_called_once()


    @pytest.mark.asyncio
    async def test_command_subscribe_existing_subscription(self, message, state):
        """
        Test /subscribe when user already has a subscription.
        Regression test for UnboundLocalError: 'new_lead_time' referenced before assignment.
        """
        message.text = "/subscribe 4"  # Match the 4.0 interval in mock
        user_id = 12345
        
        # Mock DB connection and cursor
        mock_db = AsyncMock()
        mock_cursor = AsyncMock()
        mock_db.execute.return_value = mock_cursor
        
        # Setup mock return values for DB queries
        # 1. Check last_check (found)
        # 2. Check existing subscription (found)
        # 3. Check notification_lead_time (found)
        # 4. Check group name (optional)
        
        async def mock_execute_side_effect(query, params=None):
            cursor = AsyncMock()
            if "SELECT city, street, house, last_hash FROM user_last_check" in query:
                cursor.fetchone.return_value = ("Dnipro", "Street", "1", "hash123")
            elif "SELECT last_schedule_hash, interval_hours FROM subscriptions" in query:
                # Subscription exists!
                cursor.fetchone.return_value = ("hash123", 4.0)
            elif "SELECT notification_lead_time FROM subscriptions" in query:
                cursor.fetchone.return_value = (15,)
            elif "SELECT group_name FROM user_last_check" in query:
                cursor.fetchone.return_value = ("Group 1",)
            return cursor

        mock_db.execute.side_effect = mock_execute_side_effect

        with patch('dtek.bot.bot.HUMAN_USERS', {user_id: True}):
            with patch('dtek.bot.bot.db_conn', mock_db):
                with patch('dtek.bot.bot.update_user_activity', new=AsyncMock()):
                    
                    await command_subscribe_handler(message, state)
                    
                    # Verify that we sent a success message
                    message.answer.assert_called_once()
                    args = message.answer.call_args[0]
                    # Check that the message contains the lead time (which was causing the error)
                    assert "Сповіщення за: **15 хв**" in args[0]
                    assert "Підписка вже існує" in args[0]

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
