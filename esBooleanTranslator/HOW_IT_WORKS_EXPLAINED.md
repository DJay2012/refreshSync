# How Automatic Resume on Crash Works - Detailed Explanation

## 🎯 Overview

The backtracking system processes large date ranges (like 2 years) by breaking them into smaller chunks, saving progress after each chunk, and automatically restarting if a crash occurs.

## 📋 Step-by-Step Flow

### Phase 1: Initial Setup

```
1. User sends API request:
   {
     "start_date": "2023-01-01",
     "end_date": "2025-01-01",
     "company_ids": ["CYBERPE865"]
   }

2. System calculates date chunks:
   - Total days: 730 days (2 years)
   - Chunk size: 7 days (default)
   - Total chunks: 104 chunks
   
3. Creates checkpoint file name:
   "backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01.json"
```

### Phase 2: Chunk Processing Loop

```
for each chunk (104 chunks total):
    1. Check if chunk already processed (from checkpoint)
    2. If yes → Skip to next chunk
    3. If no → Process this chunk:
       - Fetch articles from Elasticsearch
       - Run company queries
       - Create MongoDB tags
       - Save results
    4. Mark chunk as "processed"
    5. Save checkpoint after every N chunks (default: every chunk)
```

### Phase 3: Crash Handling

```
If crash occurs during chunk processing:

1. Exception is caught
2. Checkpoint is IMMEDIATELY saved:
   {
     "last_processed_date": "2024-07-15",
     "processed_date_ranges": [
       "2023-01-01_2023-01-07",
       "2023-01-08_2023-01-14",
       ...
       "2024-07-08_2024-07-14"  // Last completed chunk
     ],
     "chunks_processed": 78,
     "total_articles_processed": 50000,
     "total_tags_created": 1200
   }

3. Wait 10 seconds (configurable)

4. Automatically restart:
   - Reload checkpoint file
   - Restore processed_date_ranges list
   - Continue from next unprocessed chunk
```

## 🔄 Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────┐
│  START: Run Backtracking                                │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│  Retry Loop (max 3 attempts)                           │
│  retry_count = 0                                        │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
         ┌────────────────┐
         │ Load Checkpoint│  (if exists)
         └────────┬───────┘
                  │
                  ▼
         ┌──────────────────────────────────────────┐
         │ Restore Processed Ranges                 │
         │ Skip already-processed chunks            │
         └────────┬─────────────────────────────────┘
                  │
                  ▼
         ┌──────────────────────────────────────────┐
         │ Process Chunks Loop                      │
         │                                          │
         │  for chunk in chunks:                    │
         │    if chunk in processed_ranges:         │
         │      skip                                │
         │    else:                                 │
         │      process_chunk()                      │
         │      mark_as_processed()                 │
         │      save_checkpoint()                   │
         └────────┬─────────────────────────────────┘
                  │
        ┌─────────┴─────────┐
        │                   │
        ▼                   ▼
   ✅ Success          ❌ Crash
        │                   │
        │                   ▼
        │          ┌─────────────────┐
        │          │ Save Checkpoint │
        │          └────────┬────────┘
        │                   │
        │                   ▼
        │          ┌─────────────────┐
        │          │ Wait 10 seconds │
        │          └────────┬────────┘
        │                   │
        │                   ▼
        │          ┌─────────────────┐
        │          │ retry_count++  │
        │          └────────┬────────┘
        │                   │
        │          ┌─────────┴─────────┐
        │          │                   │
        │          ▼                   ▼
        │    retry_count <= 3?    retry_count > 3?
        │          │                   │
        │          │                   ▼
        │          │          ┌─────────────────┐
        │          │          │ Return Results  │
        │          │          │ (with errors)   │
        │          │          └─────────────────┘
        │          │
        │          ▼
        │    ┌─────────────────┐
        │    │ Loop Back       │
        │    │ (Reload CP)     │
        │    └─────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  Clear Checkpoint File                                  │
