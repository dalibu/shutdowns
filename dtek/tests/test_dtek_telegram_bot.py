import pytest
import asyncio
import hashlib
import sys
import os
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, MagicMock, patch, call
import aiosqlite

# Добавляем родительскую директорию в путь для импорта
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Импортируем функции из основного модуля
try:
    from dtek_telegram_bot import (
        format_minutes_to_hh_m,
        _process_single_day_schedule,
        format_shutdown_message,
        parse_address_from_text,
        _pluralize_hours,
        _get_shutdown_duration_str_by_hours,
        _get_schedule_hash,
        _get_captcha_data,
        get_shutdowns_data,
        init_db,
        HUMAN_USERS,
        ADDRESS_CACHE,
    )
except ImportError:
    # Альтернативный путь импорта, если модуль в той же директории
    import dtek_telegram_bot
    format_minutes_to_hh_m = dtek_telegram_bot.format_minutes_to_hh_m
    _process_single_day_schedule = dtek_telegram_bot._process_single_day_schedule
    format_shutdown_message = dtek_telegram_bot.format_shutdown_message
    parse_address_from_text = dtek_telegram_bot.parse_address_from_text
    _pluralize_hours = dtek_telegram_bot._pluralize_hours
    _get_shutdown_duration_str_by_hours = dtek_telegram_bot._get_shutdown_duration_str_by_hours
    _get_schedule_hash = dtek_telegram_bot._get_schedule_hash
    _get_captcha_data = dtek_telegram_bot._get_captcha_data
    get_shutdowns_data = dtek_telegram_bot.get_shutdowns_data
    init_db = dtek_telegram_bot.init_db
    HUMAN_USERS = dtek_telegram_bot.HUMAN_USERS
    ADDRESS_CACHE = dtek_telegram_bot.ADDRESS_CACHE


# ============================================================
# ТЕСТЫ ФОРМАТИРОВАНИЯ ВРЕМЕНИ И МИНУТ
# ============================================================

@pytest.mark.unit
@pytest.mark.format
class TestFormatMinutesToHHMM:
    """Тесты для функции format_minutes_to_hh_m"""
    
    def test_zero_minutes(self):
        assert format_minutes_to_hh_m(0) == "00:00"
    
    def test_full_hours(self):
        assert format_minutes_to_hh_m(60) == "01:00"
        assert format_minutes_to_hh_m(120) == "02:00"
    
    def test_minutes_with_remainder(self):
        assert format_minutes_to_hh_m(90) == "01:30"
        assert format_minutes_to_hh_m(125) == "02:05"
    
    def test_large_values(self):
        assert format_minutes_to_hh_m(1440) == "24:00"  # Целый день


# ============================================================
# ТЕСТЫ ПЛЮРАЛИЗАЦИИ УКРАИНСКОГО ЯЗЫКА
# ============================================================

@pytest.mark.unit
@pytest.mark.format
class TestPluralizeHours:
    """Тесты для функции _pluralize_hours"""
    
    def test_decimal_values(self):
        """Дробные числа всегда 'години'"""
        assert _pluralize_hours(0.5) == "години"
        assert _pluralize_hours(1.5) == "години"
        assert _pluralize_hours(2.5) == "години"
    
    def test_one(self):
        """1, 21, 31... - 'годину'"""
        assert _pluralize_hours(1.0) == "годину"
        assert _pluralize_hours(21.0) == "годину"
        assert _pluralize_hours(101.0) == "годину"
    
    def test_two_to_four(self):
        """2-4, 22-24... - 'години'"""
        assert _pluralize_hours(2.0) == "години"
        assert _pluralize_hours(3.0) == "години"
        assert _pluralize_hours(4.0) == "години"
        assert _pluralize_hours(22.0) == "години"
    
    def test_eleven_to_fourteen_exception(self):
        """11-14 - исключение, всегда 'годин'"""
        assert _pluralize_hours(11.0) == "годин"
        assert _pluralize_hours(12.0) == "годин"
        assert _pluralize_hours(14.0) == "годин"
        assert _pluralize_hours(111.0) == "годин"
    
    def test_zero_and_five_plus(self):
        """0, 5-10, 15-20... - 'годин'"""
        assert _pluralize_hours(0.0) == "годин"
        assert _pluralize_hours(5.0) == "годин"
        assert _pluralize_hours(10.0) == "годин"
        assert _pluralize_hours(100.0) == "годин"


# ============================================================
# ТЕСТЫ ФОРМАТИРОВАНИЯ ДЛИТЕЛЬНОСТИ
# ============================================================

