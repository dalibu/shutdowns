import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timedelta
import pytz
import sys
import os

# Add parent directory to path to import the bot module
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# We need to import dtek_telegram_bot to access the function and cache
import dtek_telegram_bot
from dtek_telegram_bot import _process_alert_for_user, SCHEDULE_DATA_CACHE

class TestAlertLogic(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.kiev_tz = pytz.timezone('Europe/Kiev')
        self.bot = AsyncMock()
        self.bot.send_message = AsyncMock()
        
        # Clear cache before each test
        SCHEDULE_DATA_CACHE.clear()

    async def test_alert_sent_correctly(self):
        # Mock current time: 13:45
        # FIX: Use localize to avoid LMT issues
        now = self.kiev_tz.localize(datetime(2025, 11, 19, 13, 45, 0))
        
        # Schedule: Outage at 14:00
        schedule = {
            "19.11.25": [{"shutdown": "14:00–16:00"}]
        }
        
        address_key = ("City", "Street", "1")
        SCHEDULE_DATA_CACHE[address_key] = {"schedule": schedule}
        
        # Lead time: 20 mins. Event in 15 mins. Should alert.
        result = await _process_alert_for_user(
            self.bot, 123, "City", "Street", "1", 
            lead_time=20, 
            last_alert_event_start_str=None, 
            now=now
        )
        
        # Expect alert sent
        self.bot.send_message.assert_called_once()
        args, kwargs = self.bot.send_message.call_args
        self.assertIn("Через 15 хв. очікується **відключення**", args[1])
        self.assertEqual(kwargs.get('parse_mode'), 'Markdown')
        
        # Expect return value is the event time string
        expected_event_time = self.kiev_tz.localize(datetime(2025, 11, 19, 14, 0, 0)).isoformat()
        self.assertEqual(result, expected_event_time)

    async def test_no_alert_if_too_early(self):
        # Mock current time: 13:30. Event in 30 mins. Lead time 20 mins.
        now = self.kiev_tz.localize(datetime(2025, 11, 19, 13, 30, 0))
        
        schedule = {
            "19.11.25": [{"shutdown": "14:00–16:00"}]
        }
        
        address_key = ("City", "Street", "1")
        SCHEDULE_DATA_CACHE[address_key] = {"schedule": schedule}
        
        result = await _process_alert_for_user(
            self.bot, 123, "City", "Street", "1", 
            lead_time=20, 
            last_alert_event_start_str=None, 
            now=now
        )
        
        self.bot.send_message.assert_not_called()
        self.assertIsNone(result)

    async def test_no_alert_if_already_sent(self):
        # Mock current time: 13:45. Event in 15 mins. Lead time 20 mins.
        now = self.kiev_tz.localize(datetime(2025, 11, 19, 13, 45, 0))
        
        schedule = {
            "19.11.25": [{"shutdown": "14:00–16:00"}]
        }
        
        address_key = ("City", "Street", "1")
        SCHEDULE_DATA_CACHE[address_key] = {"schedule": schedule}
        
        event_time_str = self.kiev_tz.localize(datetime(2025, 11, 19, 14, 0, 0)).isoformat()
        
        result = await _process_alert_for_user(
            self.bot, 123, "City", "Street", "1", 
            lead_time=20, 
            last_alert_event_start_str=event_time_str, # Already sent
            now=now
        )
        
        self.bot.send_message.assert_not_called()
        self.assertIsNone(result)

    async def test_alert_for_light_on(self):
        # Mock current time: 15:45. Outage ends at 16:00.
        now = self.kiev_tz.localize(datetime(2025, 11, 19, 15, 45, 0))
        
        schedule = {
            "19.11.25": [{"shutdown": "14:00–16:00"}]
        }
        
        address_key = ("City", "Street", "1")
        SCHEDULE_DATA_CACHE[address_key] = {"schedule": schedule}
        
        result = await _process_alert_for_user(
            self.bot, 123, "City", "Street", "1", 
            lead_time=20, 
            last_alert_event_start_str=None, 
            now=now
        )
        
        self.bot.send_message.assert_called_once()
        args, kwargs = self.bot.send_message.call_args
        self.assertIn("Через 15 хв. очікується **включення**", args[1])
        self.assertEqual(kwargs.get('parse_mode'), 'Markdown')

if __name__ == '__main__':
    unittest.main()
