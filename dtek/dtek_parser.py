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
from datetime import datetime
import pytz
# ДОБАВЛЕНО: Для объединения слотов
from datetime import timedelta, time
from PIL import Image
import io


# --- 1. Конфигурация Логирования ---
LOGGING_LEVEL = INFO
logger = logging.getLogger(__name__)
logger.setLevel(LOGGING_LEVEL)

# Настройка формата
handler = logging.StreamHandler()

# ДОБАВЛЕНО: Функция для преобразования времени в Киевский часовой пояс
def custom_time(*args):
    """Возвращает текущее время в Киевском часовом поясе для логирования."""
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


# --- ДОБАВЛЕНО: Вспомогательные функции для работы с временными слотами ---
def parse_short_time_slot(slot_str: str) -> tuple:
    """
    Парсит строку формата 'HH-HH' и возвращает (start_hour, end_hour) как int.
    """
    times = slot_str.split('-')
    if len(times) != 2:
        raise ValueError(f"Неверный формат сокращённого временного слота: {slot_str}")
    try:
        start_hour = int(times[0])
        end_hour = int(times[1])
    except ValueError as e:
        raise ValueError(f"Неверный формат часов в слоте {slot_str}: {e}")
    return start_hour, end_hour

def format_time(t: time) -> str:
    """Форматирует datetime.time в строку HH:MM, используя 24:00 вместо 00:00 для конца суток."""
    if t.hour == 0 and t.minute == 0:
        return "24:00"
    else:
        return t.strftime("%H:%M")

def expand_short_slot(slot_str: str, status: str) -> List[Dict[str, str]]:
    """
    Преобразует сокращённый слот 'HH-HH' и его статус ('half', 'full') в список 30-минутных слотов.
    """
    try:
        start_hour, end_hour = parse_short_time_slot(slot_str)
        # --- ИЗМЕНЕНИЕ: Проверка формата ---
        if end_hour == (start_hour + 1) % 24:
            pass # Формат корректен
        elif start_hour == 23 and end_hour == 24:
            # Специальный случай: 23-24, означает 23:00 - 24:00
            pass # Формат корректен
        else:
            logger.warning(f"Непредвиденный формат слота {slot_str}, ожидается HH-HH+1 или 23-24. Пропускаем.")
            return []
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    except ValueError as e:
        logger.error(f"Ошибка парсинга сокращённого слота {slot_str}: {e}")
        return []

    expanded = []
    # Создаём 30-минутные интервалы для HH:00-HH:30 и HH:30-HH+1:00
    start_time_1 = time(hour=start_hour, minute=0)
    end_time_1 = time(hour=start_hour, minute=30)
    start_time_2 = time(hour=start_hour, minute=30)
    # Учитываем переход через полночь для HH=23
    end_time_2_hour = (start_hour + 1) % 24
    end_time_2 = time(hour=end_time_2_hour, minute=0)

    if status == 'full':
        # Если статус full, оба 30-минутных интервала отключены
        expanded.append({"time": f"{format_time(start_time_1)}–{format_time(end_time_1)}", "disconection": "full"})
        expanded.append({"time": f"{format_time(start_time_2)}–{format_time(end_time_2)}", "disconection": "full"})
    elif status == 'half':
        # Если статус half, нужно определить, какая половина
        # Пока не знаем, предположим, что это может быть любая из двух.
        # Но логика парсинга должна была бы уточнить это на уровне ячеек.
        # Т.к. исходный статус 'half' пришёл от ячейки, соответствующей слоту HH-HH,
        # мы не можем точно сказать, первая это половина или вторая, только по этому статусу.
        # Однако, Playwright парсит *каждую* ячейку таблицы отдельно.
        # Значит, если ячейка имеет класс 'cell-first-half', она соответствует интервалу HH:00-HH:30.
        # Если 'cell-second-half', то HH:30-(HH+1):00.
        # Наша предыдущая логика парсинга ячеек не учитывала это!
        # Мы просто присваивали 'half' для любого 'cell-first-half' или 'cell-second-half'.
        # Нужно исправить логику парсинга ячеек, чтобы она возвращала правильный 30-мин интервал и его статус.
        # Это означает, что expand_short_slot не нужен в текущем виде.
        # Нужно изменить основной цикл парсинга, чтобы он генерировал правильные 30-мин слоты сразу.
        # Этот код будет изменён в основном цикле.
        pass # expand_short_slot не используется в новой логике

    # В новой логике мы не будем использовать expand_short_slot так, как планировалось.
    # Вместо этого, мы обработаем статусы 'cell-first-half' и 'cell-second-half' в основном цикле.
    return expanded


