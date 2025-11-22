"""
Tests for common.bot_base module
"""
import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, MagicMock
import aiosqlite

from common.bot_base import (
    format_minutes_to_hh_mm,
    parse_time_range,
    parse_address_from_text,
    get_shutdown_duration_str_by_hours,
    get_hours_str,
    get_captcha_data,
    get_schedule_hash_compact,
    normalize_schedule_for_hash,
    init_db,
    HUMAN_USERS,
    ADDRESS_CACHE,
    SCHEDULE_DATA_CACHE,
)


# ============================================================
# TIME FORMATTING TESTS
# ============================================================

@pytest.mark.unit
class TestFormatMinutesToHHMM:
    """Tests for format_minutes_to_hh_mm function"""
    
    def test_zero_minutes(self):
        assert format_minutes_to_hh_mm(0) == "00:00"
    
    def test_full_hours(self):
        assert format_minutes_to_hh_mm(60) == "01:00"
        assert format_minutes_to_hh_mm(120) == "02:00"
    
    def test_minutes_with_remainder(self):
        assert format_minutes_to_hh_mm(90) == "01:30"
        assert format_minutes_to_hh_mm(125) == "02:05"
    
    def test_large_values(self):
        assert format_minutes_to_hh_mm(1440) == "24:00"


@pytest.mark.unit
class TestParseTimeRange:
    """Tests for parse_time_range function"""
    
    def test_full_hour_range(self):
        start, end = parse_time_range("10:00–11:00")
        assert start == 600  # 10 * 60
        assert end == 660    # 11 * 60
    
    def test_half_hour_range(self):
        start, end = parse_time_range("10:30–11:00")
        assert start == 630  # 10 * 60 + 30
        assert end == 660
    
    def test_midnight_crossing(self):
        start, end = parse_time_range("23:00–24:00")
        assert start == 1380  # 23 * 60
        assert end == 1440    # 24 * 60


# ============================================================
# DURATION FORMATTING TESTS
# ============================================================

@pytest.mark.unit
class TestGetShutdownDurationStr:
    """Tests for get_shutdown_duration_str_by_hours function"""
    
    def test_zero_hours(self):
        assert get_shutdown_duration_str_by_hours(0) == "0 год."
    
    def test_negative_hours(self):
        assert get_shutdown_duration_str_by_hours(-1) == "0 год."
    
    def test_whole_hours(self):
        assert get_shutdown_duration_str_by_hours(1.0) == "1 год."
        assert get_shutdown_duration_str_by_hours(2.0) == "2 год."
        assert get_shutdown_duration_str_by_hours(5.0) == "5 год."
    
    def test_fractional_hours(self):
        result = get_shutdown_duration_str_by_hours(0.5)
        assert "0,5" in result
        assert "год." in result
        
        result = get_shutdown_duration_str_by_hours(2.5)
        assert "2,5" in result


@pytest.mark.unit
class TestGetHoursStr:
    """Tests for get_hours_str function (Ukrainian declension)"""
    
    def test_one_hour(self):
        assert "годин" in get_hours_str(1) or "год" in get_hours_str(1)
    
    def test_multiple_hours(self):
        result = get_hours_str(2)
        assert isinstance(result, str)
        assert len(result) > 0


# ============================================================
# ADDRESS PARSING TESTS
# ============================================================

@pytest.mark.unit
class TestParseAddressFromText:
    """Tests for parse_address_from_text function"""
    
    def test_valid_address(self):
        city, street, house = parse_address_from_text("м. Дніпро, вул. Сонячна набережна, 6")
        assert city == "м. Дніпро"
        assert street == "вул. Сонячна набережна"
        assert house == "6"
    
    def test_with_command_prefix(self):
        city, street, house = parse_address_from_text("/check Дніпро, Сонячна, 6")
        assert city == "Дніпро"
        assert street == "Сонячна"
        assert house == "6"
    
    def test_insufficient_parts(self):
        with pytest.raises(ValueError) as exc_info:
            parse_address_from_text("Дніпро, Сонячна")
        assert "Місто, Вулиця, Будинок" in str(exc_info.value)
    
    def test_empty_string(self):
        with pytest.raises(ValueError):
            parse_address_from_text("")
    
    def test_extra_spaces(self):
        city, street, house = parse_address_from_text("  Дніпро  ,  Сонячна  ,  6  ")
        assert city == "Дніпро"
        assert street == "Сонячна"
        assert house == "6"
    
    def test_house_with_letter(self):
        city, street, house = parse_address_from_text("Дніпро, Сонячна, 6А")
        assert city == "Дніпро"
        assert street == "Сонячна"
        assert house == "6А"


# ============================================================
# CAPTCHA TESTS
# ============================================================

