import asyncio
import json
import re
import argparse
import os
from pathlib import Path
import logging
from logging import DEBUG, INFO, WARNING, ERROR
from typing import List, Dict, Any
from datetime import datetime
import pytz
import uuid

from common.formatting import merge_consecutive_slots

# Botasaurus imports
from botasaurus.browser import browser, Driver
import time

# --- 1. Конфигурация Логирования ---
LOGGING_LEVEL = INFO
logger = logging.getLogger(__name__)
logger.setLevel(LOGGING_LEVEL)
logger.propagate = False  # Отключаем дублирование логов

handler = logging.StreamHandler()

def custom_time(*args):
    """Возвращает текущее время в Киевском часовом поясе для логирования."""
    return datetime.now(pytz.timezone('Europe/Kyiv')).timetuple()

formatter = logging.Formatter(
    '%(asctime)s %(name)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
formatter.converter = custom_time
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)
# ------------------------------------

# --- 2. Конфигурация по умолчанию ---

DEFAULT_CITY = "м. Дніпро"
DEFAULT_STREET = "вул. Сонячна набережна"
DEFAULT_HOUSE = "6"

@browser(
    headless=True,
    block_images=False,
    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    reuse_driver=False,
    output=None  # Disable default JSON file output
)
def run_parser_service_botasaurus(driver: Driver, data: Dict[str, Any]) -> Dict[str, Any]:
    """Основная логика парсинга с использованием Botasaurus."""
    
    city = data.get('city', DEFAULT_CITY)
    street = data.get('street', DEFAULT_STREET)
    house = data.get('house', DEFAULT_HOUSE)
    is_debug = data.get('is_debug', False)
    
    logger.info(f"Режим запуска: {'Headful (отладка)' if is_debug else 'Headless'}")

    ADDRESS_DATA = [
        {"selector": "input#city", "value": city, "autocomplete": "div#cityautocomplete-list"},
        {"selector": "input#street", "value": street, "autocomplete": "div#streetautocomplete-list"},
        {"selector": "input#house_num", "value": house, "autocomplete": "div#house_numautocomplete-list"},
    ]

    try:
        URL = "https://www.dtek-dnem.com.ua/ua/shutdowns"
        logger.info(f"Загрузка страницы: {URL}")
        driver.google_get(URL)
        
        # Дополнительное ожидание для загрузки всех скриптов
        time.sleep(3)
        
        # Попытка закрыть модальное окно
        try:
            modal_button = driver.select("button.modal__close.m-attention__close", wait=5)
            if modal_button:
                modal_button.click()
                logger.info("Модальное окно закрыто")
                time.sleep(2)
        except Exception as e:
            logger.info(f"Модальное окно не найдено: {e}")
        
        # Ожидание появления формы
        city_input = driver.select("input#city", wait=15)
        if not city_input:
            raise Exception("Поле ввода города не найдено")
        
        logger.info("Форма загружена и готова к вводу")

        # Ввод адреса - используем JavaScript для надежности
        for i, data_item in enumerate(ADDRESS_DATA):
            selector = data_item["selector"]
            value = data_item["value"]
            autocomplete = data_item["autocomplete"]
            is_last = (i == len(ADDRESS_DATA) - 1)
            
            logger.info(f"Ввод в поле {selector}: {value}")
            
            # Используем JavaScript для ввода текста и триггера событий
            js_input = f"""
                var input = document.querySelector('{selector}');
                if (input) {{
                    // Фокусируемся на поле
                    input.focus();
                    
                    // Очищаем поле
                    input.value = '';
                    
                    // Триггерим событие input для очистки
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    
                    // Вводим текст посимвольно с событиями
                    var text = '{value}';
                    for (var i = 0; i < text.length; i++) {{
                        input.value += text[i];
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('keydown', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('keyup', {{ bubbles: true }}));
                    }}
                    
                    // Финальные события
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    input.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                    
                    return true;
                }}
                return false;
            """
            
            result = driver.run_js(js_input)
            if not result:
                raise Exception(f"Не удалось ввести текст в поле {selector}")
            
            logger.info(f"Текст введен в {selector}")
            
            # Ожидание автокомплита - увеличиваем время
            time.sleep(3)
            
            # Клик по автокомплиту через JavaScript
            if i == 0:
                # Для города ищем точное совпадение
                js_click = f"""
                    var autocomplete = document.querySelector('{autocomplete}');
                    if (autocomplete) {{
                        var items = autocomplete.querySelectorAll('div');
                        var clicked = false;
                        for (var item of items) {{
                            if (item.textContent.includes('{value}')) {{
                                item.click();
                                clicked = true;
                                break;
                            }}
                        }}
                        if (!clicked && items.length > 0) {{
                            items[0].click();
                        }}
                        return clicked || items.length > 0;
                    }}
                    return false;
                """
                result = driver.run_js(js_click)
                if result:
                    logger.info(f"Выбран элемент автокомплита для города")
                else:
                    logger.warning("Автокомплит не найден для города")
            else:
                # Для улицы и дома - первый элемент
                js_click = f"""
                    var autocomplete = document.querySelector('{autocomplete}');
                    if (autocomplete) {{
                        var items = autocomplete.querySelectorAll('div');
                        if (items.length > 0) {{
                            items[0].click();
                            return true;
                        }}
                    }}
                    return false;
                """
                result = driver.run_js(js_click)
                if result:
                    logger.info(f"Выбран элемент автокомплита")
                else:
                    logger.warning(f"Автокомплит не найден для {selector}")
            
            # Пауза после выбора
            time.sleep(1)
            
            # Для последнего поля ждем появления таблицы
            if is_last:
                logger.info("Ожидание таблицы результатов...")
                # Убрали time.sleep(5) - таблица должна появиться быстро
                results_table = driver.select("#discon-fact > div.discon-fact-tables", wait=30)
                if not results_table:
                    raise Exception("Таблица результатов не появилась")

        # Сбор данных
        group_text = driver.get_text("#discon_form #group-name > span")
        
        aggregated_result = {
            "city": driver.select("input#city").get_attribute("value"),
            "street": driver.select("input#street").get_attribute("value"),
            "house_num": driver.select("input#house_num").get_attribute("value"),
            "group": group_text.replace("Черга", "").strip(),
            "schedule": {}
        }

        # Парсинг таблиц
        table_elements = driver.select_all("#discon-fact > div.discon-fact-tables > div.discon-fact-table")
        date_elements = driver.select_all("#discon-fact > div.dates > div.date")

        count = len(table_elements)
        for i in range(count):
            date_elem = date_elements[i]
            date_text = date_elem.text.strip()
            
            # Парсинг даты
            match = re.search(r'(\d{2})\.(\d{2})\.(\d{2})', date_text)
            if not match:
                continue
            
            day, month, year = match.groups()
            date_key = f"{day}.{month}.{year}"
            
            # Парсинг таблицы через JavaScript
            js_parse_table = f"""
                var tables = document.querySelectorAll('#discon-fact > div.discon-fact-tables > div.discon-fact-table');
                var table = tables[{i}];
                var rows = table.querySelectorAll('tbody > tr');
                var shutdowns = [];
                
                for (var row of rows) {{
                    var cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {{
                        var timeRange = cells[0].textContent.trim();
                        var statusCell = cells[1];
                        var statusClass = statusCell.className;
                        
                        var statusText = "";
                        if (statusClass.includes('cell-scheduled')) {{
                            statusText = "відключення";
                        }} else if (statusClass.includes('cell-maybe')) {{
                            statusText = "можливе відключення";
                        }}
                        
                        // Если есть статус (отключение или возможное), добавляем в список
                        if (statusText && timeRange && timeRange !== '—') {{
                            // Преобразуем формат времени из "08-09" в "08:00–09:00"
                            if (timeRange.match(/^\\d{{2}}-\\d{{2}}$/)) {{
                                var parts = timeRange.split('-');
                                timeRange = parts[0] + ":00–" + parts[1] + ":00";
                            }}
                            
                            shutdowns.push({{
                                shutdown: timeRange,
                                status: statusText
                            }});
                        }}
                    }}
                }}
                
                return shutdowns;
            """
            
            shutdowns = driver.run_js(js_parse_table)
            
            if shutdowns:
                # Merge slots for cleaner logging and output
                temp_schedule = {date_key: shutdowns}
                merged_temp = merge_consecutive_slots(temp_schedule)
                merged_shutdowns = merged_temp[date_key]
                
                aggregated_result["schedule"][date_key] = merged_shutdowns
                logger.info(f"Дата {date_key}: найдено {len(shutdowns)} слотів, об'єднано в {len(merged_shutdowns)} періодів.")
                # Детальное логирование каждого периода
                for idx, slot in enumerate(merged_shutdowns, 1):
                    logger.info(f"Період {idx}: {slot.get('shutdown', 'N/A')} ({slot.get('status', 'N/A')})")
            else:
                aggregated_result["schedule"][date_key] = []
                logger.info(f"Дата {date_key}: найдено 0 отключений.")
                
        # Логирование результата в DEBUG (только если нужно)
        # logger.debug(json.dumps(aggregated_result, ensure_ascii=False, indent=2))

        return {
            "data": aggregated_result
        }

    except Exception as e:
        logger.error(f"Ошибка Botasaurus: {e}", exc_info=True)
        raise

# Wrapper для обратной совместимости
async def run_parser_service(city: str, street: str, house: str, is_debug: bool = False, skip_input_on_debug: bool = False) -> Dict[str, Any]:
    """Обертка для совместимости с существующим кодом."""
    data = {
        'city': city,
        'street': street,
        'house': house,
        'is_debug': is_debug
    }
    return run_parser_service_botasaurus(data)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='DTEK Parser with Botasaurus')
    parser.add_argument('--city', type=str, default=DEFAULT_CITY)
    parser.add_argument('--street', type=str, default=DEFAULT_STREET)
    parser.add_argument('--house', type=str, default=DEFAULT_HOUSE)
    parser.add_argument('--debug', action='store_true')
    
    args = parser.parse_args()
    
    result = run_parser_service_botasaurus({
        'city': args.city,
        'street': args.street,
        'house': args.house,
        'is_debug': args.debug
    })
    
    print("\n" + "="*50)
    print("РЕЗУЛЬТАТ:")
    print(json.dumps(result.get("data", {}), ensure_ascii=False, indent=2))
