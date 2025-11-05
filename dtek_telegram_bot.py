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


# --- 2. Вспомогательные функции (Бизнес-логика) ---
# Эти функции являются чистой логикой и будут импортированы в тесты.

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
        time_parts_start = re.split(r'\s*[-\–]\s*', first_slot.get('time', '0-0'))
        start_hour = int(time_parts_start[0])
        
        if first_slot.get('disconection') == 'full':
            outage_start_min = start_hour * 60 
        else:
            outage_start_min = start_hour * 60 + 30
    except Exception:
        return "Помилка парсингу часу початку"

    # --- Расчет времени конца отключения ---
    try:
        time_parts_end = re.split(r'\s*[-\–]\s*', last_slot.get('time', '0-0'))
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
    
    return f"{start_time_final} - {end_time_final}"


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
            line = f"❌ **{date}**: `{result_str}` (💡 світла не буде)"

        schedule_lines.append(line)

    final_schedule_output = "\n".join(schedule_lines)
    
    return message + "\n" + final_schedule_output


def parse_address_from_text(text: str) -> tuple[str, str, str]:
    """Извлекает город, улицу и дом из строки, разделенной запятыми."""
    text = text.replace('/check', '', 1).strip()
    
    parts = [p.strip() for p in text.split(',') if p.strip()]
    
    if len(parts) < 3:
        raise ValueError("Адреса має бути введена у форматі: **Місто, Вулиця, Будинок**.")
    
    city = parts[0]
    street = parts[1]
    house = parts[2]
        
    return city, street, house

# --- 3. Интеграция с API (Асинхронные функции) ---

async def get_shutdowns_data(city: str, street: str, house: str) -> dict:
    """
    Вызывает API-парсер и возвращает полный агрегированный JSON-ответ.
    Bot/Client отправляет необработанные данные, следуя SoC.
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

# --- 4. Обработчики команд (aiogram v3) ---

dp = Dispatcher()

async def command_start_handler(message: types.Message) -> None:
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

async def command_cancel_handler(message: types.Message) -> None:
    await message.answer("Поточний ввід адреси неактивний. Введіть /check [адреса], щоб почати перевірку.")


async def command_check_handler(message: types.Message) -> None:
    text_args = message.text.replace('/check', '', 1).strip()
    
    if not text_args:
        await message.answer("Будь ласка, введіть повну адресу в одному повідомленні, розділену комами (наприклад, `/check м. Дніпро, вул. Сонячна набережна, 6`).")
        return

    try:
        city, street, house = parse_address_from_text(text_args)
        
        await message.answer("⏳ Перевіряю графік. Це може зайняти декілька секунд...")

        # Вызов API
        data = await get_shutdowns_data(city, street, house)
        
        # Форматирование
        response_text = format_shutdown_message(data)
        await message.answer(response_text) 

    except ValueError as e:
        await message.answer(f"❌ **Помилка вводу/помилка API:** {e}")
    except ConnectionError as e:
        await message.answer(f"❌ **Помилка:** {e}")
    except Exception as e:
        logger.error(f"Critical error during parsing for user {message.from_user.id}: {e}")
        await message.answer(f"❌ Виникла непередбачена помилка. Спробуйте пізніше.")


# --- 5. Main Execution ---

async def main() -> None:
    """Главная функция для запуска бота."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не встановлено. Перевірте змінні оточення.")
        return
    
    default_props = DefaultBotProperties(parse_mode="Markdown")
    bot = Bot(BOT_TOKEN, default=default_props) 
    
    commands = [
        BotCommand(command="check", description="Перевірити графік за адресою"),
        BotCommand(command="cancel", description="Скасувати поточну дію"),
        BotCommand(command="help", description="Довідка")
    ]
    await bot.set_my_commands(commands)
    
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