import logging
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
import pytz

# Импортируем парсеры
from dtek.dtek_parser import run_parser_service as dtek_parser
from cek.cek_parser import run_parser_service as cek_parser

# Импортируем security middleware
from security_middleware import SecurityMiddleware

# Настройка логирования с Kyiv timezone
def custom_time(*args):
    """Возвращает текущее время в Киевском часовом поясе для логирования."""
    return datetime.now(pytz.timezone('Europe/Kyiv')).timetuple()

logging.Formatter.converter = custom_time
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Shutdowns API", version="1.0.0")

# Add security middleware
app.add_middleware(SecurityMiddleware)

# --- Pydantic Models ---
class ShutdownSlot(BaseModel):
    shutdown: str

class ShutdownResponse(BaseModel):
    city: str
    street: str
    house_num: str
    group: str
    provider: str
    schedule: Dict[str, List[ShutdownSlot]]

# --- Endpoints ---

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {"status": "healthy", "service": "Shutdowns API"}

@app.get("/shutdowns", response_model=ShutdownResponse)
async def get_shutdowns(
    city: str = Query(..., description="Назва міста"),
    street: str = Query(..., description="Назва вулиці"),
    house: str = Query(..., description="Номер будинку"),
    cached_group: str = Query(None, description="Кешована група (опціонально)")
):
    logger.info(f"API Request: City={city}, Street={street}, House={house}, CachedGroup={cached_group}")
    
    # Стратегия: сначала пробуем DTEK, потом CEK
    result = None
    last_error = None
    provider_name = None
    
    try:
        # Попытка 1: DTEK парсер
        logger.info("Trying DTEK parser...")
        result = await dtek_parser(city=city, street=street, house=house, is_debug=False)
        provider_name = "ДТЕК"
        logger.info("DTEK parser succeeded")
        
    except Exception as dtek_error:
        logger.warning(f"DTEK parser failed: {str(dtek_error)[:100]}")
        last_error = dtek_error
        
        try:
            # Попытка 2: CEK парсер (с кешированной группой, если есть)
            logger.info(f"Trying CEK parser{' with cached group ' + cached_group if cached_group else ''}...")
            result = await cek_parser(city=city, street=street, house=house, is_debug=False, cached_group=cached_group)
            provider_name = "ЦЕК"
            logger.info("CEK parser succeeded")
            
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

        response_data['provider'] = provider_name
        return response_data
    
    # Если ничего не получилось
    raise HTTPException(
        status_code=404,
        detail=f"Address not found. Last error: {str(last_error)[:200]}"
    )

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
