from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, TypedDict
import os
import logging

logger = logging.getLogger(__name__)

# --- Data Models ---

class ShutdownSlot(TypedDict):
    shutdown: str  # Format: "HH:MM–HH:MM"

class ScheduleData(TypedDict, total=False):
    city: str
    street: str
    house_num: str
    group: str
    schedule: Dict[str, List[ShutdownSlot]]  # Key: "dd.mm.yy"
    current_outage: Optional[Dict[str, Any]]  # Optional: info about active outage

# --- Abstract Base Class ---

class ShutdownDataSource(ABC):
    """
    Abstract interface for retrieving shutdown schedule data.
    """

    @abstractmethod
    async def get_schedule(self, city: str, street: str, house: str, **kwargs) -> ScheduleData:
        """
        Retrieves the shutdown schedule for a specific address.
        
        Args:
            city: City name
            street: Street name
            house: House number
            **kwargs: Additional provider-specific arguments (e.g. cached_group)
        
        Returns:
            ScheduleData: Standardized dictionary containing address info, group, and schedule.
        
        Raises:
            ValueError: If address is invalid or not found.
            ConnectionError: If data source is unreachable.
        """
        pass


