from pymongo import MongoClient
from elasticsearch import Elasticsearch
import threading
import logging
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Global connection pools
_mongo_pool = None
_es_pool = None
_lock = threading.Lock()

def get_mongo_connection():
    """
    Returns a MongoDB connection from the pool.
    Uses thread-safe singleton pattern to ensure only one pool is created.
    """
    global _mongo_pool
    if _mongo_pool is None:
        with _lock:
            if _mongo_pool is None:
                # Create MongoDB connection pool
                # Try MONGO_URI first (for backward compatibility), then fall back to MONGODB_URL
                mongo_uri = os.getenv('MONGO_URI') or os.getenv('MONGODB_URL')
                if not mongo_uri:
                    raise ValueError("Either MONGO_URI or MONGODB_URL must be set in environment variables")
                
                _mongo_pool = MongoClient(
                    mongo_uri,
                    maxPoolSize=int(os.getenv('MONGO_MAX_POOL_SIZE', 50)),
                    minPoolSize=int(os.getenv('MONGO_MIN_POOL_SIZE', 5)),
                    connectTimeoutMS=int(os.getenv('MONGO_CONNECT_TIMEOUT_MS', 30000)),
                    socketTimeoutMS=int(os.getenv('MONGO_SOCKET_TIMEOUT_MS', 30000)),
                    heartbeatFrequencyMS=int(
                        os.getenv(
                            'MONGODB_HEARTBEAT_FREQUENCY_MS',
                            os.getenv('MONGO_HEARTBEAT_FREQUENCY_MS', '30000'),
                        )
                    ),
                    waitQueueTimeoutMS=int(os.getenv('MONGO_WAIT_QUEUE_TIMEOUT_MS', 30000)), 
                    retryWrites=os.getenv('MONGO_RETRY_WRITES', 'true').lower() == 'true',
                    retryReads=os.getenv('MONGO_RETRY_READS', 'true').lower() == 'true',
                )
    
    return _mongo_pool["pnq"]

def get_elasticsearch_connection():
    """
    Returns an Elasticsearch connection from the pool.
    Uses thread-safe singleton pattern to ensure only one pool is created.
    """
    global _es_pool
    if _es_pool is None:
        with _lock:
            if _es_pool is None:
                try:
                    # Create Elasticsearch connection pool with more robust settings
                    _es_pool = Elasticsearch(
                        [os.getenv('ES_HOST')],
                        retry_on_timeout=os.getenv('ES_RETRY_ON_TIMEOUT', 'true').lower() == 'true',
                        max_retries=int(os.getenv('ES_MAX_RETRIES', 3)),
                        sniff_on_start=os.getenv('ES_SNIFF_ON_START', 'false').lower() == 'true',
                        verify_certs=os.getenv('ES_VERIFY_CERTS', 'false').lower() == 'true',
                        http_auth=(os.getenv('ES_USERNAME'), os.getenv('ES_PASSWORD')),
                        ssl_show_warn=False,
                        request_timeout=int(os.getenv('ES_TIMEOUT', 30))
                    )
                    
                    # Verify connection
                    if not _es_pool.ping():
                        raise ConnectionError("Could not connect to Elasticsearch")
                        
                    logging.info("Successfully connected to Elasticsearch")
                    
                except Exception as e:
                    logging.error(f"Failed to connect to Elasticsearch: {str(e)}")
                    raise
                    
    return _es_pool

# For backward compatibility
def mongo():
    return get_mongo_connection()

def elastic():
    return get_elasticsearch_connection()