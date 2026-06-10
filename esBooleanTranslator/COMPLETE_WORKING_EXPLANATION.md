# Complete Backtracking Endpoint Working Explanation

## 🎯 Overview

The backtracking endpoint now works asynchronously with MongoDB-backed job storage. Jobs run in the background, progress is tracked in MongoDB, and you can check status anytime.

---

## 📋 Complete Flow: Step-by-Step

### Step 1: Client Sends Request

```bash
POST /backtracking/run
Content-Type: application/json

{
  "start_date": "2023-01-01",
  "end_date": "2025-01-01",
  "company_ids": ["CYBERPE865"]
}
```

### Step 2: API Endpoint Handler

```python
@app.post("/backtracking/run")
async def run_backtracking(request: BacktrackingRequest, background_tasks: BackgroundTasks):
    # 1. Validate request (Pydantic does this automatically)
    
    # 2. Create job in MongoDB
    job_id = create_job(config_dict)
    #    → Generates UUID: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    #    → Saves to MongoDB collection: backtrackingJobs
    #    → Document structure:
    #       {
    #         "_id": "a1b2c3d4...",
    #         "status": "pending",
    #         "config": {...},
    #         "progress": {...}
    #       }
    
    # 3. Start background task
    background_tasks.add_task(run_backtracking_background, job_id, config_dict)
    #    → Adds task to FastAPI's background task queue
    #    → Task runs in separate thread
    
    # 4. Return immediately!
    return {
        "job_id": job_id,
        "status": "pending",
        "status_url": "/backtracking/job/{job_id}"
    }
```

**Response arrives within milliseconds!** ✅

### Step 3: Background Task Starts

FastAPI automatically starts the background task:

```python
def run_backtracking_background(job_id: str, config_dict: Dict[str, Any]):
    # This runs in a background thread
    
    # 1. Update job status in MongoDB
    update_job_status(job_id, JobStatus.RUNNING)
    #    → MongoDB: db.backtrackingJobs.update_one(
    #         {"_id": job_id},
    #         {"$set": {"status": "running", "updated_at": "..."}}
    #       )
    
    # 2. Create BacktrackingConfig
    config = BacktrackingConfig(...)
    
    # 3. Initialize BacktrackingEngine
    engine = BacktrackingEngine(config)
    #    → Connects to Elasticsearch
    #    → Connects to MongoDB
    #    → Loads checkpoint if exists
    
    # 4. Calculate total chunks
    total_chunks = 104  # (730 days / 7 days per chunk)
    update_job_status(job_id, JobStatus.RUNNING, 
                     progress={"total_chunks": total_chunks})
    
    # 5. Run backtracking (this takes hours!)
    results = engine.run_backtracking(resume=True)
    #    → Processes chunks in loop
    #    → Creates MongoDB tags
    #    → Saves checkpoints
    #    → Auto-retries on crash
    
    # 6. Update final status
    update_job_status(job_id, JobStatus.COMPLETED, results=results)
```

### Step 4: Chunk Processing Loop

Inside `engine.run_backtracking()`:

```python
# Retry loop (max 3 attempts)
while retry_count <= 3:
    try:
        # Process date chunks
        chunks = [
            ("2023-01-01", "2023-01-07"),   # Chunk 1
            ("2023-01-08", "2023-01-14"),   # Chunk 2
            ...
            ("2024-12-25", "2025-01-01")   # Chunk 104
        ]
        
        for chunk_start, chunk_end in chunks:
            # Skip if already processed (from checkpoint)
            if chunk_key in processed_date_ranges:
                continue
            
            # Process this chunk
            process_date_range(chunk_start, chunk_end)
            #   → Fetch articles from Elasticsearch
            #   → Run company queries
            #   → Create MongoDB tags
            #   → Save tags to MongoDB
            
            # Mark as processed
            processed_date_ranges.add(chunk_key)
            
            # Save checkpoint (not job status, but checkpoint file)
            save_checkpoint()
        
        # Success!
        return results
        
    except Exception as e:
        # On crash:
        # 1. Save checkpoint
        save_checkpoint()
        # 2. Wait 10 seconds
        time.sleep(10)
        # 3. Retry
        retry_count += 1
        reload_checkpoint()
```

