import logging
import requests
import os
import re
import asyncio

# Импорт для работы с переменными окружения (для локальной разработки)
from dotenv import load_dotenv

# Импорты aiogram 3.x
from aiogram import Bot, Dispatcher, types, Router 
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command 
from aiogram.client.default import DefaultBotProperties # Для настройки parse_mode

# --- 1. ПЕРЕМЕННЫЕ (Будут заполнены позже в __main__) ---
DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN = None 
DTEK_API_URL = None 

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация роутера для регистрации обработчиков
router = Router() 

# --- 2. ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ---
def format_shutdown_message(data: dict) -> str:
    """
    Форматирует JSON-ответ от API в красивое сообщение для Telegram.
    Реализована логика консолидации блоков и интерпретации 'half' как 30-минутного отключения.
    """
    
    # Извлечение данных
    city = data.get("city", "Н/Д")
    street = data.get("street", "Н/Д")
    house = data.get("house_num", "Н/Д")
    group = data.get("group", "Н/Д")
    date = data.get("date", "Н/Д")
    slots = data.get("slots", [])

    # Формирование заголовка
    message = (
        f"💡 **Графік відключень ДТЕК**\n"
        f"🏠 Адреса: `{city}, {street}, {house}`\n"
        f"📅 Дата: **{date}**\n"
        f"👥 Черга: `{group}`\n"
        f"---"
    )
    
    outage_slots = [s for s in slots if s.get('disconection') in ('full', 'half')]
    
    if not outage_slots:
        if slots:
            return message + "\n✅ *На цю дату відключення не заплановані.*"
        else:
            return message + "\n❌ *Не вдалося отримати графік відключень (пусті слоти).* "

    first_slot = outage_slots[0]
    last_slot = outage_slots[-1]

    # --- Вспомогательная функция для форматирования минут в HH:MM ---
    def format_minutes_to_hh_m(minutes: int) -> str:
        h = minutes // 60
        m = minutes % 60
        # 📌 ИСПРАВЛЕНИЕ: Всегда возвращаем формат HH:MM
        return f"{h}:{m:02d}"

    # --- Расчет времени начала отключения ---
    try:
        time_parts = re.split(r'\s*[-\–]\s*', first_slot.get('time', '0-0'))
        start_hour = int(time_parts[0])
        
        if first_slot.get('disconection') == 'full':
            outage_start_min = start_hour * 60 
        else: # 'half' outage
            outage_start_min = start_hour * 60 + 30

    except Exception as e:
        logger.error(f"Error parsing start time from slot: {first_slot}. Error: {e}")
        return message + "\n❌ *Помилка парсингу часу початку. Перевірте формат даних.*"

    # --- Расчет времени конца отключения ---
    try:
        time_parts = re.split(r'\s*[-\–]\s*', last_slot.get('time', '0-0'))
        end_hour = int(time_parts[1])
        
        if last_slot.get('disconection') == 'full':
            outage_end_min = end_hour * 60
        else: # 'half' outage
            outage_end_min = end_hour * 60 - 30

    except Exception as e:
        logger.error(f"Error parsing end time from slot: {last_slot}. Error: {e}")
        return message + "\n❌ *Помилка парсингу часу кінця. Перевірте формат даних.*"
        
    # 2. Финальное форматирование
    
    if outage_start_min >= outage_end_min:
         return message + "\n✅ *На цю дату відключення не заплановані (або помилка часу).* "

    start_time_final = format_minutes_to_hh_m(outage_start_min)
    end_time_final = format_minutes_to_hh_m(outage_end_min)
    
    final_message = f"❌ **Світла НЕ БУДЕ: {start_time_final} - {end_time_final}**"

    return message + "\n" + final_message
    
# --- 3. TELEGRAM HANDLERS (Остаются без изменений) ---

