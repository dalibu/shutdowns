import pytest
from httpx import AsyncClient, ASGITransport
from pytest_asyncio import fixture as async_fixture 
from typing import Dict, Any
import sys
import os

# --- БЛОК ДЛЯ КОРРЕКТНОГО ИМПОРТА ОСНОВНОГО МОДУЛЯ ---
# Добавляет корневую папку ('../') в путь импорта
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dtek_parser_api import app
# -----------------------------------------------------

# --- ФИКСАЦИЯ ДАННЫХ (DRY) ---

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

# --- ФИКСТУРЫ ---

@async_fixture(scope="session") 
async def client():
    """
    Фикстура для асинхронного HTTP-клиента httpx.
    Использует ASGITransport для устранения DeprecationWarning.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

# --- ТЕСТЫ API ЭНДПОИНТА /shutdowns ---

@pytest.mark.asyncio
async def test_200_successful_outage_response(client: AsyncClient, mocker):
    """Тест 1: Успешный ответ (200) с данными об отключении."""
    mocker.patch(
        "dtek_parser_api.scrape_dtek_schedule", 
        return_value=MOCK_RESPONSE_OUTAGE
    )
    
    response = await client.get("/shutdowns", params={
        "city": "Київ", "street": "Хрещатик", "house": "2"
    })
    
    assert response.status_code == 200
    assert response.json()["group"] == "2"
    # Примечание: В реальном тесте, если бы мы не мокировали scrape_dtek_schedule,
    # мы бы проверили, что функция scrape_dtek_schedule была вызвана с очищенными "Київ" и "Хрещатик"
    # Но так как она мокируется, мы проверяем только, что API работает с моком.


@pytest.mark.asyncio
async def test_200_successful_no_outage_response(client: AsyncClient, mocker):
    """Тест 2: Успешный ответ (200) с данными, где нет отключений."""
    mocker.patch(
        "dtek_parser_api.scrape_dtek_schedule", 
        return_value=MOCK_RESPONSE_NO_OUTAGE
    )
    
    response = await client.get("/shutdowns", params={
        "city": "Одеса", "street": "Дерибасівська", "house": "1"
    })
    
    assert response.status_code == 200
    assert response.json()["group"] == "1"


@pytest.mark.asyncio
async def test_404_address_not_found(client: AsyncClient, mocker):
    """Тест 3: Адрес не найден (404)."""
    error_msg = "Графік для цієї адреси не знайдено."
    
    mocker.patch(
        "dtek_parser_api.scrape_dtek_schedule", 
        side_effect=ValueError(error_msg)
    )
    
    response = await client.get("/shutdowns", params={
        "city": "NonExistentCity", "street": "Street", "house": "1"
    })
    
    assert response.status_code == 404
    assert response.json()["detail"] == error_msg


@pytest.mark.asyncio
async def test_500_internal_parsing_error(client: AsyncClient, mocker):
    """Тест 4: Внутренняя ошибка парсинга (500)."""
    
    mocker.patch(
        "dtek_parser_api.scrape_dtek_schedule", 
        side_effect=ValueError("Неожиданная ошибка при обработке данных DTEK.")
    )
    
    response = await client.get("/shutdowns", params={
        "city": "ParsingErrorCity", "street": "Street", "house": "1"
    })
    
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal Parsing Error"


@pytest.mark.asyncio
async def test_404_empty_data_from_parser(client: AsyncClient, mocker):
    """Тест 5: Парсер вернул пустой словарь (404)."""
    
    mocker.patch(
        "dtek_parser_api.scrape_dtek_schedule", 
        return_value={}
    )
    
    response = await client.get("/shutdowns", params={
        "city": "Kyiv", "street": "Empty", "house": "1"
    })
    
    assert response.status_code == 404
    assert "пустой ответ" in response.json()["detail"]


@pytest.mark.asyncio
async def test_422_missing_query_parameter(client: AsyncClient):
    """Тест 6: Ошибка валидации FastAPI (422) при отсутствии параметра."""
    response = await client.get("/shutdowns", params={
        "city": "Kyiv", "street": "Khreschatyk" 
        # house отсутствует
    })
    
    assert response.status_code == 422
    assert "house" in response.json()["detail"][0]["loc"]
    assert response.json()["detail"][0]["type"] == "missing"