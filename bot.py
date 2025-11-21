import os
import re
import asyncio
import logging
import random 
import hashlib 
import aiosqlite
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple 
import aiohttp
from aiogram import Bot, Dispatcher, types, F 
from aiogram.filters import Command 
from aiogram.types import BotCommand, ReplyKeyboardRemove, BufferedInputFile 
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext 
from aiogram.fsm.state import State, StatesGroup 
import io
import math
import pytz 
from PIL import Image, ImageDraw, ImageFont
import json
# ----------------------------------

# --- 1. Конфігурація ---
BOT_TOKEN = os.getenv("SHUTDOWNS_TELEGRAM_BOT_TOKEN")
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000") 
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")
# --- Додано шлях до шрифту (універсальний, відносно папки зі скриптом) ---
FONT_PATH = os.getenv("FONT_PATH", os.path.join(os.path.dirname(__file__), "resources", "DejaVuSans.ttf")) 

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    'shutdowns_bot | %(levelname)s:%(name)s:%(message)s', 
    datefmt='%H:%M:%S'
)
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)

# ------------------------
dp = Dispatcher()
db_conn: aiosqlite.Connection = None 

# --- 1.5. FSM-состояния и Глобальный Кеш ---
class CaptchaState(StatesGroup):
    """Состояния для прохождения CAPTCHA-проверки"""
    waiting_for_answer = State()

class CheckAddressState(StatesGroup):
    """Состояния для пошагового ввода адреса через /check без аргументов"""
    waiting_for_city = State()
    waiting_for_street = State()
    waiting_for_house = State()

HUMAN_USERS: Dict[int, bool] = {} 
ADDRESS_CACHE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
SCHEDULE_DATA_CACHE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

DEFAULT_INTERVAL_HOURS = 1.0
CHECKER_LOOP_INTERVAL_SECONDS = 5 * 60

# ---------------------------------------------------------
# --- 1.8. Инициализация Базы Данных ---
async def init_db(db_path: str) -> aiosqlite.Connection:
    """Инициализирует соединение с SQLite и создает таблицы, если их нет."""
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL;")
    
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
        user_id INTEGER PRIMARY KEY,
        city TEXT NOT NULL,
        street TEXT NOT NULL,
        house TEXT NOT NULL,
        interval_hours REAL NOT NULL,
        next_check TIMESTAMP NOT NULL,
        last_schedule_hash TEXT,
        notification_lead_time INTEGER DEFAULT 0,
        last_alert_event_start TIMESTAMP,
        group_name TEXT
    )
    """)
    
    # --- Миграция: Добавляем колонки, если их нет (для существующих БД) ---
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN notification_lead_time INTEGER DEFAULT 0")
    except aiosqlite.OperationalError:
        pass # Колонка уже существует

    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN last_alert_event_start TIMESTAMP")
    except aiosqlite.OperationalError:
        pass # Колонка уже существует
    
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN group_name TEXT")
    except aiosqlite.OperationalError:
        pass # Колонка уже существует
    
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS user_last_check (
        user_id INTEGER PRIMARY KEY,
        city TEXT NOT NULL,
        street TEXT NOT NULL,
        house TEXT NOT NULL,
        last_hash TEXT,
        group_name TEXT
    )
    """)
    
    # --- Миграция: Добавляем group_name в user_last_check ---
    try:
        await conn.execute("ALTER TABLE user_last_check ADD COLUMN group_name TEXT")
    except aiosqlite.OperationalError:
        pass # Колонка уже существует
    await conn.commit()
    logger.info(f"Database initialized and connected at {db_path}")
    return conn

# --- 2. Вспомогательные функции ---

def parse_time_range(time_str: str) -> tuple:
    """
    Парсит строку формата 'HH:MM–HH:MM' и возвращает (start_minutes, end_minutes) с начала дня.
    """
    try:
        start_str, end_str = time_str.split('–')
        start_h, start_m = map(int, start_str.split(':'))
        end_h, end_m = map(int, end_str.split(':'))
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        # Обработка перехода через полночь: HH:MM -> HH+24:MM
        if end_min < start_min:
             end_min += 24 * 60
        return start_min, end_min
    except (ValueError, AttributeError):
        logger.error(f"Error parsing time range: {time_str}")
        return 0, 0 # Возвращаем 0,0 как ошибку

def format_minutes_to_hh_m(minutes: int) -> str:
    """Форматирует общее количество минут в HH:MM."""
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"

def _process_single_day_schedule_compact(date: str, slots: List[Dict[str, Any]], provider: str = "DTEK") -> str:
    """
    Генерирует компактное текстовое представление расписания для одного дня.
    Возвращает строку в формате:
    "🔴 14.11.2025: 10,5 год. відключень 00:00 - 02:00 (2 год.)..."
    Для ЦЕК использует 🟡/⚫, для ДТЕК - 🟢/🔴
    """
    outage_slots = slots

    # Выбираем емодзі (теперь одинаковые для всех)
    emoji_no_shutdown = "🟡"
    emoji_shutdown = "⚫"
    
    # Сценарий: Нет отключений
    if not outage_slots:
        return f"{emoji_no_shutdown} {date}: Відключення не заплановані"

    groups = []
    current_group = None
    total_duration_minutes = 0.0 # Суммируем в минутах для точности

    for slot in outage_slots:
        try:
            # --- ИЗМЕНЕНИЕ: Читаем ключ 'shutdown' вместо 'time' ---
            time_str = slot.get('shutdown', '00:00–00:00')
            slot_start_min, slot_end_min = parse_time_range(time_str)
            if slot_start_min == 0 and slot_end_min == 0:
                 continue # Ошибка парсинга, пропускаем
            # Учитываем длительность слота для подсчёта итога
            slot_duration_min = slot_end_min - slot_start_min

            total_duration_minutes += slot_duration_min

            # Логика объединения слотов
            if current_group is None:
                current_group = {
                    "start_min": slot_start_min,
                    "end_min": slot_end_min,
                    "duration_minutes": slot_duration_min 
                }
            elif slot_start_min <= current_group["end_min"]: # Проверяем пересечение или стыковку
                # Объединяем: расширяем конец и суммируем длительность
                current_group["end_min"] = max(current_group["end_min"], slot_end_min)
                current_group["duration_minutes"] += slot_duration_min
            else:
                # Слот не пересекается, сохраняем текущую группу и начинаем новую
                groups.append(current_group)
                current_group = {
                    "start_min": slot_start_min,
                    "end_min": slot_end_min,
                    "duration_minutes": slot_duration_min
                }
        except Exception as e:
            logger.error(f"Error processing slot {slot}: {e}")
            continue

    if current_group:
        groups.append(current_group)

    if not groups:
         return f"❌ {date}: Помилка парсингу слотів"
    
    # Формируем выходную строку
    total_duration_hours = total_duration_minutes / 60.0
    total_duration_str = _get_shutdown_duration_str_by_hours(total_duration_hours)
    output_parts = [f"{emoji_shutdown} {date}: {total_duration_str} відключень\n"]
    
    for group in groups:
        start_time_final = format_minutes_to_hh_m(group["start_min"])
        end_time_final = format_minutes_to_hh_m(group["end_min"])
        group_duration_hours = group["duration_minutes"] / 60.0
        duration_str = _get_shutdown_duration_str_by_hours(group_duration_hours)
        
        # Формат: " 00:00 - 02:00 (2 год.)"
        output_parts.append(f" {start_time_final} - {end_time_final} ({duration_str})\n")

    return "".join(output_parts)

def parse_address_from_text(text: str) -> tuple[str, str, str]:
    """Извлекает город, улицу и дом из строки, разделенной запятыми."""
    text = text.replace('/check', '').replace('/subscribe', '').replace('/unsubscribe', '').replace('/repeat', '').strip()
    parts = [p.strip() for p in text.split(',') if p.strip()]
    if len(parts) < 3:
        raise ValueError("Адреса має бути введена у форматі: **Місто, Вулиця, Будинок**.")
    city = parts[0]
    street = parts[1]
    house = parts[2]
    return city, street, house

def _get_hours_str(value: float) -> str:
    return "год."

def _get_shutdown_duration_str_by_hours(duration_hours: float) -> str:
    """Принимает количество часов и возвращает форматированную строку с правильным склонением."""
    try:
        if duration_hours <= 0:
             # ЗМІНА: Формат має бути "0 год."
             return "0 год." 
        if duration_hours % 1 == 0:
            hours_str = str(int(duration_hours))
        else:
            # Використовуємо :g для видалення зайвих нулів, і замінюємо . на ,
            hours_str = f"{duration_hours:g}".replace('.', ',')
        plural_form = _get_hours_str(duration_hours)
        return f"{hours_str} {plural_form}"
    except Exception:
        return "?"

