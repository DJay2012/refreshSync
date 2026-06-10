"""
Monitoring and metrics collection service.
"""

import asyncio
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import statistics

from app.config import settings
from app.models.schemas import MetricsResponse
from app.utils.logger import get_logger

logger = get_logger(__name__)


class MetricsCollector:
    """Collects and aggregates application metrics."""
    
    def __init__(self):
        self.start_time = time.time()
        self._running = False
        self._collection_task: Optional[asyncio.Task] = None
        
        # Request metrics
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        
        # Response time tracking
        self.response_times: deque = deque(maxlen=1000)
        self.recent_response_times: deque = deque(maxlen=100)
        
        # Document type metrics
        self.social_refreshes = 0
        self.article_refreshes = 0
        self.batch_refreshes = 0
        
        # Error tracking
        self.error_counts: Dict[str, int] = defaultdict(int)
        
        # Worker metrics
        self.active_workers = 0
        self.queue_size = 0
        
        # Rate tracking
        self.request_timestamps: deque = deque(maxlen=10000)
        
    async def start_collection(self):
        """Start the metrics collection task."""
        if self._running:
            return
        
        self._running = True
        self._collection_task = asyncio.create_task(self._collect_metrics())
        logger.info("Metrics collection started")
    
    async def stop_collection(self):
        """Stop the metrics collection task."""
        self._running = False
        if self._collection_task:
            self._collection_task.cancel()
            try:
                await self._collection_task
            except asyncio.CancelledError:
                pass
        logger.info("Metrics collection stopped")
    
    async def _collect_metrics(self):
        """Background task to collect and process metrics."""
        while self._running:
            try:
                # Clean up old request timestamps
                cutoff_time = time.time() - 300  # 5 minutes
                while (self.request_timestamps and 
                       self.request_timestamps[0] < cutoff_time):
                    self.request_timestamps.popleft()
                
                # Update recent response times
                if len(self.response_times) > 100:
                    recent_times = list(self.response_times)[-100:]
                    self.recent_response_times.clear()
                    self.recent_response_times.extend(recent_times)
                
                await asyncio.sleep(settings.METRICS_INTERVAL)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in metrics collection: {e}")
                await asyncio.sleep(5)
    
    def record_refresh(self, document_type: str, processing_time: float, success: bool):
        """Record a refresh operation."""
        self.total_requests += 1
        self.request_timestamps.append(time.time())
        
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1
        
        self.response_times.append(processing_time)
        
        if document_type == "social":
            self.social_refreshes += 1
        elif document_type == "article":
            self.article_refreshes += 1
    
    def record_batch_refresh(
        self, 
        document_type: str, 
        total_count: int, 
        processing_time: float, 
        successful_count: int
    ):
        """Record a batch refresh operation."""
        self.batch_refreshes += 1
        self.total_requests += total_count
        self.successful_requests += successful_count
        self.failed_requests += (total_count - successful_count)
        
        # Add multiple timestamps for batch operations
        current_time = time.time()
        for _ in range(total_count):
            self.request_timestamps.append(current_time)
        
        # Record average processing time per document
        avg_time_per_doc = processing_time / total_count if total_count > 0 else 0
        self.response_times.append(avg_time_per_doc)
        
        if document_type == "social":
            self.social_refreshes += total_count
        elif document_type == "article":
            self.article_refreshes += total_count
    
    def record_error(self, error_type: str):
        """Record an error occurrence."""
        self.error_counts[error_type] += 1
    
    def update_worker_metrics(self, active_workers: int, queue_size: int):
        """Update worker and queue metrics."""
        self.active_workers = active_workers
        self.queue_size = queue_size
    
    async def get_metrics(self) -> MetricsResponse:
        """Get current metrics."""
        current_time = time.time()
        uptime = current_time - self.start_time
        
        # Calculate requests per second (last 60 seconds)
        recent_requests = sum(
            1 for timestamp in self.request_timestamps 
            if timestamp > current_time - 60
        )
        requests_per_second = recent_requests / 60.0
        
        # Calculate response time percentiles
        response_time_percentiles = {}
        if self.response_times:
            times = list(self.response_times)
            response_time_percentiles = {
                "p50": statistics.median(times),
                "p90": statistics.quantiles(times, n=10)[8] if len(times) >= 10 else max(times),
                "p95": statistics.quantiles(times, n=20)[18] if len(times) >= 20 else max(times),
                "p99": statistics.quantiles(times, n=100)[98] if len(times) >= 100 else max(times)
            }
        
        # Calculate average response time
        average_response_time = (
            statistics.mean(self.response_times) 
            if self.response_times else 0.0
        )
        
        # Calculate error rates
        error_rates = {}
        if self.total_requests > 0:
            error_rates["overall"] = self.failed_requests / self.total_requests
            
            for error_type, count in self.error_counts.items():
                error_rates[error_type] = count / self.total_requests
        
        return MetricsResponse(
            total_requests=self.total_requests,
            successful_requests=self.successful_requests,
            failed_requests=self.failed_requests,
            average_response_time=average_response_time,
            requests_per_second=requests_per_second,
            active_workers=self.active_workers,
            queue_size=self.queue_size,
            uptime=uptime,
            timestamp=datetime.utcnow(),
            social_refreshes=self.social_refreshes,
            article_refreshes=self.article_refreshes,
            batch_refreshes=self.batch_refreshes,
            response_time_percentiles=response_time_percentiles,
            error_rates=error_rates
        )
    
    def get_health_status(self) -> Dict[str, bool]:
        """Get health status of various components."""
        return {
            "metrics_collector": self._running,
            "data_collection": len(self.response_times) > 0,
            "recent_activity": (
                len(self.request_timestamps) > 0 and 
                self.request_timestamps[-1] > time.time() - 300
            )
        }
    
    def reset_metrics(self):
        """Reset all metrics (for testing purposes)."""
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.response_times.clear()
        self.recent_response_times.clear()
        self.social_refreshes = 0
        self.article_refreshes = 0
        self.batch_refreshes = 0
        self.error_counts.clear()
        self.request_timestamps.clear()
        self.start_time = time.time()
        logger.info("Metrics reset")
