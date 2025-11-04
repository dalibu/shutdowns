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
# Объявляем глобальные переменные, которые будут заполнены после load_dotenv()
DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN = None 
DTEK_API_URL = None 

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация роутера для регистрации обработчиков
router = Router() 

# --- 2. ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ---
def format_shutdown_message(data: dict) -> str:
    """Форматирует JSON-ответ от API в красивое сообщение для Telegram."""
    
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
        f"👥 Група: `{group}`\n"
        f"---"
    )
    
    if not slots:
        return message + "\n✅ *На цю дату відключення не заплановані.*"

    # Формирование списка слотов
    slot_messages = []
    for slot in slots:
        time = slot.get('time')
        status = slot.get('disconection')
        
        status_icon = "❌" if status == "full" else "⚠️" if status == "half" else "✅"
        status_text = "Світла НЕ БУДЕ" if status == "full" else "Можливе відключення" if status == "half" else "Світло БУДЕ"
        
        slot_messages.append(f"{status_icon} `{time}`: {status_text}")

    message += "\n\n" + "\n".join(slot_messages)
    
    return message


# --- 3. TELEGRAM HANDLERS ---

@router.message(CommandStart())
async def command_start_handler(message: types.Message) -> None:
    """Обрабатывает команду /start."""
    welcome_text = (
        "👋 Привіт! Я бот для перевірки графіків відключень ДТЕК.\n\n"
        "Будь ласка, надішліть мені команду у форматі:\n"
        "`/check [Місто] [Вулиця] [Номер дому]`\n\n"
        "**Приклад:**\n"
        "`/check м. Дніпро вул. Сонячна набережна 6`"
    )
    await message.answer(welcome_text, parse_mode=ParseMode.MARKDOWN)


@router.message(Command(commands=["check"]))
async def check_shutdowns_handler(message: types.Message) -> None:
    """
    Обрабатывает команду /check, отправляет запрос к API и возвращает результат.
    """
    # Используем глобальные переменные DTEK_API_URL
    global DTEK_API_URL 
    
    # Разделяем команду и остальной текст
    text_parts = message.text.split(maxsplit=1)
    text_without_command = text_parts[1].strip() if len(text_parts) > 1 else ""
    
    # Пытаемся разделить аргументы на 3 части: Город, Улица, Дом
    args = text_without_command.split(maxsplit=2)
    
    if len(args) < 3:
        error_text = (
            "⚠️ **Некоректний формат команди!**\n"
            "Використовуйте: `/check [Місто] [Вулиця] [Номер дому]`\n"
            "Наприклад: `/check м. Дніпро вул. Сонячна набережна 6`"
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
    
    # 📌 КРИТИЧЕСКАЯ ПРОВЕРКА ТОКЕНА
    if not DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN:
        # Этого не должно случиться, если код в __name__ == "__main__" отработал корректно
        logger.error("!!! ВНУТРЕННЯЯ ОШИБКА: Токен не был загружен. Прерывание. !!!")
        return 

    # Инициализация объектов Bot с DefaultBotProperties (исправление ошибки aiogram 3.7+)
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
    # 📌 ИСПРАВЛЕНИЕ: Сначала загружаем .env, затем читаем переменные
    load_dotenv()
    
    # Читаем переменные после загрузки .env
    DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN = os.getenv("DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN")
    DTEK_API_URL = os.getenv("DTEK_API_URL", "http://localhost:8000/shutdowns") 

    # Выводим ошибку, если токен все еще пустой, и не запускаем main
    if not DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN:
        logger.error("!!! КРИТИЧЕСКАЯ ОШИБКА: DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN не установлен в переменных окружения. !!!")
        logger.error("Для локального запуска создайте файл .env и добавьте DTEK_SHUTDOWNS_TELEGRAM_BOT_TOKEN=ВАШ_ТОКЕН")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")