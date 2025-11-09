import sys
import os
import pytest
import aiohttp
import asyncio
import re
import unittest 
import hashlib 
from unittest.mock import patch, MagicMock, AsyncMock
from aioresponses import aioresponses
from urllib.parse import urlencode
from typing import List, Dict, Any
from datetime import datetime, timedelta 
from aiogram.types import ReplyKeyboardRemove # ДОДАНО для тестів /cancel

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
    _get_shutdown_duration_str_by_hours, # ИЗМЕНЕНО: Исправлен импорт
    _get_schedule_hash, # ДОДАНО: Імпорт функції хешування
    # ИМПОРТЫ ДЛЯ ТЕСТИРОВАНИЯ ХЕНДЛЕРОВ
    command_start_handler,
    captcha_answer_handler,
    command_check_handler,
    command_repeat_handler,
    command_subscribe_handler, # ДОДАНО
    command_unsubscribe_handler, # ДОДАНО
    command_cancel_handler, # ДОДАНО
    subscription_checker_task, # ДОДАНО
    # ДОБАВЛЕНО: Импорт новых FSM-обработчиков
    process_city, 
    process_street, 
    process_house,
    # КОНЕЦ ДОБАВЛЕННОГО БЛОКА
    CaptchaState, # FSM State
    CheckAddressState, # ДОБАВЛЕНО
    HUMAN_USERS, # Глобальный кеш
    SUBSCRIPTIONS, # ДОДАНО: Глобальный кеш подписок
    CHECKER_LOOP_INTERVAL_SECONDS, # ДОДАНО: для імітації часу
)


# --- Конфигурация ---
API_BASE_URL = "http://dtek_api:8000" 

# КОНСТАНТА ДЛЯ ОЖИДАЕМОГО РЕЗУЛЬТАТА: ДОБАВЛЕНО ДЛЯ ИСПРАВЛЕНИЯ ТЕСТА
SUBSCRIBE_PROMPT = "\n\n💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`."

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

