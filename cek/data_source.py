from typing import Dict, Any
import logging
from common.data_source import ShutdownDataSource, ScheduleData


logger = logging.getLogger(__name__)

class CekParserDataSource(ShutdownDataSource):
    """
    Implementation of ShutdownDataSource that uses the CEK web parser.
    """
    async def get_schedule(self, city: str, street: str, house: str, **kwargs) -> ScheduleData:
        logger.info(f"Fetching CEK data via parser for {city}, {street}, {house}")
        try:
            # Lazy import to avoid hard dependency on botasaurus for tests
            from cek.parser.cek_parser import run_parser_service as cek_run
            
            cached_group = kwargs.get("cached_group")
            result = await cek_run(city=city, street=street, house=house, is_debug=False, cached_group=cached_group)
            data = result.get("data", {})
            
            return ScheduleData(
                city=data.get("city", ""),
                street=data.get("street", ""),
                house_num=data.get("house_num", ""),
                group=data.get("group", ""),
                schedule=data.get("schedule", {})
            )
        except Exception as e:
            logger.error(f"CEK parser failed: {e}")
            raise

def get_data_source() -> ShutdownDataSource:
    """Factory to get the configured data source for CEK."""
    import os
    source_type = os.getenv("DATA_SOURCE_TYPE", "PARSER").upper()
    
    if source_type == "PARSER":
        return CekParserDataSource()
    # Add other types here (DB, API)
    
    return CekParserDataSource()