@pytest.mark.unit
class TestGetCaptchaData:
    """Tests for get_captcha_data function"""
    
    def test_returns_question_and_answer(self):
        question, answer = get_captcha_data()
        assert isinstance(question, str)
        assert isinstance(answer, int)
        assert "?" in question
    
    def test_answer_is_correct(self):
        question, answer = get_captcha_data()
        
        # Extract numbers and operation from question
        if "+" in question:
            parts = question.split("+")
            a = int(parts[0].split()[-1])
            b = int(parts[1].split("?")[0].strip())
            expected = a + b
        else:  # "-"
            parts = question.split("-")
            a = int(parts[0].split()[-1])
            b = int(parts[1].split("?")[0].strip())
            expected = a - b
        
        assert answer == expected
    
    def test_multiple_captcha_calls_are_random(self):
        questions = set()
        for _ in range(10):
            question, _ = get_captcha_data()
            questions.add(question)
        
        # Should have at least several different questions
        assert len(questions) > 3
    
    def test_captcha_answer_range(self):
        for _ in range(20):
            question, answer = get_captcha_data()
            # Answer should be reasonable (roughly -10 to 30)
            assert -10 <= answer <= 30, f"Answer {answer} is out of expected range"


# ============================================================
# SCHEDULE HASHING TESTS
# ============================================================

@pytest.mark.unit
class TestScheduleHashing:
    """Tests for schedule hashing functions"""
    
    def test_normalize_schedule_for_hash(self):
        schedule_data = {
            "schedule": {
                "12.11.24": [
                    {"shutdown": "10:00–11:00"},
                    {"shutdown": "11:00–12:00"}
                ],
                "13.11.24": []
            }
        }
        
        normalized = normalize_schedule_for_hash(schedule_data)
        assert isinstance(normalized, dict)
        assert "12.11.24" in normalized
    
    def test_get_schedule_hash_compact(self):
        data = {
            "schedule": {
                "12.11.24": [{"shutdown": "10:00–11:00"}]
            }
        }
        
        hash1 = get_schedule_hash_compact(data)
        assert isinstance(hash1, str)
        assert len(hash1) > 0
        
        # Same data should produce same hash
        hash2 = get_schedule_hash_compact(data)
        assert hash1 == hash2
    
    def test_different_schedules_different_hashes(self):
        data1 = {
            "schedule": {
                "12.11.24": [{"shutdown": "10:00–11:00"}]
            }
        }
        
        data2 = {
            "schedule": {
                "12.11.24": [{"shutdown": "14:00–15:00"}]
            }
        }
        
        hash1 = get_schedule_hash_compact(data1)
        hash2 = get_schedule_hash_compact(data2)
        assert hash1 != hash2


# ============================================================
# DATABASE TESTS
# ============================================================

@pytest.mark.db
class TestInitDB:
    """Tests for init_db function"""
    
    @pytest.mark.asyncio
    async def test_creates_database(self, tmp_path):
        db_path = tmp_path / "test.db"
        
        # Handle mocked aiosqlite
        is_mocked = isinstance(aiosqlite, MagicMock) or isinstance(aiosqlite, Mock)
        
        if is_mocked:
            mock_conn = AsyncMock()
            aiosqlite.connect.return_value = mock_conn
            mock_cursor = AsyncMock()
            mock_cursor.fetchall.return_value = [('subscriptions',), ('user_last_check',)]
            mock_conn.execute.return_value = mock_cursor
        
        conn = await init_db(str(db_path))
        
        if is_mocked:
            aiosqlite.connect.assert_called_with(str(db_path))
        else:
            assert db_path.exists()
        
        # Check that tables are created
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = await cursor.fetchall()
        table_names = [t[0] for t in tables]
        
        assert "subscriptions" in table_names
        assert "user_last_check" in table_names
        
        await conn.close()
    
    @pytest.mark.asyncio
    async def test_creates_directory_if_not_exists(self, tmp_path):
        db_path = tmp_path / "subdir" / "test.db"
        
        is_mocked = isinstance(aiosqlite, MagicMock) or isinstance(aiosqlite, Mock)
        
        if is_mocked:
            mock_conn = AsyncMock()
            aiosqlite.connect.return_value = mock_conn
        
        conn = await init_db(str(db_path))
        
        if is_mocked:
            aiosqlite.connect.assert_called_with(str(db_path))
            assert db_path.parent.exists()
        else:
            assert db_path.exists()
            assert db_path.parent.exists()
        
        await conn.close()


# ============================================================
# GLOBAL CACHE TESTS
# ============================================================

class TestGlobalCaches:
    """Tests for global caches"""
    
    def test_human_users_cache_structure(self):
        assert isinstance(HUMAN_USERS, dict)
        # Can add user
        HUMAN_USERS[12345] = True
        assert 12345 in HUMAN_USERS
        # Cleanup
        HUMAN_USERS.pop(12345, None)
    
    def test_address_cache_structure(self):
        assert isinstance(ADDRESS_CACHE, dict)
        # Can add address
        key = ("Дніпро", "Сонячна", "6")
        ADDRESS_CACHE[key] = {
            'last_schedule_hash': 'test_hash',
            'last_checked': datetime.now()
        }
        assert key in ADDRESS_CACHE
        # Cleanup
        ADDRESS_CACHE.pop(key, None)
    
    def test_schedule_data_cache_structure(self):
        assert isinstance(SCHEDULE_DATA_CACHE, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
