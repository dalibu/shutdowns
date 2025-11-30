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
    merge_consecutive_slots,
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
        # For today's date we should show the 'no outages' message
        import pytz
        from datetime import datetime
        kiev_tz = pytz.timezone('Europe/Kiev')
        today_str = datetime.now(kiev_tz).strftime('%d.%m.%y')
        result = process_single_day_schedule_compact(today_str, slots)
        assert f"🟡 {today_str}: Відключення не заплановані" in result
    
    def test_empty_slots(self):
        """Empty slots list"""
        # For a non-today date with no slots, we should not show anything
        result = process_single_day_schedule_compact("01.01.20", [])
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


# ============================================================
# MERGE CONSECUTIVE SLOTS TESTS
# ============================================================

@pytest.mark.unit
class TestMergeConsecutiveSlots:
    """Tests for merge_consecutive_slots function"""
    
    def test_merge_three_consecutive_slots(self):
        """Test merging 3 consecutive hourly slots into one period."""
        schedule = {
            "30.11.25": [
                {"shutdown": "04:00–05:00", "status": "відключення"},
                {"shutdown": "05:00–06:00", "status": "відключення"},
                {"shutdown": "06:00–07:00", "status": "відключення"}
            ]
        }
        
        merged = merge_consecutive_slots(schedule)
        
        assert "30.11.25" in merged
        assert len(merged["30.11.25"]) == 1
        assert merged["30.11.25"][0]["shutdown"] == "04:00–07:00"
        assert merged["30.11.25"][0]["status"] == "відключення"

    def test_merge_with_gap(self):
        """Test merging with a gap - should create two separate periods."""
        schedule = {
            "30.11.25": [
                {"shutdown": "04:00–05:00", "status": "відключення"},
                {"shutdown": "05:00–06:00", "status": "відключення"},
                {"shutdown": "06:00–07:00", "status": "відключення"},
                {"shutdown": "14:00–15:00", "status": "відключення"},
                {"shutdown": "15:00–16:00", "status": "відключення"},
                {"shutdown": "16:00–17:00", "status": "відключення"},
                {"shutdown": "17:00–18:00", "status": "відключення"}
            ]
        }
        
        merged = merge_consecutive_slots(schedule)
        
        assert "30.11.25" in merged
        assert len(merged["30.11.25"]) == 2
        assert merged["30.11.25"][0]["shutdown"] == "04:00–07:00"
        assert merged["30.11.25"][1]["shutdown"] == "14:00–18:00"

    def test_single_slot_no_merge(self):
        """Test that a single slot remains unchanged."""
        schedule = {
            "30.11.25": [
                {"shutdown": "14:00–15:00", "status": "відключення"}
            ]
        }
        
        merged = merge_consecutive_slots(schedule)
        
        assert "30.11.25" in merged
        assert len(merged["30.11.25"]) == 1
        assert merged["30.11.25"][0]["shutdown"] == "14:00–15:00"

    def test_empty_schedule(self):
        """Test handling of empty schedule."""
        schedule = {}
        merged = merge_consecutive_slots(schedule)
        assert merged == {}

    def test_empty_slots_for_date(self):
        """Test handling of date with no slots."""
        schedule = {
            "30.11.25": []
        }
        
        merged = merge_consecutive_slots(schedule)
        
        assert "30.11.25" in merged
        assert merged["30.11.25"] == []

    def test_non_consecutive_slots(self):
        """Test slots with gaps between them - should not merge."""
        schedule = {
            "30.11.25": [
                {"shutdown": "04:00–05:00", "status": "відключення"},
                {"shutdown": "07:00–08:00", "status": "відключення"},
                {"shutdown": "10:00–11:00", "status": "відключення"}
            ]
        }
        
        merged = merge_consecutive_slots(schedule)
        
        assert "30.11.25" in merged
        assert len(merged["30.11.25"]) == 3
        assert merged["30.11.25"][0]["shutdown"] == "04:00–05:00"
        assert merged["30.11.25"][1]["shutdown"] == "07:00–08:00"
        assert merged["30.11.25"][2]["shutdown"] == "10:00–11:00"

    def test_unsorted_slots(self):
        """Test that slots are correctly merged even if not sorted initially."""
        schedule = {
            "30.11.25": [
                {"shutdown": "06:00–07:00", "status": "відключення"},
                {"shutdown": "04:00–05:00", "status": "відключення"},
                {"shutdown": "05:00–06:00", "status": "відключення"}
            ]
        }
        
        merged = merge_consecutive_slots(schedule)
        
        assert "30.11.25" in merged
        assert len(merged["30.11.25"]) == 1
        assert merged["30.11.25"][0]["shutdown"] == "04:00–07:00"

    def test_multiple_dates(self):
        """Test merging slots across multiple dates."""
        schedule = {
            "29.11.25": [
                {"shutdown": "17:00–18:00", "status": "відключення"},
                {"shutdown": "18:00–19:00", "status": "відключення"}
            ],
            "30.11.25": [
                {"shutdown": "04:00–05:00", "status": "відключення"},
                {"shutdown": "05:00–06:00", "status": "відключення"},
                {"shutdown": "06:00–07:00", "status": "відключення"}
            ]
        }
        
        merged = merge_consecutive_slots(schedule)
        
        assert len(merged) == 2
        assert merged["29.11.25"][0]["shutdown"] == "17:00–19:00"
        assert merged["30.11.25"][0]["shutdown"] == "04:00–07:00"

    def test_overlapping_slots(self):
        """Test handling of overlapping slots (edge case)."""
        schedule = {
            "30.11.25": [
                {"shutdown": "04:00–06:00", "status": "відключення"},
                {"shutdown": "05:00–07:00", "status": "відключення"}
            ]
        }
        
        merged = merge_consecutive_slots(schedule)
        
        assert "30.11.25" in merged
        assert len(merged["30.11.25"]) == 1
        # Should merge to cover entire range
        assert merged["30.11.25"][0]["shutdown"] == "04:00–07:00"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
