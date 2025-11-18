import pytest
from httpx import AsyncClient, ASGITransport
from pytest_asyncio import fixture as async_fixture 
from typing import Dict, Any
import sys
import os
from pathlib import Path

# --- БЛОК ДЛЯ КОРРЕКТНОГО ИМПОРТА ОСНОВНОГО МОДУЛЯ ---
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dtek_parser_api import app
# -----------------------------------------------------

# --- ФИКСАЦИЯ ДАННЫХ (DRY) ---

MOCK_PARSER_RESULT_OUTAGE = {
    "data": {
        "city": "м. Київ",
        "street": "вул. Хрещатик",
        "house_num": "2",
        "group": "2",
        "schedule": {
            "04.11": [
                {"shutdown": "00:00–03:00"},
                {"shutdown": "03:00–04:30"},
            ],
            "05.11": [
                {"shutdown": "12:00–15:00"},
                {"shutdown": "15:00–18:00"},
            ]
        }
    },
    "json_path": Path("out/test-file.json"),
    "png_path": Path("out/test-file.png")
}

MOCK_PARSER_RESULT_NO_OUTAGE = {
    "data": {
        "city": "м. Одеса",
        "street": "вул. Дерибасівська",
        "house_num": "1",
        "group": "1",
        "schedule": {
            "04.11": [],
            "05.11": []
        }
    },
    "json_path": Path("out/test-file.json"),
    "png_path": Path("out/test-file.png")
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
        "dtek_parser_api.run_parser_service", 
        return_value=MOCK_PARSER_RESULT_OUTAGE
    )
    
    response = await client.get("/shutdowns", params={
        "city": "м. Київ", 
        "street": "вул. Хрещатик", 
        "house": "2"
    })
    
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["group"] == "2"
    assert json_data["city"] == "м. Київ"
    assert json_data["street"] == "вул. Хрещатик"
    assert json_data["house_num"] == "2"
    assert "04.11" in json_data["schedule"]
    assert len(json_data["schedule"]["04.11"]) == 2


@pytest.mark.asyncio
async def test_200_successful_no_outage_response(client: AsyncClient, mocker):
    """Тест 2: Успешный ответ (200) с данными, где нет отключений."""
    mocker.patch(
        "dtek_parser_api.run_parser_service", 
        return_value=MOCK_PARSER_RESULT_NO_OUTAGE
    )
    
    response = await client.get("/shutdowns", params={
        "city": "м. Одеса", 
        "street": "вул. Дерибасівська", 
        "house": "1"
    })
    
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["group"] == "1"
    assert json_data["city"] == "м. Одеса"
    assert len(json_data["schedule"]["04.11"]) == 0


@pytest.mark.asyncio
async def test_500_parser_exception(client: AsyncClient, mocker):
    """Тест 3: Парсер выбросил исключение (500)."""
    error_msg = "Не удалось загрузить страницу DTEK"
    
    mocker.patch(
        "dtek_parser_api.run_parser_service", 
        side_effect=Exception(error_msg)
    )
    
    response = await client.get("/shutdowns", params={
        "city": "NonExistentCity", 
        "street": "Street", 
        "house": "1"
    })
    
    assert response.status_code == 500
    assert error_msg in response.json()["detail"]


@pytest.mark.asyncio
async def test_500_empty_data_from_parser(client: AsyncClient, mocker):
    """Тест 4: Парсер вернул результат без ключа 'data' (500)."""
    
    mocker.patch(
        "dtek_parser_api.run_parser_service", 
        return_value={"json_path": Path("test.json"), "png_path": Path("test.png")}
    )
    
    response = await client.get("/shutdowns", params={
        "city": "м. Київ", 
        "street": "Empty", 
        "house": "1"
    })
    
    assert response.status_code == 500
    assert "empty data" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_500_parser_returns_none_data(client: AsyncClient, mocker):
    """Тест 5: Парсер вернул None в ключе 'data' (500)."""
    
    mocker.patch(
        "dtek_parser_api.run_parser_service", 
        return_value={"data": None, "json_path": Path("test.json"), "png_path": Path("test.png")}
    )
    
    response = await client.get("/shutdowns", params={
        "city": "м. Київ", 
        "street": "Empty", 
        "house": "1"
    })
    
    assert response.status_code == 500
    assert "empty data" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_422_missing_query_parameter(client: AsyncClient):
    """Тест 6: Ошибка валидации FastAPI (422) при отсутствии параметра."""
    response = await client.get("/shutdowns", params={
        "city": "м. Київ", 
        "street": "вул. Хрещатик" 
        # house отсутствует
    })
    
    assert response.status_code == 422
    assert "house" in str(response.json()["detail"][0]["loc"])
    assert response.json()["detail"][0]["type"] == "missing"


@pytest.mark.asyncio
async def test_422_missing_all_parameters(client: AsyncClient):
    """Тест 7: Ошибка валидации FastAPI (422) при отсутствии всех параметров."""
    response = await client.get("/shutdowns")
    
    assert response.status_code == 422
    detail = response.json()["detail"]
    
    # Проверяем, что все три параметра указаны как отсутствующие
    missing_params = [item["loc"][-1] for item in detail if item["type"] == "missing"]
    assert "city" in missing_params
    assert "street" in missing_params
    assert "house" in missing_params


@pytest.mark.asyncio
async def test_200_special_characters_in_params(client: AsyncClient, mocker):
    """Тест 8: Успешная обработка параметров со специальными символами."""
    mock_result = {
        "data": {
            "city": "м. Дніпро",
            "street": "вул. 8-го Березня",
            "house_num": "15/17",
            "group": "3",
            "schedule": {"06.11": [{"shutdown": "09:00–12:00"}]}
        },
        "json_path": Path("out/test.json"),
        "png_path": Path("out/test.png")
    }
    
    mocker.patch(
        "dtek_parser_api.run_parser_service", 
        return_value=mock_result
    )
    
    response = await client.get("/shutdowns", params={
        "city": "м. Дніпро",
        "street": "вул. 8-го Березня",
        "house": "15/17"
    })
    
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["house_num"] == "15/17"
    assert json_data["street"] == "вул. 8-го Березня"