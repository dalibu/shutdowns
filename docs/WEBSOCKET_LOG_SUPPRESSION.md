# Подавление шумных websocket логов

## Проблема
При каждом парсинге страницы ДТЕК появлялось сообщение:
```
dtek_bot  | ERROR:websocket:Connection to remote host was lost. - goodbye
```

Это нормальное поведение - браузер закрывается после завершения парсинга, и websocket соединение разрывается. Но сообщение выглядит как ошибка и пугает.

## Решение
Установили уровень логирования для библиотеки `websocket` в `CRITICAL`:

```python
# В common/logging_config.py
logging.getLogger('websocket').setLevel(logging.CRITICAL)
```

## Результат

### До:
```
dtek_bot  | 2025-12-11 18:33:56 EET | INFO:dtek.parser.dtek_parser:Обнаружено текущее отключение
dtek_bot  | 2025-12-11 18:33:57 EET | INFO:dtek.parser.dtek_parser:Дата 11.12.25: найдено 14 слотів
dtek_bot  | ERROR:websocket:Connection to remote host was lost. - goodbye
dtek_bot  | 2025-12-11 18:34:16 EET | user_623271476 | INFO:__main__:Deleted address
```

### После:
```
dtek_bot  | 2025-12-11 18:33:56 EET | INFO:dtek.parser.dtek_parser:Обнаружено текущее отключение
dtek_bot  | 2025-12-11 18:33:57 EET | INFO:dtek.parser.dtek_parser:Дата 11.12.25: найдено 14 слотів
dtek_bot  | 2025-12-11 18:34:16 EET | user_623271476 | INFO:__main__:Deleted address
```

Теперь логи чистые! ✨

## Что изменилось
- Файл: `common/logging_config.py`
- Добавлена одна строка для подавления websocket ERROR логов
- Критические ошибки websocket (если они будут) всё равно будут отображаться
- Обычные disconnect сообщения больше не показываются

## Примечание
Это безопасное изменение, т.к.:
- Мы подавляем только уровень ERROR и ниже
- Уровень CRITICAL всё равно будет показан
- Websocket используется только внутри Selenium/Botasaurus для связи с браузером
- Настоящие критические ошибки websocket крайне редки в этом контексте
