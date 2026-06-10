"""
Database connection utilities.
"""

import asyncio
from typing import Optional
from pymongo import MongoClient
from elasticsearch import Elasticsearch
# from elasticsearch.connection import ConnectionPool  # Not needed in newer versions

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Global connection instances
_mongo_client: Optional[MongoClient] = None
_es_client: Optional[Elasticsearch] = None


async def get_mongo_client() -> MongoClient:
    """Get MongoDB client instance."""
    global _mongo_client
    
    if _mongo_client is None:
        try:
            _mongo_client = MongoClient(
                settings.MONGODB_URL,
                maxPoolSize=settings.CONNECTION_POOL_SIZE,
                serverSelectionTimeoutMS=settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
                connectTimeoutMS=settings.MONGODB_CONNECT_TIMEOUT_MS,
                socketTimeoutMS=settings.MONGODB_SOCKET_TIMEOUT_MS,
                heartbeatFrequencyMS=settings.MONGODB_HEARTBEAT_FREQUENCY_MS,
            )
            
            # Test connection
            _mongo_client.admin.command('ping')
            logger.info(
                f"MongoDB connection established with timeouts: "
                f"serverSelection={settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS}ms, "
                f"connect={settings.MONGODB_CONNECT_TIMEOUT_MS}ms, "
                f"socket={settings.MONGODB_SOCKET_TIMEOUT_MS}ms, "
                f"heartbeat={settings.MONGODB_HEARTBEAT_FREQUENCY_MS}ms"
            )
            
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise
    
    return _mongo_client


async def get_elasticsearch_client() -> Elasticsearch:
    """Get Elasticsearch client instance."""
    global _es_client
    
    if _es_client is None:
        try:
            # Use ES_HOST if available, otherwise fall back to ELASTICSEARCH_URL
            es_url = getattr(settings, 'ES_HOST', None) or settings.ELASTICSEARCH_URL
            
            # Handle Elasticsearch authentication
            es_config = {
                "hosts": [es_url],
                "max_retries": 3,
                "retry_on_timeout": True,
                "timeout": settings.REQUEST_TIMEOUT
            }
            
            # Add authentication if credentials are provided
            es_username = getattr(settings, 'ES_USERNAME', None) or getattr(settings, 'ES_USER', None) or ""
            es_password = getattr(settings, 'ES_PASSWORD', None) or ""
            
            if es_username and es_password:
                es_config["basic_auth"] = (es_username, es_password)
                es_config["verify_certs"] = True
            
            _es_client = Elasticsearch(**es_config)
            
            # Test connection
            _es_client.ping()
            logger.info(f"Elasticsearch connection established to {es_url}")
            
        except Exception as e:
            logger.error(f"Failed to connect to Elasticsearch at {es_url if 'es_url' in locals() else settings.ELASTICSEARCH_URL}: {e}")
            raise
    
    return _es_client


async def reset_mongo_client():
    """Reset MongoDB client to force reconnection with new settings."""
    global _mongo_client
    
    try:
        if _mongo_client:
            _mongo_client.close()
            logger.info("MongoDB client closed, will reconnect with new settings")
        _mongo_client = None
    except Exception as e:
        logger.error(f"Error resetting MongoDB client: {e}")
        _mongo_client = None


async def close_connections():
    """Close all database connections."""
    global _mongo_client, _es_client
    
    try:
        if _mongo_client:
            _mongo_client.close()
            _mongo_client = None
            logger.info("MongoDB connection closed")
        
        if _es_client:
            _es_client.close()
            _es_client = None
            logger.info("Elasticsearch connection closed")
            
    except Exception as e:
        logger.error(f"Error closing database connections: {e}")


def get_elasticsearch_client_sync() -> Optional[Elasticsearch]:
    """Get Elasticsearch client instance synchronously (for use in non-async contexts).
    
    Returns the global ES client if it exists, otherwise None.
    Modules should use this instead of creating their own connections.
    """
    return _es_client


def get_connection_info() -> dict:
    """Get connection information for monitoring."""
    return {
        "mongodb": {
            "connected": _mongo_client is not None,
            "url": settings.MONGODB_URL,
            "database": settings.MONGODB_DATABASE
        },
        "elasticsearch": {
            "connected": _es_client is not None,
            "url": settings.ELASTICSEARCH_URL
        }
    }
