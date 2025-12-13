#!/bin/bash

##############################################
# Safe Deployment Script
# Runs tests before deploying Docker containers
##############################################

set -e  # Exit on any error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Detect $DOCKER_COMPOSE command (new vs old)
if command -v docker &> /dev/null && docker compose version &> /dev/null 2>&1; then
    DOCKER_COMPOSE="docker compose"
elif command -v $DOCKER_COMPOSE &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
else
    echo -e "${RED}❌ Neither 'docker compose' nor 'docker-compose' found!${NC}"
    echo -e "${YELLOW}Please install Docker Compose first.${NC}"
    exit 1
fi

# Parse arguments
BOT_NAME="${1:-all}"  # dtek, cek, or all
SKIP_TESTS="${2:-false}"  # Set to 'true' to skip tests (use with caution!)

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Safe Deployment with Tests        ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}\n"

# Auto-detect environment
if [ "$SKIP_TESTS" = "false" ]; then
    # Check if pytest is available
    if ! command -v pytest &> /dev/null && ! python3 -m pytest --version &> /dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  pytest not found on this system${NC}"
        echo -e "${YELLOW}This appears to be a production server without dev dependencies.${NC}\n"
        echo -e "${BLUE}Assuming tests were run locally/CI before deployment.${NC}"
        echo -e "${BLUE}Proceeding with deployment...${NC}\n"
        SKIP_TESTS="auto"
    fi
fi

# Step 1: Run tests (if available)
if [ "$SKIP_TESTS" = "false" ]; then
    echo -e "${YELLOW}📋 Step 1: Running test suite...${NC}\n"
    
    if ! ./run_tests.sh all all; then
        echo -e "\n${RED}╔════════════════════════════════════════╗${NC}"
        echo -e "${RED}║  ❌ TESTS FAILED - DEPLOYMENT ABORTED  ║${NC}"
        echo -e "${RED}╚════════════════════════════════════════╝${NC}\n"
        echo -e "${RED}Fix failing tests before deploying!${NC}\n"
        exit 1
    fi
    
    echo -e "\n${GREEN}✅ All tests passed!${NC}\n"
else
    echo -e "${YELLOW}⚠️  SKIPPING TESTS (not recommended for production)${NC}\n"
fi

# Step 2: Deploy
echo -e "${YELLOW}🚀 Step 2: Deploying containers...${NC}\n"

deploy_bot() {
    local bot=$1
    local compose_file="${bot}/bot/docker-compose.yml"
    
    if [ ! -f "$compose_file" ]; then
        echo -e "${RED}❌ Compose file not found: $compose_file${NC}"
        return 1
    fi
    
    echo -e "${BLUE}Deploying ${bot^^} bot...${NC}"
    
    # Stop old container
    $DOCKER_COMPOSE -f "$compose_file" down
    
    # Build and start new container
    if $DOCKER_COMPOSE -f "$compose_file" up --build -d; then
        echo -e "${GREEN}✅ ${bot^^} bot deployed successfully${NC}\n"
        
        # Show logs for verification
        echo -e "${BLUE}Last 20 lines of logs:${NC}"
        $DOCKER_COMPOSE -f "$compose_file" logs --tail=20
        echo ""
        
        return 0
    else
        echo -e "${RED}❌ ${bot^^} deployment failed${NC}\n"
        return 1
    fi
}

case "$BOT_NAME" in
    dtek)
        deploy_bot "dtek"
        ;;
    cek)
        deploy_bot "cek"
        ;;
    all)
        echo -e "${BLUE}Deploying all bots...${NC}\n"
        
        if deploy_bot "dtek" && deploy_bot "cek"; then
            echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
            echo -e "${GREEN}║   🎉 ALL BOTS DEPLOYED SUCCESSFULLY   ║${NC}"
            echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"
        else
            echo -e "${RED}╔════════════════════════════════════════╗${NC}"
            echo -e "${RED}║      ❌ DEPLOYMENT FAILED              ║${NC}"
            echo -e "${RED}╚════════════════════════════════════════╝${NC}\n"
            exit 1
        fi
        ;;
    *)
        echo -e "${RED}❌ Unknown bot: $BOT_NAME${NC}"
        echo -e "${YELLOW}Usage: $0 [dtek|cek|all] [skip-tests]${NC}"
        echo -e "${YELLOW}Examples:${NC}"
        echo -e "  $0              # Deploy all with tests"
        echo -e "  $0 dtek         # Deploy DTEK only with tests"
        echo -e "  $0 all true     # Deploy all, skip tests (not recommended)"
        exit 1
        ;;
esac

echo -e "${BLUE}Deployment complete!${NC}\n"

# Show running containers
echo -e "${BLUE}Running containers:${NC}"
docker ps --filter "name=dtek_bot" --filter "name=cek_bot" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo ""
