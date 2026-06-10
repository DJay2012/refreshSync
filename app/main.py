"""
FastAPI application for Elasticsearch refresh operations.
Provides high-performance API endpoints for refreshing single or batch documents.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any
import re
from datetime import datetime
import sys
import tempfile
import traceback
import json
import threading
import redis
import httpx
from enum import Enum
from pathlib import Path

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Query, File, UploadFile, Form, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field, validator, ConfigDict
import uvicorn
import os
import uuid
import secrets

from app.config import settings
from app.services.refresh_service import RefreshService
from app.services.monitoring import MetricsCollector
from app.services.client_sync_service import ClientSyncService
from app.services.charts_api.charts_service import validator, upload_jobs, charts_jobs, PrintInserter, SocialInserter, executor
from app.models.schemas import (
    RefreshRequest, 
    BatchRefreshRequest, 
    RefreshResponse, 
    BatchRefreshResponse,
    HealthResponse,
    MetricsResponse,
    ClientSyncRequest,
    ClientSyncResponse,
    AsyncSyncResponse,
    ExcelValidationResponse,
    UploadJobResponse,
    UploadStatusResponse
)
from app.utils.logger import setup_logging
from app.routers import allsearch

# Setup logging
logger = setup_logging()

DOCS_USERNAME = os.getenv("SWAGGER_UI_USERNAME", "pnqSync")
DOCS_PASSWORD = os.getenv("SWAGGER_UI_PASSWORD", "sync123GO!")
OPENAPI_ROUTE = "/openapi.json"
security = HTTPBasic()
MIN_BATCH_WORKERS = max(1, min(settings.MIN_BATCH_WORKERS, settings.MAX_WORKERS))
DEFAULT_BATCH_MAX_WORKERS = MIN_BATCH_WORKERS
MAX_BATCH_WORKERS = settings.MAX_WORKERS

def docs_basic_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    """Basic auth guard for Swagger UI/OpenAPI."""
    correct_username = secrets.compare_digest(credentials.username or "", DOCS_USERNAME)
    correct_password = secrets.compare_digest(credentials.password or "", DOCS_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


def resolve_max_workers(preferred_workers: Optional[Any]) -> int:
    """Normalize requested worker count against configured min/max limits."""
    effective_min = MIN_BATCH_WORKERS
    effective_max = MAX_BATCH_WORKERS
    if effective_min > effective_max:
        effective_min = effective_max
    if preferred_workers is None:
        candidate = DEFAULT_BATCH_MAX_WORKERS
    else:
        try:
            candidate = int(preferred_workers)
        except (TypeError, ValueError):
            candidate = DEFAULT_BATCH_MAX_WORKERS
    if candidate <= 0:
        candidate = DEFAULT_BATCH_MAX_WORKERS
    if candidate < effective_min:
        candidate = effective_min
    return min(candidate, effective_max)

# Import esBooleanTranslator components
try:
    # Add esBooleanTranslator to path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.join(current_dir, '..')
    esBooleanTranslator_path = os.path.join(project_root, 'esBooleanTranslator')
    if esBooleanTranslator_path not in sys.path:
        sys.path.insert(0, esBooleanTranslator_path)
    
    from src.utils.txt_to_es_transformer import TranslationToESTransformer
    from esPreview import ESPreviewEngine, ESPreviewConfig
    try:
        from esPreview.espreview import BooleanToDSLConverter
    except ImportError:
        # Try alternative import path
        from espreview.espreview import BooleanToDSLConverter
    BOOLEAN_TRANSLATOR_AVAILABLE = True
except ImportError as e:
    logger.warning(f"esBooleanTranslator not available: {e}")
    BOOLEAN_TRANSLATOR_AVAILABLE = False
    TranslationToESTransformer = None
    ESPreviewEngine = None
    ESPreviewConfig = None
    BooleanToDSLConverter = None

# Import backtracking components - Using PercolatorBacktrackingEngine (uses main tagger)
try:
    # Add esBacktracking to path
    esBacktracking_dir = os.path.join(project_root, 'esBacktracking')
    if esBacktracking_dir not in sys.path:
        sys.path.insert(0, esBacktracking_dir)
    
    from percolator_backtracking import PercolatorBacktrackingEngine
    from backtracking_config import BacktrackingConfig  # Import from config file, not engine
    BACKTRACKING_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Backtracking modules not available: {e}")
    BACKTRACKING_AVAILABLE = False
    PercolatorBacktrackingEngine = None
    BacktrackingConfig = None

# Import elasticTaggingAPI components
try:
    # Add elasticTaggingAPI to path
    elasticTaggingAPI_dir = os.path.join(project_root, 'elasticTaggingAPI')
    if elasticTaggingAPI_dir not in sys.path:
        sys.path.insert(0, elasticTaggingAPI_dir)
    
    from elasticTaggingAPI.app.models import (
        TaggingRequest,
        TaggingResponse,
        CompanyKeywordLookupRequest,
        CompanyKeywordLookupResponse,
    )
    from elasticTaggingAPI.app.tagging_service import execute_tagging
    from elasticTaggingAPI.app.persistence import persist_tagging_results, PersistenceError, PersistenceResult
    from elasticTaggingAPI.app.config import get_settings
    from legacy.core.Config import es, INDEX_NAME
    ELASTIC_TAGGING_AVAILABLE = True
except ImportError as e:
    logger.warning(f"ElasticTaggingAPI modules not available: {e}")
    ELASTIC_TAGGING_AVAILABLE = False
    TaggingRequest = None
    TaggingResponse = None
    CompanyKeywordLookupRequest = None
    CompanyKeywordLookupResponse = None
    execute_tagging = None
    persist_tagging_results = None
    PersistenceError = None
    PersistenceResult = None
    get_settings = None

# Global services
refresh_service: Optional[RefreshService] = None
metrics_collector: Optional[MetricsCollector] = None
client_sync_service: Optional[ClientSyncService] = None
boolean_transformer: Optional[Any] = None
espreview_engine: Optional[Any] = None
espreview_config: Optional[Any] = None

# ============================================================================
# BACKGROUND JOB MANAGEMENT FOR BACKTRACKING (MongoDB store + optional Redis queue)
# ============================================================================

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

# Redis-backed pending job index (optional)
_redis_job_client = None
_redis_job_lock = threading.Lock()
REDIS_PENDING_JOBS_KEY = "backtrack:jobs:pending"


def _redis_job_queue_configured() -> bool:
    """Return True if REDIS_URL is configured for job queueing."""
    redis_url = os.getenv("REDIS_URL") or settings.REDIS_URL
    return bool(redis_url)


def get_job_queue_backend_label() -> str:
    """Human-readable label for the current job queue backend."""
    return "Redis-backed queue + MongoDB" if _redis_job_queue_configured() else "MongoDB-only queue"


def get_redis_job_client():
    """Lazily create and return Redis client for job queue operations."""
    global _redis_job_client
    if not _redis_job_queue_configured():
        return None
    if _redis_job_client is None:
        with _redis_job_lock:
            if _redis_job_client is None:
                redis_url = os.getenv("REDIS_URL") or settings.REDIS_URL
                try:
                    _redis_job_client = redis.Redis.from_url(
                        redis_url,
                        decode_responses=True,
                        socket_timeout=5,
                        socket_connect_timeout=5,
                    )
                    _redis_job_client.ping()
                    logger.info("Connected to Redis for backtracking job queue")
                except Exception as e:
                    logger.error(f"Redis connection failed for backtracking job queue: {e}")
                    _redis_job_client = None
                    return None
    return _redis_job_client


def _parse_iso_timestamp(value: str) -> float:
    """Convert ISO timestamp string to epoch seconds."""
    try:
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return datetime.utcnow().timestamp()


def _redis_enqueue_pending_job(job_id: str, created_at: str):
    """Insert job ID into Redis pending index to preserve FIFO order."""
    client = get_redis_job_client()
    if not client:
        return
    try:
        client.zadd(REDIS_PENDING_JOBS_KEY, {job_id: _parse_iso_timestamp(created_at)})
    except Exception as e:
        logger.warning(f"Failed to enqueue pending job {job_id} in Redis: {e}")


def _redis_remove_pending_job(job_id: str):
    """Remove job ID from Redis pending index."""
    client = get_redis_job_client()
    if not client:
        return
    try:
        client.zrem(REDIS_PENDING_JOBS_KEY, job_id)
    except Exception as e:
        logger.warning(f"Failed to remove job {job_id} from Redis pending index: {e}")


def _sync_pending_job_with_redis(job_id: str, status: JobStatus):
    """
    Keep Redis pending index in sync with authoritative Mongo status.
    Pending jobs stay in the sorted set until their status transitions.
    """
    if not _redis_job_queue_configured():
        return
    status_value = status.value if isinstance(status, JobStatus) else status
    if status_value == JobStatus.PENDING.value:
        job = get_backtracking_job(job_id)
        if job:
            _redis_enqueue_pending_job(job_id, job["created_at"])
    else:
        _redis_remove_pending_job(job_id)

# MongoDB connection for job storage
_mongo_job_client = None
_mongo_job_db = None
_mongo_job_lock = threading.Lock()

def get_mongo_job_db():
    """Get MongoDB database for job storage."""
    global _mongo_job_client, _mongo_job_db
    
    if _mongo_job_db is None:
        with _mongo_job_lock:
            if _mongo_job_db is None:
                try:
                    from pymongo import MongoClient
                    mongo_uri = os.getenv("PG_MONGO_URI", settings.MONGODB_URL)
                    mongo_db = os.getenv("PG_MONGO_DB", settings.MONGODB_DATABASE)
                    
                    _mongo_job_client = MongoClient(
                        mongo_uri,
                        serverSelectionTimeoutMS=settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
                        connectTimeoutMS=settings.MONGODB_CONNECT_TIMEOUT_MS,
                        socketTimeoutMS=settings.MONGODB_SOCKET_TIMEOUT_MS,
                        heartbeatFrequencyMS=settings.MONGODB_HEARTBEAT_FREQUENCY_MS,
                    )
                    _mongo_job_db = _mongo_job_client[mongo_db]
                    
                    # Test connection
                    _mongo_job_client.admin.command('ping')
                    logger.info(
                        "Connected to MongoDB for backtracking job storage: "
                        f"{mongo_uri} (heartbeat={settings.MONGODB_HEARTBEAT_FREQUENCY_MS}ms)"
                    )
                except Exception as e:
                    logger.error(f"MongoDB connection failed for job storage: {e}")
                    raise
    
    return _mongo_job_db


def _normalize_mongo_job_doc(job_doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Ensure Mongo documents have a string job_id field and no ObjectId."""
    if not job_doc:
        return None
    if "_id" in job_doc:
        job_doc["job_id"] = str(job_doc["_id"])
        del job_doc["_id"]
    return job_doc

def get_backtracking_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get backtracking job status by ID from MongoDB."""
    if not BACKTRACKING_AVAILABLE:
        return None
    try:
        db = get_mongo_job_db()
        job_doc = db.backtrack.find_one({"_id": job_id})
        return _normalize_mongo_job_doc(job_doc)
    except Exception as e:
        logger.error(f"Error getting backtracking job from MongoDB: {e}")
        return None

def create_backtracking_job(config_dict: Dict[str, Any]) -> str:
    """Create a new backtracking job in MongoDB and return job ID."""
    if not BACKTRACKING_AVAILABLE:
        raise HTTPException(status_code=503, detail="Backtracking module not available")
    job_id = str(uuid.uuid4())
    try:
        db = get_mongo_job_db()
        job_doc = {
            "_id": job_id,
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "config": config_dict,
            "results": None,
            "error": None,
            "progress": {
                "chunks_processed": 0,
                "total_chunks": 0,
                "progress_percent": 0.0
            }
        }
        db.backtrack.insert_one(job_doc)
        _redis_enqueue_pending_job(job_id, job_doc["created_at"])
        return job_id
    except Exception as e:
        logger.error(f"Error creating backtracking job in MongoDB: {e}")
        raise

def has_running_backtracking_job() -> bool:
    """Check if there's currently a running backtracking job."""
    if not BACKTRACKING_AVAILABLE:
        return False
    try:
        db = get_mongo_job_db()
        running_job = db.backtrack.find_one({"status": "running"})
        return running_job is not None
    except Exception as e:
        logger.error(f"Error checking for running backtracking jobs: {e}")
        return False

def get_running_backtracking_jobs() -> List[Dict[str, Any]]:
    """Get all currently running backtracking jobs."""
    if not BACKTRACKING_AVAILABLE:
        return []
    try:
        db = get_mongo_job_db()
        jobs = list(db.backtrack.find({"status": "running"}).sort("created_at", -1))
        
        # Convert ObjectIds to strings
        jobs = [_normalize_mongo_job_doc(job) for job in jobs if job]
        
        return jobs
    except Exception as e:
        logger.error(f"Error getting running backtracking jobs: {e}")
        return []

def update_backtracking_job_status(job_id: str, status: JobStatus, results: Optional[Dict] = None, error: Optional[str] = None, progress: Optional[Dict] = None):
    """Update backtracking job status in MongoDB."""
    if not BACKTRACKING_AVAILABLE:
        return
    try:
        db = get_mongo_job_db()
        update_doc = {
            "status": status,
            "updated_at": datetime.utcnow().isoformat()
        }
        
        if results is not None:
            update_doc["results"] = results
        if error is not None:
            update_doc["error"] = error
        if progress is not None:
            update_doc["progress"] = progress
        
        db.backtrack.update_one(
            {"_id": job_id},
            {"$set": update_doc}
        )
        _sync_pending_job_with_redis(job_id, status)
    except Exception as e:
        logger.error(f"Error updating backtracking job in MongoDB: {e}")


def _redis_get_next_pending_job() -> Optional[Dict[str, Any]]:
    """Return the next pending job using Redis ordering if available."""
    client = get_redis_job_client()
    if not client:
        return None
    try:
        job_ids = client.zrange(REDIS_PENDING_JOBS_KEY, 0, 0)
        if not job_ids:
            return None
        job_id = job_ids[0]
        job_doc = get_backtracking_job(job_id)
        if job_doc and job_doc["status"] == JobStatus.PENDING.value:
            return job_doc
        # Job is no longer pending - clean up stale entry
        _redis_remove_pending_job(job_id)
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch pending job from Redis: {e}")
        return None


def _mongo_find_pending_job() -> Optional[Dict[str, Any]]:
    """Fallback to Mongo query for the oldest pending job."""
    try:
        db = get_mongo_job_db()
        pending_job = db.backtrack.find_one({"status": JobStatus.PENDING}, sort=[("created_at", 1)])
        return _normalize_mongo_job_doc(pending_job)
    except Exception as e:
        logger.error(f"Error fetching pending backtracking job from MongoDB: {e}")
        return None


def get_next_pending_backtracking_job() -> Optional[Dict[str, Any]]:
    """
    Retrieve the next pending job (Redis-powered FIFO when configured, Mongo fallback otherwise).
    """
    job_doc = _redis_get_next_pending_job()
    if job_doc:
        return job_doc
    return _mongo_find_pending_job()

_backtracking_worker_running = False
_backtracking_worker_thread = None

# Create dedicated logger for backtracking (uses separate log file)
from app.utils.backtracking_logger import get_backtracking_logger
backtracking_logger = get_backtracking_logger()