def _get_schedule_hash_compact(data: dict) -> str:
    """
    Генерирует устойчивый хеш данных графика (schedule), используя каноническую 
    нормализованную JSON-строку. Это исключает влияние форматирования вывода 
    и неустойчивого порядка слотов.
    """
    normalized_data = _normalize_schedule_for_hash(data)
    
    if not normalized_data:
        return "NO_SCHEDULE_FOUND"

    # Создаем устойчивую (каноническую) JSON-строку:
    # ensure_ascii=False для кириллицы
    # separators=(',', ':') для удаления пробелов
    # sort_keys=True гарантирует порядок верхнего уровня (хотя наша нормализация уже это делает)
    schedule_json_string = json.dumps(
        normalized_data, 
        sort_keys=True, 
        ensure_ascii=False, 
        separators=(',', ':')
    )
    
    # Хешируем полученную строку
    return hashlib.sha256(schedule_json_string.encode('utf-8')).hexdigest()

# --- НОВАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ОТПРАВКИ ОТВЕТА ---
def _get_current_status_message(schedule: dict) -> str:
    """
    Определяет текущий статус (свет есть/нет) и время следующего изменения.
    Возвращает отформатированное сообщение или None, если данных недостаточно.
    """
    if not schedule:
        return None

    try:
        # 1. Получаем текущее время в Киеве
        kiev_tz = pytz.timezone('Europe/Kiev')
        now = datetime.now(kiev_tz)
        
        # Для тестов можно раскомментировать и подставить фиктивное время
        # now = datetime(2025, 11, 19, 14, 0, tzinfo=kiev_tz)

        current_date_str = now.strftime('%d.%m.%y')
        
        # 2. Собираем все слоты отключений в один список с datetime
        #    Учитываем сегодня и завтра, чтобы найти ближайшее событие
        all_outage_intervals = []

        # Сортируем даты
        try:
            sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
        except ValueError:
            sorted_dates = sorted(schedule.keys())

        for date_str in sorted_dates:
            # Пропускаем прошедшие дни (если вдруг они есть в json), но оставляем сегодня
            try:
                date_obj = datetime.strptime(date_str, '%d.%m.%y').date()
                if date_obj < now.date():
                    continue
            except ValueError:
                continue

            slots = schedule.get(date_str, [])
            for slot in slots:
                time_str = slot.get('shutdown', '00:00–00:00')
                start_min, end_min = parse_time_range(time_str)
                
                # Преобразуем в datetime
                # start_min - минуты от начала дня date_obj
                start_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=start_min)
                end_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=end_min)
                
                all_outage_intervals.append((start_dt, end_dt))

        # Сортируем интервалы по времени начала
        all_outage_intervals.sort(key=lambda x: x[0])

        # 3. Объединяем пересекающиеся или стыкующиеся интервалы
        merged_intervals = []
        if all_outage_intervals:
            current_start, current_end = all_outage_intervals[0]
            for next_start, next_end in all_outage_intervals[1:]:
                if next_start <= current_end:
                    current_end = max(current_end, next_end)
                else:
                    merged_intervals.append((current_start, current_end))
                    current_start, current_end = next_start, next_end
            merged_intervals.append((current_start, current_end))

        # 4. Определяем текущий статус
        is_light_off = False
        current_outage_end = None
        next_outage_start = None

        for start_dt, end_dt in merged_intervals:
            if start_dt <= now < end_dt:
                is_light_off = True
                current_outage_end = end_dt
                break
            elif start_dt > now:
                next_outage_start = start_dt
                break
        
        # Если мы не нашли next_outage_start в цикле (например, сейчас свет есть, но список кончился),
        # то next_outage_start останется None (значит, отключений пока не предвидится в загруженном графике)
        
        # Если сейчас отключение, но мы не нашли его в merged_intervals (странно, но вдруг), 
        # то is_light_off будет False.

        # Дополнительная проверка: если мы нашли current_outage_end, то следующее отключение
        # нужно искать после него.
        if is_light_off:
            # Ищем следующее включение (это current_outage_end)
            # Формируем сообщение
            time_str = current_outage_end.strftime('%H:%M')
            return f"⚫ Зараз діє відключення до {time_str}"
        else:
            # Свет есть. Ищем ближайшее отключение.
            # Если next_outage_start найден в цикле выше - используем его.
            # Если нет - значит в ближайшие 48 часов (или сколько есть в графике) отключений нет.
            if next_outage_start:
                time_str = next_outage_start.strftime('%H:%M')
                return f"🟡 Наступне відключення у {time_str}"
            else:
                # Якщо відключень немає - не показуємо статусне повідомлення
                return None

    except Exception as e:
        logger.error(f"Error calculating current status: {e}")
        return None
async def send_schedule_response(message: types.Message, api_data: dict, is_subscribed: bool):
    """
    Отправляет пользователю форматированный ответ, 
    разбитый по дням (текст) и один общий 48-часовой график (изображение).
    """
    try:
        # 1. Отправляем "шапку" (Адрес, Черга)
        city = api_data.get("city", "Н/Д")
        street = api_data.get("street", "Н/Д")
        house = api_data.get("house_num", "Н/Д")
        group = api_data.get("group", "Н/Д")
        provider = api_data.get("provider", "DTEK")

        header = (
            f"🏠 Адреса: `{city}, {street}, {house}`\n"
            f"🏭 Постачальник: `{provider}`\n"
            f"👥 Черга: `{group}`"
        )
        await message.answer(header)

        schedule = api_data.get("schedule", {})
        if not schedule:
            await message.answer("❌ *Не вдалося отримати графік відключень.*")
            if not is_subscribed:
                await message.answer("💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`.")
            return

        # 2. Сортируем даты
        try:
            sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
        except ValueError:
            sorted_dates = sorted(schedule.keys())

        # 3. Генерируем и отправляем график (выбор зависит от провайдера)
        if provider == "ЦЕК":
            # ЦЕК: 24-часовой график (только сегодня)
            today_slots = {}
            if sorted_dates:
                today_date = sorted_dates[0]
                today_slots[today_date] = schedule.get(today_date, [])
            
            if today_slots:
                image_data = _generate_24h_schedule_image(today_slots)
                
                if image_data:
                    await message.answer("🕙 **Графік на 24 години**:")
                    image_file = BufferedInputFile(image_data, filename="schedule_24h.png")
                    await message.answer_photo(photo=image_file)
        else:
            # ДТЕК: 48-часовой график (первые 2 дня)
            all_slots_48h = {}
            for idx, date in enumerate(sorted_dates[:2]): # Только первые 2 дня
                slots = schedule.get(date, [])
                all_slots_48h[date] = slots

            if all_slots_48h:
                image_data = _generate_48h_schedule_image(all_slots_48h)
                
                if image_data:
                    await message.answer("🕙 **Загальний графік на 48 годин**:")
                    image_file = BufferedInputFile(image_data, filename="schedule_48h.png")
                    await message.answer_photo(photo=image_file)


        # 5. Цикл по дням (Только текст)
        # Для ЦЕК показываем только сегодня (первый день)
        days_to_show = sorted_dates[:1] if provider == "ЦЕК" else sorted_dates
        
        for date in days_to_show:
            slots = schedule.get(date, [])
            day_text = _process_single_day_schedule_compact(date, slots, provider)
            # Отправляем весь день одной сообщением
            await message.answer(day_text.strip())

        # 5.5. Добавляем сообщение о текущем статусе
        status_msg = _get_current_status_message(schedule)
        if status_msg:
            await message.answer(status_msg)

        # 6. Отправляем "подвал" (приглашение к подписке)
        if not is_subscribed:
            await message.answer("💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`.")
    
    except Exception as e:
        logger.error(f"Error in send_schedule_response for user {message.from_user.id}: {e}", exc_info=True)
        await message.answer("❌ Сталася помилка під час формування відповіді.")

