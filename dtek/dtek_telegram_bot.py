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

# --- НОВІ ІМПОРТИ ДЛЯ ГРАФІКІВ (PIL) ---
import io
import math
import pytz 
from PIL import Image, ImageDraw, ImageFont
# ----------------------------------

# --- 1. Конфігурація ---
BOT_TOKEN = os.getenv("DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN")
API_BASE_URL = os.getenv("API_BASE_URL", "http://dtek_api:8000") 
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")
# --- Додано шлях до шрифту (універсальний, відносно папки зі скриптом) ---
FONT_PATH = os.getenv("FONT_PATH", os.path.join(os.path.dirname(__file__), "resources", "DejaVuSans.ttf")) 

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    'dtek_bot | %(levelname)s:%(name)s:%(message)s', 
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
        last_schedule_hash TEXT
    )
    """)
    
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS user_last_check (
        user_id INTEGER PRIMARY KEY,
        city TEXT NOT NULL,
        street TEXT NOT NULL,
        house TEXT NOT NULL,
        last_hash TEXT
    )
    """)
    await conn.commit()
    logger.info(f"Database initialized and connected at {db_path}")
    return conn

# --- 2. Вспомогательные функции ---
def format_minutes_to_hh_m(minutes: int) -> str:
    """Форматирует общее количество минут в HH:MM."""
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"

# --- ИЗМЕНЕНИЕ: Тип возвращаемого значения изменен на Tuple[str, str, str] (emoji, header, body) ---
def _process_single_day_schedule(date: str, slots: List[Dict[str, Any]]) -> Tuple[str, str, str]:
    """
    Консолидирует слоты отключений в ГРУППЫ и возвращает кортеж (emoji, header, body).
    header - строка для шапки дня (дата | статус)
    body - строка с таблицей слотов или пустая строка
    """
    outage_slots = [s for s in slots if s.get('disconection') in ('full', 'half')]
    
    # 1. Сценарий: Нет отключений
    if not outage_slots:
        header = f"{date} | 🟢 Не заплановані"
        return "🟢", header, "" 

    groups = []
    current_group = None
    total_duration_hours = 0.0 # Новая переменная для общего времени
    
    for slot in outage_slots:
        try:
            time_parts = re.split(r'\s*[-\bi\–]\s*', slot.get('time', '0-0'))
            start_hour = int(time_parts[0])
            end_hour = int(time_parts[1])
            if end_hour == 0:
                end_hour = 24
            slot_duration = 0.0
            slot_start_min = 0
            slot_end_min = 0
            disconection = slot.get('disconection')
            
            if disconection == 'full':
                slot_duration = end_hour - start_hour # Длительность в часах
                slot_start_min = start_hour * 60
                slot_end_min = end_hour * 60
            elif disconection == 'half':
                slot_duration = 0.5 
                # Если 02-03 (time), то отключение 0.5 год. (02:30-03:00).
                # Начало всегда в .30, конец всегда в .00
                slot_start_min = start_hour * 60 + 30
                slot_end_min = end_hour * 60

            total_duration_hours += slot_duration # Суммируем общую длительность
            
            # Логика объединения слотов
            if current_group is None:
                current_group = {
                    "start_min": slot_start_min,
                    "end_min": slot_end_min,
                    "duration_hours": slot_duration 
                }
            elif slot_start_min == current_group["end_min"]: 
                current_group["end_min"] = slot_end_min
                current_group["duration_hours"] += slot_duration
            else:
                groups.append(current_group)
                current_group = {
                    "start_min": slot_start_min,
                    "end_min": slot_end_min,
                    "duration_hours": slot_duration
                }
        except Exception as e:
            logger.error(f"Error processing slot {slot}: {e}")
            continue

    if current_group:
        groups.append(current_group)

    if not groups:
         header = f"{date} | ❌ Помилка парсингу слотів"
         return "❌", header, ""
    
    # 2. Сценарий: Есть отключения
    output_parts = []
    
    # 2.1. Формуємо рядки слотів (Body)
    max_len_left_col = 0
    temp_groups_formatted = []
    
    for group in groups:
        start_time_final = format_minutes_to_hh_m(group["start_min"])
        end_time_final = format_minutes_to_hh_m(group["end_min"])
        duration_str = _get_shutdown_duration_str_by_hours(group["duration_hours"])
        
        left_col = f"{start_time_final} - {end_time_final}"
        right_col = f"{duration_str}"
        
        if len(left_col) > max_len_left_col:
            max_len_left_col = len(left_col)
            
        temp_groups_formatted.append((left_col, right_col))
    
    # Тепер формируємо body з урахуванням вирівнювання
    for left_col, right_col in temp_groups_formatted:
        # Додаємо padding для вирівнювання в pre-форматі
        padded_left_col = left_col.ljust(max_len_left_col)
        output_parts.append(f"{padded_left_col} | {right_col}")
        
    body = "\n".join(output_parts)

    # 2.2. Формуємо шапку (Header)
    total_duration_str = _get_shutdown_duration_str_by_hours(total_duration_hours)
    
    # Формат шапки: [Дата] | 🔴 Відключення: [X год.]
    # (Використовуємо Відключення: X год. для загальної інформації)
    # Зображення маємо: "14.11.2025 | 🔴 Відключення: 10,5 год."
    header = f"{date} | 🔴 Відключення: {total_duration_str}"
    
    # Повертаємо кортеж з прапором, шапкою і тілом
    return "🔴", header, body
    # --- КІНЕЦЬ ЗМІНИ ---

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