def run_backtracking_worker():
    """
    Backtracking worker - runs continuously and processes pending jobs from the queue.
    
    This worker:
    - Uses Redis (when configured) for FIFO ordering of pending jobs, with MongoDB fallback
    - Polls every 5 seconds (configurable) for PENDING jobs
    - Processes jobs one at a time in FIFO order (oldest first)
    - Updates job status: PENDING → RUNNING → COMPLETED/FAILED
    - Continues running even if individual jobs fail
    - Runs until API shutdown
    """
    global _backtracking_worker_running
    
    if not BACKTRACKING_AVAILABLE:
        backtracking_logger.warning("Backtracking worker not started: module not available")
        return
    
    backtracking_logger.info(f"Backtracking worker started - monitoring {get_job_queue_backend_label()} for pending jobs...")
    _backtracking_worker_running = True
    
    # Poll interval in seconds (how often to check for new jobs)
    poll_interval = float(os.getenv("BACKTRACKING_POLL_INTERVAL", "5"))
    backtracking_logger.info(f"   Poll interval: {poll_interval} seconds")
    
    while _backtracking_worker_running:
        try:
            # Check for pending jobs (oldest first - Redis FIFO when available)
            pending_job = get_next_pending_backtracking_job()
            
            if pending_job:
                job_id = pending_job["job_id"]
                config_dict = pending_job.get("config", {})
                
                backtracking_logger.info(f"Found pending backtracking job {job_id}, starting processing...")
                
                # Process the job
                try:
                    update_backtracking_job_status(job_id, JobStatus.RUNNING)
                    process_backtracking_job(job_id, config_dict)
                    backtracking_logger.info(f"Completed processing job {job_id}")
                except Exception as e:
                    error_msg = str(e)
                    backtracking_logger.error(f"Error processing backtracking job {job_id}: {error_msg}")
                    backtracking_logger.error(traceback.format_exc())
                    update_backtracking_job_status(job_id, JobStatus.FAILED, error=error_msg)
            else:
                # No pending jobs, sleep before next poll
                time.sleep(poll_interval)
                
        except Exception as e:
            backtracking_logger.error(f"Error in backtracking worker polling loop: {e}")
            backtracking_logger.error(traceback.format_exc())
            time.sleep(poll_interval)  # Sleep on error to avoid tight loop
    
    backtracking_logger.info("Backtracking worker stopped")

_SWAGGER_PLACEHOLDERS = {"string", "str", "<string>", "null", "none", ""}

def _sanitize_placeholder(value):
    """Return None if value is a Swagger UI default placeholder, else return value."""
    if value is None:
        return None
    if str(value).strip().lower() in _SWAGGER_PLACEHOLDERS:
        return None
    return value


