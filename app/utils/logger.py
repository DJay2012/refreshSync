"""
Logging utilities for the RefreshES API.
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

from app.config import settings


def setup_logging() -> logging.Logger:
    """Setup application logging."""
    
    # Create logs directory if it doesn't exist
    log_file_path = Path(settings.LOG_FILE)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Setup root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL))
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, settings.LOG_LEVEL))
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler with rotation
    file_handler = RotatingFileHandler(
        settings.LOG_FILE,
        maxBytes=settings.LOG_MAX_SIZE,
        backupCount=settings.LOG_BACKUP_COUNT
    )
    file_handler.setLevel(getattr(logging, settings.LOG_LEVEL))
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # Set specific logger levels
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.ERROR)  # Suppress WARNING level messages from uvicorn.error
    logging.getLogger("elasticsearch").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    
    # Add filter to suppress WebSocket-related warnings
    class WebSocketWarningFilter(logging.Filter):
        """Filter to suppress WebSocket upgrade request warnings."""
        def filter(self, record):
            message = record.getMessage()
            # Suppress these specific warnings
            if any(phrase in message for phrase in [
                "Invalid HTTP request received",
                "Unsupported upgrade request",
                "No supported WebSocket library detected"
            ]):
                return False
            return True
    
    # Apply filter to uvicorn loggers
    ws_filter = WebSocketWarningFilter()
    logging.getLogger("uvicorn").addFilter(ws_filter)
    logging.getLogger("uvicorn.error").addFilter(ws_filter)
    
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)


class RequestLogger:
    """Logger for HTTP requests."""
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or get_logger(__name__)
    
    def log_request(self, method: str, path: str, status_code: int, 
                   processing_time: float, client_ip: str = ""):
        """Log HTTP request details."""
        self.logger.info(
            f"{method} {path} - {status_code} - {processing_time:.3f}s - {client_ip}"
        )
    
    def log_error(self, method: str, path: str, error: Exception, 
                  client_ip: str = ""):
        """Log HTTP request error."""
        self.logger.error(
            f"{method} {path} - ERROR: {str(error)} - {client_ip}",
            exc_info=True
        )
