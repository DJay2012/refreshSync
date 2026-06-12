#!/usr/bin/env python3
"""
Direct one-off backtracking runner (no API / Redis queue).

Runs PercolatorBacktrackingEngine in-process for a fixed set of companies,
date range, all languages, print + online. Intended to be invoked inside the
refresh-es-api Docker image where the in-repo tagger and CA cert are present.

Usage (inside container):
    python esBacktracking/run_direct_backtrack.py
"""

import sys
import asyncio
from pathlib import Path

# Make sure esBacktracking is importable
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))
sys.path.insert(0, str(current_dir.parent))

from percolator_backtracking import PercolatorBacktrackingEngine
from backtracking_config import BacktrackingConfig

# ---------------------------------------------------------------------------
# REQUESTED RUN PARAMETERS
# ---------------------------------------------------------------------------
COMPANY_IDS = [
    "WESTBRI941",  # WESTBRIDGE CAPITAL
    "KPN486",      # KPN FRESH
    "SAVO481",     # SAVO MART
    "IBO251",      # IBO
]

START_DATE = "2026-01-01"
END_DATE = None  # None => today (set below)


def main():
    from datetime import datetime
    end_date = END_DATE or datetime.now().strftime("%Y-%m-%d")

    config = BacktrackingConfig(
        start_date=START_DATE,
        end_date=end_date,
        company_ids=COMPANY_IDS,
        language=None,          # None => ALL languages (English base + every lang_* field)
        process_print=True,     # print articles
        process_online=True,    # online / social feeds
        dry_run=False,
        # --- speed knobs (override slow defaults) ---
        tag_workers=32,
        es_page_size=5000,          # max ES page size
        msearch_batch_size=200,     # fewer ES round-trips
        mongo_bulk_batch_size=1000, # bigger Mongo bulk writes
        progress_log_interval=5000,
        enable_checkpoints=True,    # resumable if it dies
        use_mongo_checkpoints=True,
        results_file="direct_backtrack_results.json",
    )

    print("=" * 70)
    print("DIRECT BACKTRACKING RUN")
    print("=" * 70)
    print(f"Companies : {', '.join(COMPANY_IDS)}")
    print(f"Date range: {START_DATE} -> {end_date}")
    print(f"Languages : ALL")
    print(f"Sources   : print + online")
    print("=" * 70)

    engine = PercolatorBacktrackingEngine(config)
    results = asyncio.run(engine.run_percolator_backtracking(resume=True))

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    import json
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