### Step 5: Client Checks Status

At any time, client can check status:

```bash
GET /backtracking/job/{job_id}
```

**What happens:**

```python
@app.get("/backtracking/job/{job_id}")
async def get_backtracking_job_status(job_id: str):
    # 1. Query MongoDB
    job = get_job(job_id)
    #    → db.backtrackingJobs.find_one({"_id": job_id})
    
    # 2. Return current status
    return {
        "job_id": job_id,
        "status": "running",  # or "completed", "failed", etc.
        "progress": {
            "chunks_processed": 25,
            "total_chunks": 104,
            "progress_percent": 24.04
        },
        "config": {...}
    }
```

---

## 🔄 Complete Timeline Example

### T=0: Start Job

```
[00:00:00] Client sends POST /backtracking/run
[00:00:00] API creates job in MongoDB
            → Document saved: {
                "_id": "abc-123",
                "status": "pending",
                "created_at": "2025-01-10T10:00:00"
              }
[00:00:00] API starts background task
[00:00:00] API returns response (within milliseconds!)
            → {"job_id": "abc-123", "status": "pending"}
```

### T=0-5: Background Processing Starts

```
[00:00:01] Background thread starts
[00:00:01] Updates MongoDB: status = "running"
[00:00:01] Connects to Elasticsearch ✅
[00:00:01] Connects to MongoDB ✅
[00:00:01] Loads checkpoint (if exists) ✅
[00:00:02] Calculates total chunks: 104
[00:00:02] Updates MongoDB: progress = {"total_chunks": 104}
```

### T=5-60: Processing Chunks

```
[00:00:05] Processing chunk 1/104 (2023-01-01 to 2023-01-07)
            → Fetch articles from ES
            → Run company queries
            → Create MongoDB tags
            → Save tags to MongoDB
[00:00:15] Chunk 1 complete ✅
            → Save checkpoint file
            → (Note: Job status NOT updated after each chunk, 
               only checkpoint file is saved)

[00:00:15] Processing chunk 2/104 (2023-01-08 to 2023-01-14)
[00:00:25] Chunk 2 complete ✅
...

[00:05:00] Processing chunk 50/104
[00:05:10] CRASH! 💥 (Network timeout)
            → Exception caught
            → Save checkpoint file ✅
            → Save error to MongoDB
            → Update job status: "failed" (temporarily)
            → Wait 10 seconds ⏱️
```

### T=60-65: Automatic Retry

```
[00:05:20] Automatic restart #1 🔄
            → Reload checkpoint ✅
            → Restore processed_date_ranges
            → Skip chunks 1-50 ⏭️
            → Continue from chunk 51 ▶️
            → Update MongoDB: status = "running"
```

### T=65-120: Continue Processing

```
[00:05:25] Processing chunk 51/104 ✅
[00:05:35] Processing chunk 52/104 ✅
...
[01:30:00] Processing chunk 104/104 ✅
[01:30:10] All chunks complete! ✅
```

### T=120: Completion

```
[01:30:10] Update MongoDB:
            → status = "completed"
            → results = {
                "total_tags_created": 1250,
                "processing_time_seconds": 5400
              }
            → progress = {
                "chunks_processed": 104,
                "progress_percent": 100.0
              }
[01:30:10] Clear checkpoint file
[01:30:10] Background thread completes
```

### T=125: Client Checks Status

```
[01:35:00] Client checks: GET /backtracking/job/abc-123
[01:35:00] API queries MongoDB
[01:35:00] Returns:
            {
              "status": "completed",
              "results": {
                "total_tags_created": 1250
              }
            }
```

