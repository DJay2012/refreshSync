#!/usr/bin/env python3
"""
Process backtracking requests from a MongoDB collection.

Reads documents like:
{
  _id: ObjectId(...),
  backTrackOnline: false,
  backTrackPrint: true,
  startDate: ISODate(...),
  endDate: ISODate(...),
  requestedAt: ISODate(...),
  requestedBy: "editor@betapub.in",
  companyIds: [{ companyId: "CMP002", companyName: "..." }, ...],
  status: "submitted"
}

Updates status to "processing" → runs backtracking → updates to "completed" with results or "failed" on error.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from pymongo import MongoClient, ReturnDocument
from bson.objectid import ObjectId

# Ensure local imports resolve
sys.path.insert(0, str(Path(__file__).parent))

from backtracking_engine import BacktrackingConfig, BacktrackingEngine


def get_mongo_client() -> MongoClient:
    mongo_uri = os.getenv("PG_MONGO_URI", "mongodb://localhost:27017/")
    return MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)


def get_collection(client: MongoClient):
    db_name = os.getenv("PG_MONGO_DB", "pnq")
    collection_name = os.getenv("BACKTRACK_REQUESTS_COLLECTION", "backtrackRequests")
    return client[db_name][collection_name]


def pick_next_request(col) -> Dict[str, Any] | None:
    # Atomically pick one submitted request and mark it as processing
    now = datetime.utcnow()
    request = col.find_one_and_update(
        {"status": {"$in": ["submitted", "queued"]}},
        {"$set": {"status": "processing", "processingStartedAt": now}},
        return_document=ReturnDocument.AFTER,
        sort=[("requestedAt", 1)],
    )
    return request


def parse_company_ids(doc: Dict[str, Any]) -> List[str]:
    ids = []
    for entry in doc.get("companyIds", []) or []:
        cid = entry.get("companyId") if isinstance(entry, dict) else entry
        if cid:
            ids.append(str(cid))
    return ids


def to_date_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def run_single_request(doc: Dict[str, Any]) -> Dict[str, Any]:
    start_dt = doc.get("startDate")
    end_dt = doc.get("endDate")
    start_str = to_date_str(start_dt) if isinstance(start_dt, datetime) else str(start_dt)[:10]
    end_str = to_date_str(end_dt) if isinstance(end_dt, datetime) else str(end_dt)[:10]

    company_ids = parse_company_ids(doc)

    process_print = bool(doc.get("backTrackPrint", True))
    process_online = bool(doc.get("backTrackOnline", True))

    config = BacktrackingConfig(
        start_date=start_str,
        end_date=end_str,
        company_ids=company_ids,
        batch_size=int(os.getenv("BACKTRACK_BATCH_SIZE", "100")),
        parallel_processing=True,
        process_print=process_print,
        process_online=process_online,
        dry_run=bool(int(os.getenv("BACKTRACK_DRY_RUN", "0"))),
        save_results=False,
    )

    engine = BacktrackingEngine(config)
    results = engine.run_backtracking()
    return results


def main_once():
    client = get_mongo_client()
    col = get_collection(client)

    doc = pick_next_request(col)
    if not doc:
        print("No pending backtracking requests.")
        return

    request_id = doc.get("_id")
    try:
        results = run_single_request(doc)
        completed = col.find_one_and_update(
            {"_id": ObjectId(request_id)},
            {"$set": {
                "status": "completed",
                "completedAt": datetime.utcnow(),
                "resultSummary": {
                    "totalArticlesProcessed": results.get("total_articles_processed", 0),
                    "totalSocialFeedsProcessed": results.get("total_social_feeds_processed", 0),
                    "totalTagsCreated": results.get("total_tags_created", 0),
                    "processingTimeSeconds": results.get("processing_time_seconds", 0),
                }
            }},
            return_document=ReturnDocument.AFTER,
        )
        print(f"Backtracking request {request_id} completed.")
    except Exception as e:
        col.find_one_and_update(
            {"_id": ObjectId(request_id)},
            {"$set": {
                "status": "failed",
                "failedAt": datetime.utcnow(),
                "error": str(e),
            }},
        )
        print(f"Backtracking request {request_id} failed: {e}")


def main_loop():
    # Single-run by default; enable loop with env BACKTRACK_LOOP=1
    loop = os.getenv("BACKTRACK_LOOP", "0") == "1"
    if not loop:
        main_once()
        return

    import time
    interval = int(os.getenv("BACKTRACK_POLL_INTERVAL_SECONDS", "30"))
    client = get_mongo_client()
    col = get_collection(client)
    while True:
        # Drain the queue: keep picking until none left
        picked_any = False
        while True:
            doc = pick_next_request(col)
            if not doc:
                break
            picked_any = True
            try:
                results = run_single_request(doc)
                request_id = doc.get("_id")
                col.find_one_and_update(
                    {"_id": ObjectId(request_id)},
                    {"$set": {
                        "status": "completed",
                        "completedAt": datetime.utcnow(),
                        "resultSummary": {
                            "totalArticlesProcessed": results.get("total_articles_processed", 0),
                            "totalSocialFeedsProcessed": results.get("total_social_feeds_processed", 0),
                            "totalTagsCreated": results.get("total_tags_created", 0),
                            "processingTimeSeconds": results.get("processing_time_seconds", 0),
                        }
                    }},
                    return_document=ReturnDocument.AFTER,
                )
            except Exception as e:
                col.find_one_and_update(
                    {"_id": ObjectId(doc.get("_id"))},
                    {"$set": {
                        "status": "failed",
                        "failedAt": datetime.utcnow(),
                        "error": str(e),
                    }},
                )
        # If nothing was picked, sleep; otherwise loop immediately to try more
        if not picked_any:
            time.sleep(interval)


if __name__ == "__main__":
    main_loop()


