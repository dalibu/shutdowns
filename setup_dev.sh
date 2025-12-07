#!/bin/bash

#############################################
# Development Environment Setup Script
# Usage: ./setup_dev.sh
#############################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Shutdowns Project Setup              ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

# Check Python version
REQUIRED_PYTHON_VERSION="3.13"
RECOMMENDED_PYTHON_VERSION="3.14.1"

if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    PYTHON_MAJOR_MINOR=$(echo $PYTHON_VERSION | cut -d'.' -f1,2)
    
    echo -e "${BLUE}Found Python:${NC} $PYTHON_VERSION"
    
    # Check if version is compatible (3.13 or 3.14)
    if [[ "$PYTHON_MAJOR_MINOR" != "3.13" && "$PYTHON_MAJOR_MINOR" != "3.14" ]]; then
        echo -e "${YELLOW}⚠ Warning: Python $PYTHON_VERSION detected${NC}"
        echo -e "${YELLOW}  Recommended: Python $RECOMMENDED_PYTHON_VERSION${NC}"
        echo -e "${YELLOW}  This project requires Python >= 3.13 and < 3.15${NC}\n"
    fi
else
    echo -e "${RED}✗ Python 3 not found!${NC}"
    exit 1
fi

# Detect if we're in a conda environment
if [[ -n "$CONDA_DEFAULT_ENV" ]]; then
    echo -e "${GREEN}✓ Conda environment detected:${NC} $CONDA_DEFAULT_ENV\n"
    PYTHON_CMD="python"
    PIP_CMD="pip"
elif [[ -n "$VIRTUAL_ENV" ]]; then
    echo -e "${GREEN}✓ Virtual environment detected:${NC} $VIRTUAL_ENV\n"
    PYTHON_CMD="python"
    PIP_CMD="pip"
else
    echo -e "${YELLOW}⚠ No virtual environment detected${NC}"
    echo -e "${YELLOW}  It's recommended to use conda or venv${NC}"
    echo -e "${YELLOW}  Continue anyway? (y/N)${NC}"
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        echo -e "\n${BLUE}To create a conda environment:${NC}"
        echo "  conda create -n shutdowns python=3.14"
        echo "  conda activate shutdowns"
        echo ""
        echo -e "${BLUE}Or use venv:${NC}"
        echo "  python3 -m venv venv"
        echo "  source venv/bin/activate"
        exit 0
    fi
    PYTHON_CMD="python3"
    PIP_CMD="pip3"
fi

# Install dependencies
echo -e "${BLUE}════════════════════════════════════════${NC}"
echo -e "${BLUE}Installing dependencies...${NC}"
echo -e "${BLUE}════════════════════════════════════════${NC}\n"

if [ -f "requirements-dev.txt" ]; then
    echo -e "${GREEN}Installing from requirements-dev.txt...${NC}"
    $PIP_CMD install -r requirements-dev.txt
    echo -e "${GREEN}✓ Dependencies installed${NC}\n"
else
    echo -e "${RED}✗ requirements-dev.txt not found!${NC}"
    exit 1
fi

# Verify installation
echo -e "${BLUE}════════════════════════════════════════${NC}"
echo -e "${BLUE}Verifying installation...${NC}"
echo -e "${BLUE}════════════════════════════════════════${NC}\n"

# Check for key packages
PACKAGES=("pytest" "aiogram" "botasaurus")
ALL_OK=true

for pkg in "${PACKAGES[@]}"; do
    if $PIP_CMD show "$pkg" &> /dev/null; then
        VERSION=$($PIP_CMD show "$pkg" | grep Version | cut -d' ' -f2)
        echo -e "${GREEN}✓${NC} $pkg ($VERSION)"
    else
        echo -e "${RED}✗${NC} $pkg not found"
        ALL_OK=false
    fi
done

echo ""

if [ "$ALL_OK" = true ]; then
    echo -e "${GREEN}════════════════════════════════════════${NC}"
    echo -e "${GREEN}✓ Setup completed successfully!${NC}"
    echo -e "${GREEN}════════════════════════════════════════${NC}\n"
    echo -e "${BLUE}Next steps:${NC}"
    echo "  1. Run tests: ./run_tests.sh"
    echo "  2. Check README.md for more information"
    echo ""
else
    echo -e "${RED}════════════════════════════════════════${NC}"
    echo -e "${RED}✗ Setup completed with errors${NC}"
    echo -e "${RED}════════════════════════════════════════${NC}\n"
    exit 1
fi
