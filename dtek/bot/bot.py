"""
DTEK Telegram Bot - Independent bot for DTEK power shutdown schedules.
Uses common library and calls DTEK parser directly.
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BotCommand, ReplyKeyboardRemove, BufferedInputFile, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
import pytz

# Import from common library
from common.bot_base import (
    init_db,
    BotContext,
    CaptchaState,
    CheckAddressState,
    AddressRenameState,
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
    is_human_user,
    set_human_user,
    # Multi-address functions
    save_user_address,
    get_user_addresses,
    get_address_by_id,
    delete_user_address,
    rename_user_address,
    get_user_subscriptions,
    get_subscription_count,
    is_address_subscribed,
    remove_subscription_by_id,
    remove_all_subscriptions,
    build_address_selection_keyboard,
    build_subscription_selection_keyboard,
    build_address_management_keyboard,
)
from common.formatting import (
    process_single_day_schedule_compact,
    get_current_status_message,
    merge_consecutive_slots,
)
from common.formatting import (
    build_subscription_exists_message,
    build_subscription_created_message,
)
# Common handlers
from common.handlers import (
    handle_captcha_check,
    handle_captcha_answer,
    handle_cancel,
    handle_alert,
    handle_unsubscribe,
    handle_process_city,
    handle_process_street,
    handle_callback_unsubscribe,
    handle_addresses_command,
    handle_callback_address_info,
    handle_callback_address_delete,
    handle_callback_address_rename_start,
    handle_process_address_rename,
)
from common.visualization import (
    generate_48h_schedule_image,
    generate_24h_schedule_image,
)

# Import Data Source Factory
from dtek.data_source import get_data_source

# --- Configuration ---
PROVIDER = "ДТЕК"
BOT_TOKEN = os.getenv("DTEK_BOT_TOKEN")
DB_PATH = os.getenv("DTEK_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "bot.db"))
FONT_PATH = os.getenv("DTEK_FONT_PATH", os.path.join(os.path.dirname(__file__), "..", "resources", "DejaVuSans.ttf"))

# Logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False  # Отключаем дублирование логов
handler = logging.StreamHandler()
formatter = logging.Formatter(
    'dtek_bot | %(levelname)s:%(name)s:%(message)s',
    datefmt='%H:%M:%S'
)
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)

# Dispatcher
dp = Dispatcher()
db_conn = None

# BotContext for common handlers
ctx: BotContext = None

def get_ctx() -> BotContext:
    """Get current BotContext with updated db_conn."""
    global ctx, db_conn
    if ctx is None:
        ctx = BotContext(
            provider_name="ДТЕК",
            provider_code="dtek",
            visualization_hours=48,
            db_conn=db_conn,
            font_path=FONT_PATH,
            logger=logger,
        )
    else:
        ctx.db_conn = db_conn
    return ctx

# --- Helper Functions ---
async def _handle_captcha_check(message: types.Message, state: FSMContext) -> bool:
    """Wrapper for common handler."""
    return await handle_captcha_check(message, state, get_ctx())

async def get_shutdowns_data(city: str, street: str, house: str) -> dict:
    """Отримує дані через абстракцію DataSource."""
    try:
        source = get_data_source()
        return await source.get_schedule(city, street, house)
    except Exception as e:
        logger.error(f"Data source error: {e}", exc_info=True)
        error_str = str(e)
        if "Could not determine group for address" in error_str:
            raise ValueError(f"Не вдалося отримати групу для адреси: {city}, {street}, {house}")
        raise ValueError(f"Не вдалося отримати графік для адреси. Помилка: {error_str[:100]}")

async def send_schedule_response(message: types.Message, api_data: dict, is_subscribed: bool):
    """
    Отправляет пользователю форматированный ответ с графиком ДТЕК.
    """
    try:
        city = api_data.get("city", "Н/Д")
        street = api_data.get("street", "Н/Д")
        house = api_data.get("house_num", "Н/Д")
        group = api_data.get("group", "Н/Д")

        schedule = api_data.get("schedule", {})
        if not schedule:
            await message.answer("❌ *Не вдалося отримати графік відключень.*")
            if not is_subscribed:
                await message.answer("💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`.")
            return

        # Сортируем даты
        try:
            sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
        except ValueError:
            sorted_dates = sorted(schedule.keys())

        # Генерация диаграммы (24h или 48h)
        has_shutdowns_tomorrow = False
        if len(sorted_dates) >= 2:
            tomorrow_date = sorted_dates[1]
            if schedule.get(tomorrow_date):
                has_shutdowns_tomorrow = True
        
        image_data = None
        diagram_caption = ""
        filename = ""

        if has_shutdowns_tomorrow:
            # 48 часов
            all_slots_48h = {}
            for date in sorted_dates[:2]:
                all_slots_48h[date] = schedule.get(date, [])

            if any(slots for slots in all_slots_48h.values()):
                image_data = generate_48h_schedule_image(all_slots_48h, FONT_PATH, current_time=datetime.now(pytz.timezone('Europe/Kiev')))
                diagram_caption = "🕙 **Загальний графік на 48 годин**"
                filename = "schedule_48h.png"
        else:
            # 24 часа
            if sorted_dates:
                today_date = sorted_dates[0]
                today_slots = {today_date: schedule.get(today_date, [])}
                if schedule.get(today_date):
                    image_data = generate_24h_schedule_image(today_slots, FONT_PATH, current_time=datetime.now(pytz.timezone('Europe/Kiev')))
                    diagram_caption = "🕙 **Графік на сьогодні**"
                    filename = "schedule_24h.png"

        # Собираем все части в один блок
        message_parts = []
        message_parts.append(f"🏠 Адреса: `{city}, {street}, {house}`\n👥 Черга: `{group}`")
        
        if diagram_caption:
            message_parts.append(diagram_caption)
        
        # Текстовые данные по дням
        for date in sorted_dates:
            slots = schedule.get(date, [])
            day_text = process_single_day_schedule_compact(date, slots, PROVIDER)
            if day_text and day_text.strip():
                message_parts.append(day_text.strip())

        # Статусное сообщение
        status_msg = get_current_status_message(schedule)
        if status_msg:
            message_parts.append(status_msg)
        
        # Подвал
        if not is_subscribed:
            message_parts.append("💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`.")
        
        # Объединяем все части
        full_message = "\n\n".join(message_parts)
        
        # Отправляем одно сообщение с фото и полной подписью
        if image_data:
            # Telegram позволяет до 1024 символов в caption
            if len(full_message) <= 1024:
                image_file = BufferedInputFile(image_data, filename=filename)
                await message.answer_photo(
                    photo=image_file,
                    caption=full_message,
                    parse_mode="Markdown"
                )
            else:
                # Отправляем фото с коротким caption и текст отдельно
                short_caption = "\n\n".join(message_parts[:2])  # Адрес + диаграмма
                remaining_text = "\n\n".join(message_parts[2:])  # Остальное
                
                image_file = BufferedInputFile(image_data, filename=filename)
                await message.answer_photo(
                    photo=image_file,
                    caption=short_caption,
                    parse_mode="Markdown"
                )
                await message.answer(remaining_text, parse_mode="Markdown")
        else:
            # Нет диаграммы - просто отправляем текст
            await message.answer(full_message, parse_mode="Markdown")
    
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
                data = await get_shutdowns_data(city, street, house)
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
                    # Get user info for logging
                    try:
                        user = await bot.get_chat(user_id)
                        user_info = format_user_info(user)
                    except:
                        user_info = str(user_id)
                    logger.error(f"Failed to send error message to user {user_info}: {e}")

                db_updates_fail.append((next_check_time, user_id))
                continue

            data = data_or_error
            last_hash = sub_data.get('last_schedule_hash')
            new_hash = ADDRESS_CACHE[address_key]['last_schedule_hash']

            # Проверяем, есть ли реальные изменения в расписании
            schedule = data.get("schedule", {})
            has_actual_schedule = any(slots for slots in schedule.values() if slots)
            
            # Отправляем уведомление только если:
            # 1. Хеш изменился И
            # 2. Есть реальное расписание ИЛИ это первая проверка (last_hash в специальных значениях)
            should_notify = (
                new_hash != last_hash and 
                (has_actual_schedule or last_hash in (None, "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION"))
            )
            
            if should_notify:
                group = data.get("group", "Н/Д")
                
                interval_str = f"{f'{interval_hours:g}'.replace('.', ',')} год"
                update_header = "🔔 **ОНОВЛЕННЯ ГРАФІКУ!**" if last_hash not in (None, "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION") else "🔔 **Графік перевірено**"
                
                try:
                    sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
                except ValueError:
                    sorted_dates = sorted(schedule.keys())

                # Генерация диаграммы (24h или 48h)
                has_shutdowns_tomorrow = False
                if len(sorted_dates) >= 2:
                    tomorrow_date = sorted_dates[1]
                    if schedule.get(tomorrow_date):
                        has_shutdowns_tomorrow = True
                
                image_data = None
                diagram_caption = ""
                filename = ""

                if has_shutdowns_tomorrow:
                    # 48 часов
                    days_slots_48h = {}
                    for date in sorted_dates[:2]:
                        days_slots_48h[date] = schedule.get(date, [])
                    
                    if any(slots for slots in days_slots_48h.values()):
                        image_data = generate_48h_schedule_image(days_slots_48h, FONT_PATH, current_time=datetime.now(pytz.timezone('Europe/Kiev')))
                    diagram_caption = "🕙 **Загальний графік на 48 годин**"
                    filename = "schedule_48h_update.png"
                else:
                    # 24 часа
                    if sorted_dates:
                        today_date = sorted_dates[0]
                        today_slots = {today_date: schedule.get(today_date, [])}
                        if schedule.get(today_date):
                            image_data = generate_24h_schedule_image(today_slots, FONT_PATH, current_time=datetime.now(pytz.timezone('Europe/Kiev')))
                            diagram_caption = "🕙 **Графік на сьогодні**"
                            filename = "schedule_24h_update.png"

                # Собираем все текстовые части в один блок
                message_parts = []
                message_parts.append(f"{update_header}\nдля {address_str} (інтервал {interval_str})")
                message_parts.append(f"🏠 Адреса: `{city}, {street}, {house}`\n👥 Черга: `{group}`")
                
                if diagram_caption:
                    message_parts.append(diagram_caption)
                
                # Текстовые данные по дням
                for date in sorted_dates:
                    slots = schedule[date]
                    day_text = process_single_day_schedule_compact(date, slots, PROVIDER)
                    if day_text and day_text.strip():
                        message_parts.append(day_text.strip())

                # Статусное сообщение
                status_msg = get_current_status_message(schedule)
                if status_msg:
                    message_parts.append(status_msg)
                
                # Объединяем все части
                full_message = "\n\n".join(message_parts)
                
                # Отправляем одно сообщение с фото и полной подписью
                try:
                    if image_data:
                        # Telegram позволяет до 1024 символов в caption
                        # Если текст длиннее, отправляем фото с коротким caption и текст отдельно
                        if len(full_message) <= 1024:
                            image_file = BufferedInputFile(image_data, filename=filename)
                            await bot.send_photo(
                                chat_id=user_id,
                                photo=image_file,
                                caption=full_message,
                                parse_mode="Markdown"
                            )
                        else:
                            # Отправляем фото с коротким caption и текст отдельно
                            short_caption = "\n\n".join(message_parts[:3])  # Заголовок + адрес + диаграмма
                            remaining_text = "\n\n".join(message_parts[3:])  # Остальное
                            
                            image_file = BufferedInputFile(image_data, filename=filename)
                            await bot.send_photo(
                                chat_id=user_id,
                                photo=image_file,
                                caption=short_caption,
                                parse_mode="Markdown"
                            )
                            await bot.send_message(
                                chat_id=user_id,
                                text=remaining_text,
                                parse_mode="Markdown",
                                disable_notification=True
                            )
                    else:
                        # Нет диаграммы - просто отправляем текст
                        await bot.send_message(
                            chat_id=user_id,
                            text=full_message,
                            parse_mode="Markdown"
                        )
                except Exception as e:
                    logger.error(f"Failed to send update notification to user {user_id}: {e}")

                # Get user info for logging
                try:
                    user = await bot.get_chat(user_id)
                    user_info = format_user_info(user)
                except:
                    user_info = str(user_id)
                    
                db_updates_success.append((next_check_time, new_hash, user_id))
                logger.info(f"Notification sent to user {user_info}. Hash updated to {new_hash[:8]}.")
            else:
                # Get user info for logging
                try:
                    user = await bot.get_chat(user_id)
                    user_info = format_user_info(user)
                except:
                    user_info = str(user_id)
                    
                logger.debug(f"User {user_info} check for {address_str}. No change in hash: {new_hash[:8]}.")
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

async def _process_alert_for_user(bot: Bot, user_id: int, city: str, street: str, house: str, lead_time: int, last_alert_event_start_str: str, now: datetime, user_info: str = None) -> str:
    """Проверяет, нужно ли отправить алерт пользователю."""
    if user_info is None:
        user_info = str(user_id)
        
    address_key = (city, street, house)
    data = SCHEDULE_DATA_CACHE.get(address_key)
    
    if not data:
        logger.debug(f"Alert check user {user_info}: no data in cache for address {address_key}")
        return None
    
    schedule = data.get("schedule", {})
    if not schedule:
        logger.debug(f"Alert check user {user_info}: no schedule data")
        return None
    
    # Data is already merged by the parser service
    merged_schedule = schedule
    
    kiev_tz = pytz.timezone('Europe/Kiev')
    
    # Собираем все события (начало и конец отключений) из объединенных периодов
    events = []
    
    try:
        sorted_dates = sorted(merged_schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
    except ValueError:
        sorted_dates = sorted(merged_schedule.keys())
    
    for date_str in sorted_dates:
        try:
            date_obj = datetime.strptime(date_str, '%d.%m.%y').date()
            if date_obj < now.date():
                continue
        except ValueError:
            continue
        
        slots = merged_schedule.get(date_str, [])
        for slot in slots:
            from common.bot_base import parse_time_range
            time_str = slot.get('shutdown', '00:00–00:00')
            start_min, end_min = parse_time_range(time_str)
            
            start_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=start_min)
            end_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=end_min)
            
            events.append((start_dt, 'off_start'))
            events.append((end_dt, 'on_start'))
    
    events.sort(key=lambda x: x[0])
    
    logger.debug(f"Alert check user {user_info}: found {len(events)} events total")
    
    # Ищем ближайшее событие в будущем
    target_event = None
    for event_dt, event_type in events:
        if event_dt > now:
            target_event = (event_dt, event_type)
            break
    
    if not target_event:
        logger.debug(f"Alert check user {user_info}: no future events found")
        return None
        
    event_dt, event_type = target_event
    time_to_event = (event_dt - now).total_seconds() / 60.0  # минуты
    
    msg_type = "відключення" if event_type == 'off_start' else "включення"
    logger.debug(f"Alert check user {user_info}: next event is {msg_type} at {event_dt.strftime('%H:%M')} (in {time_to_event:.1f} min), lead_time={lead_time} min")
    
    # Проверяем, пора ли слать алерт
    if 0 < time_to_event <= lead_time:
        event_dt_str = event_dt.isoformat()
        
        if last_alert_event_start_str != event_dt_str:
            # Шлем алерт!
            time_str = event_dt.strftime('%H:%M')
            minutes_left = int(time_to_event)
            
            msg = f"⚠️ **Увага!** Через {minutes_left} хв. у {time_str} очікується **{msg_type}** світла."
            
            logger.info(f"Sending alert to user {user_info}: {msg_type} at {time_str} in {minutes_left} min")
            
            try:
                await bot.send_message(user_id, msg, parse_mode="Markdown")
                logger.info(f"Alert sent successfully to user {user_info}, event_dt={event_dt_str}")
                return event_dt_str  # Возвращаем время события для обновления БД
            except Exception as e:
                logger.error(f"Failed to send alert to {user_info}: {e}")
                return None
        else:
            logger.debug(f"Alert check user {user_info}: alert already sent for this event (last_alert={last_alert_event_start_str})")
    else:
        if time_to_event <= 0:
            logger.debug(f"Alert check user {user_info}: event already passed")
        else:
            logger.debug(f"Alert check user {user_info}: event too far ({time_to_event:.1f} min > {lead_time} min)")
    
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
            
            if rows:
                logger.debug(f"Alert check cycle at {now.strftime('%H:%M:%S')}: checking {len(rows)} user(s) with notifications enabled")
            
            for row in rows:
                user_id, city, street, house, lead_time, last_alert_event_start_str = row
                
                # Get user info for logging
                try:
                    user = await bot.get_chat(user_id)
                    user_info = format_user_info(user)
                except:
                    user_info = str(user_id)
                
                logger.debug(f"Processing alerts for user {user_info}, lead_time={lead_time} min, last_alert={last_alert_event_start_str}")
                
                new_last_alert = await _process_alert_for_user(
                    bot, user_id, city, street, house, lead_time, last_alert_event_start_str, now, user_info
                )
                
                if new_last_alert:
                    logger.info(f"Updating last_alert_event_start for user {user_info} to {new_last_alert}")
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
    user_info = format_user_info(message.from_user)
    
    logger.info(f"Command /start by user {user_info}")
    
    if user_id not in HUMAN_USERS:
        logger.info(f"CAPTCHA requested for user {user_info}")
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

    await message.answer("⏳ **Збираю статистику...**")
    
    try:
        # 1. Summary
        async with db_conn.execute("SELECT COUNT(*) FROM user_activity") as cursor:
            total_users = (await cursor.fetchone())[0]
        
        async with db_conn.execute("SELECT COUNT(*) FROM subscriptions") as cursor:
            total_subscriptions = (await cursor.fetchone())[0]
        
        yesterday = datetime.now() - timedelta(days=1)
        async with db_conn.execute("SELECT COUNT(*) FROM user_activity WHERE last_seen >= ?", (yesterday,)) as cursor:
            active_24h = (await cursor.fetchone())[0]
            
        summary = (
            f"📊 **Статистика ДТЕК Бот**\n"
            f"👤 Всього користувачів: {total_users}\n"
            f"📋 Всього підписок: {total_subscriptions}\n"
            f"🔥 Активних за 24г: {active_24h}\n"
            f"📥 Завантажую детальний звіт..."
        )
        await message.answer(summary)
        
        # 2. CSV Export with subscription data
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['User ID', 'Username', 'Full Name', 'First Seen', 'Last Seen', 'Last City', 'Last Street', 'Last House', 'Last Group', 'Subscribed', 'Sub City', 'Sub Street', 'Sub House', 'Sub Group', 'Sub Interval'])
        
        # Join user_activity with subscriptions
        query = """
        SELECT 
            ua.user_id, ua.username, 
            COALESCE(ua.first_name || ' ' || ua.last_name, 'N/A') as full_name,
            ua.first_seen, ua.last_seen, 
            ua.last_city, ua.last_street, ua.last_house, ua.last_group,
            CASE WHEN s.user_id IS NOT NULL THEN 'Yes' ELSE 'No' END as subscribed,
            s.city as sub_city, s.street as sub_street, s.house as sub_house, 
            s.group_name as sub_group, s.interval_hours as sub_interval
        FROM user_activity ua
        LEFT JOIN subscriptions s ON ua.user_id = s.user_id
        ORDER BY ua.last_seen DESC
        """
        
        # Add full_name column to user_activity if not exists
        try:
            await db_conn.execute("ALTER TABLE user_activity ADD COLUMN first_name TEXT")
            await db_conn.execute("ALTER TABLE user_activity ADD COLUMN last_name TEXT")
            await db_conn.commit()
        except:
            pass  # Columns already exist
        
        async with db_conn.execute(query) as cursor:
            async for row in cursor:
                writer.writerow(row)
                
        output.seek(0)
        document = BufferedInputFile(output.getvalue().encode('utf-8'), filename=f"dtek_stats_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
        await message.answer_document(document, caption="📄 Детальна статистика користувачів")
        
    except Exception as e:
        logger.error(f"Error generating stats: {e}", exc_info=True)
        await message.answer(f"❌ Помилка генерації статистики: {e}")

@dp.message(CaptchaState.waiting_for_answer)
async def captcha_answer_handler(message: types.Message, state: FSMContext) -> None:
    """Wrapper for common handler."""
    await handle_captcha_answer(message, state, get_ctx())

@dp.message(Command("cancel"))
async def command_cancel_handler(message: types.Message, state: FSMContext) -> None:
    """Wrapper for common handler."""
    await handle_cancel(message, state)

# FSM handlers for step-by-step address input
@dp.message(CheckAddressState.waiting_for_city, F.text)
async def process_city(message: types.Message, state: FSMContext) -> None:
    """Wrapper for common handler."""
    await handle_process_city(message, state)

@dp.message(CheckAddressState.waiting_for_street, F.text)
async def process_street(message: types.Message, state: FSMContext) -> None:
    """Wrapper for common handler."""
    await handle_process_street(message, state)

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
        api_data = await get_shutdowns_data(city, street, house)
        current_hash = get_schedule_hash_compact(api_data)
        group = api_data.get('group', None)
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, city, street, house, last_hash, group_name) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, city, street, house, current_hash, group)
        )
        await db_conn.commit()
        await state.clear()
        
        # Auto-save to address book
        await save_user_address(db_conn, user_id, city, street, house, group)
        
        sub_count = await get_subscription_count(db_conn, user_id)
        is_subscribed = sub_count > 0
        
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
    user_info = format_user_info(message.from_user)
    
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await _handle_captcha_check(message, state)
        return

    text_args = message.text.replace('/check', '', 1).strip()
    if not text_args:
        # Check if user has saved addresses
        addresses = await get_user_addresses(db_conn, user_id, limit=10)
        if addresses:
            logger.info(f"Command /check (address selection) by user {user_info}, {len(addresses)} addresses")
            keyboard = build_address_selection_keyboard(addresses, action="check", include_new_button=True)
            await message.answer(
                "📍 **Оберіть адресу для перевірки** або додайте нову:",
                reply_markup=keyboard
            )
            return
        else:
            logger.info(f"Command /check (FSM) by user {user_info}")
            await state.set_state(CheckAddressState.waiting_for_city)
            await message.answer("📍 **Будь ласка, введіть назву міста** (наприклад, `м. Дніпро`):")
            return

    current_state = await state.get_state()
    if current_state:
        await state.clear()

    await message.answer("⏳ Перевіряю графік за вказаною адресою. Очікуйте...")
    try:
        city, street, house = parse_address_from_text(text_args)
        logger.info(f"Command /check by user {user_info} for address: {city}, {street}, {house}")
        
        api_data = await get_shutdowns_data(city, street, house)
        current_hash = get_schedule_hash_compact(api_data)
        group = api_data.get('group', None)
        
        # Save to user_last_check
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, city, street, house, last_hash, group_name) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, city, street, house, current_hash, group)
        )
        await db_conn.commit()
        
        # Auto-save to address book
        await save_user_address(db_conn, user_id, city, street, house, group)
        
        sub_count = await get_subscription_count(db_conn, user_id)
        is_subscribed = sub_count > 0
        
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
    user_info = format_user_info(message.from_user)
    
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await _handle_captcha_check(message, state)
        return

    # Check if user has multiple saved addresses
    addresses = await get_user_addresses(db_conn, user_id, limit=10)
    if len(addresses) > 1:
        logger.info(f"Command /repeat (address selection) by user {user_info}, {len(addresses)} addresses")
        keyboard = build_address_selection_keyboard(addresses, action="repeat", include_new_button=False)
        await message.answer(
            "📍 **Оберіть адресу для повторної перевірки:**",
            reply_markup=keyboard
        )
        return

    # Single or no address - use last checked
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

    await _perform_address_check(message, user_id, city, street, house, group, is_repeat=True)

async def _perform_address_check(message: types.Message, user_id: int, city: str, street: str, house: str, group: str = None, is_repeat: bool = False) -> None:
    """Helper function to perform address check (used by repeat and callback handlers)."""
    global db_conn
    user_info = format_user_info(message.from_user) if hasattr(message, 'from_user') and message.from_user else str(user_id)
    
    action = "repeat" if is_repeat else "check"
    logger.info(f"Performing {action} for user {user_id} address: {city}, {street}, {house}")
    
    address_str = f"`{city}, {street}, {house}`"
    prefix = "🔄 **Повторюю перевірку**" if is_repeat else "⏳ **Перевіряю графік**"
    await message.answer(f"{prefix} для: {address_str}...")

    try:
        data = await get_shutdowns_data(city, street, house)
        
        current_hash = get_schedule_hash_compact(data)
        new_group = data.get('group', group)
        
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, city, street, house, last_hash, group_name) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, city, street, house, current_hash, new_group)
        )
        await db_conn.commit()
        
        # Update last_used_at in address book
        await save_user_address(db_conn, user_id, city, street, house, new_group)
        
        sub_count = await get_subscription_count(db_conn, user_id)
        is_subscribed = sub_count > 0
        
        await send_schedule_response(message, data, is_subscribed)
        
        if hasattr(message, 'from_user') and message.from_user:
            await update_user_activity(db_conn, user_id, username=message.from_user.username, city=city, street=street, house=house, group_name=new_group)

    except (ValueError, ConnectionError) as e:
        error_type = "Помилка вводу/помилка API" if isinstance(e, ValueError) else "Помилка"
        await message.answer(f"❌ **{error_type}:** {e}")
    except Exception as e:
        logger.error(f"Critical error during {action} check for user {user_id}: {e}", exc_info=True)
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")

@dp.message(Command("subscribe"))
async def command_subscribe_handler(message: types.Message, state: FSMContext) -> None:
    global db_conn
    user_id = message.from_user.id
    user_info = format_user_info(message.from_user)
    
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

    logger.info(f"Command /subscribe by user {user_info} for address: {city}, {street}, {house}")
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
    
    # Проверяем текущее значение notification_lead_time
    current_lead_time = 0
    cursor = await db_conn.execute("SELECT notification_lead_time FROM subscriptions WHERE user_id = ?", (user_id,))
    row_alert = await cursor.fetchone()
    if row_alert:
        current_lead_time = row_alert[0] if row_alert[0] is not None else 0
    
    # Если алерты выключены (0), включаем их по умолчанию (15 мин)
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
                exists_msg = build_subscription_exists_message(city, street, house, interval_display, new_lead_time)
                await message.answer(exists_msg)
                # Fetch group name if available
                group = None
                try:
                    async with db_conn.execute("SELECT group_name FROM user_last_check WHERE user_id = ?", (user_id,)) as cur:
                        row = await cur.fetchone()
                        if row:
                            group = row[0]
                except Exception:
                    pass
                await update_user_activity(db_conn, user_id, username=message.from_user.username, city=city, street=street, house=house, group_name=group)
                
                # Update lead time if it changed (e.g. from 0 to 15)
                if new_lead_time != current_lead_time:
                     await db_conn.execute(
                        "UPDATE subscriptions SET notification_lead_time = ? WHERE user_id = ?",
                        (new_lead_time, user_id)
                    )
                     await db_conn.commit()
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
    except Exception as e:
        logger.error(f"Failed to write subscription to DB for user {user_id}: {e}", exc_info=True)
        await message.answer("❌ **Помилка БД** при спробі зберегти підписку.")

@dp.message(Command("alert"))
async def cmd_alert(message: types.Message):
    """Wrapper for common handler."""
    await handle_alert(message, get_ctx())

@dp.message(Command("unsubscribe"))
async def command_unsubscribe_handler(message: types.Message) -> None:
    """Wrapper for common handler."""
    await handle_unsubscribe(message, get_ctx())

# --- Callback Handlers for Inline Buttons ---
@dp.callback_query(F.data.startswith("check:"))
async def callback_check_address(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle address selection for /check."""
    global db_conn
    user_id = callback.from_user.id
    data = callback.data.split(":", 1)[1]
    
    await callback.answer()  # Acknowledge the callback
    
    if data == "new":
        # Start step-by-step address input
        await state.set_state(CheckAddressState.waiting_for_city)
        await callback.message.edit_text("📍 **Будь ласка, введіть назву міста** (наприклад, `м. Дніпро`):")
        return
    
    try:
        address_id = int(data)
        address = await get_address_by_id(db_conn, user_id, address_id)
        if not address:
            await callback.message.edit_text("❌ Адреса не знайдена.")
            return
        
        city, street, house = address['city'], address['street'], address['house']
        group = address.get('group_name')
        
        # Note: _perform_address_check will send status message
        await _perform_address_check(callback.message, user_id, city, street, house, group, is_repeat=False)
        
    except ValueError:
        await callback.message.edit_text("❌ Невірний формат даних.")
    except Exception as e:
        logger.error(f"Error in callback_check_address: {e}", exc_info=True)
        await callback.message.edit_text("❌ Виникла помилка.")

