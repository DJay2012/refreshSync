# Full Backtracking Endpoint Explained

## 🎯 Complete Flow: API Request → Processing → Response

This document explains the **entire flow** of the `/backtracking/run` endpoint, from when you send an HTTP request to when you get results back.

---

## 📥 Step 1: API Request

### HTTP Request

```bash
POST http://localhost:8000/backtracking/run
Content-Type: application/json

{
  "start_date": "2023-01-01",
  "end_date": "2025-01-01",
  "company_ids": ["CYBERPE865"],
  "language": "en",
  "batch_size": 100,
  "max_workers": 4,
  "dry_run": false,
  "process_print": true,
  "process_online": true,
  "enable_checkpoints": true,
  "chunk_days": 7,
  "resume": true,
  "auto_resume_on_crash": true,
  "max_auto_retries": 3,
  "retry_delay_seconds": 10
}
```

### Request Model (Pydantic)

```python
class BacktrackingRequest(BaseModel):
    start_date: str              # Required: "YYYY-MM-DD"
    end_date: str                # Required: "YYYY-MM-DD"
    company_ids: list[str]       # Required: ["CYBERPE865", ...]
    language: str = "en"         # Optional: Default "en"
    batch_size: int = 100        # Optional: Default 100
    max_workers: int = 4         # Optional: Default 4
    dry_run: bool = False        # Optional: Default False
    process_print: bool = True   # Optional: Default True
    process_online: bool = True # Optional: Default True
    enable_checkpoints: bool = True      # Optional: Default True
    chunk_days: int = 7          # Optional: Default 7
    resume: bool = True          # Optional: Default True
    checkpoint_file: Optional[str] = None  # Optional: Auto-generated
    auto_resume_on_crash: bool = True      # Optional: Default True
    max_auto_retries: int = 3             # Optional: Default 3
    retry_delay_seconds: int = 10         # Optional: Default 10
```

---

## 🔄 Step 2: Endpoint Handler

### API Endpoint Code Flow

```python
@app.post("/backtracking/run")
async def run_backtracking(request: BacktrackingRequest):
    """
    This is the main entry point for backtracking requests
    """
    
    # ┌─────────────────────────────────────────┐
    # │ 1. CHECK AVAILABILITY                   │
    # └─────────────────────────────────────────┘
    if not BACKTRACKING_AVAILABLE:
        raise HTTPException(503, "Backtracking module not available")
    
    # ┌─────────────────────────────────────────┐
    # │ 2. CREATE CONFIGURATION                 │
    # └─────────────────────────────────────────┘
    config = BacktrackingConfig(
        start_date=request.start_date,
        end_date=request.end_date,
        company_ids=request.company_ids,
        language=request.language,
        batch_size=request.batch_size,
        max_workers=request.max_workers,
        dry_run=request.dry_run,
        process_print=request.process_print,
        process_online=request.process_online,
        enable_checkpoints=request.enable_checkpoints,
        chunk_days=request.chunk_days,
        auto_resume_on_crash=request.auto_resume_on_crash,
        max_auto_retries=request.max_auto_retries,
        retry_delay_seconds=request.retry_delay_seconds
    )
    
    # Override checkpoint file if provided
    if request.checkpoint_file:
        config.checkpoint_file = request.checkpoint_file
    
    # ┌─────────────────────────────────────────┐
    # │ 3. INITIALIZE ENGINE                     │
    # └─────────────────────────────────────────┘
    engine = BacktrackingEngine(config)
    # This creates:
    #   - Elasticsearch client connection
    #   - MongoDB client connection
    #   - ESPreview engine (for company queries)
    #   - Data retriever (for historical articles)
    #   - Tag creator (for MongoDB tags)
    #   - Loads checkpoint if exists
    
    # ┌─────────────────────────────────────────┐
    # │ 4. RUN BACKTRACKING                     │
    # └─────────────────────────────────────────┘
    results = engine.run_backtracking(resume=request.resume)
    # This executes the full backtracking process:
    #   - Processes date chunks
    #   - Runs company queries
    #   - Creates MongoDB tags
    #   - Saves checkpoints
    #   - Handles crashes and retries
    
    # ┌─────────────────────────────────────────┐
    # │ 5. FORMAT RESPONSE                       │
    # └─────────────────────────────────────────┘
    return {
        "success": True,
        "message": "Backtracking completed successfully",
        "results": {...},
        "config": {...}
    }
```

