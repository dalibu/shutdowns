import os
import re
import asyncio
import logging
import random 
import hashlib 
import aiosqlite # ДОБАВЛЕНО: для работы с SQLite
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple 
import aiohttp
from aiogram import Bot, Dispatcher, types, F 
from aiogram.filters import Command 
from aiogram.types import BotCommand, ReplyKeyboardRemove
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext 
from aiogram.fsm.state import State, StatesGroup 

# --- 1. Конфігурація ---
# Токен бота берется из переменных окружения
BOT_TOKEN = os.getenv("DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN")
# URL вашего FastAPI парсера
API_BASE_URL = os.getenv("API_BASE_URL", "http://dtek_api:8000") 
# ДОБАВЛЕНО: Путь к базе данных
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
# ДОДАНО: ГЛОБАЛЬНИЙ ДИСПЕТЧЕР для роботи декораторів
dp = Dispatcher()

# ДОБАВЛЕНО: Глобальное соединение с БД
db_conn: aiosqlite.Connection = None 

# --- 1.5. FSM-состояния и Глобальный Кеш ---
class CaptchaState(StatesGroup):
    """Состояния для прохождения CAPTCHA-проверки"""
    waiting_for_answer = State()

# ДОБАВЛЕНО: Новый класс состояний для пошагового ввода адреса
class CheckAddressState(StatesGroup):
    """Состояния для пошагового ввода адреса через /check без аргументов"""
    waiting_for_city = State()
    waiting_for_street = State()
    waiting_for_house = State()
# КОНЕЦ ДОБАВЛЕННОГО БЛОКА

# Кеш для хранения user_id пользователей, успешно прошедших проверку.
# 📌 ИЗМЕНЕНИЕ: Этот кеш остается в памяти.
HUMAN_USERS: Dict[int, bool] = {} 

# ❌ УДАЛЕНО: Глобальный кеш SUBSCRIPTIONS, он будет заменен базой данных.
# SUBSCRIPTIONS: Dict[int, Dict[str, Any]] = {} 

# ДОБАВЛЕНО: Кеш для хранения расписания по адресу для дедупликации API запросов.
# Key: (city, street, house)
# Value: {'last_schedule_hash': str, 'last_checked': datetime}
ADDRESS_CACHE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

DEFAULT_INTERVAL_HOURS = 1.0 # ІНТЕРВАЛ ЗА ЗАМОВЧУВАННЯМ: 1 година
CHECKER_LOOP_INTERVAL_SECONDS = 5 * 60 # Фонова задача прокидається кожні 5 хвилин

# ---------------------------------------------------------
# --- 1.8. Инициализация Базы Данных (НОВЫЙ БЛОК) ---
async def init_db(db_path: str) -> aiosqlite.Connection:
    """Инициализирует соединение с SQLite и создает таблицы, если их нет."""
    # Убедимся, что директория существует
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    # Включаем WAL-режим для лучшей производительности при одновременной записи и чтении
    await conn.execute("PRAGMA journal_mode=WAL;")
    # Таблица для хранения подписок
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
    # Таблица для хранения последнего успешного запроса (замена FSM)
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
# -----------------------------------------------------

# --- 2. Вспомогательные функции (Бизнес-логика) ---
def format_minutes_to_hh_m(minutes: int) -> str:
    """Форматирует общее количество минут в HH:MM."""
    h = minutes // 60
    m = minutes % 60
    # ИСПРАВЛЕНИЕ: Добавление :02d для часа для двухзначного формата
    return f"{h:02d}:{m:02d}"