def process_backtracking_job(job_id: str, config_dict: Dict[str, Any]):
    """Process a single backtracking job."""
    if not BACKTRACKING_AVAILABLE:
        update_backtracking_job_status(job_id, JobStatus.FAILED, error="Backtracking module not available")
        return
    
    try:

        # Handle "all" languages - convert to None for processing
        language_param = config_dict.get("language", "en")
        if language_param and language_param.lower() == "all":
            language_param = None
        
        # Create config - PercolatorBacktrackingEngine uses BacktrackingConfig with checkpoint support
        config = BacktrackingConfig(
            start_date=config_dict["start_date"],
            end_date=config_dict["end_date"],
            company_ids=config_dict["company_ids"],
            language=language_param,
            batch_size=config_dict.get("batch_size", 100),
            max_workers=config_dict.get("max_workers", 4),
            dry_run=config_dict.get("dry_run", False),
            process_print=config_dict.get("process_print", True),
            process_online=config_dict.get("process_online", True),
            enable_checkpoints=config_dict.get("enable_checkpoints", True),
            chunk_days=config_dict.get("chunk_days", 7),
            use_mongo_checkpoints=config_dict.get("use_mongo_checkpoints", True),
            checkpoint_id=_sanitize_placeholder(config_dict.get("checkpoint_id")),
            checkpoint_file=_sanitize_placeholder(config_dict.get("checkpoint_file")),
            auto_resume_on_crash=config_dict.get("auto_resume_on_crash", True),
            max_auto_retries=config_dict.get("max_auto_retries", 3),
            retry_delay_seconds=config_dict.get("retry_delay_seconds", 10),
            tag_workers=config_dict.get("tag_workers", 16),
            mongo_bulk_batch_size=config_dict.get("mongo_bulk_batch_size", 500),
            progress_log_interval=config_dict.get("progress_log_interval", 10000),
            es_page_size=config_dict.get("es_page_size", 1000),
            es_keepalive_minutes=config_dict.get("es_keepalive_minutes", 30),
            msearch_batch_size=config_dict.get("msearch_batch_size", 100)
        )
        
        # Initialize engine - Using PercolatorBacktrackingEngine (uses main tagger)
        engine = PercolatorBacktrackingEngine(config)
        
        # Update status - PercolatorBacktrackingEngine processes all at once (no chunking)
        update_backtracking_job_status(job_id, JobStatus.RUNNING, progress={"message": "Processing all data using percolator approach"})
        
        # Run backtracking - PercolatorBacktrackingEngine uses run_percolator_backtracking()
        # This processes all articles/feeds using the main tagger with checkpoint support
        # Since it's now async, we need to run it in an event loop
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        results = loop.run_until_complete(engine.run_percolator_backtracking(resume=config_dict.get("resume", True)))
        
        # Prefer engine-level totals (single-pass execution), fallback to company sums for compatibility.
        total_articles = results.get("total_articles_processed")
        total_social_feeds = results.get("total_social_feeds_processed")
        if total_articles is None:
            total_articles = sum(cr.get("articles_processed", 0) for cr in results.get("company_results", {}).values())
        if total_social_feeds is None:
            total_social_feeds = sum(cr.get("social_feeds_processed", 0) for cr in results.get("company_results", {}).values())
        
        # Update with final results
        update_backtracking_job_status(
            job_id, 
            JobStatus.COMPLETED,
            results={
                "start_time": results.get("start_time"),
                "end_time": results.get("end_time"),
                "total_articles_processed": total_articles,
                "total_social_feeds_processed": total_social_feeds,
                "total_tags_created": results.get("total_tags_created", 0),
                "processing_time_seconds": results.get("processing_time_seconds", 0),
                "errors": results.get("errors", []),
                "company_results": results.get("company_results", {})
            },
            progress={"progress_percent": 100.0, "message": "Completed"}
        )
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Backtracking job {job_id} failed: {error_msg}")
        logger.error(traceback.format_exc())
        update_backtracking_job_status(job_id, JobStatus.FAILED, error=error_msg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    global refresh_service, metrics_collector, client_sync_service, boolean_transformer, espreview_engine, espreview_config, _backtracking_worker_running, _backtracking_worker_thread
    
    # Startup
    logger.info("Starting RefreshES API service...")
    
    try:
        # Initialize allSearchAPI dependencies
        logger.info("Initializing allSearchAPI dependencies...")
        from allSearchAPI.app.db import init_pool, close_pool
        from app.routers.allsearch import registry
        
        init_pool()
        registry.refresh()
        if registry.last_error:
            logger.warning(f"Publication registry loaded with warnings: {registry.last_error}")
        logger.info("allSearchAPI dependencies initialized")
        
        # Initialize services
        refresh_service = RefreshService()
        metrics_collector = MetricsCollector()
        client_sync_service = ClientSyncService()
        await client_sync_service.initialize()
        
        # Initialize boolean translator services if available
        if BOOLEAN_TRANSLATOR_AVAILABLE:
            try:
                boolean_transformer = TranslationToESTransformer()
                espreview_config = ESPreviewConfig.from_env()
                espreview_engine = ESPreviewEngine(espreview_config)
                logger.info("Boolean translator services initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to initialize boolean translator services: {e}")
                boolean_transformer = None
                espreview_engine = None
                espreview_config = None
        
        # Start background tasks
        asyncio.create_task(metrics_collector.start_collection())
        
        # Start backtracking worker if available AND not running standalone
        # Check if running as standalone worker (BACKTRACKING_STANDALONE_WORKER env var)
        start_worker_in_api = os.getenv("BACKTRACKING_STANDALONE_WORKER", "false").lower() != "true"
        
        if BACKTRACKING_AVAILABLE and start_worker_in_api:
            try:
                _backtracking_worker_thread = threading.Thread(
                    target=run_backtracking_worker,
                    name="BacktrackingWorker",
                    daemon=True
                )
                _backtracking_worker_thread.start()
                backtracking_logger.info(f"Backtracking worker thread started - monitoring {get_job_queue_backend_label()} for pending jobs")
            except Exception as e:
                logger.warning(f"Failed to start backtracking worker: {e}")
        elif BACKTRACKING_AVAILABLE:
            logger.info("Backtracking worker not started in API (running as standalone process)")
        
        logger.info("RefreshES API service started successfully")
        yield
        
    except Exception as e:
        logger.error(f"Failed to start RefreshES API service: {e}")
        raise
    finally:
        # Shutdown
        logger.info("Shutting down RefreshES API service...")
        
        # Stop backtracking worker
        if BACKTRACKING_AVAILABLE:
            _backtracking_worker_running = False
            if _backtracking_worker_thread and _backtracking_worker_thread.is_alive():
                backtracking_logger.info("Stopping backtracking worker...")
                _backtracking_worker_thread.join(timeout=5)
                backtracking_logger.info("Backtracking worker stopped")
        
        if metrics_collector:
            await metrics_collector.stop_collection()
        if refresh_service:
            await refresh_service.cleanup()
        if client_sync_service:
            await client_sync_service.cleanup()
        
        # Cleanup allSearchAPI dependencies
        try:
            from allSearchAPI.app.db import close_pool
            close_pool()
            logger.info("allSearchAPI dependencies cleaned up")
        except Exception as e:
            logger.warning(f"Error cleaning up allSearchAPI dependencies: {e}")
        
        logger.info("RefreshES API service shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="RefreshES API",
    description="High-performance API for refreshing Elasticsearch documents, uploading Excel charts data, and managing boolean query translations",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.get("/docs", include_in_schema=False)
async def swagger_ui(credentials: HTTPBasicCredentials = Depends(docs_basic_auth)):
    """Serve Swagger UI with basic auth."""
    return get_swagger_ui_html(openapi_url=OPENAPI_ROUTE, title="RefreshES API Docs")


@app.get(OPENAPI_ROUTE, include_in_schema=False)
async def openapi_endpoint(credentials: HTTPBasicCredentials = Depends(docs_basic_auth)):
    """Serve OpenAPI schema with basic auth."""
    return JSONResponse(app.openapi())


# Include routers
app.include_router(allsearch.router)


# Dependency to get refresh service
async def get_refresh_service() -> RefreshService:
    if refresh_service is None:
        raise HTTPException(status_code=503, detail="Refresh service not available")
    return refresh_service


# Dependency to get metrics collector
async def get_metrics_collector() -> MetricsCollector:
    if metrics_collector is None:
        raise HTTPException(status_code=503, detail="Metrics collector not available")
    return metrics_collector


# Dependency to get client sync service
async def get_client_sync_service() -> ClientSyncService:
    if client_sync_service is None:
        raise HTTPException(status_code=503, detail="Client sync service not available")
    return client_sync_service


# Boolean Translator Pydantic Models
class QueryRequest(BaseModel):
    query: str
    indexes: Optional[str] = None
    limit: Optional[int] = None
    include_content: Optional[bool] = False

class CompanyQueryRequest(BaseModel):
    language: str = "en"
    indexes: Optional[str] = None
    limit: Optional[int] = None
    include_content: Optional[bool] = False

class BooleanUploadRequest(BaseModel):
    companyId: str
    companyName: str
    originalQuery: str
    translations: Dict[str, str]
    index_name: Optional[str] = None
    delete_existing: Optional[bool] = True
    validate_translation_script: Optional[bool] = False


class WildcardUploadRequest(BaseModel):
    """Request for upload-wildcard: companyId, companyName, and lang_<code> with list or comma-separated keywords."""
    model_config = ConfigDict(extra="allow")
    companyId: str
    companyName: str
    index_name: Optional[str] = None
    delete_existing: Optional[bool] = True
    validate_translation_script: Optional[bool] = False


class BacktrackingRequest(BaseModel):
    start_date: str
    end_date: str
    company_ids: List[str]
    language: Optional[str] = "en"  # Can be "en", "hi", "all", or any language code. Use "all" to process all languages.
    batch_size: int = 100
    max_workers: int = 4
    dry_run: bool = False
    process_print: bool = True
    process_online: bool = True
    enable_checkpoints: bool = True
    chunk_days: int = 7
    resume: bool = True
    checkpoint_file: Optional[str] = None
    use_mongo_checkpoints: bool = True
    checkpoint_id: Optional[str] = None
    auto_resume_on_crash: bool = True
    max_auto_retries: int = 3
    retry_delay_seconds: int = 10
    tag_workers: int = 16
    mongo_bulk_batch_size: int = 500
    progress_log_interval: int = 10000
    es_page_size: int = 200
    es_keepalive_minutes: int = 30


@app.get("/", response_model=Dict[str, Any])
async def root():
    """Root endpoint with API information."""
    endpoints = {
        "refresh": {
            "POST /refresh/article/{article_id}": "Refresh single article",
            "POST /refresh/social/{social_feed_id}": "Refresh single social feed",
            "POST /refresh/article/batch": "Refresh multiple articles",
            "POST /refresh/social/batch": "Refresh multiple social feeds"
        },
        "sync": {
            "POST /sync/client": "Sync client data",
            "POST /sync/client/cbcp": "Sync CBCP data (async)",
            "POST /sync/client/cponline": "Sync CPOnline data (async)",
            "GET /sync/client/{client_id}/status": "Get sync status"
        },
        "charts": {
            "POST /charts/validate/social": "Validate social Excel file",
            "POST /charts/validate/print": "Validate print Excel file",
            "POST /charts/upload/social": "Upload social chart data",
            "POST /charts/upload/print": "Upload print chart data",
            "GET /charts/upload/status/{job_id}": "Get upload job status"
        },
        "boolean_translator": {
            "POST /boolean/upload": "Upload boolean query JSON data",
            "POST /boolean/upload-wildcard": "Upload wildcard keywords per language to ES (doc id: companyId_wildcard)",
            "GET /boolean/company/{company_id}": "Get company boolean query",
            "DELETE /boolean/company/{company_id}": "Delete company boolean query",
            "POST /boolean/espreview/query": "Execute boolean query using esPreview",
            "POST /boolean/espreview/query/file": "Execute query from JSON file",
            "POST /boolean/espreview/company/{company_id}": "Execute company query",
            "GET /boolean/espreview/companies": "List available companies",
            "GET /boolean/espreview/health": "Check esPreview health"
        },
    }
    
    # Add backtracking endpoints if available
    if BACKTRACKING_AVAILABLE:
        endpoints["backtracking"] = {
            "POST /backtracking/run": "Start backtracking job (queued if worker enabled, else immediate) - Worker mode processes jobs automatically from queue",
            "GET /backtracking/job/{job_id}": "Get backtracking job status",
            "GET /backtracking/jobs": "List all backtracking jobs (use ?status_filter=running to check active jobs)",
            "DELETE /backtracking/job/{job_id}": "Cancel a backtracking job",
            "GET /backtracking/health": "Check backtracking system health",
            "GET /backtracking/status/{checkpoint_file}": "Get checkpoint status (filesystem)",
            "GET /backtracking/checkpoint/{checkpoint_id}": "Get checkpoint status (MongoDB)",
            "POST /backtracking/resume": "Resume backtracking from checkpoint (returns immediately with job_id)"
        }
    
    # Add elasticTagging endpoints if available
    if ELASTIC_TAGGING_AVAILABLE:
        endpoints["elastic_tagging"] = {
            "POST /tagging/tag": "Tag an article and get company/keyword matches",
            "POST /tagging/tag/company-keywords": "Get keywords for a specific company from article content",
            "GET /tagging/health": "Check elasticTagging system health"
        }
    
    return {
        "message": "RefreshES API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "endpoints": endpoints,
        "boolean_translator_available": BOOLEAN_TRANSLATOR_AVAILABLE,
        "backtracking_available": BACKTRACKING_AVAILABLE,
        "elastic_tagging_available": ELASTIC_TAGGING_AVAILABLE
    }


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    try:
        # Check service availability
        refresh_healthy = refresh_service is not None and await refresh_service.health_check()
        client_sync_healthy = client_sync_service is not None and client_sync_service._initialized
        
        return HealthResponse(
            status="healthy" if (refresh_healthy and client_sync_healthy) else "unhealthy",
            timestamp=datetime.utcnow(),
            services={
                "refresh_service": refresh_healthy,
                "metrics_collector": metrics_collector is not None,
                "client_sync_service": client_sync_healthy
            }
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return HealthResponse(
            status="unhealthy",
            timestamp=datetime.utcnow(),
            services={"refresh_service": False, "metrics_collector": False, "client_sync_service": False},
            error=str(e)
        )


# ============================================================================
# BOOLEAN TRANSLATOR ENDPOINTS
# ============================================================================

def _build_wildcard_query_quoted(parts: List[str]) -> str:
    """Build a boolean query string from keyword list: each term in quotes joined by OR."""
    if not parts:
        return ""
    terms = [t.strip() for t in parts if str(t).strip()]
    if not terms:
        return ""
    return " OR ".join(f'"{t}"' for t in terms)


def convert_json_format(json_data, transformer=None):
    """Convert JSON format with 'translations' to 'lang_{code}' format with Elasticsearch DSL.
    When transformer (TranslationToESTransformer) is provided, uses strict parsing only:
    parse_boolean_query_to_elasticsearch() for each query; raises ValueError(field: message) on syntax error.
    """
    converted_data = {
        "companyId": json_data.get("companyId"),
        "companyName": json_data.get("companyName")
    }
    translations = json_data.get("translations", {})

    # Strict path: use TranslationToESTransformer only; no fallback
    if transformer is not None:
        if "originalQuery" in json_data:
            q = (json_data.get("originalQuery") or "").strip()
            if q:
                try:
                    converted_data["lang_en"] = transformer.parse_boolean_query_to_elasticsearch(q)
                except Exception as e:
                    raise ValueError(f"originalQuery: {str(e)}")
        for lang_code, query_string in translations.items():
            q = (query_string or "").strip()
            if not q:
                continue
            try:
                converted_data[f"lang_{lang_code}"] = transformer.parse_boolean_query_to_elasticsearch(q)
            except Exception as e:
                raise ValueError(f"lang_{lang_code}: {str(e)}")
        return converted_data

    # Legacy path: BooleanToDSLConverter with fallbacks
    # Initialize BooleanToDSLConverter for parsing boolean queries
    if BooleanToDSLConverter is None:
        raise ValueError("BooleanToDSLConverter not available - esBooleanTranslator module not properly imported")
    
    converter = BooleanToDSLConverter()
    
    def parse_query_to_dsl(query_string: str) -> Dict[str, Any]:
        """Parse a query string (which may contain OR logic) into Elasticsearch DSL."""
        if not query_string:
            return None
        
        # Log the raw input for debugging
        logger.debug(f"Raw query string received: {repr(query_string[:200])}")
        
        # Clean up the query string - handle escaped quotes properly
        # The query string might have literal backslash-quote sequences like: \" 
        # We need to convert these to regular quotes
        
        clean_query = query_string.strip()
        
        # CRITICAL: Replace literal escaped quotes FIRST before any other processing
        # The query string might contain literal backslash-quote sequences: \"
        # These need to be converted to regular quotes: "
        # Do this multiple times to handle nested escaping
        for _ in range(3):  # Handle up to triple escaping
            old_query = clean_query
            clean_query = clean_query.replace('\\"', '"')
            if old_query == clean_query:
                break  # No more changes
        
        # Also handle if backslash-quote appears as literal characters (not escaped)
        # This regex will catch any remaining \ followed by "
        clean_query = re.sub(r'\\(?=")', '', clean_query)
        
        # Now check for OR logic BEFORE removing outer quotes
        # This is important because we need to know if OR exists to decide whether to remove outer quotes
        temp_check = clean_query.strip()
        has_or_before_strip = (
            ' OR ' in temp_check.upper() or 
            re.search(r'\s+OR\s+', temp_check, re.IGNORECASE) is not None
        )
        
        # Remove outer quotes ONLY if:
        # 1. The entire string is wrapped in quotes
        # 2. AND it's NOT a query with OR/AND logic (those should keep their structure)
        if clean_query.startswith('"') and clean_query.endswith('"') and not has_or_before_strip:
            # Single phrase wrapped in quotes - remove outer quotes
            clean_query = clean_query[1:-1].strip()
        
        clean_query = clean_query.strip()
        
        # Log cleaned query for debugging
        logger.debug(f"Cleaned query: {repr(clean_query[:200])}")
        
        # Check if the query contains OR logic (case-insensitive)
        # Look for " OR " pattern (with spaces) - this is the key pattern
        # Also check for patterns with different spacing and escaped quotes
        has_or = (
            ' OR ' in clean_query.upper() or 
            re.search(r'\s+OR\s+', clean_query, re.IGNORECASE) is not None or
            re.search(r'\"\s+OR\s+\"', clean_query, re.IGNORECASE) is not None or
            # Also check for literal backslash-quote patterns (in case normalization didn't work)
            '\\"  OR  \\"' in clean_query or
            re.search(r'\\"\s+OR\s+\\"', clean_query, re.IGNORECASE) is not None
        )
        
        # Debug logging - use INFO level so it shows up in production logs
        logger.info(f"OR detection - has_or: {has_or}, query preview: {clean_query[:150]}...")
        if not has_or and (' OR ' in query_string.upper() or '\\"  OR  \\"' in query_string):
            logger.warning(f"OR logic detected in raw input but not in cleaned query! Raw: {repr(query_string[:150])}, Cleaned: {repr(clean_query[:150])}")
        
        if has_or:
            # Try BooleanToDSLConverter first, but always normalize the result
            # For percolator queries, we use "content" as the target field
            try:
                dsl_query = converter.convert(clean_query, target_fields=["content"])
                # Normalize the result to ensure all clauses use match_phrase for content field
                normalized_query = normalize_dsl_to_match_phrase(dsl_query, "content")
                
                # Verify that normalization produced a bool query with should clauses
                # If not, fall back to manual parsing
                if isinstance(normalized_query, dict):
                    if "bool" in normalized_query and "should" in normalized_query.get("bool", {}):
                        return normalized_query
                    # If it's still a single match/match_phrase, manual parse instead
                    if "match" in normalized_query or "match_phrase" in normalized_query:
                        logger.debug(f"BooleanToDSLConverter returned single query instead of bool, using manual parser")
                        return manual_parse_or_query(clean_query)
                
                return normalized_query
            except Exception as e:
                logger.warning(f"Error parsing boolean query with BooleanToDSLConverter: {e}, falling back to manual parsing")
                # Fallback: manually parse OR logic
                return manual_parse_or_query(clean_query)
        else:
            # Simple single phrase query - use match_phrase
            # Remove quotes if present
            phrase = clean_query.strip('"').strip()
            return {
            "match_phrase": {
                "content": {
                        "query": phrase
                    }
                }
            }
    
    def normalize_dsl_to_match_phrase(dsl_query: Dict[str, Any], target_field: str) -> Dict[str, Any]:
        """Normalize DSL query to ensure all clauses use match_phrase for the target field."""
        if not isinstance(dsl_query, dict):
            return dsl_query
        
        # If it's a bool query, normalize all clauses
        if "bool" in dsl_query:
            bool_query = dsl_query["bool"]
            normalized_bool = {}
            
            # Normalize should clauses (OR logic)
            if "should" in bool_query:
                normalized_should = []
                for clause in bool_query["should"]:
                    normalized_clause = normalize_clause_to_match_phrase(clause, target_field)
                    if normalized_clause:
                        normalized_should.append(normalized_clause)
                if normalized_should:
                    normalized_bool["should"] = normalized_should
                    normalized_bool["minimum_should_match"] = 1
            
            # Normalize must clauses (AND logic)
            if "must" in bool_query:
                normalized_must = []
                for clause in bool_query["must"]:
                    normalized_clause = normalize_clause_to_match_phrase(clause, target_field)
                    if normalized_clause:
                        normalized_must.append(normalized_clause)
                if normalized_must:
                    normalized_bool["must"] = normalized_must
            
            # Normalize must_not clauses
            if "must_not" in bool_query:
                normalized_must_not = []
                for clause in bool_query["must_not"]:
                    normalized_clause = normalize_clause_to_match_phrase(clause, target_field)
                    if normalized_clause:
                        normalized_must_not.append(normalized_clause)
                if normalized_must_not:
                    normalized_bool["must_not"] = normalized_must_not
            
            if normalized_bool:
                return {"bool": normalized_bool}
            else:
                return dsl_query
        
        # If it's a single match/match_phrase query, normalize it
        return normalize_clause_to_match_phrase(dsl_query, target_field) or dsl_query
    
    def normalize_clause_to_match_phrase(clause: Dict[str, Any], target_field: str) -> Optional[Dict[str, Any]]:
        """Convert a clause to match_phrase format for the target field."""
        if not isinstance(clause, dict):
            return None
        
        # If already match_phrase for the target field, return as-is
        if "match_phrase" in clause:
            match_phrase = clause["match_phrase"]
            if target_field in match_phrase:
                return clause
            # If it's match_phrase for a different field, extract query and convert
            for field, field_data in match_phrase.items():
                if isinstance(field_data, dict):
                    query_text = field_data.get("query", "")
                else:
                    query_text = field_data
                return {
                    "match_phrase": {
                        target_field: {
                            "query": query_text
                        }
                    }
                }
        
        # If it's a match query, convert to match_phrase
        if "match" in clause:
            match_clause = clause["match"]
            for field, match_data in match_clause.items():
                if isinstance(match_data, dict):
                    query_text = match_data.get("query", "")
                else:
                    query_text = match_data
                return {
                    "match_phrase": {
                        target_field: {
                            "query": query_text
                        }
                    }
                }
        
        # If it's a nested bool query, normalize recursively
        if "bool" in clause:
            return normalize_dsl_to_match_phrase(clause, target_field)
        
        return clause
    
    def manual_parse_or_query(query_string: str) -> Dict[str, Any]:
        """Manually parse OR query when BooleanToDSLConverter fails."""
        # Handle quoted phrases properly - split by " OR " but preserve quotes
        # First, normalize escaped quotes if present
        normalized = query_string.replace('\\"', '"')
        normalized = normalized.replace('\\\\"', '"')
        
        # Split by " OR " (case-insensitive, with spaces) while respecting parentheses
        # We need to split on OR that's not inside parentheses
        parts = []
        current_part = []
        paren_depth = 0
        i = 0
        normalized_upper = normalized.upper()
        
        while i < len(normalized):
            char = normalized[i]
            
            # Track parentheses depth
            if char == '(':
                paren_depth += 1
                current_part.append(char)
            elif char == ')':
                paren_depth -= 1
                current_part.append(char)
            # Check for OR operator (only when not inside parentheses)
            elif (i + 1 < len(normalized) and 
                  normalized_upper[i:i+2] == 'OR' and
                  paren_depth == 0 and
                  (i == 0 or normalized[i-1].isspace()) and
                  (i + 2 >= len(normalized) or normalized[i+2].isspace())):
                # Found OR at top level - save current part and start new one
                part_str = ''.join(current_part).strip()
                if part_str:
                    parts.append(part_str)
                current_part = []
                # Skip the OR and any following whitespace
                i += 2
                while i < len(normalized) and normalized[i].isspace():
                    i += 1
                continue
            else:
                current_part.append(char)
            
            i += 1
        
        # Add the last part
        part_str = ''.join(current_part).strip()
        if part_str:
            parts.append(part_str)
        
        # If no parts found, try simple split as fallback
        if not parts:
            parts = re.split(r'\s+OR\s+', normalized, flags=re.IGNORECASE)
        
        should_clauses = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # Handle parentheses with AND logic: ("phrase1" AND "phrase2")
            if part.startswith('(') and part.endswith(')'):
                inner = part[1:-1].strip()
                # Check if it contains AND
                if ' AND ' in inner.upper():
                    # Split by AND and create must clauses
                    and_parts = re.split(r'\s+AND\s+', inner, flags=re.IGNORECASE)
                    must_clauses = []
                    for and_part in and_parts:
                        and_part = and_part.strip().strip('"').strip()
                        if and_part:
                            must_clauses.append({
            "match_phrase": {
                "content": {
                                        "query": and_part
                                    }
                                }
                            })
                    if must_clauses:
                        if len(must_clauses) == 1:
                            should_clauses.append(must_clauses[0])
                        else:
                            should_clauses.append({
                                "bool": {
                                    "must": must_clauses
                                }
                            })
                else:
                    # Just parentheses, no AND - treat as single phrase
                    phrase = inner.strip('"').strip()
                    if phrase:
                        should_clauses.append({
                            "match_phrase": {
                                "content": {
                                    "query": phrase
                }
            }
                        })
            else:
                # Simple quoted or unquoted phrase
                phrase = part.strip('"').strip()
                if phrase:
                    should_clauses.append({
                        "match_phrase": {
                            "content": {
                                "query": phrase
                            }
                        }
                    })
        
        if len(should_clauses) == 1:
            return should_clauses[0]
        
        if not should_clauses:
            # Fallback: return as single match_phrase
            return {
                "match_phrase": {
                    "content": {
                        "query": normalized.strip().strip('"').strip()
                    }
                }
            }
        
        return {
            "bool": {
                "should": should_clauses,
                "minimum_should_match": 1
            }
        }
    
    # Process originalQuery (English)
    if "originalQuery" in json_data:
        original_query = json_data["originalQuery"]
        dsl_query = parse_query_to_dsl(original_query)
        if dsl_query:
            converted_data["lang_en"] = dsl_query
    
    # Process translations
    for lang_code, query_string in translations.items():
        dsl_query = parse_query_to_dsl(query_string)
        if dsl_query:
            converted_data[f"lang_{lang_code}"] = dsl_query
    
    return converted_data


# ============================================================================
# BOOLEAN TRANSLATE / TRANSLITERATE ENDPOINT
# ============================================================================

UNICODE_RANGES: Dict[str, tuple] = {
    "Devanagari": (0x0900, 0x097F),
    "Bengali":    (0x0980, 0x09FF),
    "Gujarati":   (0x0A80, 0x0AFF),
    "Gurmukhi":   (0x0A00, 0x0A7F),
    "Odia":       (0x0B00, 0x0B7F),
    "Tamil":      (0x0B80, 0x0BFF),
    "Telugu":     (0x0C00, 0x0C7F),
    "Kannada":    (0x0C80, 0x0CFF),
    "Malayalam":  (0x0D00, 0x0D7F),
    "Arabic":     (0x0600, 0x06FF),
    "Latin":      (0x0000, 0x007F),
    "Assamese":   (0x0980, 0x09FF),
}

FORBIDDEN_SCRIPTS: Dict[str, list] = {
    "or": ["Devanagari"], "bn": ["Devanagari"], "as": ["Devanagari"],
    "ta": ["Devanagari"], "te": ["Devanagari"], "kn": ["Devanagari"],
    "ml": ["Devanagari"], "gu": ["Devanagari"], "pa": ["Devanagari"],
}


def _detect_scripts(text: str) -> set:
    found = set()
    for ch in text:
        cp = ord(ch)
        for name, (lo, hi) in UNICODE_RANGES.items():
            if lo <= cp <= hi:
                found.add(name)
                break
    return found


def _validate_script(text: str, lang_code: str):
    """Return (is_valid, error_msg). Checks for forbidden scripts and requires expected script for non-English."""
    if lang_code == "en" or not text:
        return True, None
    check = text.replace(",", "").replace(" ", "")
    scripts = _detect_scripts(check)
    scripts.discard("Latin")
    forbidden = FORBIDDEN_SCRIPTS.get(lang_code, [])
    bad = [s for s in scripts if s in forbidden]
    if bad:
        expected = LANG_SCRIPTS.get(lang_code, "?")
        return False, f"Forbidden script(s) {bad} found for {lang_code}; expected {expected}"
    # Stricter: for non-English, reject Latin-only output (e.g. unchanged ALL CAPS)
    expected_script = LANG_SCRIPTS.get(lang_code)
    if expected_script and expected_script != "Latin" and not scripts:
        return False, (
            f"Output for {lang_code} must use {expected_script} script; "
            "no non-Latin script detected (e.g. do not leave keywords in English or ALL CAPS)."
        )
    return True, None


LANG_ID_TO_NAME: Dict[str, Optional[str]] = {
    "en": "English",
    "hi": "Hindi",
    "as": "Assamese",
    "bn": "Bengali",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "mni": "Manipuri",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia (Oriya)",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
    "ks": "Kashmiri",
    "sd": "Sindhi",
    "fr": "French",
}

LANG_SCRIPTS: Dict[str, str] = {
    "en": "Latin",
    "hi": "Devanagari",
    "bn": "Bengali",
    "mr": "Devanagari",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ta": "Tamil",
    "te": "Telugu",
    "ml": "Malayalam",
    "as": "Assamese",
    "or": "Odia",
    "pa": "Gurmukhi",
    "ur": "Arabic",
    "ne": "Devanagari",
    "sa": "Devanagari",
    "ks": "Arabic",
    "mni": "Meitei",
    "sd": "Arabic",
}


class BooleanQueryTranslateRequest(BaseModel):
    mode: str         # "translate" or "transliterate"
    keywordType: Optional[str] = "boolean"  # "boolean" or "wildcard"
    languages: List[str]
    includeQuery: str


def _openai_boolean_sync(query: str, lang_code: str, lang_name: str, script: str, mode: str, keyword_type: str = "boolean") -> str:
    """
    Synchronous OpenAI call for a single language.
    mode='translate'      → semantic translation (meaning-based)
    mode='transliterate'  → phonetic transliteration (sound-based)
    keyword_type='boolean'  → full boolean query with AND/OR/NOT operators
    keyword_type='wildcard' → comma-separated keywords with optional * wildcards
    """
    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        return query

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return query

    if lang_code == "en":
        return query

    client = OpenAI(api_key=api_key)
    model = os.getenv("BOOLEAN_TRANSLATE_MODEL", "gpt-4o-mini")

    if keyword_type == "wildcard":
        if mode == "translate":
            system_prompt = (
                "You are an expert translator for wildcard search keywords used in media monitoring.\n\n"
                "Your task: for each keyword, produce multiple translated/transliterated variants that cover "
                "different natural spellings and translations in the target language.\n\n"
                "STRICT RULES:\n"
                "- Input is a comma-separated list of keywords. Each keyword may or may not end with *.\n"
                "- For each keyword, generate 2-4 variants: natural translation + common alternate spellings.\n"
                "- For proper nouns, brand names, and product names, generate phonetic variants only.\n"
                "- If a keyword ends with *, preserve the * at the end of EVERY variant of that keyword.\n"
                "- Separate variants of the same keyword with commas.\n"
                "- All variants of all keywords go into one flat comma-separated output list.\n"
                f"- Output must use ONLY {lang_name} script ({script}). Do NOT mix scripts.\n"
                "- The output MUST NOT contain English letters (A–Z, a–z). Even if a keyword is in ALL CAPS, translate it into the target script.\n\n"
                "OUTPUT: Return ONLY the flat comma-separated list of all variants. No explanations."
            )
            user_prompt = (
                f"Translate the following keywords into {lang_name} ({script} script) with multiple variants.\n"
                "If a keyword has *, keep * on every variant. Output: flat comma-separated list of all variants.\n"
                "Even if a keyword is in ALL CAPS, translate it into the target script. No English letters in the output.\n\n"
                f"Keywords:\n{query}"
            )
        else:  # transliterate wildcard
            system_prompt = (
                "You are a phonetic transliteration expert for wildcard search keywords in Indian languages.\n\n"
                "Your task: for each keyword, produce multiple phonetic variants that capture different "
                "possible transliterations in the target language script.\n\n"
                "STRICT RULES:\n"
                "- Input is a comma-separated list of keywords. Each keyword may or may not end with *.\n"
                "- Do NOT translate meaning. Only transliterate phonetically (how it SOUNDS).\n"
                "- For each keyword, generate 2-4 phonetic variants covering common spelling variations.\n"
                "- If a keyword ends with *, preserve the * at the end of EVERY variant of that keyword.\n"
                "- Separate variants of the same keyword with commas.\n"
                "- All variants of all keywords go into one flat comma-separated output list.\n"
                f"- Every variant must use ONLY {lang_name} script ({script}). Do NOT use Devanagari for non-Hindi languages.\n"
                "- The output MUST NOT contain English letters (A–Z, a–z). Even if a keyword is in ALL CAPS, transliterate it into the target script.\n\n"
                "OUTPUT: Return ONLY the flat comma-separated list of all variants. No explanations."
            )
            user_prompt = (
                f"Transliterate the following keywords into {lang_name} ({script} script) with multiple phonetic variants.\n"
                "If a keyword has *, keep * on every variant. Output: flat comma-separated list of all variants.\n"
                "Even if a keyword is in ALL CAPS, transliterate it into the target script. No English letters in the output.\n\n"
                f"Keywords:\n{query}"
            )
    elif mode == "translate":
        system_prompt = (
            "You are an expert translator for Boolean search queries used in media monitoring.\n\n"
            "Your task: translate the Boolean query into the target language.\n\n"
            "LANGUAGE ENFORCEMENT RULE (CRITICAL):\n"
            "- The final output MUST NOT contain ANY English alphabet characters (A–Z, a–z) EXCEPT for: "
            "1) Boolean operators AND / OR / NOT, 2) Field names appearing before ':'.\n\n"
            "STRICT RULES:\n"
            "- Translate the MEANING of each keyword into natural, idiomatic target-language equivalents.\n"
            "- For proper nouns, brand names, and product names (e.g. 'Honda', 'Bajaj', 'Yamaha'), "
            "transliterate them phonetically instead of translating.\n"
            "- Translate or phonetic-transliterate EVERY keyword into the target language script. "
            "NEVER leave keywords in English (including ALL CAPS or acronyms).\n"
            "- If you cannot eliminate English letters from a keyword, you MUST transliterate it character-by-character into the target script.\n"
            "- Preserve Boolean operators AND, OR, NOT exactly as-is (uppercase English).\n"
            "- Preserve ++, +, *, NEAR/10 (or NEAR/n), parentheses, and double quotes exactly.\n"
            "- Every substring inside double quotes in the input must appear in the output as exactly ONE "
            "contiguous double-quoted string. Never split a quoted phrase.\n"
            "- Between any two quoted phrases or terms there must remain AND, OR, or NEAR/n.\n"
            f"- Output must use ONLY {lang_name} script ({script}). Do NOT mix scripts.\n\n"
            "OUTPUT: Return ONLY the translated Boolean query. No explanations."
        )
        user_prompt = (
            f"Translate the following Boolean query into {lang_name} ({script} script).\n"
            "Translate or phonetic-transliterate EVERY keyword. English letters are STRICTLY FORBIDDEN in the output "
            "(except AND / OR / NOT and field names). Even if the input is in ALL CAPS, produce output in the target script only.\n\n"
            f"Boolean query:\n{query}"
        )
    else:  # transliterate boolean
        system_prompt = (
            "You are a phonetic transliteration expert for Boolean search queries in Indian languages.\n\n"
            "Your task: transliterate the Boolean query into the target language script.\n\n"
            "LANGUAGE ENFORCEMENT RULE (CRITICAL):\n"
            "- The final output MUST NOT contain ANY English alphabet characters (A–Z, a–z) EXCEPT for: "
            "Boolean operators AND / OR / NOT and field names before ':'.\n\n"
            "STRICT RULES:\n"
            "- Do NOT translate meaning. Only transliterate each keyword phonetically (how it SOUNDS).\n"
            "- For English-origin phrases and brand names, keep the same English loanword feel in the target script.\n"
            "- Transliterate EVERY keyword (including ALL CAPS and acronyms) into the target script. NEVER leave any in English.\n"
            "- If you cannot eliminate English letters from a keyword, you MUST transliterate it character-by-character into the target script.\n"
            "- Preserve Boolean operators AND, OR, NOT exactly as-is (uppercase English).\n"
            "- Preserve ++, +, *, NEAR/10 (or NEAR/n), parentheses, and double quotes exactly.\n"
            "- Every substring inside double quotes in the input must appear in the output as exactly ONE "
            "contiguous double-quoted string. Never split a quoted phrase.\n"
            "- Between any two quoted phrases or terms there must remain AND, OR, or NEAR/n.\n"
            f"- Every keyword must be phonetically transliterated into {lang_name} script ({script}) only.\n"
            "- You MUST use ONLY the correct script for the language. Do NOT use Devanagari for non-Hindi languages.\n\n"
            "OUTPUT: Return ONLY the transliterated Boolean query. No explanations."
        )
        user_prompt = (
            f"Transliterate the following Boolean query into {lang_name} ({script} script).\n"
            "Do NOT translate – only transliterate each keyword phonetically. "
            "Even if the input is in ALL CAPS, output MUST be in the target script only (no English letters except AND/OR/NOT). "
            "Keep AND, OR, NOT, ++, +, *, NEAR/10, and quotes unchanged in structure.\n\n"
            f"Boolean query:\n{query}"
        )

    for attempt in range(3):
        try:
            if attempt > 0:
                user_prompt += (
                    f"\n\n[RETRY] You MUST use ONLY {script} script for {lang_name}. "
                    "Do NOT use Devanagari or other scripts. Do NOT leave any keyword in English or ALL CAPS. "
                    "Keep AND, OR, NOT and quotes unchanged."
                )
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2 if attempt >= 2 else 0.3,
                max_tokens=2048,
            )
            result = (resp.choices[0].message.content or "").strip()
            if not result:
                continue
            is_valid, err = _validate_script(result, lang_code)
            if not is_valid:
                logger.warning(f"Script validation failed for {lang_code} (attempt {attempt + 1}): {err}")
                if attempt < 2:
                    continue
            return result
        except Exception as e:
            logger.error(f"OpenAI {mode} failed for {lang_code} (attempt {attempt + 1}): {e}")
            if attempt == 2:
                return query
    return query


@app.post("/boolean/query/translate")
async def translate_or_transliterate_boolean(request: BooleanQueryTranslateRequest):
    """
    Translate or transliterate a boolean query into multiple languages using OpenAI.

    - **mode**: `"translate"` (semantic — meaning-based) or `"transliterate"` (phonetic — sound-based)
    - **languages**: list of language codes e.g. `["hi","bn","gu"]`
    - **includeQuery**: boolean query string e.g. `\\"HERO\\" OR \\"Honda\\"`

    Requires `OPENAI_API_KEY` env var. Model defaults to `gpt-4o-mini` (override via `BOOLEAN_TRANSLATE_MODEL`).
    """
    mode = (request.mode or "").lower().strip()
    if mode not in ("translate", "transliterate"):
        raise HTTPException(status_code=400, detail="mode must be 'translate' or 'transliterate'")
    if not request.includeQuery or not request.includeQuery.strip():
        raise HTTPException(status_code=400, detail="includeQuery is required")
    if not request.languages:
        raise HTTPException(status_code=400, detail="languages list must not be empty")
    keyword_type = (request.keywordType or "boolean").lower().strip()
    if keyword_type not in ("boolean", "wildcard"):
        raise HTTPException(status_code=400, detail="keywordType must be 'boolean' or 'wildcard'")

    try:
        import openai  # type: ignore  # noqa: F401
    except ImportError:
        raise HTTPException(status_code=503, detail="openai package is not installed. Run: pip install openai")
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY environment variable is not set")

    async def _process_one(lang_code: str) -> Dict[str, Any]:
        lang_name = LANG_ID_TO_NAME.get(lang_code, lang_code)
        script = LANG_SCRIPTS.get(lang_code, "Latin")
        output = await asyncio.to_thread(
            _openai_boolean_sync, request.includeQuery, lang_code, lang_name or lang_code, script, mode, keyword_type
        )
        return {
            "langId": lang_code,
            "langName": LANG_ID_TO_NAME.get(lang_code),
            "query": output,
        }

    results = await asyncio.gather(*[_process_one(lc) for lc in request.languages])
    return {"result": list(results)}


@app.post("/boolean/upload")
async def upload_boolean(request: BooleanUploadRequest):
    """Upload and insert boolean query from JSON data"""
    if not BOOLEAN_TRANSLATOR_AVAILABLE or boolean_transformer is None:
        raise HTTPException(status_code=503, detail="Boolean translator service not available")
    
    try:
        # Convert request to JSON format
        json_data = {
            "companyId": request.companyId,
            "companyName": request.companyName,
            "originalQuery": request.originalQuery,
            "translations": request.translations
        }
        
        # Convert to internal format (strict validation when transformer available)
        try:
            parsed_data = convert_json_format(json_data, transformer=boolean_transformer)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Boolean syntax validation failed: {str(e)}")
        if not parsed_data:
            raise HTTPException(status_code=400, detail="Failed to process boolean query data")
        if request.validate_translation_script and request.translations:
            for lang_code, query_string in request.translations.items():
                q = (query_string or "").strip()
                if q:
                    is_valid, err = _validate_script(q, lang_code)
                    if not is_valid:
                        raise HTTPException(status_code=400, detail=f"Translation script validation failed for lang_{lang_code}: {err}")
        
        company_id = parsed_data.get('companyId')
        company_name = parsed_data.get('companyName')
        
        if not company_id:
            raise HTTPException(status_code=400, detail="Company ID is required")
        
        es_document = parsed_data
        languages = [k for k in es_document.keys() if k.startswith('lang_')]
        language_count = len(languages)
        
        target_index = request.index_name or boolean_transformer.default_index
        
        document_exists = False
        if request.delete_existing:
            try:
                document_exists = boolean_transformer.es_client.exists(
                    index=target_index,
                    id=company_id
                )
                
                if document_exists:
                    boolean_transformer.es_client.delete(
                        index=target_index,
                        id=company_id
                    )
            except Exception as e:
                logger.warning(f"Error checking/deleting existing document: {e}")
        
        success = boolean_transformer.upsert_data_to_es_single(
            es_document,
            company_id,
            target_index
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to insert document into Elasticsearch")
        
        return {
            "success": True,
            "message": "Document inserted successfully",
            "companyId": company_id,
            "companyName": company_name,
            "index": target_index,
            "documentExists": document_exists,
            "deletedExisting": document_exists and request.delete_existing,
            "languages": language_count,
            "languageCodes": languages
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing boolean query: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error processing boolean query: {str(e)}")


@app.post("/boolean/upload-wildcard")
async def upload_wildcard(request: WildcardUploadRequest):
    """
    Upload wildcard keywords per language and save as a single ES document with _id = {companyId}_wildcard.
    Each lang_* value can be an array of strings or a comma-separated string; terms are converted to OR-quoted
    boolean and parsed to ES DSL with strict validation.
    """
    if not BOOLEAN_TRANSLATOR_AVAILABLE or boolean_transformer is None:
        raise HTTPException(status_code=503, detail="Boolean translator service not available")

    body = request.model_dump()
    lang_keys = [k for k in body if k.startswith("lang_") and k != "lang_"]
    if not lang_keys:
        raise HTTPException(
            status_code=400,
            detail="At least one lang_<code> field is required (e.g. lang_en, lang_hi)",
        )

    company_id = (request.companyId or "").strip()
    company_name = (request.companyName or "").strip()
    if not company_id:
        raise HTTPException(status_code=400, detail="companyId is required")

    target_index = request.index_name or boolean_transformer.default_index
    if not target_index:
        raise HTTPException(status_code=500, detail="No default Elasticsearch index configured")

    wildcard_doc = {"companyId": company_id, "companyName": company_name or company_id}
    errors = []

    for lang_key in lang_keys:
        raw = body[lang_key]
        if isinstance(raw, str):
            terms = [t.strip() for t in raw.split(",") if t.strip()]
        elif isinstance(raw, list):
            terms = [str(t).strip() for t in raw if str(t).strip()]
        else:
            errors.append(f"{lang_key}: value must be a string or array of strings")
            continue
        if not terms:
            errors.append(f"{lang_key}: at least one non-empty keyword required")
            continue
        query_string = _build_wildcard_query_quoted(terms)
        lang_code = lang_key.replace("lang_", "", 1)
        if request.validate_translation_script and lang_code != "en":
            is_valid, err = _validate_script(query_string, lang_code)
            if not is_valid:
                errors.append(f"{lang_key}: {err}")
                continue
        try:
            dsl = boolean_transformer.parse_boolean_query_to_elasticsearch(query_string)
            wildcard_doc[lang_key] = dsl
        except Exception as e:
            errors.append(f"{lang_key}: {str(e)}")

    if errors:
        raise HTTPException(
            status_code=400,
            detail={"message": "Validation or parse errors for wildcard keywords", "errors": errors},
        )

    doc_id = f"{company_id}_wildcard"
    try:
        if request.delete_existing:
            try:
                boolean_transformer.es_client.delete(index=target_index, id=doc_id, ignore=[404])
            except Exception:
                pass  # ignore 404 or other delete errors
        boolean_transformer.es_client.index(index=target_index, id=doc_id, body=wildcard_doc)
    except Exception as e:
        logger.error(f"Error indexing wildcard document: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to index wildcard document: {str(e)}")

    return {
        "success": True,
        "companyId": company_id,
        "index": target_index,
        "documentId": doc_id,
        "languages": [k.replace("lang_", "") for k in lang_keys],
    }


@app.get("/boolean/company/{company_id}")
async def get_company_boolean(company_id: str, index_name: Optional[str] = None):
    """Get a boolean query document by company ID"""
    if not BOOLEAN_TRANSLATOR_AVAILABLE or boolean_transformer is None:
        raise HTTPException(status_code=503, detail="Boolean translator service not available")
    
    try:
        target_index = index_name or boolean_transformer.default_index
        
        result = boolean_transformer.es_client.get(
            index=target_index,
            id=company_id
        )
        
        return {
            "success": True,
            "companyId": company_id,
            "index": target_index,
            "document": result['_source']
        }
        
    except Exception as e:
        logger.error(f"Error getting document: {e}")
        raise HTTPException(status_code=404, detail=f"Document with company ID '{company_id}' not found")


@app.delete("/boolean/company/{company_id}")
async def delete_company_boolean(company_id: str, index_name: Optional[str] = None):
    """Delete a boolean query document by company ID"""
    if not BOOLEAN_TRANSLATOR_AVAILABLE or boolean_transformer is None:
        raise HTTPException(status_code=503, detail="Boolean translator service not available")
    
    try:
        target_index = index_name or boolean_transformer.default_index
        
        document_exists = boolean_transformer.es_client.exists(
            index=target_index,
            id=company_id
        )
        
        if not document_exists:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": f"Document with company ID '{company_id}' not found",
                    "companyId": company_id,
                    "index": target_index
                }
            )
        
        result = boolean_transformer.es_client.delete(
            index=target_index,
            id=company_id
        )
        
        return {
            "success": True,
            "message": f"Document with company ID '{company_id}' deleted successfully",
            "companyId": company_id,
            "index": target_index,
            "result": result
        }
        
    except Exception as e:
        logger.error(f"Error deleting document: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting document: {str(e)}")


@app.get("/boolean/espreview/health")
async def espreview_health_check():
    """Check esPreview system health"""
    if not BOOLEAN_TRANSLATOR_AVAILABLE or espreview_engine is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "message": "esPreview service not available"
            }
        )
    
    try:
        health = espreview_engine.health_check()
        return health
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "message": f"esPreview health check failed: {str(e)}"
            }
        )


