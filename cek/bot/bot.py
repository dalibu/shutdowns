"""
CEK Telegram Bot - Independent bot for CEK power shutdown schedules.
Uses common library and calls CEK parser directly with group caching optimization.
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BotCommand, ReplyKeyboardRemove, BufferedInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
import pytz

# Import from common library
from common.bot_base import (
    init_db,
    CaptchaState,
    CheckAddressState,
    HUMAN_USERS,
    ADDRESS_CACHE,
    SCHEDULE_DATA_CACHE,
    DEFAULT_INTERVAL_HOURS,
    CHECKER_LOOP_INTERVAL_SECONDS,
    parse_address_from_text,
    get_schedule_hash_compact,
    get_captcha_data,
    get_hours_str,
    get_shutdown_duration_str_by_hours,
    update_user_activity,
    format_user_info,
)
from common.formatting import (
    process_single_day_schedule_compact,
    get_current_status_message,
)
from common.formatting import (
    build_subscription_exists_message,
    build_subscription_created_message,
)
from common.visualization import (
    generate_24h_schedule_image,
)
from common.formatting import merge_consecutive_slots
from common.visualization import generate_48h_schedule_image

# Import Data Source Factory
from cek.data_source import get_data_source

# --- Configuration ---
PROVIDER = "ЦЕК"
BOT_TOKEN = os.getenv("CEK_BOT_TOKEN")
DB_PATH = os.getenv("CEK_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "bot.db"))
FONT_PATH = os.getenv("CEK_FONT_PATH", os.path.join(os.path.dirname(__file__), "..", "resources", "DejaVuSans.ttf"))

# Logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False  # Отключаем дублирование логов
handler = logging.StreamHandler()
formatter = logging.Formatter(
    'cek_bot | %(levelname)s:%(name)s:%(message)s',
    datefmt='%H:%M:%S'
)
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)

# Dispatcher
dp = Dispatcher()
db_conn = None

# --- Helper Functions ---
async def _handle_captcha_check(message: types.Message, state: FSMContext) -> bool:
    """Проверяет, прошел ли пользователь CAPTCHA. Возвращает True, если прошел."""
    user_id = message.from_user.id
    if user_id in HUMAN_USERS:
        return True

    await state.set_state(CaptchaState.waiting_for_answer)
    question, correct_answer = get_captcha_data()
    await state.update_data(captcha_answer=correct_answer)
    await message.answer(
        "⚠️ **Увага! Для захисту від ботів, пройдіть просту перевірку.**\n"
        f"**{question}**\n"
        "Введіть лише число-відповідь."
    )
    return False

async def get_shutdowns_data(city: str, street: str, house: str, cached_group: str = None) -> dict:
    """Отримує дані через абстракцію DataSource."""
    try:
        source = get_data_source()
        return await source.get_schedule(city, street, house, cached_group=cached_group)
    except Exception as e:
        logger.error(f"Data source error: {e}", exc_info=True)
        error_str = str(e)
        if "Could not determine group for address" in error_str:
            # Extract address from error message if possible, or just use the input args
            raise ValueError(f"Не вдалося отримати групу для адреси: {city}, {street}, {house}")
        raise ValueError(f"Не вдалося отримати графік для адреси. Помилка: {error_str[:100]}")

async def send_schedule_response(message: types.Message, api_data: dict, is_subscribed: bool):
    """
    Отправляет пользователю форматированный ответ с графиком ЦЕК.
    """
    try:
        # 1. Отправляем "шапку"
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

        # 3. Генерация диаграммы (24h или 48h) - унифицировано с DTEK
        has_shutdowns_tomorrow = False
        if len(sorted_dates) >= 2:
            tomorrow_date = sorted_dates[1]
            if schedule.get(tomorrow_date):
                has_shutdowns_tomorrow = True
        
        image_data = None
        caption = ""
        filename = ""

        if has_shutdowns_tomorrow:
            # Если есть отключения на завтра -> 48 часов
            all_slots_48h = {}
            for date in sorted_dates[:2]:
                all_slots_48h[date] = schedule.get(date, [])

            if any(slots for slots in all_slots_48h.values()):
                image_data = generate_48h_schedule_image(all_slots_48h, FONT_PATH, current_time=datetime.now(pytz.timezone('Europe/Kiev')))
                caption = "🕙 **Загальний графік на 48 годин**:"
                filename = "schedule_48h.png"
        else:
            # Если нет отключений на завтра -> 24 часа (только сегодня)
            if sorted_dates:
                today_date = sorted_dates[0]
                today_slots = {today_date: schedule.get(today_date, [])}
                if schedule.get(today_date):
                    image_data = generate_24h_schedule_image(today_slots, FONT_PATH, current_time=datetime.now(pytz.timezone('Europe/Kiev')))
                    caption = "🕙 **Графік на сьогодні**:"
                    filename = "schedule_24h.png"

        if image_data:
            await message.answer(caption)
            image_file = BufferedInputFile(image_data, filename=filename)
            await message.answer_photo(photo=image_file)

        # 4. Цикл по дням (текст) - показываем все дни, как в DTEK
        for date in sorted_dates:
            slots = schedule.get(date, [])
            day_text = process_single_day_schedule_compact(date, slots, PROVIDER)
            if day_text and day_text.strip():
                await message.answer(day_text.strip())

        # 5. Добавляем сообщение о текущем статусе
        status_msg = get_current_status_message(schedule)
        if status_msg:
            await message.answer(status_msg)

        # 6. Отправляем "подвал"
        if not is_subscribed:
            await message.answer("💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`.")
    
    except Exception as e:
        logger.error(f"Error in send_schedule_response for user {message.from_user.id}: {e}", exc_info=True)
        await message.answer("❌ Сталася помилка під час формування відповіді.")

# --- Background Tasks ---
async def subscription_checker_task(bot: Bot):
    """Фонова задача: періодично перевіряє графік для всіх підписаних користувачів."""
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
                logger.debug(f"Calling parser for address {address_str}")
                # Try to get cached group for CEK optimization
                cached_group = None
                try:
                    cursor_group = await db_conn.execute(
                        "SELECT group_name FROM subscriptions WHERE city = ? AND street = ? AND house = ? LIMIT 1",
                        (city, street, house)
                    )
                    row_group = await cursor_group.fetchone()
                    if row_group and row_group[0]:
                        cached_group = row_group[0]
                        logger.info(f"Using cached group for subscription check: {cached_group}")
                except Exception:
                    pass
                
                data = await get_shutdowns_data(city, street, house, cached_group)
                current_hash = get_schedule_hash_compact(data)
                ADDRESS_CACHE[address_key] = {
                    'last_schedule_hash': current_hash,
                    'last_checked': now
                }
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
                final_message = f"❌ **Помилка перевірки** для {address_str}: {error_message}\n*Перевірка буде повторена через {f'{interval_hours:g}'.replace('.', ',')} {get_hours_str(interval_hours)}.*"
                try:
                    await bot.send_message(chat_id=user_id, text=final_message, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Failed to send error message to user {user_id}: {e}")

                db_updates_fail.append((next_check_time, user_id))
                continue

            data = data_or_error
            last_hash = sub_data.get('last_schedule_hash')
            new_hash = ADDRESS_CACHE[address_key]['last_schedule_hash']

            # Проверяем, есть ли реальные изменения в расписании
            schedule = data.get("schedule", {})
            has_actual_schedule = any(slots for slots in schedule.values() if slots)
            
            if new_hash != last_hash and (has_actual_schedule or last_hash not in (None, "NO_SCHEDULE_FOUND", "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION")):
                group = data.get("group", "Н/Д")
                
                header_msg = (
                    f"🏠 Адреса: `{city}, {street}, {house}`\n"
                    f"👥 Черга: `{group}`"
                )
                interval_str = f"{f'{interval_hours:g}'.replace('.', ',')} год"
                update_header = "🔔 **ОНОВЛЕННЯ ГРАФІКУ!**" if last_hash not in (None, "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION") else "🔔 **Графік перевірено**"
                
                await bot.send_message(
                    chat_id=user_id,
                    text=f"{update_header}\nдля {address_str} (інтервал {interval_str}):\n{header_msg}",
                    parse_mode="Markdown"
                )

                try:
                    sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
                except ValueError:
                    sorted_dates = sorted(schedule.keys())

                # Генерация диаграммы (24h или 48h) - унифицировано с DTEK
                has_shutdowns_tomorrow = False
                if len(sorted_dates) >= 2:
                    tomorrow_date = sorted_dates[1]
                    if schedule.get(tomorrow_date):
                        has_shutdowns_tomorrow = True

                image_data = None
                caption = ""
                filename = ""

                if has_shutdowns_tomorrow:
                    days_slots_48h = {}
                    for date in sorted_dates[:2]:
                        days_slots_48h[date] = schedule.get(date, [])
                    if any(slots for slots in days_slots_48h.values()):
                        image_data = generate_48h_schedule_image(days_slots_48h, FONT_PATH, current_time=datetime.now(pytz.timezone('Europe/Kiev')))
                        caption = "🕙 **Загальний графік на 48 годин**:"
                        filename = "schedule_48h_update.png"
                else:
                    if sorted_dates:
                        today_date = sorted_dates[0]
                        today_slots = {today_date: schedule.get(today_date, [])}
                        if schedule.get(today_date):
                            image_data = generate_24h_schedule_image(today_slots, FONT_PATH, current_time=datetime.now(pytz.timezone('Europe/Kiev')))
                            caption = "🕙 **Графік на сьогодні**:"
                            filename = "schedule_24h_update.png"

                if image_data:
                    await bot.send_message(chat_id=user_id, text=caption)
                    image_file = BufferedInputFile(image_data, filename=filename)
                    await bot.send_photo(chat_id=user_id, photo=image_file)

                # Текстовые данные по дням - отправляем все дни
                for date in sorted_dates:
                    slots = schedule[date]
                    day_text = process_single_day_schedule_compact(date, slots, PROVIDER)
                    if not day_text or not day_text.strip():
                        continue
                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=day_text.strip(),
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Failed to send update message to user {user_id}: {e}")

                # Статусное сообщение
                status_msg = get_current_status_message(schedule)
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

async def _process_alert_for_user(bot: Bot, user_id: int, city: str, street: str, house: str, lead_time: int, last_alert_event_start_str: str, now: datetime) -> str:
    """Проверяет, нужно ли отправить алерт пользователю."""
    address_key = (city, street, house)
    data = SCHEDULE_DATA_CACHE.get(address_key)
    
    if not data:
        return None
    
    schedule = data.get("schedule", {})
    if not schedule:
        return None
    
    kiev_tz = pytz.timezone('Europe/Kiev')
    
    # Собираем все события (начало и конец отключений)
    events = []
    
    try:
        sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
    except ValueError:
        sorted_dates = sorted(schedule.keys())
    
    for date_str in sorted_dates:
        try:
            date_obj = datetime.strptime(date_str, '%d.%m.%y').date()
            if date_obj < now.date():
                continue
        except ValueError:
            continue
        
        slots = schedule.get(date_str, [])
        for slot in slots:
            from common.bot_base import parse_time_range
            time_str = slot.get('shutdown', '00:00–00:00')
            start_min, end_min = parse_time_range(time_str)
            
            start_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=start_min)
            end_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=end_min)
            
            events.append((start_dt, 'off_start'))
            events.append((end_dt, 'on_start'))
    
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
    time_to_event = (event_dt - now).total_seconds() / 60.0  # минуты
    
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
                return event_dt_str  # Возвращаем время события для обновления БД
            except Exception as e:
                logger.error(f"Failed to send alert to {user_id}: {e}")
                return None
    return None

async def alert_checker_task(bot: Bot):
    """Фоновая задача для проверки алертов."""
    global db_conn
    logger.info("Alert checker started.")
    while True:
        await asyncio.sleep(60)
        if db_conn is None:
            continue

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

# --- Command Handlers ---
@dp.message(Command("start", "help"))
async def command_start_handler(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    username = message.from_user.username or "N/A"
    full_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or "N/A"
    
    logger.info(f"Command /start by user {user_id} (@{username}) {full_name}")
    
    if user_id not in HUMAN_USERS:
        logger.info(f"CAPTCHA requested for user {user_id} (@{username}) {full_name}")
        is_human = await _handle_captcha_check(message, state)
        if not is_human:
            return

    text = (
        "👋 **Вітаю! Я бот для перевірки графіків відключень ЦЕК.**\n"
        "Для перевірки графіку, введіть команду **/check**, додавши адресу у форматі:\n"
        "`/check Місто, Вулиця, Будинок`\n"
        "**АБО** просто введіть **/check** без адреси, щоб ввести дані покроково.\n"
        "**Наприклад:**\n"
        "`/check м. Павлоград, вул. Нова, 7`\n"
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
    await update_user_activity(db_conn, user_id, username=message.from_user.username)

@dp.message(Command("stats"))
async def command_stats_handler(message: types.Message) -> None:
    user_id = message.from_user.id
    
    # Load ADMIN_IDS from env
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    try:
        admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]
    except ValueError:
        admin_ids = []

    if user_id not in admin_ids:
         await message.answer("⛔ **Відмовлено в доступі.** У вас недостатньо прав для перегляду статистики.")
         return

    await message.answer("📊 **Збираю статистику...**")
    
    try:
        # 1. Summary
        async with db_conn.execute("SELECT COUNT(*) FROM user_activity") as cursor:
            total_users = (await cursor.fetchone())[0]
        
        yesterday = datetime.now() - timedelta(days=1)
        async with db_conn.execute("SELECT COUNT(*) FROM user_activity WHERE last_seen >= ?", (yesterday,)) as cursor:
            active_24h = (await cursor.fetchone())[0]
            
        summary = (
            f"📊 **Статистика ЦЕК Бот**\n"
            f"👤 Всього користувачів: {total_users}\n"
            f"🔥 Активних за 24г: {active_24h}\n"
            f"📥 Завантажую детальний звіт..."
        )
        await message.answer(summary)
        
        # 2. CSV Export
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['User ID', 'Username', 'First Seen', 'Last Seen', 'Last City', 'Last Street', 'Last House', 'Last Group'])
        
        async with db_conn.execute("SELECT user_id, username, first_seen, last_seen, last_city, last_street, last_house, last_group FROM user_activity ORDER BY last_seen DESC") as cursor:
            async for row in cursor:
                writer.writerow(row)
                
        output.seek(0)
        document = BufferedInputFile(output.getvalue().encode('utf-8'), filename=f"cek_stats_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
        await message.answer_document(document, caption="📄 Детальна статистика користувачів")
        
    except Exception as e:
        logger.error(f"Error generating stats: {e}", exc_info=True)
        await message.answer(f"❌ Помилка генерації статистики: {e}")

@dp.message(CaptchaState.waiting_for_answer)
async def captcha_answer_handler(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        return
        
    user_id = message.from_user.id
    username = message.from_user.username or "N/A"
    full_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or "N/A"
    data = await state.get_data()
    correct_answer = data.get("captcha_answer")
    
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("⚠️ Будь ласка, введіть лише число-відповідь.")
        return

    try:
        user_answer = int(text)
    except ValueError:
        user_answer = -1

    if user_answer == correct_answer:
        HUMAN_USERS[user_id] = True
        await state.clear()
        logger.info(f"CAPTCHA passed by user {user_id} (@{username}) {full_name}")
        await message.answer(
            "✅ **Перевірка пройдена!**\n"
            "Тепер ви можете користуватися всіма функціями бота. Введіть **/start** ще раз, щоб побачити список команд.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await state.clear()
        logger.info(f"CAPTCHA failed by user {user_id} (@{username}) {full_name}")
        await message.answer(
            "❌ **Неправильна відповідь.** Спробуйте ще раз, ввівши **/start**."
        )

@dp.message(Command("cancel"))
async def command_cancel_handler(message: types.Message, state: FSMContext) -> None:
    """Обработчик команды /cancel."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Немає активних дій для скасування.")
        return
    await state.clear()
    await message.answer("Дію скасовано. Введіть /check [адреса], щоб почати перевірку, або /check для покрокового вводу.")

