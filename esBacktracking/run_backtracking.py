#!/usr/bin/env python3
"""
Main entry point for backtracking system.
Run this script to perform historical data backtracking and MongoDB tag creation.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add the current directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from backtracking_engine import BacktrackingEngine
from backtracking_config import (
    BacktrackingConfig, 
    get_cyberpeace_config, 
    get_test_config, 
    get_full_backtracking_config,
    create_custom_config
)

def run_cyberpeace_backtracking():
    """Run backtracking specifically for CyberPeace Foundation."""
    print("Running CyberPeace Foundation Backtracking")
    print("=" * 50)
    
    config = get_cyberpeace_config()
    engine = BacktrackingEngine(config)
    results = engine.run_backtracking()
    
    return results

def run_test_backtracking():
    """Run test backtracking (dry run)."""
    print("Running Test Backtracking (Dry Run)")
    print("=" * 50)
    
    config = get_test_config()
    engine = BacktrackingEngine(config)
    results = engine.run_backtracking()
    
    return results

def run_full_backtracking():
    """Run full backtracking for all companies."""
    print("Running Full Backtracking")
    print("=" * 50)
    
    config = get_full_backtracking_config()
    engine = BacktrackingEngine(config)
    results = engine.run_backtracking()
    
    return results

def run_custom_backtracking():
    """Run custom backtracking with your specific settings."""
    print("Running Custom Backtracking")
    print("=" * 50)
    
    # Customize these settings for your needs
    config = create_custom_config(
        start_date="2025-01-01",  # Your start date
        end_date="2025-01-10",    # Your end date
        company_ids=["CYBERPE865", "HUL", "TATA"],  # Your company IDs
        days_back=10,
        dry_run=False,  # Set to True for testing
        batch_size=100
    )
    
    engine = BacktrackingEngine(config)
    results = engine.run_backtracking()
    
    return results

def main():
    """Main function - choose your backtracking mode."""
    
    print("esPreview Backtracking System")
    print("=" * 50)
    print("Choose your backtracking mode:")
    print("1. CyberPeace Foundation only")
    print("2. Test mode (dry run)")
    print("3. Full backtracking (all companies)")
    print("4. Custom backtracking")
    print("5. Exit")
    
    try:
        choice = input("\nEnter your choice (1-5): ").strip()
        
        if choice == "1":
            results = run_cyberpeace_backtracking()
        elif choice == "2":
            results = run_test_backtracking()
        elif choice == "3":
            results = run_full_backtracking()
        elif choice == "4":
            results = run_custom_backtracking()
        elif choice == "5":
            print("Exiting...")
            return
        else:
            print("Invalid choice. Please run again.")
            return
        
        # Display results
        print("\n" + "=" * 50)
        print("BACKTRACKING RESULTS")
        print("=" * 50)
        print(f"Total articles processed: {results.get('total_articles_processed', 0)}")
        print(f"Total social feeds processed: {results.get('total_social_feeds_processed', 0)}")
        print(f"Total tags created: {results.get('total_tags_created', 0)}")
        print(f"Processing time: {results.get('processing_time_seconds', 0):.2f} seconds")
        
        if results.get('errors'):
            print(f"Errors: {len(results['errors'])}")
            for error in results['errors'][:5]:  # Show first 5 errors
                print(f"  - {error}")
        
        print(f"\nResults saved to: {results.get('results_file', 'backtracking_results.json')}")
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
