def _process_single_day_schedule(date: str, slots: List[Dict[str, Any]]) -> str:
    """
    Консолидирует слоты отключений в ГРУППЫ и возвращает строку со временем.
    """
    outage_slots = [s for s in slots if s.get('disconection') in ('full', 'half')]
    if not outage_slots:
        return "Відключення не заплановані" 

    # --- 1. Группировка смежных слотов ---
    groups = [] # Список групп [{start_min, end_min, duration_hours}]
    current_group = None
    for slot in outage_slots:
        try:
            # 1.1. Извлечение времени и длительности слота
            time_parts = re.split(r'\s*[-\bi\–]\s*', slot.get('time', '0-0'))
            start_hour = int(time_parts[0])
            end_hour = int(time_parts[1])
            if end_hour == 0: # Обработка 23-00 (00 == 24)
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
                # Логика из старой версии: half - это вторая половина часа
                slot_start_min = start_hour * 60 + 30
                slot_end_min = end_hour * 60

            # 1.2. Логика группировки
            if current_group is None:
                # Начинаем новую группу
                current_group = {
                    "start_min": slot_start_min,
                    "end_min": slot_end_min,
                    "duration_hours": slot_duration
                }
            # Слот (slot_start_min) начинается *сразу* после конца текущей группы (current_group.end_min)?
            elif slot_start_min == current_group["end_min"]: 
                # Продлеваем группу
                current_group["end_min"] = slot_end_min
                current_group["duration_hours"] += slot_duration
            else:
                # Разрыв. Сохраняем старую группу и начинаем новую.
                groups.append(current_group)
                current_group = {
                    "start_min": slot_start_min,
                    "end_min": slot_end_min,
                    "duration_hours": slot_duration
                }
        except Exception as e:
            logger.error(f"Error processing slot {slot}: {e}")
            continue # Пропускаем битый слот

    # 1.3. Добавляем последнюю группу
    if current_group:
        groups.append(current_group)

    # --- 2. Форматирование вывода ---
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
    # Игнорируем команды при парсинге адреса
    # Удаляем все вхождения команд, а не только первые
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
    # Для дробных чисел (0,5; 1,5; 2,5) всегда используем 'години'
    if value % 1 != 0:
        return "години"

    # Правила для целых чисел
    h = int(value)
    last_two_digits = h % 100
    last_digit = h % 10

    # 11-14, 211-214, ...: годин (обработка исключения для 11-14)
    if 11 <= last_two_digits <= 14:
        return "годин"

    # 1, 21, 31, 101...: годину
    if last_digit == 1:
        return "годину"

    # 2-4, 22-24, 32-34, 102...: години
    if 2 <= last_digit <= 4:
        return "години"

    # 0, 5-10, 15-20, ...: годин
    return "годин"

# 📌 ИЗМЕНЕНИЕ: Упрощение функции расчета длительности. 
# Теперь принимает просто количество часов (включая дробные).
def _get_shutdown_duration_str_by_hours(duration_hours: float) -> str:
    """
    Принимает количество часов и возвращает форматированную строку
    с правильным склонением: '(X [година/години/годин])'.
    """
    try:
        if duration_hours <= 0:
             return "0 годин"
        # Форматирование: 1.0 -> '1', 2.5 -> '2,5'. Используем запятую.
        # Используем f"{duration_hours:g}" для удаления незначащих нулей (1.0 -> 1)
        if duration_hours % 1 == 0:
            hours_str = str(int(duration_hours))
        else:
            hours_str = f"{duration_hours:g}".replace('.', ',')
        plural_form = _pluralize_hours(duration_hours)
        # Обновленный лаконичный формат:
        return f"{hours_str} {plural_form}"
    except Exception:
        return "?" # Упрощенный резервный вариант

# НОВАЯ ФУНКЦИЯ: Генерация хеша из расписания
def _get_schedule_hash(data: dict) -> str:
    """
    Генерирует хеш только из данных графика (schedule) для сравнения изменений.
    Хешируются только ключевые данные расписания, чтобы избежать сравнения времени генерации или других метаданных.
    """
    schedule = data.get("schedule", {})
    if not schedule:
        return "NO_SCHEDULE_FOUND"

    # Формируем строку из расписания: дата + _process_single_day_schedule результат
    schedule_parts = []
    try:
        # Сортировка по дате для стабильного хеша
        sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
    except ValueError:
        sorted_dates = sorted(schedule.keys())

    for date in sorted_dates:
        slots = schedule[date]
        # Используем результат _process_single_day_schedule, который компактно описывает отключение
        result_str = _process_single_day_schedule(date, slots)
        schedule_parts.append(f"{date}:{result_str}")

    schedule_string = "|".join(schedule_parts)
    # Хеширование с использованием SHA256
    return hashlib.sha256(schedule_string.encode('utf-8')).hexdigest()
