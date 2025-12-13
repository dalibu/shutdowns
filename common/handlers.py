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
    get_address_by_id,  # User address book function
    delete_user_address,
    rename_user_address,
    get_user_subscriptions,
    get_subscription_count,
    is_address_subscribed,
    remove_subscription_by_id,
    remove_all_subscriptions,
    remove_group_subscription,  # For group subscription removal
    build_address_selection_keyboard,
    build_subscription_selection_keyboard,
    build_address_management_keyboard,
    get_schedule_hash_compact,
    parse_address_from_text,
    detect_check_input_type,  # For group detection in /check
    get_group_cache,
    update_group_cache,
    get_group_for_address,
    update_address_group_mapping,
    get_address_id,  # New normalized function
    update_address_group,  # New normalized function
    get_address_data_by_id,  # Get address data from addresses table
)
from common.handlers_group_subscription import handle_group_subscription
from common.formatting import (
    process_single_day_schedule_compact,
    get_current_status_message,
    format_group_name,
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
        logger.info("CAPTCHA passed")
        await message.answer(
            "✅ **Перевірка пройдена!**\n"
            "Тепер ви можете користуватися всіма функціями бота. Введіть **/start** ще раз, щоб побачити список команд.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await state.clear()
        logger.info("CAPTCHA failed")
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
        logger.error(f"Error setting alert: {e}")
        await message.answer("❌ Сталася помилка при збереженні налаштувань.")


async def handle_unsubscribe(message: types.Message, ctx: BotContext) -> None:
    """Handle /unsubscribe command with support for both address and group subscriptions."""
    user_id = message.from_user.id
    logger = ctx.logger or logging.getLogger(__name__)
    
    try:
        subscriptions = await get_user_subscriptions(ctx.db_conn, user_id, ctx.provider_code)
        
        if not subscriptions:
            await message.answer("❌ **Помилка.** Ви не підписані на оновлення.", parse_mode="Markdown")
            return
        
        if len(subscriptions) == 1:
            # Single subscription - unsubscribe immediately
            sub = subscriptions[0]
            
            if sub['type'] == 'group':
                # Group subscription
                success = await remove_group_subscription(ctx.db_conn, sub['id'])
                if success:
                    from .formatting import format_group_name
                    logger.info(f"Unsubscribed from group {sub['group_name']}")
                    await message.answer(
                        f"🚫 **Підписку скасовано** для черги: `{format_group_name(sub['group_name'])}`",
                        parse_mode="Markdown"
                    )
                else:
                    await message.answer("❌ Не вдалося скасувати підписку.")
            else:
                # Address subscription
                success = await remove_subscription_by_id(ctx.db_conn, user_id, sub['id'])
                if success:
                    logger.info(f"Unsubscribed from {sub['city']}, {sub['street']}, {sub['house']}")
                    await message.answer(
                        f"🚫 **Підписку скасовано** для адреси: `{sub['city']}, {sub['street']}, {sub['house']}`",
                        parse_mode="Markdown"
                    )
                else:
                    await message.answer("❌ Не вдалося скасувати підписку.")
        else:
            # Multiple subscriptions - show selection
            keyboard = build_subscription_selection_keyboard(subscriptions, action="unsub")
            await message.answer(
                f"📋 **У вас {len(subscriptions)} активних підписок.** Оберіть, від якої відписатися:",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Failed to unsubscribe: {e}", exc_info=True)
        await message.answer("❌ **Помилка БД** при спробі скасувати підписку.", parse_mode="Markdown")


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
    """Handle unsubscribe selection from inline keyboard (address or group)."""
    user_id = callback.from_user.id
    data_parts = callback.data.split(":", 2)  # Changed to handle "unsub:group:123"
    logger = ctx.logger or logging.getLogger(__name__)
    
    await callback.answer()
    
    try:
        # Extract action and data
        action = data_parts[0]  # "unsub"
        
        if len(data_parts) == 2 and data_parts[1] == "all":
            # Unsubscribe from all (both address and group)
            count_addr = await remove_all_subscriptions(ctx.db_conn, user_id)
            
            # Also remove all group subscriptions
            count_group = 0
            if ctx.provider_code:
                try:
                    cursor = await ctx.db_conn.execute(
                        "DELETE FROM group_subscriptions WHERE user_id = ? AND provider = ?",
                        (user_id, ctx.provider_code)
                    )
                    await ctx.db_conn.commit()
                    count_group = cursor.rowcount
                except Exception as e:
                    logger.error(f"Failed to remove group subscriptions: {e}")
            
            total_count = count_addr + count_group
            logger.info(f"Unsubscribed from all {total_count} subscriptions ({count_addr} addr, {count_group} group).")
            await callback.message.edit_text(
                f"�️ **Всі підписки скасовано** ({total_count} шт.)",
                parse_mode="Markdown"
            )
        elif len(data_parts) == 3 and data_parts[1] == "group":
            # Group subscription: "unsub:group:123"
            sub_id = int(data_parts[2])
            
            # Get subscription details before removing
            subs = await get_user_subscriptions(ctx.db_conn, user_id, ctx.provider_code)
            sub = next((s for s in subs if s['type'] == 'group' and s['id'] == sub_id), None)
            
            if sub:
                success = await remove_group_subscription(ctx.db_conn, sub_id)
                if success:
                    from .formatting import format_group_name
                    group_name = sub['group_name']
                    logger.info(f"Unsubscribed from group {group_name}")
                    await callback.message.edit_text(
                        f"🚫 **Підписку скасовано** для черги: `{format_group_name(group_name)}`",
                        parse_mode="Markdown"
                    )
                else:
                    await callback.message.edit_text("❌ Не вдалося скасувати підписку.")
            else:
                await callback.message.edit_text("❌ Підписку не знайдено.")
        else:
            # Address subscription: "unsub:123"
            sub_id = int(data_parts[1])
            
            # Get subscription details before removing
            subs = await get_user_subscriptions(ctx.db_conn, user_id, ctx.provider_code)
            sub = next((s for s in subs if s.get('type') != 'group' and s['id'] == sub_id), None)
            
            if sub:
                success = await remove_subscription_by_id(ctx.db_conn, user_id, sub_id)
                if success:
                    city, street, house = sub['city'], sub['street'], sub['house']
                    logger.info(f"Unsubscribed from {city}, {street}, {house}")
                    await callback.message.edit_text(
                        f"🚫 **Підписку скасовано** для адреси: `{city}, {street}, {house}`",
                        parse_mode="Markdown"
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
        f"👥 **Черга:** {format_group_name(address.get('group_name'))}"
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
        logger.info(f"Deleted address: {city}, {street}, {house}")
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
        logger.info(f"Renamed address {address_id} to '{new_alias}'")
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
        group = format_group_name(api_data.get("group"))

        schedule = api_data.get("schedule", {})
        
        # Check for current outage information
        # Only show outage warning if:
        # 1. There is NO schedule table (empty schedule)
        # 2. The outage contains parsed details from regex (not just raw message)
        outage_warning = None
        current_outage = api_data.get("current_outage")
        
        # Determine if we should show outage warning
        has_schedule_table = bool(schedule)
        
        if current_outage and current_outage.get("has_current_outage") and not has_schedule_table:
            # Check if outage has any structured details extracted by regex
            has_details = any([
                current_outage.get("reason"),
                current_outage.get("start_time"),
                current_outage.get("expected_restoration"),
                current_outage.get("update_time")
            ])
            
            # Only show outage warning if it has extracted details
            if has_details:
                # Format outage warning message
                outage_parts = ["⚡ **УВАГА! Поточне відключення**\n"]
                
                # Add detailed information if available
                if current_outage.get("reason"):
                    outage_parts.append(f"🔧 **Причина:** {current_outage['reason']}")
                
                if current_outage.get("start_time"):
                    outage_parts.append(f"⏰ **Початок:** {current_outage['start_time']}")
                
                if current_outage.get("expected_restoration"):
                    outage_parts.append(f"🔋 **Відновлення:** {current_outage['expected_restoration']}")
                
                if current_outage.get("update_time"):
                    outage_parts.append(f"📅 _Оновлено: {current_outage['update_time']}_")
                
                outage_warning = "\n".join(outage_parts)
        if not schedule:
            # No schedule, only show outage warning if exists
            if outage_warning:
                full_message = f"📍 Адреса: `{city}, {street}, {house}`"
                if group != "невідомо":
                    full_message += f"\n👥 Черга: `{group}`"
                full_message += f"\n\n{outage_warning}"
                
                if not is_subscribed:
                    full_message += "\n\n💡 *Ви можете підписатися на автоматичні оновлення графіку для цієї адреси, використовуючи команду* `/subscribe`."
                
                await message.answer(full_message, parse_mode="Markdown")
                return
            else:
                # No schedule and no outage
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
        
        # Show address line only for real addresses, not for group-only checks
        # Group checks have city like "Черга 3.1" with empty street/house
        if street or house:  # Real address
            message_parts.append(f"📍 Адреса: `{city}, {street}, {house}`\n👥 Черга: `{group}`")
        else:  # Group-only check
            message_parts.append(f"👥 Черга: `{group}`")
        
        # Add current outage warning if exists
        if outage_warning:
            message_parts.append(outage_warning)
        
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
        logger.error(f"Error in send_schedule_response: {e}", exc_info=True)
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
    
    logger.info("Command /start")
    
    if user_id not in HUMAN_USERS:
        logger.info("CAPTCHA requested")
        is_human = await captcha_check_func(message, state)
        if not is_human:
            return

    text = (
        f"👋 **Вітаю! Я бот (неофиційний, але найкращій та найефективніший 😉) для перевірки графіків відключень {provider}.**\n\n"
        "**Для перевірки графіку** введіть команду **/check**, додавши адресу або номер черги:\n\n"
        "**За адресою:**\n"
        "`/check м. Місто, вул. Вулиця, Будинок`\n"
        f"*Наприклад:* `/check {example_address}`\n\n"
        "**За номером черги (миттєво! ⚡):**\n"
        "`/check 3.1` або `/check 3,1`\n\n"
        "Або просто введіть **/check** без параметрів для покрокового вводу.\n\n"
        "**Команди:**\n"
        "/start або /help - показати цю довідку.\n"
        "/check - перевірити графік за адресою або номером черги.\n"
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
        writer.writerow(['user_id', 'username', 'first_seen', 'last_seen', 'last_city', 'last_street', 'last_house', 'last_group'])
        
        async with db_conn.execute(
            "SELECT user_id, username, first_seen, last_seen, last_city, last_street, last_house, last_group FROM user_activity ORDER BY last_seen DESC"
        ) as cursor:
            async for row in cursor:
                writer.writerow(row)
        
        csv_buffer.seek(0)
        csv_data = csv_buffer.getvalue().encode('utf-8')
        
        # Generate filename with timestamp and latin prefix
        kiev_tz = pytz.timezone('Europe/Kiev')
        timestamp = datetime.now(kiev_tz).strftime("%Y%m%d_%H%M%S")
        filename_prefix = provider.lower().replace('дтек', 'dtek').replace('цек', 'cek')
        filename = f"{filename_prefix}_users_export_{timestamp}.csv"
        
        csv_file = BufferedInputFile(csv_data, filename=filename)
        await message.answer_document(csv_file, caption="📁 Експорт користувачів")
        
        logger.info("Stats requested (admin)")

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
        # Get or create address_id
        address_id, _ = await get_address_id(db_conn, city, street, house)
        if not address_id:
            raise Exception("Failed to get/create address")
        
        api_data = await get_shutdowns_data(city, street, house)
        current_hash = get_schedule_hash_compact(api_data)
        group = api_data.get('group', None)
        
        # Update address group
        if group:
            await update_address_group(db_conn, address_id, group)
        
        # Save to user_last_check with address_id
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, address_id, last_hash) VALUES (?, ?, ?)",
            (user_id, address_id, current_hash)
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
        logger.error(f"Critical error during FSM address process: {e}", exc_info=True)
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
    Handle /check command - check power schedule for address OR group.
    
    Now intelligently detects input type:
    - /check 3.1 or /check 3,1 → checks group schedule
    - /check м. Дніпро, вул. ... → checks address schedule
    
    Args:
        message: Aiogram message object
        state: FSM context
        ctx: BotContext with provider configuration
        captcha_check_func: Function to check CAPTCHA
        get_shutdowns_data: Async function to fetch schedule data  
        send_response_func: Function to send formatted response
        example_city: Example city for FSM prompt
    """
    from common.bot_base import (
        find_addresses_by_group,
        detect_check_input_type
    )
    
    user_id = message.from_user.id
    user_info = format_user_info(message.from_user)
    logger = ctx.logger or logging.getLogger(__name__)
    db_conn = ctx.db_conn
    provider_code = ctx.provider_code
    
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
            logger.info(f"Command /check (address selection), {len(addresses)} addresses")
            keyboard = build_address_selection_keyboard(addresses, action="check", include_new_button=True)
            await message.answer(
                "📍 **Оберіть адресу для перевірки** або додайте нову:",
                reply_markup=keyboard
            )
            return
        else:
            logger.info("Command /check (FSM)")
            await state.set_state(CheckAddressState.waiting_for_city)
            await message.answer(f"📍 **Будь ласка, введіть назву міста** (наприклад, `{example_city}`):")
            return

    current_state = await state.get_state()
    if current_state:
        await state.clear()

    # ===== NEW: Detect input type (group or address) =====
    input_type, parsed_value = detect_check_input_type(text_args)
    
    # ===== BRANCH 1: GROUP CHECK =====
    if input_type == "group":
        group_name = parsed_value
        logger.info(f"Command /check for group: {group_name}")
        
        try:
            # Step 1: Check group cache
            group_cache = await get_group_cache(db_conn, group_name, provider_code)
            
            if group_cache:
                # Cache HIT! Show schedule from cache
                logger.info(f"✓ Group cache HIT for /check {group_name} (instant response)")
                api_data = group_cache['data']
                
                # Override address information to show group instead
                api_data_for_display = api_data.copy()
                api_data_for_display['city'] = f"Черга {format_group_name(group_name)}"
                api_data_for_display['street'] = ""
                api_data_for_display['house_num'] = ""
                api_data_for_display['group'] = group_name
                
                await send_response_func(message, api_data_for_display, False)
                await update_user_activity(db_conn, user_id, username=message.from_user.username, group_name=group_name)
                return
            
            # Step 2: Cache miss - try to find a known address from this group
            logger.info(f"✗ Group cache MISS for /check {group_name}")
            addresses = await find_addresses_by_group(db_conn, provider_code, group_name, limit=1)
            
            if not addresses:
                # Group is completely unknown to us
                logger.info(f"Group {group_name} is unknown (no addresses found)")
                await message.answer(
                    f"❌ **Черга `{format_group_name(group_name)}` невідома.**\n\n"
                    "Ми ще не маємо інформації про цю чергу. "
                    "Будь ласка, спочатку перевірте графік за адресою (наприклад, `/check м. Дніпро, вул. Сонячна набережна, 6`), "
                    "щоб ми могли визначити, які адреси належать до цієї черги."
                )
                return
            
            # Step 3: Found an address - use it to get fresh data
            addr = addresses[0]
            city, street, house = addr['city'], addr['street'], addr['house']
            
            logger.info(f"Found address for group {group_name}: {city}, {street}, {house}")
            await message.answer(f"⏳ Оновлюю графік для черги `{format_group_name(group_name)}`... Очікуйте...")
            
            # Get fresh data from parser
            api_data = await get_shutdowns_data(city, street, house)
            current_hash = get_schedule_hash_compact(api_data)
            group_from_parser = api_data.get('group', None)
            
            # Update group cache with fresh data
            if group_from_parser:
                await update_group_cache(db_conn, group_from_parser, provider_code, current_hash, api_data)
                logger.debug(f"Updated group cache for {group_from_parser} after /check")
                
                # Also verify/update address group mapping
                address_id, _ = await get_address_id(db_conn, city, street, house)
                if address_id:
                    await update_address_group(db_conn, address_id, group_from_parser)
            
            # Override address information to show group instead
            api_data_for_display = api_data.copy()
            api_data_for_display['city'] = f"Черга {format_group_name(group_name)}"
            api_data_for_display['street'] = ""
            api_data_for_display['house_num'] = ""
            
            await send_response_func(message, api_data_for_display, False)
            await update_user_activity(db_conn, user_id, username=message.from_user.username, group_name=group_name)
            
        except ValueError as e:
            logger.error(f"Group check error: {e}")
            await message.answer(f"❌ {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in /check (group): {e}", exc_info=True)
            await message.answer("❌ **Помилка** при перевірці графіку для черги.")
        
        return
    
    # ===== BRANCH 2: ADDRESS CHECK (original logic) =====
    await message.answer("⏳ Перевіряю графік за вказаною адресою. Очікуйте...")
    try:
        city, street, house = parse_address_from_text(text_args)
        logger.info(f"Command /check for address: {city}, {street}, {house}")
        
        # Get or create address_id
        address_id, _ = await get_address_id(db_conn, city, street, house)
        if not address_id:
            raise Exception("Failed to get/create address")
        
        api_data = await get_shutdowns_data(city, street, house)
        current_hash = get_schedule_hash_compact(api_data)
        group = api_data.get('group', None)
        
        # Update address group
        if group:
            await update_address_group(db_conn, address_id, group)
        
        # Save to user_last_check with address_id
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, address_id, last_hash) VALUES (?, ?, ?)",
            (user_id, address_id, current_hash)
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
        logger.error(f"Critical error in /check: {e}", exc_info=True)
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
        async with db_conn.execute("""
            SELECT a.city, a.street, a.house, a.group_name
            FROM user_last_check ulc
            JOIN addresses a ON a.id = ulc.address_id
            WHERE ulc.user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()

        if not row:
            await message.answer("❔ У вас ще немає збережених перевірок. Скористайтесь командою /check.")
            return

        city, street, house, group = row
        logger.info(f"Command /repeat for address: {city}, {street}, {house}")
        
        await perform_check_func(message, user_id, city, street, house, group, is_repeat=True)

    except Exception as e:
        logger.error(f"Error in /repeat: {e}", exc_info=True)
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
    logger.info(f"Performing {action} for address: {city}, {street}, {house}")
    
    address_str = f"`{city}, {street}, {house}`"
    prefix = "🔄 **Повторюю перевірку**" if is_repeat else "⏳ **Перевіряю графік**"
    await message.answer(f"{prefix} для: {address_str}...")

    try:
        # === GROUP CACHE OPTIMIZATION (with normalized addresses) ===
        # Get or create address_id
        address_id, cached_group = await get_address_id(
            db_conn, city, street, house
        )
        
        if not address_id:
            raise Exception("Failed to get/create address_id")
        
        data = None
        current_hash = None
        
        if cached_group:
            logger.debug(f"Check: address [ID:{address_id}] belongs to group {cached_group}")
            
            # Try to get from group cache
            group_cache = await get_group_cache(
                db_conn, cached_group, ctx.provider_code
            )
            
            if group_cache:
                # Use cached data
                logger.info(f"Check: using group cache for {cached_group}")
                data = group_cache['data']
                current_hash = group_cache['hash']
        
        # Fetch from provider if cache miss or group unknown
        if data is None:
            logger.debug(f"Check: calling parser for {address_str}")
            data = await get_shutdowns_data(city, street, house)
            current_hash = get_schedule_hash_compact(data)
            
            # Update group cache
            if data.get('group'):
                await update_group_cache(
                    db_conn, data['group'], ctx.provider_code,
                    current_hash, data
                )
        
        new_group = data.get('group', group)
        
        # Update address group in normalized table
        if new_group:
            await update_address_group(db_conn, address_id, new_group)
        
        # Save to user_last_check (now using address_id)
        await db_conn.execute(
            "INSERT OR REPLACE INTO user_last_check (user_id, address_id, last_hash) VALUES (?, ?, ?)",
            (user_id, address_id, current_hash)
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
        logger.error(f"Critical error during {action} check: {e}", exc_info=True)
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
    
    Supports both address and group subscriptions:
    - /subscribe → subscribes to last checked address
    - /subscribe 3.1 → subscribes to group 3.1
    
    Args:
        message: Aiogram message object
        state: FSM context
        ctx: BotContext with provider configuration
        captcha_check_func: Function to check CAPTCHA
    """
    from .bot_base import DEFAULT_INTERVAL_HOURS, get_hours_str, detect_check_input_type
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

    # Check if user is trying to subscribe to a group directly
    text_args = message.text.replace('/subscribe', '', 1).strip()
    
    # Extract interval if specified (e.g., "/subscribe 3.1 6" or "/subscribe 6")
    interval_hours = DEFAULT_INTERVAL_HOURS
    parts = text_args.split() if text_args else []
    
    # Try to detect group subscription
    if parts:
        first_part = parts[0]
        input_type, value = detect_check_input_type(first_part)
        
        if input_type == "group":
            # Group subscription!
            group_name = value
            
            # Check for interval in second part
            if len(parts) > 1:
                try:
                    val = float(parts[1].replace(',', '.'))
                    if val <= 0.0:
                        await message.answer("❌ **Помилка.** Інтервал має бути позитивним числом годин.")
                        return
                    if val < 0.5:
                        await message.answer("❌ **Помилка.** Мінімальний інтервал перевірки — 0.5 години (30 хвилин).")
                        return
                    interval_hours = val
                except ValueError:
                    await message.answer("❌ **Помилка.** Інтервал повинен бути числом (наприклад, `/subscribe 3.1 6`).")
                    return
            
            # Handle group subscription
            await handle_group_subscription(message, group_name, interval_hours, ctx)
            return

    # Original address subscription logic continues...
    city, street, house, hash_from_check = None, None, None, None
    try:
        # After migration 006, user_last_check only has address_id
        # Need to JOIN with addresses to get city, street, house
        cursor = await db_conn.execute("""
            SELECT a.city, a.street, a.house, ulc.last_hash 
            FROM user_last_check ulc
            JOIN addresses a ON a.id = ulc.address_id
            WHERE ulc.user_id = ?
        """, (user_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("❌ **Помилка.** Спочатку вам потрібно перевірити графік за допомогою команди `/check Місто, Вулиця, Будинок`.", parse_mode="Markdown")
            return
        city, street, house, hash_from_check = row
    except Exception as e:
        logger.error(f"Failed to fetch last_check from DB: {e}")
        await message.answer("❌ **Помилка БД** при спробі знайти ваш останній запит.", parse_mode="Markdown")
        return

    logger.info(f"Command /subscribe for address: {city}, {street}, {house}")
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
        cursor = await db_conn.execute("""
            SELECT s.last_schedule_hash, s.interval_hours
            FROM subscriptions s
            JOIN addresses a ON a.id = s.address_id
            WHERE s.user_id = ? AND a.city = ? AND a.street = ? AND a.house = ?
        """, (user_id, city, street, house))
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

        import pytz
        kiev_tz = pytz.timezone('Europe/Kiev')
        next_check_time = datetime.now(kiev_tz)
        
        # Extract group from last check (after migration 006, group_name is in addresses table)
        cursor_group = await db_conn.execute("""
            SELECT a.group_name 
            FROM user_last_check ulc
            JOIN addresses a ON a.id = ulc.address_id
            WHERE ulc.user_id = ?
        """, (user_id,))
        row_group = await cursor_group.fetchone()
        group = row_group[0] if row_group and row_group[0] else None
        
        await db_conn.execute(
            "INSERT OR REPLACE INTO subscriptions (user_id, city, street, house, interval_hours, next_check, last_schedule_hash, notification_lead_time, group_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, city, street, house, interval_hours, next_check_time, hash_to_use, new_lead_time, group)
        )
        await db_conn.commit()
        
        logger.info(f"Subscribed/updated to {city}, {street}, {house} with interval {interval_hours}h. Alert: {new_lead_time}m")
        created_msg = build_subscription_created_message(city, street, house, interval_display, new_lead_time, current_lead_time)
        await message.answer(created_msg)
        await update_user_activity(db_conn, user_id, username=message.from_user.username, city=city, street=street, house=house, group_name=group)
    except Exception as e:
        logger.error(f"Failed to write subscription to DB: {e}", exc_info=True)
        await message.answer("❌ **Помилка БД** при спробі зберегти підписку.")