def _normalize_schedule_for_hash(data: dict) -> Dict[str, List[Dict[str, str]]]:
    """
    Нормализует данные расписания, сортируя их по дате и слотам.
    Это необходимо, чтобы хеш зависел только от содержания, а не от порядка в исходном JSON.
    """
    schedule = data.get("schedule", {})
    if not schedule:
        return {}

    normalized_schedule = {}

    try:
        # 1. Сортировка ключей по дате
        sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
    except ValueError:
        # Если формат даты не '%d.%m.%y', сортируем просто по строке
        sorted_dates = sorted(schedule.keys())

    for date in sorted_dates:
        slots = schedule.get(date, [])
        
        # 2. Сортировка слотов по времени начала (используя parse_time_range)
        def sort_key(slot):
            time_str = slot.get('shutdown', '00:00–00:00')
            start_min, _ = parse_time_range(time_str)
            return start_min

        sorted_slots = sorted(slots, key=sort_key)
        
        # 3. Сохраняем только ключевые данные, исключая потенциально лишние поля
        normalized_slots = []
        for slot in sorted_slots:
            # Убеждаемся, что хешируем только "shutdown", так как это основной маркер
            if 'shutdown' in slot:
                normalized_slots.append({'shutdown': slot['shutdown']})
        
        normalized_schedule[date] = normalized_slots

    return normalized_schedule

# ---------------------------------------------------------

