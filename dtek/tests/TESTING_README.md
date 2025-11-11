# Тестирование DTEK Telegram Bot

## 📁 Структура проекта

Убедитесь, что структура вашего проекта выглядит так:

```
shutdowns/dtek/
├── dtek_telegram_bot.py          # Основной файл бота
├── tests/
│   ├── __init__.py               # Пустой файл (создайте если нет)
│   ├── conftest.py               # Конфигурация pytest
│   └── test_dtek_telegram_bot.py # Тесты
├── requirements.txt
└── pytest.ini (опционально)
```

## 🔧 Установка зависимостей

```bash
# Основные зависимости для тестирования
pip install pytest pytest-asyncio pytest-mock pytest-cov

# Все зависимости проекта
pip install -r requirements.txt
```

## 📝 Создание __init__.py

Создайте пустой файл `tests/__init__.py`:

```bash
touch tests/__init__.py
```

## 🚀 Варианты запуска тестов

### 1. Если dtek_telegram_bot.py в корне проекта

```bash
# Запуск из корня проекта
cd /Users/kovala/Development/python/shutdowns/dtek/

# Запуск всех тестов
pytest tests/

# Подробный вывод
pytest tests/ -v

# С покрытием кода
pytest tests/ --cov=dtek_telegram_bot --cov-report=html
```

### 2. Если нужно указать PYTHONPATH явно

```bash
# Linux/Mac
export PYTHONPATH="${PYTHONPATH}:/Users/kovala/Development/python/shutdowns/dtek"
pytest tests/

# Или в одну строку
PYTHONPATH=/Users/kovala/Development/python/shutdowns/dtek pytest tests/
```

### 3. Запуск конкретных тестов

```bash
# Запуск конкретного класса тестов
pytest tests/test_dtek_telegram_bot.py::TestParseAddressFromText -v

# Запуск конкретного теста
pytest tests/test_dtek_telegram_bot.py::TestParseAddressFromText::test_valid_address -v

# Запуск тестов по маске
pytest tests/ -k "test_format" -v
```

## 📊 Опции pytest

```bash
# Показать print() в тестах
pytest tests/ -s

# Остановиться на первой ошибке
pytest tests/ -x

# Запустить последние упавшие тесты
pytest tests/ --lf

# Параллельный запуск (требует pytest-xdist)
pip install pytest-xdist
pytest tests/ -n auto
```

## 🐛 Решение проблем импорта

### Проблема: ModuleNotFoundError

**Решение 1:** Установите проект в режиме разработки

```bash
# Создайте setup.py в корне проекта
cat > setup.py << EOF
from setuptools import setup, find_packages

setup(
    name="dtek-telegram-bot",
    version="0.1",
    packages=find_packages(),
    py_modules=["dtek_telegram_bot"],
)
EOF

# Установите в режиме разработки
pip install -e .
```

**Решение 2:** Используйте pytest.ini

Создайте файл `pytest.ini` в корне проекта:

```ini
[pytest]
testpaths = tests
pythonpath = .
asyncio_mode = auto
```

**Решение 3:** Добавьте путь в sys.path в тестах (уже сделано)

Код уже содержит:
```python
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
```

## 📈 Coverage отчет

```bash
# Генерация HTML отчета
pytest tests/ --cov=dtek_telegram_bot --cov-report=html

# Открыть отчет
open htmlcov/index.html  # Mac
xdg-open htmlcov/index.html  # Linux

# Отчет в терминале с пропущенными строками
pytest tests/ --cov=dtek_telegram_bot --cov-report=term-missing
```

## 🎯 Примеры использования

### Запуск конкретной категории тестов

```bash
# Только тесты форматирования
pytest tests/ -k "Format" -v

# Только async тесты
pytest tests/ -k "async" -v

# Только тесты API
pytest tests/ -k "Api or Shutdown" -v
```

### Запуск с маркерами (если добавить в тесты)

Добавьте маркеры в `pytest.ini`:
```ini
[pytest]
markers =
    slow: marks tests as slow
    integration: marks tests as integration tests
    unit: marks tests as unit tests
```

Запуск:
```bash
pytest tests/ -m "not slow"  # Пропустить медленные
pytest tests/ -m "unit"       # Только unit тесты
```

## 🔍 Отладка тестов

```bash
# Запуск с отладчиком Python
pytest tests/ --pdb

# Остановиться на первой ошибке и войти в отладчик
pytest tests/ -x --pdb

# Показать локальные переменные при ошибке
pytest tests/ -l
```

## ✅ Проверка перед коммитом

```bash
# Запустите все проверки
pytest tests/ -v --cov=dtek_telegram_bot --cov-report=term-missing

# Или создайте скрипт pre-commit
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/bash
pytest tests/ --tb=short
EOF

chmod +x .git/hooks/pre-commit
```

## 📦 requirements-test.txt

Создайте отдельный файл для тестовых зависимостей:

```txt
pytest>=7.4.0
pytest-asyncio>=0.21.0
pytest-mock>=3.11.0
pytest-cov>=4.1.0
pytest-xdist>=3.3.0  # для параллельного запуска
```

Установка:
```bash
pip install -r requirements-test.txt
```

## 🎓 Полезные команды

```bash
# Список всех тестов без запуска
pytest --collect-only tests/

# Запуск с таймингом
pytest tests/ --durations=10

# Генерация JUnit XML (для CI/CD)
pytest tests/ --junit-xml=report.xml

# Запуск в verbose режиме с цветным выводом
pytest tests/ -vv --color=yes
```

## 🚨 Типичные ошибки и решения

### Ошибка: "event_loop" fixture not found

**Решение:** Убедитесь, что установлен `pytest-asyncio`:
```bash
pip install pytest-asyncio
```

### Ошибка: Cannot find module 'dtek_telegram_bot'

**Решения:**
1. Запускайте pytest из корня проекта
2. Используйте `pytest.ini` с `pythonpath = .`
3. Установите проект через `pip install -e .`

### Тесты зависают на async функциях

**Решение:** Добавьте в `pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
```

## 🎉 Быстрый старт

Вот минимальный набор команд для начала:

```bash
# 1. Перейти в директорию проекта
cd /Users/kovala/Development/python/shutdowns/dtek/

# 2. Создать __init__.py если нет
touch tests/__init__.py

# 3. Установить зависимости
pip install pytest pytest-asyncio pytest-mock

# 4. Запустить тесты
pytest tests/ -v

# 5. Если ошибка импорта
PYTHONPATH=. pytest tests/ -v
```

Готово! 🎊