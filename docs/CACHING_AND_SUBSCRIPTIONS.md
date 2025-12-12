# Архитектура кэширования и автоматических проверок

## Обзор

Система использует многоуровневую архитектуру кэширования для оптимизации запросов к парсеру и снижения нагрузки на сайт провайдера. Документ описывает, как работает кэширование на уровне групп, автоматические проверки подписок и взаимодействие компонентов системы.

---

## 1. Основные компоненты

### 1.1. Таблицы базы данных

#### `addresses` (нормализованная таблица адресов)
Центральная таблица для хранения всех адресов в системе.

```sql
CREATE TABLE addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,           -- "dtek" или "cek"
    city TEXT NOT NULL,
    street TEXT NOT NULL,
    house TEXT NOT NULL,
    group_name TEXT,                  -- Группа отключений (например, "3.1", "5.2")
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, city, street, house)
);
```

**Назначение:**
- Единственный источник истины для связи адрес → группа
- Исключает дублирование адресов в системе
- Позволяет быстро найти все адреса одной группы

#### `group_cache` (кэш расписаний по группам)
Хранит актуальные расписания для каждой группы отключений.

```sql
CREATE TABLE group_cache (
    group_name TEXT NOT NULL,         -- Идентификатор группы ("3.1", "5.2", и т.д.)
    provider TEXT NOT NULL,           -- Провайдер ("dtek", "cek")
    last_schedule_hash TEXT,          -- Хэш расписания для детекции изменений
    schedule_data TEXT,               -- JSON с полным расписанием
    last_updated TIMESTAMP,           -- Время последнего обновления
    PRIMARY KEY (group_name, provider)
);
```

**Назначение:**
- Централизованное хранение расписаний для групп
- Один запрос к парсеру обновляет данные для всех адресов группы
- Время жизни кэша: **10 минут**

#### `subscriptions` (подписки пользователей)
Связывает пользователей с адресами для автоматических проверок.

```sql
CREATE TABLE subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    address_id INTEGER NOT NULL,              -- Ссылка на addresses.id
    interval_hours REAL DEFAULT 1.0,          -- Интервал проверки (часы)
    next_check TIMESTAMP,                     -- Время следующей проверки
    last_schedule_hash TEXT,                  -- Последний известный хэш
    notification_lead_time INTEGER DEFAULT 0, -- Время уведомления (минуты)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (address_id) REFERENCES addresses(id) ON DELETE CASCADE
);
```

---

## 2. Архитектура кэширования

### 2.1. Кэш на уровне групп

Ключевая идея: **адреса одной группы имеют одинаковое расписание отключений**.

#### Пример:
```
Группа 3.1:
  ├─ м. Дніпро, вул. Сонячна набережна, 6
  ├─ м. Дніпро, вул. Інша, 10
  └─ м. Дніпро, вул. Третя, 15

Группа 5.2:
  ├─ м. Дніпро, вул. Центральна, 1
  └─ м. Дніпро, вул. Нова, 20
```

При проверке **любого** адреса группы 3.1:
1. Система определяет группу → `3.1`
2. Проверяет кэш для группы `3.1`
3. Если кэш свежий (< 10 мин) → возвращает данные из кэша
4. Если кэш устарел → вызывает парсер и обновляет кэш

### 2.2. Время жизни кэша

**10 минут** (`CACHE_MAX_AGE_MINUTES = 10`)

Логика в `get_group_cache()` (common/bot_base.py):
```python
age_minutes = (now - last_updated).total_seconds() / 60.0

if age_minutes > CACHE_MAX_AGE_MINUTES:
    logger.debug(f"Group cache is stale ({age_minutes:.1f} min old)")
    return None  # Кэш устарел

return {'data': schedule_data, 'hash': last_hash}
```

### 2.3. In-Memory кэши

Дополнительные кэши в оперативной памяти для ускорения доступа:

```python
# common/bot_base.py
ADDRESS_CACHE: Dict[Tuple[str, str, str], Dict] = {}
SCHEDULE_DATA_CACHE: Dict[Tuple[str, str, str], Dict] = {}
```