# FSM handlers for step-by-step address input
@dp.message(CheckAddressState.waiting_for_city, F.text)
async def process_city(message: types.Message, state: FSMContext) -> None:
    city = message.text.strip()
    await state.update_data(city=city)
    await state.set_state(CheckAddressState.waiting_for_street)
    await message.answer(f"📍 Місто: `{city}`\n**Будь ласка, введіть назву вулиці** (наприклад, `вул. Нова`):")

@dp.message(CheckAddressState.waiting_for_street, F.text)
async def process_street(message: types.Message, state: FSMContext) -> None:
    street = message.text.strip()
    await state.update_data(street=street)
    await state.set_state(CheckAddressState.waiting_for_house)
    await message.answer(f"📍 Вулиця: `{street}`\n**Будь ласка, введіть номер будинку** (наприклад, `7`):")

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
        # Try to get cached group for CEK optimization
        cached_group = None
        cursor_cached = await db_conn.execute(
            "SELECT group_name FROM user_last_check WHERE user_id = ? AND city = ? AND street = ? AND house = ?",
            (user_id, city, street, house)
        )
        row_cached = await cursor_cached.fetchone()
        if row_cached and row_cached[0]:
            cached_group = row_cached[0]
            logger.info(f"Using cached group for FSM check: {cached_group}")
        
        api_data = await get_shutdowns_data(city, street, house, cached_group)
        current_hash = get_schedule_hash_compact(api_data)
        group = api_data.get('group', None)
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, city, street, house, last_hash, group_name) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, city, street, house, current_hash, group)
        )
        await db_conn.commit()
        await state.clear()
        
        cursor = await db_conn.execute("SELECT 1 FROM subscriptions WHERE user_id = ?", (user_id,))
        is_subscribed = bool(await cursor.fetchone())
        
        await send_schedule_response(message, api_data, is_subscribed)
        await update_user_activity(db_conn, user_id, username=message.from_user.username, city=city, street=street, house=house, group_name=group)

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