def _generate_48h_schedule_image(days_slots: Dict[str, List[Dict[str, Any]]]) -> bytes:
    """
    Генерирует 48-часовое изображение графика (clock-face) в стиле ЦЕК.
    - 48 секторов (по 1 часу).
    - Верхняя половина = День 1, Нижняя половина = День 2.
    - Цвета: Зеленый (свет есть), Красный (света нет).
    - Метки каждые 2 часа.
    """
    global FONT_PATH
    
    if not days_slots:
        return None

    try:
        # 1. Сортировка дат
        try:
            sorted_dates = sorted(days_slots.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
        except ValueError:
            sorted_dates = sorted(days_slots.keys())
        
        # 2. Преобразуем слоты в массив 48 часов
        hours_status = [False] * 48
        has_any_shutdowns = False
        
        for day_idx, date in enumerate(sorted_dates[:2]):
            slots = days_slots[date]
            if slots:
                has_any_shutdowns = True
            day_offset = day_idx * 24
            
            for slot in slots:
                try:
                    # Читаем ключ 'shutdown'
                    time_str = slot.get('shutdown', '00:00–00:00')
                    time_parts = time_str.split('–')
                    if len(time_parts) != 2:
                        continue
                        
                    start_h, start_m = map(int, time_parts[0].split(':'))
                    end_h, end_m = map(int, time_parts[1].split(':'))
                    
                    # Абсолютные часы 0..48
                    abs_start = start_h + day_offset
                    abs_end = end_h + day_offset
                    
                    # Обработка перехода через полночь внутри слота
                    if end_h < start_h:
                         abs_end += 24
                    
                    # Заполняем часы
                    curr = abs_start
                    while curr < abs_end:
                        if 0 <= curr < 48:
                            hours_status[curr] = True
                        curr += 1
                        
                    # Если есть минуты в конце, захватываем и этот час (как в ЦЕК)
                    if end_m > 0:
                        if 0 <= abs_end < 48:
                            hours_status[abs_end] = True

                except Exception as e:
                    logger.warning(f"Error processing DTEK slot '{slot}': {e}")
                    continue

        if not has_any_shutdowns:
            return None

        # 3. Настройка рисования
        size = 300  # Как у ЦЕК
        padding = 30
        center = (size // 2, size // 2)
        radius = (size // 2) - padding
        bbox = [padding, padding, size - padding, size - padding]
        image = Image.new('RGB', (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(image)

        # 4. Шрифт
        font_size = 9
        font = None
        try:
            font = ImageFont.truetype(FONT_PATH, font_size)
        except IOError:
            logger.warning(f"Font not found at '{FONT_PATH}', using default")
            font = ImageFont.load_default()

        # 5. Рисуем 48 секторов (7.5 градусов каждый)
        degrees_per_hour = 360.0 / 48.0
        
        for hour in range(48):
            # 00:00 День 1 = 180 градусов (Слева)
            # Идем по часовой стрелке
            start_angle = 180 + (hour * degrees_per_hour)
            end_angle = start_angle + degrees_per_hour
            
            # Цвет: Черный (отключение) / Желтый (свет)
            # Используем цвета как у ЦЕК
            color = "#000000" if hours_status[hour] else "#FFD700"
            
            draw.pieslice(bbox, start_angle, end_angle, fill=color, outline=None)

        # 6. Белые разделительные линии (каждый час)
        for hour in range(48):
            angle_deg = 180 + (hour * degrees_per_hour)
            angle_rad = math.radians(angle_deg)
            x_pos = center[0] + radius * math.cos(angle_rad)
            y_pos = center[1] + radius * math.sin(angle_rad)
            draw.line([center, (x_pos, y_pos)], fill="#FFFFFF", width=2)

        # 7. Белый центральный круг
        inner_radius = int(radius * 0.50)
        inner_bbox = [
            center[0] - inner_radius,
            center[1] - inner_radius,
            center[0] + inner_radius,
            center[1] + inner_radius
        ]
        draw.ellipse(inner_bbox, fill='#FFFFFF', outline=None)
        
        # --- Разделительная линия (0-24) ---
        # Тонкая линия на весь диаметр
        draw.line(
            [(padding, center[1]), (size - padding, center[1])],
            fill='#000000',
            width=1
        )
        
        # 8. Даты в центре
        try:
            date_font = font
            
            # День 1 (Верхняя половина)
            if len(sorted_dates) >= 1:
                date1 = sorted_dates[0]
                # Рисуем во временное изображение для точного позиционирования
                temp_img = Image.new('RGBA', (100, 50), (255, 255, 255, 0))
                temp_draw = ImageDraw.Draw(temp_img)
                temp_draw.text((50, 40), date1, fill='#000000', font=date_font, anchor="ms") # ms = middle, baseline (bottom)
                
                bbox_date = temp_img.getbbox()
                if bbox_date:
                    cropped_date = temp_img.crop(bbox_date)
                    # Размещаем выше центра (отступ 10px)
                    paste_x = int(center[0] - cropped_date.width // 2)
                    paste_y = int(center[1] - cropped_date.height - 10)
                    image.paste(cropped_date, (paste_x, paste_y), cropped_date)
            
            # День 2 (Нижняя половина) - Перевернута
            if len(sorted_dates) >= 2:
                date2 = sorted_dates[1]
                
                temp_img = Image.new('RGBA', (100, 50), (255, 255, 255, 0))
                temp_draw = ImageDraw.Draw(temp_img)
                # Рисуем текст нормально
                temp_draw.text((50, 40), date2, fill='#000000', font=date_font, anchor="ms")
                
                # Обрезаем
                bbox_date = temp_img.getbbox()
                if bbox_date:
                    cropped_date = temp_img.crop(bbox_date)
                    # Поворачиваем на 180
                    rotated_date = cropped_date.rotate(180, expand=True)
                    
                    # Размещаем ниже центра (отступ 10px)
                    paste_x = int(center[0] - rotated_date.width // 2)
                    paste_y = int(center[1] + 10)
                    image.paste(rotated_date, (paste_x, paste_y), rotated_date)

        except Exception as e:
            logger.warning(f"Failed to add dates: {e}")

        # 9. Метки (только начало и конец отключений + 00:00 и 24:00)
        label_radius = radius + (padding * 0.6)
        
        # Собираем все уникальные точки времени (в часах 0..48)
        label_points = set()
        
        # Добавляем фиксированные метки: 00:00 (слева) и 24:00 (справа)
        label_points.add(0.0)
        label_points.add(24.0)
        
        for day_idx, date in enumerate(sorted_dates[:2]):
            slots = days_slots[date]
            day_offset = day_idx * 24
            
            for slot in slots:
                try:
                    time_str = slot.get('shutdown', '00:00–00:00')
                    time_parts = time_str.split('–')
                    if len(time_parts) != 2:
                        continue
                        
                    start_h, start_m = map(int, time_parts[0].split(':'))
                    end_h, end_m = map(int, time_parts[1].split(':'))
                    
                    # Добавляем точки (в формате float часов)
                    start_point = day_offset + start_h + (start_m / 60.0)
                    end_point = day_offset + end_h + (end_m / 60.0)
                    
                    # Коррекция перехода через полночь
                    if end_point < start_point:
                        end_point += 24
                        
                    label_points.add(start_point)
                    label_points.add(end_point)
                    
                except Exception:
                    continue
        
        # Рисуем метки
        for point in label_points:
            # Угол
            angle_deg = 180 + (point * degrees_per_hour)
            angle_rad = math.radians(angle_deg)
            
            x_pos = center[0] + label_radius * math.cos(angle_rad)
            y_pos = center[1] + label_radius * math.sin(angle_rad)
            
            # Текст метки: ВСЕГДА "HH:MM"
            # Специальная обработка для 24.0 -> "24:00"
            if abs(point - 24.0) < 0.001:
                label = "24:00"
            else:
                hours = int(point) % 24
                minutes = int(round((point - int(point)) * 60))
                label = f"{hours:02d}:{minutes:02d}"
            
            try:
                # Поворачиваем текст для читаемости? Нет, просто размещаем
                draw.text((x_pos, y_pos), label, fill="black", font=font, anchor="mm")
            except Exception:
                bbox_text = draw.textbbox((0, 0), label, font=font)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
                draw.text((x_pos - text_width / 2, y_pos - text_height / 2), label, fill="black", font=font)

        # 10. Сохранение
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        buf.seek(0)
        return buf.getvalue()

    except Exception as e:
        logger.error(f"Failed to generate 48h schedule image: {e}", exc_info=True)
        return None

def _generate_24h_schedule_image(day_slots: Dict[str, List[Dict[str, Any]]]) -> bytes:
    """
    Генерирует 24-часовое изображение графика для ЦЕК.
    НОВАЯ ЛОГИКА: 24 равных сектора (по 1 часу каждый) с белыми разделительными линиями.
    Метки часов в середине каждого сектора (например, "20-21", "08-09").
    """
    global FONT_PATH
    
    if not day_slots:
        return None

    try:
        # 1. Получаем данные для сегодня
        try:
            sorted_dates = sorted(day_slots.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
        except ValueError:
            sorted_dates = sorted(day_slots.keys())
        
        if not sorted_dates:
            return None
            
        today_date = sorted_dates[0]
        today_slots = day_slots[today_date]
        
        if not today_slots:
            return None
        
        # 2. Создаем массив из 24 часов (каждый час - отдельный сектор)
        # True = отключение (черный), False = свет есть (желтый)
        hours_status = [False] * 24  # По умолчанию везде свет
        
        # Заполняем часы с отключениями
        for slot in today_slots:
            try:
                time_str = slot.get('shutdown', '00:00–00:00')
                time_parts = time_str.split('–')
                if len(time_parts) != 2:
                    continue
                    
                start_h, start_m = map(int, time_parts[0].split(':'))
                end_h, end_m = map(int, time_parts[1].split(':'))
                
                # Определяем какие часы затронуты
                current_h = start_h
                while True:
                    if current_h >= 24:
                        current_h -= 24
                    hours_status[current_h] = True
                    
                    # Проверяем достигли ли конца
                    if current_h == end_h or (current_h + 1) % 24 == end_h:
                        if end_m > 0:  # Если минуты > 0, значит час затронут
                            hours_status[end_h % 24] = True
                        break
                    
                    current_h += 1
                    if current_h >= 24:
                        current_h = 0
                    
                    # Защита от бесконечного цикла
                    if current_h == start_h:
                        break
                        
            except Exception as e:
                logger.warning(f"Error processing shutdown slot '{slot}': {e}")
                continue

        # 3. Настройка рисования
        size = 300  # Как у ДТЕК
        padding = 30  # Как у ДТЕК
        center = (size // 2, size // 2)
        radius = (size // 2) - padding
        bbox = [padding, padding, size - padding, size - padding]
        image = Image.new('RGB', (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(image)

        # 4. Загрузка шрифта
        font_size = 9  # Уменьшен шрифт с 12 до 9
        font = None
        try:
            font = ImageFont.truetype(FONT_PATH, font_size)
        except IOError:
            logger.warning(f"Font not found at '{FONT_PATH}', using default")
            font = ImageFont.load_default()

        # 5. Рисуем 24 сектора
        # Каждый сектор = 15 градусов (360 / 24)
        # Начало: 00:00 слева (180 градусов)
        degrees_per_hour = 360.0 / 24.0  # 15 градусов на час
        
        for hour in range(24):
            # Угол начала сектора (00:00 = 180°, идем по часовой стрелке)
            start_angle = 180 + (hour * degrees_per_hour)
            end_angle = start_angle + degrees_per_hour
            
            # Цвет сектора
            color = "#000000" if hours_status[hour] else "#FFD700"
            
            # Рисуем сектор
            draw.pieslice(bbox, start_angle, end_angle, fill=color, outline=None)

        # 6. Рисуем белые разделительные линии между всеми секторами
        for hour in range(24):
            angle_deg = 180 + (hour * degrees_per_hour)
            angle_rad = math.radians(angle_deg)
            x_pos = center[0] + radius * math.cos(angle_rad)
            y_pos = center[1] + radius * math.sin(angle_rad)
            draw.line([center, (x_pos, y_pos)], fill="#FFFFFF", width=2)

        # 7. Белый центральный круг
        inner_radius = int(radius * 0.50)
        inner_bbox = [
            center[0] - inner_radius,
            center[1] - inner_radius,
            center[0] + inner_radius,
            center[1] + inner_radius
        ]
        draw.ellipse(inner_bbox, fill='#FFFFFF', outline=None)
        
        # 8. Дата в центре
        try:
            temp_img = Image.new('RGBA', (100, 100), (255, 255, 255, 0))
            temp_draw = ImageDraw.Draw(temp_img)
            temp_draw.text((50, 50), today_date, fill='#000000', font=font, anchor="mm")
            
            bbox_date = temp_img.getbbox()
            if bbox_date:
                cropped_date = temp_img.crop(bbox_date)
                paste_x = int(center[0] - cropped_date.width // 2)
                paste_y = int(center[1] - cropped_date.height // 2)
                image.paste(cropped_date, (paste_x, paste_y), cropped_date)
        except Exception as e:
            logger.warning(f"Failed to add date: {e}")

        # 9. Метки часов в середине каждого сектора
        # Показываем ВСЕ 24 часа
        label_radius = radius + (padding * 0.5)  # Оптимальное расстояние от секторов
        
        for hour in range(0, 24):  # Все 24 часа
            # Угол середины сектора
            mid_angle_deg = 180 + (hour * degrees_per_hour) + (degrees_per_hour / 2)
            mid_angle_rad = math.radians(mid_angle_deg)
            
            x_pos = center[0] + label_radius * math.cos(mid_angle_rad)
            y_pos = center[1] + label_radius * math.sin(mid_angle_rad)
            
            # Метка вида "20-21", "08-09"
            next_hour = (hour + 1) % 24
            label = f"{hour:02d}-{next_hour:02d}"
            
            try:
                draw.text((x_pos, y_pos), label, fill="black", font=font, anchor="mm")
            except Exception:
                # Fallback для старых версий Pillow
                bbox_text = draw.textbbox((0, 0), label, font=font)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
                draw.text((x_pos - text_width / 2, y_pos - text_height / 2), label, fill="black", font=font)

        # 10. Без обводки основного кольца

        # 11. Сохранение
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        buf.seek(0)
        return buf.getvalue()

    except Exception as e:
        logger.error(f"Failed to generate 24h CEK diagram: {e}", exc_info=True)
        return None

# -----------------------------------------------

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ CAPTCHA ---
def _get_captcha_data() -> Tuple[str, int]:
    """Генерирует простое математическое задание и ответ."""
    a = random.randint(5, 15)
    b = random.randint(1, 5)
    operation = random.choice(['+', '-'])
    if operation == '+':
        question = f"Скільки буде {a} + {b}?"
        answer = a + b
    else:
        question = f"Скільки буде {a} - {b}?"
        answer = a - b
    return question, answer

async def _handle_captcha_check(message: types.Message, state: FSMContext) -> bool:
    """Проверяет, прошел ли пользователь CAPTCHA. Возвращает True, если прошел."""
    user_id = message.from_user.id
    if user_id in HUMAN_USERS:
        return True

    await state.set_state(CaptchaState.waiting_for_answer)
    question, correct_answer = _get_captcha_data()
    await state.update_data(captcha_answer=correct_answer)
    await message.answer(
        "⚠️ **Увага! Для захисту від ботів, пройдіть просту перевірку.**\n"
        f"**{question}**\n"
        "Введіть лише число-відповідь."
    )
    return False

# --- 3. Интеграция с API ---
async def _fetch_shutdowns_data_from_api(city: str, street: str, house: str, cached_group: str = None) -> dict:
    """Выполняет HTTP-запрос к API и возвращает JSON-ответ."""
    params = {
        "city": city,
        "street": street,
        "house": house
    }
    # Add cached_group if available
    if cached_group:
        params["cached_group"] = cached_group
        
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE_URL}/shutdowns", params=params, timeout=60) as response:
            if response.status == 404:
                error_json = {}
                try:
                    error_json = await response.json()
                except aiohttp.ContentTypeError:
                    pass
                detail = error_json.get("detail", "Графік для цієї адреси не знайдено.")
                raise ValueError(detail)
            response.raise_for_status()
            return await response.json()

async def get_shutdowns_data(city: str, street: str, house: str, cached_group: str = None) -> dict:
    """Вызывает API-парсер и возвращает полный агрегированный JSON-ответ."""
    try:
        return await _fetch_shutdowns_data_from_api(city, street, house, cached_group)
    except aiohttp.ClientError:
        logger.error("API Connection Error during shutdown data fetch.", exc_info=True)
        raise ConnectionError("Помилка підключення до парсера. Спробуйте пізніше.")
    except asyncio.TimeoutError:
        raise ConnectionError("Таймаут запроса к API. Парсер не ответил вовремя.")
    except Exception as e:
        if isinstance(e, aiohttp.ClientResponseError):
            raise Exception(f"API Internal Error: HTTP {e.status}")
        raise e

# --- Фоновая задача для проверки подписок ---
async def subscription_checker_task(bot: Bot):
    """Фонова задача: періодично перевіряє графік для всіх підписаних користувачів з бази даних."""
    global db_conn
    logger.info("Subscription checker started.")
    while True:
        await asyncio.sleep(CHECKER_LOOP_INTERVAL_SECONDS)
        if db_conn is None:
            logger.error("DB connection is not available. Skipping check cycle.")
            continue

        now = datetime.now()
        users_to_check = []
        try:
            cursor = await db_conn.execute(
                "SELECT user_id, city, street, house, interval_hours, last_schedule_hash FROM subscriptions WHERE next_check <= ?", 
                (now,)
            )
            rows = await cursor.fetchall()
            if not rows:
                logger.debug("Subscription check skipped: no users require check.")
                continue

            for row in rows:
                users_to_check.append({
                    'user_id': row[0],
                    'city': row[1],
                    'street': row[2],
                    'house': row[3],
                    'interval_hours': row[4],
                    'last_schedule_hash': row[5]
                })
        except Exception as e:
            logger.error(f"Failed to fetch subscriptions from DB: {e}", exc_info=True)
            continue

        logger.debug(f"Starting subscription check for {len(users_to_check)} users at {now.strftime('%H:%M:%S')}.")

        addresses_to_check_map: Dict[Tuple[str, str, str], List[int]] = {}
        for sub_data in users_to_check:
            address_key = (sub_data['city'], sub_data['street'], sub_data['house'])
            if address_key not in addresses_to_check_map:
                addresses_to_check_map[address_key] = []
            addresses_to_check_map[address_key].append(sub_data['user_id'])

        logger.info(f"Checking {len(addresses_to_check_map)} unique addresses now for {len(users_to_check)} users.")

        api_results: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

        for address_key in addresses_to_check_map.keys():
            city, street, house = address_key
            address_str = f"`{city}, {street}, {house}`"
            try:
                logger.debug(f"Calling API for address {address_str}")
                data = await get_shutdowns_data(city, street, house)
                current_hash = _get_schedule_hash_compact(data) # ИСПРАВЛЕНО: используем новую функцию
                ADDRESS_CACHE[address_key] = {
                    'last_schedule_hash': current_hash,
                    'last_checked': now 
                }
                # --- НОВОЕ: Сохраняем полные данные для алертов ---
                SCHEDULE_DATA_CACHE[address_key] = data
                
                api_results[address_key] = data
            except Exception as e:
                logger.error(f"Error checking address {address_str}: {e}")
                api_results[address_key] = {"error": str(e)}

        db_updates_success = []
        db_updates_fail = []

        for sub_data in users_to_check:
            user_id = sub_data['user_id']
            city = sub_data['city']
            street = sub_data['street']
            house = sub_data['house']
            address_key = (city, street, house)
            address_str = f"`{city}, {street}, {house}`"
            interval_hours = sub_data.get('interval_hours', DEFAULT_INTERVAL_HOURS)
            interval_delta = timedelta(hours=interval_hours)
            next_check_time = now + interval_delta
            data_or_error = api_results.get(address_key)

            if data_or_error is None:
                logger.error(f"Address {address_key} was checked, but result is missing.")
                db_updates_fail.append((next_check_time, user_id))
                continue

            if "error" in data_or_error:
                error_message = data_or_error['error']
                final_message = f"❌ **Помилка перевірки** для {address_str}: {error_message}\n*Перевірка буде повторена через {f'{interval_hours:g}'.replace('.', ',')} {_get_hours_str(interval_hours)}.*"
                try:
                    await bot.send_message(chat_id=user_id, text=final_message, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Failed to send error message to user {user_id}: {e}")

                db_updates_fail.append((next_check_time, user_id))
                continue

            data = data_or_error
            last_hash = sub_data.get('last_schedule_hash')
            new_hash = ADDRESS_CACHE[address_key]['last_schedule_hash']

            if new_hash != last_hash:
                
                # ИСПРАВЛЕНИЕ: Определение 'group' из полученных данных.
                group = data.get("group", "Н/Д") 
                provider = data.get("provider", "DTEK")
                
                # --- ИЗМЕНЕНИЕ: Форматирование уведомления с учетом нового формата ---
                # Отправляем "шапку" (Адрес, Черга)
                header_msg = (
                    f"🏠 Адреса: `{city}, {street}, {house}`\n"
                    f"🏭 Постачальник: `{provider}`\n"
                    f"👥 Черга: `{group}`"
                )
                interval_str = f"{f'{interval_hours:g}'.replace('.', ',')} год"
                update_header = "🔔 **ОНОВЛЕННЯ ГРАФІКУ!**" if last_hash not in (None, "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION") else "🔔 **Графік перевірено**"
                
                await bot.send_message(
                    chat_id=user_id,
                    text=f"{update_header}\nдля {address_str} (інтервал {interval_str}):\n{header_msg}",
                    parse_mode="Markdown"
                )

                # --- ИЗМЕНЕНИЕ: Сначала отправляем диаграмму (выбор зависит от провайдера) ---
                schedule = data.get("schedule", {})
                try:
                    sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
                except ValueError:
                    sorted_dates = sorted(schedule.keys())

                # Генерируем диаграмму в зависимости от провайдера
                if provider == "ЦЕК":
                    # ЦЕК: 24-часовой график (только сегодня)
                    today_slots = {}
                    if sorted_dates:
                        today_date = sorted_dates[0]
                        today_slots[today_date] = schedule.get(today_date, [])
                    
                    if today_slots:
                        image_data = _generate_24h_schedule_image(today_slots)
                        if image_data:
                            await bot.send_message(chat_id=user_id, text="🕙 **Графік на 24 години**:")
                            image_file = BufferedInputFile(image_data, filename="schedule_24h_update.png")
                            await bot.send_photo(chat_id=user_id, photo=image_file)
                else:
                    # ДТЕК: 48-часовой график (первые 2 дня)
                    days_slots_48h = {}
                    for idx, date in enumerate(sorted_dates[:2]): # Только первые 2 дня
                        slots = schedule[date]
                        days_slots_48h[date] = slots

                    if days_slots_48h:
                        image_data = _generate_48h_schedule_image(days_slots_48h)
                        if image_data:
                            await bot.send_message(chat_id=user_id, text="🕙 **Загальний графік на 48 годин**:")
                            image_file = BufferedInputFile(image_data, filename="schedule_48h_update.png")
                            await bot.send_photo(chat_id=user_id, photo=image_file)


                # --- КОНЕЦ ИЗМЕНЕНИЯ ---

                # --- ИЗМЕНЕНИЕ: Затем отправляем текстовые данные по дням ---
                # Для ЦЕК показываем только сегодня (первый день)
                days_to_show = sorted_dates[:1] if provider == "ЦЕК" else sorted_dates
                
                for date in days_to_show:
                    slots = schedule[date]
                    day_text = _process_single_day_schedule_compact(date, slots, provider)
                    # Отправляем весь день одной сообщением
                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=day_text.strip(),
                            parse_mode="Markdown" # Используем Markdown, но без моноширинного форматирования
                        )
                    except Exception as e:
                        logger.error(f"Failed to send update message to user {user_id}: {e}")
                # --- КОНЕЦ ИЗМЕНЕНИЯ ---

                # Добавляем статусное сообщение в конце (как в send_schedule_response)
                status_msg = _get_current_status_message(schedule)
                if status_msg:
                    try:
                        await bot.send_message(chat_id=user_id, text=status_msg)
                    except Exception as e:
                        logger.error(f"Failed to send status message to user {user_id}: {e}")

                db_updates_success.append((next_check_time, new_hash, user_id))
                logger.info(f"Notification sent to user {user_id}. Hash updated to {new_hash[:8]}.")
            else:
                logger.debug(f"User {user_id} check for {address_str}. No change in hash: {new_hash[:8]}.")
                db_updates_fail.append((next_check_time, user_id))

        try:
            if db_updates_success:
                await db_conn.executemany(
                    "UPDATE subscriptions SET next_check = ?, last_schedule_hash = ? WHERE user_id = ?",
                    db_updates_success
                )
            if db_updates_fail:
                 await db_conn.executemany(
                    "UPDATE subscriptions SET next_check = ? WHERE user_id = ?",
                    db_updates_fail
                )
            await db_conn.commit()
            logger.debug(f"DB updated for {len(db_updates_success)} success and {len(db_updates_fail)} other checks.")
        except Exception as e:
             logger.error(f"Failed to batch update subscriptions in DB: {e}", exc_info=True)

# --- 4. Обработчики команд (Telegram) ---

@dp.message(Command("start", "help"))
async def command_start_handler(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    if user_id not in HUMAN_USERS:
        is_human = await _handle_captcha_check(message, state)
        if not is_human:
            return

    text = (
        "👋 **Вітаю! Я бот для перевірки графіків відключень ДТЕК.**\n"
        "Для перевірки графіку, введіть команду **/check**, додавши адресу у форматі:\n"
        "`/check Місто, Вулиця, Будинок`\n"
        "**АБО** просто введіть **/check** без адреси, щоб ввести дані покроково.\n"
        "**Наприклад:**\n"
        "`/check м. Дніпро, вул. Сонячна набережна, 6`\n"
        "**Команди:**\n"
        "/start або /help - показати цю довідку.\n"
        "/check - перевірити графік за адресою.\n"
        "/repeat - повторити останню перевірку /check.\n"
        "/subscribe - підписатися на оновлення (за замовчуванням 1 година).\n"
        "*Приклад: `/subscribe 3` (кожні 3 години). Автоматично вмикає сповіщення за 15 хв.*\n"
        "/unsubscribe - скасувати підписку.\n"
        "/alert - налаштувати час сповіщення (або вимкнути).\n"
        "*Приклад: `/alert 30` (за 30 хв) або `/alert 0` (вимкнути)*\n"
        "/cancel - скасувати поточну дію."
    )
    await message.answer(text, reply_markup=ReplyKeyboardRemove())

@dp.message(CaptchaState.waiting_for_answer, F.text.regexp(r"^\d+$"))
async def captcha_answer_handler(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    data = await state.get_data()
    correct_answer = data.get("captcha_answer")
    try:
        user_answer = int(message.text.strip())
    except ValueError:
        user_answer = -1

    if user_answer == correct_answer:
        HUMAN_USERS[user_id] = True
        await state.clear()
        await message.answer(
            "✅ **Перевірка пройдена!**\n"
            "Тепер ви можете користуватися всіма функціями бота. Введіть **/start** ще раз, щоб побачити список команд.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await state.clear()
        await message.answer(
            "❌ **Неправильна відповідь.** Спробуйте ще раз, ввівши **/start**."
        )

# --- ОБРАБОТЧИК /cancel (ДОЛЖЕН БЫТЬ ПЕРВЫМ ПЕРЕД FSM-ОБРАБОТЧИКАМИ) ---
@dp.message(Command("cancel"))
async def command_cancel_handler(message: types.Message, state: FSMContext) -> None:
    """Обработчик команды /cancel, который срабатывает независимо от текущего состояния FSM."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Немає активних дій для скасування.")
        return
    await state.clear()
    await message.answer("Дію скасовано. Введіть /check [адреса], щоб почати перевірку, або /check для покрокового вводу.")

# --- ОБРАБОТЧИКИ FSM ДЛЯ ПОШАГОВОГО ВВОДА АДРЕСА ---
@dp.message(CheckAddressState.waiting_for_city, F.text)
async def process_city(message: types.Message, state: FSMContext) -> None:
    city = message.text.strip()
    await state.update_data(city=city)
    await state.set_state(CheckAddressState.waiting_for_street)
    await message.answer(f"📍 Місто: `{city}`\n**Будь ласка, введіть назву вулиці** (наприклад, `вул. Сонячна набережна`):")

@dp.message(CheckAddressState.waiting_for_street, F.text)
async def process_street(message: types.Message, state: FSMContext) -> None:
    street = message.text.strip()
    await state.update_data(street=street)
    await state.set_state(CheckAddressState.waiting_for_house)
    await message.answer(f"📍 Вулиця: `{street}`\n**Будь ласка, введіть номер будинку** (наприклад, `6`):")

@dp.message(CheckAddressState.waiting_for_house, F.text)
async def process_house(message: types.Message, state: FSMContext) -> None:
    global db_conn
    user_id = message.from_user.id
    house = message.text.strip()
    data = await state.get_data()
    city = data.get('city', '')
    street = data.get('street', '')
    address_str = f"`{city}, {street}, {house}`"
    await message.answer(f"✅ **Перевіряю графік** для адреси: {address_str}\n\n⏳ Очікуйте...")

    try:
        # Try to get cached group from database
        cached_group = None
        cursor = await db_conn.execute(
            "SELECT group_name FROM user_last_check WHERE user_id = ? AND city = ? AND street = ? AND house = ?",
            (user_id, city, street, house)
        )
        row = await cursor.fetchone()
        if row and row[0]:
            cached_group = row[0]
            logger.info(f"Using cached group: {cached_group}")
        
        api_data = await get_shutdowns_data(city, street, house, cached_group)
        current_hash = _get_schedule_hash_compact(api_data) # ИСПРАВЛЕНО: используем новую функцию
        group = api_data.get('group', None)  # Extract group from API response
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, city, street, house, last_hash, group_name) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, city, street, house, current_hash, group)
        )
        await db_conn.commit()
        await state.clear()
        
        cursor = await db_conn.execute("SELECT 1 FROM subscriptions WHERE user_id = ?", (user_id,))
        is_subscribed = bool(await cursor.fetchone())
        
        # --- Вызов функции-отправщика с графиком ---
        await send_schedule_response(message, api_data, is_subscribed)
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    except (ValueError, ConnectionError) as e:
        await state.clear()
        error_type = "Помилка вводу/помилка API" if isinstance(e, ValueError) else "Помилка"
        error_message = f"❌ **{error_type}:** {e}"
        error_message += "\n*Попередній успішний запит (якщо він був) збережено. Ви можете його повторити командою `/repeat`.*"
        await message.answer(error_message)
    except Exception as e:
        await state.clear()
        logger.error(f"Critical error during FSM address process for user {user_id}: {e}", exc_info=True)
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")

# --- ОБРАБОТЧИК /check ---
@dp.message(Command("check")) 
async def command_check_handler(message: types.Message, state: FSMContext) -> None:
    global db_conn
    user_id = message.from_user.id
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await _handle_captcha_check(message, state)
        return

    text_args = message.text.replace('/check', '', 1).strip()
    if not text_args:
        await state.set_state(CheckAddressState.waiting_for_city)
        await message.answer("📍 **Будь ласка, введіть назву міста** (наприклад, `м. Дніпро`):")
        return

    current_state = await state.get_state()
    if current_state:
        await state.clear()

    await message.answer("⏳ Перевіряю графік за вказаною адресою. Очікуйте...")
    try:
        city, street, house = parse_address_from_text(text_args)
        
        # Try to get cached group from database
        cached_group = None
        cursor = await db_conn.execute(
            "SELECT group_name FROM user_last_check WHERE user_id = ? AND city = ? AND street = ? AND house = ?",
            (user_id, city, street, house)
        )
        row = await cursor.fetchone()
        if row and row[0]:
            cached_group = row[0]
            logger.info(f"Using cached group for inline check: {cached_group}")
        
        api_data = await get_shutdowns_data(city, street, house, cached_group)
        current_hash = _get_schedule_hash_compact(api_data) # ИСПРАВЛЕНО: используем новую функцию
        group = api_data.get('group', None)  # Extract group from API response
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, city, street, house, last_hash, group_name) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, city, street, house, current_hash, group)
        )
        await db_conn.commit()
        
        cursor = await db_conn.execute("SELECT 1 FROM subscriptions WHERE user_id = ?", (user_id,))
        is_subscribed = bool(await cursor.fetchone())
        
        # --- Вызов функции-отправщика с графиком ---
        await send_schedule_response(message, api_data, is_subscribed)
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        
    except (ValueError, ConnectionError) as e:
        error_type = "Помилка вводу/помилка API" if isinstance(e, ValueError) else "Помилка"
        error_message = f"❌ **{error_type}:** {e}"
        error_message += "\n*Попередній успішний запит (якщо він був) збережено. Ви можете його повторити командою `/repeat`.*"
        await message.answer(error_message)
    except Exception as e:
        logger.error(f"Critical error during check command for user {user_id}: {e}", exc_info=True)
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")

# --- ОБРАБОТЧИК /repeat ---
@dp.message(Command("repeat"))
async def command_repeat_handler(message: types.Message, state: FSMContext) -> None:
    global db_conn
    user_id = message.from_user.id
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await _handle_captcha_check(message, state)
        return

    city, street, house = None, None, None
    try:
        cursor = await db_conn.execute("SELECT city, street, house FROM user_last_check WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("❌ **Помилка.** Спочатку вам потрібно перевірити графік за допомогою команди `/check Місто, Вулиця, Будинок`.")
            return
        city, street, house = row
    except Exception as e:
        logger.error(f"Failed to fetch last_check from DB for user {user_id}: {e}")
        await message.answer("❌ **Помилка БД** при спробі знайти ваш останній запит.")
        return

    address_str = f"`{city}, {street}, {house}`"
    await message.answer(f"🔄 **Повторюю перевірку** для адреси:\n{address_str}\n⏳ Очікуйте...")
    
    try:
        # Try to get cached group from database
        cached_group = None
        cursor = await db_conn.execute(
            "SELECT group_name FROM user_last_check WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        if row and row[0]:
            cached_group = row[0]
            logger.info(f"Using cached group for /repeat: {cached_group}")
        
        data = await get_shutdowns_data(city, street, house, cached_group)
        current_hash = _get_schedule_hash_compact(data) # ИСПРАВЛЕНО: используем новую функцию
        await db_conn.execute(
            "UPDATE user_last_check SET last_hash = ? WHERE user_id = ?", 
            (current_hash, user_id)
        )
        await db_conn.commit()
        
        cursor = await db_conn.execute("SELECT 1 FROM subscriptions WHERE user_id = ?", (user_id,))
        is_subscribed = bool(await cursor.fetchone())
        
        # --- Вызов функции-отправщика с графиком ---
        await send_schedule_response(message, data, is_subscribed)
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    except (ValueError, ConnectionError) as e:
        error_type = "Помилка вводу/помилка API" if isinstance(e, ValueError) else "Помилка"
        await message.answer(f"❌ **{error_type}:** {e}")
    except Exception as e:
        logger.error(f"Critical error during repeat check for user {message.from_user.id}: {e}", exc_info=True)
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")

# --- ОБРАБОТЧИК /subscribe ---
@dp.message(Command("subscribe"))
async def command_subscribe_handler(message: types.Message, state: FSMContext) -> None:
    global db_conn
    user_id = message.from_user.id
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await _handle_captcha_check(message, state)
        return

    city, street, house, hash_from_check = None, None, None, None
    try:
        cursor = await db_conn.execute("SELECT city, street, house, last_hash FROM user_last_check WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("❌ **Помилка.** Спочатку вам потрібно перевірити графік за допомогою команди `/check Місто, Вулиця, Будинок`.")
            return
        city, street, house, hash_from_check = row
    except Exception as e:
        logger.error(f"Failed to fetch last_check from DB for user {user_id}: {e}")
        await message.answer("❌ **Помилка БД** при спробі знайти ваш останній запит.")
        return

    text_args = message.text.replace('/subscribe', '', 1).strip()
    interval_hours = DEFAULT_INTERVAL_HOURS
    if text_args:
        try:
            val = float(text_args.replace(',', '.'))
            if val <= 0.0:
                await message.answer("❌ **Помилка.** Інтервал має бути позитивним числом годин.")
                return
            if val < 0.5:
                await message.answer("❌ **Помилка.** Мінімальний інтервал перевірки — 0.5 години (30 хвилин).")
                return
            interval_hours = val
        except ValueError:
            await message.answer("❌ **Помилка.** Інтервал повинен бути числом (наприклад, `/subscribe 3` або `/subscribe 0.5`).")
            return

    hours_str = f'{interval_hours:g}'.replace('.', ',')
    interval_display = f"{hours_str} {_get_hours_str(interval_hours)}"

    hash_to_use = hash_from_check
    try:
        cursor = await db_conn.execute(
            "SELECT last_schedule_hash, interval_hours FROM subscriptions WHERE user_id = ? AND city = ? AND street = ? AND house = ?", 
            (user_id, city, street, house)
        )
        sub_row = await cursor.fetchone()
        if sub_row:
            hash_to_use = sub_row[0]
            if sub_row[1] == interval_hours:
                await message.answer(f"✅ Ви вже підписані на оновлення для адреси: `{city}, {street}, {house}` з інтервалом **{interval_display}**.")
                return

        if hash_to_use is None:
            hash_to_use = "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION"

        next_check_time = datetime.now()
        
        # --- ИЗМЕНЕНИЕ: Объединение логики /alert и /subscribe ---
        # Проверяем текущее значение notification_lead_time
        current_lead_time = 0
        cursor = await db_conn.execute("SELECT notification_lead_time FROM subscriptions WHERE user_id = ?", (user_id,))
        row_alert = await cursor.fetchone()
        if row_alert:
            current_lead_time = row_alert[0] if row_alert[0] is not None else 0
        
        # Если алерты выключены (0), включаем их по умолчанию (15 мин)
        # Если пользователь уже настроил (например, 30 мин), оставляем как есть
        new_lead_time = current_lead_time
        if current_lead_time == 0:
            new_lead_time = 15

        # Extract group from last check or API data
        cursor_group = await db_conn.execute(
            "SELECT group_name FROM user_last_check WHERE user_id = ?",
            (user_id,)
        )
        row_group = await cursor_group.fetchone()
        group = row_group[0] if row_group and row_group[0] else None
        
        await db_conn.execute(
            "INSERT OR REPLACE INTO subscriptions (user_id, city, street, house, interval_hours, next_check, last_schedule_hash, notification_lead_time, group_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, city, street, house, interval_hours, next_check_time, hash_to_use, new_lead_time, group)
        )
        await db_conn.commit()
        
        alert_msg = ""
        if new_lead_time > 0:
            alert_msg = f"\n🔔 Сповіщення за **{new_lead_time} хв.** до події також увімкнено."
            if current_lead_time == 0:
                 alert_msg += " (Ви можете змінити це командою `/alert`)"

        logger.info(f"User {user_id} subscribed/updated to {city}, {street}, {house} with interval {interval_hours}h. Next check now. Alert: {new_lead_time}m")
        await message.answer(
            f"✅ **Підписка оформлена!**\n"
            f"Ви будете отримувати оновлення для адреси: `{city}, {street}, {house}` з інтервалом **{interval_display}**."
            f"{alert_msg}"
        )
    except Exception as e:
        logger.error(f"Failed to write subscription to DB for user {user_id}: {e}", exc_info=True)
        await message.answer("❌ **Помилка БД** при спробі зберегти підписку.")

# --- 4.5. Команда /alert ---
@dp.message(Command("alert"))
async def cmd_alert(message: types.Message):
    """
    Встановлює час попередження перед відключенням/включенням (у хвилинах).
    Використання: /alert 15
    """
    user_id = message.from_user.id
    args = message.text.split()

    if len(args) != 2:
        await message.answer(
            "⚠️ **Використання:** `/alert <хвилини>`\n"
            "Наприклад: `/alert 15` - щоб отримувати сповіщення за 15 хвилин до події.\n"
            "Введіть `0`, щоб вимкнути сповіщення."
        )
        return

    try:
        minutes = int(args[1])
        if minutes < 0 or minutes > 120:
            await message.answer("⚠️ Будь ласка, вкажіть час від 0 до 120 хвилин.")
            return
    except ValueError:
        await message.answer("⚠️ Будь ласка, вкажіть число (кількість хвилин).")
        return

    global db_conn
    if db_conn is None:
        await message.answer("❌ Помилка бази даних.")
        return

    try:
        # Проверяем, есть ли подписка
        cursor = await db_conn.execute("SELECT 1 FROM subscriptions WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("❌ Ви ще не підписані на оновлення. Спочатку використайте `/subscribe`.")
            return

        await db_conn.execute(
            "UPDATE subscriptions SET notification_lead_time = ? WHERE user_id = ?",
            (minutes, user_id)
        )
        await db_conn.commit()

        if minutes == 0:
            await message.answer("🔕 Сповіщення про наближення подій вимкнено.")
        else:
            await message.answer(f"🔔 Сповіщення встановлено! Ви отримаєте повідомлення за **{minutes} хв.** до зміни статусу світла.")

    except Exception as e:
        logger.error(f"Error setting alert for user {user_id}: {e}")
        await message.answer("❌ Сталася помилка при збереженні налаштувань.")

# --- ОБРАБОТЧИК /unsubscribe ---
@dp.message(Command("unsubscribe"))
async def command_unsubscribe_handler(message: types.Message) -> None:
    global db_conn
    user_id = message.from_user.id
    try:
        cursor = await db_conn.execute("SELECT city, street, house FROM subscriptions WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("❌ **Помилка.** Ви не підписані на оновлення.")
            return
        city, street, house = row
        await db_conn.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        await db_conn.commit()
        logger.info(f"User {user_id} unsubscribed from {city}, {street}, {house}.")
        await message.answer(
            f"🚫 **Підписку скасовано.** Ви більше не будете отримувати автоматичні оновлення для адреси: `{city}, {street}, {house}`.\n"
            "Ви можете підписатися знову, скориставшися командою `/subscribe` після перевірки графіку."
        )
    except Exception as e:
        logger.error(f"Failed to delete subscription from DB for user {user_id}: {e}", exc_info=True)
        await message.answer("❌ **Помилка БД** при спробі скасувати підписку.")

# --- Фоновая задача для уведомлений о предстоящих событиях ---
async def _process_alert_for_user(bot: Bot, user_id: int, city: str, street: str, house: str, lead_time: int, last_alert_event_start_str: str, now: datetime):
    """
    Обрабатывает логику проверки и отправки алертов для одного пользователя.
    Возвращает True, если алерт был отправлен (нужно обновить БД).
    """
    address_key = (city, street, house)
    
    # Берем данные из нового кеша
    data = SCHEDULE_DATA_CACHE.get(address_key)
    if not data:
        return None
    
    schedule = data.get("schedule", {})
    if not schedule:
        return None

    kiev_tz = pytz.timezone('Europe/Kiev')
    
    # Логика поиска ближайшего события
    events = [] # (time, type) type: 'off_start' or 'off_end'
    
    # Сортируем даты
    sorted_dates = sorted(schedule.keys())
    
    for date_str in sorted_dates:
        try:
            date_obj = datetime.strptime(date_str, '%d.%m.%y').date()
        except ValueError:
            continue
            
        # Пропускаем прошедшие дни
        if date_obj < now.date():
            continue
            
        slots = schedule.get(date_str, [])
        for slot in slots:
            time_str = slot.get('shutdown', '00:00–00:00')
            start_min, end_min = parse_time_range(time_str)
            
            start_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=start_min)
            end_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=end_min)
            
            events.append((start_dt, 'off_start'))
            events.append((end_dt, 'off_end'))
    
    events.sort(key=lambda x: x[0])
    
    # Ищем ближайшее событие в будущем
    target_event = None
    for event_dt, event_type in events:
        if event_dt > now:
            target_event = (event_dt, event_type)
            break
    
    if not target_event:
        return None
        
    event_dt, event_type = target_event
    time_to_event = (event_dt - now).total_seconds() / 60.0 # минуты
    
    # Проверяем, пора ли слать алерт
    if 0 < time_to_event <= lead_time:
        event_dt_str = event_dt.isoformat()
        
        if last_alert_event_start_str != event_dt_str:
            # Шлем алерт!
            msg_type = "відключення" if event_type == 'off_start' else "включення"
            time_str = event_dt.strftime('%H:%M')
            minutes_left = int(time_to_event)
            
            msg = f"⚠️ **Увага!** Через {minutes_left} хв. у {time_str} очікується **{msg_type}** світла."
            
            try:
                await bot.send_message(user_id, msg, parse_mode="Markdown")
                return event_dt_str # Возвращаем время события для обновления БД
            except Exception as e:
                logger.error(f"Failed to send alert to {user_id}: {e}")
                return None
    return None

async def alert_checker_task(bot: Bot):
    global db_conn
    logger.info("Alert checker started.")
    while True:
        await asyncio.sleep(60)
        if db_conn is None: continue

        kiev_tz = pytz.timezone('Europe/Kiev')
        now = datetime.now(kiev_tz)

        try:
            cursor = await db_conn.execute(
                "SELECT user_id, city, street, house, notification_lead_time, last_alert_event_start FROM subscriptions WHERE notification_lead_time > 0"
            )
            rows = await cursor.fetchall()
            
            for row in rows:
                user_id, city, street, house, lead_time, last_alert_event_start_str = row
                
                new_last_alert = await _process_alert_for_user(
                    bot, user_id, city, street, house, lead_time, last_alert_event_start_str, now
                )
                
                if new_last_alert:
                    await db_conn.execute(
                        "UPDATE subscriptions SET last_alert_event_start = ? WHERE user_id = ?",
                        (new_last_alert, user_id)
                    )
                    await db_conn.commit()

        except Exception as e:
            logger.error(f"Error in alert_checker_task loop: {e}", exc_info=True)

# --- 5. Запуск Бота ---
async def set_default_commands(bot: Bot):
    """Устанавливает список команд в меню Telegram."""
    commands = [
        BotCommand(command="start", description="Почати роботу"),
        BotCommand(command="help", description="Показати довідку/команди"),
        BotCommand(command="check", description="Перевірити графік відключень"),
        BotCommand(command="repeat", description="Повторити останню перевірку"),
        BotCommand(command="subscribe", description="Підписатися на оновлення"),
        BotCommand(command="unsubscribe", description="Скасувати підписку"),
        BotCommand(command="alert", description="Налаштувати сповіщення"),
        BotCommand(command="cancel", description="Скасувати поточну дію")
    ]
    logger.info("Setting default commands...")
    try:
        await bot.set_my_commands(commands)
        logger.info("Default commands set successfully.")
    except Exception as e:
        logger.error(f"Failed to set default commands: {e}")

async def main():
    global db_conn 
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Exiting.")
        return

    default_properties = DefaultBotProperties(
        parse_mode="Markdown"
    )
    bot = Bot(token=BOT_TOKEN, default=default_properties)

    try:
        db_conn = await init_db(DB_PATH)
    except Exception as e:
        logger.error(f"Failed to initialize database at {DB_PATH}: {e}", exc_info=True)
        return

    await set_default_commands(bot)

    # КРИТИЧЕСКИ ВАЖНО: /cancel регистрируется ПЕРВЫМ
    dp.message.register(command_cancel_handler, Command("cancel"))
    
    # Затем регистрируем остальные команды
    dp.message.register(command_start_handler, Command("start", "help"))
    dp.message.register(command_check_handler, Command("check")) 
    dp.message.register(command_repeat_handler, Command("repeat"))
    dp.message.register(command_subscribe_handler, Command("subscribe")) 
    dp.message.register(command_unsubscribe_handler, Command("unsubscribe"))
    dp.message.register(cmd_alert, Command("alert"))

    checker_task = asyncio.create_task(subscription_checker_task(bot))
    alert_task = asyncio.create_task(alert_checker_task(bot)) # Add alert_task here

    logger.info("Бот запущено. Початок опитування...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Зупинка бота. Скасування фонових завдань...")
        checker_task.cancel()
        alert_task.cancel() # Ensure alert task is also cancelled
        if db_conn:
            await db_conn.close()
            logger.info("Database connection closed.")
        await bot.session.close()
        logger.info("Bot session closed.")

if __name__ == "__main__":
    logger.setLevel(logging.DEBUG) 
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")