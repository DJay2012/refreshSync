#!/usr/bin/env python3
"""
FastAPI Boolean Inserter API
Receives .txt files and inserts boolean queries into Elasticsearch
If a boolean for a companyID already exists, it deletes and inserts new one
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, status, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import Dict, Any, Optional
import os
import sys
import tempfile
import traceback
import json
import uuid
import threading
from datetime import datetime
from enum import Enum
from pydantic import BaseModel

# Add src directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(current_dir, '..')
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from utils.Config import es, INDEX_NAME
from utils.txt_to_es_transformer import TranslationToESTransformer

# Import esPreview components
from esPreview import ESPreviewEngine, ESPreviewConfig

# Import backtracking components
from pathlib import Path
from datetime import timedelta

# Add parent directory to path for backtracking imports
parent_dir = Path(__file__).parent.parent.parent.parent
esBacktracking_dir = parent_dir / "esBacktracking"
if str(esBacktracking_dir) not in sys.path:
    sys.path.insert(0, str(esBacktracking_dir))

try:
    from backtracking_engine import BacktrackingEngine, BacktrackingConfig
    BACKTRACKING_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Backtracking modules not available: {e}")
    BACKTRACKING_AVAILABLE = False

# Initialize FastAPI app
app = FastAPI(
    title="Boolean Inserter API",
    description="API to insert boolean queries from .txt files into Elasticsearch",
    version="1.0.0"
)

# Initialize transformer
transformer = TranslationToESTransformer()

# Initialize esPreview engine
espreview_config = ESPreviewConfig.from_env()
espreview_engine = ESPreviewEngine(espreview_config)

# ============================================================================
# BACKGROUND JOB MANAGEMENT (MongoDB-backed)
# ============================================================================

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

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
                    mongo_uri = os.getenv("PG_MONGO_URI", "mongodb://localhost:27017/")
                    mongo_db = os.getenv("PG_MONGO_DB", "pnq")
                    
                    _mongo_job_client = MongoClient(
                        mongo_uri,
                        serverSelectionTimeoutMS=5000
                    )
                    _mongo_job_db = _mongo_job_client[mongo_db]
                    
                    # Test connection
                    _mongo_job_client.admin.command('ping')
                    print(f"Connected to MongoDB for job storage: {mongo_uri}")
                except Exception as e:
                    print(f"MongoDB connection failed for job storage: {e}")
                    raise
    
    return _mongo_job_db

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get job status by ID from MongoDB."""
    try:
        db = get_mongo_job_db()
        job_doc = db.backtrackingJobs.find_one({"_id": job_id})
        if job_doc:
            # Convert ObjectId to string and ensure job_id field exists
            if "_id" in job_doc:
                job_doc["job_id"] = str(job_doc["_id"])
                del job_doc["_id"]
            return job_doc
        return None
    except Exception as e:
        print(f"Error getting job from MongoDB: {e}")
        return None

def create_job(config_dict: Dict[str, Any]) -> str:
    """Create a new job in MongoDB and return job ID."""
    job_id = str(uuid.uuid4())
    try:
        db = get_mongo_job_db()
        job_doc = {
            "_id": job_id,
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "config": config_dict,
            "results": None,
            "error": None,
            "progress": {
                "chunks_processed": 0,
                "total_chunks": 0,
                "progress_percent": 0.0
            }
        }
        db.backtrackingJobs.insert_one(job_doc)
        return job_id
    except Exception as e:
        print(f"Error creating job in MongoDB: {e}")
        raise

def update_job_status(job_id: str, status: JobStatus, results: Optional[Dict] = None, error: Optional[str] = None, progress: Optional[Dict] = None):
    """Update job status in MongoDB."""
    try:
        db = get_mongo_job_db()
        update_doc = {
            "status": status,
            "updated_at": datetime.now().isoformat()
        }
        
        if results is not None:
            update_doc["results"] = results
        if error is not None:
            update_doc["error"] = error
        if progress is not None:
            update_doc["progress"] = progress
        
        db.backtrackingJobs.update_one(
            {"_id": job_id},
            {"$set": update_doc}
        )
    except Exception as e:
        print(f"Error updating job in MongoDB: {e}")
        raise