---

## 🏗️ Step 3: BacktrackingEngine Initialization

### What Happens When Engine is Created

```python
engine = BacktrackingEngine(config)

# Inside __init__:
def __init__(self, config: BacktrackingConfig):
    self.config = config
    
    # 1. Initialize ESPreview engine (for company queries)
    self.espreview_config = ESPreviewConfig.from_env()
    self.espreview_engine = ESPreviewEngine(self.espreview_config)
    #   → Connects to Elasticsearch
    #   → Loads percolator index settings
    
    # 2. Initialize MongoDB tag creator
    self.mongo_creator = MongoTagCreator(config)
    #   → Connects to MongoDB
    #   → Tests connection
    
    # 3. Initialize Elasticsearch data retriever
    self.es_retriever = ElasticsearchDataRetriever(
        config, 
        self.espreview_engine.es_client
    )
    #   → Uses same ES client
    #   → Configures index names
    
    # 4. Initialize results tracking
    self.results = {
        "start_time": datetime.now(),
        "end_time": None,
        "total_articles_processed": 0,
        "total_social_feeds_processed": 0,
        "total_tags_created": 0,
        "company_results": {},
        "errors": []
    }
    
    # 5. Initialize checkpoint system
    self.checkpoint_data = None
    self.processed_date_ranges = set()
    
    # 6. Load checkpoint if exists
    if self.config.enable_checkpoints:
        self._load_checkpoint()
        #   → Reads checkpoint file
        #   → Restores processed_date_ranges
        #   → Restores progress counts
```

---

## 🔄 Step 4: Execution Flow

### Complete Processing Flow

```
┌─────────────────────────────────────────────────────────────┐
│ API Endpoint: POST /backtracking/run                       │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. Validate Request (Pydantic)                            │
│    - Check required fields                                 │
│    - Validate date formats                                 │
│    - Set defaults for optional fields                      │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Create BacktrackingConfig                               │
│    - Convert request params to config object                │
│    - Generate checkpoint filename if not provided          │
│    - Load environment variables                            │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Initialize BacktrackingEngine                          │
│    ├─ Connect to Elasticsearch                             │
│    ├─ Connect to MongoDB                                   │
│    ├─ Initialize ESPreview engine                          │
│    ├─ Initialize data retrievers                           │
│    └─ Load checkpoint (if exists)                          │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Call engine.run_backtracking(resume=True)               │
│    │                                                        │
│    ├─ Retry Loop (max 3 attempts)                          │
│    │   │                                                    │
│    │   ├─ Try: _run_backtracking_attempt()                 │
│    │   │   │                                                │
│    │   │   ├─ If checkpoint exists:                        │
│    │   │   │   └─ Load processed ranges                     │
│    │   │   │   └─ Restore progress counts                   │
│    │   │   │                                                │
│    │   │   ├─ Process date chunks:                         │
│    │   │   │   ├─ Generate all chunks (7 days each)        │
│    │   │   │   ├─ For each chunk:                           │
│    │   │   │   │   ├─ Skip if already processed            │
│    │   │   │   │   ├─ Fetch articles from ES               │
│    │   │   │   │   ├─ Run company queries                  │
│    │   │   │   │   ├─ Create MongoDB tags                  │
│    │   │   │   │   ├─ Mark chunk as processed              │
│    │   │   │   │   └─ Save checkpoint                      │
│    │   │   │                                                │
│    │   │   └─ Return results                                │
│    │   │                                                    │
│    │   └─ On Exception:                                    │
│    │       ├─ Save checkpoint                              │
│    │       ├─ Wait (retry_delay_seconds)                   │
│    │       ├─ Reload checkpoint                            │
│    │       └─ Retry (up to max_auto_retries)               │
│    │                                                        │
│    └─ Return final results                                 │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Format Response                                          │
│    - Extract key metrics                                   │
│    - Include configuration used                            │
│    - Include errors (if any)                               │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. Return JSON Response                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 📊 Step 5: Processing Details

### Chunk Processing Loop

```python
# Inside process_date_range_chunked():

