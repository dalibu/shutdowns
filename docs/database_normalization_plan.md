# Database Normalization: Addresses Table

## 🎯 Цель

Устранить избыточное хранение адресных данных (city, street, house) в нескольких таблицах путем создания централизованной таблицы `addresses`.

## 📊 Текущая проблема

Адреса дублируются в **4 таблицах**:

1. **subscriptions** - (city, street, house, group_name)
2. **user_last_check** - (city, street, house, group_name)
3. **user_addresses** - (city, street, house, group_name)
4. **address_group_mapping** - (city, street, house, group_name)

**Проблемы**:
- ❌ Избыточность данных (~150-200 байт на запись × N таблиц)
- ❌ Риск inconsistency (группа может отличаться в разных таблицах)
- ❌ Сложность обновления (нужно обновлять в 4 местах)
- ❌ Медленные запросы с WHERE по (city, street, house)

## ✅ Решение: Нормализация

### Новая структура

```
addresses (центральная таблица)
├── id (PK)
├── provider ('dtek' or 'cek')
├── city
├── street
├── house
├── group_name (единый источник истины для группы)
├── created_at
└── updated_at

subscriptions (refactored)
├── id (PK)
├── user_id
├── address_id (FK → addresses.id)  ← ВМЕСТО (city, street, house)
├── interval_hours
├── next_check
├── last_schedule_hash
├── notification_lead_time
└── last_alert_event_start

user_last_check (refactored)
├── user_id (PK)
├── address_id (FK → addresses.id)  ← ВМЕСТО (city, street, house)
└── last_hash

user_addresses (refactored)
├── id (PK)
├── user_id
├── address_id (FK → addresses.id)  ← ВМЕСТО (city, street, house)
├── alias
├── created_at
└── last_used_at
```

### Что удаляется

- ❌ **address_group_mapping** - становится избыточной, её данные переносятся в `addresses.group_name`

## 📈 Преимущества

### 1. Экономия места
**До**: 4 таблицы × ~150 байт/адрес = ~600 байт на уникальный адрес
**После**: 1 таблица × ~150 байт + 4 байта в FK = ~154 байт на адрес
**Экономия**: ~75% для множественных подписок на один адрес

### 2. Consistency (консистентность)
- ✅ Группа хранится в **одном месте** (addresses.group_name)
- ✅ Обновление группы - один UPDATE на addresses
- ✅ Нет риска расхождения данных

### 3. Производительность
- ✅ JOIN по INTEGER (address_id) быстрее чем по 3 TEXT полям
- ✅ Меньше индексов нужно поддерживать
- ✅ Компактнее данные = лучше кэширование

### 4. Упрощение кода
- ✅ Меньше дублирования в SQL запросах
- ✅ Центральное место для получения группы по адресу
- ✅ Проще добавлять новые метаданные к адресам

## 🔄 План миграции

### Phase 1: База данных ✅
- [x] Создать миграцию `006_normalize_addresses.sql`
- [ ] Тестировать на копии БД
- [ ] Применить к production БД

### Phase 2: Обновление кода
Нужно обновить следующие функции:

#### `common/bot_base.py`:

**До**:
```python
async def get_group_for_address(conn, provider, city, street, house):
    # Ищет в address_group_mapping, subscriptions, user_last_check
```

**После**:
```python
async def get_address_id(conn, provider, city, street, house):
    """Get or create address_id for given address."""
    cursor = await conn.execute(
        "SELECT id, group_name FROM addresses WHERE provider = ? AND city = ? AND street = ? AND house = ?",
        (provider, city, street, house)
    )
    row = await cursor.fetchone()
    if row:
        return row[0], row[1]  # (address_id, group_name)
    
    # Create new address
    cursor = await conn.execute(
        "INSERT INTO addresses (provider, city, street, house) VALUES (?, ?, ?, ?)",
        (provider, city, street, house)
    )
    await conn.commit()
    return cursor.lastrowid, None

async def update_address_group(conn, address_id, group_name):
    """Update group for an address."""
    await conn.execute(
        "UPDATE addresses SET group_name = ?, updated_at = ? WHERE id = ?",
        (group_name, datetime.now(pytz.timezone('Europe/Kiev')), address_id)
    )
    await conn.commit()
```

**Удалить**:
- `update_address_group_mapping()` - заменить на `update_address_group()`
- `find_addresses_by_group()` - упростить (напрямую из addresses)

#### `common/tasks.py`:

**До**:
```python
cached_group = await get_group_for_address(db_conn, ctx.provider_code, city, street, house)
```

