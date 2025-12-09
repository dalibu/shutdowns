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
    send_schedule_response as _send_schedule_response_common,
    # New handlers
    handle_start_command,
    handle_stats_command,
    handle_process_house,
    handle_check_command,
    handle_repeat_command,
    perform_address_check,
    handle_callback_check_address,
    handle_callback_repeat_address,
    handle_subscribe_command,
)
from common.visualization import (
    generate_48h_schedule_image,
    generate_24h_schedule_image,
)
from common.tasks import (
    subscription_checker_task as _subscription_checker_task_common,
    alert_checker_task as _alert_checker_task_common,
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
    from common.formatting import build_address_error_message, build_group_error_message
    try:
        source = get_data_source()
        return await source.get_schedule(city, street, house)
    except Exception as e:
        logger.error(f"Data source error: {e}", exc_info=True)
        error_str = str(e)
        if "Could not determine group for address" in error_str:
            raise ValueError(build_group_error_message(city, street, house))
        raise ValueError(build_address_error_message(EXAMPLE_ADDRESS))

async def send_schedule_response(message: types.Message, api_data: dict, is_subscribed: bool):
    """Wrapper for common handler - sends formatted schedule response."""
    await _send_schedule_response_common(
        message, api_data, is_subscribed, get_ctx(),
        generate_24h_schedule_image, generate_48h_schedule_image
    )

# --- Background Tasks (using common library) ---
def _get_db_conn():
    """Returns current db connection for background tasks."""
    return db_conn

async def subscription_checker_task(bot: Bot):
    """Wrapper for common subscription checker task."""
    await _subscription_checker_task_common(
        bot=bot,
        ctx=get_ctx(),
        db_conn_getter=_get_db_conn,
        get_shutdowns_data=get_shutdowns_data,
        generate_24h_image=generate_24h_schedule_image,
        generate_48h_image=generate_48h_schedule_image
    )

async def alert_checker_task(bot: Bot):
    """Wrapper for common alert checker task."""
    await _alert_checker_task_common(
        bot=bot,
        db_conn_getter=_get_db_conn,
        logger=logger
    )

# --- Configuration ---
EXAMPLE_ADDRESS = "м. Дніпро, вул. Сонячна набережна, 6"
EXAMPLE_CITY = "м. Дніпро"

# --- Helper for perform_address_check wrapper ---
async def _perform_address_check(message: types.Message, user_id: int, city: str, street: str, house: str, group: str = None, is_repeat: bool = False) -> None:
    """Wrapper for common perform_address_check."""
    await perform_address_check(
        message, user_id, city, street, house, get_ctx(),
        get_shutdowns_data, send_schedule_response, group, is_repeat
    )

# --- Command Handlers (using common library) ---
@dp.message(Command("start", "help"))
async def command_start_handler(message: types.Message, state: FSMContext) -> None:
    """Wrapper for common start handler."""
    await handle_start_command(message, state, get_ctx(), _handle_captcha_check, EXAMPLE_ADDRESS)

@dp.message(Command("stats"))
async def command_stats_handler(message: types.Message) -> None:
    """Wrapper for common stats handler."""
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    try:
        admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]
    except ValueError:
        admin_ids = []
    await handle_stats_command(message, get_ctx(), admin_ids)

@dp.message(CaptchaState.waiting_for_answer)
async def captcha_answer_handler(message: types.Message, state: FSMContext) -> None:
    """Wrapper for common handler."""
    await handle_captcha_answer(message, state, get_ctx())

@dp.message(Command("cancel"))
async def command_cancel_handler(message: types.Message, state: FSMContext) -> None:
    """Wrapper for common handler."""
    await handle_cancel(message, state)

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
    """Wrapper for common handler."""
    await handle_process_house(message, state, get_ctx(), get_shutdowns_data, send_schedule_response)

@dp.message(Command("check"))
async def command_check_handler(message: types.Message, state: FSMContext) -> None:
    """Wrapper for common check handler."""
    await handle_check_command(message, state, get_ctx(), _handle_captcha_check, get_shutdowns_data, send_schedule_response, EXAMPLE_CITY)

@dp.message(Command("repeat"))
async def command_repeat_handler(message: types.Message, state: FSMContext) -> None:
    """Wrapper for common repeat handler."""
    await handle_repeat_command(message, state, get_ctx(), _handle_captcha_check, _perform_address_check)

@dp.message(Command("subscribe"))
async def command_subscribe_handler(message: types.Message, state: FSMContext) -> None:
    """Wrapper for common subscribe handler."""
    await handle_subscribe_command(message, state, get_ctx(), _handle_captcha_check)

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
    """Wrapper for common handler."""
    await handle_callback_check_address(callback, state, get_ctx(), _perform_address_check)

@dp.callback_query(F.data.startswith("repeat:"))
async def callback_repeat_address(callback: CallbackQuery) -> None:
    """Wrapper for common handler."""
    await handle_callback_repeat_address(callback, get_ctx(), _perform_address_check)

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

