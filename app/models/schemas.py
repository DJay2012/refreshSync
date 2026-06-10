"""
Pydantic models for API request and response schemas.
"""

from datetime import datetime
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, validator


class RefreshRequest(BaseModel):
    """Base refresh request model."""
    document_id: Union[int, str] = Field(..., description="Document ID to refresh")
    document_type: str = Field(..., description="Type of document (social/article)")


class BatchRefreshRequest(BaseModel):
    """Batch refresh request model."""
    social_feed_ids: Optional[List[int]] = Field(None, description="List of social feed IDs")
    article_ids: Optional[List[int]] = Field(None, description="List of article IDs")
    max_workers: Optional[int] = Field(
        60,
        ge=1,
        le=200,
        description="Maximum number of workers (values under 60 will be elevated automatically)"
    )
    login_name: Optional[str] = Field(None, description="User login name for request tracking")
    
    @validator('social_feed_ids', 'article_ids')
    def validate_ids(cls, v):
        if v is not None and len(v) > 500:
            raise ValueError("Maximum 500 IDs allowed per batch")
        return v
    
    @validator('social_feed_ids', 'article_ids', pre=True)
    def validate_at_least_one_type(cls, v, values):
        if not v and not values.get('article_ids') and not values.get('social_feed_ids'):
            raise ValueError("At least one ID list must be provided")
        return v


class RefreshResult(BaseModel):
    """Individual refresh result."""
    document_id: str = Field(..., description="Document ID")
    document_type: str = Field(..., description="Document type")
    success: bool = Field(..., description="Whether refresh was successful")
    message: str = Field(..., description="Result message")
    processing_time: float = Field(..., description="Processing time in seconds")
    timestamp: datetime = Field(..., description="Processing timestamp")


class RefreshResponse(BaseModel):
    """Single refresh response."""
    success: bool = Field(..., description="Whether refresh was successful")
    message: str = Field(..., description="Result message")
    document_id: str = Field(..., description="Document ID")
    document_type: str = Field(..., description="Document type")
    processing_time: float = Field(..., description="Processing time in seconds")
    timestamp: datetime = Field(..., description="Response timestamp")


class BatchRefreshResults(BaseModel):
    """Batch refresh results container."""
    successful_count: int = Field(..., description="Number of successful refreshes")
    failed_count: int = Field(..., description="Number of failed refreshes")
    results: List[RefreshResult] = Field(..., description="Individual results")


class BatchRefreshResponse(BaseModel):
    """Batch refresh response."""
    total_requested: int = Field(..., description="Total number of documents requested")
    successful_count: int = Field(..., description="Number of successful refreshes")
    failed_count: int = Field(..., description="Number of failed refreshes")
    results: List[RefreshResult] = Field(..., description="Individual results")
    processing_time: float = Field(..., description="Total processing time in seconds")
    timestamp: datetime = Field(..., description="Response timestamp")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = Field(..., description="Service status")
    timestamp: datetime = Field(..., description="Check timestamp")
    services: Dict[str, bool] = Field(..., description="Service health status")
    error: Optional[str] = Field(None, description="Error message if unhealthy")


class MetricsData(BaseModel):
    """Metrics data point."""
    timestamp: datetime = Field(..., description="Metrics timestamp")
    value: float = Field(..., description="Metric value")
    labels: Dict[str, str] = Field(default_factory=dict, description="Metric labels")


class MetricsResponse(BaseModel):
    """Metrics response."""
    total_requests: int = Field(..., description="Total requests processed")
    successful_requests: int = Field(..., description="Successful requests")
    failed_requests: int = Field(..., description="Failed requests")
    average_response_time: float = Field(..., description="Average response time in seconds")
    requests_per_second: float = Field(..., description="Requests per second")
    active_workers: int = Field(..., description="Currently active workers")
    queue_size: int = Field(..., description="Current queue size")
    uptime: float = Field(..., description="Service uptime in seconds")
    timestamp: datetime = Field(..., description="Metrics timestamp")
    
    # Detailed metrics
    social_refreshes: int = Field(..., description="Total social feed refreshes")
    article_refreshes: int = Field(..., description="Total article refreshes")
    batch_refreshes: int = Field(..., description="Total batch refreshes")
    
    # Performance metrics
    response_time_percentiles: Dict[str, float] = Field(
        default_factory=dict, 
        description="Response time percentiles"
    )
    
    # Error metrics
    error_rates: Dict[str, float] = Field(
        default_factory=dict,
        description="Error rates by type"
    )


