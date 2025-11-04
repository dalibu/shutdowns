# 1. Используем официальный образ Playwright с Python и предустановленным Chromium
# ФИКСИРУЕМ ВЕРСИЮ: v1.55.0-jammy, чтобы соответствовать Python-библиотеке
FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy

# 2. Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# 3. Копируем файл зависимостей и устанавливаем Python-пакеты
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Копируем файлы скрипта
COPY dtek_parser.py .
COPY dtek_parser_api.py . 

# 5. Открываем порт
EXPOSE 8000

# 6. Команда запуска сервиса
CMD ["uvicorn", "dtek_parser_api:app", "--host", "0.0.0.0", "--port", "8000"]