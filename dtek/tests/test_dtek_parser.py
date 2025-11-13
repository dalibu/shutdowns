import pytest
import asyncio
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

# --- 🛠️ Фикстуры и Моки для Юнит-Тестов (Изолированная логика) ---

@pytest.fixture
def mock_playwright_components():
    """
    Мок-объекты для имитации Playwright API для юнит-тестов.
    """
    
    # 1. Мок-объект, возвращаемый после page.locator(...)
    final_locator_mock = MagicMock()
    
    # --- АСИНХРОННЫЕ МЕТОДЫ (для самого локатора) ---
    final_locator_mock.wait_for = AsyncMock() 
    final_locator_mock.click = AsyncMock()  
    final_locator_mock.screenshot = AsyncMock()
    
    final_locator_mock.inner_text = AsyncMock(side_effect=[
        "Черга 3", # Группа
        "08.11",   # Дата 1
        "09.11"    # Дата 2
    ])
    
    # 📌 6 вызовов для input_value (3 в цикле + 3 для финального извлечения адреса)
    final_locator_mock.input_value = AsyncMock(side_effect=[
        DEFAULT_CITY,   
        DEFAULT_STREET, 
        DEFAULT_HOUSE,  
        DEFAULT_CITY,   
        DEFAULT_STREET, 
        DEFAULT_HOUSE   
    ])
    
    # --- МОК-ОБЪЕКТЫ ДЛЯ ЯЧЕЕК ТАБЛИЦЫ ---

    # Список ожидаемых атрибутов класса
    cell_class_attributes = [
        "cell-scheduled", "", "cell-first-half",  
        "cell-scheduled", "cell-second-half", "" 
    ]
    
    # Создаем итератор для последовательной выдачи классов
    class_attr_iterator = iter(cell_class_attributes)
    
    # Функция-конструктор мока для ячейки данных
    def create_data_cell_mock(iterator):
        mock = MagicMock()
        try:
            class_attr = next(iterator)
        except StopIteration:
            class_attr = "" 
            
        # 🌟 ИСПРАВЛЕНИЕ: td_element.get_attribute должен быть AsyncMock
        mock.get_attribute = AsyncMock(return_value=class_attr) 
        return mock
    
    # Создаем моки для ячеек
    data_cells_mocks = [create_data_cell_mock(class_attr_iterator) for _ in range(6)]
    data_cells_day1 = data_cells_mocks[0:3]
    data_cells_day2 = data_cells_mocks[3:6]
    
    # Настройка заголовков времени
    mock_time_headers = [MagicMock() for _ in range(3)]
    for i, header in enumerate(mock_time_headers):
        # 🌟 th_element.inner_text должен быть AsyncMock
        header.inner_text = AsyncMock(return_value=f"08:00–12:00\n{i}")

    # final_locator_mock.all возвращает списки заголовков и ячеек данных
    final_locator_mock.all = AsyncMock(side_effect=[
        mock_time_headers, 
        data_cells_day1, 
        mock_time_headers, 
        data_cells_day2, 
    ])
    
    # --- ДРУГИЕ МЕТОДЫ/СВОЙСТВА (синхронные) ---
    async def count_tables(): return 2 
    final_locator_mock.count = count_tables
    
    # Мокирование свойства .first для чейнинга (для await locator.first.click())
    chain_member_mock = MagicMock()
    chain_member_mock.click = AsyncMock() 
    final_locator_mock.first = chain_member_mock 
    
    # .locator() и .nth() - методы, которые вызываются и возвращают наш основной мок
    final_locator_mock.locator.return_value = final_locator_mock 
    final_locator_mock.nth.return_value = final_locator_mock 


    # 2. Мок-объект для Page
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.click = AsyncMock()
    mock_page.fill = AsyncMock()
    mock_page.type = AsyncMock()
    mock_page.wait_for_selector = AsyncMock()
    mock_page.screenshot = AsyncMock()
    
    # page.locator() - синхронный метод, возвращает MagicMock
    mock_page.locator = MagicMock(return_value=final_locator_mock) 
    
    # 3. Мок-объект для Browser
    mock_browser = AsyncMock()
    mock_browser.new_page.return_value = mock_page
    mock_browser.close = AsyncMock()
    
    # 4. Мок-объект для Chromium 
    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)
    
    # 5. Мок-объект для Playwright
    mock_p = MagicMock()
    mock_p.chromium = mock_chromium 

    return mock_p, mock_page


# --- 🧪 Юнит-Тесты (Mocking Only) ---

@pytest.mark.asyncio
@patch('dtek_parser.async_playwright')
async def test_parser_success(mock_async_playwright, mock_playwright_components):
    """
    Тест успешного выполнения run_parser_service с мокированием Playwright.
    """
    
    mock_async_playwright.return_value.__aenter__.return_value = mock_playwright_components[0]
    
    result = await run_parser_service(DEFAULT_CITY, DEFAULT_STREET, DEFAULT_HOUSE, is_debug=True, skip_input_on_debug=True)
    
    assert isinstance(result, dict)
    assert result["group"] == "3" 
    
    date_1 = "08.11"
    assert len(result["schedule"]) == 2
    assert date_1 in result["schedule"]
    
    mock_playwright_components[1].locator().screenshot.assert_called_once()


@pytest.mark.asyncio
@patch('dtek_parser.async_playwright')
async def test_parser_timeout_handling(mock_async_playwright, mock_playwright_components):
    """
    Тест на обработку ошибки TimeoutError.
    """
    
    mock_page = mock_playwright_components[1]
    
    mock_page.wait_for_selector.side_effect = [
        AsyncMock(), # Город - Успешно
        AsyncMock(), # Улица - Успешно
        TimeoutError("Test Timeout: Results did not load."), # Дом - Ошибка
    ]

    mock_async_playwright.return_value.__aenter__.return_value = mock_playwright_components[0]
    
    # Важно: тест вызывает функцию без is_debug, так что input не должен вызываться и в случае ошибки
    with pytest.raises(TimeoutError) as excinfo:
        await run_parser_service(DEFAULT_CITY, DEFAULT_STREET, DEFAULT_HOUSE)
    
    assert "Ошибка активации следующего шага или загрузки результатов" in str(excinfo.value)
    
    mock_playwright_components[0].chromium.launch.return_value.close.assert_called_once()