@dp.message(Command("check"))
async def command_check_handler(message: types.Message, state: FSMContext) -> None:
    global db_conn
    user_id = message.from_user.id
    username = message.from_user.username or "N/A"
    full_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or "N/A"
    
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await _handle_captcha_check(message, state)
        return

    text_args = message.text.replace('/check', '', 1).strip()
    if not text_args:
        logger.info(f"Command /check (FSM) by user {user_id} (@{username}) {full_name}")
        await state.set_state(CheckAddressState.waiting_for_city)
        await message.answer("📍 **Будь ласка, введіть назву міста** (наприклад, `м. Павлоград`):")
        return

    current_state = await state.get_state()
    if current_state:
        await state.clear()

    await message.answer("⏳ Перевіряю графік за вказаною адресою. Очікуйте...")
    try:
        city, street, house = parse_address_from_text(text_args)
        logger.info(f"Command /check by user {user_id} (@{username}) {full_name} for address: {city}, {street}, {house}")
        
        # Try to get cached group for CEK optimization
        cached_group = None
        cursor_cached = await db_conn.execute(
            "SELECT group_name FROM user_last_check WHERE user_id = ? AND city = ? AND street = ? AND house = ?",
            (user_id, city, street, house)
        )
        row_cached = await cursor_cached.fetchone()
        if row_cached and row_cached[0]:
            cached_group = row_cached[0]
            logger.info(f"Using cached group for inline check: {cached_group}")
        
        api_data = await get_shutdowns_data(city, street, house, cached_group)
        current_hash = get_schedule_hash_compact(api_data)
        group = api_data.get('group', None)
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, city, street, house, last_hash, group_name) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, city, street, house, current_hash, group)
        )
        await db_conn.commit()
        
        cursor = await db_conn.execute("SELECT 1 FROM subscriptions WHERE user_id = ?", (user_id,))
        is_subscribed = bool(await cursor.fetchone())
        
        await send_schedule_response(message, api_data, is_subscribed)
        await update_user_activity(db_conn, user_id, username=message.from_user.username, city=city, street=street, house=house, group_name=group)

    except ValueError as e:
        await message.answer(f"❌ **Помилка вводу:** {e}")
    except ConnectionError as e:
        await message.answer(f"❌ **Помилка підключення:** {e}")
    except Exception as e:
        logger.error(f"Critical error in /check for user {user_id}: {e}", exc_info=True)
        await message.answer("❌ Виникла непередбачена помилка. Спробуйте пізніше.")

