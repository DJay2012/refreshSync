from elasticsearch import Elasticsearch
#import cx_Oracle
import psycopg2
import os
import time
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

# Elastic config
# INDEX_NAME='companyboolreseachalllangtestv1'
# INDEX_NAME='bioconbacktrackcompanyboolean9company'
# INDEX_NAME='allamazonebooleans'
#INDEX_NAME='allamazonebooleansv2'
INDEX_NAME=os.getenv("ES_INDEX_NAME")

#es = Elasticsearch(
#    hosts=["http://148.113.44.125:9200/"],  
#    http_auth=("elastic", "+OAiDmiAicakEQA=x0Yr") 
#)

es = Elasticsearch(
        hosts=[os.getenv("ES_HOST")],
        http_auth=(os.getenv("ES_USER"), os.getenv("ES_PASSWORD")),
        request_timeout=60  # Increase timeout to 60 seconds for large queries
    )

# Oracle config
# def oracleDatabaseConnection():
#     try:
#         connection = cx_Oracle.connect('cirrus/Cir^Pnq@2025@54.38.215.111/cirrus')
#         return connection
#     except Exception as error:
#         return error

# # Postgres config with connection pooling
import psycopg2.pool
import threading

_pg_pool = None
_pg_pool_lock = threading.Lock()

def get_pg_pool():
    """Get or create PostgreSQL connection pool"""
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                try:
                    # Get pool size from environment or use defaults - increased for high volume social feeds
                    pool_size = int(os.getenv("PG_POOL_SIZE", "100"))  # Support up to 1000 connections
                    min_conn = int(os.getenv("PG_MIN_CONN", "20"))   # Startup connections
                    
                    _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                        minconn=min_conn,
                        maxconn=pool_size,
                        database=os.getenv("POSTGRES_DB"),
                        user=os.getenv("POSTGRES_USER"),
                        password=os.getenv("POSTGRES_PASSWORD"),
                        host=os.getenv("POSTGRES_HOST"),
                        port=os.getenv("POSTGRES_PORT"),
                        connect_timeout=10  # 10 second timeout for new connections
                    )
                    print("PostgreSQL connection pool created successfully")
                except Exception as e:
                    print(f"Failed to create PostgreSQL connection pool: {e}")
                    raise
    return _pg_pool

def check_pg_pool_status():
    """Check PostgreSQL connection pool status"""
    try:
        pool = get_pg_pool()
        if pool:
            # Get pool statistics
            closed_connections = pool.closed
            print(f"📊 PG Pool Status: Closed connections: {closed_connections}")
            
            # Test a connection
            conn = pool.getconn()
            if conn:
                pool.putconn(conn)
                print(f"✅ PG Pool: Connection test successful")
                return True
            else:
                print(f"❌ PG Pool: Failed to get connection")
                return False
        else:
            print(f"❌ PG Pool: Pool not initialized")
            return False
    except Exception as e:
        print(f"❌ PG Pool Status Check Error: {e}")
        return False

def pgDatabaseConnection():
    """Get connection from pool (legacy compatibility)"""
    max_retries = 3
    retry_delay = 0.5
    
    for attempt in range(max_retries):
        try:
            pool = get_pg_pool()
            if pool is None:
                raise Exception("PostgreSQL connection pool is not available")
            
            conn = pool.getconn()
            if conn is None:
                # Check pool status
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                pool_status = f"Pool: {pool.minconn} min, {pool.maxconn} max connections"
                raise Exception(f"Failed to get connection from pool after {max_retries} attempts. {pool_status}")
            return conn
        except Exception as error:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            error_msg = f"Error getting PG connection: {error}"
            print(error_msg)
            return Exception(error_msg)

def return_pg_connection(conn):
    """Return connection to pool"""
    try:
        if conn is None:
            return
        
        # Check if connection is still valid
        if hasattr(conn, 'closed') and conn.closed:
            print("Warning: Attempting to return a closed connection")
            return
            
        pool = get_pg_pool()
        pool.putconn(conn)
    except Exception as error:
        print(f"Error returning PG connection: {error}")
        # If we can't return to pool, try to close the connection
        try:
            if conn and not conn.closed:
                conn.close()
        except:
            pass

