# Backtracking API Integration Summary

## What Was Done

Successfully integrated the esBacktracking module into the Boolean Inserter API, adding REST API endpoints for running backtracking processes.

## Changes Made

### 1. Updated `esBooleanTranslator/src/api/boolean_inserter_api.py`

#### Added Imports
- Imported backtracking modules from `esBacktracking` directory
- Added dynamic path resolution for backtracking imports
- Added availability check flag (`BACKTRACKING_AVAILABLE`)

#### Added Pydantic Models
- `BacktrackingRequest`: Model for backtracking API requests
  - Fields: start_date, end_date, company_ids, language, batch_size, max_workers, dry_run, process_print, process_online

#### Added API Endpoints
- `GET /backtracking/health`: Health check for backtracking system
- `POST /backtracking/run`: Execute backtracking process

#### Updated Root Endpoint
- Added backtracking endpoints to the root endpoint documentation
- Added `backtracking_available` flag to indicate module availability

### 2. Created Documentation

#### `BACKTRACKING_API_INTEGRATION.md`
- Complete API documentation
- Usage examples in curl and Python
- Configuration details
- Troubleshooting guide

#### `example_backtracking_request.json`
- Sample JSON request for testing the backtracking endpoint

## API Usage

### Health Check
```bash
curl http://localhost:8000/backtracking/health
```

### Run Backtracking
```bash
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d @example_backtracking_request.json
```

## File Structure

```
refresh_es_api/
├── esBacktracking/              # Backtracking module
│   ├── backtracking_engine.py   # Main backtracking engine
│   ├── backtracking_config.py  # Configuration classes
│   └── ...
├── esBooleanTranslator/         # API module
│   ├── src/
│   │   └── api/
│   │       └── boolean_inserter_api.py  # Updated with backtracking endpoints
│   ├── BACKTRACKING_API_INTEGRATION.md  # API documentation
│   ├── example_backtracking_request.json  # Example request
│   └── INTEGRATION_SUMMARY.md   # This file
```

## Key Features

1. **Dynamic Import**: Backtracking modules are imported dynamically with fallback handling
2. **Health Check**: Separate health check endpoint for backtracking system
3. **Configuration**: Full support for all backtracking configuration options
4. **Error Handling**: Comprehensive error handling and reporting
5. **Dry Run**: Support for testing without saving to MongoDB
6. **Parallel Processing**: Configurable parallel processing options

## Environment Requirements

The backtracking system uses the same environment variables as the main application:

- `PG_MONGO_URI`: MongoDB connection string
- `PG_MONGO_DB`: MongoDB database name
- `ES_HOST`: Elasticsearch host
- `ES_USER`: Elasticsearch username
- `ES_PASSWORD`: Elasticsearch password
- `ES_INDEX_NAME`: Percolator index name

## Testing

1. Start the API server:
   ```bash
   python esBooleanTranslator/run_api.py
   ```

2. Check if backtracking is available:
   ```bash
   curl http://localhost:8000/
   ```

3. Run a dry-run test:
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

## Notes

- The backtracking process can take significant time for large date ranges
- Use `dry_run=true` for testing before running actual processes
- Adjust `batch_size` and `max_workers` based on system resources
- MongoDB and Elasticsearch must be accessible for backtracking to work

## Future Enhancements

Potential improvements:
- Async processing for long-running backtracking jobs
- Job status tracking endpoint
- Ability to cancel running backtracking jobs
- WebSocket updates for progress tracking
- Scheduled backtracking jobs













