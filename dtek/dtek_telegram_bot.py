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

# --- НОВЫЕ ИМПОРТЫ ДЛЯ ГРАФИКОВ ---
import matplotlib
matplotlib.use('Agg') # Важно для запуска в non-GUI окружении
import matplotlib.pyplot as plt
import numpy as np
import io
# ----------------------------------

# --- 1. Конфігурація ---
BOT_TOKEN = os.getenv("DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN")
API_BASE_URL = os.getenv("API_BASE_URL", "http://dtek_api:8000") 
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")

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

def _process_single_day_schedule(date: str, slots: List[Dict[str, Any]]) -> str:
    """Консолидирует слоты отключений в ГРУППЫ и возвращает строку со временем."""
    outage_slots = [s for s in slots if s.get('disconection') in ('full', 'half')]
    if not outage_slots:
        return "Відключення не заплановані" 

    groups = []
    current_group = None
    for slot in outage_slots:
        try:
            time_parts = re.split(r'\s*[-\bi\—]\s*', slot.get('time', '0-0'))
            start_hour = int(time_parts[0])
            end_hour = int(time_parts[1])
            if end_hour == 0:
                end_hour = 24
            slot_duration = 0.0
            slot_start_min = 0
            slot_end_min = 0
            disconection = slot.get('disconection')
            if disconection == 'full':
                slot_duration = 1.0
                slot_start_min = start_hour * 60
                slot_end_min = end_hour * 60
            elif disconection == 'half':
                slot_duration = 0.5
                slot_start_min = start_hour * 60 + 30
                slot_end_min = end_hour * 60

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
         return "Помилка парсингу слотів"
    output_parts = []
    for group in groups:
        start_time_final = format_minutes_to_hh_m(group["start_min"])
        end_time_final = format_minutes_to_hh_m(group["end_min"])
        duration_str = _get_shutdown_duration_str_by_hours(group["duration_hours"])
        output_parts.append(f"{start_time_final} - {end_time_final} ({duration_str})")

    return ", ".join(output_parts)

def format_shutdown_message(data: dict) -> str:
    """
    Форматирует агрегированный JSON-ответ в новый, компактный формат.
    (Используется для фоновых уведомлений)
    """
    city = data.get("city", "Н/Д")
    street = data.get("street", "Н/Д")
    house = data.get("house_num", "Н/Д")
    group = data.get("group", "Н/Д")
    schedule = data.get("schedule", {})

    message = (
        f"🏠 Адреса: `{city}, {street}, {house}`\n"
        f"👥 Черга: `{group}`"
    )

    if not schedule:
        return message + "\n❌ *Не вдалося отримати графік відключень.*"

    try:
        sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
    except ValueError:
        sorted_dates = sorted(schedule.keys())

    schedule_lines = []
    for date in sorted_dates:
        slots = schedule[date]
        result_str = _process_single_day_schedule(date, slots)
        if "Відключення не заплановані" in result_str or "Помилка" in result_str:
            line = f"✅ **{date}**: {result_str}"
        else:
            line = f"❌ **{date}**: {result_str}"
        schedule_lines.append(line)

    final_schedule_output = "\n".join(schedule_lines)
    return message + "\n" + final_schedule_output

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
    if value % 1 != 0:
        return "години"

    h = int(value)
    last_two_digits = h % 100
    last_digit = h % 10

    if 11 <= last_two_digits <= 14:
        return "годин"
    if last_digit == 1:
        return "годину"
    if 2 <= last_digit <= 4:
        return "години"
    return "годин"

def _get_shutdown_duration_str_by_hours(duration_hours: float) -> str:
    """Принимает количество часов и возвращает форматированную строку с правильным склонением."""
    try:
        if duration_hours <= 0:
             return "0 годин"
        if duration_hours % 1 == 0:
            hours_str = str(int(duration_hours))
        else:
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
        result_str = _process_single_day_schedule(date, slots)
        schedule_parts.append(f"{date}:{result_str}")

    schedule_string = "|".join(schedule_parts)
    return hashlib.sha256(schedule_string.encode('utf-8')).hexdigest()

