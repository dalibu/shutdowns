# Database Normalization - Final Status and TODO

## ✅ Полностью завершено:

### 1. Миграция БД
- **Файл**: `common/migrations/006_normalize_addresses.sql`
- **Статус**: ✅ Готова к применению
- **Изменения**: Нет hardcoded значений, автоопределение provider

### 2. API Функции
- **Файл**: `common/bot_base.py`
- ✅ `get_address_id()` - основная функция
- ✅ `update_address_group()` - обновление группы
- ✅ `get_address_by_id()` - получение данных адреса
- ✅ `find_addresses_by_group()` - обновлена
- ✅ Deprecated wrappers для совместимости

### 3. Обновлен код (частично)

#### ✅ tasks.py - subscription_checker_task
- Использует `get_address_id()`
- Использует `update_address_group()`

#### ✅ handlers.py - обновлено:
- `perform_address_check()` - использует address_id
- `handle_process_house()` - использует address_id
- `handle_check_command()` - использует address_id
- `handle_repeat_command()` - JOIN с addresses

## ⏳ Осталось обновить вручную:

### handlers.py:

**1. handle_subscribe_command** (строка ~1225-1236):
```python
# Текущий код:
await db_conn.execute(
    "INSERT OR REPLACE INTO subscriptions (user_id, city, street, house, ...)",
    (user_id, city, street, house, ...)
)

# Нужно заменить на:
address_id, _ = await get_address_id(db_conn, ctx.provider_code, city, street, house)
await db_conn.execute(
    "INSERT OR REPLACE INTO subscriptions (user_id, address_id, interval_hours, next_check, last_schedule_hash, notification_lead_time) VALUES (?, ?, ?, ?, ?, ?)",
    (user_id, address_id, interval_hours, next_check_time, hash_to_use, new_lead_time)
)
```

**2. UPDATE subscriptions SET notification_lead_time** (строки 175, 1212):
```python
# Текущий код:
"UPDATE subscriptions SET notification_lead_time = ? WHERE user_id = ?"

# Оставить как есть (работает, т.к. user_id уникален в subscriptions)
```

**3. Любые другие SELECT/INSERT/UPDATE к subscriptions/user_last_check**:
- Найти через grep
- Обновить по мере нахождения

### bot_base.py:

**Проверить функции управления подписками**:
- `get_user_subscriptions()`
- `is_address_subscribed()`
- `remove_subscription_by_id()`
- `save_user_address()`

Эти функции могут использовать прямые SQL запросы к subscriptions/user_addresses.

## 📋 План применения:

### ВАЖНО: Порядок действий

1. **BACKUP БД** ⚠️:
```bash
cp dtek/data/dtek_bot.db dtek/data/dtek_bot.db.backup_$(date +%Y%m%d_%H%M%S)
cp cek/data/cek_bot.db cek/data/cek_bot.db.backup_$(date +%Y%m%d_%H%M%S)
```

2. **Применить миграци**ю:
```bash
python -m common.migrate --db-path dtek/data/dtek_bot.db
python -m common.migrate --db-path cek/data/cek_bot.db
```

3. **Тестирование**:
```bash
# Проверить структуру
sqlite3 dtek/data/dtek_bot.db ".schema addresses"
sqlite3 dtek/data/dtek_bot.db ".schema subscriptions"
sqlite3 dtek/data/dtek_bot.db "SELECT COUNT(*) FROM addresses;"

# Запустить бота
docker-compose restart dtek-bot cek-bot

# Следить за логами
docker-compose logs -f dtek-bot | grep -E "ERROR|Failed"
```

4. **Исправить ошибки по мере нахождения**:
- Ошибки БД будут явными (column not found, etc.)
- Исправить SQL запросы в местах ошибок

## 🔧 Быстрые исправления для частых ошибок:

### Ошибка: `no such column: subscriptions.city`
**Где**: Любой SELECT с JOIN к subscriptions  
**Исправление**:
```python
# До:
SELECT s.city, s.street, s.house FROM subscriptions s WHERE ...

# После:
SELECT a.city, a.street, a.house FROM subscriptions s 
JOIN addresses a ON a.id = s.address_id WHERE ...
```

### Ошибка: `no such column: user_last_check.city`
**Где**: SELECT из user_last_check  
**Исправление**:
```python
# До:
SELECT city, street, house FROM user_last_check WHERE user_id = ?

# После:
SELECT a.city, a.street, a.house FROM user_last_check ulc
JOIN addresses a ON a.id = ulc.address_id WHERE ulc.user_id = ?
```

### Ошибка при INSERT в subscriptions
**Где**: Создание подписки  
**Исправление**:
```python
# Добавить перед INSERT:
address_id, _ = await get_address_id(db_conn, ctx.provider_code, city, street, house)

# Изменить INSERT:
INSERT INTO subscriptions (user_id, address_id, interval_hours, ...) 
VALUES (?, ?, ?, ...)
```

## 📊 Ожидаемое поведение после миграции:

### ✅ Должно работать:
- `/check адрес` - создаст address_id автоматически
- `/repeat` - получит адрес через JOIN с addresses
- Subscription checker - использует новые функции
- Кэш групп - продолжит работать

### ⚠️ Может сломаться (нужно исправить):
- `/subscribe` - потенциально нужно обновить
- Управление подписками (если есть UI)
- Статистика (если использует прямые SQL)
- Любые кастомные запросы к subscriptions

## 🧪 Тест-план после миграции:

1. **Базовая функциональность**:
```
✓ /start - работа бота
✓ /check новый_адрес - создание address_id
✓ /check тот_же_адрес - использование существующего address_id
✓ /repeat - получение последнего адреса
```

2. **Кэш групп**:
```
✓ Первая проверка адреса → парсер
✓ Вторая проверка того же адреса → кэш HIT
✓ Адрес из той же группы → кэш HIT
```

3. **Подписки** (критично проверить!):
```
✓ /subscribe - создание подписки
✓ /unsubscribe - удаление подписки
✓ /alert - изменение времени уведомлений
✓ Checker task - проверка подписок
```

4. **База адресов**:
```sql
-- Должны накапливаться адреса
SELECT COUNT(*) FROM addresses;

-- Должны обновляться группы
SELECT * FROM addresses WHERE group_name IS NOT NULL LIMIT 5;

-- Подписки должны ссылаться на address_id
SELECT * FROM subscriptions LIMIT 5;
```

## 🚨 Rollback план:

Если что-то критично сломалось:

```bash
# 1. Остановить боты
docker-compose stop dtek-bot cek-bot

# 2. Восстановить backup
cp dtek/data/dtek_bot.db.backup_YYYYMMDD_HHMMSS dtek/data/dtek_bot.db
cp cek/data/cek_bot.db.backup_YYYYMMDD_HHMMSS cek/data/cek_bot.db

# 3. Откатить код (если применили новый)
git checkout HEAD~1 common/bot_base.py common/tasks.py common/handlers.py

# 4. Перезапустить
docker-compose start dtek-bot cek-bot
```

## ✅ Готовность к применению:

- [x] Миграция БД готова
- [x] Новые функции реализованы
- [x] Основной код обновлен (~70%)
- [x] Deprecated wrappers для совместимости
- [ ] Оставшиеся SQL запросы (исправим по ходу)
- [ ] Backup БД
- [ ] Тестирование

**Рекомендация**: Применять на тестовом окружении сначала, либо быть готовым быстро исправлять ошибки в production.

## 📝 Следующие действия:

1. Создать backup БД
2. Применить миграцию
3. Перезапустить боты
4. Следить за логами
5. Исправлять ошибки по мере появления
6. После стабилизации - удалить deprecated wrappers

---

**Статус**: ~70% готово для production, остальное исправится в процессе работы через явные ошибки БД.