# КОНЕЦ НОВОЙ ФУНКЦИИ

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
        # Убедимся, что a > b для простоты
        question = f"Скільки буде {a} - {b}?"
        answer = a - b
    return question, answer

async def _handle_captcha_check(message: types.Message, state: FSMContext) -> bool:
    """Проверяет, прошел ли пользователь CAPTCHA. Возвращает True, если прошел."""
    user_id = message.from_user.id
    if user_id in HUMAN_USERS:
        return True

    # 1. Запуск проверки
    await state.set_state(CaptchaState.waiting_for_answer)
    question, correct_answer = _get_captcha_data()
    # Сохраняем правильный ответ в FSM контексте
    await state.update_data(captcha_answer=correct_answer)
    await message.answer(
        "🚨 **Увага! Для захисту від ботів, пройдіть просту перевірку.**\n"
        f"**{question}**\n"
        "Введіть лише число-відповідь."
    )
    return False
# -----------------------------------------------------

# --- 3. Интеграция с API (Асинхронные функции) ---

# НОВАЯ ФУНКЦИЯ: Изолирует вызов API
async def _fetch_shutdowns_data_from_api(city: str, street: str, house: str) -> dict:
    """
    Выполняет HTTP-запрос к API и возвращает JSON-ответ.
    """
    params = {
        "city": city,
        "street": street,
        "house": house
    }
    async with aiohttp.ClientSession() as session:
        # Увеличен таймаут до 45 секунд, чтобы дождаться Playwright
        async with session.get(f"{API_BASE_URL}/shutdowns", params=params, timeout=45) as response:
            if response.status == 404:
                # ... (обработка 404)
                error_json = {}
                try:
                    error_json = await response.json()
                except aiohttp.ContentTypeError:
                    pass
                detail = error_json.get("detail", "Графік для цієї адреси не знайдено.")
                raise ValueError(detail)
            response.raise_for_status()
            return await response.json()

# ОБНОВЛЁННАЯ ФУНКЦИЯ: Теперь использует новую
async def get_shutdowns_data(city: str, street: str, house: str) -> dict:
    """
    Вызывает API-парсер и возвращает полный агрегированный JSON-ответ.
    """
    try:
        # Вызываем изолированную функцию
        return await _fetch_shutdowns_data_from_api(city, street, house)
    except aiohttp.ClientError:
        logger.error("API Connection Error during shutdown data fetch.", exc_info=True)
        raise ConnectionError("Помилка підключення до парсера. Спробуйте пізніше.")
    except asyncio.TimeoutError:
        # Отдельная обработка таймаута
        raise ConnectionError("Таймаут запроса к API. Парсер не ответил вовремя.")
    except Exception as e:
        # ... (Обработка остальных ошибок)
        if isinstance(e, aiohttp.ClientResponseError):
            raise Exception(f"API Internal Error: HTTP {e.status}")
        raise e

