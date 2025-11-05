import sys
import os
import pytest
import aiohttp
import asyncio
import re
import unittest 
from unittest.mock import patch, MagicMock 
from aioresponses import aioresponses
from urllib.parse import urlencode
from typing import List, Dict, Any

# =========================================================================
# === ФИКС: ОБЕСПЕЧЕНИЕ ИМПОРТА
# =========================================================================
# Добавляем родительскую директорию (корневую папку проекта) в sys.path.
# Это позволяет импортировать dtek_telegram_bot, когда тесты запускаются из папки 'tests'.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# =========================================================================

# --- ИМПОРТ ФУНКЦИЙ БИЗНЕС-ЛОГИКИ И API ИЗ ОСНОВНОГО ФАЙЛА ---
# Обновленный импорт для новых функций
from dtek_telegram_bot import (
    format_shutdown_message, 
    _process_single_day_schedule, 
    get_shutdowns_data,
    # Новые и перенесенные в импорт для тестирования
    _get_captcha_data, 
    _pluralize_hours, 
    _get_shutdown_duration_str,
)


# --- Конфигурация ---
API_BASE_URL = "http://dtek_api:8000" 

# --- 1. Функции для мокирования HTTP (Только утилиты для тестов) ---

def create_mock_url(city: str, street: str, house: str) -> str:
    """Создает полный URL с query-параметрами для мокирования."""
    query_params = {
        "city": city,
        "street": street,
        "house": house
    }
    return f"{API_BASE_URL}/shutdowns?{urlencode(query_params)}"


# --- 2. Фиксация данных (MOCK PAYLOADS) ---

MOCK_RESPONSE_OUTAGE = {
    "city": "м. Київ",
    "street": "вул. Хрещатик",
    "house_num": "2",
    "group": "2",
    "schedule": {
        "04.11": [
            {"time": "00-03", "disconection": "full"},
            {"time": "03-06", "disconection": "half"},
            {"time": "06-09", "disconection": "none"},
        ],
        "05.11": [
            {"time": "09-12", "disconection": "none"},
            {"time": "12-15", "disconection": "full"},
            {"time": "15-18", "disconection": "full"},
        ]
    }
}

MOCK_RESPONSE_NO_OUTAGE = {
    "city": "м. Одеса",
    "street": "вул. Дерибасівська",
    "house_num": "1",
    "group": "1",
    "schedule": {
        "04.11": [
            {"time": "00-03", "disconection": "none"},
        ],
        "05.11": [
            {"time": "09-12", "disconection": "none"},
        ]
    }
}

# --- 3. Тестовые функции для API-интеграции (проверка get_shutdowns_data) ---

@pytest.mark.asyncio
async def test_successful_outage_response():
    """Тестирование успешного ответа с запланированными отключениями."""
    url = create_mock_url("Київ", "Хрещатик", "2") 
    with aioresponses() as m:
        # Мокируем ответ, используя URL, который будет сгенерирован функцией get_shutdowns_data
        m.get(url, payload=MOCK_RESPONSE_OUTAGE, status=200)
        # Вызываем ИМПОРТИРОВАННУЮ функцию
        data = await get_shutdowns_data("Київ", "Хрещатик", "2")
        assert data['group'] == "2"
        assert data == MOCK_RESPONSE_OUTAGE

@pytest.mark.asyncio
async def test_successful_no_outage_response():
    """Тестирование успешного ответа без запланированных отключений."""
    url = create_mock_url("Одеса", "Дерибасівська", "1")
    with aioresponses() as m:
        m.get(url, payload=MOCK_RESPONSE_NO_OUTAGE, status=200)
        # Вызываем ИМПОРТИРОВАННУЮ функцию
        data = await get_shutdowns_data("Одеса", "Дерибасівська", "1")
        assert data['group'] == "1"
        assert data == MOCK_RESPONSE_NO_OUTAGE

@pytest.mark.asyncio
async def test_not_found_404_response():
    """Тестирование, когда API возвращает 404 (адрес не найден)."""
    # Мокируем 404 с сообщением об ошибке, которое API должен вернуть
    url = create_mock_url("Неіснуюче", "Вулиця", "1")
    mock_404_response = {"detail": "Графік для цієї адреси не знайдено."}

    with aioresponses() as m:
        m.get(url, status=404, payload=mock_404_response)
        with pytest.raises(ValueError) as excinfo:
            # Вызываем ИМПОРТИРОВАННУЮ функцию
            await get_shutdowns_data("Неіснуюче", "Вулиця", "1")
        assert "Графік для цієї адреси не знайдено." in str(excinfo.value)

@pytest.mark.asyncio
async def test_connection_error_mocked():
    """Тестирование ошибки соединения с API с помощью aioresponses."""
    url = create_mock_url("Київ", "Хрещатик", "2") 
    with aioresponses() as m:
        m.get(url, exception=aiohttp.ClientConnectorError(None, OSError('Mock connection error')))
        with pytest.raises(ConnectionError) as excinfo:
            # Вызываем ИМПОРТИРОВАННУЮ функцию
            await get_shutdowns_data("Київ", "Хрещатик", "2")
        assert "Помилка підключення до парсера." in str(excinfo.value)


