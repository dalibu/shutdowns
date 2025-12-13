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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional, Callable, Awaitable
import json
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import User, InlineKeyboardMarkup, InlineKeyboardButton


@dataclass
class BotContext:
    """
    Configuration context for parametrized bot handlers.
    Allows the same handler code to work with different providers.
    """
    provider_name: str          # Display name: "ДТЕК" or "ЦЕК"
    provider_code: str          # Code for logs/files: "dtek" or "cek"
    visualization_hours: int    # 48 for DTEK, 24 for CEK
    db_conn: Any = None         # aiosqlite.Connection
    font_path: str = ""
    get_data_func: Optional[Callable[..., Awaitable[dict]]] = None  # Provider data fetcher
    generate_image_func: Optional[Callable] = None  # Visualization function
    logger: Optional[logging.Logger] = None

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

# --- Configuration Constants (with environment variable fallback) ---
# Default subscription check interval (hours)
DEFAULT_INTERVAL_HOURS = float(os.getenv("DEFAULT_INTERVAL_HOURS", "1.0"))

# Subscription checker loop interval (seconds)
# Default: 5 minutes (300 seconds)
CHECKER_LOOP_INTERVAL_SECONDS = int(os.getenv("CHECKER_LOOP_INTERVAL_SECONDS", str(5 * 60)))

