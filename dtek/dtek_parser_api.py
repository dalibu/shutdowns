import logging
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Dict, Optional
# Импортируем функцию из вашего парсера
from dtek_parser import run_parser_service

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

# --- Endpoints ---

@app.get("/shutdowns", response_model=ShutdownResponse)
async def get_shutdowns(
    city: str = Query(..., description="Назва міста"),
    street: str = Query(..., description="Назва вулиці"),
    house: str = Query(..., description="Номер будинку")
):
    logger.info(f"API Request: City={city}, Street={street}, House={house}")
    
    try:
        # Запускаем парсер с is_debug=False для работы в Docker (headless)
        result = await run_parser_service(city=city, street=street, house=house, is_debug=False)
        
        # === ВАЖНОЕ ИСПРАВЛЕНИЕ ===
        # Извлекаем данные из ключа 'data', так как парсер возвращает:
        # { "data": { ... }, "json_path": "...", "png_path": "..." }
        response_data = result.get("data")
        
        if not response_data:
            logger.error("Parser returned empty data payload.")
            raise HTTPException(status_code=500, detail="Parser returned empty data")

        return response_data

    except Exception as e:
        logger.error(f"Error processing request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("dtek_api:app", host="0.0.0.0", port=8000, reload=True)