from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Any
import logging
import asyncio 
# Импорт re и clean_address_part удалены - SoC соблюден.

# Конфигурация логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- Pydantic Схемы ---

class TimeSlot(BaseModel):
    time: str
    disconection: str

class ShutdownResponse(BaseModel):
    city: str
    street: str
    house_num: str
    group: str
    schedule: Dict[str, List[TimeSlot]]

# --- Импорт реального сервиса (run_parser_service) ---
try:
    # Ваш асинхронный Playwright парсер
    from dtek_parser import run_parser_service as actual_parser_service
except ImportError:
    # Заглушка, если файл dtek_parser.py не найден
    async def actual_parser_service(*args, **kwargs):
        raise ValueError("Ошибка импорта dtek_parser.py. Реальный парсер недоступен.")

# --- API ---

app = FastAPI()

# Функция очистки удалена из этого слоя.

async def scrape_dtek_schedule(city: str, street: str, house: str) -> Dict[str, Any]:
    """
    СЕРВИСНЫЙ СЛОЙ (Мост): Вызывает Playwright-парсер, передавая ему сырые данные.
    """
    
    # Передаем сырые данные, позволяя сервисному слою решать, что чистить.
    city_raw = city.strip()
    street_raw = street.strip()
    house_raw = house.strip()
    
    try:
        data = await actual_parser_service(
            city=city_raw, 
            street=street_raw, 
            house=house_raw,
            is_debug=False # В API всегда Headless
        )
            
        if not isinstance(data, dict):
            raise ValueError("Парсер вернул неверный тип данных или пустой результат.")
            
        return data
        
    except ValueError as e:
        # Парсер должен возбуждать ValueError для известных ошибок (например, адрес не найден)
        raise e
    except Exception as e:
        # Все остальные неожиданные ошибки парсера (Playwright Timeout, Connection Error, и т.д.)
        raise ValueError(f"Непредвиденная ошибка в парсере: {e}")


@app.get("/shutdowns", response_model=ShutdownResponse)
async def get_shutdowns(city: str, street: str, house: str):
    
    data = {}
    try:
        # Вызов сервисного моста
        data = await scrape_dtek_schedule(city, street, house)
    
    except ValueError as e:
        # Логика обработки ошибок 404/500:
        error_message = str(e)
        if "Графік для цієї адреси не знайдено." in error_message or "Ошибка импорта" in error_message:
            # Преобразуем ожидаемую ошибку "не найдено" в 404
            raise HTTPException(status_code=404, detail=error_message)
        else:
            # Все остальные ValueErrors (например, TimeoutError, перехваченный как ValueError) в 500
            logger.error(f"Internal Parsing Error for {city}, {street}, {house}: {error_message}")
            raise HTTPException(status_code=500, detail="Internal Parsing Error")
    
    except Exception as e:
        logger.error(f"Unexpected API error during scrape: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected server error occurred during data fetching.")
        
    # Проверка на пустой результат 
    if not data:
        raise HTTPException(status_code=404, detail="Графік для цієї адреси не знайдено (пустой ответ).")

    return data