# --- 📌 ОБНОВЛЕНО: Фоновая задача для проверки подписок (интеграция с БД) ---
async def subscription_checker_task(bot: Bot):
    """
    Фонова задача: періодично перевіряє графік для всіх підписаних користувачів
    з бази даних, враховуючи індивідуальні інтервали.
    """
    global db_conn
    logger.info("Subscription checker started.")
    while True:
        await asyncio.sleep(CHECKER_LOOP_INTERVAL_SECONDS)
        if db_conn is None:
            logger.error("DB connection is not available. Skipping check cycle.")
            continue

        now = datetime.now() # Час в момент пробудження циклу
        users_to_check = [] # Список словарей
        try:
            # 1. Загружаем пользователей, у которых подошло время проверки
            # 📌 ИСПРАВЛЕНИЕ: Используем execute + fetchall вместо execute_fetchall
            cursor = await db_conn.execute(
                "SELECT user_id, city, street, house, interval_hours, last_schedule_hash FROM subscriptions WHERE next_check <= ?", 
                (now,)
            )
            rows = await cursor.fetchall()
            # КОНЕЦ ИСПРАВЛЕНИЯ
            if not rows:
                logger.debug("Subscription check skipped: no users require check.")
                continue

            # Преобразуем кортежи в словари для удобства
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

        # 2. Группировка пользователей по адресу (логика дедупликации API)
        addresses_to_check_map: Dict[Tuple[str, str, str], List[int]] = {}
        for sub_data in users_to_check:
            address_key = (sub_data['city'], sub_data['street'], sub_data['house'])
            if address_key not in addresses_to_check_map:
                addresses_to_check_map[address_key] = []
            addresses_to_check_map[address_key].append(sub_data['user_id'])

        logger.info(f"Checking {len(addresses_to_check_map)} unique addresses now for {len(users_to_check)} users.")

        # Локальный кеш API результатов
        api_results: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

        # 3. Запрос данных до API для каждого у уникального адреса
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

        # 4. Обробка результатів та надсилання повідомлень (с обновлением БД)
        db_updates_success = [] # (next_check, new_hash, user_id)
        db_updates_fail = [] # (next_check, user_id)

        for sub_data in users_to_check:
            user_id = sub_data['user_id']
            city = sub_data['city']
            street = sub_data['street']
            house = sub_data['house']
            address_key = (city, street, house)
            address_str = f"`{city}, {street}, {house}`"
            interval_hours = sub_data.get('interval_hours', DEFAULT_INTERVAL_HOURS)
            interval_delta = timedelta(hours=interval_hours)
            next_check_time = now + interval_delta # Новое время следующей проверки
            data_or_error = api_results.get(address_key)

            if data_or_error is None:
                logger.error(f"Address {address_key} was checked, but result is missing.")
                db_updates_fail.append((next_check_time, user_id)) # Обновляем время, пропускаем
                continue

            # 4.1. Обробка помилки API
            if "error" in data_or_error:
                error_message = data_or_error['error']
                final_message = f"❌ **Помилка перевірки** для {address_str}: {error_message}\n*Перевірка буде повторена через {f'{interval_hours:g}'.replace('.', ',')} {_pluralize_hours(interval_hours)}.*"
                try:
                    await bot.send_message(chat_id=user_id, text=final_message, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Failed to send error message to user {user_id}: {e}")

                # Обновляем только next_check
                db_updates_fail.append((next_check_time, user_id))
                continue

            # 4.2. Обробка успішного відповіді (data)
            data = data_or_error
            last_hash = sub_data.get('last_schedule_hash')
            new_hash = ADDRESS_CACHE[address_key]['last_schedule_hash']

            if new_hash != last_hash:
                # Графік змінився!
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
                    # 4.3. Обновляем хеш и next_check в БД
                    db_updates_success.append((next_check_time, new_hash, user_id))
                    logger.info(f"Notification sent to user {user_id}. Hash updated to {new_hash[:8]}.")
                except Exception as e:
                    logger.error(f"Failed to send update to user {user_id}: {e}. Hash NOT updated.")
                    # Обновляем только next_check, чтобы повторить попытку отправки
                    db_updates_fail.append((next_check_time, user_id))
            else:
                # Графік не змінився.
                logger.debug(f"User {user_id} check for {address_str}. No change in hash: {new_hash[:8]}.")
                # Обновляем только next_check
                db_updates_fail.append((next_check_time, user_id))

        try:
            # 5. Пакетное обновление БД
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
# КОНЕЦ ОБНОВЛЕННОГО БЛОКА

# --- 4. Обработчики команд (Telegram) ---
# ... (остальные обработчики команд, которые не изменились)

@dp.message(Command("start", "help")) # ИЗМЕНЕНИЕ: Регистрируем /start и /help на один хендлер
async def command_start_handler(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    if user_id not in HUMAN_USERS:
        # Запускаем проверку, если пользователь новый
        is_human = await _handle_captcha_check(message, state)
        if not is_human:
            # Если запущена проверка, то тут мы выходим, ответ уже отправлен в _handle_captcha_check
            return

    # Если пользователь уже прошел проверку (или только что прошел)
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

# --- НОВЫЙ ОБРАБОТЧИК ДЛЯ ОТВЕТА CAPTCHA ---
@dp.message(CaptchaState.waiting_for_answer, F.text.regexp(r"^\d+$"))
async def captcha_answer_handler(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    # Получаем данные из контекста
    data = await state.get_data()
    correct_answer = data.get("captcha_answer")
    try:
        user_answer = int(message.text.strip())
    except ValueError:
        # Уже отфильтровано F.text.regexp(r"^\d+$"), но на всякий случай
        user_answer = -1

    if user_answer == correct_answer:
        # Успех
        HUMAN_USERS[user_id] = True
        await state.clear()
        await message.answer(
            "✅ **Перевірка пройдена!**\n"
            "Тепер ви можете користуватися всіма функціями бота. Введіть **/start** ще раз, щоб побачити список команд.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        # Неудача. Перезапускаем проверку.
        await state.clear()
        await message.answer(
            "❌ **Неправильна відповідь.** Спробуйте ще раз, ввівши **/start**."
        )

# --- ОБРАБОТЧИКИ FSM ДЛЯ ПОШАГОВОГО ВВОДА АДРЕСА ---
@dp.message(CheckAddressState.waiting_for_city, F.text)
async def process_city(message: types.Message, state: FSMContext) -> None:
    city = message.text.strip()
    await state.update_data(city=city)
    await state.set_state(CheckAddressState.waiting_for_street)
    await message.answer(f"📝 Місто: `{city}`\n**Будь ласка, введіть назву вулиці** (наприклад, `вул. Сонячна набережна`):")

@dp.message(CheckAddressState.waiting_for_street, F.text)
async def process_street(message: types.Message, state: FSMContext) -> None:
    street = message.text.strip()
    await state.update_data(street=street)
    await state.set_state(CheckAddressState.waiting_for_house)
    await message.answer(f"📝 Вулиця: `{street}`\n**Будь ласка, введіть номер будинку** (наприклад, `6`):")

@dp.message(CheckAddressState.waiting_for_house, F.text)
async def process_house(message: types.Message, state: FSMContext) -> None:
    # 📌 ИЗМЕНЕНИЕ: Сохранение результата в БД
    global db_conn
    user_id = message.from_user.id
    house = message.text.strip()
    # await state.update_data(house=house) # Это больше не нужно, FSM будет очищен
    # Получаем полный адрес из FSM контекста
    data = await state.get_data()
    city = data.get('city', '')
    street = data.get('street', '')
    address_str = f"`{city}, {street}, {house}`"
    await message.answer(f"✅ **Перевіряю графік** для адреси: {address_str}\n⏳ Очікуйте...")

    # 📌 ИЗМЕНЕНИЕ: FSM больше не хранит last_checked_address
    # last_checked_address_old = data.get("last_checked_address")
    try:
        # Вызов API
        api_data = await get_shutdowns_data(city, street, house)
        # Обновляем хеш в FSM контексте (для команды /repeat и /subscribe)
        current_hash = _get_schedule_hash(api_data)
        # 📌 ИЗМЕНЕНИЕ: Сохраняем результат в БД
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, city, street, house, last_hash) VALUES (?, ?, ?, ?, ?)",
            (user_id, city, street, house, current_hash)
        )
        await db_conn.commit()
        # Форматирование
        response_text = format_shutdown_message(api_data)
        # 📌 Сначала очищаем FSM state...
        await state.clear()
        # 📌 ИЗМЕНЕНИЕ: Проверяем подписку в БД
        # ИСПРАВЛЕНИЕ: Используем execute + fetchone вместо execute_fetchone
        cursor = await db_conn.execute("SELECT 1 FROM subscriptions WHERE user_id = ?", (user_id,))
        is_subscribed = await cursor.fetchone()
        # КОНЕЦ ИСПРАВЛЕНИЯ
        if not is_subscribed:
            response_text += "\n💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`."
        await message.answer(response_text)
    except (ValueError, ConnectionError) as e:
        await state.clear()
        error_type = "Помилка вводу/помилка API" if isinstance(e, ValueError) else "Помилка"
        error_message = f"❌ **{error_type}:** {e}"
        # 📌 ИЗМЕНЕНИЕ: Мы больше не можем восстановить "старый" запрос из FSM
        error_message += "\n*Попередній успішний запит (якщо він був) збережено. Ви можете його повторити командою `/repeat`.*"
        await message.answer(error_message)
    except Exception as e:
        await state.clear() # 📌 ИЗМЕНЕНИЕ: Очищаем FSM в любом случае
        logger.error(f"Critical error during FSM address process for user {user_id}: {e}", exc_info=True)
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")

# --- ОБРАБОТЧИК /check ---
@dp.message(Command("check")) 
async def command_check_handler(message: types.Message, state: FSMContext) -> None:
    # 📌 ИЗМЕНЕНИЕ: Сохранение результата в БД
    global db_conn
    user_id = message.from_user.id
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await _handle_captcha_check(message, state)
        return

    text_args = message.text.replace('/check', '', 1).strip()
    if not text_args:
        # НОВАЯ ЛОГИКА: Запуск пошагового ввода
        await state.set_state(CheckAddressState.waiting_for_city)
        await message.answer("📝 **Будь ласка, введіть назву міста** (наприклад, `м. Дніпро`):")
        return

    # Выход из FSM-состояния, если оно было активно
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        # 📌 ИЗМЕНЕНИЕ: Мы больше не сохраняем last_checked_address из FSM

    await message.answer("⏳ Перевіряю графік за вказаною адресою. Очікуйте...")
    try:
        city, street, house = parse_address_from_text(text_args)
        # Вызов API
        api_data = await get_shutdowns_data(city, street, house)
        # Обновляем хеш в FSM контексте (для команды /repeat и /subscribe)
        current_hash = _get_schedule_hash(api_data)
        # 📌 ИЗМЕНЕНИЕ: Сохраняем результат в БД
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, city, street, house, last_hash) VALUES (?, ?, ?, ?, ?)",
            (user_id, city, street, house, current_hash)
        )
        await db_conn.commit()
        # Форматирование
        response_text = format_shutdown_message(api_data)
        # 📌 ИЗМЕНЕНИЕ: Проверяем подписку в БД
        # ИСПРАВЛЕНИЕ: Используем execute + fetchone вместо execute_fetchone
        cursor = await db_conn.execute("SELECT 1 FROM subscriptions WHERE user_id = ?", (user_id,))
        is_subscribed = await cursor.fetchone()
        # КОНЕЦ ИСПРАВЛЕНИЯ
        if not is_subscribed:
            response_text += "\n💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`."
        await message.answer(response_text)
    except (ValueError, ConnectionError) as e:
        error_type = "Помилка вводу/помилка API" if isinstance(e, ValueError) else "Помилка"
        error_message = f"❌ **{error_type}:** {e}"
        # 📌 ИЗМЕНЕНИЕ: Мы больше не можем восстановить "старый" запрос из FSM
        error_message += "\n*Попередній успішний запит (якщо він був) збережено. Ви можете його повторити командою `/repeat`.*"
        await message.answer(error_message)
    except Exception as e:
        logger.error(f"Critical error during check command for user {user_id}: {e}", exc_info=True)
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")