MOCK_RESPONSE_OUTAGE_CHANGED = {
    "city": "м. Київ",
    "street": "вул. Хрещатик",
    "house_num": "2",
    "group": "2",
    "schedule": {
        "04.11.25": [
            {"time": "00-03", "disconection": "full"},
            {"time": "03-06", "disconection": "full"}, # ЗМІНА ТУТ
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

def test_format_message_full_slots_merged():
    """
    Тестирование, что полные и смежные слоты объединяются корректно в новом формате.
    """
    mock_data = {
        "city": "м. Київ",
        "street": "вул. Хрещатик",
        "house_num": "2",
        "group": "2",
        "schedule": {
            "04.11.25": [
                {"time": "00-01", "disconection": "full"},
                {"time": "01-02", "disconection": "full"},
                {"time": "02-03", "disconection": "full"},
            ]
        }
    }

    expected_output = (
        "🏠 Адреса: `м. Київ, вул. Хрещатик, 2`\n"
        "👥 Черга: `2`\n"
        "❌ **04.11.25**: 00:00 - 03:00 (3 години)"
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
        "❌ **04.11.25**: 18:30 - 21:00 (2,5 години), 21:30 - 22:00 (0,5 години)"
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
        "❌ **04.11.25**: 18:00 - 21:00 (3 години), 21:30 - 22:00 (0,5 години)"
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
        "❌ **04.11.25**: 18:30 - 21:00 (2,5 години), 21:30 - 22:00 (0,5 години)\n"
        "❌ **05.11.25**: 15:30 - 18:00 (2,5 години), 18:30 - 19:00 (0,5 години)"
    )
    assert format_shutdown_message(mock_data).strip() == expected_output.strip()


# --- 5. Тестирование функций бизнес-логики ---
def test_pluralize_hours():
    """Тестирует правильное склонение слова 'година'."""
    assert _pluralize_hours(1.0) == "годину"
    assert _pluralize_hours(2.0) == "години"
    assert _pluralize_hours(5.0) == "годин"
    assert _pluralize_hours(11.0) == "годин"
    assert _pluralize_hours(21.0) == "годину"
    assert _pluralize_hours(22.0) == "години"
    assert _pluralize_hours(0.5) == "години"
    assert _pluralize_hours(2.5) == "години"
    assert _pluralize_hours(1.5) == "години"

def test_get_shutdown_duration_str_by_hours():
    """Тестирует форматирование и склонение длительности."""
    assert _get_shutdown_duration_str_by_hours(1.0) == "1 годину"
    assert _get_shutdown_duration_str_by_hours(2.5) == "2,5 години"
    assert _get_shutdown_duration_str_by_hours(3.0) == "3 години"
    assert _get_shutdown_duration_str_by_hours(11.0) == "11 годин"
    assert _get_shutdown_duration_str_by_hours(0.5) == "0,5 години"

# --- НОВИЙ ТЕСТ: Тестування функції _get_schedule_hash -------------
def test_get_schedule_hash():
    """
    Тестує генерацію хешу:
    1. Перевіряє, що однаковий графік дає однаковий хеш.
    2. Перевіряє, що змінений графік дає інший хеш.
    """
    # 1. Однаковий графік (MOCK_RESPONSE_OUTAGE)
    hash_original = _get_schedule_hash(MOCK_RESPONSE_OUTAGE)
    hash_original_again = _get_schedule_hash(MOCK_RESPONSE_OUTAGE)
    assert len(hash_original) == 64 # SHA256 довжина
    assert hash_original == hash_original_again
    
    # 2. Змінений графік (MOCK_RESPONSE_OUTAGE_CHANGED)
    hash_changed = _get_schedule_hash(MOCK_RESPONSE_OUTAGE_CHANGED)
    assert hash_original != hash_changed
    
    # 3. Графік без відключень
    hash_no_outage = _get_schedule_hash(MOCK_RESPONSE_NO_OUTAGE)
    assert hash_no_outage != hash_original
    assert hash_no_outage != hash_changed

    # 4. Порожній графік (повинно повернути константу)
    hash_empty = _get_schedule_hash({})
    assert hash_empty == "NO_SCHEDULE_FOUND"


# --- 6. Тестирование хендлеров (Bot Handlers) ---
# NOTE: Для совместимости с unittest и асинхронностью используем @pytest.mark.asyncio

class TestBotHandlers(unittest.TestCase):
    
    def setUp(self):
        # Очистка глобальных кешей перед каждым тестом
        HUMAN_USERS.clear()
        SUBSCRIPTIONS.clear()

    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_start_handler_initial_check_and_captcha(self):
        """
        Проверяет, что при первом запуске (не "Human") запускается CAPTCHA.
        """
        user_id = 123
        message = MagicMock(text="/start", from_user=MagicMock(id=user_id), answer=AsyncMock())
        fsm_context = AsyncMock()
        
        with patch('dtek_telegram_bot._get_captcha_data', return_value=("Скільки буде 10 + 3?", 13)):
            await command_start_handler(message, fsm_context)

        # Проверка вызова CAPTCHA
        message.answer.assert_called_once()
        self.assertIn("🚨 **Увага! Для захисту від ботів, пройдіть просту перевірку.**", message.answer.call_args[0][0])
        fsm_context.set_state.assert_called_with(CaptchaState.waiting_for_answer)

    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_captcha_answer_handler_success(self):
        """
        Проверяет успешное прохождение CAPTCHA.
        """
        user_id = 123
        message_start = MagicMock(text="/start", from_user=MagicMock(id=user_id), answer=AsyncMock())
        message_captcha_correct = MagicMock(text="13", from_user=MagicMock(id=user_id), answer=AsyncMock())
        fsm_context = AsyncMock()
        fsm_context.get_data.return_value = {"captcha_answer": 13}
        fsm_context.get_state.return_value = CaptchaState.waiting_for_answer
        
        await captcha_answer_handler(message_captcha_correct, fsm_context)
        
        # Проверка успеха
        self.assertIn(user_id, HUMAN_USERS)
        message_captcha_correct.answer.assert_called_once()
        self.assertIn("✅ **Перевірка пройдена!**", message_captcha_correct.answer.call_args[0][0])
        fsm_context.clear.assert_called_once()

    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_captcha_answer_handler_failure(self):
        """
        Проверяет неудачу при прохождении CAPTCHA.
        """
        user_id = 123
        message_captcha_wrong = MagicMock(text="10", from_user=MagicMock(id=user_id), answer=AsyncMock())
        fsm_context = AsyncMock()
        fsm_context.get_data.return_value = {"captcha_answer": 13}
        fsm_context.get_state.return_value = CaptchaState.waiting_for_answer
        
        await captcha_answer_handler(message_captcha_wrong, fsm_context)
        
        # Проверка неудачи
        self.assertNotIn(user_id, HUMAN_USERS)
        message_captcha_wrong.answer.assert_called_once()
        self.assertIn("❌ **Неправильна відповідь.**", message_captcha_wrong.answer.call_args[0][0])
        fsm_context.clear.assert_called_once()

    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_check_handler_full_flow_success(self):
        """
        Проверяет полный цикл: CAPTCHA -> Check.
        Вызов get_shutdowns_data (mocked) и получение ответа.
        """
        # 1. Mock Objects Setup
        user_id = 123 
        # Message Mocks
        message_start = MagicMock(text="/start", from_user=MagicMock(id=user_id), answer=AsyncMock())
        message_captcha_correct = MagicMock(text="13", from_user=MagicMock(id=user_id), answer=AsyncMock())
        message_check = MagicMock(text="/check м. Київ, вул. Хрещатик, 2", from_user=MagicMock(id=user_id), answer=AsyncMock())
        # FSMContext Mock
        fsm_context = AsyncMock()
        fsm_context.get_data.return_value = {"captcha_answer": 13} # Для captcha_answer_handler
        fsm_context.get_state.return_value = CaptchaState.waiting_for_answer # Для captcha_answer_handler
        
        # API Mock (Re-using MOCK_RESPONSE_OUTAGE)
        mock_api_data = MOCK_RESPONSE_OUTAGE.copy()
        expected_api_result = format_shutdown_message(mock_api_data)
        # Ожидаемый результат должен включать подсказку о подписке, т.к. пользователь новый
        expected_final_result = expected_api_result + SUBSCRIBE_PROMPT 

        # 2. CAPTCHA MOCK CONTROL и API MOCK
        with patch('dtek_telegram_bot._get_captcha_data', return_value=("Скільки буде 10 + 3?", 13)), \
             patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock(return_value=mock_api_data)) as mock_get_shutdowns:

            # --- ШАГ 1: /start (Запуск CAPTCHA) ---
            await command_start_handler(message_start, fsm_context)

            # --- ШАГ 2: Ответ CAPTCHA (Успех) ---
            await captcha_answer_handler(message_captcha_correct, fsm_context)
            self.assertIn(user_id, HUMAN_USERS)

            # --- ШАГ 3: /check (Проверка графика) ---
            fsm_context.get_state.return_value = None # Сброс состояния для check handler
            await command_check_handler(message_check, fsm_context)
            
            # Проверка API:
            mock_get_shutdowns.assert_called_once_with("м. Київ", "вул. Хрещатик", "2")
            
            # Проверка ответа:
            self.assertEqual(message_check.answer.call_count, 2)
            final_message = message_check.answer.call_args_list[1][0][0]
            self.assertEqual(final_message.strip(), expected_final_result.strip())

    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_repeat_handler_success(self):
        """
        Тестирует /repeat после успешной проверки /check.
        """
        user_id = 456
        HUMAN_USERS[user_id] = True
        
        # 1. Mock Objects Setup
        fsm_context = AsyncMock()
        last_checked_address = {'city': 'м. Київ', 'street': 'вул. Хрещатик', 'house': '2', 'hash': 'old_hash_123'}
        fsm_context.get_data.return_value = {"last_checked_address": last_checked_address}
        message_repeat = MagicMock(text="/repeat", from_user=MagicMock(id=user_id), answer=AsyncMock())
        
        # API Mock
        mock_api_data = MOCK_RESPONSE_OUTAGE.copy()
        expected_hash = _get_schedule_hash(mock_api_data)
        expected_api_result = format_shutdown_message(mock_api_data)
        expected_final_result = expected_api_result + SUBSCRIBE_PROMPT
        
        with patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock(return_value=mock_api_data)) as mock_get_shutdowns:
            
            # --- ШАГ 1: /repeat ---
            await command_repeat_handler(message_repeat, fsm_context)
            
            # Проверка API:
            mock_get_shutdowns.assert_called_once_with("м. Київ", "вул. Хрещатик", "2")
            
            # Проверка FSM update (обновление хеша):
            new_address_data = {'city': 'м. Київ', 'street': 'вул. Хрещатик', 'house': '2', 'hash': expected_hash}
            fsm_context.update_data.assert_called_once_with(last_checked_address=new_address_data)

            # Проверка ответа:
            self.assertEqual(message_repeat.answer.call_count, 2)
            final_message = message_repeat.answer.call_args_list[1][0][0]
            self.assertEqual(final_message.strip(), expected_final_result.strip())

    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_repeat_handler_no_last_check(self):
        """
        Тестирует /repeat, когда в FSM нет last_checked_address.
        """
        user_id = 789
        HUMAN_USERS[user_id] = True
        
        # 1. Mock Objects Setup
        message_repeat = MagicMock(text="/repeat", from_user=MagicMock(id=user_id), answer=AsyncMock())
        # FSMContext Mock: last_checked_address отсутствует
        fsm_context = AsyncMock()
        # Убедимся, что get_data возвращает пустой словарь (или не содержит нужного ключа)
        fsm_context.get_data.return_value = {"another_key": "value"} 

        # 2. API MOCK CONTROL (убедимся, что API не вызывается)
        with patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock()) as mock_get_shutdowns:
            
            # --- ШАГ 1: /repeat ---
            await command_repeat_handler(message_repeat, fsm_context)
            
            # Проверка API:
            mock_get_shutdowns.assert_not_called()
            
            # Проверка сообщений:
            self.assertEqual(message_repeat.answer.call_count, 1)
            error_message = message_repeat.answer.call_args_list[0][0][0]
            self.assertIn("Спочатку вам потрібно перевірити графік", error_message)

    # ------------------------------------------------------------------
    # --- НОВЫЙ ТЕСТ: Пошаговый ввод адреса через FSM ------------------
    # ------------------------------------------------------------------
    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_check_handler_fsm_flow_success(self):
        """
        Тестирует пошаговый ввод адреса через FSM:
        1. /check без аргументов -> Запрос города.
        2. Ввод города -> Запрос улицы.
        3. Ввод улицы -> Запрос дома.
        4. Ввод дома -> Вызов API и отправка ответа, очистка FSM, сохранение last_checked_address.
        """
        user_id = 999
        HUMAN_USERS[user_id] = True 

        # 1. Mock Messages
        message_check_empty = MagicMock(text="/check", from_user=MagicMock(id=user_id), answer=AsyncMock())
        message_city = MagicMock(text="м. Львів", from_user=MagicMock(id=user_id), answer=AsyncMock())
        message_street = MagicMock(text="вул. Зелена", from_user=MagicMock(id=user_id), answer=AsyncMock())
        message_house = MagicMock(text="100", from_user=MagicMock(id=user_id), answer=AsyncMock())
        
        # 2. Mock API Data
        mock_api_data = MOCK_RESPONSE_OUTAGE.copy()
        expected_hash = _get_schedule_hash(mock_api_data)
        expected_api_result = format_shutdown_message(mock_api_data)
        expected_final_result = expected_api_result + SUBSCRIBE_PROMPT

        # 3. FSM Context Mock
        fsm_context = AsyncMock()
        fsm_context.get_data.return_value = {} # Убедимся, что нет старых данных
        mock_get_state = AsyncMock(side_effect=[None, CheckAddressState.waiting_for_city, CheckAddressState.waiting_for_street, None])
        fsm_context.get_state = mock_get_state

        # 4. API Mock (для финального шага)
        with patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock(return_value=mock_api_data)) as mock_get_shutdowns:
            
            # --- ШАГ 1: /check без аргументов -> command_check_handler -> Запрос города ---
            await command_check_handler(message_check_empty, fsm_context)
            
            message_check_empty.answer.assert_called_with("📝 **Будь ласка, введіть назву міста** (наприклад, `м. Дніпро`):")
            fsm_context.set_state.assert_called_with(CheckAddressState.waiting_for_city)
            
            # --- ШАГ 2: Ввод города -> process_city -> Запрос улицы ---
            await process_city(message_city, fsm_context)
            
            fsm_context.update_data.assert_any_call(city="м. Львів")
            fsm_context.set_state.assert_called_with(CheckAddressState.waiting_for_street)
            # ИЗМЕНЕНИЕ 3: Исправлено ожидаемое сообщение для process_city
            message_city.answer.assert_called_with(
                "📝 Місто: `м. Львів`\n\n**Будь ласка, введіть назву вулиці** (наприклад, `вул. Сонячна набережна`):"
            )
            
            # --- ШАГ 3: Ввод улицы -> process_street -> Запрос дома ---
            await process_street(message_street, fsm_context)
            
            fsm_context.update_data.assert_any_call(street="вул. Зелена")
            fsm_context.set_state.assert_called_with(CheckAddressState.waiting_for_house)
            # ИЗМЕНЕНИЕ 4: Добавлено ожидаемое сообщение для process_street
            message_street.answer.assert_called_with(
                "📝 Вулиця: `вул. Зелена`\n\n**Будь ласка, введіть номер будинку** (наприклад, `6`):"
            )

            # --- ШАГ 4: Ввод дома -> process_house -> Вызов API и ответ ---
            await process_house(message_house, fsm_context)
            
            mock_get_shutdowns.assert_called_once_with("м. Львів", "вул. Зелена", "100")
            fsm_context.update_data.assert_any_call(house="100")
            fsm_context.clear.assert_called_once()
            
            # Проверяем, что last_checked_address был сохранен (ВКЛЮЧАЯ ХЕШ)
            expected_address_data = {'city': 'м. Львів', 'street': 'вул. Зелена', 'house': '100', 'hash': expected_hash}
            fsm_context.update_data.assert_any_call(last_checked_address=expected_address_data)

            # Проверка сообщений:
            self.assertEqual(message_house.answer.call_count, 2)
            # 1. 'Перевіряю графік'
            self.assertIn("✅ **Перевіряю графік**", message_house.answer.call_args_list[0][0][0])
            # 2. Финальный результат
            final_message = message_house.answer.call_args_list[1][0][0]
            self.assertEqual(final_message.strip(), expected_final_result.strip())
            
    # ------------------------------------------------------------------
    # --- НОВЫЕ ТЕСТЫ: /subscribe, /unsubscribe и /cancel --------------
    # ------------------------------------------------------------------

    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_subscribe_handler_initial_subscription(self):
        """
        Тестує, що при першій підписці встановлюється next_check і last_schedule_hash з FSM.
        """
        user_id = 1000
        HUMAN_USERS[user_id] = True 
        
        # FSM Mock с последним проверенным адресом и хешем
        hash_from_check = "some_initial_hash_abc123"
        address_data = {'city': 'м. Київ', 'street': 'вул. Хрещатик', 'house': '2', 'hash': hash_from_check}
        fsm_context = AsyncMock()
        fsm_context.get_data.return_value = {"last_checked_address": address_data}
        
        # Message Mock
        message_subscribe = MagicMock(
            text="/subscribe 1.0",
            from_user=MagicMock(id=user_id),
            answer=AsyncMock()
        )
        
        # 1. Вызов
        await command_subscribe_handler(message_subscribe, fsm_context)
        
        # 2. Проверка
        self.assertIn(user_id, SUBSCRIPTIONS)
        self.assertEqual(SUBSCRIPTIONS[user_id]['city'], 'м. Київ')
        self.assertEqual(SUBSCRIPTIONS[user_id]['interval_hours'], 1.0)
        self.assertEqual(SUBSCRIPTIONS[user_id]['last_schedule_hash'], hash_from_check)
        
        message_subscribe.answer.assert_called_once()
        self.assertIn("✅ **Ви підписалися**", message_subscribe.answer.call_args[0][0])

    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_unsubscribe_handler_success(self):
        """
        Тестує успішне скасування підписки.
        """
        user_id = 1002
        HUMAN_USERS[user_id] = True
    
        # 1. Створюємо підписку
        SUBSCRIPTIONS[user_id] = {
            'city': 'м. Київ', 'street': 'вул. Хрещатик', 'house': '2',
            'interval_hours': 1.0,
            'next_check': datetime.now(),
            'last_schedule_hash': 'some_hash',
        }
        self.assertIn(user_id, SUBSCRIPTIONS)
    
        # 2. Моки
        message_unsubscribe = MagicMock(
            text="/unsubscribe",
            from_user=MagicMock(id=user_id),
            answer=AsyncMock()
        )
        fsm_context = AsyncMock()
    
        # 3. Виклик
        # ИЗМЕНЕНИЕ 1: Сигнатура исправлена в dtek_telegram_bot.py
        await command_unsubscribe_handler(message_unsubscribe, fsm_context) 
        
        # 4. Перевірка
        self.assertNotIn(user_id, SUBSCRIPTIONS)
        message_unsubscribe.answer.assert_called_once()
        self.assertIn("✅ **Ви успішно скасували підписку**", message_unsubscribe.answer.call_args[0][0])

    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_unsubscribe_handler_not_subscribed(self):
        """
        Тестує скасування, коли підписки немає.
        """
        user_id = 1003
        HUMAN_USERS[user_id] = True
        self.assertNotIn(user_id, SUBSCRIPTIONS)
        
        # 1. Моки
        message_unsubscribe = MagicMock(
            text="/unsubscribe",
            from_user=MagicMock(id=user_id),
            answer=AsyncMock()
        )
        fsm_context = AsyncMock()
        
        # 2. Виклик
        await command_unsubscribe_handler(message_unsubscribe, fsm_context)
        
        # 3. Перевірка
        self.assertNotIn(user_id, SUBSCRIPTIONS) # Должен остаться не подписан
        message_unsubscribe.answer.assert_called_once()
        self.assertIn("❌ **Ви не підписані**", message_unsubscribe.answer.call_args[0][0])
    
    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_cancel_handler_active_fsm(self):
        """
        Тестує /cancel, коли є активний FSM-стан.
        """
        user_id = 1004
        HUMAN_USERS[user_id] = True
        
        # 1. Моки
        message_cancel = MagicMock(
            text="/cancel",
            from_user=MagicMock(id=user_id),
            answer=AsyncMock()
        )
        fsm_context = AsyncMock()
        # Имитация активного FSM-состояния
        fsm_context.get_state.return_value = CheckAddressState.waiting_for_city
        
        # 2. Виклик
        await command_cancel_handler(message_cancel, fsm_context)
        
        # 3. Перевірка
        fsm_context.get_state.assert_called_once()
        fsm_context.clear.assert_called_once()
        message_cancel.answer.assert_called_once()
        self.assertIn("✅ **Операція скасована.**", message_cancel.answer.call_args[0][0])
        # Проверяем, что удалена клавиатура
        self.assertIsInstance(message_cancel.answer.call_args[1]['reply_markup'], ReplyKeyboardRemove)


    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_cancel_handler_no_active_fsm(self):
        """
        Тестує /cancel, коли немає активного FSM-стану.
        """
        user_id = 1005
        HUMAN_USERS[user_id] = True
        
        # 1. Моки
        message_cancel = MagicMock(
            text="/cancel",
            from_user=MagicMock(id=user_id),
            answer=AsyncMock()
        )
        fsm_context = AsyncMock()
        # Имитация отсутствия FSM-состояния
        fsm_context.get_state.return_value = None
        
        # 2. Виклик
        await command_cancel_handler(message_cancel, fsm_context)
        
        # 3. Перевірка
        fsm_context.get_state.assert_called_once()
        fsm_context.clear.assert_not_called() # Clear не вызывается, если state is None
        message_cancel.answer.assert_called_once()
        self.assertIn("ℹ️ Немає активних операцій для скасування.", message_cancel.answer.call_args[0][0])


    # ------------------------------------------------------------------
    # --- ТЕСТЫ: subscription_checker_task (добавлены @pytest.mark.asyncio)
    # ------------------------------------------------------------------
    
    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_checker_task_no_changes(self):
        """
        Тестує фонову задачу:
        1. Перевіряє, що графік перевіряється один раз.
        2. Перевіряє, що повідомлення надсилається (перша перевірка).
        3. Перевіряє, що при наступній перевірці (без змін) повідомлення НЕ надсилається.
        """
        user_id = 1006
        address_data = {'city': 'м. Київ', 'street': 'вул. Хрещатик', 'house': '2'}
        initial_hash = _get_schedule_hash(MOCK_RESPONSE_OUTAGE)
        
        # 1. Setup: імітація підписки
        now = datetime(2025, 11, 7, 10, 0, 0)
        SUBSCRIPTIONS[user_id] = {
            'city': address_data['city'], 
            'street': address_data['street'], 
            'house': address_data['house'], 
            'interval_hours': 1.0, 
            'next_check': now - timedelta(minutes=1), # Перевірка має бути виконана
            'last_schedule_hash': "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION", # Це перша перевірка
        }
        
        # Mock об'єкти
        mock_bot = MagicMock(send_message=AsyncMock())
        
        # Функція для імітації одного циклу (використовуючи patch для asyncio.sleep)
        async def run_checker_once():
            class InterruptSleep:
                """Мок, который позволяет пройти одну итерацию цикла и прерывает вторую."""
                def __init__(self): self.first_call = True
                def __call__(self, delay):
                    if self.first_call:
                        self.first_call = False
                        return # Позволяем завершиться, но не спать
                    raise StopAsyncIteration # Прерываем цикл
            
            with patch('dtek_telegram_bot.asyncio.sleep', new=InterruptSleep()) as mock_sleep:
                try:
                    await subscription_checker_task(mock_bot)
                except StopAsyncIteration:
                    pass

        # --- ЦИКЛ 1: Перша перевірка (хеш оновлюється) ---
        with patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock(return_value=MOCK_RESPONSE_OUTAGE)) as mock_get_shutdowns, \
             patch('dtek_telegram_bot.datetime') as mock_datetime:

            mock_datetime.now.return_value = now
            await run_checker_once() 
            
            # Перевірка 1: Повідомлення БУЛО надіслано
            mock_get_shutdowns.assert_called_once()
            mock_bot.send_message.assert_called_once()
            
            self.assertIn("🔔 **Графік перевірено**", mock_bot.send_message.call_args[1]['text'])
            self.assertEqual(SUBSCRIPTIONS[user_id]['last_schedule_hash'], initial_hash)
            self.assertEqual(SUBSCRIPTIONS[user_id]['next_check'], now + timedelta(hours=1))

        # --- ЦИКЛ 2: Графік НЕ змінився (next_check настав) ---
        now_cycle_2 = datetime(2025, 11, 7, 11, 0, 0) # Спрацьовує перевірка
        mock_bot.send_message.reset_mock() 
        mock_get_shutdowns.reset_mock()
        
        with patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock(return_value=MOCK_RESPONSE_OUTAGE)) as mock_get_shutdowns, \
             patch('dtek_telegram_bot.datetime') as mock_datetime:
            
            mock_datetime.now.return_value = now_cycle_2
            await run_checker_once() 
            
            # Перевірка 2: Повідомлення НЕ було надіслано
            mock_get_shutdowns.assert_called_once() # API викликано, але хеш той самий
            mock_bot.send_message.assert_not_called()
            self.assertEqual(SUBSCRIPTIONS[user_id]['last_schedule_hash'], initial_hash)
            self.assertEqual(SUBSCRIPTIONS[user_id]['next_check'], now_cycle_2 + timedelta(hours=1))


        # --- ЦИКЛ 3: Графік ЗМІНИВСЯ (next_check настав) ---
        now_cycle_3 = datetime(2025, 11, 7, 12, 5, 0) # Спрацьовує перевірка
        mock_bot.send_message.reset_mock()
        mock_get_shutdowns.reset_mock()

        changed_hash = _get_schedule_hash(MOCK_RESPONSE_OUTAGE_CHANGED)
        
        with patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock(return_value=MOCK_RESPONSE_OUTAGE_CHANGED)) as mock_get_shutdowns, \
             patch('dtek_telegram_bot.datetime') as mock_datetime:
            
            mock_datetime.now.return_value = now_cycle_3
            await run_checker_once() 

            # Перевірка 3: Повідомлення БУЛО надіслано
            mock_get_shutdowns.assert_called_once()
            mock_bot.send_message.assert_called_once()
            
            self.assertIn("🔔 **ОНОВЛЕННЯ ГРАФІКУ!**", mock_bot.send_message.call_args[1]['text'])
            self.assertEqual(SUBSCRIPTIONS[user_id]['last_schedule_hash'], changed_hash)
            self.assertEqual(SUBSCRIPTIONS[user_id]['next_check'], now_cycle_3 + timedelta(hours=1))


    @pytest.mark.asyncio # ИЗМЕНЕНИЕ 2: Добавлено
    async def test_checker_task_multiple_users_same_address(self):
        """
        Тестує, що при наявності декількох підписників на ОДНУ адресу,
        API викликається лише ОДИН раз, але повідомлення отримують ВСІ.
        """
        user_id_a = 2001
        user_id_b = 2002
        address_data = {'city': 'м. Львів', 'street': 'вул. Зелена', 'house': '100'}
        initial_hash = _get_schedule_hash(MOCK_RESPONSE_OUTAGE)

        # 1. Setup: імітація підписок
        now = datetime(2025, 11, 7, 10, 0, 0)
        SUBSCRIPTIONS[user_id_a] = {**address_data, 'interval_hours': 1.0, 'next_check': now - timedelta(minutes=1), 'last_schedule_hash': "NO_SCHEDULE_FOUND"}
        SUBSCRIPTIONS[user_id_b] = {**address_data, 'interval_hours': 1.0, 'next_check': now - timedelta(minutes=1), 'last_schedule_hash': "NO_SCHEDULE_FOUND"}

        # Mock об'єкти
        mock_bot = MagicMock(send_message=AsyncMock())
        
        # Функція для імітації одного циклу
        async def run_checker_once():
            class InterruptSleep:
                def __init__(self): self.first_call = True
                def __call__(self, delay):
                    if self.first_call:
                        self.first_call = False
                        return 
                    raise StopAsyncIteration 
            
            with patch('dtek_telegram_bot.asyncio.sleep', new=InterruptSleep()) as mock_sleep:
                try:
                    await subscription_checker_task(mock_bot)
                except StopAsyncIteration:
                    pass

        # 2. Виклик (Mock API)
        with patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock(return_value=MOCK_RESPONSE_OUTAGE)) as mock_get_shutdowns, \
             patch('dtek_telegram_bot.datetime') as mock_datetime:

            mock_datetime.now.return_value = now
            # Запуск циклу
            await run_checker_once()
            
            # 4. Перевірка
            # API має бути викликане лише ОДИН раз
            mock_get_shutdowns.assert_called_once()
            
            # Обидва користувачі мають отримати повідомлення
            self.assertEqual(mock_bot.send_message.call_count, 2)
            
            # Перевірка користувача A
            call_a = next(c for c in mock_bot.send_message.call_args_list if c[1]['chat_id'] == user_id_a)
            self.assertIn("🔔 **Графік перевірено**", call_a[1]['text'])
            self.assertEqual(SUBSCRIPTIONS[user_id_a]['last_schedule_hash'], initial_hash)
            self.assertEqual(SUBSCRIPTIONS[user_id_a]['next_check'], now + timedelta(hours=1))

            # Перевірка користувача B
            call_b = next(c for c in mock_bot.send_message.call_args_list if c[1]['chat_id'] == user_id_b)
            self.assertIn("🔔 **Графік перевірено**", call_b[1]['text'])
            self.assertEqual(SUBSCRIPTIONS[user_id_b]['last_schedule_hash'], initial_hash)
            self.assertEqual(SUBSCRIPTIONS[user_id_b]['next_check'], now + timedelta(hours=1))