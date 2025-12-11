# Обновление Context-Aware Logging: Удаление избыточности

## Что изменилось

После добавления автоматического `user_XXXXX |` в префикс лога через context-aware logging, мы удалили все избыточные упоминания `user_id` и `user_info` из самих текстов лог-сообщений.

## Пример: До и После

### ДО (избыточность):
```
2025-12-11 18:18:14 EET | user_12345 | INFO:common.handlers:Command /start by user 12345 (@dalibude) A K
```
**Проблема:** user_id упоминается дважды - в префиксе и в сообщении

### ПОСЛЕ (чисто):
```
2025-12-11 18:18:14 EET | user_12345 | INFO:common.handlers:Command /start
```
**Решение:** user_id только в префиксе, сообщение короткое и читабельное

## Изменённые файлы

### `common/handlers.py`
Удалены упоминания `user_id`/`user_info` из всех лог-сообщений:

| Было | Стало |
|------|-------|
| `f"CAPTCHA passed by user {user_info}"` | `"CAPTCHA passed"` |
| `f"Command /start by user {user_info}"` | `"Command /start"` |
| `f"Command /check by user {user_info} for address: {city}, {street}, {house}"` | `f"Command /check for address: {city}, {street}, {house}"` |
| `f"User {user_id} subscribed/updated to {city}..."` | `f"Subscribed/updated to {city}..."` |
| `f"Error in /repeat for user {user_id}: {e}"` | `f"Error in /repeat: {e}"` |

### `common/tasks.py`
Аналогичные изменения для фоновых задач:

| Было | Стало |
|------|-------|
| `f"Processing alerts for {user_info}, lead_time={lead_time} min"` | `f"Processing alerts, lead_time={lead_time} min"` |
| `f"Notification sent to user {user_info}..."` | `"Notification sent..."` |
| `f"User {user_info} check for {address_str}..."` | `f"Check for {address_str}..."` |

## Преимущества

✅ **Чище читается** - нет дублирования информации  
✅ **Короче логи** - меньше места на диске  
✅ **Проще парсить** - структура более предсказуемая  
✅ **Легче фильтровать** - `grep 'user_12345'` всегда находит все логи этого пользователя  

## Реальный пример логов

### Последовательность действий пользователя:

```
2025-12-11 18:20:21 EET | user_12345 | INFO:dtek.bot.bot:Command /start
2025-12-11 18:20:21 EET | user_12345 | INFO:common.handlers:Command /check for address: м. Дніпро, вул. Сонячна набережна, 6
2025-12-11 18:20:21 EET | user_12345 | DEBUG:common.handlers:Check: address [ID:42] belongs to group 5.1
2025-12-11 18:20:21 EET | user_12345 | INFO:common.handlers:Check: using group cache for 5.1
2025-12-11 18:20:21 EET | user_12345 | INFO:common.handlers:Subscribed/updated to м. Дніпро, вул. Сонячна набережна, 6 with interval 1h. Alert: 15m
```

### Фоновая задача (alert):

```
2025-12-11 18:25:00 EET | user_67890 | DEBUG:common.tasks:Processing alerts, lead_time=15 min
2025-12-11 18:25:00 EET | user_67890 | DEBUG:common.tasks:Alert check: found 4 events total
2025-12-11 18:25:00 EET | user_67890 | DEBUG:common.tasks:Alert check: next event is відключення at 18:40 (in 14.5 min), lead_time=15 min
2025-12-11 18:25:00 EET | user_67890 | INFO:common.tasks:Sending alert: відключення at 18:40 in 14 min for `м. Дніпро, вул. Сонячна набережна, 6`
2025-12-11 18:25:00 EET | user_67890 | INFO:common.tasks:Alert sent successfully, event_dt=2025-12-11T18:40:00+02:00
```

### Системные логи (без user_id):

```
2025-12-11 18:00:00 EET | INFO:dtek.bot.bot:DTEK Bot started. Beginning polling...
2025-12-11 18:00:01 EET | INFO:common.tasks:Subscription checker started.
2025-12-11 18:00:01 EET | INFO:common.tasks:Alert checker started.
2025-12-11 18:00:05 EET | INFO:common.tasks:Checking 125 unique addresses now for 340 users.
```

## Замечания

Код теперь более читабелен и не содержит избыточной информации, при этом вся необходимая информация для debugging сохранена благодаря контекстному логированию.