def _safe_parse_datetime(value: Any) -> Optional[datetime]:
    """Parse common datetime/date values safely."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue
    return None


def _article_sort_key(index_name: str, article: Dict[str, Any]) -> tuple:
    """Build deterministic latest-first sort key per index."""
    if index_name == "printarticleindex":
        dt = _safe_parse_datetime(article.get("articleDate"))
        ts = dt.timestamp() if dt else float("-inf")
        return (ts, str(article.get("id", "")))

    if index_name == "socialfeedindex":
        dt_primary = _safe_parse_datetime(article.get("feedDateTime"))
        dt_secondary = _safe_parse_datetime(article.get("feedDate"))
        date_num = article.get("articleDateNumber")
        try:
            num_val = int(date_num) if date_num is not None else -1
        except (TypeError, ValueError):
            num_val = -1
        ts_primary = dt_primary.timestamp() if dt_primary else float("-inf")
        ts_secondary = dt_secondary.timestamp() if dt_secondary else float("-inf")
        return (ts_primary, ts_secondary, num_val, str(article.get("id", "")))

    # Generic fallback
    return (str(article.get("id", "")),)


@app.post("/boolean/espreview/query")
async def execute_query(request: QueryRequest):
    """Execute a boolean query using esPreview"""
    if not BOOLEAN_TRANSLATOR_AVAILABLE or espreview_engine is None or espreview_config is None:
        raise HTTPException(status_code=503, detail="esPreview service not available")
    
    try:
        target_indexes = None
        
        if request.limit and espreview_config:
            espreview_config.max_results_per_index = request.limit
        
        # Debug: Log the request parameters
        logger.info(f"🔍 API RECEIVED REQUEST - include_content: {request.include_content} (type: {type(request.include_content)})")
        logger.info(f"🔍 API RECEIVED REQUEST - query: {request.query[:100] if len(request.query) > 100 else request.query}")
        
        result = espreview_engine.execute_query(request.query, target_indexes, request.include_content)
        
        # Debug: Log what we got back
        num_articles = 0
        if result.index_results:
            first_result = list(result.index_results.values())[0]
            num_articles = len(first_result.articles) if first_result.articles else 0
        logger.info(f"🔍 API GOT RESULT - First index result has {num_articles} articles")
        if result.index_results:
            first_idx = list(result.index_results.keys())[0]
            first_res = result.index_results[first_idx]
            if first_res.articles and len(first_res.articles) > 0:
                first_article = first_res.articles[0]
                article_type = type(first_article).__name__
                article_keys = list(first_article.keys()) if isinstance(first_article, dict) else 'N/A'
                logger.info(f"🔍 API FIRST ARTICLE - type: {article_type}, keys: {article_keys}")
        # Build response
        response_data = {
            "success": result.success,
            "total_matches": result.total_matches,
            "execution_time_ms": result.execution_time_ms,
            "query_info": result.query_info,
            "index_results": {},
            "errors": result.errors
        }
        
        # Process each index result
        for idx, res in result.index_results.items():
            processed_articles = []
            for article in res.articles:
                if isinstance(article, dict):
                    processed_articles.append(article)
                else:
                    # If it's just an ID string, create minimal object
                    processed_articles.append({"id": str(article)})

            # Enforce latest-first ordering in API response payload.
            # This safeguards output even when source data has mixed date quality.
            if processed_articles:
                processed_articles = sorted(
                    processed_articles,
                    key=lambda a: _article_sort_key(idx, a),
                    reverse=True,
                )
            sorted_article_ids = [a.get("id") for a in processed_articles if a.get("id") is not None]
            
            response_data["index_results"][idx] = {
                "total_hits": res.total_hits,
                "article_ids": sorted_article_ids,
                "articles": processed_articles,
                "execution_time_ms": res.execution_time_ms,
                "errors": res.errors
            }
        
        return response_data
        
    except Exception as e:
        logger.error(f"Error executing query: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error executing query: {str(e)}")


@app.post("/boolean/espreview/query/file")
async def execute_query_from_file(
    file: UploadFile = File(...),
    indexes: Optional[str] = Form(None),
    limit: Optional[int] = Form(None),
    language: Optional[str] = Form(None)
):
    """Execute a boolean query from a JSON file using esPreview"""
    if not BOOLEAN_TRANSLATOR_AVAILABLE or espreview_engine is None or espreview_config is None:
        raise HTTPException(status_code=503, detail="esPreview service not available")
    
    temp_file_path = None
    try:
        if not file.filename.endswith('.json'):
            raise HTTPException(status_code=400, detail="Only .json files are supported")
        
        with tempfile.NamedTemporaryFile(mode='w+b', suffix='.json', delete=False) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        try:
            with open(temp_file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            target_lang = language or 'en'
            
            if 'translations' in json_data:
                translations = json_data.get('translations', {})
                if target_lang in translations:
                    query = translations[target_lang].strip('"')
                else:
                    query = translations.get('en', list(translations.values())[0]).strip('"')
            elif f'lang_{target_lang}' in json_data:
                lang_data = json_data[f'lang_{target_lang}']
                if isinstance(lang_data, dict):
                    query = lang_data.get('match_phrase', {}).get('content', {}).get('query', '')
                else:
                    query = lang_data.strip()
            else:
                lang_fields = [k for k in json_data.keys() if k.startswith('lang_')]
                if lang_fields:
                    lang_data = json_data[lang_fields[0]]
                    if isinstance(lang_data, dict):
                        query = lang_data.get('match_phrase', {}).get('content', {}).get('query', '')
                    else:
                        query = lang_data.strip()
                else:
                    raise HTTPException(status_code=400, detail="No language-specific queries found in JSON file")
            
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON format: {str(e)}")
        
        if not query:
            raise HTTPException(status_code=400, detail="Query file is empty")
        
        target_indexes = None
        
        if limit and espreview_config:
            espreview_config.max_results_per_index = limit
        
        result = espreview_engine.execute_query(query, target_indexes, False)
        
        return {
            "success": result.success,
            "total_matches": result.total_matches,
            "execution_time_ms": result.execution_time_ms,
            "query": query,
            "language": target_lang,
            "query_info": result.query_info,
            "index_results": {
                idx: {
                    "total_hits": res.total_hits,
                    "article_ids": res.article_ids,
                    "articles": res.articles,
                    "execution_time_ms": res.execution_time_ms,
                    "errors": res.errors
                }
                for idx, res in result.index_results.items()
            },
            "errors": result.errors
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing query from file: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error executing query from file: {str(e)}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                logger.warning(f"Could not delete temporary file: {e}")


@app.post("/boolean/espreview/company/{company_id}")
async def execute_company_query(company_id: str, request: CompanyQueryRequest):
    """Execute a company query using esPreview"""
    if not BOOLEAN_TRANSLATOR_AVAILABLE or espreview_engine is None or espreview_config is None:
        raise HTTPException(status_code=503, detail="esPreview service not available")
    
    try:
        target_indexes = None
        
        if request.limit and espreview_config:
            espreview_config.max_results_per_index = request.limit
        
        result = espreview_engine.execute_company_query(company_id, request.language, target_indexes, request.include_content)
        
        return {
            "success": result.success,
            "total_matches": result.total_matches,
            "execution_time_ms": result.execution_time_ms,
            "query_info": result.query_info,
            "index_results": {
                idx: {
                    "total_hits": res.total_hits,
                    "article_ids": res.article_ids,
                    "articles": res.articles,
                    "execution_time_ms": res.execution_time_ms,
                    "errors": res.errors
                }
                for idx, res in result.index_results.items()
            },
            "errors": result.errors
        }
        
    except Exception as e:
        logger.error(f"Error executing company query: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error executing company query: {str(e)}")


# ============================================================================
# BACKTRACKING ENDPOINTS
# ============================================================================

@app.get("/backtracking/health")
async def backtracking_health_check():
    """Check backtracking system health"""
    if not BACKTRACKING_AVAILABLE:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unavailable",
                "message": "Backtracking module not available"
            }
        )
    
    try:
        # Check dependencies - use refresh_service's ES client if available
        es_healthy = False
        if refresh_service and hasattr(refresh_service, 'es_client') and refresh_service.es_client:
            es_healthy = refresh_service.es_client.ping()
        elif boolean_transformer and hasattr(boolean_transformer, 'es_client') and boolean_transformer.es_client:
            es_healthy = boolean_transformer.es_client.ping()
        
        return {
            "status": "healthy" if es_healthy else "unhealthy",
            "message": "Backtracking system is available",
            "backtracking_module": "available",
            "elasticsearch": es_healthy,
            "dependencies": {
                "mongodb": "configured",
                "elasticsearch": "configured",
                "redis_job_queue": "configured" if _redis_job_queue_configured() else "disabled"
            },
            "job_queue_backend": get_job_queue_backend_label()
        }
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "message": f"Backtracking health check failed: {str(e)}"
            }
        )

@app.post("/backtracking/run")
async def run_backtracking(request: BacktrackingRequest, background_tasks: BackgroundTasks = None, allow_concurrent: bool = False):
    """
    Start backtracking job (returns immediately with job_id)
    
    **API acts as receptionist**: Just receives the request, persists it in MongoDB,
    and mirrors the job ID into the Redis-backed pending queue when available. The
    backtracking worker (running continuously) will automatically pick up and process
    pending jobs.
    
    - Job metadata is stored in MongoDB with status PENDING
    - Worker polls every 5 seconds (configurable via BACKTRACKING_POLL_INTERVAL) using
      the Redis FIFO index when configured, or direct MongoDB lookups as a fallback
    - Worker processes jobs one at a time in FIFO order (oldest first)
    - No concurrent job conflicts - worker handles sequential processing automatically
    
    Args:
        request: BacktrackingRequest object containing date range, company IDs, and processing settings
        background_tasks: FastAPI background tasks (not used - worker handles processing)
        allow_concurrent: Not used - worker processes sequentially (kept for backward compatibility)
    
    Returns:
        Dict with job_id, status (PENDING), and confirmation message
    """
    if not BACKTRACKING_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backtracking module not available"
        )
    
    # API acts as receptionist - just queue the request, no processing checks needed
    # Worker handles all processing logic
    try:
        # Prepare config dictionary
        config_dict = {
            "start_date": request.start_date,
            "end_date": request.end_date,
            "company_ids": request.company_ids,
            "language": request.language,
            "batch_size": request.batch_size,
            "max_workers": request.max_workers,
            "dry_run": request.dry_run,
            "process_print": request.process_print,
            "process_online": request.process_online,
            "enable_checkpoints": request.enable_checkpoints,
            "chunk_days": request.chunk_days,
            "resume": request.resume,
            "checkpoint_file": request.checkpoint_file,
            "use_mongo_checkpoints": request.use_mongo_checkpoints,
            "checkpoint_id": request.checkpoint_id,
            "auto_resume_on_crash": request.auto_resume_on_crash,
            "max_auto_retries": request.max_auto_retries,
            "retry_delay_seconds": request.retry_delay_seconds,
            "tag_workers": request.tag_workers,
            "mongo_bulk_batch_size": request.mongo_bulk_batch_size,
            "progress_log_interval": request.progress_log_interval,
            "es_page_size": request.es_page_size,
            "es_keepalive_minutes": request.es_keepalive_minutes
        }
        
        # Create job in MongoDB with PENDING status (Redis queue mirrors ordering when configured)
        # API acts as receptionist - just queues the request, worker will process it
        job_id = create_backtracking_job(config_dict)
        
        job = get_backtracking_job(job_id)
        queue_label = get_job_queue_backend_label()
        backtracking_logger.info(f"Backtracking job {job_id} queued in {queue_label} - worker will process it automatically")
        
        return {
            "success": True,
            "message": "Backtracking job queued successfully - worker will process it automatically",
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "status_url": f"/backtracking/job/{job_id}",
            "created_at": job["created_at"] if job else datetime.utcnow().isoformat(),
            "note": f"Job is queued in {queue_label}. The backtracking worker will pick it up and process it automatically."
        }
        
    except Exception as e:
        logger.error(f"Error starting backtracking job: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error starting backtracking job: {str(e)}"
        )

@app.get("/backtracking/job/{job_id}")
async def get_backtracking_job_status(job_id: str):
    """
    Get status of a backtracking job
    
    Args:
        job_id: The job ID returned from /backtracking/run
    
    Returns:
        Dict with job status, progress, and results (if completed)
    """
    if not BACKTRACKING_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backtracking module not available"
        )
    
    job = get_backtracking_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )
    
    response = {
        "job_id": job["job_id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "progress": job["progress"],
        "config": {
            "start_date": job["config"]["start_date"],
            "end_date": job["config"]["end_date"],
            "company_ids": job["config"]["company_ids"]
        }
    }
    
    if job["status"] == JobStatus.COMPLETED and job.get("results"):
        response["results"] = job["results"]
    
    if job["status"] == JobStatus.FAILED and job.get("error"):
        response["error"] = job["error"]
    
    return response

@app.get("/backtracking/jobs")
async def list_backtracking_jobs(status_filter: Optional[str] = None, limit: int = 50):
    """
    List all backtracking jobs
    
    Args:
        status_filter: Optional filter by status (pending, running, completed, failed, cancelled)
        limit: Maximum number of jobs to return
    
    Returns:
        List of jobs with their status
    """
    if not BACKTRACKING_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backtracking module not available"
        )
    
    try:
        db = get_mongo_job_db()
        query = {}
        if status_filter:
            query["status"] = status_filter
        
        # Fetch jobs from MongoDB
        cursor = db.backtrack.find(query).sort("created_at", -1).limit(limit)
        jobs_list = []
        for job_doc in cursor:
            # Convert ObjectId to string
            if "_id" in job_doc:
                job_doc["job_id"] = str(job_doc["_id"])
                del job_doc["_id"]
            jobs_list.append(job_doc)
    except Exception as e:
        logger.error(f"Error listing backtracking jobs from MongoDB: {e}")
        jobs_list = []
    
    # Return simplified job info
    return {
        "total": len(jobs_list),
        "jobs": [
            {
                "job_id": job["job_id"],
                "status": job["status"],
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
                "progress": job.get("progress", {})
            }
            for job in jobs_list
        ]
    }

@app.delete("/backtracking/job/{job_id}")
async def cancel_backtracking_job(job_id: str):
    """
    Cancel a backtracking job
    
    Note: Currently running jobs cannot be cancelled, only pending jobs.
    
    Args:
        job_id: The job ID to cancel
    
    Returns:
        Dict with cancellation status
    """
    if not BACKTRACKING_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backtracking module not available"
        )
    
    job = get_backtracking_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )
    
    if job["status"] == JobStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot cancel a running job. Only pending jobs can be cancelled."
        )
    
    if job["status"] in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is already {job['status']}"
        )
    
    update_backtracking_job_status(job_id, JobStatus.CANCELLED)
    
    return {
        "success": True,
        "message": f"Job {job_id} cancelled",
        "job_id": job_id,
        "status": JobStatus.CANCELLED
    }

@app.get("/backtracking/checkpoint/{checkpoint_id}")
async def get_mongo_checkpoint_status(checkpoint_id: str):
    """
    Get status of a MongoDB checkpoint
    
    Args:
        checkpoint_id: The checkpoint ID
    
    Returns:
        Dict with checkpoint status information
    """
    if not BACKTRACKING_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backtracking module not available"
        )
    
    try:
        db = get_mongo_job_db()
        checkpoint_collection = db.backtrackingCheckpoints
        
        checkpoint_doc = checkpoint_collection.find_one({"_id": checkpoint_id})
        
        if not checkpoint_doc:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={
                    "exists": False,
                    "message": f"Checkpoint not found: {checkpoint_id}"
                }
            )
        
        # Calculate progress
        config = checkpoint_doc.get("config", {})
        start_date = config.get("start_date")
        end_date = config.get("end_date")
        last_processed = checkpoint_doc.get("last_processed_date", start_date)
        chunks_processed = checkpoint_doc.get("chunks_processed", 0)
        
        # Estimate total chunks
        from datetime import datetime as dt
        if start_date and end_date:
            start_dt = dt.strptime(start_date, '%Y-%m-%d')
            end_dt = dt.strptime(end_date, '%Y-%m-%d')
            chunk_days = config.get("chunk_days", 7)
            total_days = (end_dt - start_dt).days + 1
            total_chunks = (total_days + chunk_days - 1) // chunk_days
            progress_percent = (chunks_processed / total_chunks * 100) if total_chunks > 0 else 0
        else:
            total_chunks = None
            progress_percent = None
        
        return {
            "exists": True,
            "checkpoint_id": checkpoint_id,
            "checkpoint_time": checkpoint_doc.get("checkpoint_time"),
            "updated_at": checkpoint_doc.get("updated_at"),
            "progress": {
                "chunks_processed": chunks_processed,
                "total_chunks": total_chunks,
                "progress_percent": round(progress_percent, 2) if progress_percent is not None else None,
                "last_processed_date": last_processed,
                "start_date": start_date,
                "end_date": end_date
            },
            "results": {
                "total_articles_processed": checkpoint_doc.get("total_articles_processed", 0),
                "total_social_feeds_processed": checkpoint_doc.get("total_social_feeds_processed", 0),
                "total_tags_created": checkpoint_doc.get("total_tags_created", 0)
            },
            "config": config,
            "errors": checkpoint_doc.get("errors", [])
        }
        
    except Exception as e:
        logger.error(f"Error reading MongoDB checkpoint: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reading checkpoint: {str(e)}"
        )

@app.get("/backtracking/status/{checkpoint_file}")
async def get_checkpoint_status(checkpoint_file: str):
    """
    Get status of a checkpoint file
    
    Args:
        checkpoint_file: Name of the checkpoint file
    
    Returns:
        Dict with checkpoint status information
    """
    if not BACKTRACKING_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backtracking module not available"
        )
    
    try:
        checkpoint_path = Path(checkpoint_file)
        
        if not checkpoint_path.exists():
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={
                    "exists": False,
                    "message": f"Checkpoint file not found: {checkpoint_file}"
                }
            )
        
        # Load checkpoint data
        with open(checkpoint_path, 'r') as f:
            checkpoint_data = json.load(f)
        
        # Calculate progress
        config = checkpoint_data.get("config", {})
        start_date = config.get("start_date")
        end_date = config.get("end_date")
        last_processed = checkpoint_data.get("last_processed_date", start_date)
        chunks_processed = checkpoint_data.get("chunks_processed", 0)
        
        # Estimate total chunks
        from datetime import datetime as dt
        if start_date and end_date:
            start_dt = dt.strptime(start_date, '%Y-%m-%d')
            end_dt = dt.strptime(end_date, '%Y-%m-%d')
            chunk_days = config.get("chunk_days", 7)
            total_days = (end_dt - start_dt).days + 1
            total_chunks = (total_days + chunk_days - 1) // chunk_days
            progress_percent = (chunks_processed / total_chunks * 100) if total_chunks > 0 else 0
        else:
            total_chunks = None
            progress_percent = None
        
        return {
            "exists": True,
            "checkpoint_file": checkpoint_file,
            "checkpoint_time": checkpoint_data.get("checkpoint_time"),
            "progress": {
                "chunks_processed": chunks_processed,
                "total_chunks": total_chunks,
                "progress_percent": round(progress_percent, 2) if progress_percent is not None else None,
                "last_processed_date": last_processed,
                "start_date": start_date,
                "end_date": end_date
            },
            "results": {
                "total_articles_processed": checkpoint_data.get("total_articles_processed", 0),
                "total_social_feeds_processed": checkpoint_data.get("total_social_feeds_processed", 0),
                "total_tags_created": checkpoint_data.get("total_tags_created", 0)
            },
            "config": config,
            "errors": checkpoint_data.get("errors", [])
        }
        
    except Exception as e:
        logger.error(f"Error reading checkpoint: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reading checkpoint: {str(e)}"
        )

@app.post("/backtracking/resume")
async def resume_backtracking(request: BacktrackingRequest, background_tasks: BackgroundTasks = None, allow_concurrent: bool = False):
    """
    Resume backtracking from a checkpoint (returns immediately with job_id)
    
    **API acts as receptionist**: Just receives the request, writes it to MongoDB, and mirrors
    the job ID into the Redis FIFO queue when available. The worker then picks it up automatically.
    
    - Job metadata stored in MongoDB with status PENDING
    - Worker polls Redis (if configured) or MongoDB every few seconds and processes jobs sequentially
    - No concurrent job conflicts - worker handles sequential processing automatically
    
    Args:
        request: BacktrackingRequest with checkpoint_file or checkpoint_id specified
        background_tasks: FastAPI background tasks (not used - worker handles processing)
        allow_concurrent: Not used - worker processes sequentially (kept for backward compatibility)
    
    Returns:
        Dict with job_id, status (PENDING), and confirmation message
    """
    if not BACKTRACKING_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backtracking module not available"
        )
    
    if not request.checkpoint_file and not request.checkpoint_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either checkpoint_file or checkpoint_id is required for resume operation"
        )
    
    # If not using worker (old mode), check for running jobs
    use_worker = os.getenv("BACKTRACKING_USE_WORKER", "true").lower() == "true"
    running_jobs = []
    
    if not use_worker and not allow_concurrent:
        running_jobs = get_running_backtracking_jobs()
        if running_jobs:
            running_job_ids = [job.get("job_id", "unknown") for job in running_jobs]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "A backtracking job is already running",
                    "message": f"Cannot resume backtracking job while {len(running_jobs)} job(s) are running. "
                               f"Wait for current job(s) to complete or set allow_concurrent=true (not recommended).",
                    "running_jobs": running_job_ids,
                    "suggestion": "Check status with GET /backtracking/jobs?status_filter=running"
                }
            )
    
    try:
        # Prepare config dictionary - force resume=True and enable_checkpoints=True
        config_dict = {
            "start_date": request.start_date,
            "end_date": request.end_date,
            "company_ids": request.company_ids,
            "language": request.language,
            "batch_size": request.batch_size,
            "max_workers": request.max_workers,
            "dry_run": request.dry_run,
            "process_print": request.process_print,
            "process_online": request.process_online,
            "enable_checkpoints": True,  # Force enable for resume
            "chunk_days": request.chunk_days,
            "resume": True,  # Force resume mode
            "checkpoint_file": request.checkpoint_file,
            "use_mongo_checkpoints": request.use_mongo_checkpoints,
            "checkpoint_id": request.checkpoint_id,
            "auto_resume_on_crash": request.auto_resume_on_crash,
            "max_auto_retries": request.max_auto_retries,
            "retry_delay_seconds": request.retry_delay_seconds,
            "tag_workers": request.tag_workers,
            "mongo_bulk_batch_size": request.mongo_bulk_batch_size,
            "progress_log_interval": request.progress_log_interval,
            "es_page_size": request.es_page_size,
            "es_keepalive_minutes": request.es_keepalive_minutes
        }
        
        # Create job in MongoDB with PENDING status
        # API acts as receptionist - just queues the request, worker will process it
        job_id = create_backtracking_job(config_dict)
        
        job = get_backtracking_job(job_id)
        queue_label = get_job_queue_backend_label()
        backtracking_logger.info(f"Backtracking resume job {job_id} queued in {queue_label} - worker will process it automatically")
        
        return {
            "success": True,
            "message": "Backtracking resume job queued successfully - worker will process it automatically",
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "status_url": f"/backtracking/job/{job_id}",
            "created_at": job["created_at"] if job else datetime.utcnow().isoformat(),
            "resumed": True,
            "checkpoint_file": request.checkpoint_file,
            "checkpoint_id": request.checkpoint_id,
            "note": f"Job is queued in {queue_label}. The backtracking worker will pick it up and process it automatically."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting resume backtracking job: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error starting resume backtracking job: {str(e)}"
        )


# ============================================================================
# ELASTIC TAGGING API ENDPOINTS
# ============================================================================

if ELASTIC_TAGGING_AVAILABLE:
    @app.get("/tagging/health")
    async def tagging_health():
        """Check elasticTagging system health."""
        try:
            es_status = bool(es.ping())
            settings = get_settings()

            return {
                "status": "ok" if es_status else "degraded",
                "elasticsearch": es_status,
                "index": INDEX_NAME,
                "useOptimizedByDefault": settings.default_use_optimized,
                "service": "elasticTaggingAPI",
            }
        except Exception as e:
            logger.error(f"Tagging health check failed: {e}")
            return {
                "status": "degraded",
                "elasticsearch": False,
                "error": str(e),
                "service": "elasticTaggingAPI",
            }

    @app.post("/tagging/tag", response_model=TaggingResponse, status_code=status.HTTP_200_OK)
    async def tag_article_endpoint(payload: TaggingRequest):
        """
        Tag an article and get company/keyword matches.

        This endpoint uses the elasticTagging percolator index to find matching companies
        and keywords in the provided article content.
        """
        try:
            settings = get_settings()
            use_optimized = payload.use_optimized
            if use_optimized is None:
                use_optimized = settings.default_use_optimized

            tags, duration_ms, used_optimized, raw_tags = execute_tagging(
                payload.article_id,
                payload.headline,
                payload.summary,
                payload.content,
                payload.language,
                use_optimized,
            )
        except Exception as exc:
            logger.exception("Tagging failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Tagging failed",
            ) from exc

        persistence_result: Optional[PersistenceResult] = None
        if payload.write_to_db:
            if payload.article_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="articleId is required when writeToDb is true.",
                )
            try:
                persistence_result = persist_tagging_results(
                    payload.article_id,
                    payload.headline,
                    payload.summary,
                    payload.content,
                    payload.language,
                    raw_tags,
                )
            except PersistenceError as exc:
                logger.exception("Persistence failed")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=str(exc),
                ) from exc

        return TaggingResponse(
            articleId=payload.article_id,
            tags=tags,
            tagCount=len(tags),
            durationMs=duration_ms,
            usedOptimized=used_optimized,
            persisted=persistence_result.persisted if persistence_result else False,
            postgresUpdateType=persistence_result.postgres_update_type if persistence_result else None,
            mongoUpserted=persistence_result.mongo_upserted if persistence_result else None,
        )

    @app.post(
        "/tagging/tag/company-keywords",
        response_model=CompanyKeywordLookupResponse,
        status_code=status.HTTP_200_OK,
    )
    async def company_keyword_lookup_endpoint(payload: CompanyKeywordLookupRequest):
        """
        Get keywords for a specific company from article content.

        This endpoint tags the article and returns only the keywords for the specified company,
        including which sections (headline/content/summary) triggered each keyword.
        """
        try:
            settings = get_settings()
            use_optimized = payload.use_optimized
            if use_optimized is None:
                use_optimized = settings.default_use_optimized

            tags, _, _, _ = execute_tagging(
                article_id=None,
                headline=payload.headline,
                summary=payload.summary,
                content=payload.content,
                language=payload.language,
                use_optimized=use_optimized,
            )
        except Exception as exc:
            logger.exception("Company keyword lookup failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Tagging failed",
            ) from exc

        requested_company_id = str(payload.company_id).strip()
        matching_tag = next(
            (
                tag
                for tag in tags
                if tag.company_id is not None
                and str(tag.company_id).strip() == requested_company_id
            ),
            None,
        )

        if matching_tag is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No tags found for supplied companyId.",
            )

        return CompanyKeywordLookupResponse(
            companyId=matching_tag.company_id,
            companyName=matching_tag.company_name,
            keywords=matching_tag.keywords,
            keywordSources=matching_tag.keyword_sources,
        )


@app.get("/boolean/espreview/companies")
async def list_companies(limit: int = 100):
    """List available companies"""
    if not BOOLEAN_TRANSLATOR_AVAILABLE or espreview_engine is None:
        raise HTTPException(status_code=503, detail="esPreview service not available")
    
    try:
        companies = espreview_engine.list_companies(limit)
        return {
            "success": True,
            "count": len(companies),
            "companies": companies
        }
    except Exception as e:
        logger.error(f"Error listing companies: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error listing companies: {str(e)}")


# ============================================================================
# REFRESH ENDPOINTS
# ============================================================================

async def background_refresh_social_batch(social_feed_ids: List[int], max_workers: int, login_name: Optional[str], service: RefreshService):
    """Background task for social feed batch refresh with chunked processing."""
    try:
        logger.info(f"Starting background social batch refresh for {len(social_feed_ids)} feeds | login_name={login_name or 'N/A'}")

        # Process the full batch in one optimized call; the service will parallelize internally
        results = await service.refresh_social_feeds_batch(social_feed_ids, max_workers)
        logger.info(
            f"Completed background social batch refresh: {results.successful_count} successful, {results.failed_count} failed | login_name={login_name or 'N/A'}"
        )
        
    except Exception as e:
        logger.error(f"Background social batch refresh failed: {e} | login_name={login_name or 'N/A'}")


# Batch endpoints must come before single endpoints to avoid routing conflicts
@app.post("/refresh/social/batch", response_model=BatchRefreshResponse, status_code=202)
async def refresh_batch_social(
    request: Request,
    background_tasks: BackgroundTasks,
    service: RefreshService = Depends(get_refresh_service)
):
    """Refresh multiple social feed documents in Elasticsearch.
    
    Accepts either:
    - Simple array format: [12345, 12346]
    - Object format: {"social_feed_ids": [12345], "max_workers": 60}
    """
    start_time = time.time()
    
    try:
        # Parse request body - support both array and object formats
        body = await request.json()
        social_feed_ids_safe = []
        max_workers = DEFAULT_BATCH_MAX_WORKERS

        # Pull caller identity from headers (optional)
        login_name = (
            request.headers.get("X-Login-Name")
            or request.headers.get("X-User")
            or request.headers.get("X-Username")
        )
        
        # Check if body is a simple array
        if isinstance(body, list):
            social_feed_ids_safe = body
        # Check if body is an object with social_feed_ids or systemSocialFeedIds
        elif isinstance(body, dict):
            logger.debug(f"Parsed as dict format. Keys: {list(body.keys())}")
            # Extract login_name if present
            login_name = body.get("login_name") or login_name
            # Check for social_feed_ids, systemSocialFeedIds, or socialFeedIds
            social_feed_ids_raw = None
            if "social_feed_ids" in body:
                social_feed_ids_raw = body.get("social_feed_ids")
                logger.debug("Found 'social_feed_ids' field")
            elif "systemSocialFeedIds" in body:
                social_feed_ids_raw = body.get("systemSocialFeedIds")
                logger.debug("Found 'systemSocialFeedIds' field")
            elif "socialFeedIds" in body:
                social_feed_ids_raw = body.get("socialFeedIds")
                logger.debug("Found 'socialFeedIds' field")
            
            if social_feed_ids_raw is not None:
                # Handle None, empty list, or actual list
                if social_feed_ids_raw is None:
                    social_feed_ids_safe = []
                elif isinstance(social_feed_ids_raw, list):
                    social_feed_ids_safe = social_feed_ids_raw
                else:
                    # Try to convert to list if it's a single value
                    social_feed_ids_safe = [social_feed_ids_raw] if social_feed_ids_raw else []
                requested_workers = body.get("max_workers", DEFAULT_BATCH_MAX_WORKERS)
                max_workers = requested_workers or DEFAULT_BATCH_MAX_WORKERS
                logger.debug(f"Found social feed IDs in body: {len(social_feed_ids_safe) if social_feed_ids_safe else 0} IDs")
            else:
                # Try to parse as BatchRefreshRequest
                try:
                    batch_request = BatchRefreshRequest(**body)
                    social_feed_ids_safe = batch_request.social_feed_ids or []
                    max_workers = batch_request.max_workers or DEFAULT_BATCH_MAX_WORKERS
                except Exception as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid request format. Expected array of IDs or object with 'social_feed_ids' field. Error: {str(e)}"
                    )
        else:
            raise HTTPException(
                status_code=400,
                detail="Invalid request format. Expected array of IDs or object with 'social_feed_ids' field."
            )
        
        # Validate social_feed_ids
        if not social_feed_ids_safe:
            raise HTTPException(
                status_code=400,
                detail="No social feed IDs provided. Please provide an array of social feed IDs or an object with 'social_feed_ids' field."
            )
        
        if not isinstance(social_feed_ids_safe, list):
            raise HTTPException(
                status_code=400,
                detail="social_feed_ids must be an array of integers."
            )
        
        # Validate all IDs are integers
        try:
            social_feed_ids_safe = [int(id) for id in social_feed_ids_safe]
        except (ValueError, TypeError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"All social feed IDs must be integers. Error: {str(e)}"
            )
        
        max_workers = resolve_max_workers(max_workers)
        
        logger.info(
            f"Received request: batch social refresh | count={len(social_feed_ids_safe)} | "
            f"max_workers={max_workers} | login_name={login_name or 'N/A'}"
        )
        # Always run in background and return immediately (optimistic)
        asyncio.create_task(background_refresh_social_batch(
            social_feed_ids_safe, 
            max_workers,
            login_name,
            service
        ))
        logger.info(
            f"Scheduled background social batch refresh | count={len(social_feed_ids_safe)} | login_name={login_name or 'N/A'}"
        )

        return BatchRefreshResponse(
            total_requested=len(social_feed_ids_safe),
            successful_count=0,
            failed_count=0,
            results=[],
            processing_time=time.time() - start_time,
            timestamp=datetime.utcnow()
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Log full exception with traceback for debugging
        error_msg = str(e) if str(e) else repr(e)
        logger.error(f"Error in batch social refresh: {error_msg} | login_name={login_name or 'N/A'}", exc_info=True)
        
        # Provide more detailed error message
        if not error_msg:
            error_msg = f"Unknown error: {type(e).__name__}"
        
        raise HTTPException(
            status_code=500,
            detail=f"Batch social refresh failed: {error_msg}"
        )


async def background_refresh_article_batch(article_ids: List[int], max_workers: int, login_name: Optional[str], service: RefreshService):
    """Background task for article batch refresh with chunked processing."""
    try:
        logger.info(f"Starting background article batch refresh for {len(article_ids)} articles | login_name={login_name or 'N/A'}")
        
        # Process in chunks - but process multiple chunks in parallel for speed
        # The refresh_articles_batch method already handles internal parallelism,
        # so we can process multiple chunks concurrently
        chunk_size = 100
        chunks = [article_ids[i:i + chunk_size] for i in range(0, len(article_ids), chunk_size)]
        total_successful = 0
        total_failed = 0
        
        # Process chunks in parallel (up to 5 concurrent chunks to avoid overwhelming the system)
        max_concurrent_chunks = 5
        semaphore = asyncio.Semaphore(max_concurrent_chunks)
        
        async def process_chunk(chunk, chunk_num, total_chunks):
            async with semaphore:
                logger.info(f"Processing article batch chunk {chunk_num}/{total_chunks}: {len(chunk)} articles | login_name={login_name or 'N/A'}")
                try:
                    results = await service.refresh_articles_batch(chunk, max_workers)
                    return results.successful_count, results.failed_count
                except Exception as chunk_error:
                    logger.error(f"Error processing article batch chunk: {chunk_error} | login_name={login_name or 'N/A'}")
                    return 0, len(chunk)
        
        # Process all chunks in parallel (limited by semaphore)
        tasks = [process_chunk(chunk, i+1, len(chunks)) for i, chunk in enumerate(chunks)]
        chunk_results = await asyncio.gather(*tasks)
        
        # Aggregate results
        for successful, failed in chunk_results:
            total_successful += successful
            total_failed += failed
        
        logger.info(f"Completed background article batch refresh: {total_successful} successful, {total_failed} failed | login_name={login_name or 'N/A'}")
        
    except Exception as e:
        logger.error(f"Background article batch refresh failed: {e} | login_name={login_name or 'N/A'}")


@app.post("/refresh/article/batch", response_model=BatchRefreshResponse, status_code=202)
async def refresh_batch_article(
    request: Request,
    background_tasks: BackgroundTasks,
    service: RefreshService = Depends(get_refresh_service)
):
    """Refresh multiple article documents in Elasticsearch.
    
    Accepts either:
    - Simple array format: [91194570, 91194571]
    - Object format: {"article_ids": [91194570], "max_workers": 60}
    """
    start_time = time.time()
    
    try:
        # Parse request body - support both array and object formats
        try:
            body = await request.json()
        except Exception as json_error:
            logger.error(f"Failed to parse JSON request body: {json_error}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON in request body: {str(json_error)}"
            )
        
        # Pull caller identity from headers (optional)
        login_name = (
            request.headers.get("X-Login-Name")
            or request.headers.get("X-User")
            or request.headers.get("X-Username")
        )

        if body is None:
            raise HTTPException(
                status_code=400,
                detail="Request body is empty. Please provide article IDs."
            )
        
        article_ids_safe = []
        max_workers = DEFAULT_BATCH_MAX_WORKERS
        
        # Check if body is a simple array
        if isinstance(body, list):
            article_ids_safe = body
            logger.debug(f"Parsed as array format: {len(article_ids_safe)} IDs")
        # Check if body is an object with article_ids or systemArticleIds
        elif isinstance(body, dict):
            logger.debug(f"Parsed as dict format. Keys: {list(body.keys())}")
            # Extract login_name if present
            login_name = body.get("login_name") or login_name
            # Check for article_ids, systemArticleIds, or articleIds (case-insensitive check)
            article_ids_raw = None
            if "article_ids" in body:
                article_ids_raw = body.get("article_ids")
                logger.debug("Found 'article_ids' field")
            elif "systemArticleIds" in body:
                article_ids_raw = body.get("systemArticleIds")
                logger.debug("Found 'systemArticleIds' field")
            elif "articleIds" in body:
                article_ids_raw = body.get("articleIds")
                logger.debug("Found 'articleIds' field")
            
            if article_ids_raw is not None:
                # Handle None, empty list, or actual list
                if article_ids_raw is None:
                    article_ids_safe = []
                elif isinstance(article_ids_raw, list):
                    article_ids_safe = article_ids_raw
                else:
                    # Try to convert to list if it's a single value
                    article_ids_safe = [article_ids_raw] if article_ids_raw else []
                requested_workers = body.get("max_workers", DEFAULT_BATCH_MAX_WORKERS)
                max_workers = requested_workers or DEFAULT_BATCH_MAX_WORKERS
                logger.debug(f"Found article IDs in body: {len(article_ids_safe) if article_ids_safe else 0} IDs, type: {type(article_ids_raw)}")
            else:
                # Try to parse as BatchRefreshRequest
                try:
                    batch_request = BatchRefreshRequest(**body)
                    article_ids_safe = batch_request.article_ids or []
                    max_workers = batch_request.max_workers or DEFAULT_BATCH_MAX_WORKERS
                    logger.debug(f"Parsed as BatchRefreshRequest: {len(article_ids_safe) if article_ids_safe else 0} IDs")
                except Exception as e:
                    logger.error(f"Failed to parse as BatchRefreshRequest: {e}")
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid request format. Expected array of IDs or object with 'article_ids' field. Error: {str(e)}"
                    )
        else:
            logger.error(f"Unexpected body type: {type(body)}, value: {body}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid request format. Expected array of IDs or object with 'article_ids' field. Got: {type(body).__name__}"
            )
        
        # Validate article_ids
        if not article_ids_safe:
            logger.warning(f"No article IDs found. Body type: {type(body)}, Body: {body}")
            raise HTTPException(
                status_code=400,
                detail="No article IDs provided. Please provide an array of article IDs or an object with 'article_ids' field."
            )
        
        if not isinstance(article_ids_safe, list):
            raise HTTPException(
                status_code=400,
                detail="article_ids must be an array of integers."
            )
        
        # Validate all IDs are integers
        try:
            article_ids_safe = [int(id) for id in article_ids_safe]
        except (ValueError, TypeError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"All article IDs must be integers. Error: {str(e)}"
            )
        max_workers = resolve_max_workers(max_workers)
        
        logger.info(
            f"Received request: batch article refresh | count={len(article_ids_safe)} | "
            f"max_workers={max_workers} | login_name={login_name or 'N/A'}"
        )
        
        # Always run in background and return immediately (optimistic)
        asyncio.create_task(background_refresh_article_batch(
            article_ids_safe, 
            max_workers,
            login_name,
            service
        ))
        logger.info(
            f"Scheduled background article batch refresh | count={len(article_ids_safe)} | login_name={login_name or 'N/A'}"
        )
        
        return BatchRefreshResponse(
            total_requested=len(article_ids_safe),
            successful_count=0,
            failed_count=0,
            results=[],
            processing_time=time.time() - start_time,
            timestamp=datetime.utcnow()
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Log full exception with traceback for debugging
        error_msg = str(e) if str(e) else repr(e)
        logger.error(f"Error in batch article refresh: {error_msg} | login_name={login_name or 'N/A'}", exc_info=True)
        
        # Provide more detailed error message
        if not error_msg:
            error_msg = f"Unknown error: {type(e).__name__}"
        
        raise HTTPException(
            status_code=500,
            detail=f"Batch article refresh failed: {error_msg}"
        )


@app.post("/refresh/social/{social_feed_id}", response_model=RefreshResponse)
async def refresh_single_social(
    social_feed_id: int,
    background_tasks: BackgroundTasks,
    service: RefreshService = Depends(get_refresh_service)
):
    """Refresh a single social feed document in Elasticsearch."""
    start_time = time.time()
    logger.info(f"Received request: refresh social feed {social_feed_id}")
    
    try:
        result = await service.refresh_social_feed(social_feed_id)
        logger.info(
            f"Completed refresh for social feed {social_feed_id} | success={result.success} | "
            f"elapsed={time.time() - start_time:.3f}s"
        )
        
        # Record metrics
        if metrics_collector:
            background_tasks.add_task(
                metrics_collector.record_refresh,
                "social", 
                time.time() - start_time, 
                result.success
            )
        
        return RefreshResponse(
            success=result.success,
            message=result.message,
            document_id=str(social_feed_id),
            document_type="social",
            processing_time=time.time() - start_time,
            timestamp=datetime.utcnow()
        )
        
    except Exception as e:
        logger.error(f"Error refreshing social feed {social_feed_id}: {e}")
        
        if metrics_collector:
            background_tasks.add_task(
                metrics_collector.record_refresh,
                "social", 
                time.time() - start_time, 
                False
            )
        
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh social feed {social_feed_id}: {str(e)}"
        )


@app.post("/refresh/article/{article_id}", response_model=RefreshResponse)
async def refresh_single_article(
    article_id: int,
    background_tasks: BackgroundTasks,
    service: RefreshService = Depends(get_refresh_service)
):
    """Refresh a single article document in Elasticsearch."""
    start_time = time.time()
    logger.info(f"Received request: refresh article {article_id}")
    
    try:
        result = await service.refresh_article(article_id)
        logger.info(
            f"Completed refresh for article {article_id} | success={result.success} | "
            f"elapsed={time.time() - start_time:.3f}s"
        )
        
        # Record metrics
        if metrics_collector:
            background_tasks.add_task(
                metrics_collector.record_refresh,
                "article", 
                time.time() - start_time, 
                result.success
            )
        
        return RefreshResponse(
            success=result.success,
            message=result.message,
            document_id=str(article_id),
            document_type="article",
            processing_time=time.time() - start_time,
            timestamp=datetime.utcnow()
        )
        
    except Exception as e:
        logger.error(f"Error refreshing article {article_id}: {e}")
        
        if metrics_collector:
            background_tasks.add_task(
                metrics_collector.record_refresh,
                "article", 
                time.time() - start_time, 
                False
            )
        
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh article {article_id}: {str(e)}"
        )


@app.get("/metrics", response_model=MetricsResponse)
async def get_metrics(
    metrics: MetricsCollector = Depends(get_metrics_collector)
):
    """Get application metrics."""
    return await metrics.get_metrics()


# Client Sync Endpoints
@app.post("/sync/client", response_model=ClientSyncResponse)
async def sync_client_data(
    request: ClientSyncRequest,
    background_tasks: BackgroundTasks,
    service: ClientSyncService = Depends(get_client_sync_service)
):
    """Sync client-specific data from MongoDB to Elasticsearch."""
    start_time = time.time()
    
    try:
        results = []
        total_processing_time = 0.0
        
        # Sync CBCP data if requested
        if "cbcp" in request.sync_types:
            cbcp_result = await service.sync_cbcp_data(request.client_id)
            results.append(cbcp_result)
            total_processing_time += cbcp_result.processing_time
        
        # Sync CPOnline data if requested
        if "cponline" in request.sync_types:
            cponline_result = await service.sync_cponline_data(request.client_id)
            results.append(cponline_result)
            total_processing_time += cponline_result.processing_time
        
        # Determine overall success
        overall_success = all(result.success for result in results)
        overall_message = f"Client sync completed for {request.client_id}"
        if not overall_success:
            failed_syncs = [r.sync_type for r in results if not r.success]
            overall_message = f"Client sync partially failed for {request.client_id}. Failed: {', '.join(failed_syncs)}"
        
        return ClientSyncResponse(
            client_id=request.client_id,
            success=overall_success,
            message=overall_message,
            results=results,
            total_processing_time=time.time() - start_time,
            timestamp=datetime.utcnow()
        )
        
    except Exception as e:
        logger.error(f"Error syncing client data for {request.client_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to sync client data for {request.client_id}: {str(e)}"
        )


async def background_sync_cbcp(client_id: str, service: ClientSyncService):
    """Background task for CBCP sync."""
    try:
        logger.info(f"Starting background CBCP sync for client {client_id}")
        result = await service.sync_cbcp_data(client_id)
        logger.info(f"Completed background CBCP sync for client {client_id}: {result.message}")
    except Exception as e:
        logger.error(f"Background CBCP sync failed for client {client_id}: {e}")


@app.post("/sync/client/cbcp", response_model=AsyncSyncResponse)
async def sync_client_cbcp_data(
    client_id: str,
    service: ClientSyncService = Depends(get_client_sync_service)
):
    """Sync CBCP (Client Basket City Publication Group) data for a client asynchronously."""
    try:
        # Start background task without waiting
        asyncio.create_task(background_sync_cbcp(client_id, service))
        
        return AsyncSyncResponse(
            client_id=client_id,
            sync_type="cbcp",
            message=f"CBCP sync started for client {client_id}",
            status="started",
            timestamp=datetime.utcnow()
        )
        
    except Exception as e:
        logger.error(f"Error starting CBCP sync for client {client_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start CBCP sync for client {client_id}: {str(e)}"
        )


async def background_sync_cponline(client_id: str, service: ClientSyncService):
    """Background task for CPOnline sync."""
    try:
        logger.info(f"Starting background CPOnline sync for client {client_id}")
        result = await service.sync_cponline_data(client_id)
        logger.info(f"Completed background CPOnline sync for client {client_id}: {result.message}")
    except Exception as e:
        logger.error(f"Background CPOnline sync failed for client {client_id}: {e}")


@app.post("/sync/client/cponline", response_model=AsyncSyncResponse)
async def sync_client_cponline_data(
    client_id: str,
    service: ClientSyncService = Depends(get_client_sync_service)
):
    """Sync CPOnline (Client Publication Online) data for a client asynchronously."""
    try:
        # Start background task without waiting
        asyncio.create_task(background_sync_cponline(client_id, service))
        
        return AsyncSyncResponse(
            client_id=client_id,
            sync_type="cponline",
            message=f"CPOnline sync started for client {client_id}",
            status="started",
            timestamp=datetime.utcnow()
        )
        
    except Exception as e:
        logger.error(f"Error starting CPOnline sync for client {client_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start CPOnline sync for client {client_id}: {str(e)}"
        )


@app.get("/sync/client/{client_id}/status", response_model=Dict[str, Any])
async def get_client_sync_status(client_id: str):
    """Get sync status for a client (placeholder for future implementation)."""
    return {
        "client_id": client_id,
        "message": "Sync status endpoint - check logs for detailed progress",
        "status": "running",
        "timestamp": datetime.utcnow()
    }


# Charts API Endpoints
@app.post("/charts/validate/social")
async def validate_file_social(file: UploadFile = File(...)):
    """Validate Excel file explicitly as Social Feed format"""
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file selected")
        if not file.filename.lower().endswith('.xlsx'):
            raise HTTPException(status_code=400, detail="File must be an Excel file (.xlsx)")
        
        temp_path = f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
        content = await file.read()
        with open(temp_path, 'wb') as f:
            f.write(content)
        
        try:
            validation_results = await validator.validate_excel_file(temp_path, upload_type_override="social")
            return validation_results
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Validation API error (social): {str(e)}")
        raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")
 

@app.post("/charts/validate/print")
async def validate_file_print(file: UploadFile = File(...)):
    """Validate Excel file explicitly as Print Article format"""
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file selected")
        if not file.filename.lower().endswith('.xlsx'):
            raise HTTPException(status_code=400, detail="File must be an Excel file (.xlsx)")
        
        temp_path = f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
        content = await file.read()
        with open(temp_path, 'wb') as f:
            f.write(content)
        
        try:
            validation_results = await validator.validate_excel_file(temp_path, upload_type_override="print")
            return validation_results
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Validation API error (print): {str(e)}")
        raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")


@app.post("/charts/upload/social")
async def upload_social(file: UploadFile = File(...), index_name: Optional[str] = None):
    """Enqueue background upload for Social Feed data. Returns job id immediately."""
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file selected")
        if not file.filename.lower().endswith('.xlsx'):
            raise HTTPException(status_code=400, detail="File must be an Excel file (.xlsx)")

        temp_path = f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
        content = await file.read()
        with open(temp_path, 'wb') as f:
            f.write(content)

        # Create job
        job_id = str(uuid.uuid4())
        upload_jobs[job_id] = {
            'status': 'queued',
            'type': 'social',
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }

        # Persist queued job to MongoDB
        try:
            charts_jobs.insert_one({
                '_id': job_id,
                'status': 'queued',
                'type': 'social',
                'filename': file.filename,
                'index': (index_name or os.getenv('ES_SOCIAL_INDEX', 'onlinearticlereport')),
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            })
        except Exception as e:
            logger.warning(f"Could not persist queued job to MongoDB: {str(e)}")

        async def process_job():
            upload_jobs[job_id]['status'] = 'processing'
            upload_jobs[job_id]['updated_at'] = datetime.now().isoformat()
            try:
                charts_jobs.update_one({'_id': job_id}, {'$set': {'status': 'processing', 'updated_at': datetime.now().isoformat()}})
            except Exception as e:
                logger.debug(f"Mongo update processing failed: {str(e)}")
            
            try:
                # Validate first
                validation = await validator.validate_excel_file(temp_path, upload_type_override='social')
                if not validation.get('overall_valid'):
                    upload_jobs[job_id]['status'] = 'failed'
                    upload_jobs[job_id]['error'] = {
                        'message': 'Validation failed',
                        'details': validation
                    }
                    upload_jobs[job_id]['updated_at'] = datetime.now().isoformat()
                    try:
                        charts_jobs.update_one({'_id': job_id}, {'$set': {'status': 'failed', 'error': upload_jobs[job_id]['error'], 'updated_at': datetime.now().isoformat()}})
                    except Exception as e:
                        logger.debug(f"Mongo update failed-state failed: {str(e)}")
                    return
                
                # Use existing social uploader class
                loop = asyncio.get_event_loop()
                es_index = index_name or os.getenv('ES_SOCIAL_INDEX', 'onlinearticlereport')
                inserter = SocialInserter(temp_path, 'socialFeed', 'socialFeedTag', es_index)
                await loop.run_in_executor(executor, inserter.run)
                
                upload_jobs[job_id]['status'] = 'completed'
                upload_jobs[job_id]['summary'] = {
                    'message': 'Upload completed using Social inserter',
                    'index': es_index
                }
                try:
                    charts_jobs.update_one({'_id': job_id}, {'$set': {'status': 'completed', 'summary': upload_jobs[job_id]['summary'], 'updated_at': datetime.now().isoformat()}})
                except Exception as e:
                    logger.debug(f"Mongo update completion failed: {str(e)}")
            except Exception as e:
                logger.error(f"Upload job (social) failed: {str(e)}")
                upload_jobs[job_id]['status'] = 'failed'
                upload_jobs[job_id]['error'] = {'message': str(e)}
                try:
                    charts_jobs.update_one({'_id': job_id}, {'$set': {'status': 'failed', 'error': upload_jobs[job_id]['error'], 'updated_at': datetime.now().isoformat()}})
                except Exception as e2:
                    logger.debug(f"Mongo update failure persist failed: {str(e2)}")
            finally:
                upload_jobs[job_id]['updated_at'] = datetime.now().isoformat()
                try:
                    charts_jobs.update_one({'_id': job_id}, {'$set': {'updated_at': datetime.now().isoformat()}})
                except Exception:
                    pass
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass

        asyncio.create_task(process_job())
        return JSONResponse(status_code=202, content={'job_id': job_id, 'status': 'queued'})
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload enqueue error (social): {str(e)}")
        raise HTTPException(status_code=500, detail=f"Could not enqueue upload: {str(e)}")


@app.post("/charts/upload/print")
async def upload_print(file: UploadFile = File(...), index_name: Optional[str] = None):
    """Enqueue background upload for Print Article data. Returns job id immediately."""
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file selected")
        if not file.filename.lower().endswith('.xlsx'):
            raise HTTPException(status_code=400, detail="File must be an Excel file (.xlsx)")

        temp_path = f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
        content = await file.read()
        with open(temp_path, 'wb') as f:
            f.write(content)

        job_id = str(uuid.uuid4())
        upload_jobs[job_id] = {
            'status': 'queued',
            'type': 'print',
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }

        # Persist queued job to MongoDB
        try:
            charts_jobs.insert_one({
                '_id': job_id,
                'status': 'queued',
                'type': 'print',
                'filename': file.filename,
                'index': (index_name or os.getenv('ES_PRINT_INDEX', 'printarticlereport')),
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            })
        except Exception as e:
            logger.warning(f"Could not persist queued job to MongoDB: {str(e)}")

        async def process_job():
            upload_jobs[job_id]['status'] = 'processing'
            upload_jobs[job_id]['updated_at'] = datetime.now().isoformat()
            try:
                charts_jobs.update_one({'_id': job_id}, {'$set': {'status': 'processing', 'updated_at': datetime.now().isoformat()}})
            except Exception as e:
                logger.debug(f"Mongo update processing failed: {str(e)}")
            
            try:
                validation = await validator.validate_excel_file(temp_path, upload_type_override='print')
                if not validation.get('overall_valid'):
                    upload_jobs[job_id]['status'] = 'failed'
                    upload_jobs[job_id]['error'] = {
                        'message': 'Validation failed',
                        'details': validation
                    }
                    upload_jobs[job_id]['updated_at'] = datetime.now().isoformat()
                    try:
                        charts_jobs.update_one({'_id': job_id}, {'$set': {'status': 'failed', 'error': upload_jobs[job_id]['error'], 'updated_at': datetime.now().isoformat()}})
                    except Exception as e:
                        logger.debug(f"Mongo update failed-state failed: {str(e)}")
                    return
                
                loop = asyncio.get_event_loop()
                es_index = index_name or os.getenv('ES_PRINT_INDEX', 'printarticlereport')
                inserter = PrintInserter(temp_path, 'article', es_index)
                await loop.run_in_executor(executor, inserter.run)
                
                upload_jobs[job_id]['status'] = 'completed'
                upload_jobs[job_id]['summary'] = {
                    'message': 'Upload completed using Print inserter',
                    'index': es_index
                }
                try:
                    charts_jobs.update_one({'_id': job_id}, {'$set': {'status': 'completed', 'summary': upload_jobs[job_id]['summary'], 'updated_at': datetime.now().isoformat()}})
                except Exception as e:
                    logger.debug(f"Mongo update completion failed: {str(e)}")
            except Exception as e:
                logger.error(f"Upload job (print) failed: {str(e)}")
                upload_jobs[job_id]['status'] = 'failed'
                upload_jobs[job_id]['error'] = {'message': str(e)}
                try:
                    charts_jobs.update_one({'_id': job_id}, {'$set': {'status': 'failed', 'error': upload_jobs[job_id]['error'], 'updated_at': datetime.now().isoformat()}})
                except Exception as e2:
                    logger.debug(f"Mongo update failure persist failed: {str(e2)}")
            finally:
                upload_jobs[job_id]['updated_at'] = datetime.now().isoformat()
                try:
                    charts_jobs.update_one({'_id': job_id}, {'$set': {'updated_at': datetime.now().isoformat()}})
                except Exception:
                    pass
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass

        asyncio.create_task(process_job())
        return JSONResponse(status_code=202, content={'job_id': job_id, 'status': 'queued'})
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload enqueue error (print): {str(e)}")
        raise HTTPException(status_code=500, detail=f"Could not enqueue upload: {str(e)}")


@app.get("/charts/upload/status/{job_id}")
async def get_upload_status(job_id: str):
    """Get the status of an upload job"""
    # Try MongoDB first
    try:
        db_job = charts_jobs.find_one({'_id': job_id})
        if db_job:
            job_doc = dict(db_job)
            job_doc['job_id'] = job_doc.pop('_id', job_id)
            return job_doc
    except Exception as e:
        logger.debug(f"Mongo read job status failed: {str(e)}")
    
    # Fallback to in-memory
    job = upload_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        workers=1 if settings.DEBUG else settings.WORKERS,
        log_level="info"
    )