**Назначение:**
- `ADDRESS_CACHE`: последний хэш и время проверки адреса
- `SCHEDULE_DATA_CACHE`: данные расписания для алертов и быстрого доступа

**Время жизни:** до перезапуска бота (сбрасываются при рестарте)

---

## 3. Автоматические проверки подписок

### 3.1. Фоновая задача

**Файл:** `common/tasks.py` → `subscription_checker_task()`

**Периодичность:** каждые **60 секунд**

```python
CHECKER_LOOP_INTERVAL_SECONDS = 60  # common/bot_base.py

while True:
    await asyncio.sleep(60)
    # Проверка подписок
```

### 3.2. Алгоритм работы

#### Шаг 1: Выбор подписок для проверки
```sql
SELECT s.user_id, a.city, a.street, a.house, s.interval_hours, s.last_schedule_hash
FROM subscriptions s
JOIN addresses a ON a.id = s.address_id
WHERE s.next_check <= NOW()
```

Проверяются только подписки, у которых пришло время (`next_check <= NOW()`).

#### Шаг 2: Группировка по адресам
```python
# Один адрес может иметь несколько подписчиков
addresses_to_check_map = {
    ("м. Дніпро", "вул. Сонячна", "6"): [user_id_1, user_id_2, ...],
    ("м. Дніпро", "вул. Інша", "10"): [user_id_3],
}
```

**Оптимизация:** парсер вызывается один раз на адрес, результат используется для всех подписчиков.

#### Шаг 3: Проверка с использованием кэша

```python
for address_key in addresses_to_check_map:
    city, street, house = address_key
    
    # 1. Получить ID адреса и группу
    address_id, cached_group = await get_address_id(db_conn, city, street, house)
    
    # 2. Попытка получить из кэша группы
    if cached_group:
        group_cache = await get_group_cache(db_conn, cached_group, provider_code)
        
        if group_cache:
            # ✅ Cache HIT
            logger.info(f"✓ Cache HIT for {address}, group {cached_group}")
            data = group_cache['data']
            current_hash = group_cache['hash']
            used_cache = True
        else:
            # ❌ Cache MISS (устарел или не найден)
            logger.info(f"✗ Cache MISS for {address}, group {cached_group}")
    
    # 3. Если кэша нет - вызов парсера
    if data is None:
        data = await get_shutdowns_data(city, street, house)
        current_hash = get_schedule_hash_compact(data)
        
        # 4. Обновить кэш группы
        if data.get('group'):
            await update_group_cache(
                db_conn, data['group'], provider_code,
                current_hash, data
            )
    
    # 5. Обновить группу адреса (если изменилась)
    if address_id and data.get('group'):
        await update_address_group(db_conn, address_id, data['group'])
```

#### Шаг 4: Детекция изменений и отправка уведомлений

```python
new_hash = get_schedule_hash_compact(data)
last_hash = subscription['last_schedule_hash']

if new_hash != last_hash:
    # Расписание изменилось!
    logger.info(f"Hash changed: {last_hash[:16]} → {new_hash[:16]}")
    
    # Отправить уведомление пользователю
    await bot.send_message(user_id, ...)
    
    # Обновить хэш в БД
    await db_conn.execute(
        "UPDATE subscriptions SET last_schedule_hash = ? WHERE ...",
        (new_hash, ...)
    )
```

#### Шаг 5: Планирование следующей проверки

```python
next_check_time = now + timedelta(hours=interval_hours)

await db_conn.execute(
    "UPDATE subscriptions SET next_check = ? WHERE user_id = ? AND address_id = ?",
    (next_check_time, user_id, address_id)
)
```

---

## 4. Типы проверок

### 4.1. Ручная проверка (`/check`) ⚡ ОПТИМИЗИРОВАНО

**Когда:** пользователь вызывает команду `/check`

**Использует кэш групп:** ✅ **ДА** (с версии feature/manual-check-cache-optimization)

