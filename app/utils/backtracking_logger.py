"""
Dedicated logging utilities for backtracking service.
Separate logger and log file for backtracking operations.
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

from app.config import settings


def setup_backtracking_logger() -> logging.Logger:
    """Setup dedicated logger for backtracking service with separate log file."""
    
    # Create logs directory if it doesn't exist
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Separate log file for backtracking
    backtracking_log_file = log_dir / "backtracking.log"
    
    # Create formatter for backtracking logs
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Create backtracking logger
    backtracking_logger = logging.getLogger("backtracking")
    backtracking_logger.setLevel(logging.INFO)
    
    # Clear existing handlers to avoid duplicates
    backtracking_logger.handlers.clear()
    
    # Console handler (for visibility)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    backtracking_logger.addHandler(console_handler)
    
    # File handler with rotation (dedicated backtracking log file)
    file_handler = RotatingFileHandler(
        backtracking_log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    backtracking_logger.addHandler(file_handler)
    
    # Prevent propagation to root logger to avoid duplicate logs
    backtracking_logger.propagate = False
    
    return backtracking_logger


def get_backtracking_logger() -> logging.Logger:
    """Get the backtracking logger instance."""
    logger = logging.getLogger("backtracking")
    if not logger.handlers:
        # If logger not initialized, set it up
        return setup_backtracking_logger()
    return logger