@pytest.mark.unit
@pytest.mark.format
class TestGetShutdownDurationStr:
    """Тесты для функции _get_shutdown_duration_str_by_hours"""
    
    def test_zero_hours(self):
        assert _get_shutdown_duration_str_by_hours(0) == "0 годин"
    
    def test_negative_hours(self):
        assert _get_shutdown_duration_str_by_hours(-1) == "0 годин"
    
    def test_whole_hours(self):
        assert _get_shutdown_duration_str_by_hours(1.0) == "1 годину"
        assert _get_shutdown_duration_str_by_hours(2.0) == "2 години"
        assert _get_shutdown_duration_str_by_hours(5.0) == "5 годин"
    
    def test_fractional_hours(self):
        result = _get_shutdown_duration_str_by_hours(0.5)
        assert "0,5" in result
        assert "години" in result
        
        result = _get_shutdown_duration_str_by_hours(2.5)
        assert "2,5" in result


# ============================================================
# ТЕСТЫ ОБРАБОТКИ РАСПИСАНИЯ
# ============================================================

@pytest.mark.unit
class TestProcessSingleDaySchedule:
    """Тесты для функции _process_single_day_schedule"""
    
    def test_no_outages(self):
        """Нет отключений"""
        slots = [
            {"time": "0-1", "disconection": "no"},
            {"time": "1-2", "disconection": "no"}
        ]
        result = _process_single_day_schedule("12.11.24", slots)
        assert "Відключення не заплановані" in result
    
    def test_empty_slots(self):
        """Пустой список слотов"""
        result = _process_single_day_schedule("12.11.24", [])
        assert "Відключення не заплановані" in result
    
    def test_single_full_slot(self):
        """Один полный слот отключения"""
        slots = [{"time": "10-11", "disconection": "full"}]
        result = _process_single_day_schedule("12.11.24", slots)
        assert "10:00 - 11:00" in result
        assert "1 годину" in result
    
    def test_consecutive_full_slots(self):
        """Несколько последовательных полных слотов"""
        slots = [
            {"time": "10-11", "disconection": "full"},
            {"time": "11-12", "disconection": "full"},
            {"time": "12-13", "disconection": "full"}
        ]
        result = _process_single_day_schedule("12.11.24", slots)
        assert "10:00 - 13:00" in result
        assert "3 години" in result
    
    def test_half_slot(self):
        """Половинный слот (вторая половина часа)"""
        slots = [{"time": "10-11", "disconection": "half"}]
        result = _process_single_day_schedule("12.11.24", slots)
        assert "10:30 - 11:00" in result
        assert "0,5 години" in result
    
    def test_mixed_slots_with_gap(self):
        """Слоты с разрывом"""
        slots = [
            {"time": "10-11", "disconection": "full"},
            {"time": "11-12", "disconection": "full"},
            {"time": "14-15", "disconection": "full"}  # Разрыв после 12
        ]
        result = _process_single_day_schedule("12.11.24", slots)
        # Должно быть 2 группы
        assert "10:00 - 12:00" in result
        assert "14:00 - 15:00" in result
    
    def test_end_hour_zero_means_24(self):
        """Обработка 23-00 (00 = 24)"""
        slots = [{"time": "23-0", "disconection": "full"}]
        result = _process_single_day_schedule("12.11.24", slots)
        assert "23:00" in result
        # 24:00 или 00:00 в зависимости от реализации
        assert ("24:00" in result or "00:00" in result)
    
    def test_mixed_full_and_half_consecutive(self):
        """Последовательные full и half слоты"""
        slots = [
            {"time": "10-11", "disconection": "full"},  # 10:00-11:00
            {"time": "11-12", "disconection": "half"}   # 11:30-12:00 (должны объединиться)
        ]
        result = _process_single_day_schedule("12.11.24", slots)
        # Проверяем что есть время и длительность
        assert "10:00" in result or "10:30" in result
        assert "12:00" in result
    
    def test_invalid_slot_time_format(self):
        """Обработка невалидного формата времени"""
        slots = [
            {"time": "invalid", "disconection": "full"}
        ]
        result = _process_single_day_schedule("12.11.24", slots)
        # Должен обработать ошибку gracefully
        assert isinstance(result, str)


# ============================================================
# ТЕСТЫ ФОРМАТИРОВАНИЯ СООБЩЕНИЯ
# ============================================================

