from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Dict, List, Any
import logging
import asyncio 
from playwright.async_api import TimeoutError # Импорт для явной обработки ошибок

# Конфигурация логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- Pydantic Схемы ---

class TimeSlot(BaseModel):
    # 📌 ИСПРАВЛЕНИЕ: Field(..., example="value") заменено на json_schema_extra={"example": "value"}
    time: str = Field(..., json_schema_extra={"example": "08:00–12:00"}, description="Временной интервал")
    disconection: str = Field(..., json_schema_extra={"example": "full"}, description="Статус отключения ('full', 'half', или '')")

class ShutdownResponse(BaseModel):
    # 📌 ИСПРАВЛЕНИЕ: Field(..., example="value") заменено на json_schema_extra={"example": "value"}
    city: str = Field(..., json_schema_extra={"example": "м. Київ"}, description="Город")
    street: str = Field(..., json_schema_extra={"example": "вул. Хрещатик"}, description="Улица")
    house_num: str = Field(..., json_schema_extra={"example": "2"}, description="Номер дома")
    group: str = Field(..., json_schema_extra={"example": "2"}, description="Группа отключения")
    schedule: Dict[str, List[TimeSlot]] = Field(..., description="График по датам")

# --- Импорт реального сервиса (run_parser_service) ---
try:
    # Ваш асинхронный Playwright парсер
    from dtek_parser import run_parser_service as actual_parser_service
except ImportError:
    # Заглушка, если файл dtek_parser.py не найден
    async def actual_parser_service(*args, **kwargs):
        raise ValueError("Ошибка импорта dtek_parser.py. Реальный парсер недоступен.")

# --- API ---

app = FastAPI(
    title="DTEK Shutdown Schedule API",
    description="API для получения актуального графика отключений электроэнергии от ДТЭК.",
    version="1.0.0",
    openapi_tags=[
        {"name": "schedule", "description": "Операции по получению графика отключений"},
    ]
)

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
        # Добавляем явную проверку на TimeoutError для лучшей обработки
        if isinstance(e, TimeoutError):
             raise ValueError(f"Ошибка таймаута при парсинге: {e}")
        raise ValueError(f"Непредвиденная ошибка в парсере: {e}")


@app.get(
    "/shutdowns", 
    response_model=ShutdownResponse,
    tags=["schedule"],
    summary="Получить график отключений по адресу",
    description="Запрашивает актуальный график отключений ДТЭК.",
    responses={
        404: {"description": "График для указанного адреса не найден."},
        500: {"description": "Внутренняя ошибка парсинга (например, таймаут)."}
    }
)
async def get_shutdowns(
    # 📌 ИСПРАВЛЕНИЕ: example заменено на examples (список)
    city: str = Query(..., examples=["м. Київ"], description="Город/населенный пункт"), 
    street: str = Query(..., examples=["вул. Хрещатик"], description="Улица"), 
    house: str = Query(..., examples=["2"], description="Номер дома")
):
    
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
        elif "Ошибка таймаута" in error_message:
            logger.error(f"Parsing Timeout Error for {city}, {street}, {house}")
            raise HTTPException(status_code=500, detail="Internal Parsing Error: Timeout occurred.")
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

    # Маппинг ключа 'house' из результата парсера в 'house_num' для соответствия Pydantic модели.
    if 'house' in data:
        data['house_num'] = data.pop('house')
    else:
        # Если 'house' нет (что не должно происходить), используем исходный параметр.
        data['house_num'] = house

    return data