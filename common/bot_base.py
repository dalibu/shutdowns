"""
Common bot base functionality for power shutdown bots.
Contains database, FSM states, CAPTCHA, and core bot logic.
"""

import os
import re
import asyncio
import logging
import random
import hashlib
import aiosqlite
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional
import json
from aiogram.fsm.state import State, StatesGroup

# --- FSM States ---
class CaptchaState(StatesGroup):
    """Состояния для прохождения CAPTCHA-проверки"""
    waiting_for_answer = State()

class CheckAddressState(StatesGroup):
    """Состояния для пошагового ввода адреса через /check без аргументов"""
    waiting_for_city = State()
    waiting_for_street = State()
    waiting_for_house = State()

# --- Global Caches ---
HUMAN_USERS: Dict[int, bool] = {}
ADDRESS_CACHE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
SCHEDULE_DATA_CACHE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

DEFAULT_INTERVAL_HOURS = 1.0
CHECKER_LOOP_INTERVAL_SECONDS = 5 * 60

# --- Database Functions ---
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
        pass  # Колонка уже существует

    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN last_alert_event_start TIMESTAMP")
    except aiosqlite.OperationalError:
        pass  # Колонка уже существует
    
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN group_name TEXT")
    except aiosqlite.OperationalError:
        pass  # Колонка уже существует
    
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
        pass  # Колонка уже существует

    # --- Создание таблицы активности пользователей ---
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS user_activity (
        user_id INTEGER PRIMARY KEY,
        first_seen TIMESTAMP,
        last_seen TIMESTAMP,
        last_city TEXT,
        last_street TEXT,
        last_house TEXT,
        username TEXT,
        last_group TEXT
    )
    """)
    
    # --- Миграция: Добавляем last_group в user_activity ---
    try:
        await conn.execute("ALTER TABLE user_activity ADD COLUMN last_group TEXT")
    except aiosqlite.OperationalError:
        pass  # Колонка уже существует

    await conn.commit()
    logging.info(f"Database initialized and connected at {db_path}")
    return conn

async def update_user_activity(
    conn: aiosqlite.Connection, 
    user_id: int, 
    username: Optional[str] = None,
    city: Optional[str] = None, 
    street: Optional[str] = None, 
    house: Optional[str] = None,
    group_name: Optional[str] = None
):
    """Updates user activity record. Sets first_seen if new, updates last_seen and address."""
    if not conn:
        return

    now = datetime.now()
    
    try:
        # Check if user exists
        async with conn.execute("SELECT first_seen FROM user_activity WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        
        if row:
            # Update existing
            query = "UPDATE user_activity SET last_seen = ?, username = COALESCE(?, username)"
            params = [now, username]
            
            if city and street and house:
                query += ", last_city = ?, last_street = ?, last_house = ?"
                params.extend([city, street, house])
            
            if group_name:
                query += ", last_group = ?"
                params.append(group_name)
            
            query += " WHERE user_id = ?"
            params.append(user_id)
            
            await conn.execute(query, params)
        else:
            # Insert new
            await conn.execute(
                """INSERT INTO user_activity 
                   (user_id, first_seen, last_seen, last_city, last_street, last_house, username, last_group) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, now, now, city, street, house, username, group_name)
            )
            
        await conn.commit()
    except Exception as e:
        logging.error(f"Failed to update user activity: {e}")

# --- Utility Functions ---
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
        logging.error(f"Error parsing time range: {time_str}")
        return 0, 0  # Возвращаем 0,0 как ошибку

def format_minutes_to_hh_mm(minutes: int) -> str:
    """Форматирует общее количество минут в HH:MM."""
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"

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

def get_hours_str(value: float) -> str:
    """Возвращает правильное склонение слова 'год.'"""
    return "год."

def get_shutdown_duration_str_by_hours(duration_hours: float) -> str:
    """Принимает количество часов и возвращает форматированную строку с правильным склонением."""
    try:
        if duration_hours <= 0:
             return "0 год." 
        if duration_hours % 1 == 0:
            hours_str = str(int(duration_hours))
        else:
            # Використовуємо :g для видалення зайвих нулів, і замінюємо . на ,
            hours_str = f"{duration_hours:g}".replace('.', ',')
        plural_form = get_hours_str(duration_hours)
        return f"{hours_str} {plural_form}"
    except Exception:
        return "?"

def normalize_schedule_for_hash(data: dict) -> Dict[str, List[Dict[str, str]]]:
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

def get_schedule_hash_compact(data: dict) -> str:
    """
    Генерирует устойчивый хеш данных графика (schedule), используя каноническую 
    нормализованную JSON-строку. Это исключает влияние форматирования вывода 
    и неустойчивого порядка слотов.
    """
    normalized_data = normalize_schedule_for_hash(data)
    
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

# --- CAPTCHA Functions ---
def get_captcha_data() -> Tuple[str, int]:
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
