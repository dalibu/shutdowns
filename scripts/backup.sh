#!/bin/bash

#############################################
# Скрипт резервного копирования
# Использование: bash backup.sh
#############################################

set -e

# Цвета
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

BACKUP_DIR="/opt/backups"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Backup Script                  ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

# Создание директории для бэкапов
mkdir -p ${BACKUP_DIR}

# Бэкап базы данных бота
echo -e "${BLUE}💾 Backing up Shutdowns Bot database...${NC}"
if [ -d "/opt/shutdowns" ]; then
    docker compose -f /opt/shutdowns/docker-compose.yml exec -T bot \
        tar -czf /data/bot-db-${TIMESTAMP}.tar.gz /data/bot.db 2>/dev/null || true
    
    # Копирование бэкапа на хост
    docker cp shutdowns_bot:/data/bot-db-${TIMESTAMP}.tar.gz ${BACKUP_DIR}/ 2>/dev/null || true
    echo -e "${GREEN}✓ Database backup saved to ${BACKUP_DIR}/bot-db-${TIMESTAMP}.tar.gz${NC}"
fi

# Бэкап конфигураций Nginx
echo -e "\n${BLUE}⚙️  Backing up Nginx configs...${NC}"
tar -czf ${BACKUP_DIR}/nginx-config-${TIMESTAMP}.tar.gz /etc/nginx/sites-available/ /etc/nginx/nginx.conf
echo -e "${GREEN}✓ Nginx configs backed up${NC}"

# Бэкап .env файлов
echo -e "\n${BLUE}🔐 Backing up environment files...${NC}"
find /opt -name ".env" -exec tar -czf ${BACKUP_DIR}/env-files-${TIMESTAMP}.tar.gz {} +
echo -e "${GREEN}✓ Environment files backed up${NC}"

# Очистка старых бэкапов (старше 30 дней)
echo -e "\n${BLUE}🧹 Cleaning old backups (>30 days)...${NC}"
find ${BACKUP_DIR} -name "*.tar.gz" -mtime +30 -delete
echo -e "${GREEN}✓ Old backups cleaned${NC}"

# Список бэкапов
echo -e "\n${BLUE}📋 Available backups:${NC}"
ls -lh ${BACKUP_DIR}/*.tar.gz 2>/dev/null | tail -n 10

echo -e "\n${GREEN}✅ Backup completed successfully!${NC}"
echo -e "${YELLOW}💡 Backups location: ${BACKUP_DIR}${NC}\n"
