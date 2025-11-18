import pytest
import asyncio
import json
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path
import os
import logging
from playwright.async_api import TimeoutError 

# 📌 ИМПОРТ: Используем относительный импорт для Pytest
try:
    from dtek_parser import (
        run_parser_service, 
        DEFAULT_CITY, 
        DEFAULT_STREET, 
        DEFAULT_HOUSE, 
    )
except ImportError:
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from dtek_parser import (
        run_parser_service, 
        DEFAULT_CITY, 
        DEFAULT_STREET, 
        DEFAULT_HOUSE, 
    )

logging.getLogger("dtek_parser").setLevel(logging.CRITICAL) 

# --- 🛠️ Фикстуры для тестов ---

@pytest.fixture
def mock_browser_and_page():
    """
    Фикстура для создания мок-объектов browser и page.
    """
    # Создаем мок для page
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.click = AsyncMock()
    mock_page.fill = AsyncMock()
    mock_page.type = AsyncMock()
    mock_page.wait_for_selector = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.screenshot = AsyncMock()
    mock_page.close = AsyncMock()
    
    # Создаем мок для browser
    mock_browser = AsyncMock()
    mock_browser.new_page = AsyncMock(return_value=mock_page)
    mock_browser.close = AsyncMock()
    
    return mock_browser, mock_page


# --- 🧪 Тесты ---

@pytest.mark.asyncio
@patch('dtek_parser.create_combined_screenshot', new_callable=AsyncMock)
@patch('dtek_parser.async_playwright')
async def test_parser_success(mock_async_playwright, mock_create_combined_screenshot, mock_browser_and_page):
    """Тест успешного выполнения парсера с корректными данными."""
    
    # Распаковываем моки
    mock_browser, mock_page = mock_browser_and_page
    
    # Настраиваем async_playwright для возврата наших моков
    mock_playwright_instance = AsyncMock()
    mock_playwright_instance.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_async_playwright.return_value.__aenter__.return_value = mock_playwright_instance
    
    # --- Подготовка данных для таблиц ---
    time_headers_text = ["00-03", "03-06", "06-09", "09-12", "12-15", "15-18", "18-21", "21-24"]
    data_cells_classes_day0 = [
        "cell-scheduled discon-status", 
        "clear discon-status", 
        "cell-scheduled discon-status", 
        "clear discon-status",
        "cell-first-half discon-status",
        "clear discon-status",
        "cell-scheduled discon-status",
        "clear discon-status"
    ]
    data_cells_classes_day1 = ["clear discon-status"] * 8
    
    # --- Настройка моков для таблиц ---
    
    # День 0 - таблица
    mock_table_day0 = MagicMock()
    mock_headers_locator_day0 = MagicMock()
    mock_cells_locator_day0 = MagicMock()
    mock_headers_locator_day0.all = AsyncMock(return_value=[
        MagicMock(inner_text=AsyncMock(return_value=h)) for h in time_headers_text
    ])
    mock_cells_locator_day0.all = AsyncMock(return_value=[
        MagicMock(get_attribute=AsyncMock(return_value=c)) for c in data_cells_classes_day0
    ])
    
    def table_locator_day0(selector):
        if "thead" in selector:
            return mock_headers_locator_day0
        elif "tbody" in selector:
            return mock_cells_locator_day0
        return MagicMock()
    
    mock_table_day0.locator = MagicMock(side_effect=table_locator_day0)
    
    # День 1 - таблица
    mock_table_day1 = MagicMock()
    mock_headers_locator_day1 = MagicMock()
    mock_cells_locator_day1 = MagicMock()
    mock_headers_locator_day1.all = AsyncMock(return_value=[
        MagicMock(inner_text=AsyncMock(return_value=h)) for h in time_headers_text
    ])
    mock_cells_locator_day1.all = AsyncMock(return_value=[
        MagicMock(get_attribute=AsyncMock(return_value=c)) for c in data_cells_classes_day1
    ])
    
    def table_locator_day1(selector):
        if "thead" in selector:
            return mock_headers_locator_day1
        elif "tbody" in selector:
            return mock_cells_locator_day1
        return MagicMock()
    
    mock_table_day1.locator = MagicMock(side_effect=table_locator_day1)
    
    # --- Настройка моков для table_locators ---
    mock_table_locators = MagicMock()
    
    async def mock_table_count():
        return 2
    mock_table_locators.count = mock_table_count
    
    def mock_table_nth(index):
        result = MagicMock()
        if index == 0:
            result.locator = MagicMock(return_value=mock_table_day0)
        elif index == 1:
            result.locator = MagicMock(return_value=mock_table_day1)
        return result
    
    mock_table_locators.nth = MagicMock(side_effect=mock_table_nth)
    
    # --- Настройка моков для date_locators ---
    mock_date_locators = MagicMock()
    
    def mock_date_nth(index):
        result = MagicMock()
        span_mock = MagicMock()
        if index == 0:
            span_mock.inner_text = AsyncMock(return_value="08.11")
        elif index == 1:
            span_mock.inner_text = AsyncMock(return_value="09.11")
        result.locator = MagicMock(return_value=span_mock)
        return result
    
    mock_date_locators.nth = MagicMock(side_effect=mock_date_nth)
    
    # --- Настройка page.locator() ---
    def page_locator(selector):
        # Для группы
        if "#group-name" in selector:
            mock = MagicMock()
            mock.inner_text = AsyncMock(return_value="3")
            return mock
        # Для адреса
        elif "input#city" in selector:
            mock = MagicMock()
            mock.input_value = AsyncMock(return_value="м. Дніпро")
            return mock
        elif "input#street" in selector:
            mock = MagicMock()
            mock.input_value = AsyncMock(return_value="вул. Сонячна набережна")
            return mock
        elif "input#house_num" in selector:
            mock = MagicMock()
            mock.input_value = AsyncMock(return_value="6")
            return mock
        # Для таблиц
        elif "discon-fact-table" in selector:
            return mock_table_locators
        # Для дат
        elif "div.date" in selector:
            return mock_date_locators
        # Для автокомплита с has-text
        elif "has-text" in selector:
            mock = MagicMock()
            mock.first = MagicMock(click=AsyncMock())
            return mock
        # По умолчанию
        return MagicMock()
    
    mock_page.locator = MagicMock(side_effect=page_locator)
    
    # --- Запуск парсера ---
    result = await run_parser_service(
        city="м. Дніпро", 
        street="вул. Сонячна набережна", 
        house="6"
    )

    # --- Проверки ---
    
    # Проверяем структуру результата
    assert "data" in result
    assert "json_path" in result
    assert "png_path" in result
    
    data = result["data"]
    
    # Проверяем адрес
    assert data["city"] == "м. Дніпро"
    assert data["street"] == "вул. Сонячна набережна"
    assert data["house_num"] == "6"
    assert data["group"] == "3"

    # Проверка первого дня (08.11)
    assert "08.11" in data["schedule"]
    day1_slots = data["schedule"]["08.11"]
    assert len(day1_slots) == 4  # Ожидаем 4 объединенных слота
    assert day1_slots[0]["shutdown"] == "00:00–03:00"
    assert day1_slots[1]["shutdown"] == "06:00–09:00"
    assert day1_slots[2]["shutdown"] == "12:00–12:30" 
    assert day1_slots[3]["shutdown"] == "18:00–21:00"

    # Проверка второго дня (09.11)
    assert "09.11" in data["schedule"]
    day2_slots = data["schedule"]["09.11"]
    assert len(day2_slots) == 0
    
    # Проверка сохранения JSON
    json_output = json.dumps(data, indent=4, ensure_ascii=False)
    assert "м. Дніпро" in json_output
    assert "\"group\": \"3\"" in json_output
    assert "06:00–09:00" in json_output