---

## 🗄️ MongoDB Storage Details

### Collection: `backtrackingJobs`

All jobs are stored here:

```javascript
// MongoDB document structure
{
  "_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",  // Job ID (UUID string)
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "running",  // pending | running | completed | failed | cancelled
  "created_at": "2025-01-10T10:00:00",
  "updated_at": "2025-01-10T10:05:00",
  "config": {
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "company_ids": ["CYBERPE865"],
    "chunk_days": 7,
    "batch_size": 100,
    ...
  },
  "progress": {
    "chunks_processed": 25,
    "total_chunks": 104,
    "progress_percent": 24.04
  },
  "results": null,  // Set when completed
  "error": null     // Set if failed
}
```

### MongoDB Operations

**Create Job:**
```python
db.backtrackingJobs.insert_one({
    "_id": job_id,
    "status": "pending",
    ...
})
```

**Update Status:**
```python
db.backtrackingJobs.update_one(
    {"_id": job_id},
    {"$set": {
        "status": "running",
        "updated_at": "...",
        "progress": {...}
    }}
)
```

**Get Job:**
```python
db.backtrackingJobs.find_one({"_id": job_id})
```

**List Jobs:**
```python
db.backtrackingJobs.find({"status": "running"}).sort("created_at", -1)
```

---

## 🔄 Two-Stage Storage System

### 1. MongoDB (Job Status)
- **Purpose**: Track job lifecycle and progress
- **Updated**: When job starts, completes, fails, or progress changes
- **Collection**: `backtrackingJobs`
- **Fields**: status, progress, results, config

### 2. Checkpoint Files (Processing State)
- **Purpose**: Track which chunks are processed (for resume)
- **Updated**: After each chunk is processed
- **Location**: Filesystem (`backtracking_checkpoint_*.json`)
- **Fields**: processed_date_ranges, last_processed_date

**Why both?**
- **MongoDB**: Quick queries, job management, progress tracking
- **Checkpoint Files**: Detailed chunk-level state for resume

---

## 📊 Real-Time Flow Diagram

```
┌─────────────────────────────────────────────────────────┐
│ CLIENT                                                  │
└───────────┬─────────────────────────────────────────────┘
            │ POST /backtracking/run
            ▼
┌─────────────────────────────────────────────────────────┐
│ API ENDPOINT                                            │
│ 1. Validate request                                     │
│ 2. Create job in MongoDB                                │
│ 3. Start background task                                │
│ 4. Return job_id (immediately!)                        │
└───────────┬─────────────────────────────────────────────┘
            │ Response: {"job_id": "abc-123"}
            ▼
┌─────────────────────────────────────────────────────────┐
│ BACKGROUND THREAD                                       │
│ (runs independently)                                    │
│                                                          │
│ 1. Update MongoDB: status = "running"                   │
│ 2. Initialize BacktrackingEngine                        │
│ 3. Process chunks in loop:                              │
│    ├─ Chunk 1: Process → Save checkpoint                │
│    ├─ Chunk 2: Process → Save checkpoint                │
│    ├─ ...                                                │
│    └─ Chunk N: Process → Save checkpoint                │
│ 4. Update MongoDB: status = "completed"                 │
└─────────────────────────────────────────────────────────┘
            │
            │ (Periodically, client checks status)
            ▼
┌─────────────────────────────────────────────────────────┐
│ CLIENT                                                  │
│ GET /backtracking/job/{job_id}                         │
└───────────┬─────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────┐
│ API ENDPOINT                                            │
│ 1. Query MongoDB: db.backtrackingJobs.find_one()        │
│ 2. Return current status and progress                   │
└───────────┬─────────────────────────────────────────────┘
            │ Response: {"status": "running", "progress": {...}}
            ▼
┌─────────────────────────────────────────────────────────┐
│ CLIENT                                                  │
│ (Polls periodically until completed)                   │
└─────────────────────────────────────────────────────────┘
```

