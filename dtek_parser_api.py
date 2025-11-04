import os
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any

# Импорт функции парсинга (убедитесь, что dtek_parser.py находится в том же каталоге или доступен)
# В Docker это часто работает через структуру пакетов, убедитесь, что ваш запуск это поддерживает.
try:
    from .dtek_parser import run_parser_service 
except ImportError:
    # Запасной вариант для локального запуска
    from dtek_parser import run_parser_service

# --- Конфигурация Логирования ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)
# --------------------------------

# Создаем приложение FastAPI
app = FastAPI(title="DTEK Shutdown Parser API", version="1.0")

# --- Модели Pydantic для ответа ---
class Slot(BaseModel):
    time: str
    disconection: str

class FullScheduleResponse(BaseModel):
    city: str
    street: str
    house_num: str
    group: str
    schedule: Dict[str, List[Slot]]

# --- API Endpoint ---
@app.get("/shutdowns", response_model=FullScheduleResponse)
async def get_shutdowns(city: str, street: str, house: str):
    """
    Возвращает полный, агрегированный график отключений на сегодня и завтра.
    """
    logger.info(f"API Request received for: {city}, {street} {house}")
    try:
        # 1. Получаем агрегированный словарь от парсера
        # Формат: { Общие данные, "schedule": { "Дата1": [слоты], "Дата2": [слоты] } }
        aggregated_data = await run_parser_service(city, street, house)
        
        if not aggregated_data or not aggregated_data.get("schedule"):
            raise HTTPException(status_code=404, detail="Графік для цієї адреси не знайдено.")

        # 2. Возвращаем полный, чистый агрегированный результат
        return aggregated_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Internal processing error: {e}")
        # Возвращаем 500
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {e}")