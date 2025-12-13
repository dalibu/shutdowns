#!/bin/bash

##############################################
# Restart Script - Stops and starts bot containers
##############################################

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BOT_NAME="${1:-all}"

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║        Restarting Bots                 ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}\n"

# Stop first
echo -e "${YELLOW}Step 1: Stopping bots...${NC}\n"
if ! ./scripts/stop.sh "$BOT_NAME"; then
    echo -e "${RED}Failed to stop bots. Aborting.${NC}\n"
    exit 1
fi

# Wait a moment
echo -e "${BLUE}Waiting 2 seconds...${NC}\n"
sleep 2

# Start again
echo -e "${YELLOW}Step 2: Starting bots...${NC}\n"
if ! ./scripts/deploy.sh "$BOT_NAME"; then
    echo -e "${RED}Failed to start bots.${NC}\n"
    exit 1
fi

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   🔄 RESTART COMPLETED SUCCESSFULLY   ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"
