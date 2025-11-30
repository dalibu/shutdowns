"""
Tests for notification logic to prevent false notifications when schedule is empty.
"""

import pytest
from common.bot_base import get_schedule_hash_compact


def test_empty_schedule_notification_logic():
    """Test that empty schedules don't trigger false notifications."""
    
    # Test case 1: Empty schedule
    empty_data = {"schedule": {}}
    empty_hash = get_schedule_hash_compact(empty_data)
    
    # Test case 2: Schedule with empty days
    empty_days_data = {
        "schedule": {
            "30.11.25": [],
            "01.12.25": []
        }
    }
    empty_days_hash = get_schedule_hash_compact(empty_days_data)
    
    # Test case 3: Schedule with actual shutdowns
    real_schedule_data = {
        "schedule": {
            "30.11.25": [
                {"shutdown": "04:00–07:00", "status": "відключення"},
                {"shutdown": "14:00–18:00", "status": "відключення"}
            ],
            "01.12.25": []
        }
    }
    real_hash = get_schedule_hash_compact(real_schedule_data)
    
    # Verify hashes are different
    assert empty_hash != real_hash
    assert empty_days_hash != real_hash
    
    # Test notification logic
    def should_send_notification(new_hash, last_hash, schedule):
        """Simulate the notification logic from bot."""
        has_actual_schedule = any(slots for slots in schedule.values() if slots)
        return (new_hash != last_hash and 
                (has_actual_schedule or 
                 last_hash not in (None, "NO_SCHEDULE_FOUND", "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION")))
    
    # Test scenarios
    
    # 1. First subscription with empty schedule - should NOT notify
    assert not should_send_notification(
        empty_hash, 
        "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION", 
        empty_data["schedule"]
    )
    
    # 2. Empty to empty - should NOT notify
    assert not should_send_notification(
        empty_hash, 
        empty_hash, 
        empty_data["schedule"]
    )
    
    # 3. Empty to real schedule - should notify
    assert should_send_notification(
        real_hash, 
        empty_hash, 
        real_schedule_data["schedule"]
    )
    
    # 4. Real schedule to empty - should notify (schedule disappeared)
    assert should_send_notification(
        empty_hash, 
        real_hash, 
        empty_data["schedule"]
    )
    
    # 5. Real schedule to different real schedule - should notify
    different_real_data = {
        "schedule": {
            "30.11.25": [
                {"shutdown": "08:00–10:00", "status": "відключення"}
            ]
        }
    }
    different_real_hash = get_schedule_hash_compact(different_real_data)
    
    assert should_send_notification(
        different_real_hash, 
        real_hash, 
        different_real_data["schedule"]
    )


def test_hash_consistency():
    """Test that hash generation is consistent for same data."""
    
    data1 = {
        "schedule": {
            "30.11.25": [
                {"shutdown": "04:00–07:00", "status": "відключення"}
            ]
        }
    }
    
    data2 = {
        "schedule": {
            "30.11.25": [
                {"shutdown": "04:00–07:00", "status": "відключення"}
            ]
        }
    }
    
    # Same data should produce same hash
    assert get_schedule_hash_compact(data1) == get_schedule_hash_compact(data2)
    
    # Different data should produce different hash
    data3 = {
        "schedule": {
            "30.11.25": [
                {"shutdown": "05:00–08:00", "status": "відключення"}
            ]
        }
    }
    
    assert get_schedule_hash_compact(data1) != get_schedule_hash_compact(data3)


if __name__ == "__main__":
    test_empty_schedule_notification_logic()
    test_hash_consistency()
    print("All tests passed!")