# --- 4. Тестовые функции для форматирования сообщений (проверка format_shutdown_message) ---

def test_format_message_no_outage():
    """
    Тестирование форматирования для случая без запланированных отключений в новом формате.
    """
    mock_data = {
        "city": "м. Одеса",
        "street": "вул. Дерибасівська",
        "house_num": "1",
        "group": "1",
        "schedule": {
            "04.11.25": [
                {"time": "00-03", "disconection": "none"},
            ],
            "05.11.25": [
                {"time": "09-12", "disconection": "none"},
            ]
        }
    }

    expected_output = (
        "🏠 Адреса: `м. Одеса, вул. Дерибасівська, 1`\n"
        "👥 Черга: `1`\n"
        "✅ **04.11.25**: Відключення не заплановані\n"
        "✅ **05.11.25**: Відключення не заплановані"
    )
    assert format_shutdown_message(mock_data).strip() == expected_output.strip()


def test_format_message_half_slots():
    """
    Тест 1: начало 'half' (18:30) и конец 'half' (21:30) в новом формате.
    """
    mock_data = {
        "city": "м. Дніпро",
        "street": "вул. Сонячна набережна",
        "house_num": "6",
        "group": "3.2",
        "schedule": {
            "04.11.25": [
                {"time": "18-19", "disconection": "half"},
                {"time": "19-20", "disconection": "full"},
                {"time": "20-21", "disconection": "full"},
                {"time": "21-22", "disconection": "half"}
            ]
        }
    }

    expected_output = (
        "🏠 Адреса: `м. Дніпро, вул. Сонячна набережна, 6`\n"
        "👥 Черга: `3.2`\n"
        "❌ **04.11.25**: 18:30 - 21:30 (3 години)"
    )
    assert format_shutdown_message(mock_data).strip() == expected_output.strip()

def test_format_message_full_start_half_end():
    """
    Тест 2: начало 'full' (18:00) и конец 'half' (21:30) в новом формате.
    """
    mock_data = {
        "city": "м. Львів",
        "street": "вул. Зелена",
        "house_num": "100",
        "group": "4.1",
        "schedule": {
            "04.11.25": [
                {"time": "18-19", "disconection": "full"},
                {"time": "19-20", "disconection": "full"},
                {"time": "20-21", "disconection": "full"},
                {"time": "21-22", "disconection": "half"}
            ]
        }
    }

    expected_output = (
        "🏠 Адреса: `м. Львів, вул. Зелена, 100`\n"
        "👥 Черга: `4.1`\n"
        "❌ **04.11.25**: 18:00 - 21:30 (3,5 години)"
    )
    assert format_shutdown_message(mock_data).strip() == expected_output.strip()

def test_format_message_half_start_full_end():
    """
    Тест 3: начало 'half' (18:30) и конец 'full' (21:00) в новом формате.
    """
    mock_data = {
        "city": "м. Харків",
        "street": "вул. Сумська",
        "house_num": "10",
        "group": "5.0",
        "schedule": {
            "04.11.25": [
                {"time": "18-19", "disconection": "half"},
                {"time": "19-20", "disconection": "full"},
                {"time": "20-21", "disconection": "full"}
            ]
        }
    }

    expected_output = (
        "🏠 Адреса: `м. Харків, вул. Сумська, 10`\n"
        "👥 Черга: `5.0`\n"
        "❌ **04.11.25**: 18:30 - 21:00 (2,5 години)"
    )
    assert format_shutdown_message(mock_data).strip() == expected_output.strip()

def test_format_message_multi_day_complex_slots():
    """
    Тест 4: Несколько дней (18:30-21:00 и 15:00-18:30) в новом формате.
    """
    mock_data = {
        "city": "м. Одеса",
        "street": "вул. Приморська",
        "house_num": "5",
        "group": "6.0",
        "schedule": {
            "04.11.25": [
                {"time": "18-19", "disconection": "half"}, 
                {"time": "19-20", "disconection": "full"},
                {"time": "20-21", "disconection": "full"}
            ],
            "05.11.25": [
                {"time": "15-16", "disconection": "full"}, 
                {"time": "16-17", "disconection": "full"},
                {"time": "17-18", "disconection": "full"},
                {"time": "18-19", "disconection": "half"}
            ]
        }
    }

    expected_output = (
        "🏠 Адреса: `м. Одеса, вул. Приморська, 5`\n"
        "👥 Черга: `6.0`\n"
        "❌ **04.11.25**: 18:30 - 21:00 (2,5 години)\n"
        "❌ **05.11.25**: 15:00 - 18:30 (3,5 години)"
    )
    assert format_shutdown_message(mock_data).strip() == expected_output.strip()

