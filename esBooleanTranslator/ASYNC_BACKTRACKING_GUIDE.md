# Asynchronous Backtracking Guide

## 🎯 Problem Solved

**Before:** The endpoint waited for backtracking to complete (could take hours for 2-year jobs) before returning a response.

**Now:** The endpoint returns immediately with a `job_id`, and backtracking runs in the background. You can check status anytime!

---

## 📋 New Flow

### 1. Start Backtracking Job

```bash
POST /backtracking/run
{
  "start_date": "2023-01-01",
  "end_date": "2025-01-01",
  "company_ids": ["CYBERPE865"]
}
```

**Response (returns immediately):**
```json
{
  "success": true,
  "message": "Backtracking job started",
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending",
  "status_url": "/backtracking/job/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "created_at": "2025-01-10T10:00:00"
}
```

**Backtracking now runs in background!** ✅

### 2. Check Job Status

```bash
GET /backtracking/job/{job_id}
```

**Response:**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "running",
  "created_at": "2025-01-10T10:00:00",
  "updated_at": "2025-01-10T10:05:00",
  "progress": {
    "chunks_processed": 25,
    "total_chunks": 104,
    "progress_percent": 24.04
  },
  "config": {
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "company_ids": ["CYBERPE865"]
  }
}
```

**When completed:**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "completed",
  "progress": {
    "chunks_processed": 104,
    "total_chunks": 104,
    "progress_percent": 100.0
  },
  "results": {
    "start_time": "2025-01-10T10:00:00",
    "end_time": "2025-01-10T15:30:00",
    "total_articles_processed": 52000,
    "total_social_feeds_processed": 28000,
    "total_tags_created": 1250,
    "processing_time_seconds": 19800.5,
    "errors": []
  }
}
```

### 3. List All Jobs

```bash
GET /backtracking/jobs?status_filter=running&limit=10
```

**Response:**
```json
{
  "total": 5,
  "jobs": [
    {
      "job_id": "a1b2c3d4...",
      "status": "running",
      "created_at": "2025-01-10T10:00:00",
      "updated_at": "2025-01-10T10:05:00",
      "progress": {
        "chunks_processed": 25,
        "total_chunks": 104,
        "progress_percent": 24.04
      }
    },
    ...
  ]
}
```

### 4. Cancel Job (pending only)

```bash
DELETE /backtracking/job/{job_id}
```

---

## 🔄 Complete Example

### Step 1: Start Job

```bash
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "company_ids": ["CYBERPE865"]
  }'
```

**Returns immediately:**
```json
{
  "job_id": "abc-123-def-456",
  "status": "pending"
}
```

### Step 2: Check Status (polling)

```bash
# Check status every 30 seconds
watch -n 30 'curl -s http://localhost:8000/backtracking/job/abc-123-def-456 | jq .'
```

**Or use Python:**
```python
import requests
import time

job_id = "abc-123-def-456"
status_url = f"http://localhost:8000/backtracking/job/{job_id}"

while True:
    response = requests.get(status_url)
    data = response.json()
    
    print(f"Status: {data['status']}")
    print(f"Progress: {data['progress']['progress_percent']:.1f}%")
    
    if data['status'] in ['completed', 'failed']:
        break
    
    time.sleep(30)  # Check every 30 seconds
```

### Step 3: Get Results

Once `status == "completed"`, the response includes full results:

```json
{
  "status": "completed",
  "results": {
    "total_tags_created": 1250,
    "processing_time_seconds": 19800.5
  }
}
```

---

## 📊 Job Statuses

| Status | Description |
|--------|-------------|
| `pending` | Job created, waiting to start |
| `running` | Currently processing chunks |
| `completed` | Finished successfully |
| `failed` | Failed after all retries |
| `cancelled` | Cancelled by user |

---

## 🎯 Benefits

### ✅ **Non-Blocking**
- API returns immediately
- No timeout issues
- Client can disconnect

### ✅ **Progress Tracking**
- Real-time progress updates
- Chunks processed / total chunks
- Progress percentage

### ✅ **Multiple Jobs**
- Can run multiple backtracking jobs simultaneously
- Each has unique job_id
- Track all jobs

### ✅ **Flexible**
- Check status anytime
- Poll at your own rate
- Can cancel pending jobs

---

## 🔧 Implementation Details

### Background Processing

Uses FastAPI's `BackgroundTasks`:
```python
background_tasks.add_task(run_backtracking_background, job_id, config_dict)
```

### Job Storage

Currently uses in-memory dictionary (thread-safe):
```python
backtracking_jobs: Dict[str, Dict[str, Any]] = {}
```

**For production:** Consider using:
- Redis (for distributed systems)
- PostgreSQL (for persistence)
- MongoDB (for document storage)

### Progress Updates

Progress is updated in real-time as chunks are processed:
- `chunks_processed`: Number of chunks completed
- `total_chunks`: Total chunks to process
- `progress_percent`: Percentage complete

---

## 🚀 Usage Patterns

### Pattern 1: Fire and Poll

```python
# Start job
response = requests.post("/backtracking/run", json=config)
job_id = response.json()["job_id"]

# Poll for completion
while True:
    status = requests.get(f"/backtracking/job/{job_id}").json()
    if status["status"] == "completed":
        print("Done!", status["results"])
        break
    time.sleep(30)
```

### Pattern 2: Webhook (Future Enhancement)

Could add webhook URL to notify when complete:
```json
{
  "start_date": "...",
  "webhook_url": "https://your-api.com/webhook"
}
```

### Pattern 3: Scheduled Check

Check status periodically in your app:
```python
# Every 5 minutes
schedule.every(5).minutes.do(check_backtracking_status)
```

---

## 📝 API Endpoints Summary

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/backtracking/run` | POST | Start job (returns immediately) |
| `/backtracking/job/{job_id}` | GET | Get job status |
| `/backtracking/jobs` | GET | List all jobs |
| `/backtracking/job/{job_id}` | DELETE | Cancel job |

---

## 🎬 Complete Workflow Example

```bash
# 1. Start 2-year backtracking
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "company_ids": ["CYBERPE865"]
  }'

# Response:
{
  "job_id": "abc-123",
  "status": "pending"
}

# 2. Check status (after 5 minutes)
curl "http://localhost:8000/backtracking/job/abc-123"

# Response:
{
  "status": "running",
  "progress": {
    "chunks_processed": 12,
    "total_chunks": 104,
    "progress_percent": 11.54
  }
}

# 3. Check again (after 2 hours)
curl "http://localhost:8000/backtracking/job/abc-123"

# Response:
{
  "status": "completed",
  "results": {
    "total_tags_created": 1250,
    "processing_time_seconds": 19800.5
  }
}
```

---

## ✅ Summary

**Now backtracking is asynchronous!**

- ✅ Endpoint returns immediately with `job_id`
- ✅ Backtracking runs in background
- ✅ Check status anytime
- ✅ Track progress in real-time
- ✅ Handle multiple jobs
- ✅ No timeout issues

**Perfect for long-running 2-year backtracking jobs!** 🎉













