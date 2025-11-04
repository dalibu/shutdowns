import asyncio
import json
import re
# import argparse # <-- Удаляем argparse
from playwright.async_api import async_playwright, TimeoutError
import os
from pathlib import Path

# --- Конфигурация по умолчанию ---
# Эти константы больше не используются для ввода, но важны для имен файлов
OUTPUT_FILENAME = "discon-fact.json"
SCREENSHOT_FILENAME = "discon-fact.png"
# --------------------------------

# Удаляем функцию parse_args()

async def run(city: str, street: str, house: str):
    # Динамическое определение данных адреса для ввода
    ADDRESS_DATA = [
        {"selector": "input#city", "value": city, "autocomplete": "div#cityautocomplete-list"},
        {"selector": "input#street", "value": street, "autocomplete": "div#streetautocomplete-list"},
        {"selector": "input#house_num", "value": house, "autocomplete": "div#house_numautocomplete-list"},
    ]
    
    print(f"--- 1. Запуск Playwright для адреса: {city}, {street}, {house} ---")
    
    # Создаем уникальные имена файлов, чтобы избежать конфликтов при параллельных запросах
    # Обычно это делается с помощью UID, но для простоты используем текущее имя файла
    json_path = Path(OUTPUT_FILENAME)
    png_path = Path(SCREENSHOT_FILENAME)

    async with async_playwright() as p:
        # headless=True для работы в сервисе
        browser = await p.chromium.launch(headless=True, slow_mo=300)
        page = await browser.new_page()
        
        # Переменная для хранения итогового JSON
        final_data = None 

        try:
            URL = "https://www.dtek-dnem.com.ua/ua/shutdowns"
            await page.goto(URL, wait_until="load", timeout=60000)

            # --- 2. Проверка и закрытие модального окна ---
            # ... (логика модального окна остается прежней)
            modal_container_selector = "div.modal__container.m-attention__container"
            close_button_selector = "button.modal__close.m-attention__close"
            try:
                modal_container = page.locator(modal_container_selector)
                await modal_container.wait_for(state="visible", timeout=5000)
                await page.click(close_button_selector)
                await modal_container.wait_for(state="hidden")
            except TimeoutError:
                pass # Модалка не найдена

            # --- 3. Ввод данных и АВТОЗАПОЛНЕНИЕ ---
            for i, data in enumerate(ADDRESS_DATA):
                # ... (логика ввода данных остается прежней)
                selector = data["selector"]
                value = data["value"]
                autocomplete_selector = data["autocomplete"]
                next_selector = ADDRESS_DATA[i+1]["selector"] if i < len(ADDRESS_DATA) - 1 else None
                
                await page.type(selector, value, delay=100)
                await page.wait_for_selector(autocomplete_selector, state="visible", timeout=10000)
                
                first_item_selector = f"{autocomplete_selector} > div:first-child"
                await page.click(first_item_selector)
                
                await page.wait_for_selector(autocomplete_selector, state="hidden", timeout=5000)
                
                if next_selector:
                    await page.wait_for_selector(f"{next_selector}:not([disabled])", timeout=10000)

            # --- 4. Ожидание результата и скриншот ---
            results_selector = "#discon-fact > div.discon-fact-tables"
            await page.wait_for_selector(results_selector, state="visible", timeout=20000)
            
            # Извлечение фактических значений из input полей
            city_final = await page.locator("#discon_form input#city").input_value()
            street_final = await page.locator("#discon_form input#street").input_value()
            house_final = await page.locator("#discon_form input#house_num").input_value()

            # Сохранение скриншота
            screenshot_selector = "div.discon-fact.active"
            await page.locator(screenshot_selector).screenshot(path=png_path)

            # --- 5. Парсинг и формирование JSON ---
            # Получение даты и группы
            date_selector = "#discon-fact > div.dates > div.date.active > div:nth-child(2) > span"
            date_text = await page.locator(date_selector).inner_text()
            
            group_selector = "#discon_form #group-name > span"
            await page.wait_for_selector(group_selector, state="visible", timeout=5000) 
            group_text = await page.locator(group_selector).inner_text()
            
            # Парсинг таблицы
            table_selector = "#discon-fact > div.discon-fact-tables table"
            table = page.locator(table_selector)
            time_headers = await table.locator("thead > tr > th:is(:nth-child(n+2))").all()
            data_cells = await table.locator("tbody > tr:first-child > td:is(:nth-child(n+2))").all()
            
            slots = []
            for th_element, td_element in zip(time_headers, data_cells):
                # ... (логика парсинга слотов остается прежней)
                time_text_content = await th_element.inner_text()
                time_slot = re.sub(r'\s+', ' ', time_text_content.strip()).replace('\n', ' – ')
                td_classes = await td_element.get_attribute("class") or ""
                disconection_status = None
                
                if "cell-scheduled" in td_classes:
                    disconection_status = "full"
                elif "cell-first-half" in td_classes or "cell-second-half" in td_classes:
                    disconection_status = "half"
                
                if disconection_status:
                    slots.append({"time": time_slot, "disconection": disconection_status})

            # Формирование итогового JSON
            final_data = [{
                "city": city_final,
                "street": street_final,
                "house_num": house_final,
                "group": group_text,
                "date": date_text,
                "slots": slots
            }]
            
            json_output = json.dumps(final_data, indent=4, ensure_ascii=False)
            
            with open(json_path, "w", encoding="utf-8") as f:
                f.write(json_output)
            
            print("Скрипт успешно завершен.")
            return png_path, final_data

        except Exception as e:
            print(f"Произошла ошибка в процессе выполнения скрипта: {e}")
            # В случае ошибки удаляем файлы, если они были созданы
            if os.path.exists(json_path):
                os.remove(json_path)
            if os.path.exists(png_path):
                os.remove(png_path)
            raise e
        
        finally:
            await browser.close()

# Теперь функция run должна вызываться напрямую с аргументами, а не через __main__
# if __name__ == "__main__":
#    cli_args = parse_args()
#    asyncio.run(run(cli_args)) # <-- Удаляем эту секцию