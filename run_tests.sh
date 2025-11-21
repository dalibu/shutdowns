#!/bin/bash

# Централизованный скрипт для запуска всех тестов проекта
# Использование: ./run_tests.sh [опции] [provider]

set -e  # Остановка при ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}🧪 Централизованный запуск тестов Shutdowns Service${NC}\n"

# Проверка установки pytest
if ! python3 -m pytest --version &> /dev/null; then
    echo -e "${RED}❌ pytest не установлен!${NC}"
    echo "Установите: pip install pytest pytest-asyncio pytest-mock pytest-cov aioresponses"
    exit 1
fi

# Установка PYTHONPATH
export PYTHONPATH="."

# Определяем провайдера (по умолчанию все)
PROVIDER="${2:-all}"
TEST_TYPE="${1:-all}"

# Функция для запуска тестов конкретного провайдера
run_provider_tests() {
    local provider=$1
    local test_type=$2
    local test_dir="${provider}/tests"
    
    if [ ! -d "$test_dir" ]; then
        echo -e "${YELLOW}⚠️  Директория ${test_dir} не найдена, пропускаем${NC}"
        return 0
    fi
    
    echo -e "${BLUE}📦 Запуск тестов для ${provider}${NC}"
    
    case "$test_type" in
        all)
            python3 -m pytest "$test_dir" -v
            ;;
        unit)
            python3 -m pytest "$test_dir" -m unit -v
            ;;
        api)
            python3 -m pytest "$test_dir" -m api -v
            ;;
        integration)
            python3 -m pytest "$test_dir" -m integration -v
            ;;
        coverage)
            python3 -m pytest "$test_dir" --cov="${provider}" --cov-report=html:"htmlcov/${provider}" --cov-report=term-missing
            ;;
        quick)
            python3 -m pytest "$test_dir" -m "not slow" -v
            ;;
        *)
            python3 -m pytest "$test_dir" -v
            ;;
    esac
}

# Обработка аргументов
case "$TEST_TYPE" in
    all|unit|api|integration|quick)
        if [ "$PROVIDER" = "all" ]; then
            echo -e "${GREEN}📋 Запуск тестов для всех провайдеров${NC}\n"
            run_provider_tests "dtek" "$TEST_TYPE"
            echo ""
            run_provider_tests "cek" "$TEST_TYPE"
        elif [ "$PROVIDER" = "dtek" ] || [ "$PROVIDER" = "cek" ]; then
            run_provider_tests "$PROVIDER" "$TEST_TYPE"
        else
            echo -e "${RED}❌ Неизвестный провайдер: $PROVIDER${NC}"
            echo "Доступные провайдеры: dtek, cek, all"
            exit 1
        fi
        ;;
    coverage|cov)
        echo -e "${GREEN}📊 Запуск с покрытием кода${NC}\n"
        if [ "$PROVIDER" = "all" ]; then
            run_provider_tests "dtek" "coverage"
            echo ""
            run_provider_tests "cek" "coverage"
            echo -e "\n${GREEN}✅ HTML отчеты созданы в htmlcov/dtek и htmlcov/cek${NC}"
        else
            run_provider_tests "$PROVIDER" "coverage"
            echo -e "\n${GREEN}✅ HTML отчет создан в htmlcov/${PROVIDER}${NC}"
        fi
        ;;
    failed)
        echo -e "${GREEN}🔄 Повторный запуск упавших тестов${NC}\n"
        if [ "$PROVIDER" = "all" ]; then
            python3 -m pytest dtek/tests cek/tests --lf -v
        else
            python3 -m pytest "${PROVIDER}/tests" --lf -v
        fi
        ;;
    debug)
        echo -e "${GREEN}🐛 Запуск с отладчиком${NC}\n"
        if [ "$PROVIDER" = "all" ]; then
            python3 -m pytest dtek/tests cek/tests --pdb -v
        else
            python3 -m pytest "${PROVIDER}/tests" --pdb -v
        fi
        ;;
    verbose)
        echo -e "${GREEN}📢 Подробный вывод${NC}\n"
        if [ "$PROVIDER" = "all" ]; then
            python3 -m pytest dtek/tests cek/tests -vv -s
        else
            python3 -m pytest "${PROVIDER}/tests" -vv -s
        fi
        ;;
    help|--help|-h)
        echo "Использование: ./run_tests.sh [команда] [провайдер]"
        echo ""
        echo "Команды:"
        echo "  all         - Запустить все тесты (по умолчанию)"
        echo "  unit        - Только unit тесты"
        echo "  api         - Только API тесты"
        echo "  integration - Только интеграционные тесты"
        echo "  coverage    - Запуск с отчетом о покрытии"
        echo "  quick       - Быстрый запуск (без медленных)"
        echo "  failed      - Повторить только упавшие тесты"
        echo "  debug       - Запуск с отладчиком (--pdb)"
        echo "  verbose     - Подробный вывод"
        echo "  help        - Показать эту справку"
        echo ""
        echo "Провайдеры:"
        echo "  all         - Все провайдеры (по умолчанию)"
        echo "  dtek        - Только DTEK"
        echo "  cek         - Только CEK"
        echo ""
        echo "Примеры:"
        echo "  ./run_tests.sh                    # Все тесты всех провайдеров"
        echo "  ./run_tests.sh unit dtek          # Unit тесты DTEK"
        echo "  ./run_tests.sh coverage all       # Покрытие всех провайдеров"
        echo "  ./run_tests.sh quick cek          # Быстрые тесты CEK"
        exit 0
        ;;
    *)
        echo -e "${RED}❌ Неизвестная команда: $TEST_TYPE${NC}"
        echo "Используйте './run_tests.sh help' для справки"
        exit 1
        ;;
esac

# Код выхода pytest
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo -e "\n${GREEN}✅ Все тесты прошли успешно!${NC}"
else
    echo -e "\n${RED}❌ Некоторые тесты упали (код: $EXIT_CODE)${NC}"
    echo -e "${YELLOW}💡 Попробуйте: ./run_tests.sh debug${NC}"
fi

exit $EXIT_CODE
