#!/bin/bash

#############################################
# Deployment Script for Multi-Bot Architecture
# Usage: bash deploy.sh [dtek|cek|all]
#############################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BOT=${1:-all}
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_DIR="/var/log/shutdowns-deployments"
LOG_FILE="${LOG_DIR}/deploy-${TIMESTAMP}.log"

# Create log directory if it doesn't exist
mkdir -p "$LOG_DIR"

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║    Multi-Bot Deployment Script         ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

# Logging function
log() {
    echo -e "$1" | tee -a "$LOG_FILE"
}

# Backup database function
backup_db() {
    local bot_name=$1
    local db_path=$2
    
    if [ -f "$db_path" ]; then
        log "${YELLOW}💾 Backing up ${bot_name} database...${NC}"
        cp "$db_path" "${db_path}.backup-${TIMESTAMP}"
        log "${GREEN}✓ Database backed up${NC}"
    else
        log "${YELLOW}⚠ No database found at ${db_path}${NC}"
    fi
}

# Deploy bot function
deploy_bot() {
    local bot_name=$1
    local bot_dir=$2
    
    log "\n${BLUE}═══════════════════════════════════════${NC}"
    log "${BLUE}📦 Deploying ${bot_name} Bot...${NC}"
    log "${BLUE}═══════════════════════════════════════${NC}\n"
    
    # Navigate to bot directory
    cd "$bot_dir" || {
        log "${RED}✗ Failed to navigate to ${bot_dir}${NC}"
        return 1
    }
    
    # Check if .env exists
    if [ ! -f ".env" ]; then
        log "${RED}✗ .env file not found in ${bot_dir}${NC}"
        log "${YELLOW}  Please create .env from .env.example${NC}"
        return 1
    fi
    
    # Stop existing containers
    log "${BLUE}🛑 Stopping existing containers...${NC}"
    docker-compose down || true
    
    # Pull latest code (from parent directory)
    cd ../..
    log "${BLUE}📥 Pulling latest code...${NC}"
    git pull origin main || {
        log "${YELLOW}⚠ Git pull failed, continuing with local code${NC}"
    }
    
    # Return to bot directory
    cd "$bot_dir"
    
    # Build new images
    log "${BLUE}🔨 Building Docker images...${NC}"
    docker-compose build --no-cache
    
    # Start containers
    log "${BLUE}🚀 Starting containers...${NC}"
    docker-compose up -d
    
    # Wait for container to be healthy
    log "${BLUE}⏳ Waiting for container to be ready...${NC}"
    sleep 5
    
    # Check container status
    if docker-compose ps | grep -q "Up"; then
        log "${GREEN}✓ ${bot_name} bot deployed successfully!${NC}"
        
        # Show logs
        log "${BLUE}📋 Recent logs:${NC}"
        docker-compose logs --tail=20
        
        return 0
    else
        log "${RED}✗ ${bot_name} bot failed to start${NC}"
        log "${RED}📋 Error logs:${NC}"
        docker-compose logs --tail=50
        return 1
    fi
}

# Main deployment logic
main() {
    local project_root="/opt/shutdowns"
    
    # Check if we're in the right directory
    if [ ! -d "$project_root" ]; then
        log "${YELLOW}⚠ Project directory not found at ${project_root}${NC}"
        log "${YELLOW}  Using current directory: $(pwd)${NC}"
        project_root=$(pwd)
    fi
    
    cd "$project_root"
    
    case "$BOT" in
        dtek)
            backup_db "DTEK" "${project_root}/dtek/bot/data/dtek_bot.db"
            deploy_bot "DTEK" "${project_root}/dtek/bot"
            ;;
        cek)
            backup_db "CEK" "${project_root}/cek/bot/data/cek_bot.db"
            deploy_bot "CEK" "${project_root}/cek/bot"
            ;;
        all)
            log "${GREEN}Deploying all bots...${NC}\n"
            
            backup_db "DTEK" "${project_root}/dtek/bot/data/dtek_bot.db"
            backup_db "CEK" "${project_root}/cek/bot/data/cek_bot.db"
            
            deploy_bot "DTEK" "${project_root}/dtek/bot"
            DTEK_STATUS=$?
            
            deploy_bot "CEK" "${project_root}/cek/bot"
            CEK_STATUS=$?
            
            # Summary
            log "\n${BLUE}═══════════════════════════════════════${NC}"
            log "${BLUE}📊 Deployment Summary${NC}"
            log "${BLUE}═══════════════════════════════════════${NC}\n"
            
            if [ $DTEK_STATUS -eq 0 ]; then
                log "${GREEN}✓ DTEK bot: SUCCESS${NC}"
            else
                log "${RED}✗ DTEK bot: FAILED${NC}"
            fi
            
            if [ $CEK_STATUS -eq 0 ]; then
                log "${GREEN}✓ CEK bot: SUCCESS${NC}"
            else
                log "${RED}✗ CEK bot: FAILED${NC}"
            fi
            
            if [ $DTEK_STATUS -eq 0 ] && [ $CEK_STATUS -eq 0 ]; then
                log "\n${GREEN}🎉 All bots deployed successfully!${NC}"
                exit 0
            else
                log "\n${RED}⚠ Some deployments failed. Check logs above.${NC}"
                exit 1
            fi
            ;;
        *)
            log "${RED}✗ Invalid bot name: ${BOT}${NC}"
            log "${YELLOW}Usage: bash deploy.sh [dtek|cek|all]${NC}"
            exit 1
            ;;
    esac
    
    log "\n${GREEN}✓ Deployment completed${NC}"
    log "${BLUE}Log file: ${LOG_FILE}${NC}\n"
}

# Run main function
main
