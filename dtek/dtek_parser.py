import asyncio
import json
import re
import argparse
from playwright.async_api import async_playwright, TimeoutError
import os
from pathlib import Path
import logging
from logging import DEBUG, INFO, WARNING, ERROR
from typing import List, Dict, Any 
# ДОБАВЛЕНО: Для работы с часовыми поясами
from datetime import datetime # ДОБАВЛЕНО
import pytz # ДОБАВЛЕНО

# --- 1. Конфигурация Логирования ---
LOGGING_LEVEL = INFO 
logger = logging.getLogger(__name__)
logger.setLevel(LOGGING_LEVEL)

# Настройка формата
handler = logging.StreamHandler()

# ДОБАВЛЕНО: Функция для преобразования времени в Киевский часовой пояс
def custom_time(*args):
    """Возвращает текущее время в Киевском часовом поясе для логирования."""
    # Получаем текущее время в UTC, а затем конвертируем в 'Europe/Kyiv'
    return datetime.now(pytz.timezone('Europe/Kyiv')).timetuple()

formatter = logging.Formatter(
    '%(asctime)s %(name)s %(levelname)s %(message)s', 
    datefmt='%Y-%m-%d %H:%M:%S'
)
# ИЗМЕНЕНО: Применение функции для логирования в Киевском часовом поясе
formatter.converter = custom_time 
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)
# ------------------------------------

# --- 2. Конфигурация по умолчанию ---

DEFAULT_CITY = "м. Дніпро"
DEFAULT_STREET = "вул. Сонячна набережна"
DEFAULT_HOUSE = "6"

# === МИНИМАЛЬНОЕ ИЗМЕНЕНИЕ (1/3): Добавляем директорию OUT_DIR ===
OUT_DIR = "out"
# =================================================================

OUTPUT_FILENAME = "discon-fact.json"
SCREENSHOT_FILENAME = "discon-fact.png"
# ------------------------------------

# Вспомогательная функция (оставлена для возможности будущих правок)
def _clean_address_part(part: str, prefixes: list[str]) -> str:
    """Удаляет известные префиксы из части адреса."""
    for prefix in prefixes:
        if part.lower().startswith(prefix.lower()):
            return part[len(prefix):].lstrip(' .').strip()
    return part.strip()