│  Save Final Results                                     │
│  Return Success                                         │
└─────────────────────────────────────────────────────────┘
```

## 🔍 Detailed Code Flow

### 1. Entry Point: `run_backtracking()`

```python
def run_backtracking(self, resume: bool = True):
    retry_count = 0
    max_retries = 3  # Default
    
    while retry_count <= max_retries:
        try:
            # Try to run backtracking
            return self._run_backtracking_attempt(resume, retry_count)
        except Exception as e:
            retry_count += 1
            
            if retry_count > max_retries:
                # Give up, return with errors
                return results_with_errors
            
            # Wait 10 seconds
            time.sleep(10)
            
            # Reload checkpoint for next attempt
            self._load_checkpoint()
            resume = True  # Force resume
```

### 2. Single Attempt: `_run_backtracking_attempt()`

```python
def _run_backtracking_attempt(self, resume, attempt_number):
    # Load checkpoint if resuming
    if resume and checkpoint_exists:
        processed_ranges = load_from_checkpoint()
        restore_results_from_checkpoint()
    
    # Process chunks
    if enable_checkpoints:
        results = process_date_range_chunked()  # Uses chunks
    else:
        results = process_date_range()  # All at once
    
    # Success!
    clear_checkpoint()
    return results
```

### 3. Chunked Processing: `process_date_range_chunked()`

```python
def process_date_range_chunked(start_date, end_date):
    # 1. Generate all chunks
    chunks = [
        ("2023-01-01", "2023-01-07"),
        ("2023-01-08", "2023-01-14"),
        ...
        ("2024-12-25", "2025-01-01")
    ]
    
    # 2. Process each chunk
    for chunk_start, chunk_end in chunks:
        chunk_key = f"{chunk_start}_{chunk_end}"
        
        # Skip if already processed
        if chunk_key in processed_date_ranges:
            continue  # Skip!
        
        # Process this chunk
        try:
            process_chunk(chunk_start, chunk_end)
            mark_as_processed(chunk_key)
            save_checkpoint()  # After each chunk
        except Exception:
            save_checkpoint()  # Save even on error
            raise  # Re-raise to trigger retry
```

### 4. Checkpoint Structure

```json
{
  "config": {
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "company_ids": ["CYBERPE865"],
    "chunk_days": 7
  },
  "last_processed_date": "2024-07-15",
  "processed_date_ranges": [
    "2023-01-01_2023-01-07",
    "2023-01-08_2023-01-14",
    ...
    "2024-07-08_2024-07-14"
  ],
  "chunks_processed": 78,
  "total_articles_processed": 50000,
  "total_social_feeds_processed": 25000,
  "total_tags_created": 1200,
  "checkpoint_time": "2024-07-15T10:30:00",
  "errors": []
}
```

## 📊 Real Example: 2-Year Backtracking

### Timeline

```
[00:00] Start processing 104 chunks
[00:05] Chunk 1/104 (2023-01-01 to 2023-01-07) ✅ Done
[00:10] Chunk 2/104 (2023-01-08 to 2023-01-14) ✅ Done
...
[01:30] Chunk 50/104 (2024-07-08 to 2024-07-14) ✅ Done
[01:35] Chunk 51/104 (2024-07-15 to 2024-07-21) ❌ CRASH!

Checkpoint saved:
  - processed_date_ranges: [chunks 1-50]
  - chunks_processed: 50
  - last_processed_date: "2024-07-14"

[01:36] Waiting 10 seconds...
[01:46] *** AUTOMATIC RESTART #1 ***

Load checkpoint:
  - Found 50 processed chunks
  - Resume from chunk 51

[01:47] Chunk 1/104: SKIP (already processed) ⏭️
[01:47] Chunk 2/104: SKIP (already processed) ⏭️
...
[01:47] Chunk 50/104: SKIP (already processed) ⏭️
[01:47] Chunk 51/104: PROCESS (2024-07-15 to 2024-07-21) ✅
[01:52] Chunk 52/104: PROCESS (2024-07-22 to 2024-07-28) ✅
...
[03:00] Chunk 104/104: PROCESS ✅ Done!