@pytest.mark.asyncio
@patch('dtek_parser.cleanup_old_files')
@patch('dtek_parser.async_playwright')
async def test_parser_timeout_handling(mock_async_playwright, mock_cleanup_old_files, mock_browser_and_page):
    """Тест обработки таймаута при загрузке страницы."""
    
    # Распаковываем моки
    mock_browser, mock_page = mock_browser_and_page
    
    # Настраиваем async_playwright для возврата наших моков
    mock_playwright_instance = AsyncMock()
    mock_playwright_instance.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_async_playwright.return_value.__aenter__.return_value = mock_playwright_instance
    
    # --- Настройка мока для page.locator() ---
    mock_locator_result = MagicMock()
    mock_locator_result.wait_for = AsyncMock()
    mock_locator_result.input_value = AsyncMock(side_effect=["м. Дніпро", "вул. Сонячна набережна", "6"])
    mock_locator_result.first = MagicMock(click=AsyncMock())
    mock_locator_result.click = AsyncMock()

    mock_page.locator = MagicMock(return_value=mock_locator_result)
    
    # Мокируем поведение для имитации таймаута
    def mock_wait_for_selector(selector, **kwargs):
        # Пропускаем успешные вызовы для автокомплита
        if "autocomplete-list" in selector and kwargs.get('state') == 'visible':
            return AsyncMock()
        if "autocomplete-list" in selector and kwargs.get('state') == 'hidden':
            return AsyncMock()
            
        # Имитируем таймаут на ожидании активации поля house_num
        if selector == "input#house_num:not([disabled])":
            raise TimeoutError("Не удалось перейти к следующему шагу. Проверьте адрес.")
             
        # Для всех остальных вызовов возвращаем успешный мок
        return AsyncMock() 

    mock_page.wait_for_selector.side_effect = mock_wait_for_selector
    
    # Проверяем, что run_parser_service пробрасывает TimeoutError
    with pytest.raises(TimeoutError) as excinfo:
        await run_parser_service(
            city="м. Дніпро", 
            street="вул. Сонячна набережна", 
            house="6"
        )

    # Убеждаемся, что в сообщении есть ожидаемый текст
    assert "Не удалось перейти к следующему шагу" in str(excinfo.value)
    
    # Проверяем, что cleanup_old_files был вызван
    mock_cleanup_old_files.assert_called_once()
    
    # Проверяем, что браузер был закрыт
    mock_browser.close.assert_called_once()