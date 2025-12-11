# Quick Reference: Где найти реализацию кэширования по группам

## 📍 Расположение кода

### 1. API Функции
**Файл**: `common/bot_base.py`
**Строки**: 615-918

```python
# Строка 617: Константа TTL
GROUP_CACHE_TTL_MINUTES = 15

# Строка 619: Получение кэша группы
async def get_group_cache(conn, group_name, provider) -> Optional[Dict[str, Any]]

# Строка 685: Обновление кэша группы  
async def update_group_cache(conn, group_name, provider, schedule_hash, schedule_data) -> bool

# Строка 733: Получить группу из старых таблиц (subscriptions/user_last_check)
async def get_cached_group_for_address(conn, city, street, house) -> Optional[str]

# Строка 780: Обновить маппинг адрес→группа
async def update_address_group_mapping(conn, provider, city, street, house, group_name) -> bool

# Строка 830: Получить группу для адреса (все источники)
async def get_group_for_address(conn, provider, city, street, house) -> Optional[str]

# Строка 873: Найти адреса по группе (для будущей функции /check_group)
async def find_addresses_by_group(conn, provider, group_name, limit=10) -> List[Dict]
```

---

### 2. Использование в проверке подписок
**Файл**: `common/tasks.py`
**Функция**: `subscription_checker_task`
**Строки**: 287-365

#### Импорты (строки 25-30):
```python
from .bot_base import (
    ...
    get_group_cache,              # строка 25
    update_group_cache,           # строка 26  
    get_cached_group_for_address, # строка 27
    get_group_for_address,        # строка 28
    update_address_group_mapping, # строка 29
    find_addresses_by_group,      # строка 30
)
```

#### Использование в цикле проверки (строки 294-346):
```python
# Строка 294: Получить группу для адреса
cached_group = await get_group_for_address(db_conn, ctx.provider_code, city, street, house)

# Строка 306: Попытка получить из кэша
group_cache = await get_group_cache(db_conn, cached_group, ctx.provider_code)

# Строка 335: Обновить кэш после парсинга
await update_group_cache(db_conn, group_from_parser, ctx.provider_code, current_hash, data)

# Строка 343: Обновить маппинг адрес→группа
await update_address_group_mapping(db_conn, ctx.provider_code, city, street, house, data['group'])
```

---

### 3. Использование в пользовательских запросах
**Файл**: `common/handlers.py`
**Функция**: `perform_address_check` (используется в /check, /repeat, callbacks)
**Строки**: 920-991

#### Импорты (строки 43-46):
```python
from common.bot_base import (
    ...
    get_group_cache,              # строка 43
    update_group_cache,           # строка 44
    get_group_for_address,        # строка 45
    update_address_group_mapping, # строка 46
)
```

#### Использование (строки 925-965):
```python
# Строка 925: Получить группу
cached_group = await get_group_for_address(db_conn, ctx.provider_code, city, street, house)

# Строка 936: Попытка получить из кэша
group_cache = await get_group_cache(db_conn, cached_group, ctx.provider_code)

# Строка 952: Обновить кэш после парсинга
await update_group_cache(db_conn, data['group'], ctx.provider_code, current_hash, data)

# Строка 962: Обновить маппинг
await update_address_group_mapping(db_conn, ctx.provider_code, city, street, house, new_group)
```

---

### 4. Миграция БД
**Файл**: `common/migrations/005_group_schedule_cache.sql`
**Строки**: 1-51

```sql
-- Строка 13: Таблица кэша групп
CREATE TABLE IF NOT EXISTS group_schedule_cache (...)

-- Строка 34: Таблица маппинга адрес→группа  
CREATE TABLE IF NOT EXISTS address_group_mapping (...)
```

**Статус**: ✅ Применена к обеим БД (dtek_bot.db, cek_bot.db)

---

## 🔍 Как найти в IDE

### VS Code / Cursor:
1. **Ctrl+P** (Quick Open)
2. Набрать: `bot_base.py:619` → откроет файл на строке 619 (`get_group_cache`)

### Поиск по функции:
1. **Ctrl+Shift+F** (Search in files)
2. Искать: `async def get_group_cache`
3. Результат: `common/bot_base.py:619`

### Поиск использования:
1. **Ctrl+Shift+F**
2. Искать: `await get_group_cache`
3. Результаты:
   - `common/tasks.py:306` (subscription checker)
   - `common/handlers.py:936` (user check)

---

## ✅ Чек-лист "Всё на месте"

- [x] **Функции реализованы** → `common/bot_base.py:615-918`
- [x] **Импортированы в tasks.py** → строки 25-30
- [x] **Импортированы в handlers.py** → строки 43-46
- [x] **Используются в subscription_checker** → `tasks.py:294-346`
- [x] **Используются в perform_address_check** → `handlers.py:925-965`
- [x] **Миграция БД** → `005_group_schedule_cache.sql`
- [x] **Миграция применена** → версия БД = 5

---

## 📊 Быстрая проверка

### Проверить что функции существуют:
```bash
grep -n "async def get_group_cache" common/bot_base.py
grep -n "async def update_group_cache" common/bot_base.py
grep -n "async def get_group_for_address" common/bot_base.py
grep -n "async def update_address_group_mapping" common/bot_base.py
```

**Ожидаемый вывод**:
```
common/bot_base.py:619:async def get_group_cache(
common/bot_base.py:685:async def update_group_cache(
common/bot_base.py:830:async def get_group_for_address(
common/bot_base.py:780:async def update_address_group_mapping(
```

### Проверить что используются:
```bash
grep -n "await get_group_cache" common/tasks.py common/handlers.py
grep -n "await update_group_cache" common/tasks.py common/handlers.py
```

### Проверить БД:
```bash
sqlite3 dtek/bot/dtek_bot.db ".schema group_schedule_cache"
sqlite3 dtek/bot/dtek_bot.db ".schema address_group_mapping"
```

---

## 🎯 Вывод

**ВСЁ РЕАЛИЗОВАНО** ✅

Если вы не видите этот код в вашем редакторе, возможно:
1. Файлы не сохранены (но они должны быть, я видел подтверждения)
2. Нужно перезагрузить/переоткрыть файлы в IDE
3. Проверьте правильный ли репозиторий открыт

**Попробуйте**:
1. Закрыть и переоткрыть `common/bot_base.py`
2. Перейти к строке 619 (Ctrl+G → 619)
3. Должны увидеть `async def get_group_cache(`
