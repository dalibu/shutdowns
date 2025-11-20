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

# --- Endpoints ---

@app.get("/shutdowns", response_model=ShutdownResponse)
async def get_shutdowns(
    city: str = Query(..., description="Назва міста"),
    street: str = Query(..., description="Назва вулиці"),
    house: str = Query(..., description="Номер будинку")
):
    logger.info(f"API Request: City={city}, Street={street}, House={house}")
    
    # Стратегия: сначала пробуем DTEK, потом CEK
    result = None
    last_error = None
    
    try:
        # Попытка 1: DTEK парсер
        logger.info("Trying DTEK parser...")
        result = await dtek_parser(city=city, street=street, house=house, is_debug=False)
        logger.info("DTEK parser succeeded")
        
    except Exception as dtek_error:
        logger.warning(f"DTEK parser failed: {str(dtek_error)[:100]}")
        last_error = dtek_error
        
        try:
            # Попытка 2: CEK парсер
            logger.info("Trying CEK parser...")
            # TODO: Раскомментировать когда CEK parser будет готов
            # result = await cek_parser(city=city, street=street, house=house, is_debug=False)
            # logger.info("CEK parser succeeded")
            
            # Временно: CEK parser не реализован
            logger.error("CEK parser not implemented yet")
            raise HTTPException(
                status_code=404, 
                detail=f"Address not found in DTEK. CEK parser not yet implemented. DTEK error: {str(dtek_error)[:200]}"
            )
            
        except HTTPException:
            raise  # Пробрасываем HTTPException дальше
        except Exception as cek_error:
            logger.error(f"CEK parser also failed: {str(cek_error)[:100]}")
            # Оба парсера не смогли найти адрес
            raise HTTPException(
                status_code=404,
                detail=f"Address not found in both DTEK and CEK. Please check your address. DTEK: {str(dtek_error)[:100]}, CEK: {str(cek_error)[:100]}"
            )
    
    # Если результат получен, извлекаем данные
    if result:
        response_data = result.get("data")
        
        if not response_data:
            logger.error("Parser returned empty data payload.")
            raise HTTPException(status_code=500, detail="Parser returned empty data")

        return response_data
    
    # Если ничего не получилось
    raise HTTPException(
        status_code=404,
        detail=f"Address not found. Last error: {str(last_error)[:200]}"
    )

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