def run_backtracking_background(job_id: str, config_dict: Dict[str, Any]):
    """Run backtracking in background thread."""
    try:
        update_job_status(job_id, JobStatus.RUNNING)
        
        # Handle "all" languages - convert to None for processing
        language_param = config_dict.get("language", "en")
        if language_param and language_param.lower() == "all":
            language_param = None
        
        # Create config
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
            checkpoint_id=config_dict.get("checkpoint_id"),
            checkpoint_file=config_dict.get("checkpoint_file"),
            auto_resume_on_crash=config_dict.get("auto_resume_on_crash", True),
            max_auto_retries=config_dict.get("max_auto_retries", 3),
            retry_delay_seconds=config_dict.get("retry_delay_seconds", 10)
        )
        
        if config_dict.get("checkpoint_file"):
            config.checkpoint_file = config_dict["checkpoint_file"]
        
        # Initialize engine
        engine = BacktrackingEngine(config)
        
        # Calculate total chunks for progress tracking
        from datetime import datetime as dt
        start_dt = dt.strptime(config.start_date, '%Y-%m-%d')
        end_dt = dt.strptime(config.end_date, '%Y-%m-%d')
        total_days = (end_dt - start_dt).days + 1
        total_chunks = (total_days + config.chunk_days - 1) // config.chunk_days
        update_job_status(job_id, JobStatus.RUNNING, progress={"total_chunks": total_chunks})
        
        # Run backtracking
        results = engine.run_backtracking(resume=config_dict.get("resume", True))
        
        # Update with final results
        update_job_status(
            job_id, 
            JobStatus.COMPLETED,
            results={
                "start_time": results.get("start_time"),
                "end_time": results.get("end_time"),
                "total_articles_processed": results.get("total_articles_processed", 0),
                "total_social_feeds_processed": results.get("total_social_feeds_processed", 0),
                "total_tags_created": results.get("total_tags_created", 0),
                "processing_time_seconds": results.get("processing_time_seconds", 0),
                "errors": results.get("errors", [])
            },
            progress={"chunks_processed": total_chunks, "progress_percent": 100.0}
        )
        
    except Exception as e:
        error_msg = str(e)
        print(f"Background backtracking job {job_id} failed: {error_msg}")
        print(traceback.format_exc())
        update_job_status(job_id, JobStatus.FAILED, error=error_msg)

def convert_dsl_to_boolean_string(dsl_query):
    """
    Convert Elasticsearch DSL to boolean query string for esPreview
    
    Args:
        dsl_query: Elasticsearch DSL query object
        
    Returns:
        str: Boolean query string
    """
    if isinstance(dsl_query, dict):
        if 'match_phrase' in dsl_query:
            # Extract query from match_phrase
            query_text = dsl_query['match_phrase']['content']['query']
            # Return without extra quotes - esPreview will handle the quoting
            return query_text
        elif 'bool' in dsl_query:
            # Handle bool queries
            bool_query = dsl_query['bool']
            if 'should' in bool_query:
                # Convert should clauses to OR
                should_clauses = []
                for clause in bool_query['should']:
                    if 'match_phrase' in clause:
                        query_text = clause['match_phrase']['content']['query']
                        should_clauses.append(f'"{query_text}"')
                return ' OR '.join(should_clauses)
            elif 'must' in bool_query:
                # Convert must clauses to AND
                must_clauses = []
                for clause in bool_query['must']:
                    if 'match_phrase' in clause:
                        query_text = clause['match_phrase']['content']['query']
                        must_clauses.append(f'"{query_text}"')
                return ' AND '.join(must_clauses)
        elif 'match' in dsl_query:
            # Handle match queries
            query_text = dsl_query['match']['content']['query']
            return query_text
    
    # Fallback: return as string
    return str(dsl_query)



# Pydantic models for request bodies
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

class BacktrackingRequest(BaseModel):
    start_date: str
    end_date: str
    company_ids: list[str]
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

class BooleanInsertRequest(BaseModel):
    companyId: str
    companyName: str
    originalQuery: Optional[str] = None
    translations: Optional[Dict[str, str]] = None

