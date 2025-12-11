import os
import logging
import logging.handlers
import sys
import pytz
from datetime import datetime
from pathlib import Path

def custom_time(*args):
    """Returns current time in Kyiv timezone for logging."""
    return datetime.now(pytz.timezone('Europe/Kiev')).timetuple()

def setup_logging(name: str, log_dir: str = None) -> logging.Logger:
    """
    Setup logging with:
    1. StreamHandler (stdout)
    2. TimedRotatingFileHandler (daily rotation, keep 7 days) if log_dir is provided
    3. Custom formatting with Kyiv time
    4. Log level from LOG_LEVEL environment variable
    """
    logger = logging.getLogger(name)
    
    # Set level from environment, default to INFO
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logger.setLevel(log_level)
    
    logger.propagate = False
    
    # Common formatter
    formatter = logging.Formatter(
        '%(asctime)s EET | %(levelname)s:%(name)s:%(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    formatter.converter = custom_time
    
    # 1. Stream Handler (stdout)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    
    # Remove existing handlers to avoid duplicates
    if logger.handlers:
        logger.handlers.clear()
        
    logger.addHandler(stream_handler)
    
    # 2. File Handler (if log_dir is provided)
    if log_dir:
        # Check if we are running in a container with a mapped /data volume
        # Or just use the provided path
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        filename = log_path / "bot.log"
        
        # Daily rotation, keep 7 days
        try:
            file_handler = logging.handlers.TimedRotatingFileHandler(
                filename=filename,
                when='midnight',
                interval=1,
                backupCount=7,
                encoding='utf-8'
            )
            file_handler.setFormatter(formatter)
            file_handler.suffix = "%Y-%m-%d" # Suffix for rotated files: bot.log.2023-10-27
            logger.addHandler(file_handler)
        except (PermissionError, OSError) as e:
            # Fallback for when we cannot write to the log file (e.g. root-owned file from docker)
            print(f"Error setting up file logging to {filename}: {e}. Continuing with console logging only.", file=sys.stderr)
        
    return logger