**Логика:**
```python
# dtek/bot/bot.py (и cek/bot/bot.py)
async def get_shutdowns_data(city: str, street: str, house: str):
    # 1. Попытка получить из кэша
    address_id, cached_group = await get_address_id(db_conn, city, street, house)
    
    if cached_group:
        group_cache = await get_group_cache(db_conn, cached_group, provider)
        if group_cache:
            # ✅ Cache HIT - мгновенный ответ!
            return group_cache['data']
    
    # 2. Если кэша нет - вызвать парсер
    source = get_data_source()
    data = await source.get_schedule(city, street, house)
    
    # 3. Обновить кэш после парсинга
    if data.get('group'):
        await update_group_cache(db_conn, data['group'], provider, hash, data)
    
    return data
```

**Результат:**
- **Первая проверка адреса:** вызывается парсер (5-15 секунд)
- **Повторная проверка (<15 мин):** данные из кэша (**<1 секунда**) ⚡
- После парсинга обновляет кэш группы
- Взаимодействует с кэшем автоматических проверок (синергия!)

**Улучшение:** 60-90% снижение времени ожидания для повторных проверок

### 4.2. Автоматическая проверка (подписки)

**Когда:** фоновая задача каждые 60 секунд

**Использует кэш групп:** ✅ **ДА**

**Логика:** см. раздел 3.2 выше

**Результат:**
- Если кэш свежий (<10 мин) → мгновенный ответ из кэша
- Если кэш устарел → вызов парсера (5-15 сек)
- Один вызов парсера обновляет кэш для всех адресов группы

---

## 5. Диаграмма потока данных

### 5.1. Ручная проверка `/check`

```
User
  │
  ├─> /check м. Дніпро, вул. Сонячна, 6
  │
  ▼
Bot Handler
  │
  ├─> get_shutdowns_data(city, street, house)
  │
  ▼
Parser (Botasaurus)
  │
  ├─> Запуск браузера
  ├─> Заполнение формы на dtek-dnem.com.ua
  ├─> Парсинг таблицы
  │
  ▼
Result
  │
  ├─> Определение группы: "3.1"
  ├─> Сохранение в addresses (group_name = "3.1")
  ├─> Обновление group_cache для "3.1"
  │
  ▼
User receives schedule
```

**Время:** 5-15 секунд

---

### 5.2. Автоматическая проверка (с Cache HIT)

```
Background Task (every 60s)
  │
  ├─> SELECT subscriptions WHERE next_check <= NOW()
  │   Result: user_id=123, address="м. Дніпро, вул. Сонячна, 6"
  │
  ▼
Check Group Cache
  │
  ├─> get_address_id() → address_id=1, group="3.1"
  ├─> get_group_cache("3.1", "dtek")
  │
  ▼
Cache Found (age < 10 min)
  │
  ├─> ✅ Cache HIT!
  ├─> Return cached data
  │
  ▼
Hash Comparison
  │
  ├─> new_hash == last_hash?
  │   └─> YES: No notification
  │   └─> NO: Send update to user
```

**Время:** < 100 мс

---

### 5.3. Автоматическая проверка (с Cache MISS)

```
Background Task (every 60s)
  │
  ├─> SELECT subscriptions WHERE next_check <= NOW()
  │
  ▼
Check Group Cache
  │
  ├─> get_group_cache("3.1", "dtek")
  │   └─> Cache expired (age > 10 min) or not found
  │
  ▼
Call Parser
  │
  ├─> get_shutdowns_data() → Parser
  ├─> Получение свежих данных
  │
  ▼
Update Group Cache
  │
  ├─> update_group_cache("3.1", data)
  │   └─> Все адреса группы 3.1 теперь имеют свежий кэш!
  │
  ▼
Hash Comparison & Notification
```

**Время:** 5-15 секунд (из-за парсера)

---

## 6. Преимущества групповог кэша

### 6.1. Снижение нагрузки на парсер

**Без кэша групп:**
```
100 подписчиков на адреса группы 3.1
→ 100 вызовов парсера в час
→ ~1.5 минуты работы парсера
```

**С кэшем групп:**
```
100 подписчиков на адреса группы 3.1
→ 1 вызов парсера каждые 10 минут
→ 6 вызовов парсера в час
→ ~90 секунд экономии!
```

**Эффективность:** снижение нагрузки в **16 раз** для популярных групп

### 6.2. Быстрые ответы пользователям

