import asyncio
import json
import re
import argparse
from playwright.async_api import async_playwright, TimeoutError
import os
from pathlib import Path

# --- 1. Конфигурация по умолчанию ---
DEFAULT_CITY = "м. Дніпро"
DEFAULT_STREET = "вул. Сонячна набережна"
DEFAULT_HOUSE = "6"
OUTPUT_FILENAME = "discon-fact.json"
SCREENSHOT_FILENAME = "discon-fact.png"
# ------------------------------------

async def run_parser_service(city: str, street: str, house: str) -> tuple[Path, list]:
    """
    Основная логика парсинга, выполняемая Playwright.
    Эта функция предназначена для вызова из сторонних приложений (например, Telegram-бота).

    Args:
        city, street, house: Данные адреса для ввода.

    Returns:
      Кортеж: (Path к PNG файлу, List с данными JSON)
    """
    
    # Динамическое определение данных адреса для ввода
    ADDRESS_DATA = [
        {"selector": "input#city", "value": city, "autocomplete": "div#cityautocomplete-list"},
        {"selector": "input#street", "value": street, "autocomplete": "div#streetautocomplete-list"},
        {"selector": "input#house_num", "value": house, "autocomplete": "div#house_numautocomplete-list"},
    ]
    
    # Уникальные пути к файлам для локального сохранения
    json_path = Path(OUTPUT_FILENAME)
    png_path = Path(SCREENSHOT_FILENAME)

    print(f"--- 1. Запуск Playwright для адреса: {city}, {street}, {house} ---")

    async with async_playwright() as p:
        # headless=True для работы в сервисе (CLI или Bot)
        browser = await p.chromium.launch(headless=True, slow_mo=300)
        page = await browser.new_page()
        
        try:
            URL = "https://www.dtek-dnem.com.ua/ua/shutdowns"
            await page.goto(URL, wait_until="load", timeout=60000)

            # --- 2. Проверка и закрытие модального окна ---
            modal_container_selector = "div.modal__container.m-attention__container"
            close_button_selector = "button.modal__close.m-attention__close"
            try:
                modal_container = page.locator(modal_container_selector)
                await modal_container.wait_for(state="visible", timeout=5000)
                await page.click(close_button_selector)
                await modal_container.wait_for(state="hidden")
            except TimeoutError:
                pass

            # --- 3. Ввод данных и АВТОЗАПОЛНЕНИЕ ---
            for i, data in enumerate(ADDRESS_DATA):
                selector = data["selector"]
                value = data["value"]
                autocomplete_selector = data["autocomplete"]
                next_selector = ADDRESS_DATA[i+1]["selector"] if i < len(ADDRESS_DATA) - 1 else None
                
                # Имитация ввода (page.type с delay)
                await page.type(selector, value, delay=100)
                
                await page.wait_for_selector(autocomplete_selector, state="visible", timeout=10000)
                
                first_item_selector = f"{autocomplete_selector} > div:first-child"
                await page.click(first_item_selector)
                
                await page.wait_for_selector(autocomplete_selector, state="hidden", timeout=5000)
                
                if next_selector:
                    await page.wait_for_selector(f"{next_selector}:not([disabled])", timeout=10000)

            # --- 4. Ожидание результата, скриншот и извлечение данных ---
            results_selector = "#discon-fact > div.discon-fact-tables"
            await page.wait_for_selector(results_selector, state="visible", timeout=20000)
            
            # Извлечение фактических значений из input полей
            city_final = await page.locator("#discon_form input#city").input_value()
            street_final = await page.locator("#discon_form input#street").input_value()
            house_final = await page.locator("#discon_form input#house_num").input_value()

            screenshot_selector = "div.discon-fact.active"
            await page.locator(screenshot_selector).screenshot(path=png_path)

            # --- 5. Парсинг и формирование JSON ---
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
            
            return png_path, final_data

        except Exception as e:
            # Очистка в случае ошибки
            if os.path.exists(json_path): os.remove(json_path)
            if os.path.exists(png_path): os.remove(png_path)
            raise e
        
        finally:
            await browser.close()

def parse_args():
    """Парсит аргументы командной строки (для режима CLI)."""
    parser = argparse.ArgumentParser(
        description="Скрипт Playwright для парсинга графика отключений ДТЕК."
    )
    parser.add_argument(
        '--city', 
        type=str, 
        default=DEFAULT_CITY, 
        help=f'Название города (по умолчанию: "{DEFAULT_CITY}")'
    )
    parser.add_argument(
        '--street', 
        type=str, 
        default=DEFAULT_STREET, 
        help=f'Название улицы (по умолчанию: "{DEFAULT_STREET}")'
    )
    parser.add_argument(
        '--house', 
        type=str, 
        default=DEFAULT_HOUSE, 
        help=f'Номер дома (по умолчанию: "{DEFAULT_HOUSE}")'
    )
    return parser.parse_args()


# --- Точка входа для CLI ---
async def cli_entry_point():
    """Обрабатывает аргументы командной строки и сохраняет файлы локально."""
    args = parse_args()
    print("\n--- Запуск в режиме CLI ---")
    
    try:
        # Вызов основной сервисной функции
        png_path, final_data = await run_parser_service(
            city=args.city, 
            street=args.street, 
            house=args.house
        )
        
        # Сохранение JSON в режиме CLI (только здесь)
        json_output = json.dumps(final_data, indent=4, ensure_ascii=False)
        json_path = Path(OUTPUT_FILENAME)
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(json_output)
            
        print(f"\n--- Результат парсинга ({len(final_data[0]['slots'])} слотов) ---")
        print(json_output)
        print(f"Данные сохранены в файл: {json_path}")
        print(f"Скриншот сохранен в файл: {png_path}")

    except Exception as e:
        print(f"\n❌ Произошла критическая ошибка: {e}")
        # Выход с ошибкой
        exit(1)
        
    print("\n--- Скрипт завершен ---")


if __name__ == "__main__":
    # Если скрипт запущен напрямую, выполняется CLI-логика
    asyncio.run(cli_entry_point())