@pytest.mark.unit
@pytest.mark.format
class TestFormatShutdownMessage:
    """Тесты для функции format_shutdown_message"""
    
    def test_no_schedule(self, sample_empty_schedule):
        """Нет расписания"""
        result = format_shutdown_message(sample_empty_schedule)
        assert "Дніпро" in result
        assert "3.1" in result
        assert "Не вдалося отримати графік" in result
    
    def test_with_schedule(self, sample_schedule_data):
        """С расписанием"""
        result = format_shutdown_message(sample_schedule_data)
        assert "Дніпро" in result
        assert "Сонячна набережна" in result
        assert "6" in result
        assert "3.1" in result
        assert "12.11.24" in result
        assert "13.11.24" in result
    
    def test_message_contains_markdown(self, sample_schedule_data):
        """Сообщение содержит Markdown форматирование"""
        result = format_shutdown_message(sample_schedule_data)
        assert "`" in result  # Backticks для моноширинного текста
        assert "**" in result or "*" in result  # Жирный или курсив
    
    def test_dates_are_sorted(self):
        """Даты отображаются в правильном порядке"""
        data = {
            "city": "Дніпро",
            "street": "Сонячна",
            "house_num": "6",
            "group": "3.1",
            "schedule": {
                "15.11.24": [{"time": "10-11", "disconection": "full"}],
                "12.11.24": [{"time": "14-15", "disconection": "full"}],
                "13.11.24": [{"time": "16-17", "disconection": "full"}]
            }
        }
        result = format_shutdown_message(data)
        
        # Проверяем что 12.11 идет раньше чем 15.11 в тексте
        pos_12 = result.find("12.11.24")
        pos_15 = result.find("15.11.24")
        assert pos_12 < pos_15, "Dates should be in chronological order"


# ============================================================
# ТЕСТЫ ПАРСИНГА АДРЕСА
# ============================================================

@pytest.mark.unit
class TestParseAddressFromText:
    """Тесты для функции parse_address_from_text"""
    
    def test_valid_address(self):
        """Корректный адрес"""
        city, street, house = parse_address_from_text("м. Дніпро, вул. Сонячна набережна, 6")
        assert city == "м. Дніпро"
        assert street == "вул. Сонячна набережна"
        assert house == "6"
    
    def test_with_command_prefix(self):
        """Адрес с командой в начале"""
        city, street, house = parse_address_from_text("/check Дніпро, Сонячна, 6")
        assert city == "Дніпро"
        assert street == "Сонячна"
        assert house == "6"
    
    def test_insufficient_parts(self):
        """Недостаточно частей адреса"""
        with pytest.raises(ValueError) as exc_info:
            parse_address_from_text("Дніпро, Сонячна")
        assert "Місто, Вулиця, Будинок" in str(exc_info.value)
    
    def test_empty_string(self):
        """Пустая строка"""
        with pytest.raises(ValueError):
            parse_address_from_text("")
    
    def test_extra_spaces(self):
        """Лишние пробелы"""
        city, street, house = parse_address_from_text("  Дніпро  ,  Сонячна  ,  6  ")
        assert city == "Дніпро"
        assert street == "Сонячна"
        assert house == "6"
    
    def test_multiple_commands_in_text(self):
        """Несколько команд в тексте (должны удалиться все)"""
        city, street, house = parse_address_from_text("/check Дніпро, /subscribe Сонячна, 6")
        assert city == "Дніпро"
        # После удаления /check и /subscribe строка: "Дніпро,  Сонячна, 6"
        # Парсинг: city = "Дніпро", street = "Сонячна", house = "6"
        assert street == "Сонячна"
        assert house == "6"
            
    def test_address_with_numbers_in_street(self):
        """Номера в названии улицы"""
        city, street, house = parse_address_from_text("Дніпро, вул. 8 Березня, 10")
        assert city == "Дніпро"
        assert street == "вул. 8 Березня"
        assert house == "10"
    
    def test_house_with_letter(self):
        """Номер дома с буквой"""
        city, street, house = parse_address_from_text("Дніпро, Сонячна, 6А")
        assert city == "Дніпро"
        assert street == "Сонячна"
        assert house == "6А"


# ============================================================
# ТЕСТЫ ГЕНЕРАЦИИ ХЕША РАСПИСАНИЯ
# ============================================================

