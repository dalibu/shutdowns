# CEK Parser - Placeholder
# TODO: Implement CEK-specific parsing logic

import os
from pathlib import Path
from typing import Dict, Any

# Используем абсолютный путь относительно расположения парсера
OUT_DIR = os.path.join(os.path.dirname(__file__), "out")

async def run_parser_service(city: str, street: str, house: str, is_debug: bool = False, skip_input_on_debug: bool = False) -> Dict[str, Any]:
    """
    Placeholder for CEK parser.
    Will be implemented when CEK website structure is known.
    """
    raise NotImplementedError("CEK parser not yet implemented")