def _pluralize_hours(value: float) -> str:
    """Определяет правильную форму слова 'година' для украинского языка."""
    # --- ЗМІНА: Завжди повертаємо 'год.' згідно зі скріншотом ---
    return "год."
    # --- КІНЕЦЬ ЗМІНИ ---

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
        plural_form = _pluralize_hours(duration_hours)
        return f"{hours_str} {plural_form}"
    except Exception:
        return "?"

def _get_schedule_hash(data: dict) -> str:
    """Генерирует хеш только из данных графика (schedule) для сравнения изменений."""
    schedule = data.get("schedule", {})
    if not schedule:
        return "NO_SCHEDULE_FOUND"

    schedule_parts = []
    try:
        sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
    except ValueError:
        sorted_dates = sorted(schedule.keys())

    for date in sorted_dates:
        slots = schedule[date]
        # ЗМІНА: Використовуємо тільки header (без body) для хешу
        _, result_header, _ = _process_single_day_schedule(date, slots) 
        schedule_parts.append(f"{date}:{result_header}")

    schedule_string = "|".join(schedule_parts)
    return hashlib.sha256(schedule_string.encode('utf-8')).hexdigest()

# --- НОВАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ОТПРАВКИ ОТВЕТА ---
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
        header = (
            f"🏠 Адреса: `{city}, {street}, {house}`\n"
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

        # 3. Цикл по дням (Только текст)
        all_slots_48h = {}
        for idx, date in enumerate(sorted_dates):
            slots = schedule.get(date, [])
            
            # ЗМІНА: Виклик нової функції
            emoji, header_line, body_lines = _process_single_day_schedule(date, slots)
            
            # --- ИЗМЕНЕНИЕ: Форматирование ответа согласно требованиям пользователя ---
            # 1. Шапка (дата и общее время) всегда вне блока ```
            # Используем жирный шрифт для выделения
            await message.answer(f"**{header_line}**")
            
            # 2. Тело (список слотов) только если есть отключения, и ТОЛЬКО оно в блоке ```
            if emoji == "🔴":
                body_block = f"```\n{body_lines}\n```"
                await message.answer(body_block)
            elif emoji == "🟢" or emoji == "❌":
                # Если "зеленый" или "ошибка парсинга", то тело не отправляем, 
                # т.к. вся информация уже есть в жирной шапке.
                pass
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

            # Собираем слоты для 48-часового графика, но только для первых двух дней
            if idx < 2:
                all_slots_48h[date] = slots
        
        # 4. Генерируем и отправляем общий 48-часовой график (если есть данные хотя бы за 1 день)
        if all_slots_48h:
            image_data = _generate_48h_schedule_image(all_slots_48h)
            
            if image_data:
                await message.answer("⏰ **Загальний графік на 48 годин**:")
                image_file = BufferedInputFile(image_data, filename="schedule_48h.png")
                await message.answer_photo(photo=image_file)

        # 5. Отправляем "подвал" (приглашение к подписке)
        if not is_subscribed:
            await message.answer("💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`.")
    
    except Exception as e:
        logger.error(f"Error in send_schedule_response for user {message.from_user.id}: {e}", exc_info=True)
        await message.answer("❌ Сталася помилка під час формування відповіді.")

# ---------------------------------------------------------

def _generate_48h_schedule_image(days_slots: Dict[str, List[Dict[str, Any]]]) -> bytes:
    """
    Генерирует 48-часовое изображение графика (clock-face) на основе слотов, используя Pillow.
    Принимает словарь {дата: [слоты]}. Использует до двух дней.
    Слоты второго дня сдвигаются на 24 часа.
    """
    global FONT_PATH
    
    if not days_slots:
        return None

    try:
        # 1. Сортировка дат и объединение слотов в 48-часовом пространстве
        try:
            sorted_dates = sorted(days_slots.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
        except ValueError:
            sorted_dates = sorted(days_slots.keys())
        
        total_outage_groups = []
        minutes_in_day = 24 * 60
        
        for idx, date in enumerate(sorted_dates[:2]): # Берем только 2 дня
            day_slots = days_slots[date]
            day_offset_minutes = idx * minutes_in_day # 0 для первого дня, 1440 для второго
            
            outage_slots = [s for s in day_slots if s.get('disconection') in ('full', 'half')]
            
            groups = []
            current_group = None
            for slot in outage_slots:
                try:
                    time_parts = re.split(r'\s*[-\bi\–]\s*', slot.get('time', '0-0'))
                    start_hour_raw = int(time_parts[0])
                    end_hour_raw = int(time_parts[1])
                    
                    if end_hour_raw == 0:
                        end_hour_raw = 24
                    
                    slot_start_min = 0
                    slot_end_min = 0
                    disconection = slot.get('disconection')
                    
                    if disconection == 'full':
                        slot_start_min = start_hour_raw * 60
                        slot_end_min = end_hour_raw * 60
                    elif disconection == 'half':
                        # Включение/отключение на полчаса
                        slot_start_min = start_hour_raw * 60 + (30 if start_hour_raw != end_hour_raw else 0)
                        slot_end_min = end_hour_raw * 60
                        
                    # Сдвиг на 24 часа для второго дня
                    slot_start_min += day_offset_minutes
                    slot_end_min += day_offset_minutes

                    if current_group is None:
                        current_group = {"start_min": slot_start_min, "end_min": slot_end_min}
                    elif slot_start_min == current_group["end_min"]: 
                        current_group["end_min"] = slot_end_min
                    else:
                        groups.append(current_group)
                        current_group = {"start_min": slot_start_min, "end_min": slot_end_min}
                except Exception:
                    continue # Пропускаем битый слот

            if current_group:
                groups.append(current_group)
            
            total_outage_groups.extend(groups)

        if not total_outage_groups:
            return None # Нет отключений - нет картинки

        # --- НОВЫЙ НАБОР: Часы, которые нужно показать ---
        # ИЗМЕНЕНИЕ: Логика перенесена ПОСЛЕ формирования total_outage_groups
        hours_to_display = {0, 24, 48} # Всегда показываем 0, 24, 48

        for group in total_outage_groups:
            start_min_48h = group['start_min']
            end_min_48h = group['end_min']

            # Конвертируем минуты в 48-часовом пространстве в часы
            # Начальный час: округляем ВНИЗ (e.g., 09:30 -> 9)
            start_hour_48h = math.floor(start_min_48h / 60)
            # Конечный час: округляем ВВЕРХ (e.g., 16:30 -> 17)
            end_hour_48h = math.ceil(end_min_48h / 60)

            hours_to_display.add(start_hour_48h)
            hours_to_display.add(end_hour_48h)

        # 2. Настройка рисования (Pillow)
        # --- Размер, отступы и центр ---
        size = 300
        padding = 30
        center = (size // 2, size // 2)
        radius = (size // 2) - padding
        bbox = [padding, padding, size - padding, size - padding] # Bounding box
        
        image = Image.new('RGB', (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        # 48 часов = 2880 минут. 360 / 2880 = 0.125 градуса на минуту
        deg_per_minute = 360.0 / 2880.0 
        deg_per_hour = 360.0 / 48.0 # 7.5 градуса на час

        # 3. Загрузка шрифта
        font_size = 14 
        font = None
        try:
            font = ImageFont.truetype(FONT_PATH, font_size)
        except IOError:
            logger.warning(f"Specified font at FONT_PATH ('{FONT_PATH}') not found. Using default PIL font.")
            font = ImageFont.load_default()

        # 4. Рисуем большое кольцо (заливка зеленая, БЕЗ обводки - обводку добавим в конце)
        draw.ellipse(bbox, fill='#00ff00', outline=None) 

        # 5. Рисуем красные секторы (отключения) БЕЗ обводки
        for group in total_outage_groups:
            start_min = group['start_min']
            end_min = group['end_min']
            
            # ИЗМЕНЕНИЕ: Смещение на 180 градусов (поворот на 90 CCW)
            start_angle = (start_min * deg_per_minute) + 180
            end_angle = (end_min * deg_per_minute) + 180
            
            if abs(start_angle - end_angle) < 0.1:
                end_angle += 360.0
            
            # Рисуем красный сектор ПОВЕРХ зеленого, БЕЗ обводки
            draw.pieslice(bbox, start_angle, end_angle, fill="#ff3300", outline=None)
        
        # 6. Рисуем черные разделительные линии между секторами
        for group in total_outage_groups:
            start_min = group['start_min']
            end_min = group['end_min']
            
            # Линия в начале красного сектора
            start_angle_deg = (start_min * deg_per_minute) + 180
            start_angle_rad = math.radians(start_angle_deg)
            x_start = center[0] + radius * math.cos(start_angle_rad)
            y_start = center[1] + radius * math.sin(start_angle_rad)
            draw.line([center, (x_start, y_start)], fill="#000000", width=1)
            
            # Линия в конце красного сектора
            end_angle_deg = (end_min * deg_per_minute) + 180
            end_angle_rad = math.radians(end_angle_deg)
            x_end = center[0] + radius * math.cos(end_angle_rad)
            y_end = center[1] + radius * math.sin(end_angle_rad)
            draw.line([center, (x_end, y_end)], fill="#000000", width=1)
        
        # 7. Рисуем центральную горизонтальную линию (от 0 до 24)
        # Линия слева (0 часов) - угол 180°
        angle_0_rad = math.radians(180)
        x_0 = center[0] + radius * math.cos(angle_0_rad)
        y_0 = center[1] + radius * math.sin(angle_0_rad)
        draw.line([center, (x_0, y_0)], fill="#000000", width=1)
        
        # Линия справа (24 часа) - угол 0° (или 360°)
        angle_24_rad = math.radians(0)
        x_24 = center[0] + radius * math.cos(angle_24_rad)
        y_24 = center[1] + radius * math.sin(angle_24_rad)
        draw.line([center, (x_24, y_24)], fill="#000000", width=1)

        # 8. Рисуем часовую стрелку (текущее время) с учетом Киевского времени
        kiev_tz = pytz.timezone('Europe/Kiev')
        now = datetime.now(kiev_tz) # Берем текущее время в Киевском часовом поясе
        
        # Нам нужно 24-часовое время первого дня (0-24h)
        current_minutes = now.hour * 60 + now.minute
        
        # ИЗМЕНЕНИЕ: Смещение на 180 градусов (поворот на 90 CCW)
        angle_deg = (current_minutes * deg_per_minute) + 180
        angle_rad = math.radians(angle_deg)
        
        # Параметры стрелки (толстая и заметная)
        hand_length = radius - 2
        hand_width = 2
        arrowhead_size = 12
        
        # Координаты конца стрелки
        x_end = center[0] + hand_length * math.cos(angle_rad)
        y_end = center[1] + hand_length * math.sin(angle_rad)
        
        # --- ИЗМЕНЕНИЕ: Добавление тени (рисуем сначала смещенную серую копию) ---
        SHADOW_COLOR = "#888888" # Цвет тени
        SHADOW_OFFSET = 2 # Смещение тени
        
        # 8.0. Рисуем тень (основная линия)
        draw.line(
            [(center[0] + SHADOW_OFFSET, center[1] + SHADOW_OFFSET), (x_end + SHADOW_OFFSET, y_end + SHADOW_OFFSET)], 
            fill=SHADOW_COLOR, 
            width=hand_width
        )
        
        # 8.0. Рисуем тень (наконечник)
        perp_angle_rad = angle_rad + math.pi / 2 # (Расчет perp_angle_rad нужен до 8.1)
        
        base_x_shadow = x_end - (arrowhead_size * 0.8) * math.cos(angle_rad) + SHADOW_OFFSET
        base_y_shadow = y_end - (arrowhead_size * 0.8) * math.sin(angle_rad) + SHADOW_OFFSET
        
        x2_shadow = base_x_shadow + (arrowhead_size / 2) * math.cos(perp_angle_rad)
        y2_shadow = base_y_shadow + (arrowhead_size / 2) * math.sin(perp_angle_rad)
        
        x3_shadow = base_x_shadow - (arrowhead_size / 2) * math.cos(perp_angle_rad)
        y3_shadow = base_y_shadow - (arrowhead_size / 2) * math.sin(perp_angle_rad)
        
        draw.polygon(
            [(x_end + SHADOW_OFFSET, y_end + SHADOW_OFFSET), (x2_shadow, y2_shadow), (x3_shadow, y3_shadow)], 
            fill=SHADOW_COLOR
        )
        # --- Конец добавления тени ---
        
        # 8.1 Рисуем основную линию стрелки 
        # ИЗМЕНЕНИЕ: Сделали стрелку БЕЛОЙ
        HAND_COLOR = "#FFFFFF" 
        draw.line([center, (x_end, y_end)], fill=HAND_COLOR, width=hand_width) 
        
        # 8.2 Рисуем наконечник стрелки
        # perp_angle_rad = angle_rad + math.pi / 2 # (Уже рассчитан выше)
        
        base_x = x_end - (arrowhead_size * 0.8) * math.cos(angle_rad) 
        base_y = y_end - (arrowhead_size * 0.8) * math.sin(angle_rad)
        
        x2 = base_x + (arrowhead_size / 2) * math.cos(perp_angle_rad)
        y2 = base_y + (arrowhead_size / 2) * math.sin(perp_angle_rad)
        
        x3 = base_x - (arrowhead_size / 2) * math.cos(perp_angle_rad)
        y3 = base_y - (arrowhead_size / 2) * math.sin(perp_angle_rad)
        
        draw.polygon([(x_end, y_end), (x2, y2), (x3, y3)], fill=HAND_COLOR)

        # 8.3. Рисуємо білий круг в центрі (50% від радіусу)
        inner_radius = int(radius * 0.50)
        inner_bbox = [
            center[0] - inner_radius,
            center[1] - inner_radius,
            center[0] + inner_radius,
            center[1] + inner_radius
        ]
        # Центральный круг остается БЕЛЫМ
        draw.ellipse(inner_bbox, fill='#FFFFFF', outline='#000000', width=1)
        
        # 8.4. Рисуємо ГОРИЗОНТАЛЬНУ чорну лінію посередині білого круга
        draw.line(
            [(center[0] - inner_radius, center[1]), (center[0] + inner_radius, center[1])],
            fill='#000000',
            width=1
        )
        
        # 8.5. Додаємо дати у центральний круг
        try:
            # Отримуємо дати з days_slots (перші 2 дні)
            dates_list = list(days_slots.keys())[:2]
            
            # Використовуємо той самий шрифт, що і для міток годин
            date_font = font
            
            if len(dates_list) >= 1:
                # Перша дата (СЕГОДНЯ) - ВЕРХНЯЯ половина
                date1 = dates_list[0]
                # Позиція для першої дати (вверху, близко к центру)
                date1_x = center[0]
                date1_y = center[1] - inner_radius // 4 # Ближе к центру
                
                temp_img = Image.new('RGBA', (100, 100), (255, 255, 255, 0))
                temp_draw = ImageDraw.Draw(temp_img)
                temp_draw.text((50, 50), date1, fill='#000000', font=date_font, anchor="mm")
                rotated1 = temp_img
                
                bbox1 = rotated1.getbbox()
                if bbox1:
                    cropped1 = rotated1.crop(bbox1)
                    paste_x1 = int(date1_x - cropped1.width // 2)
                    paste_y1 = int(date1_y - cropped1.height // 2)
                    image.paste(cropped1, (paste_x1, paste_y1), cropped1)
            
            if len(dates_list) >= 2:
                # Друга дата (ЗАВТРА) - НИЖНЯЯ половина
                date2 = dates_list[1]
                date2_x = center[0]
                date2_y = center[1] + inner_radius // 4 # Ближе к центру
                
                temp_img2 = Image.new('RGBA', (100, 100), (255, 255, 255, 0))
                temp_draw2 = ImageDraw.Draw(temp_img2)
                temp_draw2.text((50, 50), date2, fill='#000000', font=date_font, anchor="mm")
                # ИЗМЕНЕНИЕ: Поворот на 180 градусов
                rotated2 = temp_img2.rotate(180, expand=True) 

                bbox2 = rotated2.getbbox()
                if bbox2:
                    cropped2 = rotated2.crop(bbox2)
                    paste_x2 = int(date2_x - cropped2.width // 2)
                    paste_y2 = int(date2_y - cropped2.height // 2)
                    image.paste(cropped2, (paste_x2, paste_y2), cropped2)

        except Exception as e:
            logger.error(f"Failed to add dates to center circle: {e}")

        # 9. Рисуем ТОЛЬКО граничные метки часов (начало/конец отключений и 0/24)
        label_radius = radius + (padding * 0.4) # Отодвигаем метки наружу

        for h_total in range(49): # До 48 включительно
            if h_total not in hours_to_display:
                continue # Пропускаем все, кроме нужных

            # ИЗМЕНЕНИЕ: Специальная обработка для часа 24 (справа)
            if h_total == 24:
                text_to_display = "24"
            else:
                text_to_display = str(h_total % 24)
            
            # ИЗМЕНЕНИЕ: Смещение на 180 градусов (поворот на 90 CCW)
            angle_deg = (h_total * deg_per_hour) + 180
            angle_rad_label = math.radians(angle_deg) 
            
            x = center[0] + label_radius * math.cos(angle_rad_label)
            y = center[1] + label_radius * math.sin(angle_rad_label)
            
            label_color = "black" 

            try:
                # anchor="mm" - центрирует текст
                draw.text((x, y), text_to_display, fill=label_color, font=font, anchor="mm")
            except Exception:
                # Резервный вариант, если anchor не поддерживается (старые PIL/Pillow)
                text_width, text_height = draw.textsize(text_to_display, font=font)
                draw.text((x - text_width / 2, y - text_height / 2), text_to_display, fill=label_color, font=font)

        # 10. Сохранение в байты
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        buf.seek(0)
        return buf.getvalue()

    except Exception as e:
        logger.error(f"Failed to generate 48h schedule image with PIL: {e}", exc_info=True)
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
        "🚨 **Увага! Для захисту від ботів, пройдіть просту перевірку.**\n"
        f"**{question}**\n"
        "Введіть лише число-відповідь."
    )
    return False

# --- 3. Интеграция с API ---
async def _fetch_shutdowns_data_from_api(city: str, street: str, house: str) -> dict:
    """Выполняет HTTP-запрос к API и возвращает JSON-ответ."""
    params = {
        "city": city,
        "street": street,
        "house": house
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE_URL}/shutdowns", params=params, timeout=45) as response:
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

async def get_shutdowns_data(city: str, street: str, house: str) -> dict:
    """Вызывает API-парсер и возвращает полный агрегированный JSON-ответ."""
    try:
        return await _fetch_shutdowns_data_from_api(city, street, house)
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
                current_hash = _get_schedule_hash(data)
                ADDRESS_CACHE[address_key] = {
                    'last_schedule_hash': current_hash,
                    'last_checked': now 
                }
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
                final_message = f"❌ **Помилка перевірки** для {address_str}: {error_message}\n*Перевірка буде повторена через {f'{interval_hours:g}'.replace('.', ',')} {_pluralize_hours(interval_hours)}.*"
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
                
                # --- ИЗМЕНЕНИЕ: Форматирование уведомления с учетом нового формата ---
                # Отправляем "шапку" (Адрес, Черга)
                header_msg = (
                    f"🏠 Адреса: `{city}, {street}, {house}`\n"
                    f"👥 Черга: `{group}`"
                )
                interval_str = f"{f'{interval_hours:g}'.replace('.', ',')} год"
                update_header = "🔔 **ОНОВЛЕННЯ ГРАФІКУ!**" if last_hash not in (None, "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION") else "🔔 **Графік перевірено**"
                
                await bot.send_message(
                    chat_id=user_id,
                    text=f"{update_header} для {address_str} (інтервал {interval_str}):\n{header_msg}",
                    parse_mode="Markdown"
                )
                
                schedule = data.get("schedule", {})
                try:
                    sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
                except ValueError:
                    sorted_dates = sorted(schedule.keys())

                days_slots_48h = {}
                for idx, date in enumerate(sorted_dates):
                    slots = schedule[date]
                    # ЗМІНА: Виклик нової функції
                    emoji, header_line, body_lines = _process_single_day_schedule(date, slots)
                    
                    # --- ИЗМЕНЕНИЕ: Формирование ответа согласно требованиям пользователя ---
                    # 1. Шапка (дата и общее время) всегда вне блока ```
                    # Используем жирный шрифт для выделения
                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=f"**{header_line}**",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Failed to send update header message to user {user_id}: {e}")
                        
                    # 2. Тело (список слотов) только если есть отключения, и ТОЛЬКО оно в блоке ```
                    if emoji == "🔴":
                        body_block = f"```\n{body_lines}\n```"
                        try:
                            await bot.send_message(
                                chat_id=user_id,
                                text=body_block,
                                parse_mode="Markdown"
                            )
                        except Exception as e:
                            logger.error(f"Failed to send update day body message to user {user_id}: {e}")
                    
                    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
                        
                    # Собираем слоты для 48-часового графика, но только для первых двух дней
                    if idx < 2:
                        days_slots_48h[date] = slots
                
                # Отправка 48-часового графика
                if days_slots_48h:
                    image_data = _generate_48h_schedule_image(days_slots_48h)
                    if image_data:
                        await bot.send_message(chat_id=user_id, text="⏰ **Загальний графік на 48 годин**:")
                        image_file = BufferedInputFile(image_data, filename="schedule_48h_update.png")
                        await bot.send_photo(chat_id=user_id, photo=image_file)
                # --- КОНЕЦ ИЗМЕНЕНИЯ ---

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
        "*Приклад: `/subscribe 3` (кожні 3 години) або `/subscribe 0.5` (кожні 30 хв)*\n"
        "/unsubscribe - скасувати підписку.\n"
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
    await message.answer(f"✅ **Перевіряю графік** для адреси: {address_str}\n⏳ Очікуйте...")

    try:
        api_data = await get_shutdowns_data(city, street, house)
        current_hash = _get_schedule_hash(api_data)
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, city, street, house, last_hash) VALUES (?, ?, ?, ?, ?)",
            (user_id, city, street, house, current_hash)
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
        api_data = await get_shutdowns_data(city, street, house)
        current_hash = _get_schedule_hash(api_data)
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, city, street, house, last_hash) VALUES (?, ?, ?, ?, ?)",
            (user_id, city, street, house, current_hash)
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
    await message.answer(f"🔄 **Повторюю перевірку** для адреси: {address_str}\n⏳ Очікуйте...")
    try:
        data = await get_shutdowns_data(city, street, house)
        current_hash = _get_schedule_hash(data)
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
    interval_display = f"{hours_str} {_pluralize_hours(interval_hours)}"

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
        await db_conn.execute(
            "INSERT OR REPLACE INTO subscriptions (user_id, city, street, house, interval_hours, next_check, last_schedule_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, city, street, house, interval_hours, next_check_time, hash_to_use)
        )
        await db_conn.commit()
        logger.info(f"User {user_id} subscribed/updated to {city}, {street}, {house} with interval {interval_hours}h. Next check now.")
        await message.answer(
            f"✅ **Підписка оформлена!**\n"
            f"Ви будете отримувати оновлення для адреси: `{city}, {street}, {house}` з інтервалом **{interval_display}**.\n"
        )
    except Exception as e:
        logger.error(f"Failed to write subscription to DB for user {user_id}: {e}", exc_info=True)
        await message.answer("❌ **Помилка БД** при спробі зберегти підписку.")

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
        BotCommand(command="cancel", description="Скасувати поточну дію")
    ]
    await bot.set_my_commands(commands)

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

    checker_task = asyncio.create_task(subscription_checker_task(bot))

    logger.info("Бот запущено. Початок опитування...")
    try:
        await asyncio.gather(
            dp.start_polling(bot),
            checker_task,
        )
    finally:
        logger.info("Зупинка бота. Скасування фонових завдань...")
        checker_task.cancel()
        if db_conn:
            await db_conn.close()
            logger.info("Database connection closed.")
        await bot.session.close()
        logger.info("Bot session closed.")

if __name__ == "__main__":
    logger.setLevel(logging.DEBUG) 
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот зупинено вручну.")