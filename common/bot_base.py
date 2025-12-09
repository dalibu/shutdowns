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
from aiogram.types import User, InlineKeyboardMarkup, InlineKeyboardButton

# --- FSM States ---
class CaptchaState(StatesGroup):
    """Состояния для прохождения CAPTCHA-проверки"""
    waiting_for_answer = State()

class CheckAddressState(StatesGroup):
    """Состояния для пошагового ввода адреса через /check без аргументов"""
    waiting_for_city = State()
    waiting_for_street = State()
    waiting_for_house = State()

class AddressRenameState(StatesGroup):
    """Состояния для переименования адреса в адресной книге"""
    waiting_for_new_name = State()

# --- Global Caches ---
HUMAN_USERS: Dict[int, bool] = {}
ADDRESS_CACHE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
SCHEDULE_DATA_CACHE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

DEFAULT_INTERVAL_HOURS = 1.0
CHECKER_LOOP_INTERVAL_SECONDS = 5 * 60

# --- Database Functions ---
async def init_db(db_path: str) -> aiosqlite.Connection:
    """
    Initialize database connection.
    
    NOTE: This function only creates a connection. Schema creation and migrations
    should be done using the migrate.py CLI tool:
        python -m common.migrate --db-path <path>
    
    For new deployments, run migrations before starting the bot.
    """
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL;")
    
    # Verify database has been migrated
    try:
        cursor = await conn.execute("SELECT MAX(version) FROM schema_version")
        version = (await cursor.fetchone())[0]
        if version:
            logging.info(f"Database connected at {db_path} (schema version: {version})")
        else:
            logging.warning(f"Database at {db_path} has no migrations applied. Run: python -m common.migrate --db-path {db_path}")
    except Exception:
        logging.warning(f"Database at {db_path} may not be migrated. Run: python -m common.migrate --db-path {db_path}")
    
    return conn

