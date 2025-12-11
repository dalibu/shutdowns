# Group-Based Schedule Caching - Architecture & Implementation

## Обзор

Эта оптимизация решает две ключевые задачи:

1. **Снижение нагрузки на провайдеров** - адреса одной группы получают одинаковый график, поэтому можно кэшировать данные по группам вместо индивидуальных адресов
2. **Построение базы адресов** - накопление знаний о том, какие адреса принадлежат каким группам для будущей функции поиска по номеру группы

## Структура базы данных

### 1. `group_schedule_cache`

**Назначение**: Хранит актуальный график для каждой группы

```sql
CREATE TABLE group_schedule_cache (
    group_name TEXT NOT NULL,          -- Номер группы (например, "3.1")
    provider TEXT NOT NULL,             -- Провайдер: "dtek" или "cek"
    last_schedule_hash TEXT NOT NULL,   -- SHA256 хэш графика
    schedule_data TEXT,                 -- Полные данные графика в JSON
    last_updated TIMESTAMP NOT NULL,    -- Время последнего обновления
    PRIMARY KEY (group_name, provider)
);
```

**TTL (Time To Live)**: 15 минут
- Если кэш свежее 15 минут → используем кэш
- Если старше → запрашиваем у провайдера

### 2. `address_group_mapping`

**Назначение**: Накапливает знания о принадлежности адресов к группам

```sql
CREATE TABLE address_group_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,                 -- Провайдер
    city TEXT NOT NULL,                     -- Город
    street TEXT NOT NULL,                   -- Улица
    house TEXT NOT NULL,                    -- Дом
    group_name TEXT NOT NULL,               -- Группа отключений
    first_seen TIMESTAMP NOT NULL,          -- Когда впервые узнали
    last_verified TIMESTAMP NOT NULL,       -- Когда последний раз подтверждали
    verification_count INTEGER DEFAULT 1,   -- Сколько раз видели это соответствие
    UNIQUE(provider, city, street, house)
);
```

**Преимущества**:
- Постоянно растущая база знаний
- Позволяет избежать запросов к парсеру для известных адресов
- Подготовка к функции поиска по группе

## Логика работы

### Текущая реализация (проверка подписок)

```
1. Получить список адресов для проверки
2. Для каждого уникального адреса:
   a. Проверить, знаем ли мы группу для этого адреса
      → address_group_mapping
      → subscriptions
      → user_last_check
   
   b. Если группа известна:
      - Проверить group_schedule_cache
      - Если кэш свежий → использовать его
      - Если кэш устарел → запросить у провайдера
   
   c. Если группа неизвестна:
      - Запросить у провайдера (полный парсинг)
   
   d. После получения данных:
      - Сохранить/обновить group_schedule_cache
      - Сохранить/обновить address_group_mapping
      - Обновить подписки пользователей
```

### Будущая функция: Поиск по группе

```
Пользователь: "/check_group 3.1"

1. Проверить group_schedule_cache для группы 3.1
   
2. Если кэш свежий:
   → Показать график из кэша
   
3. Если кэш устарел или отсутствует:
   a. Найти любой адрес из этой группы в address_group_mapping
   b. Использовать этот адрес для запроса к провайдеру
   c. Обновить group_schedule_cache
   d. Показать график
   
4. Если группа неизвестна (нет в address_group_mapping):
   → "Группа 3.1 пока неизвестна. Пожалуйста, укажите адрес из этой группы,
      и мы сохраним его для будущих запросов."
```

## API Functions

### Работа с кэшем групп

#### `get_group_cache(conn, group_name, provider) -> Optional[Dict]`

Получить кэшированный график для группы.

**Returns**:
- `{'data': {...}, 'hash': '...'` если кэш свежий
- `None` если кэш отсутствует или устарел

**Example**:
```python
cache = await get_group_cache(db_conn, "3.1", "dtek")
if cache:
    schedule_data = cache['data']
    schedule_hash = cache['hash']
```

#### `update_group_cache(conn, group_name, provider, schedule_hash, schedule_data) -> bool`

Обновить кэш графика для группы.

**Example**:
```python
success = await update_group_cache(
    db_conn,
    "3.1",
    "dtek",
    "abc123...",
    {"city": "...", "schedule": {...}}
)
```

### Работа с соответствиями адрес-группа

#### `get_group_for_address(conn, provider, city, street, house) -> Optional[str]`

Узнать группу для адреса (из всех доступных источников).

**Example**:
```python
group = await get_group_for_address(
    db_conn, "dtek",
    "м. Дніпро", "вул. Сонячна набережна", "6"
)
# group = "3.1" или None
```

#### `update_address_group_mapping(conn, provider, city, street, house, group_name) -> bool`

Записать/обновить соответствие адрес → группа.

**Когда вызывать**: Каждый раз когда получаем данные от парсера и узнаём группу.

**Example**:
```python
await update_address_group_mapping(
    db_conn, "dtek",
    "м. Дніпро", "вул. Сонячна набережна", "6",
    "3.1"
)
```

#### `find_addresses_by_group(conn, provider, group_name, limit=10) -> List[Dict]`

Найти адреса, принадлежащие группе.

**Returns**: Список адресов, отсортированных по частоте проверок.

**Example**:
```python
addresses = await find_addresses_by_group(db_conn, "dtek", "3.1", limit=5)
# [
#   {'city': '...', 'street': '...', 'house': '...', 
#    'verification_count': 42, 'last_verified': '...'},
#   ...
# ]
```

## Метрики эффективности

### До оптимизации
- 100 пользователей с адресами группы 3.1
- 100 запросов к провайдеру каждые N часов

