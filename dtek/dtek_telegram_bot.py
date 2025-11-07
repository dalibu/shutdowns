import os
import re
import asyncio
import logging
import random 
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
# Value: {'city': str, 'street': str, 'house': str, 'interval_hours': float, 'next_check': datetime}
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
    Консолидирует слоты отключений и возвращает строку со временем ИЛИ статус "немає".
    """
    outage_slots = [s for s in slots if s.get('disconection') in ('full', 'half')]
    
    if not outage_slots:
        return "Відключення не заплановані" 

    first_slot = outage_slots[0]
    last_slot = outage_slots[-1]

    # --- Расчет времени начала отключения ---
    try:
        time_parts_start = re.split(r'\s*[-\bi\–]\s*', first_slot.get('time', '0-0'))
        start_hour = int(time_parts_start[0])
        
        if first_slot.get('disconection') == 'full':
            outage_start_min = start_hour * 60 
        else:
            outage_start_min = start_hour * 60 + 30
    except Exception:
        return "Помилка парсингу часу початку"

    # --- Расчет времени конца отключения ---
    try:
        time_parts_end = re.split(r'\s*[-\bi\–]\s*', last_slot.get('time', '0-0'))
        end_hour = int(time_parts_end[1])
        
        if last_slot.get('disconection') == 'full':
            outage_end_min = end_hour * 60
        else: 
            outage_end_min = end_hour * 60 - 30

    except Exception:
        return "Помилка парсингу часу кінця"
        
    if outage_start_min >= outage_end_min:
         return "Відключення не заплановані (або помилка часу)"

    start_time_final = format_minutes_to_hh_m(outage_start_min)
    end_time_final = format_minutes_to_hh_m(outage_end_min)
    duration_str = _get_shutdown_duration_str(start_time_final, end_time_final)
    
    return f"{start_time_final} - {end_time_final} ({duration_str})"


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

def _get_shutdown_duration_str(start_time_str: str, end_time_str: str) -> str:
    """
    Рассчитывает продолжительность отключения (в часах) и возвращает форматированную строку
    с правильным склонением: '(X [година/години/годин])'.
    """
    def time_to_minutes(time_str: str) -> int:
        # Парсинг времени в формате 'HH:MM'
        h, m = map(int, time_str.split(':'))
        return h * 60 + m

    try:
        start_minutes = time_to_minutes(start_time_str)
        end_minutes = time_to_minutes(end_time_str)
        
        duration_minutes = end_minutes - start_minutes
        
        if duration_minutes < 0:
             # Ночь, переход через полночь
             duration_minutes += 24 * 60
        elif duration_minutes == 0:
             # Если время начала и конца совпадает, это полные сутки (24 часа)
             duration_minutes = 24 * 60 

        duration_hours = duration_minutes / 60.0
        
        # Форматирование: 1.0 -> '1', 2.5 -> '2,5'. Используем запятую.
        if duration_hours % 1 == 0:
            hours_str = str(int(duration_hours))
        else:
            hours_str = f"{duration_hours:g}".replace('.', ',')
        
        plural_form = _pluralize_hours(duration_hours)
        
        # Обновленный лаконичный формат:
        return f"{hours_str} {plural_form}"
        
    except Exception:
        return "?" # Упрощенный резервный вариант


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
            
# --- ДОДАНО: Фонова задача для перевірки підписок ---

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
            logger.info("Subscription check skipped: no active subscriptions.")
            continue
            
        now = datetime.now() # Час в момент пробудження циклу
        
        logger.info(f"Starting subscription check for {len(SUBSCRIPTIONS)} users.")
        
        users_to_check = []
        for user_id, sub_data in SUBSCRIPTIONS.copy().items():
            
            # Якщо next_check не встановлено (наприклад, нова підписка), перевіряємо негайно.
            # Якщо час перевірки настав (next_check <= now), додаємо в чергу.
            if sub_data.get('next_check') is None or sub_data['next_check'] <= now:
                users_to_check.append((user_id, sub_data))
                
        if not users_to_check:
            logger.info("No users require check in this cycle.")
            continue

        logger.info(f"Checking {len(users_to_check)} users now.")

        for user_id, sub_data in users_to_check:
            city = sub_data['city']
            street = sub_data['street']
            house = sub_data['house']
            address_str = f"`{city}, {street}, {house}`"
            
            interval_hours = sub_data.get('interval_hours', DEFAULT_INTERVAL_HOURS)
            interval_delta = timedelta(hours=interval_hours)

            try:
                # 1. Запит даних до API
                data = await get_shutdowns_data(city, street, house)
                response_text = format_shutdown_message(data)
                
                # 2. Відправка повідомлення користувачу
                interval_str = f"{f'{interval_hours:g}'.replace('.', ',')} год"
                final_message = f"🔔 **Автоматичне оновлення графіку** для {address_str} (кожні {interval_str}):\n\n{response_text}"
                
                await bot.send_message(
                    chat_id=user_id, 
                    text=final_message, 
                    parse_mode="Markdown"
                )
                logger.info(f"Sent update to user {user_id}. Interval: {interval_hours}h.")
                
                # 3. Оновлення next_check (якщо запит успішний)
                sub_data['next_check'] = now + interval_delta

            except ConnectionError:
                # API не відповідає. Не оновлюємо next_check, щоб спробувати пізніше.
                logger.warning(f"Failed to fetch data for user {user_id} ({address_str}) due to API connection error. Retrying soon.")
            
            except Exception as e:
                # Інші критичні помилки. Не оновлюємо next_check, щоб спробувати пізніше.
                logger.error(f"Critical error during automated update for user {user_id} ({address_str}): {e}. Retrying soon.")

            finally:
                # Оновлюємо глобальний кеш (навіть якщо була помилка і next_check не оновився, 
                # ми зберігаємо дані підписки)
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
        "**АБО** просто введіть **/check** без адреси, щоб ввести дані покроково.\n\n" # ОБНОВЛЕНО
        "**Наприклад:**\n"
        "`/check м. Дніпро, вул. Сонячна набережна, 6`\n\n"
        "**Команди:**\n"
        "/check - перевірити графік за адресою.\n"
        "/repeat - *НОВЕ!* Повторити останню перевірку /check.\n"
        "/subscribe - підписатися на оновлення (за замовчуванням 1 година).\n"
        "  *Приклад: `/subscribe 3` (кожні 3 години) або `/subscribe 0.5` (кожні 30 хв)*\n"
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
    
    # --- 1. ВИЗНАЧЕННЯ ІНТЕРВАЛУ ---
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
    # --- КІНЕЦЬ: ВИЗНАЧЕННЯ ІНТЕРВАЛУ ---
        
    # Форматуємо інтервал для виведення
    # Використовуємо спрощене схиляння для відображення
    hours_str = f'{interval_hours:g}'.replace('.', ',')
    if interval_hours == 1:
        interval_plural = 'годину'
    elif interval_hours % 1 == 0 and 2 <= interval_hours % 10 <= 4 and interval_hours % 100 not in [11, 12, 13, 14]:
         interval_plural = 'години'
    else:
        interval_plural = 'годин'
        
    interval_display = f"{hours_str} {interval_plural}"

    # Перевірка на вже існуючу підписку з тим же інтервалом
    is_same_subscription = (
        user_id in SUBSCRIPTIONS and 
        SUBSCRIPTIONS[user_id]['city'] == city and
        SUBSCRIPTIONS[user_id]['street'] == street and
        SUBSCRIPTIONS[user_id]['house'] == house and
        SUBSCRIPTIONS[user_id]['interval_hours'] == interval_hours
    )
    
    if is_same_subscription:
        await message.answer(f"✅ Ви вже підписані на оновлення для адреси: `{city}, {street}, {house}` з інтервалом **{interval_display}**.")
        return

    # Підписуємо/Оновлюємо користувача
    SUBSCRIPTIONS[user_id] = {
        'city': city,
        'street': street,
        'house': house,
        'interval_hours': interval_hours,
        # Встановлюємо next_check на поточний час, щоб перевірка запустилася при першому ж пробудженні checker_task
        'next_check': datetime.now()
    }
    
    logger.info(f"User {user_id} subscribed to {city}, {street}, {house} with interval {interval_hours}h.")
    
    await message.answer(
        f"🔔 **Успіх!** Ви підписалися на автоматичні оновлення графіку для адреси: `{city}, {street}, {house}`.\n"
        f"Інтервал перевірки: **{interval_display}**.\n"
        "Щоб скасувати підписку, скористайтеся командою `/unsubscribe`."
    )


async def command_unsubscribe_handler(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        return
        
    if user_id not in SUBSCRIPTIONS:
        await message.answer("❌ **Помилка.** Ви не підписані на оновлення.")
        return

    address_data = SUBSCRIPTIONS.pop(user_id)
    city = address_data['city']
    street = address_data['street']
    house = address_data['house']
    
    logger.info(f"User {user_id} unsubscribed from {city}, {street}, {house}.")
    
    await message.answer(
        f"🚫 **Підписку скасовано.** Ви більше не будете отримувати автоматичні оновлення для адреси: `{city}, {street}, {house}`.\n"
        "Ви можете підписатися знову, скориставшись командою `/subscribe` після перевірки графіку."
    )


async def command_cancel_handler(message: types.Message, state: FSMContext) -> None:
    # Добавляем очистку FSM состояния при отмене
    await state.clear()
    await message.answer("Поточний ввід адреси неактивний. Введіть /check [адреса], щоб почати перевірку, або /check для покрокового вводу.")


# --- ОБНОВЛЕННЫЙ command_check_handler ---
async def command_check_handler(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        # Также можем попробовать сразу запустить проверку
        await _handle_captcha_check(message, state)
        return
    
    text_args = message.text.replace('/check', '', 1).strip()
    
    if not text_args:
        # НОВАЯ ЛОГИКА: Запуск пошагового ввода
        await state.set_state(CheckAddressState.waiting_for_city)
        await message.answer("📝 **Будь ласка, введіть назву міста** (наприклад, `м. Київ`):")
        return # Выход, ждем ввода города

    # СУЩЕСТВУЮЩАЯ ЛОГИКА: Прямой ввод адреса через запятую
    try:
        city, street, house = parse_address_from_text(text_args)
        
        # --- Сохранение последней адрес для подписки в FSMContext ---
        address_data = {'city': city, 'street': street, 'house': house}
        await state.update_data(last_checked_address=address_data)
        # --- КОНЕЦ ЛОГИКИ СОХРАНЕНИЯ ---
        
        await message.answer("⏳ Перевіряю графік. Це може зайняти декілька секунд...")

        # Вызов API
        data = await get_shutdowns_data(city, street, house)
        
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
    await message.answer("📝 **Тепер введіть назву вулиці** (наприклад, `вул. Хрещатик`):")

@dp.message(CheckAddressState.waiting_for_street, F.text)
async def process_street(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод улицы и запрашивает номер дома."""
    await state.update_data(street=message.text.strip())
    await state.set_state(CheckAddressState.waiting_for_house)
    await message.answer("📝 **Нарешті, введіть номер будинку** (наприклад, `2`):")

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
    
    # 2. Проверка, что все поля есть (на всякий случай)
    if not all([city, street, house]):
         await message.answer("❌ **Помилка.** Не вдалося отримати повну адресу. Спробуйте ще раз, набравши `/check`.")
         await state.clear()
         return

    # 3. Выполняем проверку
    await message.answer("⏳ Перевіряю графік. Це може зайняти декілька секунд...")

    try:
        # --- Сохранение адреса для /repeat и /subscribe (временное сохранение) ---
        address_data = {'city': city, 'street': street, 'house': house}
        # --- КОНЕЦ ЛОГИКИ СОХРАНЕНИЯ ---
        
        # Вызов API
        api_data = await get_shutdowns_data(city, street, house)
        
        # Форматирование
        response_text = format_shutdown_message(api_data)
        
        # 📌 ИСПРАВЛЕНИЕ: Сначала очищаем FSM state, затем сохраняем только last_checked_address
        # Это гарантирует, что last_checked_address не будет очищен вместе с временными city/street/house
        # данными текущего состояния.
        await state.clear()
        await state.update_data(last_checked_address=address_data)
        # -------------------------------------------------------------------------
        
        # Пропозиція про підписку
        if user_id not in SUBSCRIPTIONS:
             response_text += "\n\n💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`."

        await message.answer(response_text) 

    except ValueError as e:
        await message.answer(f"❌ **Помилка вводу/помилка API:** {e}")
        await state.clear()
    except ConnectionError as e:
        await message.answer(f"❌ **Помилка:** {e}")
        await state.clear()
    except Exception as e:
        logger.error(f"Critical error during FSM check for user {user_id}: {e}")
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")
        await state.clear()

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
        BotCommand(command="check", description="Перевірити графік за адресою (покроково або /check Місто,...)"), # ОБНОВЛЕНО
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
    dp.message.register(process_city, CheckAddressState.waiting_for_city, F.text)
    dp.message.register(process_street, CheckAddressState.waiting_for_street, F.text)
    dp.message.register(process_house, CheckAddressState.waiting_for_house, F.text)

    # --- ДОДАНО: Запуск фонової задачі ---
    checker_task = asyncio.create_task(subscription_checker_task(bot))
    # --- КІНЕЦЬ ДОДАНОГО БЛОКУ ---\
    
    logger.info("Бот запущено. Початок опитування...")
    
    # Запускаємо опитування бота та фонову задачу паралельно
    await asyncio.gather(
        dp.start_polling(bot),
        checker_task,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот зупинено вручну.")
    except Exception as e:
        logger.critical(f"Критична помилка виконання: {e}")