# MongoDB Checkpoints Guide

## 🎯 Overview

Checkpoints are now stored in MongoDB by default instead of filesystem files. This provides better scalability, centralized storage, and easier querying.

---

## 📊 MongoDB Collections

### 1. `backtrackingJobs` Collection
**Purpose:** Job lifecycle and status tracking

```json
{
  "_id": "job-uuid",
  "status": "running",
  "progress": {...},
  "results": {...}
}
```

### 2. `backtrackingCheckpoints` Collection
**Purpose:** Detailed chunk processing state (for resume)

```json
{
  "_id": "backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01",
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
  ],
  "chunks_processed": 78,
  "total_articles_processed": 50000,
  "total_tags_created": 1200,
  "checkpoint_time": "2024-07-15T10:30:00",
  "updated_at": "2024-07-15T10:30:00"
}
```

---

## 🔄 How It Works

### Checkpoint Storage Flow

```
1. Start Backtracking
   ↓
2. Process Chunk 1
   ↓
3. Save Checkpoint to MongoDB
   → db.backtrackingCheckpoints.replace_one({_id: checkpoint_id}, {...})
   ↓
4. Process Chunk 2
   ↓
5. Save Checkpoint to MongoDB (updated)
   ↓
... (repeat for each chunk)
```

### Checkpoint Loading Flow

```
1. Restart/Crash Occurs
   ↓
2. Load Checkpoint from MongoDB
   → db.backtrackingCheckpoints.find_one({_id: checkpoint_id})
   ↓
3. Restore processed_date_ranges
   ↓
4. Skip already-processed chunks
   ↓
5. Continue from next chunk
```

---

## 📋 Configuration

### Default: MongoDB Checkpoints

```json
{
  "start_date": "2023-01-01",
  "end_date": "2025-01-01",
  "company_ids": ["CYBERPE865"],
  "use_mongo_checkpoints": true  // Default: true
}
```

**Checkpoint ID auto-generated:**
```
backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01
```

### Custom Checkpoint ID

```json
{
  "checkpoint_id": "my_custom_checkpoint_id"
}
```

### Filesystem Checkpoints (Optional)

```json
{
  "use_mongo_checkpoints": false,
  "checkpoint_file": "custom_checkpoint.json"
}
```

---

## 🔍 API Endpoints

### Get MongoDB Checkpoint Status

```bash
GET /backtracking/checkpoint/{checkpoint_id}
```

**Example:**
```bash
curl "http://localhost:8000/backtracking/checkpoint/backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01"
```

**Response:**
```json
{
  "exists": true,
  "checkpoint_id": "backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01",
  "checkpoint_time": "2024-07-15T10:30:00",
  "updated_at": "2024-07-15T10:30:00",
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

### Get Filesystem Checkpoint Status (Legacy)

```bash
GET /backtracking/status/{checkpoint_file}
```

---

## 💡 Benefits of MongoDB Checkpoints

### ✅ Centralized Storage
- All checkpoints in one place
- Easy to query and manage
- No file system management

### ✅ Queryable
```javascript
// Find all checkpoints for a company
db.backtrackingCheckpoints.find({
  "config.company_ids": "CYBERPE865"
})

// Find incomplete checkpoints
db.backtrackingCheckpoints.find({
  "chunks_processed": {"$lt": 100}
})

// Find recent checkpoints
db.backtrackingCheckpoints.find().sort({"updated_at": -1}).limit(10)
```

### ✅ Scalable
- Works across multiple API instances
- No file system locking issues
- Better for distributed systems

### ✅ Persistent
- Survives server restarts
- No file cleanup needed
- Automatic backup with MongoDB

---

## 🔄 Complete Flow with MongoDB Checkpoints

### Step 1: Start Job

```bash
POST /backtracking/run
{
  "start_date": "2023-01-01",
  "end_date": "2025-01-01",
  "company_ids": ["CYBERPE865"]
}
```

**What happens:**
1. Job created in `backtrackingJobs` collection
2. Checkpoint ID generated: `backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01`
3. Background processing starts

### Step 2: Processing Chunks

```python
# After each chunk:
db.backtrackingCheckpoints.replace_one(
    {"_id": "backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01"},
    {
        "processed_date_ranges": ["chunk1", "chunk2", ...],
        "chunks_processed": 25,
        "last_processed_date": "2023-07-01",
        ...
    },
    upsert=True
)
```

### Step 3: On Crash

```python
# Checkpoint saved to MongoDB before crash
# On restart:
checkpoint = db.backtrackingCheckpoints.find_one({
    "_id": "backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01"
})
# Load processed_date_ranges
# Skip already-processed chunks
# Continue from last chunk
```

### Step 4: On Completion

```python
# Checkpoint deleted from MongoDB
db.backtrackingCheckpoints.delete_one({
    "_id": "backtracking_checkpoint_CYBERPE865_2023-01-01_to_2025-01-01"
})
```

---

## 📊 MongoDB Query Examples

### Find All Checkpoints

```javascript
db.backtrackingCheckpoints.find()
```

### Find Checkpoints for Specific Company

```javascript
db.backtrackingCheckpoints.find({
  "config.company_ids": "CYBERPE865"
})
```

### Find Incomplete Checkpoints

```javascript
db.backtrackingCheckpoints.find({
  $expr: {
    $lt: [
      "$chunks_processed",
      { $divide: [
        { $add: [
          { $subtract: [
            { $dateFromString: { dateString: "$config.end_date" }},
            { $dateFromString: { dateString: "$config.start_date" }}
          ]},
          1
        ]},
        "$config.chunk_days"
      ]}
    ]
  }
})
```

### Find Recent Checkpoints

```javascript
db.backtrackingCheckpoints.find()
  .sort({"updated_at": -1})
  .limit(10)
```

### Delete Old Checkpoints

```javascript
// Delete checkpoints older than 30 days
db.backtrackingCheckpoints.deleteMany({
  "updated_at": {
    $lt: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString()
  }
})
```

---

## 🔄 Migration from Filesystem

If you have existing filesystem checkpoints:

1. **Old filesystem checkpoints** still work (if `use_mongo_checkpoints: false`)
2. **New jobs** use MongoDB by default
3. **Can manually convert** filesystem checkpoints to MongoDB:

```python
# Read filesystem checkpoint
with open("checkpoint.json") as f:
    checkpoint_data = json.load(f)

# Save to MongoDB
db.backtrackingCheckpoints.replace_one(
    {"_id": checkpoint_id},
    checkpoint_data,
    upsert=True
)
```

---

## ✅ Summary

**MongoDB Checkpoints provide:**
- ✅ Centralized storage
- ✅ Easy querying
- ✅ Scalability
- ✅ Persistence
- ✅ Better integration with job system

**Collections:**
- `backtrackingJobs` - Job status and progress
- `backtrackingCheckpoints` - Detailed chunk processing state

**Default:** MongoDB checkpoints (`use_mongo_checkpoints: true`)

**Fallback:** Filesystem checkpoints still supported (`use_mongo_checkpoints: false`)













