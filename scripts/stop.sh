#!/bin/bash

##############################################
# Stop Script - Safely stops all bot containers
##############################################

set -e  # Exit on any error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Parse arguments
BOT_NAME="${1:-all}"  # dtek, cek, or all
REMOVE_VOLUMES="${2:-false}"  # Set to 'true' to remove volumes (data)

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Stopping Bot Containers            ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}\n"

stop_bot() {
    local bot=$1
    local compose_file="${bot}/bot/docker-compose.yml"
    
    if [ ! -f "$compose_file" ]; then
        echo -e "${YELLOW}⚠️  Compose file not found: $compose_file${NC}"
        return 0
    fi
    
    echo -e "${BLUE}Stopping ${bot^^} bot...${NC}"
    
    # Check if container is running
    if docker ps --format '{{.Names}}' | grep -q "${bot}_bot"; then
        # Stop and remove containers
        if [ "$REMOVE_VOLUMES" = "true" ]; then
            echo -e "${YELLOW}⚠️  Removing volumes (DATA WILL BE DELETED)${NC}"
            docker-compose -f "$compose_file" down -v
        else
            docker-compose -f "$compose_file" down
        fi
        
        # Verify stopped
        if docker ps --format '{{.Names}}' | grep -q "${bot}_bot"; then
            echo -e "${RED}❌ ${bot^^} bot still running!${NC}\n"
            return 1
        else
            echo -e "${GREEN}✅ ${bot^^} bot stopped${NC}\n"
            return 0
        fi
    else
        echo -e "${YELLOW}ℹ️  ${bot^^} bot is not running${NC}\n"
        return 0
    fi
}

# Stop based on argument
case "$BOT_NAME" in
    dtek)
        stop_bot "dtek"
        ;;
    cek)
        stop_bot "cek"
        ;;
    all)
        echo -e "${BLUE}Stopping all bots...${NC}\n"
        
        if stop_bot "dtek" && stop_bot "cek"; then
            echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
            echo -e "${GREEN}║   🛑 ALL BOTS STOPPED SUCCESSFULLY    ║${NC}"
            echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"
        else
            echo -e "${RED}╔════════════════════════════════════════╗${NC}"
            echo -e "${RED}║      ❌ SOME BOTS FAILED TO STOP       ║${NC}"
            echo -e "${RED}╚════════════════════════════════════════╝${NC}\n"
            exit 1
        fi
        ;;
    *)
        echo -e "${RED}❌ Unknown bot: $BOT_NAME${NC}"
        echo -e "${YELLOW}Usage: $0 [dtek|cek|all] [remove-volumes]${NC}"
        echo -e "${YELLOW}Examples:${NC}"
        echo -e "  $0              # Stop all bots"
        echo -e "  $0 dtek         # Stop DTEK only"
        echo -e "  $0 all true     # Stop all and DELETE data (⚠️  DANGEROUS!)"
        exit 1
        ;;
esac

# Show remaining containers
echo -e "${BLUE}Remaining bot containers:${NC}"
if docker ps -a --filter "name=bot" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | tail -n +2 | grep -q .; then
    docker ps -a --filter "name=bot" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
else
    echo -e "${GREEN}None - all bots stopped${NC}"
fi
echo ""

# Cleanup orphaned networks
echo -e "${BLUE}Cleaning up orphaned networks...${NC}"
docker network prune -f > /dev/null 2>&1 || true
echo -e "${GREEN}✅ Cleanup complete${NC}\n"