@dp.callback_query(F.data.startswith("repeat:"))
async def callback_repeat_address(callback: CallbackQuery) -> None:
    """Handle address selection for /repeat."""
    global db_conn
    user_id = callback.from_user.id
    data = callback.data.split(":", 1)[1]
    
    await callback.answer()
    
    try:
        address_id = int(data)
        address = await get_address_by_id(db_conn, user_id, address_id)
        if not address:
            await callback.message.edit_text("❌ Адреса не знайдена.")
            return
        
        city, street, house = address['city'], address['street'], address['house']
        group = address.get('group_name')
        
        # Note: _perform_address_check will send status message
        await _perform_address_check(callback.message, user_id, city, street, house, group, is_repeat=True)
        
    except ValueError:
        await callback.message.edit_text("❌ Невірний формат даних.")
    except Exception as e:
        logger.error(f"Error in callback_repeat_address: {e}", exc_info=True)
        await callback.message.edit_text("❌ Виникла помилка.")

@dp.callback_query(F.data.startswith("unsub:"))
async def callback_unsubscribe(callback: CallbackQuery) -> None:
    """Wrapper for common handler."""
    await handle_callback_unsubscribe(callback, get_ctx())

# --- Address Book Command and Callbacks ---
@dp.message(Command("addresses"))
async def command_addresses_handler(message: types.Message) -> None:
    """Wrapper for common handler."""
    await handle_addresses_command(message, get_ctx())