# --- Group Schedule Cache Functions ---
# Cache time-to-live in minutes
# Default: 15 minutes
GROUP_CACHE_TTL_MINUTES = int(os.getenv("GROUP_CACHE_TTL_MINUTES", "15"))

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

    import pytz
    kiev_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kiev_tz)
    
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
    
    import pytz
    kiev_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kiev_tz)
    
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
    
    import pytz
    kiev_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kiev_tz)
    try:
        # First, get or create address_id
        cursor = await conn.execute("""
            SELECT id FROM addresses
            WHERE city = ? AND street = ? AND house = ?
        """, (city, street, house))
        row = await cursor.fetchone()
        
        if row:
            address_id = row[0]
            # Update group if provided
            if group_name:
                await conn.execute("""
                    UPDATE addresses SET group_name = ?, updated_at = ?
                    WHERE id = ?
                """, (group_name, now, address_id))
        else:
            # Create new address
            cursor = await conn.execute("""
                INSERT INTO addresses (provider, city, street, house, group_name, created_at, updated_at)
                VALUES ('unknown', ?, ?, ?, ?, ?, ?)
            """, (city, street, house, group_name, now, now))
            address_id = cursor.lastrowid
        
        # Now save/update in user_addresses
        await conn.execute("""
            INSERT INTO user_addresses (user_id, address_id, last_used_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, address_id) DO UPDATE SET
                last_used_at = excluded.last_used_at
        """, (user_id, address_id, now))
        await conn.commit()
        
        return address_id
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
            SELECT ua.id, ua.alias, a.city, a.street, a.house, a.group_name, ua.last_used_at
            FROM user_addresses ua
            JOIN addresses a ON a.id = ua.address_id
            WHERE ua.user_id = ?
            ORDER BY ua.last_used_at DESC NULLS LAST, ua.created_at DESC
            LIMIT ?
        """, (user_id, limit))
        rows = await cursor.fetchall()
        
        return [
            {
                'id': row[0],
                'alias': row[1] if row[1] else None,
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
            SELECT ua.id, ua.alias, a.city, a.street, a.house, a.group_name
            FROM user_addresses ua
            JOIN addresses a ON a.id = ua.address_id
            WHERE ua.id = ? AND ua.user_id = ?
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
        logging.error(f"Failed to get address by ID: {e}")
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
    user_id: int,
    provider_code: str = None
) -> List[Dict[str, Any]]:
    """
    Gets all subscriptions for a user (both address and group subscriptions).
    
    Returns list with 'type' field: 'address' or 'group'
    """
    if not conn:
        return []
    
    subscriptions = []
    
    try:
        # 1. Get address subscriptions
        cursor = await conn.execute("""
            SELECT s.id, a.city, a.street, a.house, s.interval_hours, s.notification_lead_time, a.group_name
            FROM subscriptions s
            JOIN addresses a ON a.id = s.address_id
            WHERE s.user_id = ?
            ORDER BY a.group_name, s.id
        """, (user_id,))
        addr_rows = await cursor.fetchall()
        
        for row in addr_rows:
            subscriptions.append({
                'type': 'address',
                'id': row[0],
                'city': row[1],
                'street': row[2],
                'house': row[3],
                'interval_hours': row[4],
                'notification_lead_time': row[5],
                'group_name': row[6]
            })
        
        # 2. Get group subscriptions (if provider_code specified)
        if provider_code:
            cursor = await conn.execute("""
                SELECT id, group_name, interval_hours, notification_lead_time, provider
                FROM group_subscriptions
                WHERE user_id = ? AND provider = ?
                ORDER BY group_name
            """, (user_id, provider_code))
            group_rows = await cursor.fetchall()
            
            for row in group_rows:
                subscriptions.append({
                    'type': 'group',
                    'id': row[0],
                    'group_name': row[1],
                    'interval_hours': row[2],
                    'notification_lead_time': row[3],
                    'provider': row[4],
                    # For compatibility with address subscriptions
                    'city': None,
                    'street': None,
                    'house': None
                })
        
        return subscriptions
        
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
            SELECT 1 FROM subscriptions s
            JOIN addresses a ON a.id = s.address_id
            WHERE s.user_id = ? AND a.city = ? AND a.street = ? AND a.house = ?
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
            WHERE user_id = ? AND address_id IN (
                SELECT id FROM addresses
                WHERE city = ? AND street = ? AND house = ?
            )
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
        cursor = await conn.execute("""
            SELECT a.city, a.street, a.house
            FROM subscriptions s
            JOIN addresses a ON a.id = s.address_id
            WHERE s.id = ? AND s.user_id = ?
        """, (subscription_id, user_id))
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

async def remove_group_subscription(
    conn: aiosqlite.Connection,
    subscription_id: int
) -> bool:
    """Removes group subscription by ID. Returns True if success."""
    if not conn:
        return False
    
    try:
        cursor = await conn.execute(
            "DELETE FROM group_subscriptions WHERE id = ?",
            (subscription_id,)
        )
        await conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Failed to remove group subscription: {e}")
        return False

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

def detect_check_input_type(text: str) -> tuple[str, str]:
    """
    Определяет тип ввода для команды /check: группа или адрес.
    
    ДТЕК має 6 груп з двома підгрупами кожна: 1.1, 1.2, 2.1, 2.2, ... 6.1, 6.2
    Паттерн групи: перша цифра 1-6, роздільник (точка/кома/пробіл), друга цифра 1-2
    
    Приклади валідних груп:
    - 3.1, 3,1, 3 1 (нормалізується до 3.1)
    - 1.2, 6.1 (мінімум і максимум)
    
    Невалідні приклади (будуть адресою):
    - 7.1 (перша цифра > 6)
    - 3.3 (друга цифра > 2)
    - 3 (немає другої цифри)
    
    Args:
        text: Текст після команди /check
    
    Returns:
        ("group", normalized_group) - якщо це група (нормалізована до формату X.Y)
        ("address", original_text) - якщо це адреса
        ("unknown", "") - якщо порожній ввід
    """
    import re
    
    text_clean = text.strip()
    if not text_clean:
        return ("unknown", "")
    
    # Строгий паттерн для ДТЕК груп:
    # - Перша цифра: тільки [1-6]
    # - Роздільник: точка, кома, або пробіли \s*[.,\s]\s*
    # - Друга цифра: тільки [12]
    # ^: початок рядка, $: кінець рядка (щоб було ТІЛЬКИ це)
    group_pattern = r'^([1-6])\s*[.,\s]\s*([12])$'
    match = re.match(group_pattern, text_clean)
    
    if match:
        # Нормалізуємо групу до формату з точкою
        group_normalized = f"{match.group(1)}.{match.group(2)}"
        return ("group", group_normalized)
    
    # Інакше вважаємо що це адреса
    return ("address", text)


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
    Генерирует устойчивый хеш данных графика (schedule) и текущего отключения (current_outage), 
    используя каноническую нормализованную JSON-строку. Это исключает влияние форматирования 
    вывода и неустойчивого порядка слотов.
    """
    normalized_data = normalize_schedule_for_hash(data)
    
    # Включаем информацию о текущем отключении в хеш
    current_outage = data.get("current_outage")
    
    # Создаем объект для хеширования
    hash_object = {
        "schedule": normalized_data
    }
    
    # Добавляем current_outage если есть (только значимые поля)
    if current_outage and current_outage.get("has_current_outage"):
        hash_object["current_outage"] = {
            "reason": current_outage.get("reason"),
            "start_time": current_outage.get("start_time"),
            "expected_restoration": current_outage.get("expected_restoration"),
        }
    
    if not normalized_data and not hash_object.get("current_outage"):
        return "NO_SCHEDULE_FOUND"

    # Создаем устойчивую (каноническую) JSON-строку:
    # ensure_ascii=False для кириллицы
    # separators=(',', ':') для удаления пробелов
    # sort_keys=True гарантирует порядок верхнего уровня
    schedule_json_string = json.dumps(
        hash_object, 
        sort_keys=True, 
        ensure_ascii=False, 
        separators=(',', ':')
    )
    
    # Хешируем полученную строку
    return hashlib.sha256(schedule_json_string.encode('utf-8')).hexdigest()


