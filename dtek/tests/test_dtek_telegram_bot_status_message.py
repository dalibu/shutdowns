import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import pytz
import sys
import os

# Add parent directory to path to import the bot module
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dtek_telegram_bot import _get_current_status_message

class TestStatusMessage(unittest.TestCase):
    def setUp(self):
        self.kiev_tz = pytz.timezone('Europe/Kiev')

    @patch('dtek_telegram_bot.datetime')
    def test_light_on_next_off(self, mock_datetime):
        # Mock current time: 12:00
        mock_now = datetime(2025, 11, 19, 12, 0, 0, tzinfo=self.kiev_tz)
        mock_datetime.now.return_value = mock_now
        mock_datetime.strptime = datetime.strptime
        mock_datetime.combine = datetime.combine
        mock_datetime.min = datetime.min

        schedule = {
            "19.11.25": [
                {"shutdown": "14:00–16:00"}
            ]
        }
        
        msg = _get_current_status_message(schedule)
        self.assertEqual(msg, "💡 Наступне відключення у 14:00")

    @patch('dtek_telegram_bot.datetime')
    def test_light_off_next_on(self, mock_datetime):
        # Mock current time: 15:00 (inside 14:00-16:00)
        mock_now = datetime(2025, 11, 19, 15, 0, 0, tzinfo=self.kiev_tz)
        mock_datetime.now.return_value = mock_now
        mock_datetime.strptime = datetime.strptime
        mock_datetime.combine = datetime.combine
        mock_datetime.min = datetime.min

        schedule = {
            "19.11.25": [
                {"shutdown": "14:00–16:00"}
            ]
        }
        
        msg = _get_current_status_message(schedule)
        self.assertEqual(msg, "🔦 Відключення триватиме до 16:00")

    @patch('dtek_telegram_bot.datetime')
    def test_light_off_merged_slots(self, mock_datetime):
        # Mock current time: 15:00 (inside 14:00-16:00, followed by 16:00-18:00)
        mock_now = datetime(2025, 11, 19, 15, 0, 0, tzinfo=self.kiev_tz)
        mock_datetime.now.return_value = mock_now
        mock_datetime.strptime = datetime.strptime
        mock_datetime.combine = datetime.combine
        mock_datetime.min = datetime.min

        schedule = {
            "19.11.25": [
                {"shutdown": "14:00–16:00"},
                {"shutdown": "16:00–18:00"}
            ]
        }
        
        msg = _get_current_status_message(schedule)
        # Should merge and say until 18:00
        self.assertEqual(msg, "🔦 Відключення триватиме до 18:00")

    @patch('dtek_telegram_bot.datetime')
    def test_light_on_no_more_today(self, mock_datetime):
        # Mock current time: 20:00
        mock_now = datetime(2025, 11, 19, 20, 0, 0, tzinfo=self.kiev_tz)
        mock_datetime.now.return_value = mock_now
        mock_datetime.strptime = datetime.strptime
        mock_datetime.combine = datetime.combine
        mock_datetime.min = datetime.min

        schedule = {
            "19.11.25": [
                {"shutdown": "14:00–16:00"}
            ],
            "20.11.25": [
                {"shutdown": "08:00–10:00"}
            ]
        }
        
        msg = _get_current_status_message(schedule)
        self.assertEqual(msg, "💡 Наступне відключення у 08:00")

    @patch('dtek_telegram_bot.datetime')
    def test_light_on_no_schedule(self, mock_datetime):
        # Mock current time: 12:00
        mock_now = datetime(2025, 11, 19, 12, 0, 0, tzinfo=self.kiev_tz)
        mock_datetime.now.return_value = mock_now
        mock_datetime.strptime = datetime.strptime
        mock_datetime.combine = datetime.combine
        mock_datetime.min = datetime.min

        schedule = {}
        
        msg = _get_current_status_message(schedule)
        self.assertIsNone(msg)

if __name__ == '__main__':
    unittest.main()
