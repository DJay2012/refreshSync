# Quick Start: Backtracking API

## Prerequisites

1. Ensure MongoDB and Elasticsearch are running
2. Environment variables are configured in `.env`
3. API server is running

## Start the API

```bash
cd esBooleanTranslator
python run_api.py
```

The API will be available at `http://localhost:8000`

## Quick Test

### 1. Check if backtracking is available

```bash
curl http://localhost:8000/
```

Look for `"backtracking_available": true` in the response.

### 2. Check backtracking health

```bash
curl http://localhost:8000/backtracking/health
```

### 3. Run a test backtracking (dry run)

```bash
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2025-01-01",
    "end_date": "2025-01-05",
    "company_ids": ["CYBERPE865"],
    "dry_run": true
  }'
```

### 4. Run actual backtracking

```bash
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2025-01-01",
    "end_date": "2025-01-10",
    "company_ids": ["CYBERPE865", "HUL"],
    "language": "en",
    "batch_size": 100
  }'
```

## Expected Response

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
    "company_ids": ["CYBERPE865", "HUL"],
    "language": "en",
    "batch_size": 100,
    "dry_run": false
  }
}
```

## Common Issues

### Backtracking not available

Check that:
- `esBacktracking` directory exists
- `backtracking_engine.py` is present
- No import errors in API startup logs

### Connection errors

Verify:
- MongoDB is running and accessible
- Elasticsearch is running and accessible
- Environment variables are set correctly

### Timeout errors

For large date ranges:
- Reduce `batch_size` (e.g., 50 instead of 100)
- Reduce date range
- Increase API timeout settings

## Using Python

```python
import requests

# Configuration
config = {
    "start_date": "2025-01-01",
    "end_date": "2025-01-10",
    "company_ids": ["CYBERPE865"],
    "dry_run": False
}

# Run backtracking
response = requests.post(
    "http://localhost:8000/backtracking/run",
    json=config
)

# Check results
if response.status_code == 200:
    data = response.json()
    print(f"Success! Created {data['results']['total_tags_created']} tags")
else:
    print(f"Error: {response.text}")
```

## Full Documentation

See `BACKTRACKING_API_INTEGRATION.md` for complete documentation.