async def get_group_cache(
    conn: aiosqlite.Connection,
    group_name: str,
    provider: str
) -> Optional[Dict[str, Any]]:
    """
    Get cached schedule data for a group if available and fresh.
    
    Args:
        conn: Database connection
        group_name: Group identifier (e.g., "3.1", "5.2")
        provider: Provider code ("dtek" or "cek")
    
    Returns:
        Dict with 'data' (parsed JSON) and 'hash' if cache is fresh, None otherwise
    """
    if not conn or not group_name:
        return None
    
    import pytz
    kiev_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kiev_tz)
    
    try:
        cursor = await conn.execute("""
            SELECT last_schedule_hash, schedule_data, last_updated
            FROM group_schedule_cache
            WHERE group_name = ? AND provider = ?
        """, (group_name, provider))
        row = await cursor.fetchone()
        
        if not row:
            return None
        
        last_hash, schedule_json, last_updated = row
        
        # Parse last_updated timestamp
        try:
            last_updated_dt = datetime.fromisoformat(last_updated)
            if last_updated_dt.tzinfo is None:
                last_updated_dt = kiev_tz.localize(last_updated_dt)
        except:
            return None
        
        # Check if cache is still fresh
        age_minutes = (now - last_updated_dt).total_seconds() / 60
        if age_minutes > GROUP_CACHE_TTL_MINUTES:
            logging.debug(f"Group cache for {group_name} ({provider}) is stale ({age_minutes:.1f} min old)")
            return None
        
        # Parse schedule data
        try:
            schedule_data = json.loads(schedule_json) if schedule_json else {}
        except:
            return None
        
        logging.info(f"Using group cache for {group_name} ({provider}), age: {age_minutes:.1f} min")
        return {
            'data': schedule_data,
            'hash': last_hash
        }
    except Exception as e:
        logging.error(f"Failed to get group cache: {e}")
        return None


async def update_group_cache(
    conn: aiosqlite.Connection,
    group_name: str,
    provider: str,
    schedule_hash: str,
    schedule_data: Dict[str, Any]
) -> bool:
    """
    Update or insert group schedule cache.
    
    Args:
        conn: Database connection
        group_name: Group identifier
        provider: Provider code ("dtek" or "cek")
        schedule_hash: Computed hash of the schedule
        schedule_data: Full schedule data dict
    
    Returns:
        True if successful, False otherwise
    """
    if not conn or not group_name:
        return False
    
    import pytz
    kiev_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kiev_tz)
    
    try:
        # Serialize schedule data to JSON
        schedule_json = json.dumps(schedule_data, ensure_ascii=False)
        
        await conn.execute("""
            INSERT INTO group_schedule_cache (group_name, provider, last_schedule_hash, schedule_data, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(group_name, provider) DO UPDATE SET
                last_schedule_hash = excluded.last_schedule_hash,
                schedule_data = excluded.schedule_data,
                last_updated = excluded.last_updated
        """, (group_name, provider, schedule_hash, schedule_json, now))
        await conn.commit()
        
        logging.debug(f"Updated group cache for {group_name} ({provider}), hash: {schedule_hash[:16]}")
        return True
    except Exception as e:
        logging.error(f"Failed to update group cache: {e}")
        return False


