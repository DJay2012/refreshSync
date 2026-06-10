#!/usr/bin/env python3
"""
Streaming Backtracking Script - Process data in batches as we retrieve it
This approach is much more memory efficient and faster for large datasets.
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
    """Run streaming backtracking for both companies."""
    
    print("Streaming Backtracking for Large Dataset")
    print("=" * 60)
    
    # Calculate date range (last 5 days)
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    
    print(f"Configuration:")
    print(f"  Date range: {start_date} to {end_date}")
    print(f"  Companies: INDIA124")
    print(f"  Processing: Streaming batches of 1000 documents")
    print(f"  Memory efficient: Process as we retrieve")
    print()
    
    # Create configuration
    config = BacktrackingConfig(
        start_date=start_date,
        end_date=end_date,
        company_ids=["INDIA124"],
        dry_run=False
    )
    
    # Create backtracking engine
    engine = PercolatorBacktrackingEngine(config)
    
    # Run backtracking
    print("Starting Streaming Percolator Backtracking Process")
    print("=" * 50)
    print(f"Date range: {start_date} to {end_date}")
    print(f"Companies: {', '.join(config.company_ids)}")
    print(f"Dry run: {config.dry_run}")
    print()
    
    try:
        results = engine.run_percolator_backtracking()
        
        # Print results
        print("\n" + "=" * 50)
        print("STREAMING BACKTRACKING RESULTS")
        print("=" * 50)
        
        for company_id, company_results in results.items():
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
        results_file = f"streaming_backtracking_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        print(f"\nResults saved to: {results_file}")
        
        # Summary
        total_articles = sum(r.get('articles_processed', 0) for r in results.values())
        total_social_feeds = sum(r.get('social_feeds_processed', 0) for r in results.values())
        total_tags = sum(r.get('tags_created', 0) for r in results.values())
        
        print(f"\nSUMMARY:")
        print(f"  Total articles processed: {total_articles:,}")
        print(f"  Total social feeds processed: {total_social_feeds:,}")
        print(f"  Total tags created: {total_tags:,}")
        print(f"  Processing method: Streaming batches (memory efficient)")
        
    except Exception as e:
        print(f"Error running streaming backtracking: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