Если адрес проверялся недавно (< 10 мин):
- Автоматическая проверка: **мгновенная** (< 100 мс)
- Ручная проверка: по-прежнему медленная (5-15 сек) ⚠️

---

## 7. Лог-сообщения для мониторинга

### 7.1. Cache HIT (успешное использование кэша)
```
INFO:common.tasks:✓ Cache HIT for `м. Дніпро, вул. Сонячна набережна, 6`, group 3.1 (age: fresh)
```

### 7.2. Cache MISS (кэш устарел)
```
INFO:common.tasks:✗ Cache MISS for `м. Дніпро, вул. Сонячна набережна, 6`, group 3.1 (stale or not found)
INFO:common.tasks:Calling parser for address `м. Дніпро, вул. Сонячна набережна, 6`
```

### 7.3. Обновление кэша группы
```
DEBUG:common.bot_base:Updated group cache for 3.1 (dtek), hash: a3f2b8c1d4e5f6a7
```

### 7.4. Изменение расписания
```
INFO:common.tasks:Hash changed for `м. Дніпро, вул. Сонячна набережна, 6`: a3f2b8c1d4e5f6a7 → b9c3d1e2f4a5b6c7
INFO:common.tasks:Notification sent. Hash updated to b9c3d1e2.
```

---

## 8. Оптимизация ручных проверок `/check` (РЕАЛИЗОВАНО)

### 8.1. Проблема до оптимизации

**Исходное поведение:**
- Команда `/check` **всегда** вызывала парсер, даже если данные были в кэше
- Время ответа: **5-15 секунд** в любом случае
- Дублирование работы с автоматическими проверками

**Пример:**
```
08:00 - Автоматическая проверка подписки → парсер вызван, кэш обновлен
08:02 - Пользователь делает /check → парсер вызван СНОВА (!)
```

### 8.2. Решение

Модифицирована функция `get_shutdowns_data()` в обоих ботах для использования кэша групп перед вызовом парсера.

**Файлы:**
- `dtek/bot/bot.py` (строки 162-221)
- `cek/bot/bot.py` (строки 161-225)

**Алгоритм:**

```python
async def get_shutdowns_data(city: str, street: str, house: str) -> dict:
    """
    1. Спочатку перевіряє наявність свіжого кешу для групи адреси
    2. Якщо кеш свіжий - повертає дані з кешу (миттєво)
    3. Якщо кешу немає або він застарів - викликає парсер
    4. Після отримання даних від парсера - оновлює кеш групи
    """
    # Step 1: Try to get address_id and cached group
    address_id, cached_group = await get_address_id(db_conn, city, street, house)
    
    # Step 2: If group is known, try to use group cache
    if address_id and cached_group:
        group_cache = await get_group_cache(db_conn, cached_group, "dtek")
        
        if group_cache:
            # ✅ Cache HIT! Return cached data immediately
            logger.info(f"✓ Cache HIT for manual /check, group {cached_group}")
            return group_cache['data']
    
    # Step 3: Cache miss or no group - call parser
    source = get_data_source()
    data = await source.get_schedule(city, street, house)
    
    # Step 4: Update group cache with fresh data
    if data.get('group'):
        await update_group_cache(db_conn, data['group'], "dtek", hash, data)
        
    return data
```

### 8.3. Сценарии работы после оптимизации

#### Сценарий 1: Первая проверка адреса (кэша нет)

```
Пользователь → /check м. Дніпро, вул. Сонячна набережна, 6
          ↓
    1. get_address_id() → address_id=NULL, group=NULL
          ↓
    2. Кэша нет → вызвать парсер (5-15 сек)
          ↓
    3. Парсер вернул: group="3.1", schedule={...}
          ↓
    4. Сохранить в кэш группы "3.1"
          ↓
    5. Сохранить адрес в addresses с group="3.1"
          ↓
    Ответ пользователю
```

**Время:** 5-15 секунд (парсер)  
**Лог:** `DEBUG: No cached group, calling parser`

---

#### Сценарий 2: Повторная проверка (кэш свежий)

