#!/bin/bash

#############################################
# Test Runner for Multi-Bot Architecture
# Usage: ./run_tests.sh [test_type] [provider]
#############################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     Multi-Bot Test Runner              ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}\n"

# Detect Python command
if [[ -n "$CONDA_DEFAULT_ENV" ]] || [[ -n "$VIRTUAL_ENV" ]]; then
    # Inside conda or venv, use 'python' directly
    PYTHON_CMD="python"
else
    # Not in virtual environment, use python3
    PYTHON_CMD="python3"
fi

echo -e "${CYAN}Using Python: $($PYTHON_CMD --version)${NC}\n"

# Check pytest installation
if ! $PYTHON_CMD -m pytest --version &> /dev/null; then
    echo -e "${RED}✗ pytest not installed!${NC}"
    echo "Install: pip install -r requirements-dev.txt"
    echo "Or run: ./setup_dev.sh"
    exit 1
fi

# Set PYTHONPATH
export PYTHONPATH="."

# Parse arguments
TEST_TYPE="${1:-all}"
PROVIDER="${2:-all}"

# Function to run tests for a specific component
run_tests() {
    local component=$1
    local test_dir=$2
    local test_type=$3
    
    if [ ! -d "$test_dir" ]; then
        echo -e "${YELLOW}⚠ Directory ${test_dir} not found, skipping${NC}"
        return 0
    fi
    
    echo -e "${BLUE}═══════════════════════════════════════${NC}"
    echo -e "${BLUE}📦 Testing: ${component}${NC}"
    echo -e "${BLUE}═══════════════════════════════════════${NC}\n"
    
    case "$test_type" in
        all)
            $PYTHON_CMD -m pytest "$test_dir" -v --tb=short
            ;;
        unit)
            $PYTHON_CMD -m pytest "$test_dir" -m "unit" -v --tb=short
            ;;
        integration)
            $PYTHON_CMD -m pytest "$test_dir" -m "integration" -v --tb=short
            ;;
        quick)
            $PYTHON_CMD -m pytest "$test_dir" -m "not slow" -v --tb=short
            ;;
        coverage)
            local cov_source="${component%%/*}"  # Get first part (common, dtek, cek)
            $PYTHON_CMD -m pytest "$test_dir" \
                --cov="$cov_source" \
                --cov-report=html:"htmlcov/${component}" \
                --cov-report=term-missing \
                -v
            ;;
        failed)
            $PYTHON_CMD -m pytest "$test_dir" --lf -v --tb=short
            ;;
        *)
            echo -e "${RED}✗ Unknown test type: $test_type${NC}"
            return 1
            ;;
    esac
    
    local exit_code=$?
    if [ $exit_code -eq 0 ]; then
        echo -e "\n${GREEN}✓ ${component} tests passed${NC}\n"
    else
        echo -e "\n${RED}✗ ${component} tests failed${NC}\n"
    fi
    
    return $exit_code
}

# Main test execution
main() {
    local failed_components=()
    local passed_components=()
    
    case "$PROVIDER" in
        common)
            run_tests "Common Library" "common/tests" "$TEST_TYPE"
            [ $? -eq 0 ] && passed_components+=("Common") || failed_components+=("Common")
            ;;
        dtek)
            run_tests "DTEK Provider" "dtek/tests" "$TEST_TYPE"
            [ $? -eq 0 ] && passed_components+=("DTEK") || failed_components+=("DTEK")
            ;;
        cek)
            run_tests "CEK Provider" "cek/tests" "$TEST_TYPE"
            [ $? -eq 0 ] && passed_components+=("CEK") || failed_components+=("CEK")
            ;;
        all)
            echo -e "${CYAN}Running tests for all components...${NC}\n"
            
            # Common library tests
            run_tests "Common Library" "common/tests" "$TEST_TYPE"
            [ $? -eq 0 ] && passed_components+=("Common") || failed_components+=("Common")
            
            # DTEK tests
            run_tests "DTEK Provider" "dtek/tests" "$TEST_TYPE"
            [ $? -eq 0 ] && passed_components+=("DTEK") || failed_components+=("DTEK")
            
            # CEK tests
            run_tests "CEK Provider" "cek/tests" "$TEST_TYPE"
            [ $? -eq 0 ] && passed_components+=("CEK") || failed_components+=("CEK")
            ;;
        *)
            echo -e "${RED}✗ Unknown provider: $PROVIDER${NC}"
            echo -e "${YELLOW}Usage: ./run_tests.sh [test_type] [provider]${NC}"
            echo -e "${YELLOW}Test types: all, unit, integration, quick, coverage, failed${NC}"
            echo -e "${YELLOW}Providers: all, common, dtek, cek${NC}"
            exit 1
            ;;
    esac
    
    # Summary
    echo -e "${BLUE}═══════════════════════════════════════${NC}"
    echo -e "${BLUE}📊 Test Summary${NC}"
    echo -e "${BLUE}═══════════════════════════════════════${NC}\n"
    
    if [ ${#passed_components[@]} -gt 0 ]; then
        echo -e "${GREEN}✓ Passed (${#passed_components[@]}):${NC}"
        for component in "${passed_components[@]}"; do
            echo -e "  ${GREEN}✓${NC} $component"
        done
        echo ""
    fi
    
    if [ ${#failed_components[@]} -gt 0 ]; then
        echo -e "${RED}✗ Failed (${#failed_components[@]}):${NC}"
        for component in "${failed_components[@]}"; do
            echo -e "  ${RED}✗${NC} $component"
        done
        echo ""
        exit 1
    fi
    
    echo -e "${GREEN}🎉 All tests passed!${NC}\n"
    
    # Show coverage report location if coverage was run
    if [ "$TEST_TYPE" = "coverage" ]; then
        echo -e "${BLUE}📈 Coverage reports:${NC}"
        echo -e "  ${CYAN}htmlcov/${NC}"
        echo ""
    fi
}

# Help message
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo -e "${CYAN}Usage:${NC}"
    echo -e "  ./run_tests.sh [test_type] [provider]"
    echo ""
    echo -e "${CYAN}Test Types:${NC}"
    echo -e "  ${GREEN}all${NC}         - Run all tests (default)"
    echo -e "  ${GREEN}unit${NC}        - Run only unit tests"
    echo -e "  ${GREEN}integration${NC} - Run only integration tests"
    echo -e "  ${GREEN}quick${NC}       - Run quick tests (exclude slow tests)"
    echo -e "  ${GREEN}coverage${NC}    - Run tests with coverage report"
    echo -e "  ${GREEN}failed${NC}      - Re-run only failed tests from last run"
    echo ""
    echo -e "${CYAN}Providers:${NC}"
    echo -e "  ${GREEN}all${NC}         - Test all components (default)"
    echo -e "  ${GREEN}common${NC}      - Test common library only"
    echo -e "  ${GREEN}dtek${NC}        - Test DTEK provider only"
    echo -e "  ${GREEN}cek${NC}         - Test CEK provider only"
    echo ""
    echo -e "${CYAN}Examples:${NC}"
    echo -e "  ./run_tests.sh                    # Run all tests"
    echo -e "  ./run_tests.sh all dtek           # Run all DTEK tests"
    echo -e "  ./run_tests.sh unit all           # Run unit tests for all"
    echo -e "  ./run_tests.sh coverage common    # Coverage for common library"
    echo -e "  ./run_tests.sh quick cek          # Quick CEK tests"
    echo -e "  ./run_tests.sh failed all         # Re-run failed tests"
    echo ""
    exit 0
fi

# Run main function
main
