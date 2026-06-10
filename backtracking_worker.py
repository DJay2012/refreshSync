#!/usr/bin/env python3
"""
Standalone Backtracking Worker Service

This script runs the backtracking worker independently from the API.
It can be run in the same Docker container but as a separate process.

Usage:
    python backtracking_worker.py
    
Environment Variables:
    BACKTRACKING_POLL_INTERVAL: Poll interval in seconds (default: 5)
    PG_MONGO_URI: MongoDB connection URI
    PG_MONGO_DB: MongoDB database name
"""

import os
import sys
import time
import signal
import traceback
from pathlib import Path

# Add app directory to path
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

# Import esBacktracking modules early to ensure they're available
try:
    # Add esBacktracking to path
    es_backtracking_dir = current_dir / "esBacktracking"
    if es_backtracking_dir.exists():
        sys.path.insert(0, str(es_backtracking_dir))
except Exception as e:
    pass  # Will be handled by BACKTRACKING_AVAILABLE check

from app.utils.backtracking_logger import setup_backtracking_logger
from app.pnq.pnq_monitoring import start_heartbeat_if_configured
from app.main import (
    BACKTRACKING_AVAILABLE,
    JobStatus,
    update_backtracking_job_status,
    process_backtracking_job,
    get_next_pending_backtracking_job,
    get_job_queue_backend_label
)

# Setup dedicated backtracking logger
backtracking_logger = setup_backtracking_logger()

# Global flag for graceful shutdown
worker_running = True
_pnq_heartbeat = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global worker_running, _pnq_heartbeat
    backtracking_logger.info("🛑 Received shutdown signal, stopping worker...")
    worker_running = False
    if _pnq_heartbeat is not None:
        try:
            _pnq_heartbeat.stop()
        except Exception:
            pass


def run_backtracking_worker():
    """
    Standalone backtracking worker - runs continuously and processes pending jobs from the queue.
    
    This worker:
    - Uses Redis for FIFO ordering when configured (MongoDB fallback)
    - Polls every 5 seconds (configurable) for PENDING jobs
    - Processes jobs one at a time in FIFO order (oldest first)
    - Updates job status: PENDING → RUNNING → COMPLETED/FAILED
    - Continues running even if individual jobs fail
    - Runs until shutdown signal received
    """
    global worker_running, _pnq_heartbeat

    os.environ["PNQ_SERVICE_TAG"] = os.getenv("PNQ_BACKTRACKING_SERVICE_TAG", "backtracking-worker")
    _pnq_heartbeat = start_heartbeat_if_configured()
    
    if not BACKTRACKING_AVAILABLE:
        backtracking_logger.error("❌ Backtracking modules not available. Worker will stay idle to keep container running.")
        backtracking_logger.error("Please ensure esBacktracking/elasticTagging modules are installed and available.")
        # Keep process alive without doing work so container stays up and API keeps running
        while True:
            time.sleep(300)
    
    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    backtracking_logger.info("=" * 60)
    backtracking_logger.info("🔄 BACKTRACKING WORKER STARTING")
    backtracking_logger.info("=" * 60)
    backtracking_logger.info(f"Polling {get_job_queue_backend_label()} for pending backtracking jobs...")
    
    # Poll interval in seconds (how often to check for new jobs)
    poll_interval = float(os.getenv("BACKTRACKING_POLL_INTERVAL", "5"))
    backtracking_logger.info(f"   Poll interval: {poll_interval} seconds")
    backtracking_logger.info(f"   Log file: logs/backtracking.log")
    backtracking_logger.info("=" * 60)
    
    worker_running = True
    
    while worker_running:
        try:
            # Check for pending jobs (oldest first - Redis FIFO when available)
            pending_job = get_next_pending_backtracking_job()
            
            if pending_job:
                job_id = pending_job["job_id"]
                config_dict = pending_job.get("config", {})
                
                backtracking_logger.info(f"Found pending backtracking job {job_id}, starting processing...")
                backtracking_logger.info(f"   Date range: {config_dict.get('start_date')} to {config_dict.get('end_date')}")
                backtracking_logger.info(f"   Companies: {', '.join(config_dict.get('company_ids', []))}")
                
                # Process the job
                try:
                    update_backtracking_job_status(job_id, JobStatus.RUNNING)
                    process_backtracking_job(job_id, config_dict)
                    backtracking_logger.info(f"Completed processing job {job_id}")
                except Exception as e:
                    error_msg = str(e)
                    backtracking_logger.error(f"Error processing backtracking job {job_id}: {error_msg}")
                    backtracking_logger.error(traceback.format_exc())
                    update_backtracking_job_status(job_id, JobStatus.FAILED, error=error_msg)
            else:
                # No pending jobs, sleep before next poll
                time.sleep(poll_interval)
                
        except Exception as e:
            backtracking_logger.error(f"Error in backtracking worker polling loop: {e}")
            backtracking_logger.error(traceback.format_exc())
            time.sleep(poll_interval)  # Sleep on error to avoid tight loop
    
    if _pnq_heartbeat is not None:
        try:
            _pnq_heartbeat.stop()
        except Exception:
            pass

    backtracking_logger.info("=" * 60)
    backtracking_logger.info("BACKTRACKING WORKER STOPPED")
    backtracking_logger.info("=" * 60)


if __name__ == "__main__":
    try:
        run_backtracking_worker()
    except KeyboardInterrupt:
        backtracking_logger.info("Worker interrupted by user")
    except Exception as e:
        backtracking_logger.error(f"Fatal error in backtracking worker: {e}")
        backtracking_logger.error(traceback.format_exc())
        sys.exit(1)

