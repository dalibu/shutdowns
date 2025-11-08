import os
import re
import asyncio
import logging
import random 
import hashlib # ДОБАВЛЕНО: для генерации хеша графика
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
HUMAN_USERS: Dict[int, bool] = {} 

# Кеш для хранения подписок. 
# Key: user_id. 
# Value: {'city': str, 'street': str, 'house': str, 'interval_hours': float, 'next_check': datetime, 'last_schedule_hash': str}
SUBSCRIPTIONS: Dict[int, Dict[str, Any]] = {} 

DEFAULT_INTERVAL_HOURS = 1.0 # ІНТЕРВАЛ ЗА ЗАМОВЧУВАННЯМ: 1 година
CHECKER_LOOP_INTERVAL_SECONDS = 5 * 60 # Фонова задача прокидається кожні 5 хвилин

# ---------------------------------------------------------


# --- 2. Вспомогательные функции (Бизнес-логика) ---

def format_minutes_to_hh_m(minutes: int) -> str:
    """Форматирует общее количество минут в HH:MM."""
    h = minutes // 60
    m = minutes % 60
    return f"{h}:{m:02d}"


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
    text = text.replace('/check', '', 1).replace('/subscribe', '', 1).replace('/unsubscribe', '', 1).replace('/repeat', '', 1).strip()
    
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
        "🚨 **Увага! Для захисту від ботів, пройдіть просту перевірку.**\n\n"
        f"**{question}**\n\n"
        "Введіть лише число-відповідь."
    )
    return False

# -----------------------------------------------------


# --- 3. Интеграция с API (Асинхронные функции) ---

async def get_shutdowns_data(city: str, street: str, house: str) -> dict:
    """
    Вызывает API-парсер и возвращает полный агрегированный JSON-ответ.
    """
    params = {
        "city": city,
        "street": street,
        "house": house
    }
    
    async with aiohttp.ClientSession() as session:
        try:
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
            
# --- ОБНОВЛЕНО: Фонова задача для перевірки підписок ---

async def subscription_checker_task(bot: Bot):
    """
    Фонова задача: періодично перевіряє графік для всіх підписаних користувачів, 
    враховуючи індивідуальні інтервали.
    """
    logger.info("Subscription checker started.")
    
    while True:
        # Прокидаємося кожні 5 хвилин, щоб перевірити, чи не настав час оновлення для когось із користувачів.
        await asyncio.sleep(CHECKER_LOOP_INTERVAL_SECONDS)
        
        if not SUBSCRIPTIONS:
            # ИЗМЕНЕНО: Уменьшаем уровень логгирования, чтобы не засорять логи
            logger.debug("Subscription check skipped: no active subscriptions.")
            continue
            
        now = datetime.now() # Час в момент пробудження циклу
        
        logger.debug(f"Starting subscription check for {len(SUBSCRIPTIONS)} users at {now.strftime('%H:%M:%S')}.")
        
        users_to_check = []
        for user_id, sub_data in SUBSCRIPTIONS.copy().items():
            
            # Якщо next_check не встановлено (наприклад, нова підписка), перевіряємо негайно.
            # Якщо час перевірки настав (next_check <= now), додаємо в чергу.
            if sub_data.get('next_check') is None or sub_data['next_check'] <= now:
                users_to_check.append((user_id, sub_data))
                
        if not users_to_check:
            logger.debug("No users require check in this cycle.")
            continue

        logger.info(f"Checking {len(users_to_check)} users now.")

        for user_id, sub_data in users_to_check:
            city = sub_data['city']
            street = sub_data['street']
            house = sub_data['house']
            address_str = f"`{city}, {street}, {house}`"
            
            interval_hours = sub_data.get('interval_hours', DEFAULT_INTERVAL_HOURS)
            interval_delta = timedelta(hours=interval_hours)
            
            # НОВОЕ: Получаем последний сохраненный хеш
            last_hash = sub_data.get('last_schedule_hash')

            try:
                # 1. Запит даних до API
                logger.debug(f"Checking API for user {user_id} ({address_str})")
                data = await get_shutdowns_data(city, street, house)
                
                # 2. НОВОЕ: Генерируем новый хеш и сравниваем с предыдущим
                new_hash = _get_schedule_hash(data)
                
                # --- ИСПРАВЛЕНИЕ ЛОГИКИ (Проблема 2 в юнит-тестах) ---
                # Обновляем next_check в любом случае (чтобы не запрашивать API каждые 5 минут)
                # Эта строка была ПРАВИЛЬНОЙ и должна быть ЗДЕСЬ, ДО if/else.
                sub_data['next_check'] = now + interval_delta
                # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

                if new_hash != last_hash:
                    # График изменился или это первая проверка!
                    
                    response_text = format_shutdown_message(data)
                    
                    # 3. Отправка сообщения пользователю
                    interval_str = f"{f'{interval_hours:g}'.replace('.', ',')} год"
                    
                    # Измененный заголовок, если это обновление
                    header = "🔔 **ОНОВЛЕННЯ ГРАФІКУ!**" if last_hash else "🔔 **Графік перевірено**"

                    final_message = (
                        f"{header} для {address_str} (інтервал {interval_str}):\n\n"
                        f"{response_text}"
                    )
                    
                    await bot.send_message(
                        chat_id=user_id, 
                        text=final_message, 
                        parse_mode="Markdown"
                    )
                    logger.info(f"Sent update to user {user_id}. Schedule changed/first check. New Hash: {new_hash[:8]}.")
                    
                    # 4. Обновление хеша (только после успешной отправки)
                    sub_data['last_schedule_hash'] = new_hash

                else:
                    # График не изменился. Просто логируем.
                    logger.info(f"User {user_id} schedule ({address_str}) has not changed. Hash: {new_hash[:8]}. Skipping notification.")
                
            except ConnectionError:
                # API не відповідає. 
                # Устанавливаем на 5 минут вперед, чтобы повторить при следующем пробуждении
                sub_data['next_check'] = now + timedelta(minutes=CHECKER_LOOP_INTERVAL_SECONDS / 60) 
                logger.warning(f"Failed to fetch data for user {user_id} ({address_str}) due to API connection error. Retrying soon.")
            
            except Exception as e:
                # Другие критические ошибки (напр. 404, если адрес стал недействительным).
                # Устанавливаем на 5 минут вперед, чтобы повторить
                sub_data['next_check'] = now + timedelta(minutes=CHECKER_LOOP_INTERVAL_SECONDS / 60)
                logger.error(f"Critical error during automated update for user {user_id} ({address_str}): {e}. Retrying soon.")

            finally:
                # Оновлюємо глобальний кеш
                # Этот блок ОБЯЗАТЕЛЕН, так как sub_data['next_check'] 
                # обновляется в любом из трех случаев (try, except, except)
                SUBSCRIPTIONS[user_id] = sub_data
                logger.debug(f"Updated next check time for user {user_id}: {sub_data.get('next_check', 'N/A').strftime('%H:%M')}")
