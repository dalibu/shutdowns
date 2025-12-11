# Context-Aware Logging с User ID

## Описание

Реализована система контекстного логирования, которая автоматически добавляет `user_id` (Telegram ID) пользователя во все лог-сообщения в рамках обработки его запроса. Это значительно упрощает отладку и мониторинг, позволяя легко фильтровать логи по конкретному пользователю.

## Как это работает

### 1. Архитектура

Система использует три основных компонента:

- **`contextvars`** - встроенный модуль Python для хранения контекстных переменных (thread-safe и async-safe)
- **`UserContextFilter`** - кастомный logging filter, который извлекает `user_id` из контекста и добавляет в log record
- **`UserContextMiddleware`** - middleware для aiogram, который автоматически устанавливает `user_id` в начале обработки каждого сообщения

### 2. Файлы

- **`common/log_context.py`** - утилиты для работы с контекстом пользователя
- **`common/middleware.py`** - middleware для aiogram
- **`common/logging_config.py`** - обновлен для использования `UserContextFilter`

### 3. Формат логов

**Без user_id (системные логи):**
```
2025-12-11 18:18:14 EET | INFO:dtek.bot.bot:Bot starting up
```

**С user_id (логи в контексте пользователя):**
```
2025-12-11 18:18:14 EET | user_12345 | INFO:dtek.bot.bot:User requested: /start
2025-12-11 18:18:14 EET | user_12345 | INFO:common.handlers:Processing address check
```

## Использование

### Автоматическое использование (рекомендуется)

В ботах уже зарегистрирован `UserContextMiddleware`, поэтому `user_id` автоматически добавляется во все логи при обработке сообщений и callback'ов от пользователей. **Никаких изменений в коде не требуется.**

```python
# В любом handler'е просто используйте logger как обычно
logger.info("Processing user request")  
# Вывод: 2025-12-11 18:18:14 EET | user_12345 | INFO:module:Processing user request
```

### Ручное использование (для фоновых задач)

Если нужно вручную установить контекст пользователя (например, в фоновых задачах):

```python
from common.log_context import set_user_context, clear_user_context

def process_subscription(user_id: int):
    set_user_context(user_id)
    try:
        logger.info("Checking subscription")  # user_id будет в логах
        # ... ваш код ...
    finally:
        clear_user_context()  # Всегда очищайте контекст!
```

## Фильтрация логов

### Найти все логи конкретного пользователя

```bash
# В файле логов
grep 'user_12345' /logs/bot.log

# В Docker логах
docker-compose logs dtek_bot | grep 'user_12345'

# С временным интервалом
docker-compose logs --since 1h dtek_bot | grep 'user_67890'
```

### Найти логи нескольких пользователей

```bash
grep -E 'user_(12345|67890)' /logs/bot.log
```

### Исключить логи конкретного пользователя

```bash
grep -v 'user_12345' /logs/bot.log
```

### Подсчет запросов по пользователям

```bash
# Топ-10 самых активных пользователей
grep -o 'user_[0-9]*' /logs/bot.log | sort | uniq -c | sort -rn | head -10
```

## Примеры использования

### Отладка проблемы конкретного пользователя

```bash
# Найти все логи пользователя с ошибками
docker-compose logs dtek_bot | grep 'user_12345' | grep -i error

# Найти последние 50 логов пользователя
docker-compose logs --tail 1000 dtek_bot | grep 'user_12345' | tail -50
```

### Мониторинг активности

```bash
# Сколько запросов от каждого пользователя сегодня
grep "$(date +%Y-%m-%d)" /logs/bot.log | grep -o 'user_[0-9]*' | sort | uniq -c

# Какие команды использует пользователь
grep 'user_12345' /logs/bot.log | grep 'requested:'
```

### Анализ производительности

```bash
# Время обработки запросов пользователя
grep 'user_12345' /logs/bot.log | grep -E '(User requested|Successfully completed)'
```

## Преимущества

✅ **Автоматическое добавление** - не нужно везде писать `logger.info(f"user {user_id}: ...")`  
✅ **Единообразие** - формат user_id одинаковый во всех логах  
✅ **Простая фильтрация** - легко найти все логи конкретного пользователя  
✅ **Thread-safe и async-safe** - использует contextvars  
✅ **Обратная совместимость** - системные логи без user_id работают как прежде  
✅ **Нулевые изменения** в существующем коде - middleware работает автоматически  

## Технические детали

### contextvars vs threading.local

Мы используем `contextvars` вместо `threading.local` потому что:
- Работает с async/await (важно для aiogram)
- Автоматически копируется в дочерние задачи
- Изолирован для каждого асинхронного контекста

### Очистка контекста

Middleware автоматически очищает контекст в блоке `finally`, гарантируя что user_id не "утечет" в следующий запрос.

### Производительность

Накладные расходы минимальны:
- `contextvars` - очень быстрые операции (O(1))
- `UserContextFilter` - вызывается для каждого лог-сообщения, но делает только простое форматирование строки
- Middleware - добавляет ~0.1ms на обработку каждого сообщения

## Расширение

### Добавление дополнительных полей

Если нужно добавить больше контекстной информации (например, username или chat_id):

```python
# В common/log_context.py
current_username: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'current_username', default=None
)

# В UserContextFilter
def filter(self, record: logging.LogRecord) -> bool:
    user_id = get_user_context()
    username = current_username.get()
    
    if user_id is not None:
        prefix = f"user_{user_id}"
        if username:
            prefix += f"(@{username})"
        record.user_id = f"{prefix} | "
    else:
        record.user_id = ""
    return True
```

## Тестирование

Запустите тестовый скрипт:

```bash
python test_user_context_logging.py
```

Он покажет, как user_id автоматически появляется в логах внутри контекста и исчезает вне его.