# 1. Generate chunks
chunks = [
    ("2023-01-01", "2023-01-07"),   # Chunk 1
    ("2023-01-08", "2023-01-14"),   # Chunk 2
    ...
    ("2024-12-25", "2025-01-01")    # Chunk 104
]

# 2. Process each chunk
for chunk_start, chunk_end in chunks:
    chunk_key = f"{chunk_start}_{chunk_end}"
    
    # Skip if already done
    if chunk_key in processed_date_ranges:
        continue
    
    # Process this chunk
    process_date_range(chunk_start, chunk_end)
    #   ├─ Fetch articles from Elasticsearch
    #   ├─ For each company:
    #   │   ├─ Get company query from percolator index
    #   │   ├─ Execute query against articles
    #   │   └─ For matching articles:
    #   │       └─ Create MongoDB tag
    #   └─ Save tags to MongoDB
    
    # Mark as processed
    processed_date_ranges.add(chunk_key)
    
    # Save checkpoint
    save_checkpoint()
```

### Article Processing Flow

```python
# Inside process_date_range():

# 1. Fetch articles from Elasticsearch
articles = es_retriever.get_historical_articles(
    start_date="2023-01-01",
    end_date="2023-01-07"
)
# Returns: List of articles with content

# 2. For each company
for company_id in company_ids:
    # Get company's boolean query
    query_result = espreview_engine.execute_company_query(
        company_id="CYBERPE865",
        language="en"
    )
    # Returns: Matching article IDs
    
    # For each matching article
    for article in matching_articles:
        # Create MongoDB tag
        tag_doc = mongo_creator.create_article_tag(
            article_id=article.id,
            company_id=company_id,
            ...
        )
        
        # Save to MongoDB
        mongo_creator.save_tags_to_mongo([tag_doc])
```

---

## 📤 Step 6: Response Format

### Success Response

```json
{
  "success": true,
  "message": "Backtracking completed successfully",
  "results": {
    "start_time": "2025-01-10T10:00:00",
    "end_time": "2025-01-10T15:30:00",
    "total_articles_processed": 52000,
    "total_social_feeds_processed": 28000,
    "total_tags_created": 1250,
    "processing_time_seconds": 19800.5,
    "errors": []
  },
  "config": {
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "company_ids": ["CYBERPE865"],
    "language": "en",
    "batch_size": 100,
    "dry_run": false
  }
}
```

### Error Response (with retries)

```json
{
  "success": false,
  "message": "Backtracking failed after 3 retries",
  "results": {
    "start_time": "2025-01-10T10:00:00",
    "end_time": null,
    "total_articles_processed": 35000,
    "total_social_feeds_processed": 18000,
    "total_tags_created": 850,
    "processing_time_seconds": 12000.0,
    "errors": [
      "Max retries exceeded. Last error: Connection timeout",
      "Retry 1 failed: Database connection lost",
      "Retry 2 failed: Elasticsearch timeout"
    ]
  },
  "config": {...}
}
```

---

## 🔍 Complete Example: End-to-End

### Request

```bash
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "company_ids": ["CYBERPE865"],
    "chunk_days": 7
  }'
```

### What Happens Internally

```
[Step 1] API receives request
         ↓
[Step 2] Validates JSON → Creates BacktrackingRequest object
         ↓