```
Пользователь → /check м. Дніпро, вул. Сонячна набережна, 6
          ↓
    1. get_address_id() → address_id=1, group="3.1"
          ↓
    2. get_group_cache("3.1") → возраст 2 мин < 15 мин
          ↓
    3. ✅ Cache HIT!
          ↓
    4. Вернуть данные из кэша (БЕЗ парсера!)
          ↓
    Мгновенный ответ пользователю
```

**Время:** < 1 секунда ⚡  
**Лог:** `INFO: ✓ Cache HIT for manual /check, group 3.1 (instant response)`

---

#### Сценарий 3: Проверка другого адреса той же группы

```
Пользователь → /check м. Дніпро, вул. Інша, 10
          ↓
    1. get_address_id() → address_id=NULL (новый), group=NULL
          ↓
    2. Кэша для адреса нет → вызвать парсер
          ↓
    3. Парсер: group="3.1" (та же группа!)
          ↓
    4. Обновить кэш группы "3.1"
          ↓
    5. Сохранить адрес с group="3.1"

--- Теперь оба адреса используют один кэш ---

Пользователь → /check (первый адрес снова)
          ↓
    ✅ Cache HIT! (кэш только что обновлен)
```

---

#### Сценарий 4: Синергия с автоматическими проверками

```
Временная шкала:

08:00 │ Подписка создана → парсер → кэш сохранен
      │
08:02 │ /check → ✅ Cache HIT (2 мин) → <1 сек
      │                                      
08:05 │ Автопроверка → ✅ Cache HIT (5 мин) → <1 сек
      │
08:10 │ /check → ✅ Cache HIT (10 мин) → <1 сек
      │
08:16 │ Автопроверка → ✗ Cache MISS (16 мин > 15) → парсер → обновление
      │
08:18 │ /check → ✅ Cache HIT (2 мин) → <1 сек
```

**Вывод:** Автоматические проверки "прогревают" кэш для ручных проверок!

### 8.4. Измеримые улучшения

#### До оптимизации:
| Действие | Время | Использует кэш |
|----------|-------|----------------|
| `/check` (любой раз) | 5-15 сек | ❌ НЕТ |
| Автопроверка (кэш свежий) | <1 сек | ✅ ДА |
| Автопроверка (кэш устарел) | 5-15 сек | ❌ НЕТ |

#### После оптимизации:
| Действие | Время | Использует кэш |
|----------|-------|----------------|
| `/check` (первый раз) | 5-15 сек | ❌ НЕТ |
| `/check` (повтор < 15 мин) | **<1 сек** | ✅ **ДА** |
| Автопроверка (кэш свежий) | <1 сек | ✅ ДА |
| Автопроверка (кэш устарел) | 5-15 сек | ❌ НЕТ |

#### Статистика улучшений:

**Типичный пользователь (3 проверки в течение 15 минут):**
- **До:** 3 × 10 сек = 30 секунд общего времени ожидания
- **После:** 10 + 1 + 1 = 12 секунд общего времени ожидания
- **Улучшение:** **60% снижение времени ожидания**

**Популярная группа (100 ручных проверок в течение 15 минут):**
- **До:** 100 вызовов парсера
- **После:** ~7-10 вызовов парсера (в зависимости от распределения)
- **Улучшение:** **90% снижение нагрузки на парсер**

### 8.5. Лог-сообщения

#### Cache HIT (мгновенный ответ)
```log
DEBUG: Found cached group 3.1 for address м. Дніпро, вул. Сонячна набережна, 6
INFO: ✓ Cache HIT for manual /check, group 3.1 (instant response)
```

#### Cache MISS (кэш устарел)
```log
DEBUG: Found cached group 3.1 for address м. Дніпро, вул. Сонячна набережна, 6
INFO: ✗ Cache MISS for manual /check, group 3.1 (calling parser)
DEBUG: Updated group cache for 3.1 after manual check
```

#### Нет кэша (первая проверка)
```log
DEBUG: No cached group for address м. Дніпро, вул. Сонячна набережна, 6 (calling parser)
DEBUG: Updated group cache for 3.1 after manual check
```

### 8.6. Настройка баланса скорость/свежесть

Через переменную окружения `GROUP_CACHE_TTL_MINUTES`:

