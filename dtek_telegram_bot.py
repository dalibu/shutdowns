import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler
import asyncio
# Импортируем функцию run из вашего dtek_parser.py
from dtek_parser import run 

# --- Конфигурация Telegram ---
TOKEN = '8588962191:AAEe1sWtQHDRdkYGy7xz94uJ6X_hBL0kk-0'

# Включаем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Состояния разговора ---
CITY, STREET, HOUSE = range(3)

# --- Вспомогательная функция для запуска парсера ---

async def execute_parser(context, city, street, house):
    """Запускает парсер и обрабатывает результат."""
    chat_id = context.job.data['chat_id']
    
    await context.bot.send_message(
        chat_id=chat_id,
        text="⌛ Запускаю парсинг ДТЕК. Это может занять до минуты..."
    )

    try:
        # Вызов асинхронной функции run из dtek_parser
        png_path, json_data = await run(city=city, street=street, house=house)

        # 1. Отправка скриншота
        with open(png_path, 'rb') as photo_file:
            await context.bot.send_photo(
                chat_id=chat_id, 
                photo=photo_file,
                caption=f"✅ **График отключений**\n\n**Группа:** {json_data[0].get('group', 'N/A')}\n**Дата:** {json_data[0].get('date', 'N/A')}",
                parse_mode='Markdown'
            )
        
        # 2. Отправка JSON-файла
        with open(png_path.with_suffix('.json'), 'rb') as json_file:
            await context.bot.send_document(
                chat_id=chat_id, 
                document=json_file,
                filename="data.json"
            )

    except Exception as e:
        logger.error(f"Ошибка парсинга для {city}, {street}, {house}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ **Произошла ошибка при получении данных.**\n\nПроверьте введенный адрес или попробуйте позже.\nОшибка: {e}"
        )
    finally:
        # Очистка локальных файлов
        if os.path.exists(png_path):
            os.remove(png_path)
        if os.path.exists(png_path.with_suffix('.json')):
            os.remove(png_path.with_suffix('.json'))


# --- Обработчики команд ---

async def start(update: Update, context) -> int:
    """Начинает разговор и запрашивает город."""
    await update.message.reply_text(
        "👋 Привет! Я бот для проверки графика отключений ДТЕК.\n\n"
        "**Пожалуйста, введите название города** (например, 'м. Дніпро'):"
    )
    context.user_data['address'] = {}
    return CITY

async def get_city(update: Update, context) -> int:
    """Сохраняет город и запрашивает улицу."""
    context.user_data['address']['city'] = update.message.text
    await update.message.reply_text(
        "👍 Город принят. **Теперь введите название улицы** (например, 'вул. Сонячна набережна'):"
    )
    return STREET

async def get_street(update: Update, context) -> int:
    """Сохраняет улицу и запрашивает номер дома."""
    context.user_data['address']['street'] = update.message.text
    await update.message.reply_text(
        "🏡 Улица принята. **Введите номер дома** (например, '6'):"
    )
    return HOUSE

async def get_house(update: Update, context) -> int:
    """Сохраняет номер дома, запускает парсер и завершает разговор."""
    context.user_data['address']['house'] = update.message.text
    
    address = context.user_data['address']
    
    await update.message.reply_text(
        f"🔍 Адрес: **{address['city']}**, **{address['street']}**, **{address['house']}**.\n"
        "Сейчас я проверю данные. Это может занять некоторое время."
    )
    
    # Добавляем задачу парсинга в очередь для асинхронного выполнения
    # context.job_queue.run_once(execute_parser, 1, data={'chat_id': update.effective_chat.id}, name='parser')
    
    # В простом варианте, запускаем напрямую, чтобы избежать задержки с job_queue
    await execute_parser(
        context.job, 
        address['city'], 
        address['street'], 
        address['house']
    )

    context.user_data.clear() # Очистка данных пользователя
    return ConversationHandler.END

async def cancel(update: Update, context) -> int:
    """Отменяет разговор."""
    await update.message.reply_text('🚫 Запрос отменен. Начните снова командой /start.')
    context.user_data.clear()
    return ConversationHandler.END


def main():
    """Запускает бота."""
    application = Application.builder().token(TOKEN).build()

    # Устанавливаем заглушку для job_queue.job
    class DummyJob:
        def __init__(self, chat_id):
            self.data = {'chat_id': chat_id}
    
    async def dummy_executor(update: Update, context):
        context.job = DummyJob(update.effective_chat.id)
        return await get_house(update, context)

    # Определяем обработчик разговора
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_city)],
            STREET: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_street)],
            HOUSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, dummy_executor)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(conv_handler)

    print("Бот запущен. Нажмите Ctrl+C для остановки.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()