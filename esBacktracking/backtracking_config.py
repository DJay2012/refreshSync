#!/usr/bin/env python3
"""
Configuration file for backtracking system.
Customize this file for your specific backtracking needs.
"""

import os
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file (local directory)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

@dataclass
class BacktrackingConfig:
    """Configuration for backtracking operations."""
    
    # ========================================================================
    # DATE RANGE SETTINGS - Customize these dates
    # ========================================================================
    start_date: str = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    end_date: str = datetime.now().strftime("%Y-%m-%d")
    days_back: int = 10
    
    # ========================================================================
    # COMPANY IDs - Add your company IDs here
    # ========================================================================
    company_ids: List[str] = None
    
    # ========================================================================
    # PROCESSING SETTINGS
    # ========================================================================
    batch_size: int = 100
    max_workers: int = 4
    parallel_processing: bool = True
    language: Optional[str] = "en"  # Can be "en", "hi", "all", None, or any language code. Use None or "all" to process all languages.
    process_print: bool = True  # Process print articles
    process_online: bool = True  # Process online/social feeds
    tag_workers: int = 32  # Thread pool workers for tag_article
    mongo_bulk_batch_size: int = 500  # Buffered Mongo write batch size
    progress_log_interval: int = 10000  # Log progress every N docs
    es_page_size: int = 1000  # Documents fetched per ES page (capped at 5000)
    es_keepalive_minutes: int = 30  # PIT keepalive duration in minutes
    msearch_batch_size: int = 100  # Articles per msearch request (100 = 1 ES call per 100 docs)
    
    # ========================================================================
    # MONGODB SETTINGS - Using main .env configuration
    # ========================================================================
    mongo_uri: str = None
    mongo_db: str = None
    
    # ========================================================================
    # POSTGRESQL SETTINGS - Using main .env configuration
    # ========================================================================
    pg_host: str = None
    pg_port: int = None
    pg_db: str = None
    pg_user: str = None
    pg_password: str = None
    
    # ========================================================================
    # OUTPUT SETTINGS
    # ========================================================================
    dry_run: bool = False  # Set to True for testing without saving to MongoDB
    verbose: bool = True
    save_results: bool = True
    results_file: str = "backtracking_results.json"
    
    # ========================================================================
    # RESUMABILITY SETTINGS
    # ========================================================================
    enable_checkpoints: bool = True  # Enable checkpoint saving for resumability
    checkpoint_file: str = None  # Auto-generated if None (for filesystem)
    use_mongo_checkpoints: bool = True  # Store checkpoints in MongoDB instead of files
    checkpoint_id: str = None  # Auto-generated if None (for MongoDB)
    chunk_days: int = 7  # Process dates in chunks (7 days = weekly chunks)
    save_checkpoint_interval: int = 1  # Save checkpoint after N chunks
    auto_resume_on_crash: bool = True  # Automatically restart and resume on crash
    max_auto_retries: int = 3  # Maximum automatic retry attempts
    retry_delay_seconds: int = 10  # Delay between retry attempts
    
    def __post_init__(self):
        # Set default company IDs
        if self.company_ids is None:
            self.company_ids = [
                "CYBERPE865",  # CyberPeace Foundation
                "HUL",         # Hindustan Unilever
                "TATA",        # Tata Group
                "RELIANCE",    # Reliance Industries
                "INFOSYS",     # Infosys
                "WIPRO",       # Wipro
                # Add more company IDs here...
            ]
        
        # Set database configuration from environment variables
        if self.mongo_uri is None:
            self.mongo_uri = os.getenv("PG_MONGO_URI", "mongodb://localhost:27017/")
        if self.mongo_db is None:
            self.mongo_db = os.getenv("PG_MONGO_DB", "pnq")
        
        if self.pg_host is None:
            self.pg_host = os.getenv("PG_HOST", "localhost")
        if self.pg_port is None:
            self.pg_port = int(os.getenv("PG_PORT", "5432"))
        if self.pg_db is None:
            self.pg_db = os.getenv("PG_DATABASE", "prod_admin")
        if self.pg_user is None:
            self.pg_user = os.getenv("PG_USER", "prod_cirrus")
        if self.pg_password is None:
            self.pg_password = os.getenv("PG_PASSWORD", "")
        
        # Generate checkpoint identifier if not provided
        if self.enable_checkpoints:
            checkpoint_suffix = f"{self.start_date}_to_{self.end_date}"
            company_suffix = "_".join(self.company_ids[:3])[:20]  # First 3 companies, max 20 chars
            checkpoint_key = f"backtracking_checkpoint_{company_suffix}_{checkpoint_suffix}"
            
            if self.use_mongo_checkpoints:
                # Use MongoDB - generate checkpoint_id
                if self.checkpoint_id is None:
                    self.checkpoint_id = checkpoint_key
            else:
                # Use filesystem - generate checkpoint_file
                if self.checkpoint_file is None:
                    self.checkpoint_file = f"{checkpoint_key}.json"

# ========================================================================
# PRESET CONFIGURATIONS
# ========================================================================

def get_cyberpeace_config() -> BacktrackingConfig:
    """Configuration specifically for CyberPeace Foundation backtracking."""
    return BacktrackingConfig(
        start_date=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
        end_date=datetime.now().strftime("%Y-%m-%d"),
        company_ids=["CYBERPE865"],
        batch_size=50,
        dry_run=False,
        results_file="cyberpeace_backtracking_results.json"
    )

def get_test_config() -> BacktrackingConfig:
    """Configuration for testing (dry run)."""
    return BacktrackingConfig(
        start_date=(datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
        end_date=datetime.now().strftime("%Y-%m-%d"),
        company_ids=["CYBERPE865", "HUL"],
        batch_size=10,
        dry_run=True,
        results_file="test_backtracking_results.json"
    )

def get_full_backtracking_config() -> BacktrackingConfig:
    """Configuration for full backtracking of all companies."""
    return BacktrackingConfig(
        start_date=(datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d"),
        end_date=datetime.now().strftime("%Y-%m-%d"),
        company_ids=[
            "CYBERPE865", "HUL", "TATA", "RELIANCE", "INFOSYS", "WIPRO",
            "APNAINS", "FORD", "AIRINDIA", "ESCORTS", "SIGNIFY", "DISHTV"
        ],
        batch_size=200,
        max_workers=8,
        parallel_processing=True,
        dry_run=False,
        results_file="full_backtracking_results.json"
    )

# ========================================================================
# CUSTOM CONFIGURATION BUILDER
# ========================================================================

def create_custom_config(
    start_date: str = None,
    end_date: str = None,
    company_ids: List[str] = None,
    days_back: int = 10,
    dry_run: bool = False,
    batch_size: int = 100
) -> BacktrackingConfig:
    """Create a custom configuration."""
    
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    
    if company_ids is None:
        company_ids = ["CYBERPE865"]
    
    return BacktrackingConfig(
        start_date=start_date,
        end_date=end_date,
        company_ids=company_ids,
        batch_size=batch_size,
        dry_run=dry_run
    )