```bash
# Агрессивный кэш (больше HIT, реже обновления)
GROUP_CACHE_TTL_MINUTES=30

# Стандарт (баланс)
GROUP_CACHE_TTL_MINUTES=15

# Свежие данные (меньше HIT, частые обновления)
GROUP_CACHE_TTL_MINUTES=5
```

**Рекомендация:** 15 минут оптимально для большинства случаев.

### 8.7. Дополнительная оптимизация: предзагрузка популярных групп

Автоматически обновлять кэш для самых популярных групп:

```python
# Проверять топ-10 групп каждые 5 минут
async def preload_popular_groups():
    popular_groups = await get_popular_groups(limit=10)
    for group in popular_groups:
        # Найти любой адрес этой группы
        address = await find_address_by_group(group)
        # Обновить кэш
        await get_shutdowns_data(address.city, address.street, address.house)
```

**Статус:** ❌ Не реализовано (опциональное улучшение для будущего)

**Полезность:** ⭐⭐⭐ (3/5)
- **Плюсы:** Гарантия свежего кэша для популярных групп
- **Минусы:** Дополнительная нагрузка на парсер, сложность реализации
- **Вывод:** Текущая оптимизация (8.1-8.6) уже даёт 60-90% улучшение, эта идея может быть добавлена при необходимости

---

### 8.8. Инвалидация кэша (FUTURE)

#### Проблема

Иногда может потребоваться принудительно сбросить кэш:
- Администратор знает, что данные на сайте изменились срочно
- Обнаружены некорректные данные в кэше
- Пользователь хочет гарантированно свежие данные

#### Предлагаемые команды

**Для администраторов:**

```python
@dp.message(Command("invalidate_cache"))
async def cmd_invalidate_cache(message: types.Message):
    """Сбросить весь кэш (admin only)"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    parts = message.text.split(maxsplit=1)
    
    if len(parts) == 1:
        # Сбросить весь кэш
        await db_conn.execute("DELETE FROM group_schedule_cache")
        await db_conn.commit()
        await message.answer("✅ Весь кэш групп очищен")
        logger.info(f"Admin {message.from_user.id} cleared entire group cache")
    else:
        # /invalidate_cache 3.1
        group_name = parts[1].strip()
        await db_conn.execute(
            "DELETE FROM group_schedule_cache WHERE group_name = ?",
            (group_name,)
        )
        await db_conn.commit()
        await message.answer(f"✅ Кэш для группы {group_name} очищен")
        logger.info(f"Admin {message.from_user.id} cleared cache for group {group_name}")
```

**Для пользователей:**

```python
@dp.message(Command("refresh"))
async def cmd_refresh(message: types.Message):
    """Принудительно обновить расписание для последнего адреса"""
    # Получить последний проверенный адрес пользователя
    address = await get_last_checked_address(db_conn, message.from_user.id)
    
    if not address:
        await message.answer("❌ Нет недавно проверенных адресов")
        return
    
    # Инвалидировать кэш для группы этого адреса
    if address.get('group_name'):
        await db_conn.execute(
            "DELETE FROM group_schedule_cache WHERE group_name = ? AND provider = ?",
            (address['group_name'], 'dtek')
        )
        await db_conn.commit()
        logger.info(f"User {message.from_user.id} invalidated cache for group {address['group_name']}")
    
    # Вызвать проверку (парсер будет вызван, т.к. кэша нет)
    await message.answer("⏳ Получаю свежие данные...")
    data = await get_shutdowns_data(address['city'], address['street'], address['house'])
    
    await send_schedule_response(message, data, is_subscribed=False)
```

#### Варианты реализации

**1. Soft invalidation (пометка как устаревший):**
```python
# Не удалять, а пометить timestamp как устаревший
await db_conn.execute(
    "UPDATE group_schedule_cache SET last_updated = ? WHERE group_name = ?",
    (datetime(2000, 1, 1), group_name)
)
```
**Плюсы:** Сохраняет историю, можно откатить  
**Минусы:** Кэш остаётся в БД

