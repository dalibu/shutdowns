import pytest
import aiohttp
import asyncio
from aioresponses import aioresponses
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

# --- БЛОК ФУНКЦИЙ ИЗ dtek_telegram_bot.py, необходимых для теста ---

API_BASE_URL = "http://dtek_api:8000" # Используем тестовый адрес

# --- НОВАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ---
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
    (Скопировано из dtek_telegram_bot.py)
    """
    params = {
        "city": city,
        "street": street,
        "house": house
    }
    
    # aioresponses будет перехватывать этот вызов
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{API_BASE_URL}/shutdowns", params=params, timeout=45) as response: 
                if response.status == 404:
                    # 📌 Генерируем ValueError (как ожидалось в тесте)
                    raise ValueError("Графік для цієї адреси не знайдено.")
                
                response.raise_for_status()
                return await response.json()

        except aiohttp.ClientError as e:
            # Ловит ошибки соединения, таймауты и другие ошибки HTTP-клиента
            raise ConnectionError("Помилка підключення до парсера. Спробуйте пізніше.")
        # ❌ УДАЛЕН БЛОК except Exception as e:
        # Теперь ValueError выходит напрямую.
        # Любые другие непредвиденные ошибки выйдут как есть.

# --- ФИКСАЦИЯ ДАННЫХ (MOCK PAYLOADS) ---

# 1. Ответ: Есть отключения
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

# 2. Ответ: Нет отключений
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

# --- ТЕСТОВЫЕ ФУНКЦИИ ---

@pytest.mark.asyncio
async def test_successful_outage_response():
    """Тестирование успешного ответа с запланированными отключениями."""
    
    url = create_mock_url("Київ", "Хрещатик", "2") # Полный URL с параметрами
    
    with aioresponses() as m:
        # 📌 ИСПРАВЛЕНО: Передаем полный URL
        m.get(
            url, 
            payload=MOCK_RESPONSE_OUTAGE, 
            status=200
        )
        
        data = await get_shutdowns_data("Київ", "Хрещатик", "2")
        
        assert data['group'] == "2"
        assert data == MOCK_RESPONSE_OUTAGE

@pytest.mark.asyncio
async def test_successful_no_outage_response():
    """Тестирование успешного ответа без запланированных отключений."""
    
    url = create_mock_url("Одеса", "Дерибасівська", "1") # Полный URL с параметрами

    with aioresponses() as m:
        # 📌 ИСПРАВЛЕНО: Передаем полный URL
        m.get(
            url, 
            payload=MOCK_RESPONSE_NO_OUTAGE, 
            status=200
        )
        
        data = await get_shutdowns_data("Одеса", "Дерибасівська", "1")
        
        assert data['group'] == "1"
        assert data == MOCK_RESPONSE_NO_OUTAGE

@pytest.mark.asyncio
async def test_not_found_404_response():
    """Тестирование, когда API возвращает 404 (адрес не найден)."""
    
    url = create_mock_url("Неіснуюче", "Вулиця", "1") # Полный URL с параметрами

    with aioresponses() as m:
        # 📌 ИСПРАВЛЕНО: Передаем полный URL
        m.get(
            url, 
            status=404
        )
        
        # Ожидаем, что функция вызовет ValueError
        with pytest.raises(ValueError) as excinfo:
            await get_shutdowns_data("Неіснуюче", "Вулиця", "1")
            
        assert "Графік для цієї адреси не знайдено." in str(excinfo.value)

@pytest.mark.asyncio
async def test_connection_error_mocked():
    """Тестирование ошибки соединения с API с помощью aioresponses."""
    
    # В этом тесте URL не так важен, но лучше его определить
    url = create_mock_url("Київ", "Хрещатик", "2") 

    with aioresponses() as m:
        # Используем exception=... для имитации ошибки сети
        m.get(
            url, 
            exception=aiohttp.ClientConnectorError(None, OSError('Mock connection error'))
        )

        # Ожидаем, что функция перехватит aiohttp.ClientError и вызовет ConnectionError
        with pytest.raises(ConnectionError) as excinfo:
            await get_shutdowns_data("Київ", "Хрещатик", "2")
            
        assert "Помилка підключення до парсера." in str(excinfo.value)