async def update_user_activity(
    conn: aiosqlite.Connection, 
    user_id: int, 
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
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
                
            if first_name is not None:
                query += ", first_name = ?"
                params.append(first_name)
                
            if last_name is not None:
                query += ", last_name = ?"
                params.append(last_name)
            
            query += " WHERE user_id = ?"
            params.append(user_id)
            
            await conn.execute(query, params)
        else:
            # Insert new
            await conn.execute(
                """INSERT INTO user_activity 
                   (user_id, first_seen, last_seen, last_city, last_street, last_house, username, last_group, first_name, last_name) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, now, now, city, street, house, username, group_name, first_name, last_name)
            )
            
        await conn.commit()
    except Exception as e:
        logging.error(f"Failed to update user activity: {e}")


async def is_human_user(conn: aiosqlite.Connection, user_id: int) -> bool:
    """Check if user has passed CAPTCHA verification (persistent in DB)."""
    if not conn:
        return False
    
    try:
        async with conn.execute(
            "SELECT is_human FROM user_activity WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0])
    except Exception as e:
        # Column might not exist yet (before migration 004)
        logging.debug(f"is_human_user check failed (may need migration): {e}")
        return False


async def set_human_user(conn: aiosqlite.Connection, user_id: int, username: Optional[str] = None) -> None:
    """Mark user as verified human (persistent in DB)."""
    if not conn:
        return
    
    now = datetime.now()
    
    try:
        # Try to update existing record
        result = await conn.execute(
            "UPDATE user_activity SET is_human = 1 WHERE user_id = ?",
            (user_id,)
        )
        
        if result.rowcount == 0:
            # User doesn't exist, create new record
            await conn.execute(
                """INSERT INTO user_activity 
                   (user_id, first_seen, last_seen, username, is_human) 
                   VALUES (?, ?, ?, ?, 1)""",
                (user_id, now, now, username)
            )
        
        await conn.commit()
        logging.info(f"User {user_id} marked as human in database")
    except Exception as e:
        logging.error(f"Failed to set human user: {e}")

# --- Address Book Functions ---
async def save_user_address(
    conn: aiosqlite.Connection,
    user_id: int,
    city: str,
    street: str,
    house: str,
    group_name: Optional[str] = None
) -> int:
    """
    Saves address to user's address book. Updates last_used_at if exists.
    Returns the address ID.
    """
    if not conn:
        return -1
    
    now = datetime.now()
    try:
        # Try to insert, on conflict update last_used_at
        await conn.execute("""
            INSERT INTO user_addresses (user_id, city, street, house, group_name, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, city, street, house) DO UPDATE SET
                last_used_at = excluded.last_used_at,
                group_name = COALESCE(excluded.group_name, group_name)
        """, (user_id, city, street, house, group_name, now))
        await conn.commit()
        
        # Get the ID
        cursor = await conn.execute(
            "SELECT id FROM user_addresses WHERE user_id = ? AND city = ? AND street = ? AND house = ?",
            (user_id, city, street, house)
        )
        row = await cursor.fetchone()
        return row[0] if row else -1
    except Exception as e:
        logging.error(f"Failed to save user address: {e}")
        return -1

async def get_user_addresses(
    conn: aiosqlite.Connection,
    user_id: int,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Gets user's saved addresses, ordered by last_used_at (most recent first).
    Returns list of dicts with id, alias, city, street, house, group_name.
    """
    if not conn:
        return []
    
    try:
        cursor = await conn.execute("""
            SELECT id, alias, city, street, house, group_name, last_used_at
            FROM user_addresses
            WHERE user_id = ?
            ORDER BY last_used_at DESC NULLS LAST, created_at DESC
            LIMIT ?
        """, (user_id, limit))
        rows = await cursor.fetchall()
        
        return [
            {
                'id': row[0],
                'alias': row[1],
                'city': row[2],
                'street': row[3],
                'house': row[4],
                'group_name': row[5],
                'last_used_at': row[6]
            }
            for row in rows
        ]
    except Exception as e:
        logging.error(f"Failed to get user addresses: {e}")
        return []

async def get_address_by_id(
    conn: aiosqlite.Connection,
    user_id: int,
    address_id: int
) -> Optional[Dict[str, Any]]:
    """Gets a specific address by ID, ensuring it belongs to the user."""
    if not conn:
        return None
    
    try:
        cursor = await conn.execute("""
            SELECT id, alias, city, street, house, group_name
            FROM user_addresses
            WHERE id = ? AND user_id = ?
        """, (address_id, user_id))
        row = await cursor.fetchone()
        
        if row:
            return {
                'id': row[0],
                'alias': row[1],
                'city': row[2],
                'street': row[3],
                'house': row[4],
                'group_name': row[5]
            }
        return None
    except Exception as e:
        logging.error(f"Failed to get address by id: {e}")
        return None

async def delete_user_address(
    conn: aiosqlite.Connection,
    user_id: int,
    address_id: int
) -> bool:
    """Deletes an address from user's address book."""
    if not conn:
        return False
    
    try:
        cursor = await conn.execute(
            "DELETE FROM user_addresses WHERE id = ? AND user_id = ?",
            (address_id, user_id)
        )
        await conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Failed to delete user address: {e}")
        return False

async def rename_user_address(
    conn: aiosqlite.Connection,
    user_id: int,
    address_id: int,
    alias: str
) -> bool:
    """Sets or updates alias for an address."""
    if not conn:
        return False
    
    try:
        cursor = await conn.execute(
            "UPDATE user_addresses SET alias = ? WHERE id = ? AND user_id = ?",
            (alias, address_id, user_id)
        )
        await conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Failed to rename user address: {e}")
        return False

# --- Multi-Subscription Functions ---
async def get_user_subscriptions(
    conn: aiosqlite.Connection,
    user_id: int
) -> List[Dict[str, Any]]:
    """Gets all subscriptions for a user."""
    if not conn:
        return []
    
    try:
        cursor = await conn.execute("""
            SELECT id, city, street, house, interval_hours, notification_lead_time, group_name
            FROM subscriptions
            WHERE user_id = ?
            ORDER BY id
        """, (user_id,))
        rows = await cursor.fetchall()
        
        return [
            {
                'id': row[0],
                'city': row[1],
                'street': row[2],
                'house': row[3],
                'interval_hours': row[4],
                'notification_lead_time': row[5],
                'group_name': row[6]
            }
            for row in rows
        ]
    except Exception as e:
        logging.error(f"Failed to get user subscriptions: {e}")
        return []

async def get_subscription_count(conn: aiosqlite.Connection, user_id: int) -> int:
    """Returns number of subscriptions for a user."""
    if not conn:
        return 0
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0
    except Exception as e:
        logging.error(f"Failed to count subscriptions: {e}")
        return 0

async def is_address_subscribed(
    conn: aiosqlite.Connection,
    user_id: int,
    city: str,
    street: str,
    house: str
) -> bool:
    """Checks if user is already subscribed to this address."""
    if not conn:
        return False
    
    try:
        cursor = await conn.execute("""
            SELECT 1 FROM subscriptions
            WHERE user_id = ? AND city = ? AND street = ? AND house = ?
        """, (user_id, city, street, house))
        row = await cursor.fetchone()
        return row is not None
    except Exception as e:
        logging.error(f"Failed to check subscription: {e}")
        return False

async def remove_subscription(
    conn: aiosqlite.Connection,
    user_id: int,
    city: str,
    street: str,
    house: str
) -> bool:
    """Removes a specific subscription."""
    if not conn:
        return False
    
    try:
        cursor = await conn.execute("""
            DELETE FROM subscriptions
            WHERE user_id = ? AND city = ? AND street = ? AND house = ?
        """, (user_id, city, street, house))
        await conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Failed to remove subscription: {e}")
        return False

async def remove_subscription_by_id(
    conn: aiosqlite.Connection,
    user_id: int,
    subscription_id: int
) -> Optional[Tuple[str, str, str]]:
    """Removes subscription by ID. Returns (city, street, house) if success."""
    if not conn:
        return None
    
    try:
        # First get the address info
        cursor = await conn.execute(
            "SELECT city, street, house FROM subscriptions WHERE id = ? AND user_id = ?",
            (subscription_id, user_id)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        
        city, street, house = row
        
        # Delete
        await conn.execute(
            "DELETE FROM subscriptions WHERE id = ? AND user_id = ?",
            (subscription_id, user_id)
        )
        await conn.commit()
        return (city, street, house)
    except Exception as e:
        logging.error(f"Failed to remove subscription by id: {e}")
        return None

async def remove_all_subscriptions(
    conn: aiosqlite.Connection,
    user_id: int
) -> int:
    """Removes all subscriptions for a user. Returns count of removed."""
    if not conn:
        return 0
    
    try:
        cursor = await conn.execute(
            "DELETE FROM subscriptions WHERE user_id = ?",
            (user_id,)
        )
        await conn.commit()
        return cursor.rowcount
    except Exception as e:
        logging.error(f"Failed to remove all subscriptions: {e}")
        return 0

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

def format_user_info(user) -> str:
    """Форматирует информацию о пользователе для логирования."""
    user_id = user.id
    username = user.username or "N/A"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "N/A"
    return f"{user_id} (@{username}) {full_name}"

# --- Keyboard Builders ---
def _format_address_label(addr: Dict[str, Any], max_length: int = 35) -> str:
    """Formats address for button label, using alias if available."""
    if addr.get('alias'):
        label = addr['alias']
    else:
        label = f"{addr['city']}, {addr['street']}, {addr['house']}"
    
    if len(label) > max_length:
        label = label[:max_length - 3] + "..."
    return label

def build_address_selection_keyboard(
    addresses: List[Dict[str, Any]],
    action: str,
    include_new_button: bool = False
) -> InlineKeyboardMarkup:
    """
    Build keyboard with address buttons.
    action: 'check', 'repeat' - prefix for callback_data
    """
    buttons = []
    for addr in addresses:
        label = _format_address_label(addr)
        callback_data = f"{action}:{addr['id']}"
        buttons.append([InlineKeyboardButton(text=f"📍 {label}", callback_data=callback_data)])
    
    if include_new_button:
        buttons.append([InlineKeyboardButton(text="➕ Новий адреса", callback_data=f"{action}:new")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_subscription_selection_keyboard(
    subscriptions: List[Dict[str, Any]],
    action: str = "unsub"
) -> InlineKeyboardMarkup:
    """
    Build keyboard for unsubscribe selection.
    action: 'unsub' - prefix for callback_data
    """
    buttons = []
    for sub in subscriptions:
        label = _format_address_label(sub)
        callback_data = f"{action}:{sub['id']}"
        buttons.append([InlineKeyboardButton(text=f"📍 {label}", callback_data=callback_data)])
    
    # Add "unsubscribe all" button
    if len(subscriptions) > 1:
        buttons.append([InlineKeyboardButton(text="🚫 Відписатися від усіх", callback_data=f"{action}:all")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_address_management_keyboard(
    addresses: List[Dict[str, Any]]
) -> InlineKeyboardMarkup:
    """
    Build keyboard for address book management (delete/rename).
    """
    buttons = []
    for addr in addresses:
        label = _format_address_label(addr, max_length=25)
        # Two buttons per address: rename and delete
        buttons.append([
            InlineKeyboardButton(text=f"📍 {label}", callback_data=f"addr_info:{addr['id']}"),
        ])
        buttons.append([
            InlineKeyboardButton(text="✏️ Перейменувати", callback_data=f"addr_rename:{addr['id']}"),
            InlineKeyboardButton(text="🗑️ Видалити", callback_data=f"addr_delete:{addr['id']}")
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

