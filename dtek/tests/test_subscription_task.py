
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, ANY
from datetime import datetime, timedelta

# Import module to test
import dtek_telegram_bot

@pytest.mark.asyncio
async def test_subscription_checker_sends_status_message():
    """
    Verify that subscription_checker_task sends a status message 
    when a schedule update is detected.
    """
    # Mock Bot
    mock_bot = AsyncMock()
    
    # Mock DB Connection and Cursor
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    
    # Setup DB return values
    # Row: user_id, city, street, house, interval_hours, last_schedule_hash
    user_id = 12345
    mock_cursor.fetchall.return_value = [
        (user_id, "Дніпро", "Test St", "1", 1, "old_hash")
    ]
    mock_conn.execute.return_value = mock_cursor
    
    # Mock API response (New data)
    new_schedule_data = {
        "city": "Дніпро",
        "street": "Test St",
        "house_num": "1",
        "group": "1",
        "schedule": {
            "20.11.24": [{"shutdown": "00:00–04:00"}] # Light OFF at start of day
        }
    }
    
    # Mock helper functions
    with patch('dtek_telegram_bot.db_conn', mock_conn), \
         patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock(return_value=new_schedule_data)), \
         patch('dtek_telegram_bot._get_schedule_hash_compact', return_value="new_hash"), \
         patch('dtek_telegram_bot.ADDRESS_CACHE', {}), \
         patch('dtek_telegram_bot.SCHEDULE_DATA_CACHE', {}), \
         patch('dtek_telegram_bot._process_single_day_schedule_compact', return_value="Schedule Text"), \
         patch('dtek_telegram_bot._generate_48h_schedule_image', return_value=None), \
         patch('dtek_telegram_bot._get_current_status_message', return_value="🔦 Status Message") as mock_get_status:

        # We need to break the infinite loop in subscription_checker_task
        # We can do this by mocking asyncio.sleep to raise an exception after the first call
        # But subscription_checker_task calls sleep at the START of the loop.
        # So we need it to run once, then raise.
        
        # However, the loop starts with sleep.
        # await asyncio.sleep(CHECKER_LOOP_INTERVAL_SECONDS)
        
        side_effect = [None, Exception("Break Loop")] # Sleep once (allow logic to run), then break
        
        with patch('asyncio.sleep', side_effect=side_effect):
            try:
                await dtek_telegram_bot.subscription_checker_task(mock_bot)
            except Exception as e:
                if str(e) != "Break Loop":
                    raise e

        # VERIFICATION
        
        # 1. Verify _get_current_status_message was called
        mock_get_status.assert_called_once()
        
        # 2. Verify bot.send_message was called with the status message
        # The task sends multiple messages: header, image (skipped), day text, status message.
        # We check if ANY call to send_message contained our status message.
        
        status_message_sent = False
        for call_args in mock_bot.send_message.call_args_list:
            # call_args is (args, kwargs)
            # kwargs might contain 'text'
            text = call_args.kwargs.get('text', '')
            if "🔦 Status Message" in text:
                status_message_sent = True
                break
        
        assert status_message_sent, "Status message was not sent to the user!"
