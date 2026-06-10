#!/usr/bin/env python3
"""
Run backtracking for CYBERPE865 for the last 10 days
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file (local directory)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

# Add the current directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from percolator_backtracking import PercolatorBacktrackingEngine
from backtracking_config import BacktrackingConfig

def main():
    """Run backtracking for CYBERPE865 for the last 10 days."""
    
    print("Running Backtracking for CYBERPE865 (Last 10 Days)")
    print("=" * 60)
    
    # Calculate date range (last 10 days)
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
    
    print(f"Configuration:")
    print(f"  Company: CYBERPE865")
    print(f"  Date range: {start_date} to {end_date}")
    print(f"  Processing: Streaming batches of 1000 documents")
    print(f"  Memory efficient: Process as we retrieve")
    print()
    
    # Create configuration
    config = BacktrackingConfig(
        start_date=start_date,
        end_date=end_date,
        company_ids=["CYBERPE865"],  # Only CYBERPE865
        dry_run=False
    )
    
    # Create backtracking engine
    engine = PercolatorBacktrackingEngine(config)
    
    # Run backtracking
    print("Starting Percolator Backtracking Process")
    print("=" * 50)
    print(f"Date range: {start_date} to {end_date}")
    print(f"Company: CYBERPE865")
    print(f"Dry run: {config.dry_run}")
    print()
    
    try:
        results = engine.run_percolator_backtracking()
        
        # Print results
        print("\n" + "=" * 50)
        print("CYBERPE865 BACKTRACKING RESULTS")
        print("=" * 50)
        
        for company_id, company_results in results.items():
            if company_id == "start_time":
                continue
            print(f"\nCompany: {company_id}")
            print(f"  Articles processed: {company_results.get('articles_processed', 0):,}")
            print(f"  Social feeds processed: {company_results.get('social_feeds_processed', 0):,}")
            print(f"  Total tags created: {company_results.get('tags_created', 0):,}")
            
            if company_results.get('errors'):
                print(f"  Errors: {len(company_results['errors'])}")
                for error in company_results['errors'][:5]:  # Show first 5 errors
                    print(f"    - {error}")
                if len(company_results['errors']) > 5:
                    print(f"    - ... and {len(company_results['errors']) - 5} more errors")
        
        # Save results to file
        results_file = f"cyberpe865_backtracking_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        print(f"\nResults saved to: {results_file}")
        
        # Summary
        total_articles = sum(r.get('articles_processed', 0) for r in results.values() if isinstance(r, dict))
        total_social_feeds = sum(r.get('social_feeds_processed', 0) for r in results.values() if isinstance(r, dict))
        total_tags = sum(r.get('tags_created', 0) for r in results.values() if isinstance(r, dict))
        
        print(f"\nSUMMARY:")
        print(f"  Total articles processed: {total_articles:,}")
        print(f"  Total social feeds processed: {total_social_feeds:,}")
        print(f"  Total tags created: {total_tags:,}")
        print(f"  Processing method: Streaming batches (memory efficient)")
        
    except Exception as e:
        print(f"Error running backtracking: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
