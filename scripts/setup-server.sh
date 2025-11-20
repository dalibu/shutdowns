#!/bin/bash

#############################################
# Автоматическая настройка Contabo VPS
# Использование: bash setup-server.sh
#############################################

set -e  # Остановка при ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Contabo VPS Initial Setup Script    ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

# Проверка, что скрипт запущен от root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}❌ Пожалуйста, запустите скрипт от root (sudo bash setup-server.sh)${NC}"
    exit 1
fi

echo -e "${BLUE}📋 Этот скрипт установит и настроит:${NC}"
echo "  • Docker & Docker Compose"
echo "  • Nginx"
echo "  • Certbot (SSL certificates)"
echo "  • UFW Firewall"
echo "  • Fail2ban"
echo "  • Автоматические обновления безопасности"
echo ""
read -p "Продолжить? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Установка отменена${NC}"
    exit 0
fi

# 1. Обновление системы
echo -e "\n${GREEN}[1/8] Обновление системы...${NC}"
apt update && apt upgrade -y

# 2. Установка базовых утилит
echo -e "\n${GREEN}[2/8] Установка базовых утилит...${NC}"
apt install -y \
    git \
    curl \
    wget \
    vim \
    htop \
    net-tools \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release

# 3. Установка Docker
echo -e "\n${GREEN}[3/8] Установка Docker...${NC}"
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}✓ Docker установлен${NC}"
else
    echo -e "${YELLOW}⚠ Docker уже установлен${NC}"
fi

# Установка Docker Compose
if ! docker compose version &> /dev/null; then
    apt install -y docker-compose-plugin
    echo -e "${GREEN}✓ Docker Compose установлен${NC}"
else
    echo -e "${YELLOW}⚠ Docker Compose уже установлен${NC}"
fi

# 4. Установка Nginx
echo -e "\n${GREEN}[4/8] Установка Nginx...${NC}"
if ! command -v nginx &> /dev/null; then
    apt install -y nginx
    systemctl enable nginx
    systemctl start nginx
    echo -e "${GREEN}✓ Nginx установлен${NC}"
else
    echo -e "${YELLOW}⚠ Nginx уже установлен${NC}"
fi

# 5. Установка Certbot
echo -e "\n${GREEN}[5/8] Установка Certbot для SSL...${NC}"
if ! command -v certbot &> /dev/null; then
    apt install -y certbot python3-certbot-nginx
    echo -e "${GREEN}✓ Certbot установлен${NC}"
else
    echo -e "${YELLOW}⚠ Certbot уже установлен${NC}"
fi

# 6. Настройка UFW Firewall
echo -e "\n${GREEN}[6/8] Настройка UFW Firewall...${NC}"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable
echo -e "${GREEN}✓ Firewall настроен${NC}"

# 7. Установка Fail2ban
echo -e "\n${GREEN}[7/8] Установка Fail2ban...${NC}"
if ! command -v fail2ban-client &> /dev/null; then
    apt install -y fail2ban
    systemctl enable fail2ban
    systemctl start fail2ban
    
    # Базовая конфигурация для SSH
    cat > /etc/fail2ban/jail.local <<EOF
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port = 22
logpath = /var/log/auth.log
EOF
    
    systemctl restart fail2ban
    echo -e "${GREEN}✓ Fail2ban установлен и настроен${NC}"
else
    echo -e "${YELLOW}⚠ Fail2ban уже установлен${NC}"
fi

# 8. Настройка автоматических обновлений безопасности
echo -e "\n${GREEN}[8/8] Настройка автоматических обновлений...${NC}"
apt install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades
echo -e "${GREEN}✓ Автообновления настроены${NC}"

# Создание директорий для проектов
echo -e "\n${GREEN}📁 Создание директорий для проектов...${NC}"
mkdir -p /opt/shutdowns
mkdir -p /opt/personal-site
mkdir -p /opt/webapp1
mkdir -p /opt/webapp2
mkdir -p /var/log/deployments

# Создание пользователя для деплоя (опционально)
echo -e "\n${BLUE}👤 Создать отдельного пользователя для деплоя? (рекомендуется)${NC}"
read -p "Создать пользователя 'deploy'? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if ! id "deploy" &>/dev/null; then
        useradd -m -s /bin/bash deploy
        usermod -aG docker deploy
        echo -e "${GREEN}✓ Пользователь 'deploy' создан${NC}"
        echo -e "${YELLOW}⚠ Установите пароль: passwd deploy${NC}"
    else
        echo -e "${YELLOW}⚠ Пользователь 'deploy' уже существует${NC}"
    fi
fi

# Вывод информации о системе
echo -e "\n${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        Установка завершена! ✓          ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

echo -e "${BLUE}📊 Информация о системе:${NC}"
echo "  • Docker: $(docker --version | cut -d' ' -f3)"
echo "  • Docker Compose: $(docker compose version --short)"
echo "  • Nginx: $(nginx -v 2>&1 | cut -d'/' -f2)"
echo "  • Certbot: $(certbot --version | cut -d' ' -f2)"
echo ""

echo -e "${BLUE}🔥 Firewall правила:${NC}"
ufw status numbered
echo ""

echo -e "${GREEN}✅ Следующие шаги:${NC}"
echo "  1. Клонировать проекты в /opt/"
echo "  2. Настроить Nginx конфигурации"
echo "  3. Получить SSL сертификаты"
echo "  4. Запустить Docker контейнеры"
echo ""
echo -e "${YELLOW}💡 Используйте скрипт deploy.sh для деплоя проектов${NC}"
echo ""
