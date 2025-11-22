#!/bin/bash

#############################################
# Monitoring Script for Multi-Bot Architecture
# Usage: bash monitor.sh [dtek|cek|all]
#############################################

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BOT=${1:-all}

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     Multi-Bot Monitoring Script        ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

# Monitor function
monitor_bot() {
    local bot_name=$1
    local container_name="${bot_name,,}_bot"
    
    echo -e "${BLUE}═══════════════════════════════════════${NC}"
    echo -e "${BLUE}📊 ${bot_name} Bot Status${NC}"
    echo -e "${BLUE}═══════════════════════════════════════${NC}\n"
    
    # Check if container is running
    if docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo -e "${GREEN}✓ Container: Running${NC}"
        
        # Get container uptime
        uptime=$(docker inspect --format='{{.State.StartedAt}}' "$container_name" 2>/dev/null)
        echo -e "${BLUE}⏱  Started: ${uptime}${NC}"
        
        # Get container stats
        echo -e "\n${BLUE}📈 Resource Usage:${NC}"
        docker stats "$container_name" --no-stream --format "table {{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
        
        # Check recent logs for errors
        echo -e "\n${BLUE}📋 Recent Logs (last 10 lines):${NC}"
        docker logs "$container_name" --tail=10
        
        # Count errors in last 100 lines
        error_count=$(docker logs "$container_name" --tail=100 2>&1 | grep -i "error" | wc -l)
        if [ "$error_count" -gt 0 ]; then
            echo -e "\n${YELLOW}⚠ Found ${error_count} errors in recent logs${NC}"
        else
            echo -e "\n${GREEN}✓ No recent errors${NC}"
        fi
        
    else
        echo -e "${RED}✗ Container: Not Running${NC}"
        
        # Check if container exists but stopped
        if docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
            echo -e "${YELLOW}⚠ Container exists but is stopped${NC}"
            echo -e "\n${BLUE}📋 Last logs:${NC}"
            docker logs "$container_name" --tail=20
        else
            echo -e "${RED}✗ Container does not exist${NC}"
        fi
    fi
    
    echo ""
}

# Health check function
health_check() {
    local bot_name=$1
    local container_name="${bot_name,,}_bot"
    
    if docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        # Check if bot is responding (look for recent activity in logs)
        recent_activity=$(docker logs "$container_name" --since=5m 2>&1 | wc -l)
        if [ "$recent_activity" -gt 0 ]; then
            echo -e "${GREEN}✓ ${bot_name} bot is active (${recent_activity} log entries in last 5 min)${NC}"
            return 0
        else
            echo -e "${YELLOW}⚠ ${bot_name} bot is running but inactive${NC}"
            return 1
        fi
    else
        echo -e "${RED}✗ ${bot_name} bot is not running${NC}"
        return 1
    fi
}

# Main monitoring logic
case "$BOT" in
    dtek)
        monitor_bot "DTEK"
        health_check "DTEK"
        ;;
    cek)
        monitor_bot "CEK"
        health_check "CEK"
        ;;
    all)
        monitor_bot "DTEK"
        monitor_bot "CEK"
        
        echo -e "${BLUE}═══════════════════════════════════════${NC}"
        echo -e "${BLUE}🏥 Health Summary${NC}"
        echo -e "${BLUE}═══════════════════════════════════════${NC}\n"
        
        health_check "DTEK"
        DTEK_HEALTH=$?
        
        health_check "CEK"
        CEK_HEALTH=$?
        
        echo ""
        if [ $DTEK_HEALTH -eq 0 ] && [ $CEK_HEALTH -eq 0 ]; then
            echo -e "${GREEN}🎉 All bots are healthy!${NC}\n"
            exit 0
        else
            echo -e "${YELLOW}⚠ Some bots need attention${NC}\n"
            exit 1
        fi
        ;;
    *)
        echo -e "${RED}✗ Invalid bot name: ${BOT}${NC}"
        echo -e "${YELLOW}Usage: bash monitor.sh [dtek|cek|all]${NC}"
        exit 1
        ;;
esac
