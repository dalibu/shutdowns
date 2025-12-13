# WIP: New version of subscription_checker_task with grouping
# This is a complete rewrite to be integrated into common/tasks.py

Это рефакторинг subscription_checker_task для группировки уведомлений.

## Ключевые изменения:

1. **SQL запрос** группирует по (user_id, group_name)
2. **API вызовы** группируются по group_name  
3. **Уведомления** отправляются раз на группу с списком адресов
4. **Обновление БД** для всех address_ids в группе одновременно

## Следующие шаги для завершения:

### 1. Полностью переписать subscription_checker_task

Функция слишком большая (370 строк) для частичных изменений.
Нужно создать новую версию с нуля и заменить целиком.

Основные секции для изменения:
- Строки 270-296: Query groups вместо individual subscriptions ✅ (готово)
- Строки 298-349: Группировать по group_name вместо address ✅ (частично готово)
- Строки 351-438: Fetch schedule по группам с использованием group cache
- Строки 440-600: Отправка уведомлений с format_address_list и обновление БД

### 2. Аналогично для alert_checker_task

После subscription_checker нужно сделать то же самое для alert_checker_task.

### 3. Обновить импорты

Добавить format_address_list в импорты из formatting:
```python
from .formatting import (
    process_single_day_schedule_compact,
    get_current_status_message,
    format_group_name,
    format_address_list,  # NEW
)
```

### 4. Тестирование

- Создать test subscriptions с несколькими адресами в одной группе
- Проверить что приходит одно уведомление, а не несколько
- Проверить edge cases (NULL group, unknown group, etc)

## Оценка времени

**Осталось:** ~2-3 часа для полной реализации и тестирования
- 1 час: переписать subscription_checker_task
- 30 мин: переписать alert_checker_task  
- 1-1.5 часа: тестирование и debugging

## Альтернативный подход

Вместо рефакторинга существующей функции, можно:
1. Создать новую функцию `subscription_checker_task_v2`
2. Протестировать её параллельно
3. Когда всё работает - переключиться

Это безопаснее для production.
