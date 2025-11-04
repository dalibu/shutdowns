import os
import re
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command 
from aiogram.types import BotCommand, ReplyKeyboardRemove
from aiogram.client.default import DefaultBotProperties

# --- 1. Конфигурація ---
# Токен бота берется из переменных окружения
BOT_TOKEN = os.getenv("DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN")
# URL вашего FastAPI парсера (должен быть доступен изнутри Docker)
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


# --- 2. Вспомогательные функции (Бизнес-логика) ---

def format_minutes_to_hh_m(minutes: int) -> str:
    """Форматирует общее количество минут в HH:MM."""
    h = minutes // 60
    m = minutes % 60
    return f"{h}:{m:02d}"

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
            async with session.get(f"{API_BASE_URL}/shutdowns", params=params, timeout=45) as response: 
                if response.status == 404:
                    # Корректно бросаем ValueError для обработки "Адрес не найден"
                    raise ValueError("Графік для цієї адреси не знайдено.")
                
                response.raise_for_status()
                return await response.json()

        except aiohttp.ClientError as e:
            # Ошибки сети, таймауты, DNS и т.д.
            logger.error(f"API Connection Error: {e}")
            raise ConnectionError("Помилка підключення до парсера. Спробуйте пізніше.")
        # Все остальные ошибки (например, JSONDecodeError) будут отловлены в общем except в command_check_handler

def _process_single_day_schedule(date: str, slots: List[Dict[str, Any]]) -> str:
    """
    Консолидирует слоты отключений для одной даты и возвращает строку с временем.
    """
    outage_slots = [s for s in slots if s.get('disconection') in ('full', 'half')]
    
    if not outage_slots:
        return f"✅ **{date}**: *Відключення не заплановані.*"

    first_slot = outage_slots[0]
    last_slot = outage_slots[-1]

    # --- Расчет времени начала отключения ---
    try:
        time_parts = re.split(r'\s*[-\–]\s*', first_slot.get('time', '0-0'))
        start_hour = int(time_parts[0])
        
        if first_slot.get('disconection') == 'full':
            outage_start_min = start_hour * 60 
        else:
            # 'half' начало: +30 минут
            outage_start_min = start_hour * 60 + 30
    except Exception as e:
        logger.error(f"Error parsing start time for {date}: {first_slot}. Error: {e}")
        return f"❌ **{date}**: *Помилка парсингу часу початку.*"

    # --- Расчет времени конца отключения ---
    try:
        time_parts = re.split(r'\s*[-\–]\s*', last_slot.get('time', '0-0'))
        end_hour = int(time_parts[1])
        
        if last_slot.get('disconection') == 'full':
            outage_end_min = end_hour * 60
        else: 
            # 'half' конец: -30 минут
            outage_end_min = end_hour * 60 - 30

    except Exception as e:
        logger.error(f"Error parsing end time for {date}: {last_slot}. Error: {e}")
        return f"❌ **{date}**: *Помилка парсингу часу кінця.*"
        
    # Финальное форматирование
    if outage_start_min >= outage_end_min:
         return f"✅ **{date}**: *Відключення не заплановані (або помилка часу).* "

    start_time_final = format_minutes_to_hh_m(outage_start_min)
    end_time_final = format_minutes_to_hh_m(outage_end_min)
    
    return f"📅 **{date}**: `{start_time_final} - {end_time_final}`"


def format_shutdown_message(data: dict) -> str:
    """
    Форматирует агрегированный JSON-ответ, показывая график для ВСЕХ доступных дней.
    """
    
    city = data.get("city", "Н/Д")
    street = data.get("street", "Н/Д")
    house = data.get("house_num", "Н/Д")
    group = data.get("group", "Н/Д")
    schedule = data.get("schedule", {})
    
    message = (
        f"💡 **Графік відключень ДТЕК**\n"
        f"🏠 Адреса: `{city}, {street}, {house}`\n"
        f"👥 Черга: `{group}`\n"
        f"---"
    )
    
    if not schedule:
        return message + "\n❌ *Не вдалося отримати графік відключень.*"

    # Сортируем даты по дате, если возможно, или по ключу
    try:
        sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
    except ValueError:
        sorted_dates = sorted(schedule.keys())
    
    schedule_lines = []
    has_outage = False 
    
    for date in sorted_dates:
        slots = schedule[date]
        line = _process_single_day_schedule(date, slots)
        schedule_lines.append(line)
        
        if "Відключення не заплановані" not in line and "Помилка" not in line:
            has_outage = True

    final_schedule_output = "\n".join(schedule_lines)

    if has_outage:
        return message + "\n❌ **Світла НЕ БУДЕ:**\n" + final_schedule_output
    else:
        return message + "\n✅ **На найближчі дні відключення не заплановані.**"