# Continue with /repeat, /subscribe, /unsubscribe, /alert handlers...
# (Due to length, I'll create a second part)

@dp.message(Command("repeat"))
async def command_repeat_handler(message: types.Message, state: FSMContext) -> None:
    global db_conn
    user_id = message.from_user.id
    username = message.from_user.username or "N/A"
    full_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or "N/A"
    
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await _handle_captcha_check(message, state)
        return

    city, street, house, group = None, None, None, None
    try:
        cursor = await db_conn.execute("SELECT city, street, house, group_name FROM user_last_check WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("❌ **Помилка.** Спочатку вам потрібно перевірити графік за допомогою команди `/check Місто, Вулиця, Будинок`.")
            return
        city, street, house, group = row
    except Exception as e:
        logger.error(f"Failed to fetch last_check from DB for user {user_id}: {e}")
        await message.answer("❌ **Помилка БД** при спробі знайти ваш останній запит.")
        return

    logger.info(f"Command /repeat by user {user_id} (@{username}) {full_name} for address: {city}, {street}, {house}")
    address_str = f"`{city}, {street}, {house}`"
    await message.answer(f"🔄 **Повторюю перевірку** для адреси:\n{address_str}\n⏳ Очікуйте...")
    
    try:
        # Try to get cached group for CEK optimization
        cached_group = None
        cursor_cached = await db_conn.execute(
            "SELECT group_name FROM user_last_check WHERE user_id = ?",
            (user_id,)
        )
        row_cached = await cursor_cached.fetchone()
        if row_cached and row_cached[0]:
            cached_group = row_cached[0]
            logger.info(f"Using cached group for /repeat: {cached_group}")
        
        data = await get_shutdowns_data(city, street, house, cached_group)
        current_hash = get_schedule_hash_compact(data)
        await db_conn.execute(
            "UPDATE user_last_check SET last_hash = ? WHERE user_id = ?", 
            (current_hash, user_id)
        )
        await db_conn.commit()
        
        cursor = await db_conn.execute("SELECT 1 FROM subscriptions WHERE user_id = ?", (user_id,))
        is_subscribed = bool(await cursor.fetchone())
        
        await send_schedule_response(message, data, is_subscribed)
        await update_user_activity(db_conn, user_id, username=message.from_user.username, city=city, street=street, house=house, group_name=group)

    except (ValueError, ConnectionError) as e:
        error_type = "Помилка вводу/помилка API" if isinstance(e, ValueError) else "Помилка"
        await message.answer(f"❌ **{error_type}:** {e}")
    except Exception as e:
        logger.error(f"Critical error during repeat check for user {message.from_user.id}: {e}", exc_info=True)
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")

@dp.message(Command("subscribe"))
async def command_subscribe_handler(message: types.Message, state: FSMContext) -> None:
    global db_conn
    user_id = message.from_user.id
    username = message.from_user.username or "N/A"
    full_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or "N/A"
    
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

    logger.info(f"Command /subscribe by user {user_id} (@{username}) {full_name} for address: {city}, {street}, {house}")
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
    interval_display = f"{hours_str} {get_hours_str(interval_hours)}"

    hash_to_use = hash_from_check
    # Проверяем текущее значение notification_lead_time (вынесено сюда, чтобы быть совместимым с DTEK)
    current_lead_time = 0
    try:
        cursor_tmp = await db_conn.execute("SELECT notification_lead_time FROM subscriptions WHERE user_id = ?", (user_id,))
        row_alert_tmp = await cursor_tmp.fetchone()
        if row_alert_tmp:
            current_lead_time = row_alert_tmp[0] if row_alert_tmp[0] is not None else 0
    except Exception:
        current_lead_time = 0
    new_lead_time = current_lead_time
    if current_lead_time == 0:
        new_lead_time = 15
    try:
        cursor = await db_conn.execute(
            "SELECT last_schedule_hash, interval_hours FROM subscriptions WHERE user_id = ? AND city = ? AND street = ? AND house = ?", 
            (user_id, city, street, house)
        )
        sub_row = await cursor.fetchone()
        if sub_row:
            hash_to_use = sub_row[0]
            if sub_row[1] == interval_hours:
                exists_msg = build_subscription_exists_message(city, street, house, interval_display,  new_lead_time if 'new_lead_time' in locals() else 0)
                await message.answer(exists_msg)
                await update_user_activity(db_conn, user_id, username=message.from_user.username) # Added line
                return

        if hash_to_use is None:
            hash_to_use = "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION"

        next_check_time = datetime.now()
        
        

        # Extract group from last check
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

        logger.info(f"User {user_id} subscribed/updated to {city}, {street}, {house} with interval {interval_hours}h. Alert: {new_lead_time}m")
        created_msg = build_subscription_created_message(city, street, house, interval_display, new_lead_time, current_lead_time)
        await message.answer(created_msg)
        await update_user_activity(db_conn, user_id, username=message.from_user.username, city=city, street=street, house=house, group_name=group)
    except Exception as e:
        logger.error(f"Failed to write subscription to DB for user {user_id}: {e}", exc_info=True)
        await message.answer("❌ **Помилка БД** при спробі зберегти підписку.")

@dp.message(Command("alert"))
async def cmd_alert(message: types.Message):
    """Встановлює час попередження перед відключенням/включенням (у хвилинах)."""
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
        logger.error(f"Failed to unsubscribe user {user_id}: {e}", exc_info=True)
        await message.answer("❌ **Помилка БД** при спробі скасувати підписку.")

# --- Bot Setup and Main ---
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
        BotCommand(command="stats", description="📊 Статистика (Admin)"),
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
        logger.error("CEK_BOT_TOKEN is not set. Exiting.")
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

    # Register handlers (cancel must be first)
    dp.message.register(command_cancel_handler, Command("cancel"))
    dp.message.register(command_start_handler, Command("start", "help"))
    dp.message.register(command_check_handler, Command("check"))
    dp.message.register(command_repeat_handler, Command("repeat"))
    dp.message.register(command_subscribe_handler, Command("subscribe"))
    dp.message.register(command_unsubscribe_handler, Command("unsubscribe"))
    dp.message.register(cmd_alert, Command("alert"))

    checker_task = asyncio.create_task(subscription_checker_task(bot))
    alert_task = asyncio.create_task(alert_checker_task(bot))

    logger.info("CEK Bot started. Beginning polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Stopping bot. Cancelling background tasks...")
        checker_task.cancel()
        alert_task.cancel()
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
        logger.info("CEK Bot stopped.")

