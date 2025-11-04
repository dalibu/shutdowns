import asyncio
import json
import re
import argparse
from playwright.async_api import async_playwright, TimeoutError
import os
from pathlib import Path
import logging
from logging import DEBUG, INFO, WARNING, ERROR

# --- 1. Конфигурация Логирования ---
LOGGING_LEVEL = INFO  # Установите DEBUG для максимальной детализации
logger = logging.getLogger(__name__)
logger.setLevel(LOGGING_LEVEL)
# Настройка формата
handler = logging.StreamHandler()
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    datefmt='%Y-%m-%d %H:%M:%S'
)
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)
# ------------------------------------

# --- 2. Конфигурация по умолчанию ---
DEFAULT_CITY = "м. Дніпро"
DEFAULT_STREET = "вул. Сонячна набережна"
DEFAULT_HOUSE = "6"
OUTPUT_FILENAME = "discon-fact.json"
SCREENSHOT_FILENAME = "discon-fact.png"
# ------------------------------------

async def run_parser_service(city: str, street: str, house: str, is_debug: bool = False) -> tuple[Path, list]:
    """
    Основная логика парсинга, выполняемая Playwright.
    """
    
    # Определяем режим headless
    run_headless = not is_debug
    logger.info(f"Режим запуска: {'Headless (фоновый)' if run_headless else 'Headful (отладка)'}")
    
    # Динамическое определение данных адреса для ввода
    ADDRESS_DATA = [
        {"selector": "input#city", "value": city, "autocomplete": "div#cityautocomplete-list"},
        {"selector": "input#street", "value": street, "autocomplete": "div#streetautocomplete-list"},
        {"selector": "input#house_num", "value": house, "autocomplete": "div#house_numautocomplete-list"},
    ]
    
    json_path = Path(OUTPUT_FILENAME)
    png_path = Path(SCREENSHOT_FILENAME)

    logger.info(f"--- 1. Запуск Playwright для адреса: {city}, {street}, {house} ---")

    async with async_playwright() as p:
        # Устанавливаем headless в зависимости от is_debug
        browser = await p.chromium.launch(headless=run_headless, slow_mo=300)
        page = await browser.new_page()
        
        try:
            URL = "https://www.dtek-dnem.com.ua/ua/shutdowns"
            logger.info(f"Загрузка страницы: {URL}")
            await page.goto(URL, wait_until="load", timeout=60000)
            logger.debug("Страница успешно загружена.")

            # --- 2. Проверка и закрытие модального окна ---
            modal_container_selector = "div.modal__container.m-attention__container"
            close_button_selector = "button.modal__close.m-attention__close"
            logger.debug(f"Проверка наличия модального окна...")
            try:
                modal_container = page.locator(modal_container_selector)
                await modal_container.wait_for(state="visible", timeout=5000)
                
                logger.info("Модальное окно найдено. Закрытие...")
                await page.click(close_button_selector)
                
                await modal_container.wait_for(state="hidden")
                logger.debug("Модальное окно успешно закрыто.")
            except TimeoutError:
                logger.debug("Модальное окно не найдено.")
                pass

            # --- 3. Ввод данных и АВТОЗАПОЛНЕНИЕ (Упрощенный) ---
            for i, data in enumerate(ADDRESS_DATA):
                selector = data["selector"]
                value = data["value"]
                autocomplete_selector = data["autocomplete"]
                
                is_last_field = (i == len(ADDRESS_DATA) - 1)
                next_selector = ADDRESS_DATA[i+1]["selector"] if not is_last_field else None
                
                # Селектор, который сигнализирует об успешном выборе
                success_selector = "#discon-fact > div.discon-fact-tables" if is_last_field else f"{next_selector}:not([disabled])"
                
                logger.info(f"\n[{i+1}/{len(ADDRESS_DATA)}] Ввод данных в поле: {selector} (Значение: {value})")
                
                # 3.1. Ввод
                await page.fill(selector, "") 
                await page.type(selector, value, delay=100)
                
                # 3.2. Ожидание появления списка автозаполнения
                await page.wait_for_selector(autocomplete_selector, state="visible", timeout=10000)
                logger.debug("Список автозаполнения появился.")
                
                # 3.3. Клик по ПЕРВОМУ элементу
                first_item_selector = f"{autocomplete_selector} > div:first-child"
                await page.click(first_item_selector)
                logger.debug(f"Кликнут элемент: {first_item_selector}")

                # 3.4. Ожидание, пока список автозаполнения исчезнет
                await page.wait_for_selector(autocomplete_selector, state="hidden", timeout=5000)
                logger.debug("Список автозаполнения исчез после клика.")

                # 3.5. Логирование фактического выбранного значения
                final_value = await page.locator(f"#discon_form {selector}").input_value()
                logger.info(f"Выбранное значение: {final_value}")

                # 3.6. Ожидание активации следующего поля / загрузки результатов
                if not is_last_field:
                    # Ожидаем, что следующее поле станет НЕ disabled
                    await page.wait_for_selector(success_selector, timeout=10000)
                    logger.info(f"Следующее поле {next_selector} стало активным.")
                else:
                    # Для последнего поля ожидаем загрузки блока результатов
                    await page.wait_for_selector(success_selector, state="visible", timeout=20000)
                    logger.info("Результаты загружены.")

            # --- 4. Извлечение данных ---
            
            # Извлечение фактических значений
            city_final = await page.locator("#discon_form input#city").input_value()
            street_final = await page.locator("#discon_form input#street").input_value()
            house_final = await page.locator("#discon_form input#house_num").input_value()
            logger.info(f"Фактический адрес (итоговый): {city_final}, {street_final}, {house_final}")

            screenshot_selector = "div.discon-fact.active"
            await page.locator(screenshot_selector).screenshot(path=png_path)
            logger.debug(f"Скриншот элемента сохранен в файл: {png_path}")

            # --- 5. Парсинг и формирование JSON ---
            logger.info("Начало парсинга данных о графике отключений...")
            
            # Получение даты и группы
            date_selector = "#discon-fact > div.dates > div.date.active > div:nth-child(2) > span"
            date_text = await page.locator(date_selector).inner_text()
            
            group_selector = "#discon_form #group-name > span"
            await page.wait_for_selector(group_selector, state="visible", timeout=5000) 
            group_text = await page.locator(group_selector).inner_text()
            logger.info(f"Парсинг группы: {group_text} на дату: {date_text}")
            
            # Парсинг таблицы
            table_selector = "#discon-fact > div.discon-fact-tables table"
            table = page.locator(table_selector)
            time_headers = await table.locator("thead > tr > th:is(:nth-child(n+2))").all()
            data_cells = await table.locator("tbody > tr:first-child > td:is(:nth-child(n+2))").all()
            
            slots = []
            if not time_headers or not data_cells:
                 logger.warning("Не удалось найти заголовки времени или ячейки данных в таблице.")
            
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

            if not slots:
                logger.info("На указанную дату отключения не запланированы.")

            # Формирование итогового JSON
            final_data = [{
                "city": city_final,
                "street": street_final,
                "house_num": house_final,
                "group": group_text,
                "date": date_text,
                "slots": slots
            }]
            
            logger.info(f"Парсинг завершен. Найдено {len(slots)} слотов.")
            
            # В режиме отладки ждем ручного закрытия
            if is_debug:
                 input("Нажмите Enter, чтобы закрыть браузер...")

            return png_path, final_data

        except Exception as e:
            logger.error(f"Произошла ошибка в Playwright: {type(e).__name__}: {e}")
            # Очистка в случае ошибки
            if os.path.exists(json_path): os.remove(json_path)
            if os.path.exists(png_path): os.remove(png_path)
            raise e
        
        finally:
            await browser.close()
            logger.debug("Браузер закрыт.")


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
    parser.add_argument(
        '--debug', 
        action='store_true',  # Флаг, который становится True при наличии
        help='Запускает браузер в режиме Headful (с окном) для отладки.'
    )
    return parser.parse_args()


# --- Точка входа для CLI ---
async def cli_entry_point():
    """Обрабатывает аргументы командной строки и сохраняет файлы локально."""
    args = parse_args()
    logger.info("\n--- Запуск в режиме CLI ---")
    
    try:
        # Передаем статус debug в сервисную функцию
        png_path, final_data = await run_parser_service(
            city=args.city, 
            street=args.street, 
            house=args.house,
            is_debug=args.debug
        )
        
        # Сохранение JSON в режиме CLI
        json_output = json.dumps(final_data, indent=4, ensure_ascii=False)
        json_path = Path(OUTPUT_FILENAME)
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(json_output)
            
        logger.info(f"Результат парсинга ({len(final_data[0]['slots'])} слотов):")
        logger.info(json_output)
        logger.info(f"Данные сохранены в файл: {json_path}")
        logger.info(f"Скриншот сохранен в файл: {png_path}")

    except Exception as e:
        logger.error("Завершение работы с ошибкой.")
        exit(1)
        
    logger.info("--- Скрипт завершен ---")


if __name__ == "__main__":
    # Если скрипт запущен напрямую, выполняется CLI-логика
    asyncio.run(cli_entry_point())