def parse_address_from_text(text: str) -> tuple[str, str, str]:
    """Извлекает город, улицу и дом из строки, разделенной запятыми."""
    text = text.replace('/check', '', 1).strip()
    
    # Разбиваем по запятой и чистим части
    parts = [p.strip() for p in text.split(',') if p.strip()]
    
    if len(parts) < 3:
        raise ValueError("Адреса має бути введена у форматі: **Місто, Вулиця, Будинок**.")
    
    # Берем первые три части
    city = parts[0]
    street = parts[1]
    house = parts[2]
        
    return city, street, house


# --- 3. Обработчики команд (aiogram v3) ---

# Обработчик /start и /help 
async def command_start_handler(message: types.Message) -> None:
    """Обработчик команды /start и /help."""
    text = (
        "👋 **Вітаю! Я бот для перевірки графіків відключень ДТЕК.**\n\n"
        "Для перевірки графіку, введіть команду **/check**, додавши адресу у форматі:\n"
        "`/check Місто, Вулиця, Будинок`\n\n"
        "**Наприклад:**\n"
        "`/check м. Дніпро, вул. Сонячна набережна, 6`\n\n"
        "**Команди:**\n"
        "/check - перевірити графік за адресою.\n"
        "/cancel - скасувати поточну дію."
    )
    await message.answer(text, reply_markup=ReplyKeyboardRemove())

# Обработчик /cancel (возвращает информационное сообщение)
async def command_cancel_handler(message: types.Message) -> None:
    """Обработчик команды /cancel."""
    await message.answer("Поточний ввід адреси неактивний. Введіть /check [адреса], щоб почати перевірку.")


# Обработчик /check (сразу принимает и обрабатывает адрес)
async def command_check_handler(message: types.Message) -> None:
    """Обработка однострочного ввода адреса."""
    text_args = message.text.replace('/check', '', 1).strip()
    
    if not text_args:
        await message.answer("Будь ласка, введіть повну адресу в одному повідомленні, розділену комами (наприклад, `/check м. Дніпро, вул. Сонячна набережна, 6`).")
        return

    try:
        # 1. Парсинг адреса
        city, street, house = parse_address_from_text(text_args)
        
        await message.answer("⏳ Перевіряю графік. Це може зайняти декілька секунд...", reply_markup=ReplyKeyboardRemove())

        # 2. Логика API
        data = await get_shutdowns_data(city, street, house)
        
        # 3. Форматирование
        response_text = format_shutdown_message(data)
        await message.answer(response_text) # parse_mode "Markdown" установлен по умолчанию

    except ValueError as e:
        # Ошибка 404 / Неправильный формат адреса
        await message.answer(f"❌ **Помилка вводу/помилка API:** {e}")
    except ConnectionError as e:
        # Ошибка соединения
        await message.answer(f"❌ **Помилка:** {e}")
    except Exception as e:
        # Критические и непредвиденные ошибки
        logger.error(f"Critical error during parsing for user {message.from_user.id}: {e}")
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")


# --- 4. Main Execution ---

async def main() -> None:
    """Главная функция для запуска бота."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не встановлено. Перевірте змінні оточення.")
        return
    
    # Инициализация с настройкой Markdown по умолчанию для aiogram v3
    default_props = DefaultBotProperties(parse_mode="Markdown")
    bot = Bot(BOT_TOKEN, default=default_props) 
    
    dp = Dispatcher()

    # Установка команд (для меню в Telegram)
    commands = [
        BotCommand(command="check", description="Перевірити графік за адресою"),
        BotCommand(command="cancel", description="Скасувати поточну дію"),
        BotCommand(command="help", description="Довідка")
    ]
    await bot.set_my_commands(commands)
    
    # Регистрация обработчиков
    dp.message.register(command_start_handler, Command("start", "help"))
    dp.message.register(command_cancel_handler, Command("cancel"))
    dp.message.register(command_check_handler, Command("check")) 

    logger.info("Бот запущено. Початок опитування...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот зупинено вручну.")
    except Exception as e:
        logger.critical(f"Критична помилка виконання: {e}")