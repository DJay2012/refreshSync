"""
Configuration settings for the RefreshES API.
"""

import os
from typing import List, Union
from pydantic_settings import BaseSettings
from pydantic import validator


class Settings(BaseSettings):
    """Application settings."""
    
    # API Configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    WORKERS: int = 1
    
    # CORS Configuration
    ALLOWED_ORIGINS: Union[List[str], str] = ["*"]
    #ALLOWED_ORIGINS: List[str] = [
    #"https://clientarchive.pnq.co.in",
    #"https://adminhub.pnq.co.in/"  # Your production domain
    #"http://localhost:5173",             # Local development
    #"http://localhost:3000",
#]
    
    # Database Configuration
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DATABASE: str = "your_database"
    ELASTICSEARCH_URL: str = "http://localhost:9200"
    ES_USERNAME: str = ""
    ES_PASSWORD: str = ""
    
    # Elasticsearch Index Names for Refresh Service
    ES_REFRESH_SOCIAL_INDEX: str = "socialfeedindex"
    ES_REFRESH_PRINT_INDEX: str = "printarticleindex"
    
    # Performance Configuration
    MAX_WORKERS: int = 200
    MIN_BATCH_WORKERS: int = 60
    BATCH_SIZE: int = 200
    REQUEST_TIMEOUT: int = 60
    CONNECTION_POOL_SIZE: int = 50
    
    # MongoDB Timeout Configuration (in milliseconds)
    MONGODB_SERVER_SELECTION_TIMEOUT_MS: int = 60000  # Time to wait for server selection (60 seconds)
    MONGODB_CONNECT_TIMEOUT_MS: int = 60000  # Time to wait for initial connection (60 seconds)
    MONGODB_SOCKET_TIMEOUT_MS: int = 60000  # Time to wait for socket operations (60 seconds)
    MONGODB_HEARTBEAT_FREQUENCY_MS: int = 30000  # MongoDB connection heartbeat frequency (30 seconds)
    
    # Amazon-specific configuration for large baskets
    AMAZON_BATCH_SIZE: int = 50  # Smaller batches for Amazon entities
    AMAZON_REQUEST_TIMEOUT: int = 300  # 5 minutes for large baskets
    AMAZON_MAX_RETRIES: int = 3
    AMAZON_RETRY_DELAY: float = 2.0  # seconds between retries
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 1000
    RATE_LIMIT_WINDOW: int = 60  # seconds
    
    # Logging Configuration
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/refresh_es_api.log"
    LOG_MAX_SIZE: int = 10 * 1024 * 1024  # 10MB
    LOG_BACKUP_COUNT: int = 5
    
    # Monitoring
    METRICS_ENABLED: bool = True
    METRICS_INTERVAL: int = 60  # seconds
    
    # Security
    API_KEY: str = ""
    ENABLE_AUTH: bool = False
    
    @validator("ALLOWED_ORIGINS", pre=True)
    def parse_cors_origins(cls, v):
        if v is None:
            return ["*"]
        if isinstance(v, str):
            # Handle empty string or invalid JSON
            if not v.strip():
                return ["*"]
            # Try to parse as JSON first (in case it's a JSON string)
            try:
                import json
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            # Handle comma-separated string
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        # If it's already a list, return as-is
        if isinstance(v, list):
            return v
        # Fallback to default
        return ["*"]
    
    @validator("LOG_LEVEL")
    def validate_log_level(cls, v):
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"LOG_LEVEL must be one of {valid_levels}")
        return v.upper()
    
    # Additional optional environment variables (may be in .env but not always used)
    PG_MONGO_URI: str = ""
    PG_MONGO_DB: str = ""
    ES_HOST: str = ""
    ES_SOCIAL_INDEX: str = ""
    ES_PRINT_INDEX: str = ""
    REDIS_URL: str = ""
    ENVIRONMENT: str = "development"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"  # Ignore extra fields from .env that aren't defined


# Global settings instance
settings = Settings()