@pytest.mark.unit
class TestGetScheduleHash:
    """Тесты для функции _get_schedule_hash"""
    
    def test_no_schedule(self):
        """Нет расписания"""
        data = {"schedule": {}}
        result = _get_schedule_hash(data)
        assert result == "NO_SCHEDULE_FOUND"
    
    def test_same_schedule_same_hash(self):
        """Одинаковое расписание дает одинаковый хеш"""
        data1 = {
            "schedule": {
                "12.11.24": [{"time": "10-11", "disconection": "full"}]
            }
        }
        data2 = {
            "schedule": {
                "12.11.24": [{"time": "10-11", "disconection": "full"}]
            }
        }
        assert _get_schedule_hash(data1) == _get_schedule_hash(data2)
    
    def test_different_schedule_different_hash(self):
        """Разное расписание дает разный хеш"""
        data1 = {
            "schedule": {
                "12.11.24": [{"time": "10-11", "disconection": "full"}]
            }
        }
        data2 = {
            "schedule": {
                "12.11.24": [{"time": "11-12", "disconection": "full"}]
            }
        }
        assert _get_schedule_hash(data1) != _get_schedule_hash(data2)
    
    def test_hash_is_stable(self):
        """Хеш стабилен при повторных вызовах"""
        data = {
            "schedule": {
                "12.11.24": [{"time": "10-11", "disconection": "full"}]
            }
        }
        hash1 = _get_schedule_hash(data)
        hash2 = _get_schedule_hash(data)
        assert hash1 == hash2


# ============================================================
# ТЕСТЫ CAPTCHA
# ============================================================

@pytest.mark.unit
@pytest.mark.captcha
class TestGetCaptchaData:
    """Тесты для функции _get_captcha_data"""
    
    def test_returns_question_and_answer(self):
        """Возвращает вопрос и ответ"""
        question, answer = _get_captcha_data()
        assert isinstance(question, str)
        assert isinstance(answer, int)
        assert "?" in question
    
    def test_answer_is_correct(self):
        """Ответ математически корректен"""
        question, answer = _get_captcha_data()
        
        # Извлекаем числа и операцию из вопроса
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
        """Несколько вызовов дают разные вопросы (с высокой вероятностью)"""
        questions = set()
        for _ in range(10):
            question, _ = _get_captcha_data()
            questions.add(question)
        
        # Должно быть хотя бы несколько разных вопросов
        assert len(questions) > 3
    
    def test_captcha_answer_range(self):
        """Проверка диапазона ответов"""
        for _ in range(20):
            question, answer = _get_captcha_data()
            # Ответ должен быть разумным (от -10 до 30 примерно)
            assert -10 <= answer <= 30, f"Answer {answer} is out of expected range"


# ============================================================
# ТЕСТЫ API ИНТЕГРАЦИИ (С МОКАМИ)
# ============================================================

@pytest.mark.api
class TestGetShutdownsData:
    """Тесты для функции get_shutdowns_data (через мок _fetch_shutdowns_data_from_api)"""

    @pytest.mark.asyncio
    async def test_successful_api_call(self):
        """Успешный вызов API"""
        mock_response_data = {
            "city": "Дніпро",
            "street": "Сонячна",
            "house_num": "6",
            "group": "3.1",
            "schedule": {}
        }

        # Мокаем внутреннюю функцию, которая делает HTTP-запрос
        with patch('dtek_telegram_bot._fetch_shutdowns_data_from_api', new=AsyncMock()) as mock_fetch:
            mock_fetch.return_value = mock_response_data

            # Вызываем функцию
            result = await get_shutdowns_data("Дніпро", "Сонячна", "6")

            assert result == mock_response_data
            mock_fetch.assert_called_once_with("Дніпро", "Сонячна", "6")

    @pytest.mark.asyncio
    async def test_api_404_error(self):
        """Обработка 404 ошибки"""
        with patch('dtek_telegram_bot._fetch_shutdowns_data_from_api', new=AsyncMock()) as mock_fetch:
            mock_fetch.side_effect = ValueError("Графік для цієї адреси не знайдено.")

            with pytest.raises(ValueError) as exc_info:
                await get_shutdowns_data("Невідоме", "місто", "0")

            assert "не знайдено" in str(exc_info.value).lower()
            mock_fetch.assert_called_once_with("Невідоме", "місто", "0")

    @pytest.mark.asyncio
    async def test_api_timeout(self):
        """Обработка таймаута"""
        with patch('dtek_telegram_bot._fetch_shutdowns_data_from_api', new=AsyncMock()) as mock_fetch:
            mock_fetch.side_effect = ConnectionError("Таймаут запроса к API.")

            with pytest.raises(ConnectionError) as exc_info:
                await get_shutdowns_data("Дніпро", "Сонячна", "6")

            assert "Таймаут" in str(exc_info.value) or "таймаут" in str(exc_info.value).lower()
            mock_fetch.assert_called_once_with("Дніпро", "Сонячна", "6")

    @pytest.mark.asyncio
    async def test_api_connection_error(self):
        """Обработка ошибки подключения"""
        with patch('dtek_telegram_bot._fetch_shutdowns_data_from_api', new=AsyncMock()) as mock_fetch:
            mock_fetch.side_effect = ConnectionError("Помилка підключення до парсера.")

            with pytest.raises(ConnectionError) as exc_info:
                await get_shutdowns_data("Дніпро", "Сонячна", "6")

            assert "помилка" in str(exc_info.value).lower() or "connection" in str(exc_info.value).lower()
            mock_fetch.assert_called_once_with("Дніпро", "Сонячна", "6")
            