# MongoDB health check and retry mechanism
def test_mongodb_connection():
    """Test MongoDB connection health"""
    try:
        mongo_db = mongoConnection()
        if isinstance(mongo_db, Exception):
            return False
        
        # Simple ping test
        mongo_db.command('ping')
        return True
    except Exception as e:
        print(f"MongoDB health check failed: {e}")
        return False

def is_mongodb_enabled():
    """Check if MongoDB operations are enabled"""
    return os.getenv("MONGODB_ENABLED", "true").lower() == "true"

def get_mongodb_with_retry(max_retries=None, retry_delay=None):
    """Get MongoDB connection with retry logic"""
    if not is_mongodb_enabled():
        print("MongoDB operations disabled via MONGODB_ENABLED=false")
        return None
    
    # Use environment variables or defaults
    max_retries = max_retries or int(os.getenv("MONGODB_RETRY_ATTEMPTS", "2"))
    retry_delay = retry_delay or int(os.getenv("MONGODB_RETRY_DELAY", "3"))
    
    for attempt in range(max_retries):
        try:
            mongo_db = mongoConnection()
            if isinstance(mongo_db, Exception):
                raise mongo_db
            
            # Test the connection with a quick ping
            mongo_db.command('ping')
            return mongo_db
        except Exception as e:
            print(f"MongoDB connection attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print("All MongoDB connection attempts failed - continuing with PostgreSQL only")
                return None
    return None

# Connection context manager for better resource management
class DatabaseConnection:
    """Context manager for PostgreSQL connections"""
    
    def __init__(self):
        self.conn = None
        self.cursor = None
    
    def __enter__(self):
        try:
            self.conn = pgDatabaseConnection()
            if isinstance(self.conn, Exception):
                raise self.conn
            self.cursor = self.conn.cursor()
            return self.conn, self.cursor
        except Exception as e:
            if self.conn and not isinstance(self.conn, Exception):
                try:
                    return_pg_connection(self.conn)
                except:
                    pass
            raise e
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.cursor:
            try:
                self.cursor.close()
            except:
                pass
        
        if self.conn and not isinstance(self.conn, Exception):
            try:
                return_pg_connection(self.conn)
            except:
                pass

# MongoDB config
_mongo_client = None

def mongoConnection():
    global _mongo_client
    try:
        # Check if client exists and recreate if needed
        if _mongo_client is None:
            print("Creating new MongoDB client")
            mongo_uri = os.getenv("PG_MONGO_URI")
            # Optimize connection with longer timeouts for network issues
            _mongo_client = MongoClient(
                mongo_uri,
                maxPoolSize=20,  # Reduce pool size to avoid overwhelming server
                minPoolSize=2,   # Reduce minimum connections
                maxIdleTimeMS=60000,  # 60 seconds idle timeout
                connectTimeoutMS=30000,  # 30 second connection timeout (increased)
                serverSelectionTimeoutMS=30000,  # 30 second server selection (increased)
                socketTimeoutMS=30000,  # 30 second socket timeout (increased)
                heartbeatFrequencyMS=30000,  # 30 second heartbeat
                retryWrites=True,  # Enable retry writes
                retryReads=True,   # Enable retry reads
                w=1,  # Write concern - don't wait for replication
                journal=False,  # Don't wait for journal
                # Add connection retry settings
                maxConnecting=5,  # Limit concurrent connections
            )
            print("MongoDB client initialized with extended timeouts")
        
        # Try to access the database to verify client is still alive
        try:
            mongo_db = os.getenv("PG_MONGO_DB", "pnq")
            db = _mongo_client[mongo_db]
            # Quick check to ensure client is not closed
            _mongo_client.server_info()
            return db
        except Exception as client_error:
            # Client is closed or disconnected, recreate it
            print(f"MongoDB client is closed or disconnected: {client_error}")
            print("Recreating MongoDB client...")
            try:
                _mongo_client.close()
            except:
                pass
            _mongo_client = None
            # Recursively call to create new client
            return mongoConnection()
        
    except Exception as error:
        print(f"MongoDB connection error: {error}")
        # Reset the client on error so next call will create a new one
        _mongo_client = None
        return error
