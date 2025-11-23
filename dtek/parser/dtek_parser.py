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
from datetime import datetime
import pytz
from PIL import Image
import io
import time as time_module
import uuid

# --- 1. Конфигурация Логирования ---
LOGGING_LEVEL = INFO
logger = logging.getLogger(__name__)
logger.setLevel(LOGGING_LEVEL)

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
# Используем абсолютный путь относительно расположения парсера
OUT_DIR = os.path.join(os.path.dirname(__file__), "out")

# --- 3. Новые упрощенные вспомогательные функции ---

def format_minutes(minutes: int) -> str:
    """Преобразует кол-во минут от начала дня в строку HH:MM. 1440 -> 24:00."""
    if minutes >= 24 * 60:
        return "24:00"
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"

def cleanup_old_files(directory: Path, max_age_hours: int = 1):
    """Удаляет старые файлы."""
    try:
        current_time = time_module.time()
        max_age_seconds = max_age_hours * 3600
        
        deleted_count = 0
        for item in directory.iterdir():
            if item.is_file() and item.suffix in ['.json', '.png']:
                file_age = current_time - item.stat().st_mtime
                if file_age > max_age_seconds:
                    item.unlink()
                    deleted_count += 1
                    logger.debug(f"Удален старый файл: {item.name}")
        
        if deleted_count > 0:
            logger.info(f"Очищено старых файлов: {deleted_count}")
    except Exception as e:
        logger.warning(f"Ошибка при очистке старых файлов: {e}")

async def create_combined_screenshot(page, output_path, spacing: int = 20):
    """Создает объединенный скриншот обеих таблиц."""
    try:
        screenshot_selector = "div.discon-fact.active"
        
        # 1. Сегодня
        today_tab_selector = "#discon-fact > div.dates > div:nth-child(1)"
        await page.click(today_tab_selector)
        await page.wait_for_selector("div.discon-fact-table:nth-child(1).active", timeout=3000)
        await page.wait_for_timeout(300)
        screenshot1_bytes = await page.locator(screenshot_selector).screenshot()
        
        # 2. Завтра
        tomorrow_tab_selector = "#discon-fact > div.dates > div:nth-child(2)"
        await page.click(tomorrow_tab_selector)
        await page.wait_for_selector("div.discon-fact-table:nth-child(2).active", timeout=3000)
        await page.wait_for_timeout(300)
        screenshot2_bytes = await page.locator(screenshot_selector).screenshot()
        
        # 3. Объединение
        img1 = Image.open(io.BytesIO(screenshot1_bytes))
        img2 = Image.open(io.BytesIO(screenshot2_bytes))
        
        total_width = max(img1.width, img2.width)
        total_height = img1.height + spacing + img2.height
        combined_img = Image.new('RGB', (total_width, total_height), color='white')
        
        combined_img.paste(img1, (0, 0))
        combined_img.paste(img2, (0, img1.height + spacing))
        
        combined_img.save(output_path)
        logger.info(f"✓ Объединенный скриншот сохранен: {output_path}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при создании скриншота: {e}")

