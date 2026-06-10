# Backtracking API Integration

This document describes the integration of the esBacktracking module into the Boolean Inserter API.

## Overview

The backtracking functionality has been integrated into the Boolean Inserter API, allowing you to run backtracking processes through REST API endpoints.

## API Endpoints

### 1. Backtracking Health Check

**Endpoint:** `GET /backtracking/health`

**Description:** Check if the backtracking system is available and healthy.

**Response:**
```json
{
  "status": "healthy",
  "message": "Backtracking system is available",
  "backtracking_module": "available",
  "elasticsearch": true,
  "dependencies": {
    "mongodb": "configured",
    "elasticsearch": "configured"
  }
}
```

### 2. Run Backtracking

**Endpoint:** `POST /backtracking/run`

**Description:** Execute a backtracking process for specified companies and date range.

**Request Body:**
```json
{
  "start_date": "2025-01-01",
  "end_date": "2025-01-10",
  "company_ids": ["CYBERPE865", "HUL", "TATA"],
  "language": "en",
  "batch_size": 100,
  "max_workers": 4,
  "dry_run": false,
  "process_print": true,
  "process_online": true
}
```

**Parameters:**
- `start_date` (required): Start date in YYYY-MM-DD format
- `end_date` (required): End date in YYYY-MM-DD format
- `company_ids` (required): List of company IDs to process
- `language` (optional): Language code (default: "en")
- `batch_size` (optional): Number of articles to process per batch (default: 100)
- `max_workers` (optional): Maximum number of parallel workers (default: 4)
- `dry_run` (optional): If true, simulate without saving to MongoDB (default: false)
- `process_print` (optional): Process print articles (default: true)
- `process_online` (optional): Process social feeds (default: true)

**Response:**
```json
{
  "success": true,
  "message": "Backtracking completed successfully",
  "results": {
    "start_time": "2025-01-10T10:00:00",
    "end_time": "2025-01-10T10:05:00",
    "total_articles_processed": 1500,
    "total_social_feeds_processed": 800,
    "total_tags_created": 45,
    "processing_time_seconds": 300.5,
    "errors": []
  },
  "config": {
    "start_date": "2025-01-01",
    "end_date": "2025-01-10",
    "company_ids": ["CYBERPE865", "HUL", "TATA"],
    "language": "en",
    "batch_size": 100,
    "dry_run": false
  }
}
```

## Usage Examples

### Example 1: Basic Backtracking for Single Company

```bash
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2025-01-01",
    "end_date": "2025-01-10",
    "company_ids": ["CYBERPE865"]
  }'
```

### Example 2: Dry Run (Testing without Saving)

```bash
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2025-01-01",
    "end_date": "2025-01-10",
    "company_ids": ["CYBERPE865", "HUL"],
    "dry_run": true,
    "batch_size": 50
  }'
```

### Example 3: Multi-Language Backtracking

```bash
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2025-01-01",
    "end_date": "2025-01-10",
    "company_ids": ["CYBERPE865"],
    "language": "hi",
    "process_print": true,
    "process_online": false
  }'
```

### Example 4: Large-Scale Backtracking

```bash
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2024-12-01",
    "end_date": "2025-01-10",
    "company_ids": ["CYBERPE865", "HUL", "TATA", "RELIANCE", "INFOSYS"],
    "batch_size": 200,
    "max_workers": 8
  }'
```

## Python Example

```python
import requests

# Backtracking configuration
config = {
    "start_date": "2025-01-01",
    "end_date": "2025-01-10",
    "company_ids": ["CYBERPE865", "HUL"],
    "language": "en",
    "batch_size": 100,
    "max_workers": 4,
    "dry_run": False,
    "process_print": True,
    "process_online": True
}

# Run backtracking
response = requests.post(
    "http://localhost:8000/backtracking/run",
    json=config
)

results = response.json()
print(f"Tags created: {results['results']['total_tags_created']}")
print(f"Processing time: {results['results']['processing_time_seconds']}s")
```

## Integration Details

### Import Structure

The backtracking module is imported dynamically from the `esBacktracking` directory:

```python
from backtracking_engine import BacktrackingEngine, BacktrackingConfig
```

### Module Availability

The API checks if backtracking modules are available at startup. You can verify this by checking the root endpoint:

```bash
curl http://localhost:8000/
```

The response includes a `backtracking_available` field indicating whether the module is loaded.

### Error Handling

If the backtracking module is not available, API calls will return a 503 Service Unavailable status with an appropriate error message.

## Configuration

The backtracking system uses environment variables from the main `.env` file:

- `PG_MONGO_URI`: MongoDB connection string
- `PG_MONGO_DB`: MongoDB database name
- `ES_HOST`: Elasticsearch host
- `ES_USER`: Elasticsearch username
- `ES_PASSWORD`: Elasticsearch password
- `ES_INDEX_NAME`: Percolator index name

## Notes

- The backtracking process can take a significant amount of time depending on the date range and number of companies
- Consider using `dry_run=true` for testing before running the actual process
- Large date ranges may require adjustment of `batch_size` and `max_workers` parameters
- The process creates MongoDB tags for articles and social feeds that match company queries
- Results are tracked and returned after completion

## Troubleshooting

### Backtracking Module Not Available

If `backtracking_available` is `false`:
1. Verify the `esBacktracking` directory exists at the workspace root
2. Check that `backtracking_engine.py` exists and is importable
3. Review import errors in the API startup logs

### Connection Errors

If you encounter connection errors:
1. Verify MongoDB and Elasticsearch are running
2. Check environment variables are set correctly
3. Review network connectivity

### Timeout Issues

For large date ranges:
1. Reduce `batch_size` to process smaller chunks
2. Increase API timeout settings
3. Consider running backtracking in multiple smaller requests













