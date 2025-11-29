"""
Tests for common.formatting module
"""
import pytest
import pytz
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from common.formatting import (
    process_single_day_schedule_compact,
    get_current_status_message,
)


# ============================================================
# SCHEDULE PROCESSING TESTS
# ============================================================

@pytest.mark.unit
class TestProcessSingleDaySchedule:
    """Tests for process_single_day_schedule_compact function"""
    
    def test_no_outages(self):
        """No outages - empty list"""
        slots = []
        result = process_single_day_schedule_compact("12.11.24", slots)
        # No message should be produced for days without outages
        assert result == "" or result.strip() == ""
    
    def test_empty_slots(self):
        """Empty slots list"""
        result = process_single_day_schedule_compact("12.11.24", [])
        assert result == "" or result.strip() == ""
    
    def test_single_full_slot(self):
        """One full hour slot"""
        slots = [{"shutdown": "10:00–11:00"}]
        result = process_single_day_schedule_compact("12.11.24", slots)
        assert "10:00 - 11:00" in result
        assert "1 год." in result
        assert "⚫" in result
    
    def test_consecutive_full_slots(self):
        """Consecutive full slots should be merged"""
        slots = [
            {"shutdown": "10:00–11:00"},
            {"shutdown": "11:00–12:00"},
            {"shutdown": "12:00–13:00"}
        ]
        result = process_single_day_schedule_compact("12.11.24", slots)
        assert "10:00 - 13:00" in result
        assert "3 год." in result
    
    def test_half_slot(self):
        """Half hour slot"""
        slots = [{"shutdown": "10:30–11:00"}]
        result = process_single_day_schedule_compact("12.11.24", slots)
        assert "10:30 - 11:00" in result
        assert "0,5 год." in result
    
    def test_mixed_slots_with_gap(self):
        """Slots with a gap"""
        slots = [
            {"shutdown": "10:00–11:00"},
            {"shutdown": "11:00–12:00"},
            {"shutdown": "14:00–15:00"}  # Gap after 12
        ]
        result = process_single_day_schedule_compact("12.11.24", slots)
        # Should be 2 groups
        assert "10:00 - 12:00" in result
        assert "14:00 - 15:00" in result
    
    def test_end_hour_zero_means_24(self):
        """Handle 23-24 (midnight crossing)"""
        slots = [{"shutdown": "23:00–24:00"}]
        result = process_single_day_schedule_compact("12.11.24", slots)
        assert "23:00" in result
        assert "24:00" in result
    
    def test_mixed_full_and_half_consecutive(self):
        """Consecutive full and half slots"""
        slots = [
            {"shutdown": "10:00–11:00"},
            {"shutdown": "11:30–12:00"}  # Gap 11:00-11:30
        ]
        result = process_single_day_schedule_compact("12.11.24", slots)
        assert "10:00 - 11:00" in result
        assert "11:30 - 12:00" in result
    
    def test_continuous_merge(self):
        """Merge adjacent slots"""
        slots = [
            {"shutdown": "10:00–11:00"},
            {"shutdown": "11:00–12:00"},
            {"shutdown": "12:00–12:30"}
        ]
        result = process_single_day_schedule_compact("12.11.24", slots)
        assert "10:00 - 12:30" in result
        assert "2,5 год." in result

    def test_no_double_escaped_newlines(self):
        """Regression test: Ensure no double escaped newlines (\n) in output"""
        slots = [{"shutdown": "10:00–11:00"}]
        result = process_single_day_schedule_compact("12.11.24", slots)
        assert "\\n" not in result
        assert "\n" in result  # Should contain actual newline



# ============================================================
# STATUS MESSAGE TESTS
# ============================================================

@pytest.mark.unit
class TestGetCurrentStatusMessage:
    """Tests for get_current_status_message function"""
    
    @pytest.fixture
    def kiev_tz(self):
        return pytz.timezone('Europe/Kiev')
    
    def test_no_schedule_data(self):
        """No schedule data available -> None"""
        msg = get_current_status_message({})
        assert msg is None
    
    def test_currently_shutdown(self, kiev_tz):
        """Currently in a shutdown period"""
        # Mock datetime to be 10:30 Kiev time
        mock_now = kiev_tz.localize(datetime(2024, 11, 12, 10, 30))
        
        schedule_data = {
            "12.11.24": [{"shutdown": "10:00–12:00"}]
        }
        
        with patch('common.formatting.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_now
            mock_datetime.strptime.side_effect = datetime.strptime
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            
            msg = get_current_status_message(schedule_data)
            
            assert msg is not None
            assert "⚫ Зараз діє відключення" in msg
            assert "до 12:00" in msg
    
    def test_currently_power_on_next_shutdown_soon(self, kiev_tz):
        """Power is on, next shutdown is today"""
        # Mock datetime to be 09:00 Kiev time
        mock_now = kiev_tz.localize(datetime(2024, 11, 12, 9, 0))
        
        schedule_data = {
            "12.11.24": [{"shutdown": "10:00–12:00"}]
        }
        
        with patch('common.formatting.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_now
            mock_datetime.strptime.side_effect = datetime.strptime
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            
            msg = get_current_status_message(schedule_data)
            
            assert msg is not None
            assert "🟡 Наступне відключення у 10:00" in msg
    
    def test_currently_power_on_no_shutdowns_today(self, kiev_tz):
        """Power is on, no shutdowns today -> None"""
        # Mock datetime to be 09:00 Kiev time
        mock_now = kiev_tz.localize(datetime(2024, 11, 12, 9, 0))
        
        schedule_data = {
            "12.11.24": []
        }
        
        with patch('common.formatting.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_now
            mock_datetime.strptime.side_effect = datetime.strptime
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            
            msg = get_current_status_message(schedule_data)
            
            assert msg is None
    
    def test_currently_power_on_next_shutdown_tomorrow(self, kiev_tz):
        """Power is on, next shutdown is tomorrow"""
        # Mock datetime to be 23:00 Kiev time
        mock_now = kiev_tz.localize(datetime(2024, 11, 12, 23, 0))
        
        schedule_data = {
            "12.11.24": [],
            "13.11.24": [{"shutdown": "01:00–03:00"}]
        }
        
        with patch('common.formatting.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_now
            mock_datetime.strptime.side_effect = datetime.strptime
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            
            msg = get_current_status_message(schedule_data)
            
            # Should find the one tomorrow
            assert msg is not None
            assert "🟡 Наступне відключення у 01:00" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
