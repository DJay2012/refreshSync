# Backtracking Resumability Guide

## Overview

The backtracking system now supports **automatic resumability** for long-running jobs. If a backtracking process crashes or is interrupted, it can automatically resume from where it left off without losing progress.

## How It Works

### Automatic Resume on Crash

The backtracking system now features **automatic crash recovery**:

1. **Processes dates in chunks** (default: 7-day chunks)
2. **Saves progress periodically** after each chunk or at specified intervals
3. **Tracks which date ranges have been processed** to avoid duplicates
4. **Automatically restarts and resumes** if a crash occurs
5. **Retries up to 3 times** (configurable) with automatic delay between retries

### Checkpoint System

When backtracking runs with checkpoints enabled (default), it:

- Automatically saves checkpoints during processing
- Detects crashes and saves checkpoint before exit
- Automatically restarts and resumes from checkpoint
- No manual intervention required!

### Automatic Resume

When you restart a backtracking job with the same configuration:
- The system automatically detects existing checkpoint files
- Skips already-processed date ranges
- Continues from the last checkpoint
- Only processes remaining chunks

## Features

### 1. Chunked Processing

Dates are split into manageable chunks (default: 7 days each). For a 2-year backtracking:

- **Total chunks**: ~104 chunks (730 days / 7 days per chunk)
- **Each chunk**: Processed independently
- **Progress saved**: After each chunk or at configured intervals

### 2. Checkpoint Files

Checkpoint files are automatically created with names like:
```
backtracking_checkpoint_CYBERPE865_HUL_TATA_2023-01-01_to_2025-01-01.json
```

They contain:
- Last processed date
- List of completed date ranges
- Progress statistics
- Current results
- Configuration used

### 3. Crash Recovery

If backtracking crashes:
- ✅ Progress is saved in checkpoint file
- ✅ Already-processed chunks are tracked
- ✅ No duplicate processing
- ✅ Can resume from exact point of failure

## Usage Examples

### Example 1: Starting a 2-Year Backtracking

```bash
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "company_ids": ["CYBERPE865"],
    "chunk_days": 7,
    "enable_checkpoints": true
  }'
```

**If it crashes after processing 1.5 years:**
- ✅ Checkpoint file is automatically saved
- ✅ Progress is preserved
- ✅ **Automatically restarts and resumes** (up to 3 retries)
- ✅ No manual action needed - it handles crashes automatically!

### Example 2: Checking Progress

```bash
# Check checkpoint status
curl "http://localhost:8000/backtracking/status/backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01.json"
```

Response:
```json
{
  "exists": true,
  "progress": {
    "chunks_processed": 78,
    "total_chunks": 104,
    "progress_percent": 75.0,
    "last_processed_date": "2024-07-15"
  },
  "results": {
    "total_articles_processed": 50000,
    "total_tags_created": 1200
  }
}
```

### Example 3: Manual Resume

```bash
curl -X POST "http://localhost:8000/backtracking/resume" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "company_ids": ["CYBERPE865"],
    "checkpoint_file": "backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01.json"
  }'
```

## Configuration Options

### Checkpoint Settings

In the API request:

```json
{
  "enable_checkpoints": true,     // Enable/disable checkpoints (default: true)
  "chunk_days": 7,                // Days per chunk (default: 7)
  "resume": true,                  // Auto-resume if checkpoint exists (default: true)
  "checkpoint_file": "custom.json" // Custom checkpoint file name (optional)
}
```

### Chunk Size Recommendations

| Date Range | Recommended Chunk Days | Reason |
|------------|----------------------|--------|
| < 1 month  | 1-3 days             | Fine-grained control |
| 1-6 months  | 7 days               | Good balance |
| 6-12 months| 7-14 days            | Fewer checkpoints |
| > 1 year    | 7-14 days            | Manageable chunks |

## How Resume Works

### Automatic Resume Flow

