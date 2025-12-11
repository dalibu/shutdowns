"""
Test for notification message formatting.
Ensures that newlines are properly rendered without double escaping.
This is a regression test for the bug where literal \\n appeared in messages.
"""

import pytest
from common.formatting import process_single_day_schedule_compact


def test_notification_message_no_literal_backslash_n():
    """Test that notification message doesn't contain literal \\n characters."""
    
    # Sample schedule data (similar to what parser returns)
    schedule = {
        "11.12.25": [
            {"shutdown": "02:00–09:00"},
            {"shutdown": "12:30–19:00"},
            {"shutdown": "23:00–24:00"}
        ],
        "12.12.25": [
            {"shutdown": "00:00–02:30"},
            {"shutdown": "09:30–16:30"},
            {"shutdown": "20:00–24:00"}
        ]
    }
    
    # Build message parts like in tasks.py
    city = "м. Дніпро"
    street = "вул. Сонячна набережна"
    house = "6"
    group = "3.2"
    interval_str = "1 год"
    
    update_header = "🔔 **ОНОВЛЕННЯ ГРАФІКУ!**"
    address_str = f"`{city}, {street}, {house}`"
    
    message_parts = []
    message_parts.append(f"{update_header}\nдля {address_str} (інтервал {interval_str})")
    message_parts.append(f"📍 Адреса: `{city}, {street}, {house}`\n👥 Черга: `{group}`")
    message_parts.append("🕙 **Загальний графік на 48 годин**")
    
    # Add day schedules
    for date in ["11.12.25", "12.12.25"]:
        slots = schedule[date]
        day_text = process_single_day_schedule_compact(date, slots, "ДТЕК")
        if day_text and day_text.strip():
            message_parts.append(day_text.strip())
    
    # Add status message
    message_parts.append("🟡 Наступне відключення у 23:00")
    
    # Combine all parts (like in tasks.py line 533)
    full_message = "\n\n".join(message_parts)
    
    # Assertions
    # Check 1: Should NOT contain literal \n characters (the bug we're testing for)
    assert "\\n" not in full_message, "Message contains literal \\n characters!"
    
    # Check 2: Should contain actual newlines
    assert "\n" in full_message, "Message doesn't contain any newlines!"
    
    # Check 3: Should have reasonable number of lines
    lines = full_message.split("\n")
    assert len(lines) >= 10, f"Expected at least 10 lines, got {len(lines)}"
    
    # Check 4: No double backslashes
    assert "\\\\" not in full_message, "Message contains double backslashes!"
    
    # Check 5: Message should contain expected content
    assert "🔔" in full_message
    assert "📍 Адреса" in full_message
    assert "👥 Черга" in full_message
    assert "⚫ 11.12.25" in full_message
    assert "⚫ 12.12.25" in full_message