# ============================================================
# ТЕСТЫ БАЗЫ ДАННЫХ
# ============================================================

@pytest.mark.db
class TestInitDB:
    """Тесты для функции init_db"""
    
    @pytest.mark.asyncio
    async def test_creates_database(self, tmp_path):
        """Создает базу данных"""
        db_path = tmp_path / "test.db"
        
        conn = await init_db(str(db_path))
        
        assert db_path.exists()
        
        # Проверяем, что таблицы созданы
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
        """Создает директорию если её нет"""
        db_path = tmp_path / "subdir" / "test.db"
        
        conn = await init_db(str(db_path))
        
        assert db_path.exists()
        assert db_path.parent.exists()
        
        await conn.close()


# ============================================================
# ТЕСТЫ ГЛОБАЛЬНЫХ ПЕРЕМЕННЫХ И КЕША
# ============================================================

class TestGlobalCache:
    """Тесты для глобальных кешей"""
    
    def test_human_users_cache_structure(self):
        """Структура кеша HUMAN_USERS"""
        assert isinstance(HUMAN_USERS, dict)
        # Можем добавить пользователя
        HUMAN_USERS[12345] = True
        assert 12345 in HUMAN_USERS
        # Очищаем после теста
        HUMAN_USERS.pop(12345, None)
    
    def test_address_cache_structure(self):
        """Структура кеша ADDRESS_CACHE"""
        assert isinstance(ADDRESS_CACHE, dict)
        # Можем добавить адрес
        key = ("Дніпро", "Сонячна", "6")
        ADDRESS_CACHE[key] = {
            'last_schedule_hash': 'test_hash',
            'last_checked': datetime.now()
        }
        assert key in ADDRESS_CACHE
        # Очищаем после теста
        ADDRESS_CACHE.pop(key, None)


# ============================================================
# ИНТЕГРАЦИОННЫЕ ТЕСТЫ
# ============================================================

@pytest.mark.integration
class TestIntegration:
    """Интеграционные тесты для комплексных сценариев"""
    
    @pytest.mark.asyncio
    async def test_full_schedule_processing_pipeline(self):
        """Полный пайплайн обработки расписания"""
        # 1. Данные от API
        api_data = {
            "city": "Дніпро",
            "street": "Сонячна",
            "house_num": "6",
            "group": "3.1",
            "schedule": {
                "12.11.24": [
                    {"time": "10-11", "disconection": "full"},
                    {"time": "11-12", "disconection": "full"}
                ],
                "13.11.24": [
                    {"time": "0-1", "disconection": "no"}
                ]
            }
        }
        
        # 2. Генерация хеша
        hash1 = _get_schedule_hash(api_data)
        assert hash1 != "NO_SCHEDULE_FOUND"
        
        # 3. Форматирование сообщения
        message = format_shutdown_message(api_data)
        assert "Дніпро" in message
        assert "12.11.24" in message
        assert "10:00 - 12:00" in message
        
        # 4. Проверка стабильности хеша
        hash2 = _get_schedule_hash(api_data)
        assert hash1 == hash2
        
        # 5. Изменение расписания меняет хеш
        api_data["schedule"]["12.11.24"].append(
            {"time": "12-13", "disconection": "full"}
        )
        hash3 = _get_schedule_hash(api_data)
        assert hash3 != hash1


# ============================================================
# ДОПОЛНИТЕЛЬНЫЕ ФИКСТУРЫ (Основные в conftest.py)
# ============================================================

@pytest.fixture
def sample_schedule_data():
    """Пример данных расписания для тестов"""
    return {
        "city": "Дніпро",
        "street": "Сонячна набережна",
        "house_num": "6",
        "group": "3.1",
        "schedule": {
            "12.11.24": [
                {"time": "10-11", "disconection": "full"},
                {"time": "11-12", "disconection": "full"}
            ],
            "13.11.24": [
                {"time": "14-15", "disconection": "half"}
            ]
        }
    }


@pytest.fixture
def sample_empty_schedule():
    """Пример данных без расписания"""
    return {
        "city": "Дніпро",
        "street": "Сонячна",
        "house_num": "6",
        "group": "3.1",
        "schedule": {}
    }


# ============================================================
# ЗАПУСК ТЕСТОВ
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])