# --- КІНЕЦЬ: Фонова задача ---


# --- 4. Обработчики команд (aiogram v3) ---

dp = Dispatcher()

# --- ОБНОВЛЕННЫЙ command_start_handler ---
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
        "👋 **Вітаю! Я бот для перевірки графіків відключень ДТЕК.**\n\n"
        "Для перевірки графіку, введіть команду **/check**, додавши адресу у форматі:\n"
        "`/check Місто, Вулиця, Будинок`\n\n"
        "**АБО** просто введіть **/check** без адреси, щоб ввести дані покроково.\n\n"
        "**Наприклад:**\n"
        "`/check м. Дніпро, вул. Сонячна набережна, 6`\n\n"
        "**Команди:**\n"
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
        HUMAN_USERS[user_id] = True
        await state.clear()
        
        logger.info(f"User {user_id} passed CAPTCHA.")
        
        await message.answer(
            "✅ **Перевірку успішно пройдено!** Тепер ви можете користуватися всіма командами.\n"
            "Введіть `/check` і вашу адресу, щоб отримати графік."
        )
    else:
        # Даем еще один шанс, но очищаем старый ответ, чтобы избежать легкого брутфорса
        await state.clear() 
        logger.warning(f"User {user_id} failed CAPTCHA. Starting over.")

        # Запускаем проверку снова с новым вопросом
        await _handle_captcha_check(message, state)


# --- ОБРАБОТЧИК НЕПРАВИЛЬНОГО ОТВЕТА CAPTCHA (не число) ---
@dp.message(CaptchaState.waiting_for_answer)
async def captcha_wrong_format_handler(message: types.Message, state: FSMContext) -> None:
    await message.answer("❌ Неправильний формат відповіді. Будь ласка, введіть **тільки число**.")

# ---------------------------------------------------------