async def get_cached_group_for_address(
    conn: aiosqlite.Connection,
    city: str,
    street: str,
    house: str
) -> Optional[str]:
    """
    Get cached group name for an address from subscriptions or user_last_check.
    
    This checks both tables to find if we've previously determined the group
    for this address, avoiding a full parser call just to get the group.
    
    Returns:
        Group name string if found, None otherwise
    """
    if not conn:
        return None
    
    try:
        # Try subscriptions first (most reliable)
        cursor = await conn.execute("""
            SELECT group_name FROM subscriptions
            WHERE city = ? AND street = ? AND house = ?
            AND group_name IS NOT NULL
            LIMIT 1
        """, (city, street, house))
        row = await cursor.fetchone()
        if row and row[0]:
            return row[0]
        
        # Try user_last_check as fallback
        cursor = await conn.execute("""
            SELECT group_name FROM user_last_check
            WHERE city = ? AND street = ? AND house = ?
            AND group_name IS NOT NULL
            LIMIT 1
        """, (city, street, house))
        row = await cursor.fetchone()
        if row and row[0]:
            return row[0]
        
        return None
    except Exception as e:
        logging.debug(f"Failed to get cached group for address: {e}")
        return None


async def get_address_id(
    conn: aiosqlite.Connection,
    city: str,
    street: str,
    house: str,
    provider: str = 'unknown'  # Optional since each DB has only one provider
) -> Tuple[Optional[int], Optional[str]]:
    """
    Get or create address_id for given address.
    
    This is the main function for working with addresses after normalization.
    Returns address ID and cached group name.
    
    Note: provider parameter is optional/ignored since each bot database only contains one provider.
    
    Args:
        conn: Database connection
        city: City name
        street: Street name
        house: House number
        provider: Provider code (optional, defaults to 'unknown')
    
    Returns:
        Tuple of (address_id, group_name) if found/created, (None, None) on error
    """
    if not conn:
        return None, None
    
    try:
        # Try to find existing address (no provider filter - each DB has only one provider anyway)
        cursor = await conn.execute("""
            SELECT id, group_name FROM addresses
            WHERE city = ? AND street = ? AND house = ?
        """, (city, street, house))
        row = await cursor.fetchone()
        
        if row:
            return row[0], row[1]  # (address_id, group_name)
        
        # Create new address if not found
        cursor = await conn.execute("""
            INSERT INTO addresses (provider, city, street, house, created_at, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (provider, city, street, house))
        await conn.commit()
        
        address_id = cursor.lastrowid
        logging.debug(f"Created new address {address_id}: {city}, {street}, {house}")
        return address_id, None
        
    except Exception as e:
        logging.error(f"Failed to get/create address: {e}")
        return None, None


async def update_address_group(
    conn: aiosqlite.Connection,
    address_id: int,
    group_name: str
) -> bool:
    """
    Update group name for an address.
    
    This is now the single source of truth for address group information.
    Replaces update_address_group_mapping().
    
    Args:
        conn: Database connection
        address_id: Address ID from addresses table
        group_name: Group identifier
    
    Returns:
        True if successful, False otherwise
    """
    if not conn or not address_id or not group_name:
        return False
    
    import pytz
    kiev_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kiev_tz)
    
    try:
        await conn.execute("""
            UPDATE addresses 
            SET group_name = ?, updated_at = ?
            WHERE id = ?
        """, (group_name, now, address_id))
        await conn.commit()
        
        logging.debug(f"Updated group for address {address_id} -> {group_name}")
        return True
    except Exception as e:
        logging.error(f"Failed to update address group: {e}")
        return False


async def get_address_data_by_id(
    conn: aiosqlite.Connection,
    address_id: int
) -> Optional[Dict[str, Any]]:
    """
    Get full address information by ID.
    
    Args:
        conn: Database connection
        address_id: Address ID
    
    Returns:
        Dict with address info or None
    """
    if not conn or not address_id:
        return None
    
    try:
        cursor = await conn.execute("""
            SELECT id, provider, city, street, house, group_name
            FROM addresses
            WHERE id = ?
        """, (address_id,))
        row = await cursor.fetchone()
        
        if row:
            return {
                'id': row[0],
                'provider': row[1],
                'city': row[2],
                'street': row[3],
                'house': row[4],
                'group_name': row[5]
            }
        return None
    except Exception as e:
        logging.error(f"Failed to get address by ID: {e}")
        return None


async def find_addresses_by_group(
    conn: aiosqlite.Connection,
    provider: str,
    group_name: str,
    limit: int = 10
) -> List[Dict[str, str]]:
    """
    Find addresses that belong to a specific group.
    
    This enables the future feature where users can search by group number.
    Now uses the normalized addresses table.
    
    Args:
        conn: Database connection
        provider: Provider code ("dtek" or "cek")
        group_name: Group identifier to search for
        limit: Maximum number of results
    
    Returns:
        List of dicts with 'id', 'city', 'street', 'house', 'updated_at'
    """
    if not conn or not group_name:
        return []
    
    try:
        cursor = await conn.execute("""
            SELECT id, city, street, house, updated_at
            FROM addresses
            WHERE provider = ? AND group_name = ?
            ORDER BY updated_at DESC
            LIMIT ?
        """, (provider, group_name, limit))
        rows = await cursor.fetchall()
        
        return [
            {
                'id': row[0],
                'city': row[1],
                'street': row[2],
                'house': row[3],
                'updated_at': row[4]
            }
            for row in rows
        ]
    except Exception as e:
        logging.error(f"Failed to find addresses by group: {e}")
        return []


# --- DEPRECATED FUNCTIONS (kept for backward compatibility during migration) ---

async def update_address_group_mapping(
    conn: aiosqlite.Connection,
    provider: str,
    city: str,
    street: str,
    house: str,
    group_name: str
) -> bool:
    """
    DEPRECATED: Use get_address_id() + update_address_group() instead.
    
    Kept for backward compatibility during migration.
    This function now updates the addresses table instead of address_group_mapping.
    """
    address_id, _ = await get_address_id(conn, city, street, house)
    if address_id:
        return await update_address_group(conn, address_id, group_name)
    return False


async def get_cached_group_for_address(
    conn: aiosqlite.Connection,
    city: str,
    street: str,
    house: str
) -> Optional[str]:
    """
    DEPRECATED: Use get_address_id() instead.
    
    Get cached group name for an address from subscriptions or user_last_check.
    Kept for backward compatibility, but now checks addresses table first.
    """
    if not conn:
        return None
    
    try:
        # Try addresses first (new normalized table)
        cursor = await conn.execute("""
            SELECT group_name FROM addresses
            WHERE city = ? AND street = ? AND house = ?
            AND group_name IS NOT NULL
            LIMIT 1
        """, (city, street, house))
        row = await cursor.fetchone()
        if row and row[0]:
            return row[0]
        
        # Fallback to subscriptions (for safety during migration)
        cursor = await conn.execute("""
            SELECT a.group_name FROM subscriptions s
            JOIN addresses a ON a.id = s.address_id
            WHERE a.city = ? AND a.street = ? AND a.house = ?
            AND a.group_name IS NOT NULL
            LIMIT 1
        """, (city, street, house))
        row = await cursor.fetchone()
        if row and row[0]:
            return row[0]
        
        # Fallback to user_last_check
        cursor = await conn.execute("""
            SELECT a.group_name FROM user_last_check ulc
            JOIN addresses a ON a.id = ulc.address_id
            WHERE a.city = ? AND a.street = ? AND a.house = ?
            AND a.group_name IS NOT NULL
            LIMIT 1
        """, (city, street, house))
        row = await cursor.fetchone()
        if row and row[0]:
            return row[0]
        
        return None
    except Exception as e:
        logging.debug(f"Failed to get cached group for address: {e}")
        return None


async def get_group_for_address(
    conn: aiosqlite.Connection,
    provider: str,
    city: str,
    street: str,
    house: str
) -> Optional[str]:
    """
    DEPRECATED: Use get_address_id() instead (returns both ID and group).
    
    Get group name for an address.
    Kept for backward compatibility - now just wraps get_address_id().
    """
    _, group_name = await get_address_id(conn, city, street, house)
    return group_name


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
    Supports both address and group subscriptions.
    action: 'unsub' - prefix for callback_data
    """
    from .formatting import format_group_name
    
    buttons = []
    for sub in subscriptions:
        if sub.get('type') == 'group':
            # Group subscription
            label = f"👥 Черга {format_group_name(sub['group_name'])}"
            callback_data = f"{action}:group:{sub['id']}"
        else:
            # Address subscription
            label = _format_address_label(sub)
            callback_data = f"{action}:{sub['id']}"
        
        buttons.append([InlineKeyboardButton(text=label, callback_data=callback_data)])
    
    # Add "unsubscribe all" button
    if len(subscriptions) > 1:
        buttons.append([InlineKeyboardButton(text="🗑️ Відписатися від усіх", callback_data=f"{action}:all")])
    
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