@app.get("/")
async def root():
    """Root endpoint"""
    endpoints = {
        "POST /upload": "Upload and insert boolean query from .json file",
        "POST /insert": "Insert boolean query directly from JSON body",
        "GET /health": "Check API and Elasticsearch health",
        "POST /espreview/query": "Execute boolean query using esPreview",
        "POST /espreview/query/file": "Execute boolean query from JSON file using esPreview",
        "POST /espreview/company/{company_id}": "Execute company query using esPreview",
        "GET /espreview/companies": "List available companies",
        "GET /espreview/health": "Check esPreview system health"
    }
    
    # Add backtracking endpoints if available
    if BACKTRACKING_AVAILABLE:
        endpoints.update({
            "POST /backtracking/run": "Start backtracking job (returns immediately with job_id)",
            "GET /backtracking/job/{job_id}": "Get backtracking job status",
            "GET /backtracking/jobs": "List all backtracking jobs",
            "DELETE /backtracking/job/{job_id}": "Cancel a backtracking job",
            "GET /backtracking/health": "Check backtracking system health",
            "GET /backtracking/status/{checkpoint_file}": "Get checkpoint status (filesystem)",
            "GET /backtracking/checkpoint/{checkpoint_id}": "Get checkpoint status (MongoDB)",
            "POST /backtracking/resume": "Resume backtracking from checkpoint (returns immediately with job_id)"
        })
    
    return {
        "message": "Boolean Inserter API",
        "version": "1.0.0",
        "backtracking_available": BACKTRACKING_AVAILABLE,
        "endpoints": endpoints
    }

@app.get("/health")
async def health_check():
    """Check API and Elasticsearch health"""
    try:
        # Check Elasticsearch connection
        if not transformer.es_client:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "unhealthy",
                    "message": "Elasticsearch client not available",
                    "elasticsearch": False
                }
            )
        
        # Ping Elasticsearch
        ping_result = transformer.es_client.ping()
        
        return {
            "status": "healthy" if ping_result else "unhealthy",
            "message": "API is running",
            "elasticsearch": ping_result,
            "index": INDEX_NAME
        }
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "message": f"Health check failed: {str(e)}",
                "elasticsearch": False
            }
        )

@app.post("/upload")
async def upload_boolean_file(
    file: UploadFile = File(...),
    index_name: Optional[str] = None,
    delete_existing: bool = True
):
    """
    Upload and insert boolean query from .json file
    
    Args:
        file: The .json file containing boolean query translations
        index_name: Optional Elasticsearch index name (uses default if not provided)
        delete_existing: Whether to delete existing document with same companyID (default: True)
    
    Returns:
        Dict with insertion result and document details
    """
    # Validate file extension
    if not file.filename.endswith('.json'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .json files are supported"
        )
    
    # Save uploaded file temporarily
    temp_file_path = None
    try:
        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w+b', suffix='.json', delete=False) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        # Parse the JSON file
        print(f"Parsing file: {file.filename}")
        
        try:
            with open(temp_file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            # Convert translations format to lang_{code} format using the transformer
            # This ensures boolean queries are parsed into proper ES DSL
            parsed_data = transformer.create_es_document(json_data)
            
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON format: {str(e)}"
            )
        
        if not parsed_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to parse translation file. Please check the file format."
            )
        
        company_id = parsed_data.get('companyId')
        company_name = parsed_data.get('companyName')
        
        if not company_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Company ID not found in file"
            )
        
        # Create ES document directly from parsed_data (already in DSL format)
        print(f"Using converted data for company: {company_id}")
        es_document = parsed_data
        
        # Count languages
        languages = [k for k in es_document.keys() if k.startswith('lang_')]
        language_count = len(languages)
        
        # Determine index to use
        target_index = index_name or transformer.default_index
        
        # Check if document exists and delete if requested
        document_exists = False
        if delete_existing:
            try:
                document_exists = transformer.es_client.exists(
                    index=target_index,
                    id=company_id
                )
                
                if document_exists:
                    print(f"Deleting existing document: {company_id}")
                    transformer.es_client.delete(
                        index=target_index,
                        id=company_id
                    )
                    print(f"Deleted existing document: {company_id}")
            except Exception as e:
                print(f"Warning: Error checking/deleting existing document: {e}")
                # Continue with insertion anyway
        
        # Insert the document
        print(f"Inserting document: {company_id}")
        success = transformer.upsert_data_to_es_single(
            es_document,
            company_id,
            target_index
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to insert document into Elasticsearch"
            )
        
        return {
            "success": True,
            "message": "Document inserted successfully",
            "companyId": company_id,
            "companyName": company_name,
            "index": target_index,
            "documentExists": document_exists,
            "deletedExisting": document_exists and delete_existing,
            "languages": language_count,
            "languageCodes": languages
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        print(f"Error processing file: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing file: {str(e)}"
        )
    finally:
        # Clean up temporary file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                print(f"Warning: Could not delete temporary file: {e}")

@app.post("/insert")
async def insert_boolean_json(
    request: BooleanInsertRequest,
    index_name: Optional[str] = None,
    delete_existing: bool = True
):
    """
    Insert boolean query directly from JSON body
    
    Args:
        request: BooleanInsertRequest object
        index_name: Optional Elasticsearch index name
        delete_existing: Whether to delete existing document (default: True)
    """
    try:
        # Convert request to dict
        json_data = request.dict()
        
        # Convert translations format using the transformer
        parsed_data = transformer.create_es_document(json_data)
        
        if not parsed_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to parse request data"
            )
        
        company_id = parsed_data.get('companyId')
        company_name = parsed_data.get('companyName')
        
        # Determine index to use
        target_index = index_name or transformer.default_index
        
        # Check if document exists and delete if requested
        document_exists = False
        if delete_existing:
            try:
                document_exists = transformer.es_client.exists(
                    index=target_index,
                    id=company_id
                )
                
                if document_exists:
                    print(f"Deleting existing document: {company_id}")
                    transformer.es_client.delete(
                        index=target_index,
                        id=company_id
                    )
            except Exception as e:
                print(f"Warning: Error checking/deleting existing document: {e}")
        
        # Insert the document
        print(f"Inserting document: {company_id}")
        success = transformer.upsert_data_to_es_single(
            parsed_data,
            company_id,
            target_index
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to insert document into Elasticsearch"
            )
        
        # Count languages
        languages = [k for k in parsed_data.keys() if k.startswith('lang_')]
        
        return {
            "success": True,
            "message": "Document inserted successfully",
            "companyId": company_id,
            "companyName": company_name,
            "index": target_index,
            "documentExists": document_exists,
            "deletedExisting": document_exists and delete_existing,
            "languages": len(languages),
            "languageCodes": languages
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error processing request: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing request: {str(e)}"
        )

