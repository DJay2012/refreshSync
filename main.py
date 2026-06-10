"""
FastAPI application for Elasticsearch refresh operations.
Provides high-performance API endpoints for refreshing single or batch documents.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Query, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
import uvicorn
import os
import uuid

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

# Global services
refresh_service: Optional[RefreshService] = None
metrics_collector: Optional[MetricsCollector] = None
client_sync_service: Optional[ClientSyncService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    global refresh_service, metrics_collector, client_sync_service
    
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
        
        # Start background tasks
        asyncio.create_task(metrics_collector.start_collection())
        
        logger.info("RefreshES API service started successfully")
        yield
        
    except Exception as e:
        logger.error(f"Failed to start RefreshES API service: {e}")
        raise
    finally:
        # Shutdown
        logger.info("Shutting down RefreshES API service...")
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
    description="High-performance API for refreshing Elasticsearch documents and uploading Excel charts data",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
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


@app.get("/", response_model=Dict[str, str])
async def root():
    """Root endpoint with API information."""
    return {
        "message": "RefreshES API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs"
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


async def background_refresh_social_batch(social_feed_ids: List[int], max_workers: int, service: RefreshService):
    """Background task for social feed batch refresh with chunked processing."""
    try:
        logger.info(f"Starting background social batch refresh for {len(social_feed_ids)} feeds")
        
        # Process in chunks to prevent timeouts
        chunk_size = 100
        total_successful = 0
        total_failed = 0
        
        for i in range(0, len(social_feed_ids), chunk_size):
            chunk = social_feed_ids[i:i + chunk_size]
            logger.info(f"Processing social batch chunk {i//chunk_size + 1}/{(len(social_feed_ids) + chunk_size - 1)//chunk_size}: {len(chunk)} feeds")
            
            try:
                results = await service.refresh_social_feeds_batch(chunk, max_workers)
                total_successful += results.successful_count
                total_failed += results.failed_count
                
                # Small delay between chunks
                await asyncio.sleep(0.1)
                
            except Exception as chunk_error:
                logger.error(f"Error processing social batch chunk: {chunk_error}")
                total_failed += len(chunk)
        
        logger.info(f"Completed background social batch refresh: {total_successful} successful, {total_failed} failed")
        
    except Exception as e:
        logger.error(f"Background social batch refresh failed: {e}")


# Batch endpoints must come before single endpoints to avoid routing conflicts
@app.post("/refresh/social/batch", response_model=BatchRefreshResponse)
async def refresh_batch_social(
    request: BatchRefreshRequest,
    background_tasks: BackgroundTasks,
    service: RefreshService = Depends(get_refresh_service)
):
    """Refresh multiple social feed documents in Elasticsearch."""
    start_time = time.time()
    
    try:
        # For batches over 150, use async processing
        if request.social_feed_ids and len(request.social_feed_ids) > 150:
            # Start background task without waiting
            asyncio.create_task(background_refresh_social_batch(
                request.social_feed_ids, 
                request.max_workers, 
                service
            ))
            
            return BatchRefreshResponse(
                total_requested=len(request.social_feed_ids),
                successful_count=0,
                failed_count=0,
                results=[],
                processing_time=time.time() - start_time,
                timestamp=datetime.utcnow()
            )
        
        # For smaller batches, process synchronously
        results = await service.refresh_social_feeds_batch(
            request.social_feed_ids,
            max_workers=request.max_workers
        )
        
        return BatchRefreshResponse(
            total_requested=len(request.social_feed_ids),
            successful_count=results.successful_count,
            failed_count=results.failed_count,
            results=results.results,
            processing_time=time.time() - start_time,
            timestamp=datetime.utcnow()
        )
        
    except Exception as e:
        logger.error(f"Error in batch social refresh: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Batch social refresh failed: {str(e)}"
        )


async def background_refresh_article_batch(article_ids: List[int], max_workers: int, service: RefreshService):
    """Background task for article batch refresh with chunked processing."""
    try:
        logger.info(f"Starting background article batch refresh for {len(article_ids)} articles")
        
        # Process in chunks to prevent timeouts
        chunk_size = 100
        total_successful = 0
        total_failed = 0
        
        for i in range(0, len(article_ids), chunk_size):
            chunk = article_ids[i:i + chunk_size]
            logger.info(f"Processing article batch chunk {i//chunk_size + 1}/{(len(article_ids) + chunk_size - 1)//chunk_size}: {len(chunk)} articles")
            
            try:
                results = await service.refresh_articles_batch(chunk, max_workers)
                total_successful += results.successful_count
                total_failed += results.failed_count
                
                # Small delay between chunks
                await asyncio.sleep(0.1)
                
            except Exception as chunk_error:
                logger.error(f"Error processing article batch chunk: {chunk_error}")
                total_failed += len(chunk)
        
        logger.info(f"Completed background article batch refresh: {total_successful} successful, {total_failed} failed")
        
    except Exception as e:
        logger.error(f"Background article batch refresh failed: {e}")


@app.post("/refresh/article/batch", response_model=BatchRefreshResponse)
async def refresh_batch_article(
    request: BatchRefreshRequest,
    background_tasks: BackgroundTasks,
    service: RefreshService = Depends(get_refresh_service)
):
    """Refresh multiple article documents in Elasticsearch."""
    start_time = time.time()
    
    try:
        # For batches over 150, use async processing
        if request.article_ids and len(request.article_ids) > 150:
            # Start background task without waiting
            asyncio.create_task(background_refresh_article_batch(
                request.article_ids, 
                request.max_workers, 
                service
            ))
            
            return BatchRefreshResponse(
                total_requested=len(request.article_ids),
                successful_count=0,
                failed_count=0,
                results=[],
                processing_time=time.time() - start_time,
                timestamp=datetime.utcnow()
            )
        
        # For smaller batches, process synchronously
        results = await service.refresh_articles_batch(
            request.article_ids,
            max_workers=request.max_workers
        )
        
        return BatchRefreshResponse(
            total_requested=len(request.article_ids),
            successful_count=results.successful_count,
            failed_count=results.failed_count,
            results=results.results,
            processing_time=time.time() - start_time,
            timestamp=datetime.utcnow()
        )
        
    except Exception as e:
        logger.error(f"Error in batch article refresh: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Batch article refresh failed: {str(e)}"
        )


@app.post("/refresh/social/{social_feed_id}", response_model=RefreshResponse)
async def refresh_single_social(
    social_feed_id: int,
    background_tasks: BackgroundTasks,
    service: RefreshService = Depends(get_refresh_service)
):
    """Refresh a single social feed document in Elasticsearch."""
    start_time = time.time()
    
    try:
        result = await service.refresh_social_feed(social_feed_id)
        
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
    
    try:
        result = await service.refresh_article(article_id)
        
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
