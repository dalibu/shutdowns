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
    """Создает полный URL с query-парамерами для мокирования."""
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
# ... (Остальные тестовые функции TestBotBusinessLogic остаются без изменений)

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

# ... (Остальные тесты форматирования format_message_... остаются без изменений)
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

    # ИЗМЕНЕНО: Ожидаемый результат обновлен
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

    # ИЗМЕНЕНО: Ожидаемый результат обновлен
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

    # ИЗМЕНЕНО: Ожидаемый результат обновлен
    expected_output = (
        "🏠 Адреса: `м. Одеса, вул. Приморська, 5`\n"
        "👥 Черга: `6.0`\n"
        "❌ **04.11.25**: 18:30 - 21:00 (2,5 години)\n"
        "❌ **05.11.25**: 15:00 - 18:00 (3 години), 18:30 - 19:00 (0,5 години)"
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

    # ИЗМЕНЕНО: Ожидаемый результат обновлен
    expected_output = (
        "🏠 Адреса: `м. Чернігів, вул. Івана Мазепи, 42`\n"
        "👥 Черга: `7.0`\n"
        "❌ **04.11.25**: 18:30 - 21:00 (2,5 години), 21:30 - 22:00 (0,5 години)\n"
        "❌ **05.11.25**: 15:30 - 18:00 (2,5 години), 18:30 - 19:00 (0,5 години)"
    )
    assert format_shutdown_message(mock_data).strip() == expected_output.strip()

# ------------------------------------------------------------------
# --- НОВИЙ ТЕСТ: Тестування функції _get_schedule_hash -------------
# ------------------------------------------------------------------
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
        
    # 📌 ИЗМЕНЕНИЕ: Тесты для удаленной функции _get_shutdown_duration_str удалены.
    # 📌 НОВЫЙ ТЕСТ: Добавлен тест для _get_shutdown_duration_str_by_hours
    def test_get_shutdown_duration_str_by_hours(self):
        """Проверяет корректное форматирование длительности для новой функции."""
        self.assertEqual(_get_shutdown_duration_str_by_hours(3.0), "3 години")
        self.assertEqual(_get_shutdown_duration_str_by_hours(2.5), "2,5 години")
        self.assertEqual(_get_shutdown_duration_str_by_hours(1.0), "1 годину")
        self.assertEqual(_get_shutdown_duration_str_by_hours(0.5), "0,5 години")
        self.assertEqual(_get_shutdown_duration_str_by_hours(10.0), "10 годин")
        self.assertEqual(_get_shutdown_duration_str_by_hours(0.0), "0 годин")
        self.assertEqual(_get_shutdown_duration_str_by_hours(21.0), "21 годину")


# --- 6. ИНТЕГРАЦИОННЫЕ ТЕСТЫ ДЛЯ ХЕНДЛЕРОВ (CAPTCHA + CHECK) ---

class TestBotHandlers(unittest.IsolatedAsyncioTestCase):
    
    # SETUP/TEARDOWN: Важно для очистки глобального состояния
    def setUp(self):
        # Очищаем глобальный кеш перед каждым тестом
        HUMAN_USERS.clear() 
        SUBSCRIPTIONS.clear() # ДОДАНО: Очистка кеша подписок

    def tearDown(self):
        # Очищаем глобальный кеш после каждого теста
        HUMAN_USERS.clear() 
        SUBSCRIPTIONS.clear() # ДОДАНО: Очистка кеша подписок
        
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
        
        # ДОБАВЛЕНО: Ожидаемый результат должен включать подсказку о подписке, т.к. пользователь новый
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
            await command_check_handler(message_check, fsm_context)
            
            # Проверка API:
            mock_get_shutdowns.assert_called_once_with("м. Київ", "вул. Хрещатик", "2")
            
            # Проверка сообщений (Ожидание + Результат)
            self.assertEqual(message_check.answer.call_count, 2)
            final_message = message_check.answer.call_args_list[1][0][0]
            # ИСПРАВЛЕНИЕ: Сравниваем с полным ожидаемым результатом
            self.assertEqual(final_message.strip(), expected_final_result.strip())

    # ------------------------------------------------------------------
    # --- НОВЫЕ ТЕСТЫ ДЛЯ КОМАНДЫ /repeat ------------------------------
    # ------------------------------------------------------------------
    
    async def test_repeat_handler_success(self):
        """
        Тестирует успешное выполнение команды /repeat:
        1. Пользователь прошёл CAPTCHA (HUMAN_USERS).
        2. В FSMContext есть сохраненный адрес (last_checked_address).
        3. Вызывается API и отправляется корректный ответ.
        """
        # 1. Mock Setup
        user_id = 456
        address_data = {'city': 'м. Київ', 'street': 'вул. Хрещатик', 'house': '2', 'hash': 'some_hash'} # ИСПРАВЛЕНО
        
        # Предварительная подготовка: Пользователь прошел CAPTCHA
        HUMAN_USERS[user_id] = True 
        
        # Message Mocks
        message_repeat = MagicMock(text="/repeat", from_user=MagicMock(id=user_id), answer=AsyncMock())
        
        # FSMContext Mock: Устанавливаем сохраненный адрес
        fsm_context = AsyncMock()
        fsm_context.get_data.return_value = {"last_checked_address": address_data}
        
        # API Mock
        mock_api_data = MOCK_RESPONSE_OUTAGE.copy()
        expected_api_result = format_shutdown_message(mock_api_data)
        
        # Пользователь не подписан, ожидается подсказка
        expected_final_result = expected_api_result + SUBSCRIBE_PROMPT 

        # 2. API MOCK CONTROL
        with patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock(return_value=mock_api_data)) as mock_get_shutdowns:
            
            # --- ШАГ 1: /repeat ---
            await command_repeat_handler(message_repeat, fsm_context)
            
            # Проверка вызова API:
            mock_get_shutdowns.assert_called_once_with("м. Київ", "вул. Хрещатик", "2")
            
            # Проверка сообщений:
            # 1. "Повторяю проверку..."
            # 2. Результат
            self.assertEqual(message_repeat.answer.call_count, 2)
            
            # Проверяем первое сообщение (уведомление)
            self.assertIn("Повторюю перевірку", message_repeat.answer.call_args_list[0][0][0])
            
            # Проверяем финальный результат
            final_message = message_repeat.answer.call_args_list[1][0][0]
            self.assertEqual(final_message.strip(), expected_final_result.strip())

    async def test_repeat_handler_no_previous_check(self):
        """
        Тестирует /repeat, когда в FSMContext нет сохраненного адреса.
        """
        # 1. Mock Setup
        user_id = 789
        
        # Предварительная подготовка: Пользователь прошел CAPTCHA
        HUMAN_USERS[user_id] = True 
        
        # Message Mocks
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
        
        # 2. FSMContext Mock: Устанавливаем данные, которые будут возвращены при финальном вызове get_data
        fsm_context = AsyncMock()
        # Настраиваем get_data для финального вызова (должен вернуть все три части)
        fsm_context.get_data.return_value = {'city': 'м. Львів', 'street': 'вул. Зелена', 'house': '100'}
        
        # API Mock
        mock_api_data = MOCK_RESPONSE_OUTAGE.copy()
        # Обновляем адрес в mock_api_data, чтобы он соответствовал введенному (иначе format_shutdown_message будет использовать "м. Київ")
        mock_api_data.update(city="м. Львів", street="вул. Зелена", house_num="100") 
        expected_api_result = format_shutdown_message(mock_api_data)
        expected_final_result = expected_api_result + SUBSCRIBE_PROMPT 

        # --- ИСПРАВЛЕНИЕ 1: Рассчитываем хеш, который код должен сохранить ---
        expected_hash = _get_schedule_hash(mock_api_data)
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

        with patch('dtek_telegram_bot.get_shutdowns_data', new=AsyncMock(return_value=mock_api_data)) as mock_get_shutdowns:
            
            # --- ШАГ 1: /check (старт FSM) ---
            await command_check_handler(message_check_empty, fsm_context)
            
            # Проверка: FSM перешло в waiting_for_city и ответ был отправлен
            fsm_context.set_state.assert_called_with(CheckAddressState.waiting_for_city)
            message_check_empty.answer.assert_called_once_with("📝 **Будь ласка, введіть назву міста** (наприклад, `м. Дніпро`):")
            
            # --- ШАГ 2: Ввод города ---
            await process_city(message_city, fsm_context)
            
            # Проверка: FSM перешло в waiting_for_street
            fsm_context.set_state.assert_called_with(CheckAddressState.waiting_for_street)
            message_city.answer.assert_called_once_with("📝 **Тепер введіть назву вулиці** (наприклад, `вул. Сонячна набережна`):")
            fsm_context.update_data.assert_called_with(city="м. Львів")

            # --- ШАГ 3: Ввод улицы ---
            await process_street(message_street, fsm_context)

            # Проверка: FSM перешло в waiting_for_house
            fsm_context.set_state.assert_called_with(CheckAddressState.waiting_for_house)
            message_street.answer.assert_called_once_with("📝 **Нарешті, введіть номер будинку** (наприклад, `6`):")
            fsm_context.update_data.assert_called_with(street="вул. Зелена")

            # --- ШАГ 4: Ввод дома (Финальный шаг) ---
            await process_house(message_house, fsm_context)

            # Проверка API:
            mock_get_shutdowns.assert_called_once_with("м. Львів", "вул. Зелена", "100")
            
            # Проверка FSM (обновление для новой логики сохранения/очистки):
            fsm_context.update_data.assert_any_call(house="100") 
            fsm_context.clear.assert_called_once()
            
            # --- ИСПРАВЛЕНИЕ 1: Проверяем, что last_checked_address был сохранен (ВКЛЮЧАЯ ХЕШ) ---
            expected_address_data = {'city': 'м. Львів', 'street': 'вул. Зелена', 'house': '100', 'hash': expected_hash}
            fsm_context.update_data.assert_any_call(last_checked_address=expected_address_data)
            # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
            
            # Проверка сообщений:
            self.assertEqual(message_house.answer.call_count, 2)
            final_message = message_house.answer.call_args_list[1][0][0]
            self.assertEqual(final_message.strip(), expected_final_result.strip())

    # ------------------------------------------------------------------
    # --- ФИКС 1: Тестування command_subscribe_handler ------------------
    # ------------------------------------------------------------------
    async def test_subscribe_handler_initial_subscription(self):
        """
        Тестує, що при першій підписці встановлюється next_check і last_schedule_hash = None.
        ФИКС: Используем более надежный мок FSM context и проверяем chat_id.
        """
        user_id = 1000
        # ИСПРАВЛЕНИЕ 1 (для Проблемы 1): Добавляем хеш в FSM, как это делает /check
        address_data = {'city': 'м. Київ', 'street': 'вул. Хрещатик', 'house': '2', 'hash': 'some_hash_from_check'}
        HUMAN_USERS[user_id] = True 
        
        # Для тесту /subscribe <interval>
        user_mock = MagicMock(id=user_id)
        chat_mock = MagicMock(id=user_id) # У ботов chat_id == user_id
        
        message_subscribe = MagicMock(
            text="/subscribe 2.5", 
            from_user=user_mock,
            chat=chat_mock, # <--- ДОБАВЛЕНО
            answer=AsyncMock()
        )
        
        # ФИКС 1.1: Более надежный мок FSM context, чтобы избежать KeyError
        fsm_context = MagicMock()
        fsm_context.get_data = AsyncMock(return_value={"last_checked_address": address_data, "other_data": "test"})
        fsm_context.set_state = AsyncMock()
        fsm_context.update_data = AsyncMock()
        fsm_context.clear = AsyncMock()
        
        # --- ШАГ 1: /subscribe ---
        await command_subscribe_handler(message_subscribe, fsm_context)
        
        # Перевірка:
        self.assertIn(user_id, SUBSCRIPTIONS) 
        subscription = SUBSCRIPTIONS[user_id]
        
        self.assertEqual(subscription['city'], 'м. Київ')
        self.assertEqual(subscription['interval_hours'], 2.5)
        # ИСПРАВЛЕНИЕ 1 (для Проблемы 1): Хеш должен быть взят из FSM
        self.assertEqual(subscription['last_schedule_hash'], 'some_hash_from_check') 
        self.assertIsInstance(subscription['next_check'], datetime)
        
        message_subscribe.answer.assert_called_once()
        
        self.assertIn("Ви підписалися на автоматичні оновлення", message_subscribe.answer.call_args_list[0][0][0])
        
        # --- ИСПРАВЛЕНИЕ 2: (Assert 2) Меняем "годин" на "години" ---
        self.assertIn("Інтервал перевірки: **2,5 години**", message_subscribe.answer.call_args_list[0][0][0])
        # --- КОНЕЦ ИСПРАВЛЕНИЯ 2 ---


    # ------------------------------------------------------------------
    # --- НОВИЙ ТЕСТ: Тестування command_unsubscribe_handler -----------
    # ------------------------------------------------------------------
    async def test_unsubscribe_handler(self):
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
            'chat_id': user_id 
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
        await command_unsubscribe_handler(message_unsubscribe, fsm_context)

        # 4. Перевірка
        self.assertNotIn(user_id, SUBSCRIPTIONS)
        message_unsubscribe.answer.assert_called_once()
        self.assertIn("Підписку скасовано", message_unsubscribe.answer.call_args[0][0])
        
    # ------------------------------------------------------------------
    # --- ФИКС 2: Тестування subscription_checker_task (логіка хешу) ----
    # ------------------------------------------------------------------
    async def test_subscription_checker_notification_logic(self):
        """
        Тестує логіку відправки повідомлень у фоновій задачі, повністю контролюючи час.
        """
        user_id = 1001
        address_data = {'city': 'м. Київ', 'street': 'вул. Хрещатик', 'house': '2'}
        mock_bot = MagicMock(send_message=AsyncMock())
        
        initial_hash = _get_schedule_hash(MOCK_RESPONSE_OUTAGE)
        changed_hash = _get_schedule_hash(MOCK_RESPONSE_OUTAGE_CHANGED)
        
        class InterruptSleep:
            """Мок, который позволяет пройти одну итерацию цикла и прерывает вторую."""
            def __init__(self):
                self.first_call = True
            
            def __call__(self, delay):
                if self.first_call:
      
                    self.first_call = False
                    return 
                raise StopAsyncIteration 
    
    
        async def run_checker_once():
            mock_sleep.side_effect = InterruptSleep()
            try:
                await subscription_checker_task(mock_bot)
            except StopAsyncIteration:
                pass
            finally:
                mock_sleep.side_effect = None 
        
        time_sequence = [
            datetime(2025, 11, 7, 10, 0, 0), # 1: current_time (Cycle 1)
            datetime(2025, 11, 7, 11, 5, 0), # 2: current_time (Cycle 2)
            datetime(2025, 11, 7, 12, 10, 0) # 3: current_time (Cycle 3)
        ]
        
        
        with patch('dtek_telegram_bot.get_shutdowns_data') as mock_get_shutdowns, \
             patch('dtek_telegram_bot.datetime') as mock_datetime_class, \
             patch('dtek_telegram_bot.asyncio.sleep') as mock_sleep:
            
            mock_datetime_class.now.side_effect = time_sequence
            mock_datetime_class.strptime = datetime.strptime

            # --- ЦИКЛ 1: Перша перевірка (хеш None) ---
            
            SUBSCRIPTIONS[user_id] = {
                **address_data,
                'interval_hours': 1.0,
                'next_check': datetime(2025, 11, 7, 9, 55, 0), 
                'last_schedule_hash': None,
                'chat_id': user_id, 
            }
            
            mock_get_shutdowns.return_value = MOCK_RESPONSE_OUTAGE
            
            await run_checker_once()
            
            # Перевірка 1: Повідомлення було надіслано
            mock_bot.send_message.assert_called_once()
            self.assertIn("**Графік перевірено**", mock_bot.send_message.call_args[1]['text'])
            self.assertEqual(SUBSCRIPTIONS[user_id]['last_schedule_hash'], initial_hash)
            self.assertEqual(SUBSCRIPTIONS[user_id]['next_check'], datetime(2025, 11, 7, 11, 0, 0))

            # --- ЦИКЛ 2: Графік НЕ змінився ---
            mock_bot.send_message.reset_mock() 
            mock_get_shutdowns.reset_mock()
            
            mock_get_shutdowns.return_value = MOCK_RESPONSE_OUTAGE
            
            await run_checker_once() 
            
            # Перевірка 2: Повідомлення НЕ було надіслано
            mock_bot.send_message.assert_not_called()
            self.assertEqual(SUBSCRIPTIONS[user_id]['last_schedule_hash'], initial_hash)
            self.assertEqual(SUBSCRIPTIONS[user_id]['next_check'], datetime(2025, 11, 7, 12, 5, 0))


            # --- ЦИКЛ 3: Графік ЗМІНИВСЯ ---
            mock_bot.send_message.reset_mock()
            mock_get_shutdowns.reset_mock()

            mock_get_shutdowns.return_value = MOCK_RESPONSE_OUTAGE_CHANGED
            
            await run_checker_once() 

            # Перевірка 3: Повідомлення БУЛО надіслано
            mock_bot.send_message.assert_called_once()
            self.assertIn("**ОНОВЛЕННЯ ГРАФІКУ!**", mock_bot.send_message.call_args[1]['text'])
            self.assertEqual(SUBSCRIPTIONS[user_id]['last_schedule_hash'], changed_hash)
            self.assertEqual(SUBSCRIPTIONS[user_id]['next_check'], datetime(2025, 11, 7, 13, 10, 0))