# --- ИСПРАВЛЕНИЕ 3: (Проблема 1) ---
async def command_subscribe_handler(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        return

    data = await state.get_data()
    address_data = data.get("last_checked_address")
    
    if not address_data:
        await message.answer("❌ **Помилка.** Спочатку вам потрібно перевірити графік за допомогою команди `/check Місто, Вулиця, Будинок`.")
        return

    city = address_data['city']
    street = address_data['street']
    house = address_data['house']
    
    # --- ИСПРАВЛЕНИЕ: Получаем хеш, сохраненный во время /check
    hash_from_check = address_data.get('hash') 
    
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
    # --- КОНЕЦ: ОПРЕДЕЛЕНИЕ ИНТЕРВАЛА ---
        
    # Форматируем интервал
    hours_str = f'{interval_hours:g}'.replace('.', ',')
    interval_display = f"{hours_str} {_pluralize_hours(interval_hours)}"
    
    
    # --- ИСПРАВЛЕНИЕ: Логика определения хеша ---
    
    hash_to_use = None
    
    current_subscription = SUBSCRIPTIONS.get(user_id)
    
    if current_subscription:
        # Пользователь уже подписан. Проверяем, это тот же адрес?
        is_same_address = (
            current_subscription['city'] == city and
            current_subscription['street'] == street and
            current_subscription['house'] == house
        )
        
        if is_same_address:
            # Тот же адрес (возможно, меняет интервал). 
            # Используем существующий хеш, чтобы избежать ложного уведомления.
            hash_to_use = current_subscription.get('last_schedule_hash')
            
            # Проверка, не меняет ли он просто интервал
            if current_subscription['interval_hours'] == interval_hours:
                await message.answer(f"✅ Ви вже підписані на оновлення для адреси: `{city}, {street}, {house}` з інтервалом **{interval_display}**.")
                return
        else:
            # Новый адрес. Используем хеш из FSM (от /check).
            hash_to_use = hash_from_check
    else:
        # Новый подписчик. Используем хеш из FSM (от /check).
        hash_to_use = hash_from_check

    # --- КОНЕЦ ИСПРАВЛЕНИЯ ЛОГИКИ ХЕША ---

    # Подписываем/Обновляем пользователя
    SUBSCRIPTIONS[user_id] = {
        'city': city,
        'street': street,
        'house': house,
        'interval_hours': interval_hours,
        # Устанавливаем next_check на текущее время, чтобы проверка запустилась при первом же пробуждении checker_task
        'next_check': datetime.now(), 
        # ИСПОЛЬЗУЕМ ВЫБРАННЫЙ ХЕШ
        'last_schedule_hash': hash_to_use
    }
    
    # Безопасное логгирование
    hash_display = hash_to_use[:8] if hash_to_use else 'None'
    logger.info(f"User {user_id} subscribed to {city}, {street}, {house} with interval {interval_hours}h. Hash initialized: {hash_display}")
    
    await message.answer(
        f"🔔 **Успіх!** Ви підписалися на автоматичні оновлення графіку для адреси: `{city}, {street}, {house}`.\n"
        f"Інтервал перевірки: **{interval_display}**.\n"
        "*Ви будете отримувати повідомлення лише у випадку, якщо графік відключень зміниться.*\n"
        "Щоб скасувати підписку, скористайтеся командою `/unsubscribe`."
    )
# --- КОНЕЦ ИСПРАВЛЕНИЯ 3 ---


async def command_unsubscribe_handler(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        return
        
    if user_id not in SUBSCRIPTIONS:
        await message.answer("❌ **Помилка.** Ви не підписані на оновлення.")
        return

    # Удаляем безопасно
    address_data = SUBSCRIPTIONS.pop(user_id, {})
    city = address_data.get('city', 'Н/Д')
    street = address_data.get('street', 'Н/Д')
    house = address_data.get('house', 'Н/Д')
    
    logger.info(f"User {user_id} unsubscribed from {city}, {street}, {house}.")
    
    await message.answer(
        f"🚫 **Підписку скасовано.** Ви більше не будете отримувати автоматичні оновлення для адреси: `{city}, {street}, {house}`.\n"
        "Ви можете підписатися знову, скориставшись командою `/subscribe` після перевірки графіку."
    )


async def command_cancel_handler(message: types.Message, state: FSMContext) -> None:
    # Добавляем очистку FSM состояния при отмене
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Немає активних дій для скасування.")
        return
        
    await state.clear()
    await message.answer("Дію скасовано. Введіть /check [адреса], щоб почати перевірку, або /check для покрокового вводу.")


# --- ИСПРАВЛЕНИЕ 1: (Проблема 1) ---
# ОБНОВЛЕННЫЙ command_check_handler
async def command_check_handler(message: types.Message, state: FSMContext) -> None:
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
        return # Выход, ждем ввода города

    # СУЩЕСТВУЮЩАЯ ЛОГИКА: Прямой ввод адреса через запятую
    try:
        city, street, house = parse_address_from_text(text_args)
        
        await message.answer("⏳ Перевіряю графік. Очікуйте...")

        # Вызов API
        data = await get_shutdowns_data(city, street, house)
        
        # --- ИСПРАВЛЕНИЕ: Рассчитываем и сохраняем хеш ---
        current_hash = _get_schedule_hash(data)
        address_data = {'city': city, 'street': street, 'house': house, 'hash': current_hash}
        await state.update_data(last_checked_address=address_data)
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
        
        # Форматирование
        response_text = format_shutdown_message(data)
        
        # Пропозиція про підписку
        if user_id not in SUBSCRIPTIONS:
             response_text += "\n\n💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`."

        await message.answer(response_text) 

    except ValueError as e:
        await message.answer(f"❌ **Помилка вводу/помилка API:** {e}")
    except ConnectionError as e:
        await message.answer(f"❌ **Помилка:** {e}")
    except Exception as e:
        logger.error(f"Critical error during parsing for user {message.from_user.id}: {e}")
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")


# --- НОВЫЙ ОБРАБОТЧИК ДЛЯ /repeat ---
async def command_repeat_handler(message: types.Message, state: FSMContext) -> None:
    """
    Повторяет последнюю успешную проверку /check, используя адрес из FSMContext.
    """
    user_id = message.from_user.id

    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await _handle_captcha_check(message, state)
        return

    data = await state.get_data()
    address_data = data.get("last_checked_address")

    if not address_data:
        await message.answer("❌ **Помилка.** Спочатку вам потрібно перевірити графік за допомогою команди `/check Місто, Вулиця, Будинок`.")
        return

    city = address_data['city']
    street = address_data['street']
    house = address_data['house']
    address_str = f"`{city}, {street}, {house}`"

    await message.answer(f"🔄 **Повторюю перевірку** для адреси: {address_str}\n\n⏳ Очікуйте...")

    try:
        # Вызов API
        data = await get_shutdowns_data(city, street, house)
        
        # --- ИСПРАВЛЕНИЕ (для /repeat -> /subscribe): Также обновляем хеш в FSM ---
        current_hash = _get_schedule_hash(data)
        new_address_data = {'city': city, 'street': street, 'house': house, 'hash': current_hash}
        await state.update_data(last_checked_address=new_address_data)
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
        
        # Форматирование
        response_text = format_shutdown_message(data)
        
        # Пропозиція про підписку
        if user_id not in SUBSCRIPTIONS:
             response_text += "\n\n💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`."
        
        await message.answer(response_text) 

    except ValueError as e:
        await message.answer(f"❌ **Помилка вводу/помилка API:** {e}")
    except ConnectionError as e:
        await message.answer(f"❌ **Помилка:** {e}")
    except Exception as e:
        logger.error(f"Critical error during repeat check for user {message.from_user.id}: {e}")
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")


# --- ДОБАВЛЕННЫЕ ОБРАБОТЧИКИ FSM ДЛЯ ПОШАГОВОГО ВВОДА АДРЕСА ---

@dp.message(CheckAddressState.waiting_for_city, F.text)
async def process_city(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод города и запрашивает улицу."""
    await state.update_data(city=message.text.strip())
    await state.set_state(CheckAddressState.waiting_for_street)
    await message.answer("📝 **Тепер введіть назву вулиці** (наприклад, `вул. Сонячна набережна`):")

@dp.message(CheckAddressState.waiting_for_street, F.text)
async def process_street(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод улицы и запрашивает номер дома."""
    await state.update_data(street=message.text.strip())
    await state.set_state(CheckAddressState.waiting_for_house)
    await message.answer("📝 **Нарешті, введіть номер будинку** (наприклад, `6`):")

# --- ИСПРАВЛЕНИЕ 2: (Проблема 1) ---
# ОБНОВЛЕННЫЙ process_house
@dp.message(CheckAddressState.waiting_for_house, F.text)
async def process_house(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод номера дома, выполняет проверку и завершает FSM."""
    
    # 1. Получаем все данные
    await state.update_data(house=message.text.strip())
    data = await state.get_data()
    
    city = data.get('city')
    street = data.get('street')
    house = data.get('house')
    user_id = message.from_user.id
    
    # 📌 ИСПРАВЛЕНИЕ: Сохраняем предыдущий адрес на случай сбоя
    last_checked_address_old = data.get('last_checked_address')
    
    # 2. Проверка, что все поля есть (на всякий случай)
    if not all([city, street, house]):
         await message.answer("❌ **Помилка.** Не вдалося отримати повну адресу. Спробуйте ще раз, набравши `/check`.")
         await state.clear()
         return

    # 3. Выполняем проверку
    await message.answer("⏳ Перевіряю графік. Очікуйте...")

    try:
        # Вызов API
        api_data = await get_shutdowns_data(city, street, house)
        
        # --- ИСПРАВЛЕНИЕ: Рассчитываем хеш ---
        current_hash = _get_schedule_hash(api_data)
        address_data = {'city': city, 'street': street, 'house': house, 'hash': current_hash}
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
        
        # Форматирование
        response_text = format_shutdown_message(api_data)
        
        # 📌 Сначала очищаем FSM state...
        await state.clear()
        # 📌 ...затем сохраняем только last_checked_address (с хешем)
        await state.update_data(last_checked_address=address_data)
        
        # Пропозиція про підписку
        if user_id not in SUBSCRIPTIONS:
             response_text += "\n\n💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`."

        await message.answer(response_text) 

    except ValueError as e:
        await state.clear()
        error_message = f"❌ **Помилка вводу/помилка API:** {e}"
        if last_checked_address_old:
             await state.update_data(last_checked_address=last_checked_address_old)
             error_message += "\n\n*Попередній успішний запит збережено. Ви можете його повторити командою `/repeat`.*"
        await message.answer(error_message) 
        
    except ConnectionError as e:
        await state.clear()
        error_message = f"❌ **Помилка:** {e}"
        if last_checked_address_old:
             await state.update_data(last_checked_address=last_checked_address_old)
             error_message += "\n\n*Попередній успішний запит збережено. Ви можете його повторити командою `/repeat`.*"
        await message.answer(error_message)
        
    except Exception as e:
        logger.error(f"Critical error during FSM check for user {user_id}: {e}")
        await state.clear()
        error_message = f"❌ Виникла непередбачена помилка. Спробуйте пізніше."
        if last_checked_address_old:
             await state.update_data(last_checked_address=last_checked_address_old)
             error_message += "\n\n*Попередній успішний запит збережено. Ви можете його повторити командою `/repeat`.*"
        await message.answer(error_message)

# --- КОНЕЦ ДОБАВЛЕННЫХ ОБРАБОТЧИКОВ FSM ---


# --- 5. Main Execution ---

async def main() -> None:
    """Главная функция для запуска бота."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не встановлено. Перевірте змінні оточення.")
        return
    
    default_props = DefaultBotProperties(parse_mode="Markdown")
    bot = Bot(BOT_TOKEN, default=default_props) 
    
    commands = [
        BotCommand(command="check", description="Перевірити графік за адресою (покроково або /check Місто,...)"),
        BotCommand(command="repeat", description="Повторити останню перевірку /check"),
        BotCommand(command="subscribe", description="Підписатися на оновлення (опціонально: /subscribe 3)"), 
        BotCommand(command="unsubscribe", description="Скасувати підписку"), 
        BotCommand(command="cancel", description="Скасувати поточну дію"),
        BotCommand(command="help", description="Довідка")
    ]
    await bot.set_my_commands(commands)
    
    # РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ
    dp.message.register(command_start_handler, Command("start", "help"))
    # Регистрация captcha_answer_handler происходит декоратором
    dp.message.register(command_cancel_handler, Command("cancel"))
    dp.message.register(command_check_handler, Command("check")) 
    dp.message.register(command_repeat_handler, Command("repeat"))
    dp.message.register(command_subscribe_handler, Command("subscribe")) 
    dp.message.register(command_unsubscribe_handler, Command("unsubscribe")) 
    
    # РЕГИСТРАЦИЯ FSM-ОБРАБОТЧИКОВ ДЛЯ АДРЕСА
    # (Они регистрируются через декораторы @dp.message(...) выше)
    
    # --- ДОДАНО: Запуск фонової задачі ---
    checker_task = asyncio.create_task(subscription_checker_task(bot))
    # --- КІНЕЦЬ ДОДАНОГО БЛОКУ ---
    
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
        await bot.session.close()


if __name__ == "__main__":
    # Настраиваем логирование на более подробный уровень для отладки
    # (Вы можете изменить 'DEBUG' на 'INFO' для обычной работы)
    logger.setLevel(logging.DEBUG) 
    
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот зупинено.")
    except Exception as e:
        logger.critical(f"Критична помилка виконання: {e}", exc_info=True)