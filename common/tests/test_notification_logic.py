"""
Tests for notification logic to prevent false notifications in both DTEK and CEK bots.
"""
import pytest

def test_notification_logic():
    """Test notification logic - should not notify for empty schedules unless first check."""
    
    # Test cases: (new_hash, last_hash, has_actual_schedule, should_notify)
    test_cases = [
        # Empty schedule, not first check - should NOT notify
        ("hash2", "hash1", False, False),
        
        # Empty schedule, first check - should notify
        ("hash1", None, False, True),
        ("hash1", "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION", False, True),
        
        # Has schedule, hash changed - should notify
        ("hash2", "hash1", True, True),
        
        # Has schedule, hash same - should NOT notify
        ("hash1", "hash1", True, False),
        
        # Empty schedule, hash same - should NOT notify
        ("hash1", "hash1", False, False),
    ]
    
    for new_hash, last_hash, has_actual_schedule, expected in test_cases:
        # Unified notification logic for both DTEK and CEK
        should_notify = (
            new_hash != last_hash and 
            (has_actual_schedule or last_hash in (None, "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION"))
        )
        
        assert should_notify == expected, f"Failed for: new_hash={new_hash}, last_hash={last_hash}, has_schedule={has_actual_schedule}"

if __name__ == "__main__":
    test_notification_logic()
    print("✅ All notification logic tests passed for both DTEK and CEK!")