[Step 3] Creates BacktrackingConfig
         - start_date: "2023-01-01"
         - end_date: "2025-01-01"
         - company_ids: ["CYBERPE865"]
         - checkpoint_file: "backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01.json"
         ↓
[Step 4] Initializes BacktrackingEngine
         - Connects to Elasticsearch ✅
         - Connects to MongoDB ✅
         - Loads checkpoint (if exists) ✅
         ↓
[Step 5] Calls engine.run_backtracking()
         ↓
[Step 6] Retry Loop Begins
         ├─ Attempt 1:
         │   ├─ Generate 104 chunks
         │   ├─ Process chunks 1-50 ✅
         │   ├─ CRASH at chunk 51 💥
         │   └─ Save checkpoint ✅
         │
         ├─ Wait 10 seconds ⏱️
         │
         ├─ Attempt 2 (Auto-retry):
         │   ├─ Load checkpoint ✅
         │   ├─ Skip chunks 1-50 ⏭️
         │   ├─ Process chunks 51-80 ✅
         │   ├─ CRASH at chunk 81 💥
         │   └─ Save checkpoint ✅
         │
         ├─ Wait 10 seconds ⏱️
         │
         └─ Attempt 3 (Auto-retry):
             ├─ Load checkpoint ✅
             ├─ Skip chunks 1-80 ⏭️
             ├─ Process chunks 81-104 ✅
             └─ SUCCESS! ✅
         ↓
[Step 7] Format response with results
         ↓
[Step 8] Return JSON response
```

### Response

```json
{
  "success": true,
  "message": "Backtracking completed successfully",
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

---

## 🎯 Key Points

### 1. **Synchronous Processing**
- The endpoint runs synchronously (blocks until complete)
- For long jobs, the HTTP request stays open
- Consider using async or background jobs for very long tasks

### 2. **Automatic Retry Built-In**
- Retries happen automatically inside `run_backtracking()`
- No need to manually call the endpoint again
- Up to 3 retries by default

### 3. **Checkpoint Integration**
- Checkpoints saved automatically during processing
- Checkpoint file name auto-generated from config
- Can specify custom checkpoint file if needed

### 4. **Error Handling**
- All errors caught and logged
- Checkpoint saved even on crash
- Returns partial results if some chunks succeed

### 5. **Progress Tracking**
- Progress saved after each chunk
- Can check status via `/backtracking/status/{checkpoint_file}`
- Results accumulate across retries

---

## 🔧 Configuration Options

### Required Fields
- `start_date`: Start date for backtracking
- `end_date`: End date for backtracking
- `company_ids`: List of company IDs to process

### Optional Fields (with defaults)
- `language`: Query language (default: "en")
- `batch_size`: Articles per batch (default: 100)
- `max_workers`: Parallel workers (default: 4)
- `dry_run`: Test without saving (default: false)
- `chunk_days`: Days per chunk (default: 7)
- `enable_checkpoints`: Enable checkpoints (default: true)
- `resume`: Auto-resume if checkpoint exists (default: true)
- `auto_resume_on_crash`: Auto-retry on crash (default: true)
- `max_auto_retries`: Max retry attempts (default: 3)
- `retry_delay_seconds`: Delay between retries (default: 10)

---

## 🎬 Summary

**The endpoint works like this:**

1. ✅ **Receives** HTTP POST request with JSON config
2. ✅ **Validates** request using Pydantic model
3. ✅ **Creates** BacktrackingConfig from request
4. ✅ **Initializes** BacktrackingEngine (connects to ES, MongoDB)
5. ✅ **Loads** checkpoint if exists (for resume)
6. ✅ **Executes** backtracking process:
   - Processes date chunks
   - Creates MongoDB tags
   - Saves checkpoints
   - Auto-retries on crash
7. ✅ **Returns** JSON response with results

**Even if crashes occur multiple times, the endpoint automatically handles retries and resumes until completion!**