@dp.callback_query(F.data.startswith("addr_info:"))
async def callback_address_info(callback: CallbackQuery) -> None:
    """Wrapper for common handler."""
    await handle_callback_address_info(callback, get_ctx())

@dp.callback_query(F.data.startswith("addr_delete:"))
async def callback_address_delete(callback: CallbackQuery) -> None:
    """Wrapper for common handler."""
    await handle_callback_address_delete(callback, get_ctx())

@dp.callback_query(F.data.startswith("addr_rename:"))
async def callback_address_rename_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Wrapper for common handler."""
    await handle_callback_address_rename_start(callback, state, get_ctx())

@dp.message(AddressRenameState.waiting_for_new_name, F.text)
async def process_address_rename(message: types.Message, state: FSMContext) -> None:
    """Wrapper for common handler."""
    await handle_process_address_rename(message, state, get_ctx())

# --- Bot Setup and Main ---
async def set_default_commands(bot: Bot):
    """Устанавливает список команд в меню Telegram."""
    commands = [
        BotCommand(command="start", description="Почати роботу"),
        BotCommand(command="help", description="Показати довідку/команди"),
        BotCommand(command="check", description="Перевірити графік відключень"),
        BotCommand(command="repeat", description="Повторити останню перевірку"),
        BotCommand(command="addresses", description="Керувати адресною книгою"),
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
        logger.error("DTEK_BOT_TOKEN is not set. Exiting.")
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

    logger.info("DTEK Bot started. Beginning polling...")
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
        logger.info("DTEK Bot stopped.")