@app.delete("/company/{company_id}")
async def delete_company_boolean(
    company_id: str,
    index_name: Optional[str] = None
):
    """
    Delete a boolean query document by company ID
    
    Args:
        company_id: The company ID to delete
        index_name: Optional Elasticsearch index name (uses default if not provided)
    
    Returns:
        Dict with deletion result
    """
    try:
        target_index = index_name or transformer.default_index
        
        # Check if document exists
        document_exists = transformer.es_client.exists(
            index=target_index,
            id=company_id
        )
        
        if not document_exists:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={
                    "success": False,
                    "message": f"Document with company ID '{company_id}' not found",
                    "companyId": company_id,
                    "index": target_index
                }
            )
        
        # Delete the document
        result = transformer.es_client.delete(
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
        print(f"Error deleting document: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting document: {str(e)}"
        )

@app.get("/company/{company_id}")
async def get_company_boolean(
    company_id: str,
    index_name: Optional[str] = None
):
    """
    Get a boolean query document by company ID
    
    Args:
        company_id: The company ID to retrieve
        index_name: Optional Elasticsearch index name (uses default if not provided)
    
    Returns:
        Dict with document data
    """
    try:
        target_index = index_name or transformer.default_index
        
        # Get the document
        result = transformer.es_client.get(
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
        print(f"Error getting document: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with company ID '{company_id}' not found"
        )

# ============================================================================
# ESPREVIEW ENDPOINTS
# ============================================================================

@app.get("/espreview/health")
async def espreview_health_check():
    """Check esPreview system health"""
    try:
        health = espreview_engine.health_check()
        return health
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "message": f"esPreview health check failed: {str(e)}"
            }
        )

@app.post("/espreview/query")
async def execute_query(request: QueryRequest):
    """
    Execute a boolean query using esPreview
    
    Args:
        request: QueryRequest object containing query string and optional parameters
    
    Returns:
        Dict with query results
    """
    try:
        # Use hardcoded indexes from esPreview config (ignore request.indexes)
        target_indexes = None  # Will use default indexes from config
        
        # Override limit if provided
        if request.limit:
            espreview_config.max_results_per_index = request.limit
        
        # Execute query
        result = espreview_engine.execute_query(request.query, target_indexes, request.include_content)
        
        # Convert result to dict
        return {
            "success": result.success,
            "total_matches": result.total_matches,
            "execution_time_ms": result.execution_time_ms,
            "query_info": result.query_info,
            "index_results": {
                idx: {
                    "total_hits": res.total_hits,
                    "article_ids": res.article_ids,
                    "articles": res.articles,  # Include article content
                    "execution_time_ms": res.execution_time_ms,
                    "errors": res.errors
                }
                for idx, res in result.index_results.items()
            },
            "errors": result.errors
        }
        
    except Exception as e:
        print(f"Error executing query: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error executing query: {str(e)}"
        )