def merge_slots(slot_list: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Объединяет идущие подряд слоты отключений (full, half) в один сплошной промежуток.
    Возвращает новый список объединенных слотов.
    Учитывает переход через полночь: 23:30-24:00 идет после 23:00-23:30.
    """
    if not slot_list:
        return []

    # --- НОВАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ---
    def time_to_minutes(t: time) -> int:
        """Преобразует time в количество минут от начала условных 48 часов (00:00 второго дня = 24*60)."""
        # Это позволяет корректно сравнивать 23:30 и 00:00 (24:00) как 23*60+30 и 24*60
        # Это НЕ изменяет исходные объекты time, а только для сравнения внутри merge_slots.
        if t.hour == 0 and t.minute == 0:
            return 24 * 60 # 24:00
        else:
            return t.hour * 60 + t.minute
    # --- КОНЕЦ ВСПОМОГАТЕЛЬНОЙ ФУНКЦИИ ---

    try:
        # Сортируем слоты по времени начала, используя минуты
        sorted_slots = sorted(slot_list, key=lambda x: parse_time_slot(x['time'])[0])
        # Но для сортировки нужно вызвать parse_time_slot, которая конвертирует 24:00 в 00:00
        # Это всё равно будет работать корректно, потому что 00:00 в начале дня идёт после 23:XX.
        # НО если есть слот, заканчивающийся на 00:00 (24:00), и следующий начинается на 00:30,
        # то сортировка может быть неправильной.
        # Лучше сортировать с учётом "виртуальных" минут.
        def sort_key(x):
            start_t, _ = parse_time_slot(x['time'])
            return time_to_minutes(start_t)

        sorted_slots = sorted(slot_list, key=sort_key)

    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка сортировки слотов: {e}. Слоты: {slot_list}")
        # Если не удалось отсортировать, возвращаем исходный список
        return slot_list

    merged = []
    try:
        current_start_time, current_end_time = parse_time_slot(sorted_slots[0]['time'])
        current_status = sorted_slots[0]['disconection']
    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка парсинга первого слота: {e}. Слот: {sorted_slots[0]}")
        # Если не удалось распарсить первый слот, возвращаем исходный список
        return slot_list

    for slot in sorted_slots[1:]:
        try:
            slot_start_time, slot_end_time = parse_time_slot(slot['time'])
        except (ValueError, IndexError) as e:
            logger.error(f"Ошибка парсинга слота в цикле: {e}. Слот: {slot}")
            # Пропускаем некорректный слот
            continue

        slot_status = slot['disconection']

        # Проверяем, является ли слот отключением (full или half)
        is_current_discon = current_status in ['full', 'half']
        is_slot_discon = slot_status in ['full', 'half']

        if is_current_discon and is_slot_discon:
            # --- ИЗМЕНЕНИЕ: Используем вспомогательную функцию для сравнения ---
            # Если статусы совпадают и слоты идут подряд (или пересекаются), объединяем
            current_end_min = time_to_minutes(current_end_time)
            slot_start_min = time_to_minutes(slot_start_time)

            if slot_start_min <= current_end_min:
                # Слот пересекается или идет сразу за текущим, объединяем
                # Выбираем максимальное время окончания (тоже по виртуальным минутам)
                current_end_time = max(current_end_time, slot_end_time, key=time_to_minutes)
            elif slot_start_min > current_end_min:
                # Слот идет позже, текущий блок закончен
                merged.append({
                    "time": f"{format_time(current_start_time)}–{format_time(current_end_time)}",
                    "disconection": current_status
                })
                # Начинаем новый блок
                current_start_time, current_end_time = slot_start_time, slot_end_time
                current_status = slot_status
        else:
            # Текущий слот - отключение, а следующий - свет есть, или наоборот
            # Заканчиваем текущий блок отключения
            if is_current_discon:
                merged.append({
                    "time": f"{format_time(current_start_time)}–{format_time(current_end_time)}",
                    "disconection": current_status
                })
            # Если следующий слот - отключение, начинаем новый блок
            if is_slot_discon:
                current_start_time, current_end_time = slot_start_time, slot_end_time
                current_status = slot_status
            # Если следующий слот - свет есть, мы его просто пропускаем, т.к. не собираем "false" в объединенный список

    # Добавляем последний блок
    if current_status in ['full', 'half']:
        merged.append({
            "time": f"{format_time(current_start_time)}–{format_time(current_end_time)}",
            "disconection": current_status
        })

    return merged

def parse_time_slot(slot_str: str) -> tuple:
    """
    Парсит строку формата 'HH:MM–HH:MM' и возвращает (start_time, end_time) как datetime.time.
    Поддерживает '24:00' как синоним '00:00' следующего дня для целей сортировки.
    """
    times = slot_str.split('–')
    if len(times) != 2:
        raise ValueError(f"Неверный формат временного слота: {slot_str}")
    start_str, end_str = times

    # Вспомогательная функция для парсинга времени с поддержкой 24:00
    def _parse_time_with_24(time_str):
        time_str = time_str.strip()
        if time_str == "24:00":
            # Возвращаем 00:00, но помечаем, что это на самом деле 24:00 (для сортировки это не важно, так как 00:00 < 01:00)
            return time(hour=0, minute=0)
        else:
            return datetime.strptime(time_str, "%H:%M").time()

    try:
        start_time = _parse_time_with_24(start_str)
        end_time = _parse_time_with_24(end_str)
    except ValueError as e:
        raise ValueError(f"Неверный формат времени в слоте {slot_str}: {e}")
    return start_time, end_time

async def create_combined_screenshot(page, output_path, spacing: int = 20):
    """
    Создает объединенный скриншот обеих таблиц отключений (сегодня и завтра).
    
    Args:
        page: Playwright page object
        output_path: Путь для сохранения результата (Path или str)
        spacing: Отступ между таблицами в пикселях (по умолчанию 20)
    """
    try:
        screenshot_selector = "div.discon-fact.active"
        
        # 1. Возвращаемся на первую вкладку (сегодня)
        logger.debug("Переход на первую таблицу для скриншота")
        today_tab_selector = "#discon-fact > div.dates > div:nth-child(1)"
        await page.click(today_tab_selector)
        await page.wait_for_selector("div.discon-fact-table:nth-child(1).active", timeout=3000)
        await page.wait_for_timeout(300)
        
        # 2. Делаем скриншот первой таблицы
        screenshot1_bytes = await page.locator(screenshot_selector).screenshot()
        logger.debug("✓ Скриншот первой таблицы (сегодня) получен")
        
        # 3. Переходим на вторую вкладку (завтра)
        logger.debug("Переход на вторую таблицу для скриншота")
        tomorrow_tab_selector = "#discon-fact > div.dates > div:nth-child(2)"
        await page.click(tomorrow_tab_selector)
        await page.wait_for_selector("div.discon-fact-table:nth-child(2).active", timeout=3000)
        await page.wait_for_timeout(300)
        
        # 4. Делаем скриншот второй таблицы
        screenshot2_bytes = await page.locator(screenshot_selector).screenshot()
        logger.debug("✓ Скриншот второй таблицы (завтра) получен")
        
        # 5. Объединяем два скриншота вертикально
        img1 = Image.open(io.BytesIO(screenshot1_bytes))
        img2 = Image.open(io.BytesIO(screenshot2_bytes))
        
        # Создаем новое изображение с суммарной высотой + отступ
        total_width = max(img1.width, img2.width)
        total_height = img1.height + spacing + img2.height
        combined_img = Image.new('RGB', (total_width, total_height), color='white')
        
        # Вставляем изображения одно под другим с отступом
        combined_img.paste(img1, (0, 0))
        combined_img.paste(img2, (0, img1.height + spacing))
        
        # Сохраняем объединенный скриншот
        combined_img.save(output_path)
        logger.info(f"✓ Объединенный скриншот сохранен: {output_path}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при создании объединенного скриншота: {e}")
        # Fallback: пытаемся сохранить хотя бы текущую активную таблицу
        try:
            await page.locator("div.discon-fact.active").screenshot(path=output_path)
            logger.warning(f"⚠ Сохранен скриншот только одной таблицы: {output_path}")
        except Exception as fallback_error:
            logger.error(f"❌ Не удалось создать даже резервный скриншот: {fallback_error}")

# --------------------------------------------------------------------------

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
            URL = "https://www.dtek-dnem.com.ua/ua/shutdowns"
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

                # --- ИЗМЕНЕНА ЛОГИКА ПАРСИНГА ---
                for th_element, td_element in zip(time_headers, data_cells):
                    time_text_content = await th_element.inner_text()
                    # Парсим сокращённый формат HH-HH
                    short_time_slot = re.sub(r'\s+', ' ', time_text_content.strip()).replace('\n', '-').replace(' – ', '-')

                    td_classes = await td_element.get_attribute("class") or ""

                    # Определяем статус и соответствующие 30-мин интервалы
                    disconection_status = "false" # Свет будет
                    slot_time_30min = None

                    if "cell-scheduled" in td_classes:
                        disconection_status = "full"
                        # Для full, интервал HH-HH означает оба 30-мин интервала
                        try:
                            start_hour, end_hour = parse_short_time_slot(short_time_slot)
                            # --- ИЗМЕНЕНИЕ: Проверка формата ---
                            if end_hour == (start_hour + 1) % 24:
                                pass # Формат корректен
                            elif start_hour == 23 and end_hour == 24:
                                # Специальный случай: 23-24, означает 23:00 - 24:00
                                pass # Формат корректен
                            else:
                                logger.warning(f"Непредвиденный формат слота {short_time_slot}, ожидается HH-HH+1 или 23-24. Пропускаем ячейку.")
                                continue
                            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
                        except ValueError as e:
                            logger.error(f"Ошибка парсинга сокращённого слота {short_time_slot} для full: {e}")
                            continue
                        # Создаём два слота HH:00-HH:30 и HH:30-HH+1:00
                        start_time_1 = time(hour=start_hour, minute=0)
                        end_time_1 = time(hour=start_hour, minute=30)
                        start_time_2 = time(hour=start_hour, minute=30)
                        end_time_2_hour = (start_hour + 1) % 24
                        end_time_2 = time(hour=end_time_2_hour, minute=0)
                        slots.append({"time": f"{format_time(start_time_1)}–{format_time(end_time_1)}", "disconection": "full"})
                        slots.append({"time": f"{format_time(start_time_2)}–{format_time(end_time_2)}", "disconection": "full"})
                    elif "cell-first-half" in td_classes:
                        disconection_status = "half"
                        # Для first-half, отключение в HH:00-HH:30
                        try:
                            start_hour, end_hour = parse_short_time_slot(short_time_slot)
                            # --- ИЗМЕНЕНИЕ: Проверка формата ---
                            if end_hour == (start_hour + 1) % 24:
                                pass # Формат корректен
                            elif start_hour == 23 and end_hour == 24:
                                # Специальный случай: 23-24, означает 23:00 - 24:00
                                pass # Формат корректен
                            else:
                                logger.warning(f"Непредвиденный формат слота {short_time_slot}, ожидается HH-HH+1 или 23-24. Пропускаем ячейку.")
                                continue
                            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
                        except ValueError as e:
                            logger.error(f"Ошибка парсинга сокращённого слота {short_time_slot} для first-half: {e}")
                            continue
                        start_time = time(hour=start_hour, minute=0)
                        end_time = time(hour=start_hour, minute=30)
                        slots.append({"time": f"{format_time(start_time)}–{format_time(end_time)}", "disconection": "half"})
                    elif "cell-second-half" in td_classes:
                        disconection_status = "half"
                        # Для second-half, отключение в HH:30-HH+1:00
                        try:
                            start_hour, end_hour = parse_short_time_slot(short_time_slot)
                            # --- ИЗМЕНЕНИЕ: Проверка формата ---
                            if end_hour == (start_hour + 1) % 24:
                                pass # Формат корректен
                            elif start_hour == 23 and end_hour == 24:
                                # Специальный случай: 23-24, означает 23:00 - 24:00
                                pass # Формат корректен
                            else:
                                logger.warning(f"Непредвиденный формат слота {short_time_slot}, ожидается HH-HH+1 или 23-24. Пропускаем ячейку.")
                                continue
                            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
                        except ValueError as e:
                            logger.error(f"Ошибка парсинга сокращённого слота {short_time_slot} для second-half: {e}")
                            continue
                        start_time = time(hour=start_hour, minute=30)
                        # --- ИЗМЕНЕНИЕ: Для слота 23-24 end_time_hour должен быть 0 ---
                        end_time_hour = (start_hour + 1) % 24 # Для 23 это даст 0
                        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
                        end_time = time(hour=end_time_hour, minute=0)
                        slots.append({"time": f"{format_time(start_time)}–{format_time(end_time)}", "disconection": "half"})
                    else:
                        # "false" - свет есть, добавляем слот, но он не будет учитываться при объединении
                        # Формируем временной слот для "false" аналогично
                        try:
                            start_hour, end_hour = parse_short_time_slot(short_time_slot)
                            # --- ИЗМЕНЕНИЕ: Проверка формата ---
                            if end_hour == (start_hour + 1) % 24:
                                pass # Формат корректен
                            elif start_hour == 23 and end_hour == 24:
                                # Специальный случай: 23-24, означает 23:00 - 24:00
                                pass # Формат корректен
                            else:
                                logger.warning(f"Непредвиденный формат слота {short_time_slot}, ожидается HH-HH+1 или 23-24. Пропускаем ячейку.")
                                continue
                            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
                        except ValueError as e:
                            logger.error(f"Ошибка парсинга сокращённого слота {short_time_slot} для false: {e}")
                            continue
                        start_time_1 = time(hour=start_hour, minute=0)
                        end_time_1 = time(hour=start_hour, minute=30)
                        start_time_2 = time(hour=start_hour, minute=30)
                        end_time_2_hour = (start_hour + 1) % 24
                        end_time_2 = time(hour=end_time_2_hour, minute=0)
                        slots.append({"time": f"{format_time(start_time_1)}–{format_time(end_time_1)}", "disconection": "false"})
                        slots.append({"time": f"{format_time(start_time_2)}–{format_time(end_time_2)}", "disconection": "false"})

                logger.info(f"Парсинг завершен для {date_text}. Найдено {len(slots)} 30-минутных слотов ДО объединения.")

                # --- ОБЪЕДИНЯЕМ СЛОТЫ ---
                # Извлекаем только слоты с отключениями ('full' или 'half')
                discon_slots = [s for s in slots if s['disconection'] in ['full', 'half']]
                # Объединяем их
                merged_discon_slots = merge_slots(discon_slots)
                logger.info(f"Объединено слотов отключений для {date_text}: {len(merged_discon_slots)}.")

                # 📌 Добавляем ОБЪЕДИНЕННЫЕ слоты в секцию schedule по дате
                aggregated_result["schedule"][date_text] = merged_discon_slots

            # Создаем объединенный скриншот обеих таблиц
            if is_debug:
                await create_combined_screenshot(page, png_path, spacing=40)

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