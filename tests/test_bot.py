import pytest
import aiohttp
import asyncio
import re
from aioresponses import aioresponses
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode
from typing import List, Dict, Any
from datetime import datetime

# --- Конфигурация ---
API_BASE_URL = "http://dtek_api:8000" 

# --- 1. Вспомогательные функции (Дублируют dtek_telegram_bot.py для юнит-тестов) ---

def format_minutes_to_hh_m(minutes: int) -> str:
    """Форматирует общее количество минут в HH:MM."""
    h = minutes // 60
    m = minutes % 60
    return f"{h}:{m:02d}"

def _process_single_day_schedule(date: str, slots: List[Dict[str, Any]]) -> str:
    """
    Консолидирует слоты отключений и возвращает строку со временем ИЛИ статус "немає".
    """
    outage_slots = [s for s in slots if s.get('disconection') in ('full', 'half')]
    
    if not outage_slots:
        return "Відключення не заплановані" 

    first_slot = outage_slots[0]
    last_slot = outage_slots[-1]

    # --- Расчет времени начала отключения ---
    try:
        time_parts_start = re.split(r'\s*[-\–]\s*', first_slot.get('time', '0-0'))
        start_hour = int(time_parts_start[0])
        
        if first_slot.get('disconection') == 'full':
            outage_start_min = start_hour * 60 
        else:
            outage_start_min = start_hour * 60 + 30
    except Exception:
        return "Помилка парсингу часу початку"

    # --- Расчет времени конца отключения ---
    try:
        time_parts_end = re.split(r'\s*[-\–]\s*', last_slot.get('time', '0-0'))
        end_hour = int(time_parts_end[1])
        
        if last_slot.get('disconection') == 'full':
            outage_end_min = end_hour * 60
        else: 
            outage_end_min = end_hour * 60 - 30

    except Exception:
        return "Помилка парсингу часу кінця"
        
    if outage_start_min >= outage_end_min:
         return "Відключення не заплановані (або помилка часу)"

    start_time_final = format_minutes_to_hh_m(outage_start_min)
    end_time_final = format_minutes_to_hh_m(outage_end_min)
    
    # Возвращаем только строку времени
    return f"{start_time_final} - {end_time_final}"


def format_shutdown_message(data: dict) -> str:
    """
    Форматирует агрегированный JSON-ответ в новый, компактный формат.
    """
    
    city = data.get("city", "Н/Д")
    street = data.get("street", "Н/Д")
    house = data.get("house_num", "Н/Д")
    group = data.get("group", "Н/Д")
    schedule = data.get("schedule", {})
    
    # 1. Формирование заголовка (адрес + очередь)
    message = (
        f"🏠 Адреса: `{city}, {street}, {house}`\n"
        f"👥 Черга: `{group}`"
    )
    
    if not schedule:
        return message + "\n❌ *Не вдалося отримати графік відключень.*"

    # Сортируем даты
    try:
        sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
    except ValueError:
        sorted_dates = sorted(schedule.keys())
    
    schedule_lines = []
    
    for date in sorted_dates:
        slots = schedule[date]
        result_str = _process_single_day_schedule(date, slots)
        
        if "Відключення не заплановані" in result_str or "Помилка" in result_str:
            # ✅ Для отсутствия отключений
            line = f"✅ **{date}**: {result_str}"
        else:
            # ❌ Для отключений
            line = f"❌ **{date}**: `{result_str}` (💡 світла не буде)"

        schedule_lines.append(line)

    final_schedule_output = "\n".join(schedule_lines)
    
    return message + "\n" + final_schedule_output

# --- 2. Функции для мокирования HTTP ---

def create_mock_url(city: str, street: str, house: str) -> str:
    """Создает полный URL с query-параметрами для мокирования."""
    query_params = {
        "city": city,
        "street": street,
        "house": house
    }
    return f"{API_BASE_URL}/shutdowns?{urlencode(query_params)}"


async def get_shutdowns_data(city: str, street: str, house: str) -> dict:
    """
    Вызывает API-парсер и возвращает полный агрегированный JSON-ответ.
    """
    params = {
        "city": city,
        "street": street,
        "house": house
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{API_BASE_URL}/shutdowns", params=params, timeout=45) as response: 
                if response.status == 404:
                    raise ValueError("Графік для цієї адреси не знайдено.")
                
                response.raise_for_status()
                return await response.json()

        except aiohttp.ClientError as e:
            raise ConnectionError("Помилка підключення до парсера. Спробуйте пізніше.")
        
# --- 3. Фиксация данных (MOCK PAYLOADS) ---

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
            {"time": "03-06", "disconection": "none"},
        ],
        "05.11": [
            {"time": "09-12", "disconection": "none"},
            {"time": "12-15", "disconection": "none"},
        ]
    }
}

# --- 4. Тестовые функции для API-интеграции (проверка get_shutdowns_data) ---

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
    with aioresponses() as m:
        m.get(url, status=404)
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


# --- 5. Тестовые функции для форматирования сообщений (проверка format_shutdown_message) ---

def test_format_message_no_outage():
    """
    Тестирование форматирования для случая без запланированных отключений в новом формате.
    Использует MOCK_RESPONSE_NO_OUTAGE с датами '04.11.25' и '05.11.25' для соответствия тестам.
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
        "❌ **04.11.25**: `18:30 - 21:30` (💡 світла не буде)"
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
        "❌ **04.11.25**: `18:00 - 21:30` (💡 світла не буде)"
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
        "❌ **04.11.25**: `18:30 - 21:00` (💡 світла не буде)"
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
        "❌ **04.11.25**: `18:30 - 21:00` (💡 світла не буде)\n"
        "❌ **05.11.25**: `15:00 - 18:30` (💡 світла не буде)"
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
        "❌ **04.11.25**: `18:30 - 21:30` (💡 світла не буде)\n"
        "❌ **05.11.25**: `15:30 - 18:30` (💡 світла не буде)"
    )
    assert format_shutdown_message(mock_data).strip() == expected_output.strip()