@app.post("/espreview/query/file")
async def execute_query_from_file(
    file: UploadFile = File(...),
    indexes: Optional[str] = Form(None),
    limit: Optional[int] = Form(None),
    language: Optional[str] = Form(None)
):
    """
    Execute a boolean query from a JSON file using esPreview
    
    Args:
        file: The JSON file containing the boolean query
        indexes: Comma-separated list of indexes to search (optional)
        limit: Maximum results per index (optional)
        language: Language code for JSON files (optional, defaults to 'en')
    
    Returns:
        Dict with query results
    """
    temp_file_path = None
    try:
        # Validate file extension
        if not file.filename.endswith('.json'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only .json files are supported"
            )
        
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(mode='w+b', suffix='.json', delete=False) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        # Parse JSON file
        try:
            with open(temp_file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            # Extract query based on language
            target_lang = language or 'en'
            
            if 'translations' in json_data:
                # Your format with translations - convert to DSL first, then to boolean string
                translations = json_data.get('translations', {})
                if target_lang in translations:
                    # Convert the DSL to boolean string for esPreview
                    dsl_query = {
                        "match_phrase": {
                            "content": {
                                "query": translations[target_lang].strip('"')
                            }
                        }
                    }
                    # Convert DSL to boolean string for esPreview
                    query = convert_dsl_to_boolean_string(dsl_query)
                else:
                    # Fallback to English if target language not found
                    if 'en' in translations:
                        dsl_query = {
                            "match_phrase": {
                                "content": {
                                    "query": translations['en'].strip('"')
                                }
                            }
                        }
                        query = convert_dsl_to_boolean_string(dsl_query)
                    else:
                        # Use first available language
                        available_langs = list(translations.keys())
                        if available_langs:
                            dsl_query = {
                                "match_phrase": {
                                    "content": {
                                        "query": translations[available_langs[0]].strip('"')
                                    }
                                }
                            }
                            query = convert_dsl_to_boolean_string(dsl_query)
                        else:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail="No translations found in JSON file"
                            )
            elif f'lang_{target_lang}' in json_data:
                # Internal format with lang_{code} - could be DSL or string
                lang_data = json_data[f'lang_{target_lang}']
                if isinstance(lang_data, dict):
                    # It's DSL, convert to boolean string
                    query = convert_dsl_to_boolean_string(lang_data)
                else:
                    # It's a string, use as is
                    query = lang_data.strip()
            else:
                # Try to find any lang_{code} field
                lang_fields = [k for k in json_data.keys() if k.startswith('lang_')]
                if lang_fields:
                    lang_data = json_data[lang_fields[0]]
                    if isinstance(lang_data, dict):
                        # It's DSL, convert to boolean string
                        query = convert_dsl_to_boolean_string(lang_data)
                    else:
                        # It's a string, use as is
                        query = lang_data.strip()
                else:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="No language-specific queries found in JSON file"
                    )
            
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON format: {str(e)}"
            )
        
        if not query:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Query file is empty"
            )
        
        # Use hardcoded indexes from esPreview config (ignore indexes parameter)
        target_indexes = None  # Will use default indexes from config
        
        # Override limit if provided
        if limit:
            espreview_config.max_results_per_index = limit
        
        # Execute query
        result = espreview_engine.execute_query(query, target_indexes, False)  # Default to no content for file queries
        
        # Convert result to dict
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
                    "articles": res.articles,  # Include article content
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
        print(f"Error executing query from file: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error executing query from file: {str(e)}"
        )
    finally:
        # Clean up temporary file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                print(f"Warning: Could not delete temporary file: {e}")

@app.post("/espreview/company/{company_id}")
async def execute_company_query(
    company_id: str,
    request: CompanyQueryRequest
):
    """
    Execute a company query using esPreview
    
    Args:
        company_id: The company ID to query
        request: CompanyQueryRequest object containing language and optional parameters
    
    Returns:
        Dict with query results
    """
    try:
        # Use hardcoded indexes from esPreview config (ignore request.indexes)
        target_indexes = None  # Will use default indexes from config
        
        # Override limit if provided
        if request.limit:
            espreview_config.max_results_per_index = request.limit
        
        # Execute company query
        result = espreview_engine.execute_company_query(company_id, request.language, target_indexes, request.include_content)
        
        # Convert result to dict
        return {
            "success": result.success,
            "total_matches": result.total_matches,
            "execution_time_ms": result.execution_time_ms,
            "query_info": result.query_info,
            "index_results": {
                idx: {
                    "total_hits": res.total_hits,
                    "article_ids": res.article_ids,
                    "articles": res.articles,  # Include article content
                    "execution_time_ms": res.execution_time_ms,
                    "errors": res.errors
                }
                for idx, res in result.index_results.items()
            },
            "errors": result.errors
        }
        
    except Exception as e:
        print(f"Error executing company query: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error executing company query: {str(e)}"
        )