async def run_parser_service(city: str, street: str, house: str, is_debug: bool = False, skip_input_on_debug: bool = False) -> Dict[str, Any]:
    """Основная логика парсинга."""

    run_headless = not is_debug
    logger.info(f"Режим запуска: {'Headless' if run_headless else 'Headful (отладка)'}")

    ADDRESS_DATA = [
        {"selector": "input#city", "value": city, "autocomplete": "div#cityautocomplete-list"},
        {"selector": "input#street", "value": street, "autocomplete": "div#streetautocomplete-list"},
        {"selector": "input#house_num", "value": house, "autocomplete": "div#house_numautocomplete-list"},
    ]

    out_path = Path(OUT_DIR)
    out_path.mkdir(exist_ok=True)
    cleanup_old_files(out_path, max_age_hours=24)
    
    session_id = uuid.uuid4().hex[:8]
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_filename = f"dtek-disconnections-{timestamp}-{session_id}.json"
    png_filename = f"dtek-disconnections-{timestamp}-{session_id}.png"
    
    json_path = out_path / json_filename
    png_path = out_path / png_filename

    keep_open = False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=run_headless)
        page = await browser.new_page()

        try:
            URL = "https://www.dtek-dnem.com.ua/ua/shutdowns"
            logger.info(f"Загрузка страницы: {URL}")
            await page.goto(URL, wait_until="load", timeout=60000)

            # Закрытие модального окна
            try:
                await page.click("button.modal__close.m-attention__close", timeout=5000)
            except TimeoutError:
                pass

            # Ввод адреса
            for i, data in enumerate(ADDRESS_DATA):
                selector = data["selector"]
                value = data["value"]
                autocomplete = data["autocomplete"]
                
                # Определяем селектор успеха (следующее поле или таблица результатов)
                is_last = (i == len(ADDRESS_DATA) - 1)
                success_selector = "#discon-fact > div.discon-fact-tables" if is_last else f"{ADDRESS_DATA[i+1]['selector']}:not([disabled])"

                logger.info(f"Ввод: {value}")
                await page.fill(selector, "")
                await page.type(selector, value, delay=100)
                await page.wait_for_selector(autocomplete, state="visible", timeout=10000)

                if i == 0: # Город
                    await page.locator(f'{autocomplete} > div:has-text("{value}")').first.click()
                else:
                    await page.click(f"{autocomplete} > div:first-child")

                await page.wait_for_selector(autocomplete, state="hidden", timeout=5000)
                
                try:
                    if is_last:
                        await page.wait_for_selector(success_selector, state="visible", timeout=20000)
                    else:
                        await page.wait_for_selector(success_selector, timeout=10000)
                except TimeoutError:
                    raise TimeoutError("Не удалось перейти к следующему шагу. Проверьте адрес.")

            # Сбор данных
            group_text = await page.locator("#discon_form #group-name > span").inner_text()
            
            aggregated_result = {
                "city": await page.locator("#discon_form input#city").input_value(),
                "street": await page.locator("#discon_form input#street").input_value(),
                "house_num": await page.locator("#discon_form input#house_num").input_value(),
                "group": group_text.replace("Черга", "").strip(),
                "schedule": {}
            }

            table_locators = page.locator("#discon-fact > div.discon-fact-tables > div.discon-fact-table")
            date_locators = page.locator("#discon-fact > div.dates > div.date")

            # === УПРОЩЕННЫЙ ПАРСИНГ ТАБЛИЦ ===
            count = await table_locators.count()
            for i in range(count):
                # 1. Дата
                try:
                    date_text = await date_locators.nth(i).locator("div:nth-child(2) > span").inner_text()
                except:
                    date_text = f"Date_{i}"

                # 2. Ячейки таблицы
                current_table = table_locators.nth(i).locator("table")
                time_headers = await current_table.locator("thead > tr > th:is(:nth-child(n+2))").all()
                data_cells = await current_table.locator("tbody > tr:first-child > td:is(:nth-child(n+2))").all()

                if not time_headers:
                    continue

                merged_slots = []
                current_start = None
                current_end = None

                for th, td in zip(time_headers, data_cells):
                    header_text = await th.inner_text() # "00-01"
                    classes = await td.get_attribute("class") or ""
                    
                    # Парсим часы из заголовка "00-01"
                    clean_header = header_text.strip().replace('\n', '').replace(' ', '')
                    try:
                        h_parts = clean_header.split('-')
                        h_start = int(h_parts[0])
                        h_end = int(h_parts[1])
                    except ValueError:
                        continue

                    # Базовые минуты для этой ячейки (например, 00-01 -> 0..60)
                    base_start_min = h_start * 60
                    base_end_min = h_end * 60 
                    # Коррекция для 23-24 (или если день заканчивается переходом)
                    if base_end_min == 0 and h_end == 0: 
                        base_end_min = 24 * 60 # 1440

                    # Определяем фактическое начало и конец отключения в этом слоте
                    slot_start = None
                    slot_end = None

                    if "cell-scheduled" in classes:
                        # Весь час
                        slot_start = base_start_min
                        slot_end = base_end_min
                    elif "cell-first-half" in classes:
                        # Первая половина (00-30)
                        slot_start = base_start_min
                        slot_end = base_start_min + 30
                    elif "cell-second-half" in classes:
                        # Вторая половина (30-60)
                        slot_start = base_start_min + 30
                        slot_end = base_end_min

                    # --- ЛОГИКА ОБЪЕДИНЕНИЯ ---
                    if slot_start is not None:
                        # Если есть активный слот и он стыкуется с текущим -> продлеваем
                        if current_end is not None and current_end == slot_start:
                            current_end = slot_end
                        else:
                            # Если не стыкуется, сохраняем предыдущий (если был) и начинаем новый
                            if current_start is not None:
                                merged_slots.append({
                                    "shutdown": f"{format_minutes(current_start)}–{format_minutes(current_end)}"
                                })
                            current_start = slot_start
                            current_end = slot_end
                    else:
                        # Если в ячейке света нет (белая), закрываем текущий интервал, если он был открыт
                        if current_start is not None:
                            merged_slots.append({
                                "shutdown": f"{format_minutes(current_start)}–{format_minutes(current_end)}"
                            })
                            current_start = None
                            current_end = None

                # Добавляем последний хвост, если остался
                if current_start is not None:
                    merged_slots.append({
                        "shutdown": f"{format_minutes(current_start)}–{format_minutes(current_end)}"
                    })

                aggregated_result["schedule"][date_text] = merged_slots
                logger.info(f"Дата {date_text}: найдено {len(merged_slots)} отключений.")

            if is_debug:
                await create_combined_screenshot(page, png_path)
                keep_open = True
                print("✅ Выполнено (Debug).")
                if not skip_input_on_debug:
                    input("Enter чтобы закрыть...")

            return {
                "data": aggregated_result,
                "json_path": json_path,
                "png_path": png_path
            }

        except Exception as e:
            logger.error(f"Ошибка Playwright: {e}")
            if is_debug:
                keep_open = True
                input("Ошибка. Enter чтобы закрыть...")
            raise e
        finally:
            if not keep_open:
                await browser.close()

# --- CLI ---
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--city', default=DEFAULT_CITY)
    parser.add_argument('--street', default=DEFAULT_STREET)
    parser.add_argument('--house', default=DEFAULT_HOUSE)
    parser.add_argument('--debug', action='store_true')
    return parser.parse_args()

async def cli_entry_point():
    args = parse_args()
    try:
        result = await run_parser_service(args.city, args.street, args.house, args.debug)
        if result and args.debug:
            with open(result["json_path"], "w", encoding="utf-8") as f:
                json.dump(result["data"], f, indent=4, ensure_ascii=False)
            logger.info(f"Saved: {result['json_path']}")
    except Exception:
        exit(1)

if __name__ == "__main__":
    asyncio.run(cli_entry_point())