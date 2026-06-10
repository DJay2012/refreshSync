# Automatic Resume on Crash Feature

## Overview

The backtracking system now includes **automatic crash recovery** - if a backtracking job crashes, it will automatically restart and resume from the last checkpoint without any manual intervention.

## How Automatic Resume Works

### Crash Detection & Recovery Flow

```
Start Backtracking
    ↓
Process Chunks (saving checkpoints)
    ↓
Crash Occurs ❌
    ↓
Save Checkpoint ✅
    ↓
Wait 10 seconds ⏱️
    ↓
Automatic Restart 🔄
    ↓
Load Checkpoint 📂
    ↓
Resume from Last Processed Chunk ▶️
    ↓
Continue Processing...
```

### Automatic Retry Logic

1. **First Attempt**: Normal processing starts
2. **On Crash**: 
   - Checkpoint is automatically saved
   - Error is logged
   - Wait period begins (default: 10 seconds)
3. **Automatic Retry**: 
   - System restarts automatically
   - Loads checkpoint
   - Resumes from last processed chunk
   - Continues processing remaining chunks
4. **Max Retries**: Up to 3 automatic retries (configurable)
5. **Success**: Process completes or exhausts retries

## Configuration

### Default Settings

```json
{
  "auto_resume_on_crash": true,    // Enable automatic resume (default: true)
  "max_auto_retries": 3,           // Maximum retry attempts (default: 3)
  "retry_delay_seconds": 10,       // Wait time between retries (default: 10)
  "enable_checkpoints": true        // Required for auto-resume (default: true)
}
```

### Example Request

```bash
curl -X POST "http://localhost:8000/backtracking/run" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "company_ids": ["CYBERPE865"],
    "auto_resume_on_crash": true,
    "max_auto_retries": 3,
    "retry_delay_seconds": 10
  }'
```

## Real-World Scenario: 2-Year Backtracking

### Timeline Example

**Initial Run:**
```
[00:00] Starting backtracking (2023-01-01 to 2025-01-01)
[01:30] Processing chunk 50/104...
[01:35] CRASH! 💥 (Network timeout)
        → Checkpoint saved automatically ✅
        → Waiting 10 seconds...
[01:45] AUTOMATIC RESTART #1 🔄
        → Loading checkpoint...
        → Resuming from chunk 50...
[03:00] Processing chunk 75/104...
[03:05] CRASH! 💥 (Database connection lost)
        → Checkpoint saved automatically ✅
        → Waiting 10 seconds...
[03:15] AUTOMATIC RESTART #2 🔄
        → Loading checkpoint...
        → Resuming from chunk 75...
[04:30] Processing complete! ✅
```

### What Happens Behind the Scenes

1. **Chunk 1-49**: ✅ Completed and saved
2. **Chunk 50**: ❌ Crash during processing
3. **Checkpoint saved**: Last processed date = end of chunk 49
4. **Wait 10 seconds**: Allows system to stabilize
5. **Automatic restart**: Loads checkpoint, skips chunks 1-49
6. **Chunk 50+**: Continues processing
7. **Chunk 50-74**: ✅ Completed
8. **Chunk 75**: ❌ Another crash
9. **Automatic restart #2**: Resumes from chunk 75
10. **Final completion**: All chunks processed

## Benefits

### ✅ Fully Automatic
- No manual intervention required
- System handles crashes automatically
- Transparent to the user

### ✅ No Lost Progress
- Every chunk is saved before processing
- Checkpoints saved even on crash
- Resume picks up exactly where it left off

### ✅ Resilient
- Handles transient errors (network, DB, etc.)
- Multiple retry attempts
- Graceful degradation after max retries

### ✅ Safe
- No duplicate processing
- Atomic chunk operations
- Complete error logging

## Example Console Output

```
Starting backtracking process...
Date range: 2023-01-01 to 2025-01-01
Checkpoints enabled: True
Auto-resume on crash: True

Processing 104 date chunks (7 days each)

Chunk 1/104: Processing 2023-01-01 to 2023-01-07 ✅
Chunk 2/104: Processing 2023-01-08 to 2023-01-14 ✅
...
Chunk 50/104: Processing 2024-07-08 to 2024-07-14
ERROR: Connection timeout
Saving checkpoint before retry...
*** AUTOMATIC RESTART #1 ***
Error: Connection timeout
Waiting 10 seconds before retry...

=== RETRY ATTEMPT #1 ===
*** RESUMING FROM CHECKPOINT ***
Last processed date: 2024-07-07
Progress: 49 chunks completed

Chunk 1/104: Skipping 2023-01-01 to 2023-01-07 (already processed) ⏭️
...
Chunk 50/104: Processing 2024-07-08 to 2024-07-14 ✅
Chunk 51/104: Processing 2024-07-15 to 2024-07-21 ✅
...
```

## Disabling Automatic Resume

If you want to disable automatic resume (not recommended):

```json
{
  "auto_resume_on_crash": false
}
```

With this setting:
- Checkpoints still saved
- Can manually resume later
- No automatic retries

## Monitoring

### Check Retry Status

The API response includes retry information:

```json
{
  "success": true,
  "results": {
    "total_tags_created": 1200,
    "retry_attempts": 2,
    "final_attempt": true
  }
}
```

### Log Messages

Watch for these log messages:
- `*** AUTOMATIC RESTART #N ***` - System is auto-restarting
- `Saving checkpoint before retry...` - Checkpoint saved before retry
- `*** RESUMING FROM CHECKPOINT ***` - Loading saved progress
- `Max retries exceeded` - All retries exhausted

## Best Practices

### 1. Use Default Settings
```json
{
  "auto_resume_on_crash": true,   // Keep enabled
  "max_auto_retries": 3,          // 3 is usually sufficient
  "retry_delay_seconds": 10       // 10 seconds is good
}
```

### 2. For Very Long Jobs
Increase retry count for critical jobs:
```json
{
  "max_auto_retries": 5,
  "retry_delay_seconds": 30
}
```

### 3. Monitor Progress
Even with auto-resume, check progress periodically:
```bash
curl "http://localhost:8000/backtracking/status/checkpoint_file.json"
```

## Summary

**Automatic Resume = Zero Downtime**

- ✅ Crashes → Automatically restarts
- ✅ Checkpoints → Automatically saved
- ✅ Resume → Automatically loads
- ✅ Retries → Up to 3 automatic attempts
- ✅ Progress → Never lost

**For a 2-year backtracking: Even if it crashes multiple times, the system will automatically restart and resume until completion!**