@app.get("/espreview/companies")
async def list_companies(limit: int = 100):
    """
    List available companies
    
    Args:
        limit: Maximum number of companies to return (default: 100)
    
    Returns:
        List of companies
    """
    try:
        companies = espreview_engine.list_companies(limit)
        return {
            "success": True,
            "count": len(companies),
            "companies": companies
        }
    except Exception as e:
        print(f"Error listing companies: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error listing companies: {str(e)}"
        )

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
        # Check dependencies
        es_healthy = transformer.es_client.ping() if transformer.es_client else False
        
        return {
            "status": "healthy" if es_healthy else "unhealthy",
            "message": "Backtracking system is available",
            "backtracking_module": "available",
            "elasticsearch": es_healthy,
            "dependencies": {
                "mongodb": "configured",
                "elasticsearch": "configured"
            }
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
async def run_backtracking(request: BacktrackingRequest, background_tasks: BackgroundTasks):
    """
    Start backtracking job (returns immediately with job_id)
    
    The backtracking runs in the background. Use the job_id to check status.
    
    Args:
        request: BacktrackingRequest object containing date range, company IDs, and processing settings
        background_tasks: FastAPI background tasks
    
    Returns:
        Dict with job_id and initial status
    """
    if not BACKTRACKING_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backtracking module not available"
        )
    
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
            "retry_delay_seconds": request.retry_delay_seconds
        }
        
        # Create job
        job_id = create_job(config_dict)
        
        # Start background task
        background_tasks.add_task(run_backtracking_background, job_id, config_dict)
        
        return {
            "success": True,
            "message": "Backtracking job started",
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "status_url": f"/backtracking/job/{job_id}",
            "created_at": get_job(job_id)["created_at"]
        }
        
    except Exception as e:
        print(f"Error starting backtracking job: {e}")
        print(traceback.format_exc())
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
    
    job = get_job(job_id)
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
    
    if job["status"] == JobStatus.COMPLETED and job["results"]:
        response["results"] = job["results"]
    
    if job["status"] == JobStatus.FAILED and job["error"]:
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
        cursor = db.backtrackingJobs.find(query).sort("created_at", -1).limit(limit)
        jobs_list = []
        for job_doc in cursor:
            # Convert ObjectId to string
            if "_id" in job_doc:
                job_doc["job_id"] = str(job_doc["_id"])
                del job_doc["_id"]
            jobs_list.append(job_doc)
    except Exception as e:
        print(f"Error listing jobs from MongoDB: {e}")
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
                "progress": job["progress"]
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
    
    job = get_job(job_id)
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
    
    update_job_status(job_id, JobStatus.CANCELLED)
    
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
        print(f"Error reading MongoDB checkpoint: {e}")
        print(traceback.format_exc())
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
        from pathlib import Path
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
        print(f"Error reading checkpoint: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reading checkpoint: {str(e)}"
        )

@app.post("/backtracking/resume")
async def resume_backtracking(request: BacktrackingRequest, background_tasks: BackgroundTasks):
    """
    Resume backtracking from a checkpoint (returns immediately with job_id)
    
    The resume runs in the background. Use the job_id to check status.
    
    Args:
        request: BacktrackingRequest with checkpoint_file or checkpoint_id specified
        background_tasks: FastAPI background tasks
    
    Returns:
        Dict with job_id and initial status
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
            "retry_delay_seconds": request.retry_delay_seconds
        }
        
        # Create job
        job_id = create_job(config_dict)
        
        # Start background task (reuse the same background function, it handles resume logic)
        background_tasks.add_task(run_backtracking_background, job_id, config_dict)
        
        return {
            "success": True,
            "message": "Backtracking resume job started",
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "status_url": f"/backtracking/job/{job_id}",
            "created_at": get_job(job_id)["created_at"],
            "resumed": True,
            "checkpoint_file": request.checkpoint_file,
            "checkpoint_id": request.checkpoint_id
        }
        
    except Exception as e:
        print(f"Error starting resume backtracking job: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error starting resume backtracking job: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


