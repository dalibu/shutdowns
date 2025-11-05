import sys
import os
import pytest
import aiohttp
import asyncio
import re
import unittest 
from unittest.mock import patch, MagicMock, AsyncMock
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
from dtek_telegram_bot import (
    format_shutdown_message, 
    _process_single_day_schedule, 
    get_shutdowns_data,
    # Функции для тестирования
    _get_captcha_data, 
    _pluralize_hours, 
    _get_shutdown_duration_str,
    # ИМПОРТЫ ДЛЯ ТЕСТИРОВАНИЯ ХЕНДЛЕРОВ
    command_start_handler,
    captcha_answer_handler,
    command_check_handler,
    CaptchaState, # FSM State
    HUMAN_USERS, # Глобальный кеш
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
        "04.11.25": [
            {"time": "00-03", "disconection": "full"},
            {"time": "03-06", "disconection": "half"},
            {"time": "06-09", "disconection": "none"},
        ],
        "05.11.25": [
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
        "04.11.25": [
            {"time": "00-03", "disconection": "none"},
        ],
        "05.11.25": [
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
        m.get(url, payload=MOCK_RESPONSE_OUTAGE, status=200)
        data = await get_shutdowns_data("Київ", "Хрещатик", "2")
        assert data['group'] == "2"
        assert data == MOCK_RESPONSE_OUTAGE

@pytest.mark.asyncio
async def test_successful_no_outage_response():
    """Тестирование успешного ответа без запланированных отключений."""
    url = create_mock_url("Одеса", "Дерибасівська", "1")
    with aioresponses() as m:
        m.get(url, payload=MOCK_RESPONSE_NO_OUTAGE, status=200)
        data = await get_shutdowns_data("Одеса", "Дерибасівська", "1")
        assert data['group'] == "1"
        assert data == MOCK_RESPONSE_NO_OUTAGE

@pytest.mark.asyncio
async def test_not_found_404_response():
    """Тестирование, когда API возвращает 404 (адрес не найден)."""
    url = create_mock_url("Неіснуюче", "Вулиця", "1")
    mock_404_response = {"detail": "Графік для цієї адреси не знайдено."}

    with aioresponses() as m:
        m.get(url, status=404, payload=mock_404_response)
        with pytest.raises(ValueError) as excinfo:
            await get_shutdowns_data("Неіснуюче", "Вулиця", "1")
        assert "Графік для цієї адреси не знайдено." in str(excinfo.value)

@pytest.mark.asyncio
async def test_connection_error_mocked():
    """Тестирование ошибки соединения с API с помощью aioresponses."""
    url = create_mock_url("Київ", "Хрещатик", "2") 
    with aioresponses() as m:
        m.get(url, exception=aiohttp.ClientConnectorError(None, OSError('Mock connection error')))
        with pytest.raises(ConnectionError) as excinfo:
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


# --- 5. Тесты для чистой бизнес-логики (CAPTCHA/склонения) ---

class TestBotBusinessLogic(unittest.TestCase):
    
    def test_get_captcha_data_generation(self):
        """Проверяет, что _get_captcha_data генерирует вопрос и корректный ответ."""
        
        # Тест на сложение
        with patch('random.choice', return_value='+'), \
             patch('random.randint', side_effect=[10, 3, 0]):
            question, answer = _get_captcha_data()
            self.assertEqual(answer, 13)

        # Тест на вычитание
        with patch('random.choice', return_value='-'), \
             patch('random.randint', side_effect=[15, 5, 0]):
            question, answer = _get_captcha_data()
            self.assertEqual(answer, 10)
            
    def test_pluralize_hours(self):
        """Проверяет правильное склонение слова 'година'."""
        
        # Целые числа
        self.assertEqual(_pluralize_hours(1), "годину")
        self.assertEqual(_pluralize_hours(2), "години")
        self.assertEqual(_pluralize_hours(4), "години")
        self.assertEqual(_pluralize_hours(5), "годин")
        self.assertEqual(_pluralize_hours(11), "годин")
        self.assertEqual(_pluralize_hours(21), "годину")
        self.assertEqual(_pluralize_hours(24), "години")
        self.assertEqual(_pluralize_hours(101), "годину")

        # Дробные числа
        self.assertEqual(_pluralize_hours(0.5), "години")
        self.assertEqual(_pluralize_hours(2.5), "години")
        
    def test_get_shutdown_duration_str_basic(self):
        """[ВОССТАНОВЛЕНО] Проверяет корректное форматирование длительности для стандартных случаев."""
        self.assertEqual(_get_shutdown_duration_str('10:00', '13:00'), "3 години")
        self.assertEqual(_get_shutdown_duration_str('18:30', '21:00'), "2,5 години")
        self.assertEqual(_get_shutdown_duration_str('01:00', '02:00'), "1 годину")
        self.assertEqual(_get_shutdown_duration_str('12:00', '12:30'), "0,5 години")
        self.assertEqual(_get_shutdown_duration_str('08:00', '18:00'), "10 годин")

    def test_get_shutdown_duration_str_midnight_rollover(self):
        """[ВОССТАНОВЛЕНО] Проверяет расчет длительности через полночь."""
        self.assertEqual(_get_shutdown_duration_str('22:00', '02:00'), "4 години")
        self.assertEqual(_get_shutdown_duration_str('23:30', '06:00'), "6,5 години")
        self.assertEqual(_get_shutdown_duration_str('23:30', '00:30'), "1 годину")

    def test_get_shutdown_duration_str_edge_cases(self):
        """Проверяет крайние и ошибочные случаи."""
        
        # Старт = Конец (24 часа)
        self.assertEqual(_get_shutdown_duration_str('12:00', '12:00'), "24 години") 
        # Неправильный формат времени
        self.assertEqual(_get_shutdown_duration_str('10-00', '12:00'), "?")
        self.assertEqual(_get_shutdown_duration_str('abc', 'def'), "?")


# --- 6. ИНТЕГРАЦИОННЫЕ ТЕСТЫ ДЛЯ ХЕНДЛЕРОВ (CAPTCHA + CHECK) ---

class TestBotHandlers(unittest.IsolatedAsyncioTestCase):
    
    # SETUP/TEARDOWN: Важно для очистки глобального состояния
    def setUp(self):
        # Очищаем глобальный кеш перед каждым тестом
        HUMAN_USERS.clear() 

    def tearDown(self):
        # Очищаем глобальный кеш после каждого теста
        HUMAN_USERS.clear() 
        
    async def test_full_check_workflow_with_captcha(self):
        """
        [НОВЫЙ ТЕСТ] Тестирует полный цикл:
        1. /start -> Запуск CAPTCHA.
        2. Ответ CAPTCHA -> Успешное прохождение, запись в HUMAN_USERS.
        3. /check [address] -> Вызов get_shutdowns_data (mocked) и получение ответа.
        """
        # 1. Mock Objects Setup
        user_id = 123
        
        # Message Mocks
        message_start = MagicMock(text="/start", from_user=MagicMock(id=user_id), answer=AsyncMock())
        message_captcha_correct = MagicMock(text="13", from_user=MagicMock(id=user_id), answer=AsyncMock())
        message_check = MagicMock(text="/check м. Київ, вул. Хрещатик, 2", from_user=MagicMock(id=user_id), answer=AsyncMock())
        
        # FSMContext Mock
        fsm_context = AsyncMock()
        fsm_context.get_data.return_value = {"captcha_answer": 13}
        
        # API Mock (Re-using MOCK_RESPONSE_OUTAGE)
        mock_api_data = MOCK_RESPONSE_OUTAGE.copy()
        expected_api_result = format_shutdown_message(mock_api_data)
        
        # 2. CAPTCHA MOCK CONTROL и API MOCK
        with patch('dtek_telegram_bot._get_captcha_data', return_value=("Скільки буде 10 + 3?", 13)), \
             patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock(return_value=mock_api_data)) as mock_get_shutdowns:
            
            # --- ШАГ 1: /start (Запуск CAPTCHA) ---
            await command_start_handler(message_start, fsm_context)
            
            # --- ШАГ 2: Ответ CAPTCHA (Успех) ---
            await captcha_answer_handler(message_captcha_correct, fsm_context)
            self.assertIn(user_id, HUMAN_USERS)
            
            # --- ШАГ 3: /check (Проверка графика) ---
            await command_check_handler(message_check, fsm_context)
            
            # Проверка API:
            mock_get_shutdowns.assert_called_once_with("м. Київ", "вул. Хрещатик", "2")
            
            # Проверка сообщений (Ожидание + Результат)
            self.assertEqual(message_check.answer.call_count, 2)
            final_message = message_check.answer.call_args_list[1][0][0]
            self.assertEqual(final_message.strip(), expected_api_result.strip())