# --- НОВАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ОТПРАВКИ ОТВЕТА ---
async def send_schedule_response(message: types.Message, api_data: dict, is_subscribed: bool):
    """
    Отправляет пользователю форматированный ответ, 
    разбитый по дням (текст + изображение для каждого дня).
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

        # 3. Цикл по дням (Текст + Картинка)
        for date in sorted_dates:
            slots = schedule.get(date, [])
            result_str = _process_single_day_schedule(date, slots)
            
            if "Відключення не заплановані" in result_str or "Помилка" in result_str:
                line = f"✅ **{date}**: {result_str}"
            else:
                line = f"❌ **{date}**: {result_str}"
            
            # Отправляем текст для этого дня
            await message.answer(line)
            
            # Генерируем и отправляем картинку для этого дня
            # _generate_schedule_image вернет None, если отключений нет
            image_data = _generate_schedule_image(slots)
            
            if image_data:
                image_file = BufferedInputFile(image_data, filename=f"schedule_{date}.png")
                await message.answer_photo(photo=image_file)

        # 4. Отправляем "подвал" (приглашение к подписке)
        if not is_subscribed:
            await message.answer("💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`.")
    
    except Exception as e:
        logger.error(f"Error in send_schedule_response for user {message.from_user.id}: {e}", exc_info=True)
        await message.answer("❌ Сталася помилка під час формування відповіді.")

# ---------------------------------------------------------

# --- НОВАЯ ФУНКЦИЯ ДЛЯ ГЕНЕРАЦИИ ГРАФИКА ---
def _generate_schedule_image(slots: List[Dict[str, Any]]) -> bytes:
    """
    Генерирует 24-часовое изображение графика (clock-face) на основе слотов.
    """
    try:
        N = 1440 # 1440 минут в дне
        radii = np.ones(N)
        colors = ['#FFFFFF'] * N # Белый (есть свет)

        has_outage = False
        for slot in slots:
            disconection = slot.get('disconection')
            if disconection not in ('full', 'half'):
                continue
                
            try:
                time_parts = re.split(r'\s*[-\bi\—]\s*', slot.get('time', '0-0'))
                start_hour = int(time_parts[0])
                end_hour = int(time_parts[1])
                if end_hour == 0:
                    end_hour = 24
                
                slot_start_min = 0
                slot_end_min = 0

                if disconection == 'full':
                    slot_start_min = start_hour * 60
                    slot_end_min = end_hour * 60
                elif disconection == 'half':
                    slot_start_min = start_hour * 60 + 30
                    slot_end_min = end_hour * 60

                if slot_end_min > slot_start_min:
                    has_outage = True
                    # Убедимся, что end_min не больше 1440
                    end_idx = min(slot_end_min, N)
                    for i in range(slot_start_min, end_idx):
                        if 0 <= i < N:
                            colors[i] = '#FF0000' # Красный (нет света)
            except Exception:
                continue 

        if not has_outage:
            return None # Не генерируем картинку, если нет отключений

        # 2. Настройка графика
        theta = np.linspace(0.0, 2 * np.pi, N, endpoint=False)
        width = (2 * np.pi) / N + 0.001 # Чуть больше, чтобы перекрыть пробелы

        fig, ax = plt.subplots(figsize=(6, 6), subplot_kw={'projection': 'polar'})
        
        ax.bar(theta, radii, width=width, bottom=0.0, color=colors, alpha=1.0, edgecolor='none')

        # 3. Настройка осей
        ax.set_theta_zero_location('N') # 0 (полночь) сверху
        ax.set_theta_direction(-1) # По часовой стрелке
        
        # Метки часов (0-23)
        # --- ИЗМЕНЕНИЕ: Увеличен шрифт ---
        ax.set_xticks(np.linspace(0, 2 * np.pi, 24, endpoint=False))
        ax.set_xticklabels([str(i) for i in range(24)], fontsize=14)
        
        # Убираем радиальные метки
        ax.set_rticks([])
        
        # Настраиваем сетку (только радиальные линии, как в примере)
        ax.yaxis.grid(False)
        ax.xaxis.grid(True, color='black', linestyle='-', linewidth=0.5, alpha=0.7)

        # Устанавливаем предел, чтобы график занимал все место
        ax.set_ylim(0, 1.0) 
        ax.spines['polar'].set_visible(False) # Убираем внешнюю рамку
        
        plt.tight_layout()

        # 4. Сохранение в байты
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.1)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()

    except Exception as e:
        logger.error(f"Failed to generate schedule image: {e}", exc_info=True)
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
                response_text = format_shutdown_message(data)
                interval_str = f"{f'{interval_hours:g}'.replace('.', ',')} год"
                header = "🔔 **ОНОВЛЕННЯ ГРАФІКУ!**" if last_hash not in (None, "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION") else "🔔 **Графік перевірено**"
                final_message = (
                    f"{header} для {address_str} (інтервал {interval_str}):\n"
                    f"{response_text}"
                )
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=final_message,
                        parse_mode="Markdown"
                    )
                    db_updates_success.append((next_check_time, new_hash, user_id))
                    logger.info(f"Notification sent to user {user_id}. Hash updated to {new_hash[:8]}.")
                except Exception as e:
                    logger.error(f"Failed to send update to user {user_id}: {e}. Hash NOT updated.")
                    db_updates_fail.append((next_check_time, user_id))
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
        
        # --- ИЗМЕНЕНИЕ: Вызов новой функции-отправщика ---
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
        
        # --- ИЗМЕНЕНИЕ: Вызов новой функции-отправщика ---
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
        
        # --- ИЗМЕНЕНИЕ: Вызов новой функции-отправщика ---
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