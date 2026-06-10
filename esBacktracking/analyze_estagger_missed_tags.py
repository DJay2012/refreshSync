#!/usr/bin/env python3
"""
Analyze why the main esTagger missed tags that backtracking would find
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file (local directory)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

# Add the current directory to the path
sys.path.insert(0, str(Path(__file__).parent))

def analyze_estagger_missed_tags():
    """Analyze why esTagger missed tags."""
    
    print("ANALYZING WHY ESTAGGER MISSED TAGS")
    print("=" * 50)
    
    # Current date
    today = datetime.now()
    print(f"Current date: {today.strftime('%Y-%m-%d')}")
    print()
    
    # 1. Article Tagger Date Filter
    print("1. ARTICLE TAGGER DATE FILTER:")
    print("   Query: DATE(articledate) >= CURRENT_DATE - INTERVAL '1 day'")
    one_day_ago = today - timedelta(days=1)
    print(f"   This means: articledate >= {one_day_ago.strftime('%Y-%m-%d')}")
    print(f"   Only processes articles from: {one_day_ago.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')}")
    print()
    
    # 2. Social Feed Tagger Date Filter
    print("2. SOCIAL FEED TAGGER DATE FILTER:")
    print("   Query: DATE(FEEDDATE) >= '2025-06-21' AND DATE(FEEDDATE) <= '2025-07-05'")
    print("   This is HARDCODED to only process feeds from: 2025-06-21 to 2025-07-05")
    print("   This is a VERY NARROW window!")
    print()
    
    # 3. Our Backtracking Range
    print("3. OUR BACKTRACKING RANGE:")
    backtracking_start = today - timedelta(days=10)
    print(f"   We're processing: {backtracking_start.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')}")
    print()
    
    # 4. Analysis
    print("4. ANALYSIS - WHY TAGS WERE MISSED:")
    print()
    
    print("   ARTICLES:")
    print(f"   - esTagger only processes: {one_day_ago.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')} (1 day)")
    print(f"   - Backtracking processes: {backtracking_start.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')} (10 days)")
    print(f"   - MISSED PERIOD: {backtracking_start.strftime('%Y-%m-%d')} to {(one_day_ago - timedelta(days=1)).strftime('%Y-%m-%d')} (9 days)")
    print()
    
    print("   SOCIAL FEEDS:")
    print("   - esTagger only processes: 2025-06-21 to 2025-07-05 (15 days in June/July)")
    print(f"   - Backtracking processes: {backtracking_start.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')} (October)")
    print("   - MISSED PERIOD: Almost ALL recent data (June to October gap!)")
    print()
    
    # 5. Additional Conditions
    print("5. ADDITIONAL CONDITIONS THAT MIGHT CAUSE MISSED TAGS:")
    print()
    print("   ARTICLES:")
    print("   - elasticTagger = FALSE (not yet processed)")
    print("   - UPDATECIRRUS = 1 (marked for processing)")
    print("   - Only processes 1 day back from current date")
    print()
    print("   SOCIAL FEEDS:")
    print("   - elasticTagger = FALSE (not yet processed)")
    print("   - UPDATECIRRUS = 1 (marked for processing)")
    print("   - HARDCODED date range (2025-06-21 to 2025-07-05)")
    print("   - This date range is OLD and doesn't include recent data!")
    print()
    
    # 6. Conclusion
    print("6. CONCLUSION:")
    print("   The main esTagger missed tags because:")
    print()
    print("   [X] ARTICLES: Only processes 1 day back, missing 9 days of data")
    print("   [X] SOCIAL FEEDS: Hardcoded to June/July 2025, missing ALL recent data")
    print("   [X] BACKTRACKING: Processes 10 days of recent data, catching missed tags")
    print()
    
    # 7. Recommendations
    print("7. RECOMMENDATIONS:")
    print("   To prevent missed tags in the future:")
    print()
    print("   [*] Update Article Tagger:")
    print("      Change: DATE(articledate) >= CURRENT_DATE - INTERVAL '1 day'")
    print("      To:     DATE(articledate) >= CURRENT_DATE - INTERVAL '10 days'")
    print()
    print("   [*] Update Social Feed Tagger:")
    print("      Change: DATE(FEEDDATE) >= '2025-06-21' AND DATE(FEEDDATE) <= '2025-07-05'")
    print("      To:     DATE(FEEDDATE) >= CURRENT_DATE - INTERVAL '10 days'")
    print()
    print("   [*] Or use dynamic date ranges based on business requirements")

if __name__ == "__main__":
    analyze_estagger_missed_tags()
