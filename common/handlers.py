"""
Common bot handlers for power shutdown bots.
Contains parametrized handler factories that work with BotContext.
"""

import logging
from datetime import datetime
from typing import Optional, Callable

from aiogram import types, F
from aiogram.types import ReplyKeyboardRemove, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
import pytz

from common.bot_base import (
    BotContext,
    CaptchaState,
    CheckAddressState,
    AddressRenameState,
    HUMAN_USERS,
    SCHEDULE_DATA_CACHE,
    get_captcha_data,
    get_hours_str,
    format_user_info,
    is_human_user,
    set_human_user,
    update_user_activity,
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
    get_schedule_hash_compact,
    parse_address_from_text,
)
from common.formatting import (
    process_single_day_schedule_compact,
    get_current_status_message,
)

# ============================================================
# CAPTCHA HANDLERS
# ============================================================

async def handle_captcha_check(message: types.Message, state: FSMContext, ctx: BotContext) -> bool:
    """Check if user passed CAPTCHA. Returns True if passed."""
    user_id = message.from_user.id
    
    # First check memory cache
    if user_id in HUMAN_USERS:
        return True
    
    # Then check database
    if await is_human_user(ctx.db_conn, user_id):
        HUMAN_USERS[user_id] = True
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


async def handle_captcha_answer(message: types.Message, state: FSMContext, ctx: BotContext) -> None:
    """Process CAPTCHA answer."""
    if not message.text:
        return
        
    user_id = message.from_user.id
    user_info = format_user_info(message.from_user)
    data = await state.get_data()
    correct_answer = data.get("captcha_answer")
    
    text = message.text.strip()
    if not text.lstrip('-').isdigit():
        await message.answer("⚠️ Будь ласка, введіть лише число-відповідь.")
        return

    try:
        user_answer = int(text)
    except ValueError:
        user_answer = -9999

    logger = ctx.logger or logging.getLogger(__name__)
    
    if user_answer == correct_answer:
        HUMAN_USERS[user_id] = True
        await set_human_user(ctx.db_conn, user_id, message.from_user.username)
        await state.clear()
        logger.info(f"CAPTCHA passed by user {user_info}")
        await message.answer(
            "✅ **Перевірка пройдена!**\n"
            "Тепер ви можете користуватися всіма функціями бота. Введіть **/start** ще раз, щоб побачити список команд.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await state.clear()
        logger.info(f"CAPTCHA failed by user {user_info}")
        await message.answer(
            "❌ **Неправильна відповідь.** Спробуйте ще раз, ввівши **/start**."
        )


# ============================================================
# SIMPLE COMMAND HANDLERS
# ============================================================

async def handle_cancel(message: types.Message, state: FSMContext) -> None:
    """Handle /cancel command."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Немає активних дій для скасування.")
        return
    await state.clear()
    await message.answer("Дію скасовано. Введіть /check [адреса], щоб почати перевірку, або /check для покрокового вводу.")


async def handle_alert(message: types.Message, ctx: BotContext) -> None:
    """Handle /alert command - set notification lead time."""
    user_id = message.from_user.id
    args = message.text.split()
    
    logger = ctx.logger or logging.getLogger(__name__)

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

    if ctx.db_conn is None:
        await message.answer("❌ Помилка бази даних.")
        return

    try:
        # Check if user has any subscription
        cursor = await ctx.db_conn.execute("SELECT 1 FROM subscriptions WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("❌ Ви ще не підписані на оновлення. Спочатку використайте `/subscribe`.")
            return

        await ctx.db_conn.execute(
            "UPDATE subscriptions SET notification_lead_time = ? WHERE user_id = ?",
            (minutes, user_id)
        )
        await ctx.db_conn.commit()

        if minutes == 0:
            await message.answer("🔕 Сповіщення про наближення подій вимкнено.")
        else:
            await message.answer(f"🔔 Сповіщення встановлено! Ви отримаєте повідомлення за **{minutes} хв.** до зміни статусу світла.")

    except Exception as e:
        logger.error(f"Error setting alert for user {user_id}: {e}")
        await message.answer("❌ Сталася помилка при збереженні налаштувань.")


async def handle_unsubscribe(message: types.Message, ctx: BotContext) -> None:
    """Handle /unsubscribe command with multi-subscription support."""
    user_id = message.from_user.id
    logger = ctx.logger or logging.getLogger(__name__)
    
    try:
        subscriptions = await get_user_subscriptions(ctx.db_conn, user_id)
        
        if not subscriptions:
            await message.answer("❌ **Помилка.** Ви не підписані на оновлення.")
            return
        
        if len(subscriptions) == 1:
            # Single subscription - unsubscribe immediately
            sub = subscriptions[0]
            success = await remove_subscription_by_id(ctx.db_conn, sub['id'])
            if success:
                logger.info(f"User {user_id} unsubscribed from {sub['city']}, {sub['street']}, {sub['house']}")
                await message.answer(
                    f"🚫 **Підписку скасовано** для адреси: `{sub['city']}, {sub['street']}, {sub['house']}`"
                )
            else:
                await message.answer("❌ Не вдалося скасувати підписку.")
        else:
            # Multiple subscriptions - show selection
            keyboard = build_subscription_selection_keyboard(subscriptions, action="unsub")
            await message.answer(
                f"📋 **У вас {len(subscriptions)} активних підписок.** Оберіть, від якої відписатися:",
                reply_markup=keyboard
            )
    except Exception as e:
        logger.error(f"Failed to unsubscribe user {user_id}: {e}", exc_info=True)
        await message.answer("❌ **Помилка БД** при спробі скасувати підписку.")


# ============================================================
# FSM HANDLERS (Address Input)
# ============================================================

async def handle_process_city(message: types.Message, state: FSMContext) -> None:
    """FSM handler for city input."""
    city = message.text.strip()
    await state.update_data(city=city)
    await state.set_state(CheckAddressState.waiting_for_street)
    await message.answer(f"📍 Місто: `{city}`\n**Будь ласка, введіть назву вулиці** (наприклад, `вул. Сонячна набережна`):")


async def handle_process_street(message: types.Message, state: FSMContext) -> None:
    """FSM handler for street input."""
    street = message.text.strip()
    await state.update_data(street=street)
    await state.set_state(CheckAddressState.waiting_for_house)
    await message.answer(f"📍 Вулиця: `{street}`\n**Будь ласка, введіть номер будинку** (наприклад, `6`):")


# ============================================================
# CALLBACK HANDLERS
# ============================================================

async def handle_callback_unsubscribe(callback: CallbackQuery, ctx: BotContext) -> None:
    """Handle unsubscribe selection from inline keyboard."""
    user_id = callback.from_user.id
    data = callback.data.split(":", 1)[1]
    logger = ctx.logger or logging.getLogger(__name__)
    
    await callback.answer()
    
    try:
        if data == "all":
            count = await remove_all_subscriptions(ctx.db_conn, user_id)
            logger.info(f"User {user_id} unsubscribed from all {count} subscriptions.")
            await callback.message.edit_text(
                f"🚫 **Всі підписки скасовано** ({count} шт.)"
            )
        else:
            sub_id = int(data)
            # Get subscription details before removing
            subs = await get_user_subscriptions(ctx.db_conn, user_id)
            sub = next((s for s in subs if s['id'] == sub_id), None)
            
            if sub:
                success = await remove_subscription_by_id(ctx.db_conn, sub_id)
                if success:
                    city, street, house = sub['city'], sub['street'], sub['house']
                    remaining = len(subs) - 1
                    remaining_text = f"\n\n_Залишилось підписок: {remaining}_" if remaining > 0 else ""
                    logger.info(f"User {user_id} unsubscribed from {city}, {street}, {house}")
                    await callback.message.edit_text(
                        f"🚫 **Підписку скасовано** для адреси: `{city}, {street}, {house}`{remaining_text}"
                    )
                else:
                    await callback.message.edit_text("❌ Не вдалося скасувати підписку.")
            else:
                await callback.message.edit_text("❌ Підписку не знайдено.")
                
    except ValueError:
        await callback.message.edit_text("❌ Невірний формат даних.")
    except Exception as e:
        logger.error(f"Error in callback_unsubscribe: {e}", exc_info=True)
        await callback.message.edit_text("❌ Виникла помилка при відписці.")


# ============================================================
# ADDRESS BOOK HANDLERS
# ============================================================

async def handle_addresses_command(message: types.Message, ctx: BotContext) -> None:
    """Handle /addresses command - show saved addresses."""
    user_id = message.from_user.id
    
    addresses = await get_user_addresses(ctx.db_conn, user_id, limit=20)
    
    if not addresses:
        await message.answer(
            "📖 **Ваша адресна книга порожня.**\n\n"
            "Адреси зберігаються автоматично при перевірці графіка командою `/check`."
        )
        return
    
    keyboard = build_address_management_keyboard(addresses)
    await message.answer(
        f"📖 **Ваші збережені адреси** ({len(addresses)} шт.):\n\n"
        "Натисніть на адресу для перегляду деталей або керування.",
        reply_markup=keyboard
    )


async def handle_callback_address_info(callback: CallbackQuery, ctx: BotContext) -> None:
    """Show address info."""
    user_id = callback.from_user.id
    address_id = int(callback.data.split(":", 1)[1])
    
    await callback.answer()
    
    address = await get_address_by_id(ctx.db_conn, user_id, address_id)
    if not address:
        await callback.message.edit_text("❌ Адреса не знайдена.")
        return
    
    alias_text = f"(**{address['alias']}**)" if address.get('alias') else ""
    await callback.message.answer(
        f"📍 **Адреса:** `{address['city']}, {address['street']}, {address['house']}` {alias_text}\n"
        f"👥 **Черга:** {address.get('group_name') or 'Н/Д'}"
    )


async def handle_callback_address_delete(callback: CallbackQuery, ctx: BotContext) -> None:
    """Delete address from address book."""
    user_id = callback.from_user.id
    address_id = int(callback.data.split(":", 1)[1])
    logger = ctx.logger or logging.getLogger(__name__)
    
    await callback.answer()
    
    address = await get_address_by_id(ctx.db_conn, user_id, address_id)
    if not address:
        await callback.message.edit_text("❌ Адреса не знайдена.")
        return
    
    city, street, house = address['city'], address['street'], address['house']
    success = await delete_user_address(ctx.db_conn, user_id, address_id)
    
    if success:
        logger.info(f"User {user_id} deleted address: {city}, {street}, {house}")
        await callback.message.edit_text(
            f"🗑️ **Адресу видалено:** `{city}, {street}, {house}`"
        )
    else:
        await callback.message.edit_text("❌ Не вдалося видалити адресу.")


async def handle_callback_address_rename_start(callback: CallbackQuery, state: FSMContext, ctx: BotContext) -> None:
    """Start address rename flow."""
    user_id = callback.from_user.id
    address_id = int(callback.data.split(":", 1)[1])
    
    await callback.answer()
    
    address = await get_address_by_id(ctx.db_conn, user_id, address_id)
    if not address:
        await callback.message.edit_text("❌ Адреса не знайдена.")
        return
    
    await state.set_state(AddressRenameState.waiting_for_new_name)
    await state.update_data(address_id=address_id)
    
    await callback.message.edit_text(
        f"✏️ **Введіть нову назву** для адреси:\n"
        f"`{address['city']}, {address['street']}, {address['house']}`\n\n"
        "Або /cancel для скасування."
    )


async def handle_process_address_rename(message: types.Message, state: FSMContext, ctx: BotContext) -> None:
    """Process new address alias."""
    user_id = message.from_user.id
    new_alias = message.text.strip()[:50]  # Limit alias length
    logger = ctx.logger or logging.getLogger(__name__)
    
    data = await state.get_data()
    address_id = data.get('address_id')
    
    if not address_id:
        await state.clear()
        await message.answer("❌ Помилка: адресу не знайдено в контексті.")
        return
    
    success = await rename_user_address(ctx.db_conn, user_id, address_id, new_alias)
    await state.clear()
    
    if success:
        logger.info(f"User {user_id} renamed address {address_id} to '{new_alias}'")
        await message.answer(f"✅ **Адресу перейменовано** на: **{new_alias}**")
    else:
        await message.answer("❌ Не вдалося перейменувати адресу.")


# ============================================================
# SCHEDULE RESPONSE HANDLER
# ============================================================

async def send_schedule_response(
    message: types.Message,
    api_data: dict,
    is_subscribed: bool,
    ctx: BotContext,
    generate_24h_image: Callable,
    generate_48h_image: Callable
) -> None:
    """
    Sends formatted schedule response to user.
    
    Args:
        message: Aiogram message object
        api_data: Schedule data from parser
        is_subscribed: Whether user is subscribed to this address
        ctx: BotContext with provider configuration
        generate_24h_image: Function to generate 24h schedule image
        generate_48h_image: Function to generate 48h schedule image
    """
    logger = ctx.logger or logging.getLogger(__name__)
    provider = ctx.provider_name
    font_path = ctx.font_path
    
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

        # Sort dates
        try:
            sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
        except ValueError:
            sorted_dates = sorted(schedule.keys())

        # Generate diagram (24h or 48h)
        has_shutdowns_tomorrow = False
        if len(sorted_dates) >= 2:
            tomorrow_date = sorted_dates[1]
            if schedule.get(tomorrow_date):
                has_shutdowns_tomorrow = True
        
        image_data = None
        diagram_caption = ""
        filename = ""
        
        kiev_tz = pytz.timezone('Europe/Kiev')
        current_time = datetime.now(kiev_tz)

        if has_shutdowns_tomorrow:
            # 48 hours
            all_slots_48h = {}
            for date in sorted_dates[:2]:
                all_slots_48h[date] = schedule.get(date, [])

            if any(slots for slots in all_slots_48h.values()):
                image_data = generate_48h_image(all_slots_48h, font_path, current_time=current_time)
                diagram_caption = "🕙 **Загальний графік на 48 годин**"
                filename = "schedule_48h.png"
        else:
            # 24 hours
            if sorted_dates:
                today_date = sorted_dates[0]
                today_slots = {today_date: schedule.get(today_date, [])}
                if schedule.get(today_date):
                    image_data = generate_24h_image(today_slots, font_path, current_time=current_time)
                    diagram_caption = "🕙 **Графік на сьогодні**"
                    filename = "schedule_24h.png"

        # Build message parts
        message_parts = []
        message_parts.append(f"🏠 Адреса: `{city}, {street}, {house}`\n👥 Черга: `{group}`")
        
        if diagram_caption:
            message_parts.append(diagram_caption)
        
        # Text data by days
        for date in sorted_dates:
            slots = schedule.get(date, [])
            day_text = process_single_day_schedule_compact(date, slots, provider)
            if day_text and day_text.strip():
                message_parts.append(day_text.strip())

        # Status message
        status_msg = get_current_status_message(schedule)
        if status_msg:
            message_parts.append(status_msg)
        
        # Footer
        if not is_subscribed:
            message_parts.append("💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`.")
        
        # Combine all parts
        full_message = "\n\n".join(message_parts)
        
        # Send message with photo and caption
        if image_data:
            # Telegram allows up to 1024 characters in caption
            if len(full_message) <= 1024:
                image_file = BufferedInputFile(image_data, filename=filename)
                await message.answer_photo(
                    photo=image_file,
                    caption=full_message,
                    parse_mode="Markdown"
                )
            else:
                # Send photo with short caption and text separately
                short_caption = "\n\n".join(message_parts[:2])  # Address + diagram
                remaining_text = "\n\n".join(message_parts[2:])  # Rest
                image_file = BufferedInputFile(image_data, filename=filename)
                await message.answer_photo(
                    photo=image_file,
                    caption=short_caption,
                    parse_mode="Markdown"
                )
                await message.answer(remaining_text, parse_mode="Markdown")
        else:
            # No diagram - just send text
            await message.answer(full_message, parse_mode="Markdown")
    
    except Exception as e:
        logger.error(f"Error in send_schedule_response for user {message.from_user.id}: {e}", exc_info=True)
        await message.answer("❌ Сталася помілка під час формування відповіді.")


# ============================================================
# COMMAND HANDLERS
# ============================================================

async def handle_start_command(
    message: types.Message,
    state: FSMContext,
    ctx: BotContext,
    captcha_check_func: Callable,
    example_address: str = "м. Дніпро, вул. Сонячна набережна, 6"
) -> None:
    """
    Handle /start and /help commands.
    
    Args:
        message: Aiogram message object
        state: FSM context
        ctx: BotContext with provider configuration
        captcha_check_func: Function to check CAPTCHA
        example_address: Example address for the help text
    """
    user_id = message.from_user.id
    user_info = format_user_info(message.from_user)
    logger = ctx.logger or logging.getLogger(__name__)
    provider = ctx.provider_name
    
    logger.info(f"Command /start by user {user_info}")
    
    if user_id not in HUMAN_USERS:
        logger.info(f"CAPTCHA requested for user {user_info}")
        is_human = await captcha_check_func(message, state)
        if not is_human:
            return

    text = (
        f"👋 **Вітаю! Я бот для перевірки графіків відключень {provider}.**\n"
        "Для перевірки графіку, введіть команду **/check**, додавши адресу у форматі:\n"
        "`/check Місто, Вулиця, Будинок`\n"
        "**АБО** просто введіть **/check** без адреси, щоб ввести дані покроково.\n"
        "**Наприклад:**\n"
        f"`/check {example_address}`\n"
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
    await update_user_activity(ctx.db_conn, user_id, username=message.from_user.username)


async def handle_stats_command(
    message: types.Message,
    ctx: BotContext,
    admin_ids: list
) -> None:
    """
    Handle /stats command - show admin statistics.
    
    Args:
        message: Aiogram message object
        ctx: BotContext with provider configuration
        admin_ids: List of admin user IDs
    """
    import os
    import csv
    import io
    from aiogram.types import BufferedInputFile
    
    user_id = message.from_user.id
    logger = ctx.logger or logging.getLogger(__name__)
    provider = ctx.provider_name
    db_conn = ctx.db_conn

    if user_id not in admin_ids:
        await message.answer("⛔ **Відмовлено в доступі.** У вас недостатньо прав для перегляду статистики.")
        return

    await message.answer("⏳ **Збираю статистику...**")
    
    try:
        # 1. Summary
        async with db_conn.execute("SELECT COUNT(*) FROM user_activity") as cursor:
            total_users = (await cursor.fetchone())[0]

        async with db_conn.execute("SELECT COUNT(*) FROM subscriptions") as cursor:
            total_subs = (await cursor.fetchone())[0]

        async with db_conn.execute("SELECT COUNT(DISTINCT user_id) FROM subscriptions") as cursor:
            unique_subscribers = (await cursor.fetchone())[0]
        
        async with db_conn.execute("SELECT COUNT(*) FROM subscriptions WHERE notification_lead_time > 0") as cursor:
            alerts_enabled = (await cursor.fetchone())[0]
        
        # Date stats
        async with db_conn.execute(
            "SELECT COUNT(*) FROM user_activity WHERE first_seen >= date('now', '-7 days')"
        ) as cursor:
            new_week = (await cursor.fetchone())[0]
        
        async with db_conn.execute(
            "SELECT COUNT(*) FROM user_activity WHERE last_seen >= date('now', '-7 days')"
        ) as cursor:
            active_week = (await cursor.fetchone())[0]
        
        summary = (
            f"📊 **Статистика бота {provider}**\n\n"
            f"👥 **Всього користувачів:** {total_users}\n"
            f"   ├ Нових за тиждень: {new_week}\n"
            f"   └ Активних за тиждень: {active_week}\n\n"
            f"📬 **Підписки:** {total_subs} (у {unique_subscribers} користувачів)\n"
            f"   └ Сповіщення увімкнено: {alerts_enabled}\n"
        )
        
        await message.answer(summary)
        
        # 2. User export CSV
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(['user_id', 'username', 'first_seen', 'last_seen', 'last_city', 'last_street', 'last_house'])
        
        async with db_conn.execute(
            "SELECT user_id, username, first_seen, last_seen, last_city, last_street, last_house FROM user_activity ORDER BY last_seen DESC"
        ) as cursor:
            async for row in cursor:
                writer.writerow(row)
        
        csv_buffer.seek(0)
        csv_data = csv_buffer.getvalue().encode('utf-8')
        
        csv_file = BufferedInputFile(csv_data, filename=f"{provider.lower()}_users_export.csv")
        await message.answer_document(csv_file, caption="📁 Експорт користувачів")
        
        logger.info(f"Stats requested by admin {user_id}")

    except Exception as e:
        logger.error(f"Error generating stats: {e}", exc_info=True)
        # Escape error message to avoid Telegram parsing issues
        error_str = str(e).replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
        await message.answer(f"❌ Помилка при формуванні статистики: {error_str}")


async def handle_process_house(
    message: types.Message,
    state: FSMContext,
    ctx: BotContext,
    get_shutdowns_data: Callable,
    send_response_func: Callable
) -> None:
    """
    FSM handler for house number input - completes the address check.
    
    Args:
        message: Aiogram message object
        state: FSM context
        ctx: BotContext with provider configuration
        get_shutdowns_data: Async function to fetch schedule data
        send_response_func: Function to send formatted response
    """
    user_id = message.from_user.id
    house = message.text.strip()
    data = await state.get_data()
    city = data.get('city', '')
    street = data.get('street', '')
    address_str = f"`{city}, {street}, {house}`"
    
    logger = ctx.logger or logging.getLogger(__name__)
    db_conn = ctx.db_conn
    
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
        
        await send_response_func(message, api_data, is_subscribed)
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


async def handle_check_command(
    message: types.Message,
    state: FSMContext,
    ctx: BotContext,
    captcha_check_func: Callable,
    get_shutdowns_data: Callable,
    send_response_func: Callable,
    example_city: str = "м. Дніпро"
) -> None:
    """
    Handle /check command - check power schedule for address.
    
    Args:
        message: Aiogram message object
        state: FSM context
        ctx: BotContext with provider configuration
        captcha_check_func: Function to check CAPTCHA
        get_shutdowns_data: Async function to fetch schedule data  
        send_response_func: Function to send formatted response
        example_city: Example city for FSM prompt
    """
    user_id = message.from_user.id
    user_info = format_user_info(message.from_user)
    logger = ctx.logger or logging.getLogger(__name__)
    db_conn = ctx.db_conn
    
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await captcha_check_func(message, state)
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
            await message.answer(f"📍 **Будь ласка, введіть назву міста** (наприклад, `{example_city}`):")
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
        
        await send_response_func(message, api_data, is_subscribed)
        await update_user_activity(db_conn, user_id, username=message.from_user.username, city=city, street=street, house=house, group_name=group)

    except ValueError as e:
        await message.answer(f"❌ **Помилка вводу:** {e}")
    except ConnectionError as e:
        await message.answer(f"❌ **Помилка підключення:** {e}")
    except Exception as e:
        logger.error(f"Critical error in /check for user {user_id}: {e}", exc_info=True)
        await message.answer("❌ Виникла непередбачена помилка. Спробуйте пізніше.")


async def handle_repeat_command(
    message: types.Message,
    state: FSMContext,
    ctx: BotContext,
    captcha_check_func: Callable,
    perform_check_func: Callable
) -> None:
    """
    Handle /repeat command - repeat last address check.
    
    Args:
        message: Aiogram message object
        state: FSM context
        ctx: BotContext with provider configuration
        captcha_check_func: Function to check CAPTCHA
        perform_check_func: Function to perform address check
    """
    user_id = message.from_user.id
    user_info = format_user_info(message.from_user)
    logger = ctx.logger or logging.getLogger(__name__)
    db_conn = ctx.db_conn
    
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await captcha_check_func(message, state)
        return

    try:
        async with db_conn.execute(
            "SELECT city, street, house, group_name FROM user_last_check WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await message.answer("❔ У вас ще немає збережених перевірок. Скористайтесь командою /check.")
            return

        city, street, house, group = row
        logger.info(f"Command /repeat by user {user_info} for address: {city}, {street}, {house}")
        
        await perform_check_func(message, user_id, city, street, house, group, is_repeat=True)

    except Exception as e:
        logger.error(f"Error in /repeat for user {user_id}: {e}", exc_info=True)
        await message.answer("❌ Виникла помилка при повторенні перевірки.")


async def perform_address_check(
    message: types.Message,
    user_id: int,
    city: str,
    street: str,
    house: str,
    ctx: BotContext,
    get_shutdowns_data: Callable,
    send_response_func: Callable,
    group: str = None,
    is_repeat: bool = False
) -> None:
    """
    Helper function to perform address check (used by repeat and callback handlers).
    
    Args:
        message: Aiogram message object
        user_id: Telegram user ID
        city: City name
        street: Street name
        house: House number
        ctx: BotContext with provider configuration
        get_shutdowns_data: Async function to fetch schedule data
        send_response_func: Function to send formatted response
        group: Optional group name (from cache)
        is_repeat: Whether this is a repeat check
    """
    logger = ctx.logger or logging.getLogger(__name__)
    db_conn = ctx.db_conn
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
        
        await send_response_func(message, data, is_subscribed)
        
        if hasattr(message, 'from_user') and message.from_user:
            await update_user_activity(db_conn, user_id, username=message.from_user.username, city=city, street=street, house=house, group_name=new_group)

    except (ValueError, ConnectionError) as e:
        error_type = "Помилка вводу/помилка API" if isinstance(e, ValueError) else "Помилка"
        await message.answer(f"❌ **{error_type}:** {e}")
    except Exception as e:
        logger.error(f"Critical error during {action} check for user {user_id}: {e}", exc_info=True)
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")


async def handle_callback_check_address(
    callback: CallbackQuery,
    state: FSMContext,
    ctx: BotContext,
    perform_check_func: Callable
) -> None:
    """
    Handle callback for checking address from saved list.
    
    Args:
        callback: Aiogram callback query
        state: FSM context
        ctx: BotContext with provider configuration
        perform_check_func: Function to perform address check
    """
    user_id = callback.from_user.id
    data_parts = callback.data.split(":", 1)[1]
    logger = ctx.logger or logging.getLogger(__name__)
    db_conn = ctx.db_conn
    
    await callback.answer()
    
    if data_parts == "new":
        # User wants to enter new address
        await state.set_state(CheckAddressState.waiting_for_city)
        await callback.message.answer("📍 **Будь ласка, введіть назву міста:**")
        return
    
    try:
        address_id = int(data_parts)
        address = await get_address_by_id(db_conn, user_id, address_id)
        
        if not address:
            await callback.message.answer("❌ Адреса не знайдена.")
            return
        
        city, street, house = address['city'], address['street'], address['house']
        group = address.get('group_name')
        
        await perform_check_func(callback.message, user_id, city, street, house, group, is_repeat=False)
        
    except ValueError:
        await callback.message.answer("❌ Невірний формат даних.")
    except Exception as e:
        logger.error(f"Error in callback_check_address: {e}", exc_info=True)
        await callback.message.answer("❌ Виникла помилка.")


async def handle_callback_repeat_address(
    callback: CallbackQuery,
    ctx: BotContext,
    perform_check_func: Callable
) -> None:
    """
    Handle callback for repeating address check.
    
    Args:
        callback: Aiogram callback query
        ctx: BotContext with provider configuration
        perform_check_func: Function to perform address check
    """
    user_id = callback.from_user.id
    data_parts = callback.data.split(":", 1)[1]
    logger = ctx.logger or logging.getLogger(__name__)
    db_conn = ctx.db_conn
    
    await callback.answer()
    
    try:
        address_id = int(data_parts)
        address = await get_address_by_id(db_conn, user_id, address_id)
        
        if not address:
            await callback.message.answer("❌ Адреса не знайдена.")
            return
        
        city, street, house = address['city'], address['street'], address['house']
        group = address.get('group_name')
        
        await perform_check_func(callback.message, user_id, city, street, house, group, is_repeat=True)
        
    except ValueError:
        await callback.message.answer("❌ Невірний формат даних.")
    except Exception as e:
        logger.error(f"Error in callback_repeat_address: {e}", exc_info=True)
        await callback.message.answer("❌ Виникла помилка.")


async def handle_subscribe_command(
    message: types.Message,
    state: FSMContext,
    ctx: BotContext,
    captcha_check_func: Callable
) -> None:
    """
    Handle /subscribe command - subscribe to schedule updates.
    
    Args:
        message: Aiogram message object
        state: FSM context
        ctx: BotContext with provider configuration
        captcha_check_func: Function to check CAPTCHA
    """
    from .bot_base import DEFAULT_INTERVAL_HOURS, get_hours_str
    from .formatting import build_subscription_exists_message, build_subscription_created_message
    
    user_id = message.from_user.id
    user_info = format_user_info(message.from_user)
    logger = ctx.logger or logging.getLogger(__name__)
    db_conn = ctx.db_conn
    
    if user_id not in HUMAN_USERS:
        await message.answer("⛔ **Відмовлено в доступі.** Будь ласка, спочатку пройдіть перевірку "
                             "за допомогою команди **/start**.")
        await captcha_check_func(message, state)
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
    
    # Check current notification_lead_time
    current_lead_time = 0
    try:
        cursor_tmp = await db_conn.execute("SELECT notification_lead_time FROM subscriptions WHERE user_id = ?", (user_id,))
        row_alert_tmp = await cursor_tmp.fetchone()
        if row_alert_tmp:
            current_lead_time = row_alert_tmp[0] if row_alert_tmp[0] is not None else 0
    except Exception:
        current_lead_time = 0
    
    # If alerts are off (0), enable them by default (15 min)
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
        
        logger.info(f"User {user_id} subscribed/updated to {city}, {street}, {house} with interval {interval_hours}h. Alert: {new_lead_time}m")
        created_msg = build_subscription_created_message(city, street, house, interval_display, new_lead_time, current_lead_time)
        await message.answer(created_msg)
        await update_user_activity(db_conn, user_id, username=message.from_user.username, city=city, street=street, house=house, group_name=group)
    except Exception as e:
        logger.error(f"Failed to write subscription to DB for user {user_id}: {e}", exc_info=True)
        await message.answer("❌ **Помилка БД** при спробі зберегти підписку.")