1. **Start backtracking** → System checks for checkpoint file
2. **If checkpoint exists** → Loads progress, skips completed chunks
3. **Process remaining chunks** → Only processes unprocessed date ranges
4. **Save progress** → Updates checkpoint after each chunk
5. **On completion** → Deletes checkpoint file

### Example Timeline

**Initial Run:**
```
2023-01-01 → 2024-07-15 (78 chunks) ✅ Completed
2024-07-16 → 2025-01-01 (26 chunks) ❌ Crashed
```

**Checkpoint Saved:**
- Last processed: 2024-07-15
- Chunks completed: 78/104

**Resume Run:**
```
2023-01-01 → 2024-07-15 (78 chunks) ⏭️ Skipped (already done)
2024-07-16 → 2025-01-01 (26 chunks) ✅ Processed (resumed)
```

## Safety Features

### 1. No Duplicate Processing

- Processed date ranges are tracked in checkpoint
- Same chunks are never processed twice
- Safe to restart multiple times

### 2. Error Handling

- Errors are saved in checkpoint
- Can continue with next chunk after error
- Full error history preserved

### 3. Atomic Operations

- Each chunk processed independently
- Failures don't affect completed chunks
- Checkpoint saved even on errors

## Best Practices

### 1. For Long-Running Jobs

```json
{
  "chunk_days": 7,              // Smaller chunks = more frequent saves
  "enable_checkpoints": true,   // Always enable for long jobs
  "resume": true                // Auto-resume
}
```

### 2. Monitoring Progress

Check checkpoint status periodically:
```bash
# Check progress every hour
watch -n 3600 'curl -s http://localhost:8000/backtracking/status/checkpoint_file.json | jq .progress'
```

### 3. Recovery After Crash

Simply restart with same configuration:
```bash
# Same command - automatically resumes!
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d @original_request.json
```

## Troubleshooting

### Checkpoint Not Found

If checkpoint file doesn't exist:
- Check the checkpoint file name (it's auto-generated)
- Verify file path is correct
- Check if `enable_checkpoints` was set to `false`

### Resume Not Working

1. **Verify checkpoint file exists:**
   ```bash
   ls -la backtracking_checkpoint_*.json
   ```

2. **Check checkpoint content:**
   ```bash
   cat backtracking_checkpoint_*.json | jq .
   ```

3. **Ensure same configuration:**
   - Same date range
   - Same company IDs
   - Same chunk_days setting

### Partial Completion

If backtracking shows as "completed" but you suspect incomplete:
- Check checkpoint file (should be deleted on completion)
- Review results summary
- Check error logs

## Example: 2-Year Backtracking Scenario

**Scenario:** Process 2 years of data (2023-01-01 to 2025-01-01)

**Request:**
```json
{
  "start_date": "2023-01-01",
  "end_date": "2025-01-01",
  "company_ids": ["CYBERPE865"],
  "chunk_days": 7
}
```

**If crash occurs:**
- After 1.5 years → Checkpoint saved at ~78/104 chunks
- Restart same request → Auto-resumes from chunk 79
- Completes remaining 26 chunks
- No duplicate processing!

**Progress Tracking:**
```
Chunk 1-78:   ✅ Already processed (skipped)
Chunk 79-104: ✅ Processed on resume
Total: 104 chunks completed
```

## API Endpoints

### Run Backtracking (with auto-resume)
```
POST /backtracking/run
```

### Check Status
```
GET /backtracking/status/{checkpoint_file}
```

### Manual Resume
```
POST /backtracking/resume
```

## Summary

✅ **Automatic resumability** - No manual intervention needed  
✅ **Crash-safe** - Progress saved automatically  
✅ **No duplicates** - Already-processed chunks are skipped  
✅ **Progress tracking** - Check status anytime  
✅ **Configurable** - Adjust chunk size and save frequency  

**For a 2-year backtracking job, even if it crashes after 1.5 years, simply restart with the same request and it will automatically resume from where it left off!**