async def run_parser_service(city: str, street: str, house: str, is_debug: bool = False, skip_input_on_debug: bool = False) -> Dict[str, Any]:
    """
    Основная логика парсинга.
    Возвращает единый словарь с общей информацией и вложенным графиком по дням.
    """
    
    run_headless = not is_debug
    logger.info(f"Режим запуска: {'Headless (фоновый)' if run_headless else 'Headful (отладка)'}")
    
    ADDRESS_DATA = [
        {"selector": "input#city", "value": city, "autocomplete": "div#cityautocomplete-list"},
        {"selector": "input#street", "value": street, "autocomplete": "div#streetautocomplete-list"},
        {"selector": "input#house_num", "value": house, "autocomplete": "div#house_numautocomplete-list"},
    ]
    
    # === МИНИМАЛЬНОЕ ИЗМЕНЕНИЕ (2/3): Изменяем пути к файлам ===
    # 2a. Создаем директорию 'out', если она не существует
    out_path = Path(OUT_DIR)
    out_path.mkdir(exist_ok=True)
    
    # УДАЛЯЕМ все содержимое директории 'out' при каждом запуске
    for item in out_path.iterdir():
        try:
            if item.is_file():
                item.unlink()  # Удаляем файл
            elif item.is_dir():
                item.rmdir()  # Удаляем пустую директорию (или используйте shutil.rmtree(item) для рекурсивного удаления)
        except OSError as e:
            logger.warning(f"Не удалось удалить {item}: {e}")
    
    # 2b. Определяем пути внутри OUT_DIR
    json_path = out_path / OUTPUT_FILENAME
    png_path = out_path / SCREENSHOT_FILENAME
    # ==========================================================

    logger.info(f"--- 1. Запуск Playwright для адреса: {city}, {street}, {house} ---")
    
    # Флаг для управления закрытием в finally
    keep_open = False 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=run_headless)
        page = await browser.new_page()
        
        try:
            URL = "https://www.dtek-dnem.com.ua/ua/shutdowns    "
            logger.info(f"Загрузка страницы: {URL}")
            await page.goto(URL, wait_until="load", timeout=60000)
            logger.debug("Страница успешно загружена.")

            # --- 2. Проверка и закрытие модального окна (ВАША ОРИГИНАЛЬНАЯ ЛОГИКА) ---
            modal_container_selector = "div.modal__container.m-attention__container"
            close_button_selector = "button.modal__close.m-attention__close"
            try:
                modal_container = page.locator(modal_container_selector)
                await modal_container.wait_for(state="visible", timeout=5000)
                await page.click(close_button_selector)
                await modal_container.wait_for(state="hidden")
            except TimeoutError:
                pass

            # --- 3. Ввод данных и АВТОЗАПОЛНЕНИЕ (ВНЕСЕНЫ ИСПРАВЛЕНИЯ) ---
            for i, data in enumerate(ADDRESS_DATA):
                selector = data["selector"]
                value = data["value"]
                autocomplete_selector = data["autocomplete"]
                
                is_last_field = (i == len(ADDRESS_DATA) - 1)
                next_selector = ADDRESS_DATA[i+1]["selector"] if not is_last_field else None
                
                success_selector = "#discon-fact > div.discon-fact-tables" if is_last_field else f"{next_selector}:not([disabled])"
                
                logger.info(f"[{i+1}/{len(ADDRESS_DATA)}] Ввод данных в поле: {selector} (Значение: {value})")
                
                await page.fill(selector, "") 
                await page.type(selector, value, delay=100)
                
                # Ждем появления списка автозаполнения
                await page.wait_for_selector(autocomplete_selector, state="visible", timeout=10000)
                
                # 📌 ФИКС: Для города (i=0) ищем элемент, который содержит введенный текст (м. Дніпро)
                if i == 0:
                    # Это предотвратит выбор "с. Дніпровське"
                    item_to_click_selector = f'{autocomplete_selector} > div:has-text("{value}")'
                    # Если точного совпадения нет, кликнет на первый элемент (как запасной вариант)
                    await page.locator(item_to_click_selector).first.click()
                else:
                    # Для улицы и дома: просто кликаем на первый элемент в списке
                    first_item_selector = f"{autocomplete_selector} > div:first-child"
                    await page.click(first_item_selector)

                # Ждем, пока список автозаполнения скроется
                await page.wait_for_selector(autocomplete_selector, state="hidden", timeout=5000)

                final_value = await page.locator(f"#discon_form {selector}").input_value()
                logger.info(f"Выбранное значение: {final_value}")

                try:
                    if not is_last_field:
                        # Ждем, что следующее поле станет активным
                        await page.wait_for_selector(success_selector, timeout=10000)
                    else:
                        # Ждем, что блок результатов загрузится
                        await page.wait_for_selector(success_selector, state="visible", timeout=20000)
                        logger.info("Результаты загружены.")
                except TimeoutError as e:
                    raise TimeoutError(f"Ошибка активации следующего шага или загрузки результатов. Проверьте правильность введенного адреса.") from e


            # --- 4. Извлечение общей информации и скриншот ---
            
            city_final = await page.locator("#discon_form input#city").input_value()
            street_final = await page.locator("#discon_form input#street").input_value()
            house_final = await page.locator("#discon_form input#house_num").input_value()

            group_selector = "#discon_form #group-name > span"
            await page.wait_for_selector(group_selector, state="visible", timeout=5000) 
            group_text = await page.locator(group_selector).inner_text()
            group_final = group_text.replace("Черга", "").strip()
            
            if is_debug:
                screenshot_selector = "div.discon-fact.active"
                await page.locator(screenshot_selector).screenshot(path=png_path)
                logger.info(f"Скриншот сохранен: {png_path}")
            
            # 📌 Инициализируем агрегированный словарь
            aggregated_result = {
                "city": city_final,
                "street": street_final,
                "house_num": house_final,
                "group": group_final,
                "schedule": {} # Здесь будут храниться слоты по датам
            }

            # --- 5. Парсинг и формирование JSON для ДВУХ ДНЕЙ ---
            
            table_locators = page.locator("#discon-fact > div.discon-fact-tables > div.discon-fact-table")
            date_locators = page.locator("#discon-fact > div.dates > div.date")

            for i in range(await table_locators.count()):
                table_container = table_locators.nth(i)
                
                # 5.1. Извлечение даты
                try:
                    date_element = date_locators.nth(i).locator("div:nth-child(2) > span")
                    date_text = await date_element.inner_text()
                except Exception:
                    logger.warning(f"Не удалось извлечь дату для таблицы {i+1}.")
                    date_text = f"Н/Д ({i+1})"
                
                # 5.2. Парсинг слотов внутри текущей таблицы (с использованием <table>)
                current_table = table_container.locator("table")
                
                time_headers = await current_table.locator("thead > tr > th:is(:nth-child(n+2))").all()
                data_cells = await current_table.locator("tbody > tr:first-child > td:is(:nth-child(n+2))").all()
                
                slots = []
                if not time_headers or not data_cells:
                     logger.warning(f"Не удалось найти заголовки/ячейки в таблице {i+1}. Пропускаем.")
                     continue

                for th_element, td_element in zip(time_headers, data_cells):
                    time_text_content = await th_element.inner_text()
                    time_slot = re.sub(r'\s+', ' ', time_text_content.strip()).replace('\n', '–').replace(' – ', '–') 
                    
                    td_classes = await td_element.get_attribute("class") or ""
                    
                    disconection_status = "false" # Свет будет
                    if "cell-scheduled" in td_classes:
                        disconection_status = "full"
                    elif "cell-first-half" in td_classes or "cell-second-half" in td_classes:
                        disconection_status = "half"
                    
                    if disconection_status != "false":
                        slots.append({"time": time_slot, "disconection": disconection_status})

                logger.info(f"Парсинг завершен для {date_text}. Найдено {len(slots)} слотов.")

                # 📌 Добавляем слоты в секцию schedule по дате
                aggregated_result["schedule"][date_text] = slots
            
            if not aggregated_result["schedule"]:
                logger.info("График отключений не найден ни для одного дня.")

            if is_debug:
                 keep_open = True
                 print("✅ Успешное выполнение в режиме отладки (--debug).")
                 if not skip_input_on_debug:
                     input("Нажмите Enter, чтобы закрыть браузер...")

            # 📌 Возвращаем ЕДИНЫЙ агрегированный словарь
            return aggregated_result

        except Exception as e:
            logger.error(f"Произошла ошибка в Playwright: {type(e).__name__}: {e}")
            
            # Удаляем файлы, используя обновленные пути, только если в режиме отладки
            if is_debug:
                if os.path.exists(json_path): os.remove(json_path)
                if os.path.exists(png_path): os.remove(png_path)

            if is_debug:
                keep_open = True
                print("❌ Ошибка в режиме отладки (--debug).")
                if not skip_input_on_debug:
                    input("Нажмите Enter, чтобы закрыть браузер...")
            else:
                # В режиме без debug ошибку нужно пробросить
                raise e
        
        finally:
            # Закрываем браузер только если keep_open == False
            if not keep_open:
                 await browser.close()


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ CLI (остаются без изменений) ---
def parse_args():
    """Разбор аргументов командной строки."""
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
        action='store_true',  
        help='Запускает браузер в режиме Headful (с окном) для отладки.'
    )
    return parser.parse_args()


# --- Точка входа для CLI ---
async def cli_entry_point():
    args = parse_args()
    logger.info("--- Запуск в режиме CLI ---\n")
    
    final_data = None
    try:
        final_data = await run_parser_service(
            city=args.city, 
            street=args.street, 
            house=args.house,
            is_debug=args.debug
        )
        
    except Exception as e:
        logger.error("Завершение работы с ошибкой.")
        exit(1)


    if final_data and args.debug:
        json_output = json.dumps(final_data, indent=4, ensure_ascii=False)
        
        # 📌 Используем новый путь
        json_path = Path(OUT_DIR) / OUTPUT_FILENAME 
        
        # Создаем директорию перед сохранением на всякий случай, если run_parser_service не был вызван
        Path(OUT_DIR).mkdir(exist_ok=True) 
        
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(json_output)
            
        logger.info(f"Результат парсинга ({len(final_data.get('schedule', {}))} дней графика):")
        logger.debug(json_output)
        logger.info(f"Данные сохранены в файл: {json_path.absolute()}")
    
    logger.info("\n--- Скрипт завершен ---")


if __name__ == "__main__":
    asyncio.run(cli_entry_point())