@router.message(CommandStart())
async def command_start_handler(message: types.Message) -> None:
    """Обрабатывает команду /start."""
    welcome_text = (
        "👋 Привіт! Я бот для перевірки графіків відключень ДТЕК.\n\n"
        "Будь ласка, надішліть мені команду у форматі:\n"
        "`/check [Місто], [Вулиця], [Номер дому]`\n\n"
        "**Приклад:**\n"
        "`/check м. Дніпро, вул. Сонячна набережна, 6`"
    )
    await message.answer(welcome_text, parse_mode=ParseMode.MARKDOWN)


@router.message(Command(commands=["check"]))
async def check_shutdowns_handler(message: types.Message) -> None:
    """
    Обрабатывает команду /check, используя запятые в качестве разделителя.
    """
    global DTEK_API_URL 
    
    # 1. Удаляем команду /check и лишние пробелы
    text_parts = message.text.split(maxsplit=1)
    text_args = text_parts[1].strip() if len(text_parts) > 1 else ""
    
    # 2. Делим аргументы по запятой (,) и убираем пробелы вокруг
    args = [part.strip() for part in text_args.split(',')]
    
    # Проверяем, что получили ровно 3 непустых аргумента
    if len(args) != 3 or any(not arg for arg in args):
        error_text = (
            "⚠️ **Некоректний формат команди!**\n"
            "Використовуйте: `/check [Місто], [Вулиця], [Номер дому]`\n"
            "Наприклад: `/check м. Дніпро, вул. Сонячна набережна, 6`\n\n"
            "*Перевірте, що ви ввели рівно три елементи, розділені комами.*"
        )
        await message.answer(error_text, parse_mode=ParseMode.MARKDOWN)
        return

    city, street, house = args
    
    await message.answer("⏳ Перевіряю графік. Це може зайняти до 30 секунд...")

    try:
        # --- API Request ---
        params = {
            "city": city,
            "street": street,
            "house": house
        }
        
        logger.info(f"Sending API request to {DTEK_API_URL} for: {city}, {street}, {house}")
        
        # Запрос к вашему API-сервису
        response = requests.get(DTEK_API_URL, params=params, timeout=45) 
        
        response.raise_for_status() 
        
        data = response.json()
        
        # Форматирование и отправка результата
        formatted_message = format_shutdown_message(data)
        await message.answer(formatted_message, parse_mode=ParseMode.MARKDOWN)

    except requests.exceptions.HTTPError as http_err:
        if response.status_code == 404:
             error_detail = response.json().get('detail', 'Адреса не знайдена або таймаут.')
             await message.answer(f"❌ **Помилка 404:** {error_detail}")
        else:
             logger.error(f"HTTP Error: {http_err}. Full response: {response.text}")
             await message.answer(f"❌ **Помилка API (HTTP {response.status_code}):** Спробуйте пізніше.")

    except requests.exceptions.ConnectionError:
        await message.answer("❌ **Помилка підключення:** Сервіс парсингу недоступний. Перевірте Docker-контейнер.")

    except requests.exceptions.Timeout:
        await message.answer("❌ **Таймаут:** Парсер не встиг відповісти за 45 секунд. Спробуйте ще раз.")

    except Exception as e:
        logger.error(f"Unknown error in bot: {e}")
        await message.answer(f"❌ Виникла невідома помилка: {e}")

# --- 4. ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА ---
async def main() -> None:
    
    # Инициализация объектов Bot с DefaultBotProperties
    bot = Bot(
        token=DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # Регистрация роутера в диспетчере
    dp.include_router(router)

    # Удаление старых обновлений и запуск Long Polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    # Сначала загружаем .env, затем читаем переменные
    load_dotenv()
    
    # Читаем переменные после загрузки .env
    DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN = os.getenv("DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN")
    DTEK_API_URL = os.getenv("DTEK_API_URL", "http://dtek_api:8000/shutdowns") 

    # Выводим ошибку, если токен не найден
    if not DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN:
        logger.error("!!! КРИТИЧЕСКАЯ ОШИБКА: DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN не установлен в переменных окружения. !!!")
        logger.error("Для локального запуска создайте файл .env и добавьте DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN=ВАШ_ТОКЕН")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")