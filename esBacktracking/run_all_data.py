#!/usr/bin/env python3
"""
Run backtracking for ALL available data in the last 10 days
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file (local directory)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

# Add the current directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from percolator_backtracking import PercolatorBacktrackingEngine
from backtracking_config import BacktrackingConfig

def run_all_data():
    """Run backtracking for ALL available data."""
    
    print("Running Backtracking for ALL Data (Last 10 Days)")
    print("=" * 60)
    
    # Configuration for ALL data
    config = BacktrackingConfig(
        # Date range (last 10 days)
        start_date=(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"),
        end_date=datetime.now().strftime("%Y-%m-%d"),
        
        # Both companies
        company_ids=["INDIA124", "CYBERPE865"],
        
        # Processing settings - LARGE BATCH SIZE
        batch_size=50000,  # Process up to 50k articles/social feeds at once
        max_workers=4,
        parallel_processing=True,
        
        # Output settings
        dry_run=False,  # Actually save to MongoDB
        verbose=True,
        save_results=True,
        results_file="all_data_backtracking_results.json"
    )
    
    print(f"Configuration:")
    print(f"  Date range: {config.start_date} to {config.end_date}")
    print(f"  Companies: {', '.join(config.company_ids)}")
    print(f"  Dry run: {config.dry_run}")
    print(f"  Batch size: {config.batch_size:,} (LARGE BATCH)")
    print(f"  Expected data: ~227k articles + ~490k social feeds")
    print()
    
    # Run percolator backtracking
    engine = PercolatorBacktrackingEngine(config)
    results = engine.run_percolator_backtracking()
    
    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    print(f"Total tags created: {results['total_tags_created']}")
    print(f"Processing time: {results['processing_time_seconds']:.2f} seconds")
    
    if results['company_results']:
        for company_id, company_result in results['company_results'].items():
            print(f"\nCompany {company_id}:")
            print(f"  Articles processed: {company_result['articles_processed']:,}")
            print(f"  Social feeds processed: {company_result['social_feeds_processed']:,}")
            print(f"  Tags created: {company_result['tags_created']:,}")
            if company_result['errors']:
                print(f"  Errors: {len(company_result['errors'])}")
                for error in company_result['errors'][:3]:  # Show first 3 errors
                    print(f"    - {error}")
    
    if results['errors']:
        print(f"\nGlobal errors: {len(results['errors'])}")
        for error in results['errors'][:3]:  # Show first 3 errors
            print(f"  - {error}")
    
    print(f"\nResults saved to: {config.results_file}")
    
    return results

if __name__ == "__main__":
    run_all_data()
















