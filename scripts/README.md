# Deployment Scripts

Набор скриптов для управления DTEK и CEK ботами в продакшн окружении.

## Скрипты

### 1. deploy.sh - Деплой ботов

Автоматический деплой одного или всех ботов.

**Использование:**
```bash
# Деплой DTEK бота
bash scripts/deploy.sh dtek

# Деплой CEK бота
bash scripts/deploy.sh cek

# Деплой всех ботов
bash scripts/deploy.sh all
```

**Что делает:**
- Создает бэкап базы данных
- Останавливает текущие контейнеры
- Обновляет код из git
- Пересобирает Docker образы
- Запускает новые контейнеры
- Проверяет статус и показывает логи

**Логи:** `/var/log/shutdowns-deployments/deploy-YYYYMMDD-HHMMSS.log`

### 2. backup.sh - Резервное копирование

Создает бэкапы баз данных и конфигураций.

**Использование:**
```bash
# Бэкап DTEK бота
bash scripts/backup.sh dtek

# Бэкап CEK бота
bash scripts/backup.sh cek

# Бэкап всех ботов
bash scripts/backup.sh all
```

**Что делает:**
- Экспортирует базу данных из контейнера
- Копирует .env файлы
- Создает tar.gz архив
- Удаляет бэкапы старше 7 дней

**Бэкапы:** `/opt/backups/shutdowns/`

### 3. monitor.sh - Мониторинг

Проверяет статус и здоровье ботов.

**Использование:**
```bash
# Мониторинг DTEK бота
bash scripts/monitor.sh dtek

# Мониторинг CEK бота
bash scripts/monitor.sh cek

# Мониторинг всех ботов
bash scripts/monitor.sh all
```

**Что показывает:**
- Статус контейнера (запущен/остановлен)
- Время работы (uptime)
- Использование ресурсов (CPU, RAM, Network)
- Последние логи
- Количество ошибок
- Health check (активность за последние 5 минут)

## Установка

1. Сделать скрипты исполняемыми:
```bash
chmod +x scripts/*.sh
```

2. Создать необходимые директории:
```bash
sudo mkdir -p /var/log/shutdowns-deployments
sudo mkdir -p /opt/backups/shutdowns
sudo chown -R $USER:$USER /var/log/shutdowns-deployments /opt/backups/shutdowns
```

## Автоматизация

### Cron задачи

Добавить в crontab (`crontab -e`):

```bash
# Ежедневный бэкап в 3:00
0 3 * * * /opt/shutdowns/scripts/backup.sh all >> /var/log/shutdowns-deployments/backup.log 2>&1

# Мониторинг каждые 15 минут
*/15 * * * * /opt/shutdowns/scripts/monitor.sh all >> /var/log/shutdowns-deployments/monitor.log 2>&1
```

### Systemd таймеры

Альтернатива cron - systemd таймеры (более современный подход).

## Требования

- Docker и Docker Compose
- Git
- Bash 4.0+
- Права на запись в `/var/log/shutdowns-deployments` и `/opt/backups/shutdowns`

## Примеры использования

### Обычный деплой
```bash
# Обновить только DTEK бота
cd /opt/shutdowns
bash scripts/deploy.sh dtek
```

### Деплой с проверкой
```bash
# Деплой всех ботов и проверка статуса
bash scripts/deploy.sh all && bash scripts/monitor.sh all
```

### Бэкап перед обновлением
```bash
# Создать бэкап перед деплоем
bash scripts/backup.sh all
bash scripts/deploy.sh all
```

### Проверка здоровья
```bash
# Быстрая проверка всех ботов
bash scripts/monitor.sh all
```

## Troubleshooting

### Бот не запускается после деплоя
```bash
# Проверить логи
docker-compose -f dtek/bot/docker-compose.yml logs --tail=100

# Проверить .env файл
cat dtek/bot/.env
```

### Восстановление из бэкапа
```bash
# Найти нужный бэкап
ls -lh /opt/backups/shutdowns/dtek/

# Распаковать
cd /opt/backups/shutdowns
tar -xzf dtek-backup-20231122-120000.tar.gz

# Восстановить базу данных
docker cp dtek/20231122-120000/dtek_bot.db dtek_bot:/data/dtek_bot.db
docker-compose -f /opt/shutdowns/dtek/bot/docker-compose.yml restart
```

### Очистка старых логов
```bash
# Удалить логи старше 30 дней
find /var/log/shutdowns-deployments -name "*.log" -mtime +30 -delete
```

## Безопасность

- Скрипты требуют доступа к Docker (пользователь должен быть в группе `docker`)
- Бэкапы содержат чувствительные данные (.env файлы)
- Рекомендуется настроить права доступа: `chmod 700 scripts/*.sh`
- Логи могут содержать токены - защитите директорию логов

## Поддержка

При проблемах проверьте:
1. Логи деплоя: `/var/log/shutdowns-deployments/`
2. Логи контейнеров: `docker-compose logs`
3. Статус контейнеров: `docker ps -a`
4. Наличие .env файлов в директориях ботов
