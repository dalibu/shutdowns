"""
Common bot handlers for power shutdown bots.
Contains parametrized handler factories that work with BotContext.
"""

import logging
from datetime import datetime
from typing import Optional

from aiogram import types, F
from aiogram.types import ReplyKeyboardRemove, CallbackQuery
from aiogram.fsm.context import FSMContext

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
