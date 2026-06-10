from elasticsearch import Elasticsearch
import os
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

# INDEX_NAME='amazonebacktrackcompanybooleanall'
# INDEX_NAME='amazonebacktrackcompanybooleanallbis'

# INDEX_NAME='allamazonebooleans'
# INDEX_NAME='amazoneparentboolean'
# INDEX_NAME='testbooleaninterface'
# INDEX_NAME='testbooleaninterface'

# INDEX_NAME='amazonalllangbooleantest'
INDEX_NAME='testindex'

# Lazy getter for Elasticsearch client - uses main app's connection
_es_client: Optional[Elasticsearch] = None

def get_es_client() -> Elasticsearch:
    """Get Elasticsearch client - uses main app's connection if available, otherwise creates one."""
    global _es_client
    
    # Try to get from main app first
    try:
        from app.utils.database import get_elasticsearch_client_sync
        main_es = get_elasticsearch_client_sync()
        if main_es is not None:
            return main_es
    except (ImportError, AttributeError):
        # Main app not available, fall back to creating our own
        pass
    
    # Fallback: create connection if main app's connection not available
    if _es_client is None:
        _es_client = Elasticsearch(
            hosts=[os.getenv("ES_HOST", "https://elastic.pnq.co.in/")], 
            http_auth=(os.getenv("ES_USER", "pnqIndex"), os.getenv("ES_PASSWORD", "New#pnq#Change!")) 
        )
    
    return _es_client

# For backward compatibility, provide 'es' as a property that uses the getter
class _ESClientProxy:
    """Proxy to Elasticsearch client that uses main app's connection."""
    def __getattr__(self, name):
        return getattr(get_es_client(), name)
    
    def __call__(self, *args, **kwargs):
        return get_es_client()(*args, **kwargs)

es = _ESClientProxy()