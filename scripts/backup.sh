#!/bin/bash

#############################################
# Backup Script for Multi-Bot Architecture
# Usage: bash backup.sh [dtek|cek|all]
#############################################

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BOT=${1:-all}
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="/opt/backups/shutdowns"

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║      Multi-Bot Backup Script           ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Backup function
backup_bot() {
    local bot_name=$1
    local bot_dir=$2
    local db_name=$3
    
    echo -e "${BLUE}💾 Backing up ${bot_name} bot...${NC}"
    
    # Create bot-specific backup directory
    local bot_backup_dir="${BACKUP_DIR}/${bot_name,,}/${TIMESTAMP}"
    mkdir -p "$bot_backup_dir"
    
    # Backup database
    if docker cp "${bot_name,,}_bot:/data/${db_name}" "${bot_backup_dir}/${db_name}" 2>/dev/null; then
        echo -e "${GREEN}✓ Database backed up: ${bot_backup_dir}/${db_name}${NC}"
    else
        echo -e "${YELLOW}⚠ No database found in container${NC}"
    fi
    
    # Backup .env file
    if [ -f "${bot_dir}/.env" ]; then
        cp "${bot_dir}/.env" "${bot_backup_dir}/.env"
        echo -e "${GREEN}✓ .env file backed up${NC}"
    fi
    
    # Create archive
    cd "$BACKUP_DIR"
    tar -czf "${bot_name,,}-backup-${TIMESTAMP}.tar.gz" "${bot_name,,}/${TIMESTAMP}"
    echo -e "${GREEN}✓ Archive created: ${bot_name,,}-backup-${TIMESTAMP}.tar.gz${NC}"
    
    # Cleanup old backups (keep last 7 days)
    find "${BACKUP_DIR}" -name "${bot_name,,}-backup-*.tar.gz" -mtime +7 -delete
    echo -e "${GREEN}✓ Old backups cleaned up${NC}\n"
}

# Main backup logic
case "$BOT" in
    dtek)
        backup_bot "DTEK" "/opt/shutdowns/dtek/bot" "dtek_bot.db"
        ;;
    cek)
        backup_bot "CEK" "/opt/shutdowns/cek/bot" "cek_bot.db"
        ;;
    all)
        backup_bot "DTEK" "/opt/shutdowns/dtek/bot" "dtek_bot.db"
        backup_bot "CEK" "/opt/shutdowns/cek/bot" "cek_bot.db"
        ;;
    *)
        echo -e "${RED}✗ Invalid bot name: ${BOT}${NC}"
        echo -e "${YELLOW}Usage: bash backup.sh [dtek|cek|all]${NC}"
        exit 1
        ;;
esac

echo -e "${GREEN}🎉 Backup completed!${NC}"
echo -e "${BLUE}Backups location: ${BACKUP_DIR}${NC}\n"