**После**:
```python
address_id, cached_group = await get_address_id(db_conn, ctx.provider_code, city, street, house)
```

**До**:
```python
await update_address_group_mapping(db_conn, ctx.provider_code, city, street, house, data['group'])
```

**После**:
```python
await update_address_group(db_conn, address_id, data['group'])
```

#### `common/handlers.py`:

Аналогичные изменения - использовать `address_id` вместо (city, street, house) в запросах.

**Пример для subscriptions**:

**До**:
```python
await db_conn.execute(
    "INSERT OR REPLACE INTO subscriptions (user_id, city, street, house, ...) VALUES (?, ?, ?, ?, ...)",
    (user_id, city, street, house, ...)
)
```

**После**:
```python
address_id, _ = await get_address_id(db_conn, provider, city, street, house)
await db_conn.execute(
    "INSERT OR REPLACE INTO subscriptions (user_id, address_id, ...) VALUES (?, ?, ...)",
    (user_id, address_id, ...)
)
```

### Phase 3: Тестирование
- [ ] Unit tests для новых функций
- [ ] Integration tests для миграции
- [ ] Performance tests (сравнить скорость запросов)

### Phase 4: Deployment
- [ ] Backup БД перед миграцией
- [ ] Применить миграцию
- [ ] Deploy обновленного кода
- [ ] Мониторинг на наличие ошибок

## ⚠️ Важные замечания

### TODO в миграции

В файле `006_normalize_addresses.sql` есть несколько мест с `TODO: Change based on which DB this runs on`:

```sql
-- Нужно заменить 'dtek' на соответствующий provider
a.provider = 'dtek' AND  -- TODO
```

**Решение**: Создать две версии миграции или использовать параметры при запуске.

### Откат (Rollback)

Если что-то пойдет не так, можно откатить:

1. Восстановить backup БД
2. Или создать обратную миграцию `006_rollback.sql`

### Compatibility

Во время переходного периода нужно поддерживать обе структуры:
- Старый код → старая структура
- Новый код → новая структура

**Рекомендация**: Делать атомарно (одновременно БД + код) для каждого бота.

## 📊 Пример использования после миграции

### Получить группу для адреса:
```python
# До (4 запроса к разным таблицам)
group = await get_group_for_address(conn, provider, city, street, house)

# После (1 запрос)
cursor = await conn.execute(
    "SELECT id, group_name FROM addresses WHERE provider = ? AND city = ? AND street = ? AND house = ?",
    (provider, city, street, house)
)
address_id, group_name = await cursor.fetchone()
```

### Обновить группу:
```python
# До (INSERT OR REPLACE в address_group_mapping + increment verification_count)
await update_address_group_mapping(conn, provider, city, street, house, group_name)

# После (простой UPDATE)
await conn.execute(
    "UPDATE addresses SET group_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
    (group_name, address_id)
)
```

### Найти все адреса в группе:
```python
# До (запрос к address_group_mapping)
cursor = await conn.execute(
    "SELECT city, street, house FROM address_group_mapping WHERE provider = ? AND group_name = ?",
    (provider, group_name)
)

# После (то же самое, но из addresses)
cursor = await conn.execute(
    "SELECT id, city, street, house FROM addresses WHERE provider = ? AND group_name = ?",
    (provider, group_name)
)
```

## 🚀 Готовность к реализации

### Статус миграции БД: ✅ ГОТОВА
- Файл: `common/migrations/006_normalize_addresses.sql`
- Протестировать на копии БД
- Исправить TODO (provider value)

### Статус кода: ⏳ ТРЕБУЕТСЯ РЕФАКТОРИНГ
- Оценка работы: 4-6 часов
- Затронуто ~15-20 функций
- Критичность: Средняя (не ломает существующую функциональность, улучшает)

## 🤔 Стоит ли делать сейчас?

### ЗА:
- ✅ Правильная архитектура БД
- ✅ Экономия места и performance
- ✅ Упрощение кода в будущем

### ПРОТИВ:
- ⏳ Требует времени на рефакторинг кода
- ⚠️ Риск bugs во время миграции
- 📊 Текущая оптимизация кэша уже реализована и работает

### Рекомендация

**Сделать в два этапа**:

1. **Сейчас**: Протестировать текущую имплементацию кэша (1-2 недели)
2. **Потом**: Сделать нормализацию как отдельный PR

Или если хотите сделать сейчас - я готов помочь с рефакторингом кода под новую структуру БД.

Что выберете?
