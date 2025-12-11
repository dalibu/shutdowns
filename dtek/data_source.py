from typing import Dict, Any
import logging
from common.data_source import ShutdownDataSource, ScheduleData


logger = logging.getLogger(__name__)

class DtekParserDataSource(ShutdownDataSource):
    """
    Implementation of ShutdownDataSource that uses the DTEK web parser.
    """
    async def get_schedule(self, city: str, street: str, house: str, **kwargs) -> ScheduleData:
        logger.info(f"Fetching DTEK data via parser for {city}, {street}, {house}")
        try:
            # Lazy import to avoid hard dependency on botasaurus for tests
            from dtek.parser.dtek_parser import run_parser_service as dtek_run
            
            # dtek_parser returns a dict with "data" key containing the result
            result = await dtek_run(city=city, street=street, house=house, is_debug=False)
            data = result.get("data", {})
            
            return ScheduleData(
                city=data.get("city", ""),
                street=data.get("street", ""),
                house_num=data.get("house_num", ""),
                group=data.get("group", ""),
                schedule=data.get("schedule", {}),
                current_outage=data.get("current_outage")  # Pass through outage info
            )
        except Exception as e:
            logger.error(f"DTEK parser failed: {e}")
            raise

def get_data_source() -> ShutdownDataSource:
    """Factory to get the configured data source for DTEK."""
    import os
    source_type = os.getenv("DATA_SOURCE_TYPE", "PARSER").upper()
    
    if source_type == "PARSER":
        return DtekParserDataSource()
    # Add other types here (DB, API)
    
    return DtekParserDataSource()
