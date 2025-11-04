import asyncio
from fastapi import FastAPI, HTTPException
from pathlib import Path
import os
import json
import logging
# Импортируем вашу функцию из dtek_parser.py
from dtek_parser import run_parser_service, DEFAULT_CITY, DEFAULT_STREET, DEFAULT_HOUSE 

# Настройка логирования для API
logger = logging.getLogger("uvicorn") 

# --- Инициализация FastAPI ---
app = FastAPI(title="DTEK Shutdown Parser API")

@app.get("/status")
async def get_status():
    """Проверка доступности сервиса."""
    return {"status": "ok", "message": "DTEK Parser is running."}

@app.get("/shutdowns")
async def get_shutdowns(
    city: str = DEFAULT_CITY, 
    street: str = DEFAULT_STREET, 
    house: str = DEFAULT_HOUSE,
    debug: bool = False # Флаг для режима отладки Playwright
):
    """
    Получает информацию о плановых отключениях света по адресу.
    """
    if not all([city, street, house]):
        raise HTTPException(status_code=400, detail="City, street, and house are required parameters.")

    try:
        logger.info(f"API Request received for: {city}, {street}, {house}")
        
        # Запуск вашей асинхронной функции парсинга
        png_path, result_data = await run_parser_service(
            city=city, 
            street=street, 
            house=house, 
            is_debug=debug
        )
        
        # Удаляем временный скриншот
        if os.path.exists(png_path):
            os.remove(png_path)
        
        return result_data[0] # Возвращаем первый (и единственный) объект из списка

    except TimeoutError as e:
        logger.error(f"Timeout/Address Error: {e}")
        # Возвращаем 404 или 400, если адрес не найден или таймаут
        raise HTTPException(status_code=404, detail=f"Address not found or service timed out: {e}")
    except Exception as e:
        logger.error(f"Internal processing error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {e}")

# --- Запуск (Uvicorn будет запускать этот файл) ---s