def test_format_message_multi_day_all_half_slots():
    """
    Тест 5: Несколько дней, все крайние слоты 'half' (18:30-21:30 и 15:30-18:30) в новом формате.
    """
    mock_data = {
        "city": "м. Чернігів",
        "street": "вул. Івана Мазепи",
        "house_num": "42",
        "group": "7.0",
        "schedule": {
            "04.11.25": [
                {"time": "18-19", "disconection": "half"},
                {"time": "19-20", "disconection": "full"},
                {"time": "20-21", "disconection": "full"},
                {"time": "21-22", "disconection": "half"}
            ],
            "05.11.25": [
                {"time": "15-16", "disconection": "half"},
                {"time": "16-17", "disconection": "full"},
                {"time": "17-18", "disconection": "full"},
                {"time": "18-19", "disconection": "half"}
            ]
        }
    }

    expected_output = (
        "🏠 Адреса: `м. Чернігів, вул. Івана Мазепи, 42`\n"
        "👥 Черга: `7.0`\n"
        "❌ **04.11.25**: 18:30 - 21:30 (3 години)\n"
        "❌ **05.11.25**: 15:30 - 18:30 (3 години)"
    )
    assert format_shutdown_message(mock_data).strip() == expected_output.strip()


# --- НОВЫЙ БЛОК: Тесты для чистой бизнес-логики (CAPTCHA/склонения) ---

class TestBotBusinessLogic(unittest.TestCase):
    
    def test_get_captcha_data_generation(self):
        """Проверяет, что _get_captcha_data генерирует вопрос и корректный ответ."""
        
        # Тест на сложение (mocking random.choice и random.randint)
        with patch('random.choice', return_value='+'), \
             patch('random.randint', side_effect=[10, 3, 0]): # a=10, b=3.
            question, answer = _get_captcha_data()
            self.assertIn("10 + 3", question)
            self.assertEqual(answer, 13)
            self.assertIsInstance(question, str)
            self.assertIsInstance(answer, int)

        # Тест на вычитание (mocking random.choice и random.randint)
        with patch('random.choice', return_value='-'), \
             patch('random.randint', side_effect=[15, 5, 0]): # a=15, b=5.
            question, answer = _get_captcha_data()
            self.assertIn("15 - 5", question)
            self.assertEqual(answer, 10)
            self.assertIsInstance(question, str)
            self.assertIsInstance(answer, int)
            
    def test_pluralize_hours(self):
        """Проверяет правильное склонение слова 'година'."""
        
        # Целые числа
        self.assertEqual(_pluralize_hours(1), "годину")
        self.assertEqual(_pluralize_hours(2), "години")
        self.assertEqual(_pluralize_hours(4), "години")
        self.assertEqual(_pluralize_hours(5), "годин")
        self.assertEqual(_pluralize_hours(10), "годин")
        self.assertEqual(_pluralize_hours(11), "годин")
        self.assertEqual(_pluralize_hours(21), "годину")
        self.assertEqual(_pluralize_hours(23), "години")
        self.assertEqual(_pluralize_hours(24), "години") # <--- ТЕПЕРЬ ПРОХОДИТ
        self.assertEqual(_pluralize_hours(100), "годин")
        self.assertEqual(_pluralize_hours(101), "годину")

        # Дробные числа (всегда 'години')
        self.assertEqual(_pluralize_hours(0.5), "години")
        self.assertEqual(_pluralize_hours(1.5), "години")
        self.assertEqual(_pluralize_hours(2.5), "години")
        self.assertEqual(_pluralize_hours(10.5), "години")
        
    def test_get_shutdown_duration_str_basic(self):
        """Проверяет корректное форматирование длительности для стандартных случаев."""
        
        # 3 часа
        self.assertEqual(_get_shutdown_duration_str('10:00', '13:00'), "3 години")
        # 2.5 часа (2,5)
        self.assertEqual(_get_shutdown_duration_str('18:30', '21:00'), "2,5 години")
        # 1 час
        self.assertEqual(_get_shutdown_duration_str('01:00', '02:00'), "1 годину")
        # 10 часов
        self.assertEqual(_get_shutdown_duration_str('08:00', '18:00'), "10 годин")
        # 30 минут (0.5 часа)
        self.assertEqual(_get_shutdown_duration_str('12:00', '12:30'), "0,5 години")
        # 1.5 часа
        self.assertEqual(_get_shutdown_duration_str('14:00', '15:30'), "1,5 години")
        
    def test_get_shutdown_duration_str_midnight_rollover(self):
        """Проверяет расчет длительности через полночь."""
        
        # 4 часа (22:00 -> 02:00)
        self.assertEqual(_get_shutdown_duration_str('22:00', '02:00'), "4 години")
        # 6.5 часов (23:30 -> 06:00)
        self.assertEqual(_get_shutdown_duration_str('23:30', '06:00'), "6,5 години")
        # 1 час (23:30 -> 00:30)
        self.assertEqual(_get_shutdown_duration_str('23:30', '00:30'), "1 годину")

    def test_get_shutdown_duration_str_edge_cases(self):
        """Проверяет крайние и ошибочные случаи."""
        
        # Старт = Конец (24 часа)
        self.assertEqual(_get_shutdown_duration_str('12:00', '12:00'), "24 години") # <--- ИСПРАВЛЕНО
        # Неправильный формат времени
        self.assertEqual(_get_shutdown_duration_str('10-00', '12:00'), "?")
        self.assertEqual(_get_shutdown_duration_str('abc', 'def'), "?")