**2. Hard invalidation (полное удаление):**
```python
# Полностью удалить из БД
await db_conn.execute(
    "DELETE FROM group_schedule_cache WHERE group_name = ?",
    (group_name,)
)
```
**Плюсы:** Чистая БД, гарантия свежести  
**Минусы:** Нет возможности откатить

**3. Selective invalidation (по времени):**
```python
# Удалить кэш старше N минут
await db_conn.execute("""
    DELETE FROM group_schedule_cache 
    WHERE last_updated < datetime('now', '-5 minutes')
""")
```

#### Когда это нужно?

**✅ Полезные сценарии:**

1. **Экстренная ситуация**
   ```
   ДТЕК срочно изменил расписание из-за аварии
   → Админ: /invalidate_cache
   → Все пользователи получат свежие данные
   ```

2. **Обнаружен баг в парсере**
   ```
   Парсер записал некорректные данные в кэш
   → Админ: /invalidate_cache 3.1
   → Следующая проверка получит исправленные данные
   ```

3. **Пользователь видит старые данные**
   ```
   Пользователь: "У меня показывается старое расписание!"
   → Пользователь: /refresh
   → Получит гарантированно свежие данные
   ```

**❌ Менее полезно когда:**
- Кэш TTL правильно настроен (15 мин достаточно для большинства случаев)
- Нет частых экстренных изменений расписания
- Пользователи могут просто подождать истечения TTL

#### Анализ полезности

**Статус:** ❌ Не реализовано (опциональное улучшение)

**Полезность:** ⭐⭐⭐⭐ (4/5)

**Плюсы:**
- ✅ Полезно для экстренных ситуаций
- ✅ Даёт контроль администраторам
- ✅ Помогает при отладке и тестировании
- ✅ Улучшает UX (команда `/refresh` для пользователей)

**Минусы:**
- ⚠️ Может быть злоупотреблена (пользователи будут часто жать `/refresh`)
- ⚠️ Дополнительная нагрузка на парсер при массовой инвалидации
- ⚠️ Нужен rate limiting для `/refresh`

**Рекомендация:**
- **Для админов:** Полезно, стоит реализовать `/invalidate_cache`
- **Для пользователей:** Добавить `/refresh` с rate limiting (1 раз в 5 минут)

#### Пример Rate Limiting

```python
from datetime import datetime, timedelta

# In-memory store для rate limiting
LAST_REFRESH: Dict[int, datetime] = {}
REFRESH_COOLDOWN_MINUTES = 5

@dp.message(Command("refresh"))
async def cmd_refresh(message: types.Message):
    user_id = message.from_user.id
    now = datetime.now()
    
    # Проверить cooldown
    if user_id in LAST_REFRESH:
        elapsed = (now - LAST_REFRESH[user_id]).total_seconds() / 60
        if elapsed < REFRESH_COOLDOWN_MINUTES:
            remaining = REFRESH_COOLDOWN_MINUTES - elapsed
            await message.answer(
                f"⏳ Команда /refresh доступна через {remaining:.1f} хв.\n"
                f"Кэш обновляется автоматически каждые 15 минут."
            )
            return
    
    # Выполнить refresh
    LAST_REFRESH[user_id] = now
    # ... (код инвалидации и проверки)
```

#### Приоритет реализации

1. **Высокий приоритет:** `/invalidate_cache` для админов (просто, полезно)
2. **Средний приоритет:** `/refresh` для пользователей с rate limiting
3. **Низкий приоритет:** Soft invalidation и advanced механизмы



---

## 9. Конфигурационные константы

**Файл:** `common/bot_base.py`

Все константы можно настроить через переменные окружения (`.env` файл). Если переменная не задана, используется значение по умолчанию.

### Константы кэширования и проверок

```python
# Время жизни кэша группы (минуты)
# Переменная окружения: GROUP_CACHE_TTL_MINUTES
GROUP_CACHE_TTL_MINUTES = int(os.getenv("GROUP_CACHE_TTL_MINUTES", "15"))

# Интервал проверки подписок (секунды)  
# Переменная окружения: CHECKER_LOOP_INTERVAL_SECONDS
CHECKER_LOOP_INTERVAL_SECONDS = int(os.getenv("CHECKER_LOOP_INTERVAL_SECONDS", "300"))

# Интервал по умолчанию для подписок (часы)
# Переменная окружения: DEFAULT_INTERVAL_HOURS
DEFAULT_INTERVAL_HOURS = float(os.getenv("DEFAULT_INTERVAL_HOURS", "1.0"))
```

