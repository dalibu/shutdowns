#!/bin/bash

#############################################
# Скрипт деплоя проектов на сервер
# Использование: bash deploy.sh [project]
#############################################

set -e

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT=${1:-all}
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="/var/log/deployments/deploy-${TIMESTAMP}.log"

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Deployment Script              ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

# Функция логирования
log() {
    echo -e "$1" | tee -a "$LOG_FILE"
}

# Функция деплоя Shutdowns Bot
deploy_shutdowns() {
    log "${BLUE}📦 Deploying Shutdowns Bot...${NC}"
    
    cd /opt/shutdowns
    
    # Бэкап БД перед обновлением
    if [ -f "data/bot.db" ]; then
        log "${YELLOW}💾 Backing up database...${NC}"
        cp data/bot.db data/bot.db.backup-${TIMESTAMP}
    fi
    
    # Обновление кода
    log "${BLUE}📥 Pulling latest code...${NC}"
    git pull origin shutdowns-common
    
    # Пересборка и перезапуск контейнеров
    log "${BLUE}🔨 Rebuilding containers...${NC}"
    docker compose down
    docker compose build --no-cache
    docker compose up -d
    
    # Проверка статуса
    sleep 5
    if docker compose ps | grep -q "Up"; then
        log "${GREEN}✅ Shutdowns Bot deployed successfully${NC}"
    else
        log "${RED}❌ Deployment failed! Check logs: docker compose logs${NC}"
        exit 1
    fi
}

# Функция деплоя Personal Site
deploy_personal_site() {
    log "${BLUE}📦 Deploying Personal Site...${NC}"
    
    cd /opt/personal-site
    
    # Обновление кода
    log "${BLUE}📥 Pulling latest code...${NC}"
    git pull origin main
    
    # Если есть build процесс (например, для React/Vue)
    if [ -f "package.json" ]; then
        log "${BLUE}🔨 Building site...${NC}"
        npm install
        npm run build
    fi
    
    # Перезагрузка Nginx
    log "${BLUE}🔄 Reloading Nginx...${NC}"
    nginx -t && systemctl reload nginx
    
    log "${GREEN}✅ Personal Site deployed successfully${NC}"
}

# Функция деплоя Web App
deploy_webapp() {
    local app_name=$1
    log "${BLUE}📦 Deploying ${app_name}...${NC}"
    
    cd /opt/${app_name}
    
    # Обновление кода
    log "${BLUE}📥 Pulling latest code...${NC}"
    git pull origin main
    
    # Пересборка контейнеров
    if [ -f "docker-compose.yml" ]; then
        log "${BLUE}🔨 Rebuilding containers...${NC}"
        docker compose down
        docker compose build --no-cache
        docker compose up -d
        
        sleep 5
        if docker compose ps | grep -q "Up"; then
            log "${GREEN}✅ ${app_name} deployed successfully${NC}"
        else
            log "${RED}❌ Deployment failed! Check logs${NC}"
            exit 1
        fi
    fi
}

# Основная логика
case "$PROJECT" in
    shutdowns)
        deploy_shutdowns
        ;;
    personal-site)
        deploy_personal_site
        ;;
    webapp1)
        deploy_webapp "webapp1"
        ;;
    webapp2)
        deploy_webapp "webapp2"
        ;;
    all)
        log "${GREEN}🚀 Deploying all projects...${NC}\n"
        deploy_shutdowns
        echo ""
        deploy_personal_site
        echo ""
        deploy_webapp "webapp1"
        echo ""
        deploy_webapp "webapp2"
        log "\n${GREEN}✅ All projects deployed!${NC}"
        ;;
    *)
        echo -e "${RED}❌ Unknown project: $PROJECT${NC}"
        echo "Usage: bash deploy.sh [shutdowns|personal-site|webapp1|webapp2|all]"
        exit 1
        ;;
esac

# Очистка старых Docker образов
log "\n${BLUE}🧹 Cleaning up old Docker images...${NC}"
docker system prune -f

log "\n${GREEN}╔════════════════════════════════════════╗${NC}"
log "${GREEN}║      Deployment Complete! ✓            ║${NC}"
log "${GREEN}╚════════════════════════════════════════╝${NC}"
log "\n${YELLOW}📝 Log saved to: ${LOG_FILE}${NC}\n"
