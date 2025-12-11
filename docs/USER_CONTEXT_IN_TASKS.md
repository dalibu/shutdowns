# Дополнение: User Context в Фоновых Задачах

## Проблема
После внедрения context-aware logging, логи из парсера (вызванного в фоновых задачах) не содержали `user_id`, потому что:
- Парсер вызывается из `subscription_checker_task`
- Один адрес может принадлежать нескольким пользователям
- Контекст не устанавливался для каждого user_id

## Решение

### 1. Контекст при вызове парсера
Установили контекст **первого пользователя** из списка подписчиков на адрес:

```python
# В subscription_checker_task при проверке адреса
user_ids_for_address = addresses_to_check_map[address_key]
first_user_id = user_ids_for_address[0] if user_ids_for_address else None

if first_user_id:
    set_user_context(first_user_id)

try:
    # Вызов парсера - теперь логи будут с user_id
    data = await get_shutdowns_data(city, street, house)
finally:
    clear_user_context()
```

### 2. Контекст при обработке уведомлений
Установили контекст для **каждого пользователя** при отправке уведомлений:

```python
# В subscription_checker_task при обработке каждого пользователя
for sub_data in users_to_check:
    user_id = sub_data['user_id']
    set_user_context(user_id)
    
    try:
        # Вся логика отправки уведомлений
        # Теперь все логи будут с конкретным user_id
        logger.info("Notification sent...")
    finally:
        clear_user_context()
```

## Результат

### До:
```
2025-12-11 18:34:46 EET | INFO:dtek.parser.dtek_parser:Обнаружено текущее отключение
2025-12-11 18:34:48 EET | INFO:common.tasks:Hash changed for address...
2025-12-11 18:34:49 EET | INFO:common.tasks:Notification sent...
```

### После:
```
2025-12-11 18:34:46 EET | user_12345 | INFO:dtek.parser.dtek_parser:Обнаружено текущее отключение
2025-12-11 18:34:48 EET | user_12345 | INFO:common.tasks:Hash changed for address...
2025-12-11 18:34:49 EET | user_12345 | INFO:common.tasks:Notification sent...
```

## Важные моменты

1. **Множественные подписчики на один адрес**: Когда несколько пользователей подписаны на один адрес, парсер вызывается один раз, но в логах будет `user_id` первого пользователя из списка. Это нормально, т.к. парсер работает на уровне адреса, а не пользователя.

2. **Индивидуальные уведомления**: Каждое уведомление обрабатывается в своем контексте с правильным `user_id`.

3. **Автоматическая очистка**: Контекст всегда очищается в блоке `finally`, предотвращая утечку `user_id` между разными пользователями.

## Изменённые файлы
- `common/tasks.py` - добавлены `set_user_context()` и `clear_user_context()` в критических местах

## Преимущества
✅ Все логи парсера теперь связаны с конкретным пользователем  
✅ Логи уведомлений показывают для какого пользователя  
✅ Легко отследить весь flow обработки подписки пользователя  
✅ Grep по `user_12345` найдет все логи, включая работу парсера