### Настройка через .env

Добавьте в файл `.env` (dtek/bot/.env или cek/bot/.env):

```bash
# Кэширование
GROUP_CACHE_TTL_MINUTES=15         # Время жизни кэша (минуты)

# Автоматические проверки
CHECKER_LOOP_INTERVAL_SECONDS=300  # Интервал проверок (секунды)
DEFAULT_INTERVAL_HOURS=1.0         # Интервал подписок по умолчанию (часы)
```

### Значения по умолчанию

Если переменные окружения не заданы, используются следующие значения:

| Константа | Значение по умолчанию | Описание |
|-----------|----------------------|----------|
| `GROUP_CACHE_TTL_MINUTES` | 15 | Время жизни кэша группы |
| `CHECKER_LOOP_INTERVAL_SECONDS` | 300 (5 мин) | Интервал проверки подписок |
| `DEFAULT_INTERVAL_HOURS` | 1.0 | Интервал подписок по умолчанию |

### Примеры кастомизации

**Агрессивное кэширование (экономия парсера):**
```bash
GROUP_CACHE_TTL_MINUTES=30          # Кэш живёт 30 минут
CHECKER_LOOP_INTERVAL_SECONDS=600   # Проверка каждые 10 минут
```

**Минимальная латентность (свежие данные):**
```bash
GROUP_CACHE_TTL_MINUTES=5           # Кэш живёт 5 минут
CHECKER_LOOP_INTERVAL_SECONDS=60    # Проверка каждую минуту
```

**Низкая нагрузка (редкие обновления):**
```bash
DEFAULT_INTERVAL_HOURS=3.0          # Подписки проверяются каждые 3 часа
CHECKER_LOOP_INTERVAL_SECONDS=900   # Проверка каждые 15 минут
```

---

## 10. Частые вопросы (FAQ)

### Q: Использует ли `/check` кэш?
**A:** ✅ **ДА!** (с версии feature/manual-check-cache-optimization). Команда `/check` теперь использует кэш групп:
- Первая проверка адреса → вызов парсера (5-15 сек)
- Повторная проверка в течение 15 минут → мгновенный ответ из кэша (<1 сек)
- Подробности см. в разделе 8 "Оптимизация ручных проверок"

### Q: Что произойдёт, если расписание группы изменится?
**A:** В течение 15 минут (TTL кэша) пользователи будут видеть старое расписание из кэша. Затем кэш автоматически обновится при следующей проверке or подписке.

### Q: Могут ли два адреса одной группы иметь разные расписания?
**A:** Нет, это противоречит логике работы ДТЕК/ЦЕК. Если такое произойдёт, это баг в парсере или на сайте провайдера.

### Q: Как часто обновляется кэш для активных подписок?
**A:** Зависит от интервала подписки (по умолчанию 1 час). Но кэш имеет собственное время жизни 15 минут.

### Q: Что происходит после перезапуска бота?
**A:** 
- `group_cache` (БД): сохраняется ✅
- In-memory кэши: сбрасываются ❌
- Первые проверки после рестарта используют кэш из БД (если не устарел)

---

## 11. См. также

### Связанная документация
- [group_caching_architecture.md](./group_caching_architecture.md) - детальная архитектура группового кэширования
- [group_caching_implementation_complete.md](./group_caching_implementation_complete.md) - полная имплементация группового кэша
- [CURRENT_OUTAGE_TESTING.md](./CURRENT_OUTAGE_TESTING.md) - тестирование аварийных отключений
- [DATA_SOURCES.md](./DATA_SOURCES.md) - архитектура источников данных

### Файлы кода
- [common/tasks.py](../common/tasks.py) - реализация фоновых задач
- [common/bot_base.py](../common/bot_base.py) - функции работы с кэшем

---

**Версия документа:** 1.0  
**Дата создания:** 2025-12-12  
**Автор:** DTEK/CEK Bot Development Team
