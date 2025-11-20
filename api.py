import logging
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Dict, Optional

# Импортируем парсеры
from dtek.dtek_parser import run_parser_service as dtek_parser
# CEK parser будет добавлен позже
# from cek.cek_parser import run_parser_service as cek_parser

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# --- Pydantic Models ---
class ShutdownSlot(BaseModel):
    shutdown: str

class ShutdownResponse(BaseModel):
    city: str
    street: str
    house_num: str
    group: str
    schedule: Dict[str, List[ShutdownSlot]]

# --- Provider Resolution Logic ---
def determine_provider(city: str, street: str, house: str) -> str:
    """
    Определяет провайдера (DTEK или CEK) на основе адреса.
    Пока используется простая логика - можно расширить.
    """
    city_lower = city.lower()
    
    # DTEK обслуживает: Днепр, Киев (частично), Одесса (частично)
    dtek_cities = ["дніпро", "днепр", "київ", "киев", "одеса", "одесса"]
    
    # CEK обслуживает другие города
    # Пока возвращаем DTEK для известных городов, иначе CEK
    for dtek_city in dtek_cities:
        if dtek_city in city_lower:
            return "DTEK"
    
    return "CEK"

# --- Endpoints ---

@app.get("/shutdowns", response_model=ShutdownResponse)
async def get_shutdowns(
    city: str = Query(..., description="Назва міста"),
    street: str = Query(..., description="Назва вулиці"),
    house: str = Query(..., description="Номер будинку")
):
    logger.info(f"API Request: City={city}, Street={street}, House={house}")
    
    try:
        # Определяем провайдера
        provider = determine_provider(city, street, house)
        logger.info(f"Determined provider: {provider}")
        
        if provider == "DTEK":
            # Вызываем DTEK парсер
            result = await dtek_parser(city=city, street=street, house=house, is_debug=False)
        elif provider == "CEK":
            # TODO: Вызываем CEK парсер когда он будет готов
            logger.warning("CEK parser not implemented yet, using DTEK as fallback")
            result = await dtek_parser(city=city, street=street, house=house, is_debug=False)
        else:
            raise HTTPException(status_code=400, detail="Unknown provider")
        
        # Извлекаем данные из ключа 'data'
        response_data = result.get("data")
        
        if not response_data:
            logger.error("Parser returned empty data payload.")
            raise HTTPException(status_code=500, detail="Parser returned empty data")

        return response_data

    except Exception as e:
        logger.error(f"Error processing request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