---

## 🎯 Key Points

### 1. **Asynchronous Processing**
- Endpoint returns immediately
- Backtracking runs in background thread
- Client can disconnect safely

### 2. **MongoDB-Backed Storage**
- Jobs stored in `backtrackingJobs` collection
- Persistent across server restarts
- Queryable by status, date, company, etc.

### 3. **Progress Tracking**
- Updated in MongoDB during processing
- Can check progress anytime
- Real-time status updates

### 4. **Automatic Crash Recovery**
- Saves checkpoint file on crash
- Auto-retries up to 3 times
- Resumes from last processed chunk

### 5. **Two Storage Systems**
- **MongoDB**: Job status, progress, results
- **Checkpoint Files**: Detailed chunk processing state

---

## 💡 Example: Complete 2-Year Backtracking

```bash
# 1. Start job (returns immediately)
$ curl -X POST "http://localhost:8000/backtracking/run" \
    -d '{"start_date": "2023-01-01", "end_date": "2025-01-01", ...}'

Response (immediate):
{
  "job_id": "abc-123",
  "status": "pending"
}

# MongoDB now contains:
{
  "_id": "abc-123",
  "status": "pending",
  "config": {...}
}

# 2. Background processing starts (automatic)
# MongoDB updated:
{
  "_id": "abc-123",
  "status": "running",
  "progress": {"total_chunks": 104}
}

# 3. Check status (after 5 minutes)
$ curl "http://localhost:8000/backtracking/job/abc-123"

Response:
{
  "status": "running",
  "progress": {
    "chunks_processed": 12,
    "total_chunks": 104,
    "progress_percent": 11.54
  }
}

# 4. Processing continues...
# (Even if client disconnects, job keeps running)

# 5. Check status again (after 2 hours)
$ curl "http://localhost:8000/backtracking/job/abc-123"

Response:
{
  "status": "completed",
  "results": {
    "total_tags_created": 1250,
    "processing_time_seconds": 7200
  }
}

# MongoDB now contains:
{
  "_id": "abc-123",
  "status": "completed",
  "results": {...},
  "progress": {"progress_percent": 100.0}
}
```

---

## 🔍 What Happens Behind the Scenes

### When Job Starts:
1. ✅ Job document created in MongoDB (`backtrackingJobs` collection)
2. ✅ Background thread starts
3. ✅ BacktrackingEngine initialized
4. ✅ Checkpoint file loaded (if exists)
5. ✅ Processing begins

### During Processing:
1. ✅ Chunks processed one by one
2. ✅ Checkpoint file saved after each chunk
3. ✅ MongoDB tags created and saved
4. ✅ Progress can be checked anytime via MongoDB

### On Crash:
1. ✅ Checkpoint file saved automatically
2. ✅ Error logged in MongoDB
3. ✅ Auto-retry triggered (waits 10 seconds)
4. ✅ Checkpoint reloaded
5. ✅ Processing resumes from last chunk

### On Completion:
1. ✅ Final results saved to MongoDB
2. ✅ Status updated to "completed"
3. ✅ Checkpoint file deleted
4. ✅ Background thread ends

---

## ✅ Summary

**How it works:**

1. **Request** → Job created in MongoDB → Returns `job_id` immediately
2. **Background** → Processing runs independently → Updates MongoDB periodically
3. **Status Check** → Query MongoDB → Return current status and progress
4. **Completion** → Results saved to MongoDB → Status = "completed"

**Key Features:**
- ✅ Non-blocking (returns immediately)
- ✅ Persistent (MongoDB storage)
- ✅ Progress tracking (real-time updates)
- ✅ Crash recovery (auto-retry with checkpoints)
- ✅ Queryable (MongoDB queries)

**Perfect for long-running 2-year backtracking jobs!** 🎉