class ErrorResponse(BaseModel):
    """Error response model."""
    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Error details")
    timestamp: datetime = Field(..., description="Error timestamp")
    request_id: Optional[str] = Field(None, description="Request ID for tracking")


class ClientSyncRequest(BaseModel):
    """Request model for client sync operations."""
    client_id: str = Field(..., description="Client ID to sync")
    sync_types: Optional[List[str]] = Field(
        default=["cbcp", "cponline"], 
        description="Types of sync to perform (cbcp, cponline, or both)"
    )
    
    @validator('sync_types')
    def validate_sync_types(cls, v):
        valid_types = {"cbcp", "cponline"}
        if not all(sync_type in valid_types for sync_type in v):
            raise ValueError(f"Invalid sync type. Must be one of: {valid_types}")
        return v


class ClientSyncResult(BaseModel):
    """Result model for individual client sync operations."""
    client_id: str = Field(..., description="Client ID")
    sync_type: str = Field(..., description="Type of sync performed")
    success: bool = Field(..., description="Whether sync was successful")
    message: str = Field(..., description="Result message")
    documents_processed: int = Field(0, description="Number of documents processed")
    documents_indexed: int = Field(0, description="Number of documents indexed")
    documents_deleted: int = Field(0, description="Number of documents deleted")
    processing_time: float = Field(..., description="Processing time in seconds")
    timestamp: datetime = Field(..., description="Processing timestamp")


class ClientSyncResponse(BaseModel):
    """Response model for client sync operations."""
    client_id: str = Field(..., description="Client ID")
    success: bool = Field(..., description="Whether overall sync was successful")
    message: str = Field(..., description="Overall result message")
    results: List[ClientSyncResult] = Field(..., description="Individual sync results")
    total_processing_time: float = Field(..., description="Total processing time in seconds")
    timestamp: datetime = Field(..., description="Response timestamp")


class AsyncSyncResponse(BaseModel):
    """Response model for async sync operations."""
    client_id: str = Field(..., description="Client ID")
    sync_type: str = Field(..., description="Type of sync (cbcp/cponline)")
    message: str = Field(..., description="Status message")
    status: str = Field(..., description="Status (started/running/completed/failed)")
    timestamp: datetime = Field(..., description="Response timestamp")


# Charts API Schemas
class ColumnValidation(BaseModel):
    """Column validation result."""
    column: str = Field(..., description="Column name")
    description: str = Field(..., description="Column description")


class ValidationError(BaseModel):
    """Validation error details."""
    message: str = Field(..., description="Error message")
    column: Optional[str] = Field(None, description="Column name if applicable")
    row: Optional[int] = Field(None, description="Row number if applicable")


class ExcelValidationResponse(BaseModel):
    """Excel file validation response."""
    file_path: str = Field(..., description="File path")
    upload_type: str = Field(..., description="Detected upload type (social/print)")
    timestamp: datetime = Field(..., description="Validation timestamp")
    overall_valid: bool = Field(..., description="Whether file is valid")
    summary: Dict[str, Any] = Field(..., description="Validation summary")
    column_validation: Optional[Dict[str, Any]] = Field(None, description="Column validation results")
    data_validation: Optional[Dict[str, Any]] = Field(None, description="Data validation results")
    error: Optional[str] = Field(None, description="Error message if validation failed")


class UploadJobResponse(BaseModel):
    """Upload job response."""
    job_id: str = Field(..., description="Job ID")
    status: str = Field(..., description="Job status (queued/processing/completed/failed)")
    timestamp: datetime = Field(..., description="Response timestamp")


class UploadStatusResponse(BaseModel):
    """Upload status response."""
    job_id: str = Field(..., description="Job ID")
    status: str = Field(..., description="Job status")
    type: str = Field(..., description="Upload type (social/print)")
    created_at: datetime = Field(..., description="Job creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    summary: Optional[Dict[str, Any]] = Field(None, description="Summary of upload")
    error: Optional[Dict[str, Any]] = Field(None, description="Error details if failed")