### После оптимизации
- 100 пользователей с адресами группы 3.1
- **1 запрос** к провайдеру каждые 15 минут (при наличии активных проверок)
- **~99% снижение** нагрузки на провайдера

### Дополнительные выгоды
- Мгновенный ответ из кэша (без ожидания парсера)
- Построение базы знаний для future features
- Возможность offline-режима (если кэш свежий)

## Интеграция в существующий код

### Шаг 1: Применить миграцию

```bash
python -m common.migrate --db-path dtek/bot/dtek_bot.db
python -m common.migrate --db-path cek/bot/cek_bot.db
```

### Шаг 2: Обновить логику проверки подписок (уже реализовано частично)

В `subscription_checker_task`:

```python
# Для каждого адреса:
group = await get_group_for_address(db_conn, provider_code, city, street, house)

if group:
    # Попытка использовать кэш группы
    cache = await get_group_cache(db_conn, group, provider_code)
    if cache:
        data = cache['data']
        current_hash = cache['hash']
        logger.info(f"Using group cache for {group}")
    else:
        # Кэш устарел, запрашиваем
        data = await get_shutdowns_data(city, street, house, group)
        current_hash = get_schedule_hash_compact(data)
        
        # Обновляем кэш группы
        await update_group_cache(db_conn, group, provider_code, current_hash, data)
else:
    # Группа неизвестна, полный парсинг
    data = await get_shutdowns_data(city, street, house)
    current_hash = get_schedule_hash_compact(data)
    
    # Узнали группу из ответа парсера
    if data.get('group'):
        group = data['group']
        await update_group_cache(db_conn, group, provider_code, current_hash, data)
        await update_address_group_mapping(db_conn, provider_code, city, street, house, group)
```

### Шаг 3: Добавить команду `/check_group` (будущая функция)

```python
async def handle_check_group_command(message: types.Message, ctx: BotContext):
    """
    Проверить график по номеру группы.
    Использование: /check_group 3.1
    """
    try:
        group_name = message.text.replace('/check_group', '').strip()
        if not group_name:
            await message.answer("Вкажіть номер черги. Приклад: `/check_group 3.1`")
            return
        
        # Проверить кэш группы
        cache = await get_group_cache(ctx.db_conn, group_name, ctx.provider_code)
        
        if cache:
            data = cache['data']
            # Показать график...
        else:
            # Найти хотя бы один адрес из этой группы
            addresses = await find_addresses_by_group(ctx.db_conn, ctx.provider_code, group_name, limit=1)
            
            if addresses:
                addr = addresses[0]
                # Запросить график для этого адреса
                data = await get_shutdowns_data(addr['city'], addr['street'], addr['house'])
                # Обновить кэш...
            else:
                await message.answer(
                    f"❌ Черга **{group_name}** поки невідома в нашій базі.\n\n"
                    f"Будь ласка, вкажіть адресу з цієї черги командою `/check Місто, Вулиця, Будинок`, "
                    f"і ми збережемо його для майбутніх запитів."
                )
                return
    except Exception as e:
        logger.error(f"Error in check_group: {e}")
        await message.answer("❌ Помилка при обробці запиту")
```

## Тестирование

### Проверка работы кэша

```python
import asyncio
from common.bot_base import init_db, get_group_cache, update_group_cache

async def test_group_cache():
    conn = await init_db("test.db")
    
    # Симулировать данные
    test_data = {
        "city": "м. Дніпро",
        "group": "3.1",
        "schedule": {"01.01.25": [{"shutdown": "10:00–12:00"}]}
    }
    test_hash = "abc123"
    
    # Записать в кэш
    await update_group_cache(conn, "3.1", "dtek", test_hash, test_data)
    
    # Прочитать из кэша
    cache = await get_group_cache(conn, "3.1", "dtek")
    
    assert cache is not None
    assert cache['hash'] == test_hash
    assert cache['data']['group'] == "3.1"
    
    print("✓ Group cache works!")

asyncio.run(test_group_cache())
```

## Мониторинг

### Метрики для отслеживания

1. **Cache Hit Rate** (процент попаданий в кэш)
   ```sql
   -- Можно добавить таблицу для логирования
   CREATE TABLE cache_stats (
       timestamp TIMESTAMP,
       group_name TEXT,
       cache_hit BOOLEAN,
       provider TEXT
   );
   ```

2. **Provider Request Count** (количество запросов к провайдеру)
   - Логировать каждый реальный запрос к парсеру
   - Сравнивать с количеством проверок подписок

3. **Address Mapping Growth** (рост базы адресов)
   ```sql
   SELECT COUNT(*) FROM address_group_mapping;
   SELECT COUNT(DISTINCT group_name) FROM address_group_mapping WHERE provider = 'dtek';
   ```

## Roadmap

### Phase 1: Основная оптимизация ✅
- [x] Миграция БД
- [x] Функции работы с кэшем групп
- [x] Функции работы с соответствиями адрес-группа
- [ ] Интеграция в subscription_checker_task
- [ ] Тестирование на production

### Phase 2: Поиск по группе
- [ ] Команда `/check_group <number>`
- [ ] UI для выбора известных групп
- [ ] Статистика по группам

### Phase 3: Аналитика
- [ ] Dashboard с метриками кэша
- [ ] Мониторинг нагрузки на провайдеров
- [ ] Отчеты по популярным группам

## Заключение

Эта оптимизация:
- ✅ Снижает нагрузку на провайдеров на ~99%
- ✅ Ускоряет ответы пользователям (кэш vs парсинг)
- ✅ Накапливает знания для новых функций
- ✅ Масштабируема (работает для любого количества пользователей)
- ✅ Обратно совместима (не ломает существующий функционал)