[03:00] Clear checkpoint
[03:00] Save final results
[03:00] ✅ Complete!
```

## 🔑 Key Concepts

### 1. **Chunking**
- Splits large date ranges into smaller pieces (default: 7 days)
- Each chunk processed independently
- If one chunk fails, others can still succeed

### 2. **Checkpoint File**
- JSON file containing:
  - Which chunks are done
  - Current progress
  - Accumulated results
- Saved automatically after each chunk
- Loaded on restart

### 3. **Processed Ranges Tracking**
- `processed_date_ranges` = Set of chunk keys already done
- Example: `{"2023-01-01_2023-01-07", "2023-01-08_2023-01-14", ...}`
- Used to skip already-processed chunks

### 4. **Automatic Retry Loop**
- Wraps entire process in retry loop
- On crash: save checkpoint → wait → retry
- Up to 3 automatic retries (configurable)

## 🛡️ Safety Mechanisms

### 1. **No Duplicate Processing**
```python
if chunk_key in processed_date_ranges:
    skip()  # Never process same chunk twice
```

### 2. **Atomic Checkpoint Saves**
- Saved after each chunk completes
- Saved even if chunk fails
- Contains complete state

### 3. **Error Recovery**
- Errors saved in checkpoint
- Can continue with next chunk
- Full error history preserved

### 4. **User Interrupt Handling**
- KeyboardInterrupt (Ctrl+C) doesn't retry
- Checkpoint saved for manual resume
- Respects user's intent to stop

## 💡 Why This Works

1. **Small Chunks**: Processing 7 days at a time reduces crash impact
2. **Frequent Saves**: Checkpoint saved after every chunk
3. **State Tracking**: Knows exactly what's been done
4. **Automatic Retry**: Handles transient errors (network, DB, etc.)
5. **Idempotent**: Can restart any number of times safely

## 🎬 Complete Example Output

```
Starting backtracking process...
Date range: 2023-01-01 to 2025-01-01
Companies: CYBERPE865
Checkpoints enabled: True
Auto-resume on crash: True

Processing 104 date chunks (7 days each)

Chunk 1/104: Processing 2023-01-01 to 2023-01-07 ✅
Saving checkpoint...
Chunk 2/104: Processing 2023-01-08 to 2023-01-14 ✅
Saving checkpoint...
...
Chunk 50/104: Processing 2024-07-08 to 2024-07-14 ✅
Saving checkpoint...
Chunk 51/104: Processing 2024-07-15 to 2024-07-21
ERROR: Connection timeout
Saving checkpoint before retry...
*** AUTOMATIC RESTART #1 ***
Error: Connection timeout
Waiting 10 seconds before retry...

=== RETRY ATTEMPT #1 ===
*** RESUMING FROM CHECKPOINT ***
Last processed date: 2024-07-14
Progress: 50 chunks completed

Chunk 1/104: Skipping 2023-01-01 to 2023-01-07 (already processed) ⏭️
Chunk 2/104: Skipping 2023-01-08 to 2023-01-14 (already processed) ⏭️
...
Chunk 50/104: Skipping 2024-07-08 to 2024-07-14 (already processed) ⏭️
Chunk 51/104: Processing 2024-07-15 to 2024-07-21 ✅
Saving checkpoint...
Chunk 52/104: Processing 2024-07-22 to 2024-07-28 ✅
...
Chunk 104/104: Processing 2024-12-25 to 2025-01-01 ✅

Checkpoint cleared
Backtracking completed!
Total articles processed: 52000
Total tags created: 1250
```

## 🎯 Summary

**The system works by:**
1. ✅ Breaking work into small chunks (7 days each)
2. ✅ Tracking which chunks are done
3. ✅ Saving progress after each chunk
4. ✅ Automatically restarting on crash
5. ✅ Skipping already-done chunks
6. ✅ Retrying up to 3 times automatically

**Result: Even if crashes happen multiple times, progress is never lost and processing continues automatically!**













