import asyncio
import json
import re
from playwright.async_api import async_playwright, TimeoutError

# --- Конфигурация ---
URL = "https://www.dtek-dnem.com.ua/ua/shutdowns"
ADDRESS_DATA = [
    {"selector": "input#city", "value": "м. Дніпро", "autocomplete": "div#cityautocomplete-list"},
    {"selector": "input#street", "value": "вул. Сонячна Набережна", "autocomplete": "div#streetautocomplete-list"},
    {"selector": "input#house_num", "value": "6", "autocomplete": "div#house_numautocomplete-list"},
]
OUTPUT_FILENAME = "discon-fact.json"
SCREENSHOT_FILENAME = "discon-fact.png"
# --------------------

async def run():
    print("--- 1. Запуск Playwright ---")
    async with async_playwright() as p:
        # slow_mo=300ms для замедления действий
        browser = await p.chromium.launch(headless=False, slow_mo=300)
        page = await browser.new_page()

        try:
            print(f"Загрузка страницы: {URL}")
            await page.goto(URL, wait_until="load", timeout=60000)

            # --- 2. Проверка и закрытие модального окна ---
            modal_container_selector = "div.modal__container.m-attention__container"
            close_button_selector = "button.modal__close.m-attention__close"
            
            try:
                print(f"Проверка наличия модального окна...")
                modal_container = page.locator(modal_container_selector)
                await modal_container.wait_for(state="visible", timeout=5000)
                
                print("Модальное окно найдено. Закрытие...")
                await page.click(close_button_selector)
                
                await modal_container.wait_for(state="hidden")
                print("Модальное окно успешно закрыто.")
                
            except TimeoutError:
                print("Модальное окно не найдено. Продолжение основного сценария.")
            
            # --- 3. Ввод данных и АВТОЗАПОЛНЕНИЕ ---
            for i, data in enumerate(ADDRESS_DATA):
                selector = data["selector"]
                value = data["value"]
                autocomplete_selector = data["autocomplete"]
                
                next_selector = ADDRESS_DATA[i+1]["selector"] if i < len(ADDRESS_DATA) - 1 else None
                
                print(f"\n[{i+1}/{len(ADDRESS_DATA)}] Ввод данных в поле: {selector}")
                
                # 3.1. Имитация ввода (type) с задержкой 100мс между символами
                await page.type(selector, value, delay=100)
                
                # 3.2. Ожидание появления списка автозаполнения
                await page.wait_for_selector(autocomplete_selector, state="visible", timeout=10000)
                print(f"Список автозаполнения {autocomplete_selector} появился.")
                
                # 3.3. Прямой клик по первому элементу
                first_item_selector = f"{autocomplete_selector} > div:first-child"
                await page.click(first_item_selector)
                print(f"Выбран первый элемент: {first_item_selector}")

                # 3.4. Ожидание, пока список автозаполнения исчезнет
                await page.wait_for_selector(autocomplete_selector, state="hidden", timeout=5000)
                
                # 3.5. Ожидание активации следующего поля
                if next_selector:
                    await page.wait_for_selector(f"{next_selector}:not([disabled])", timeout=10000)
                    print(f"Следующее поле {next_selector} стало активным.")
                elif i == len(ADDRESS_DATA) - 1:
                    print("Ввод последнего поля завершен. Ожидание результатов...")

            # --- 4. Ожидание результата и скриншот ---
            results_selector = "#discon-fact > div.discon-fact-tables"
            await page.wait_for_selector(results_selector, state="visible", timeout=20000)
            print("Результаты загружены.")

            screenshot_selector = "div.discon-fact.active"
            await page.locator(screenshot_selector).screenshot(path=SCREENSHOT_FILENAME)
            print(f"Скриншот элемента сохранен в файл: {SCREENSHOT_FILENAME}")

            # --- 5. Парсинг и формирование JSON (Добавлено поле 'group') ---
            print("\nНачало парсинга данных о графике отключений...")
            
            # 5.1 Получение даты
            date_selector = "#discon-fact > div.dates > div.date.active > div:nth-child(2) > span"
            date_text = await page.locator(date_selector).inner_text()
            
            # 5.2 Получение группы
            group_selector = "#discon_form #group-name > span"
            group_text = await page.locator(group_selector).inner_text()
            print(f"Парсинг группы: {group_text}")
            
            # 5.3 Получение таблицы и данных
            table_selector = "#discon-fact > div.discon-fact-tables table"
            table = page.locator(table_selector)
            time_headers = await table.locator("thead > tr > th:is(:nth-child(n+2))").all()
            data_cells = await table.locator("tbody > tr:first-child > td:is(:nth-child(n+2))").all()

            if not time_headers or not data_cells:
                raise Exception("Не удалось найти заголовки времени или ячейки данных в таблице.")
            
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
                    slots.append({
                        "time": time_slot,
                        "disconection": disconection_status
                    })
                    
            if not slots:
                print("Примечание: На указанную дату отключения не запланированы.")

            # 5.4 Формирование итогового JSON
            final_data = [{
                "group": group_text,
                "date": date_text,
                "slots": slots
            }]
            json_output = json.dumps(final_data, indent=4, ensure_ascii=False)
            
            print(f"\n--- Результат парсинга ({len(slots)} слотов) ---")
            print(json_output)
            
            with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
                f.write(json_output)
            print(f"Данные сохранены в файл: {OUTPUT_FILENAME}")
            
            # --- 6. Завершение работы ---
            print("\n--- Завершение. Браузер открыт ---")
            input("Нажмите Enter, чтобы закрыть браузер и завершить скрипт...")

        except Exception as e:
            print(f"\nПроизошла ошибка в процессе выполнения скрипта: {e}")
            input("Нажмите Enter, чтобы закрыть браузер...") 
        
        finally:
            await browser.close()
            print("Браузер закрыт. Скрипт завершен.")

if __name__ == "__main__":
    asyncio.run(run())