#!/bin/bash

#############################################
# Скрипт мониторинга сервера и сервисов
# Использование: bash monitor.sh
#############################################

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Функция для получения использования CPU
get_cpu_usage() {
    top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{print 100 - $1"%"}'
}

# Функция для получения использования RAM
get_ram_usage() {
    free -m | awk 'NR==2{printf "%.1f%%", $3*100/$2 }'
}

# Функция для получения использования диска
get_disk_usage() {
    df -h / | awk 'NR==2{print $5}'
}

# Функция проверки статуса Docker контейнера
check_container() {
    local container=$1
    if docker ps --format '{{.Names}}' | grep -q "^${container}$"; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${RED}✗ Stopped${NC}"
    fi
}

# Функция проверки статуса сервиса
check_service() {
    local service=$1
    if systemctl is-active --quiet $service; then
        echo -e "${GREEN}✓ Active${NC}"
    else
        echo -e "${RED}✗ Inactive${NC}"
    fi
}

# Очистка экрана
clear

echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║              Server Monitoring Dashboard                   ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}\n"

# Системная информация
echo -e "${BLUE}📊 System Resources:${NC}"
echo -e "  CPU Usage:  $(get_cpu_usage)"
echo -e "  RAM Usage:  $(get_ram_usage)"
echo -e "  Disk Usage: $(get_disk_usage)"
echo -e "  Uptime:     $(uptime -p)"
echo ""

# Docker контейнеры
echo -e "${BLUE}🐳 Docker Containers:${NC}"
echo -e "  Shutdowns API: $(check_container 'shutdowns_api')"
echo -e "  Shutdowns Bot: $(check_container 'shutdowns_bot')"
echo ""

# Системные сервисы
echo -e "${BLUE}⚙️  System Services:${NC}"
echo -e "  Nginx:     $(check_service 'nginx')"
echo -e "  Docker:    $(check_service 'docker')"
echo -e "  Fail2ban:  $(check_service 'fail2ban')"
echo ""

# Nginx статистика
echo -e "${BLUE}🌐 Nginx Status:${NC}"
if systemctl is-active --quiet nginx; then
    NGINX_CONNECTIONS=$(ss -tn | grep :80 | wc -l)
    echo -e "  Active connections: ${NGINX_CONNECTIONS}"
else
    echo -e "  ${RED}Nginx is not running${NC}"
fi
echo ""

# Использование Docker
echo -e "${BLUE}📦 Docker Resources:${NC}"
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" | head -n 5
echo ""

# Последние логи ошибок
echo -e "${BLUE}📝 Recent Errors (last 5):${NC}"
if [ -f "/var/log/syslog" ]; then
    grep -i error /var/log/syslog | tail -n 5 | cut -c 1-80
else
    echo "  No errors found"
fi
echo ""

# Дисковое пространство по директориям
echo -e "${BLUE}💾 Disk Usage by Directory:${NC}"
du -sh /opt/* 2>/dev/null | sort -h | tail -n 5
echo ""

# Firewall статус
echo -e "${BLUE}🔥 Firewall Status:${NC}"
ufw status | head -n 10
echo ""

# SSL сертификаты
echo -e "${BLUE}🔒 SSL Certificates:${NC}"
if command -v certbot &> /dev/null; then
    certbot certificates 2>/dev/null | grep "Domains:\|Expiry Date:" | head -n 6
else
    echo "  Certbot not installed"
fi
echo ""

echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║                    End of Report                           ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}💡 Tip: Run 'watch -n 5 bash monitor.sh' for live monitoring${NC}"
echo ""