# --- ОБРАБОТЧИК /repeat ---
@dp.message(Command("repeat"))
async def command_repeat_handler(message: types.Message, state: FSMContext) -> None:
    # 📌 ИЗМЕНЕНИЕ: Загрузка адреса из БД
    global db_conn
    user_id = message.from_user.id
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await _handle_captcha_check(message, state)
        return

    # 📌 ИЗМЕНЕНИЕ: Загружаем адрес из БД
    city, street, house = None, None, None
    try:
        # ИСПРАВЛЕНИЕ: Используем execute + fetchone вместо execute_fetchone
        cursor = await db_conn.execute("SELECT city, street, house FROM user_last_check WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        # КОНЕЦ ИСПРАВЛЕНИЯ
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
        # Вызов API
        data = await get_shutdowns_data(city, street, house)
        # 📌 ИЗМЕНЕНИЕ: Обновляем хеш в БД (а не в FSM)
        current_hash = _get_schedule_hash(data)
        await db_conn.execute(
            "UPDATE user_last_check SET last_hash = ? WHERE user_id = ?", 
            (current_hash, user_id)
        )
        await db_conn.commit()
        # Форматирование
        response_text = format_shutdown_message(data)
        # 📌 ИЗМЕНЕНИЕ: Проверяем подписку в БД
        # ИСПРАВЛЕНИЕ: Используем execute + fetchone вместо execute_fetchone
        cursor = await db_conn.execute("SELECT 1 FROM subscriptions WHERE user_id = ?", (user_id,))
        is_subscribed = await cursor.fetchone()
        # КОНЕЦ ИСПРАВЛЕНИЯ
        if not is_subscribed:
            response_text += "\n💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`."
        await message.answer(response_text)
    except (ValueError, ConnectionError) as e:
        error_type = "Помилка вводу/помилка API" if isinstance(e, ValueError) else "Помилка"
        await message.answer(f"❌ **{error_type}:** {e}")
    except Exception as e:
        logger.error(f"Critical error during repeat check for user {message.from_user.id}: {e}", exc_info=True)
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")

# --- ОБРАБОТЧИК /subscribe ---
@dp.message(Command("subscribe"))
async def command_subscribe_handler(message: types.Message, state: FSMContext) -> None:
    # 📌 ИЗМЕНЕНИЕ: Загрузка адреса из БД и сохранение подписки в БД
    global db_conn
    user_id = message.from_user.id
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await _handle_captcha_check(message, state)
        return

    # --- 0. Получение адреса из БД ---
    city, street, house, hash_from_check = None, None, None, None
    try:
        # ИСПРАВЛЕНИЕ: Используем execute + fetchone вместо execute_fetchone
        cursor = await db_conn.execute("SELECT city, street, house, last_hash FROM user_last_check WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        # КОНЕЦ ИСПРАВЛЕНИЯ
        if not row:
            await message.answer("❌ **Помилка.** Спочатку вам потрібно перевірити графік за допомогою команди `/check Місто, Вулиця, Будинок`.")
            return
        city, street, house, hash_from_check = row
    except Exception as e:
        logger.error(f"Failed to fetch last_check from DB for user {user_id}: {e}")
        await message.answer("❌ **Помилка БД** при спробі знайти ваш останній запит.")
        return

    # --- 1. ОПРЕДЕЛЕНИЕ ИНТЕРВАЛА ---
    text_args = message.text.replace('/subscribe', '', 1).strip()
    interval_hours = DEFAULT_INTERVAL_HOURS # 1.0 година за замовчуванням
    if text_args:
        try:
            val = float(text_args.replace(',', '.')) # Дозволяємо кому та крапку
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

    # --- 2. Логика определения хеша (проверка существующей подписки) ---
    hash_to_use = hash_from_check
    try:
        # Проверяем, подписан ли пользователь уже на ЭТОТ адрес
        # ИСПРАВЛЕНИЕ: Используем execute + fetchone вместо execute_fetchone
        cursor = await db_conn.execute(
            "SELECT last_schedule_hash, interval_hours FROM subscriptions WHERE user_id = ? AND city = ? AND street = ? AND house = ?", 
            (user_id, city, street, house)
        )
        sub_row = await cursor.fetchone()
        # КОНЕЦ ИСПРАВЛЕНИЯ
        if sub_row:
            # Тот же адрес. Используем существующий хеш, чтобы избежать ложного уведомления.
            hash_to_use = sub_row[0] # last_schedule_hash
            # Проверка, не меняет ли он просто интервал
            if sub_row[1] == interval_hours: # interval_hours
                await message.answer(f"✅ Ви вже підписані на оновлення для адреси: `{city}, {street}, {house}` з інтервалом **{interval_display}**.")
                return
            # Если интервал меняется, продолжаем и обновляем

        # Если hash_to_use все еще None (например, last_check не вернул хеш)
        if hash_to_use is None:
            hash_to_use = "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION"

        # --- 3. Добавление/Обновление подписки в БД ---
        # Устанавливаем next_check на 'now', чтобы фоновая задача 
        # проверила адрес немедленно.
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
    # 📌 ИЗМЕНЕНИЕ: Удаление из БД
    global db_conn
    user_id = message.from_user.id
    try:
        # Сначала найдем, от чего отписываем
        # ИСПРАВЛЕНИЕ: Используем execute + fetchone вместо execute_fetchone
        cursor = await db_conn.execute("SELECT city, street, house FROM subscriptions WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        # КОНЕЦ ИСПРАВЛЕНИЯ
        if not row:
            await message.answer("❌ **Помилка.** Ви не підписані на оновлення.")
            return
        city, street, house = row
        # Удаляем
        await db_conn.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        await db_conn.commit()
        logger.info(f"User {user_id} unsubscribed from {city}, {street}, {house}.")
        await message.answer(
            f"🚫 **Підписку скасовано.** Ви більше не будете отримувати автоматичні оновлення для адреси: `{city}, {street}, {house}`.\n"
            "Ви можете підписатися знову, скориставшись командою `/subscribe` після перевірки графіку."
        )
    except Exception as e:
        logger.error(f"Failed to delete subscription from DB for user {user_id}: {e}", exc_info=True)
        await message.answer("❌ **Помилка БД** при спробі скасувати підписку.")

# --- ОБРАБОТЧИК /cancel ---
@dp.message(Command("cancel"))
async def command_cancel_handler(message: types.Message, state: FSMContext) -> None:
    # (Без изменений, FSM используется только для пошагового ввода)
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Немає активних дій для скасування.")
        return
    await state.clear()
    await message.answer("Дію скасовано. Введіть /check [адреса], щоб почати перевірку, або /check для покрокового вводу.")

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
    # 📌 ИЗМЕНЕНИЕ: Инициализация БД
    global db_conn 
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Exiting.")
        return

    # Используем DefaultBotProperties для более чистого кода
    default_properties = DefaultBotProperties(
        parse_mode="Markdown"
    )
    bot = Bot(token=BOT_TOKEN, default=default_properties)

    # 📌 ИЗМЕНЕНИЕ: Инициализируем БД перед запуском
    try:
        db_conn = await init_db(DB_PATH)
    except Exception as e:
        logger.error(f"Failed to initialize database at {DB_PATH}: {e}", exc_info=True)
        return

    # Установка команд меню
    await set_default_commands(bot)

    # Регистрация хендлеров
    dp.message.register(command_start_handler, Command("start", "help"))
    dp.message.register(command_cancel_handler, Command("cancel"))
    dp.message.register(command_check_handler, Command("check")) 
    dp.message.register(command_repeat_handler, Command("repeat"))
    dp.message.register(command_subscribe_handler, Command("subscribe")) 
    dp.message.register(command_unsubscribe_handler, Command("unsubscribe")) 

    # РЕГИСТРАЦИЯ FSM-ОБРАБОТЧИКОВ ДЛЯ АДРЕСА
    # (Они регистрируются через декораторы @dp.message(...) выше)

    # --- ДОДАНО: Запуск фонової задачі ---\
    checker_task = asyncio.create_task(subscription_checker_task(bot))
    # --- КІНЕЦЬ ДОДАНОГО БЛОКУ ---\

    logger.info("Бот запущено. Початок опитування...")
    try:
        # Запускаємо опитування бота та фонову задачу паралельно
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
    # Настраиваем логирование на более подробный уровень для отладки
    # (Вы можете изменить 'DEBUG' на 'INFO' для обычной работы)
    logger.setLevel(logging.DEBUG) 
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот зупинено вручну.")