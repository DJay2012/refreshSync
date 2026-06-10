#!/usr/bin/env python3
"""
Main entry point for esPreview simplified version.
Hardcode company IDs and boolean queries here for quick testing.
"""

import sys
from pathlib import Path

# Add the current directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from . import ESPreviewEngine, ESPreviewConfig

def main():
    """Main entry point with hardcoded queries for testing."""
    print("esPreview Simplified - Main Entry Point")
    print("=" * 50)
    
    try:
        # Load configuration
        config = ESPreviewConfig.from_env()
        engine = ESPreviewEngine(config)
        
        # Health check
        print("1. System Health Check")
        health = engine.health_check()
        print(f"   Status: {health['status']}")
        print(f"   Elasticsearch: {health['elasticsearch']['status']}")
        print()
        
        # ========================================================================
        # HARDCODED COMPANY IDs - Add your company IDs here
        # ========================================================================
        company_ids = [
            "CYBERPE865",  # CyberPeace Foundation
            "HUL",         # Hindustan Unilever
            "APNAINS",     # Apna Insurance
            "TATA",        # Tata Group
            "RELIANCE",    # Reliance Industries
        ]
        
        print("2. Testing Company Queries")
        print("-" * 30)
        
        for company_id in company_ids:
            try:
                print(f"Testing company: {company_id}")
                result = engine.execute_company_query(company_id, language="en")
                
                if result.success:
                    print(f"   [OK] Success: {result.total_matches} matches in {result.execution_time_ms}ms")
                    
                    # Show sample results
                    for index_name, index_result in result.index_results.items():
                        if index_result.article_ids:
                            print(f"   - {index_name}: {len(index_result.article_ids)} articles")
                            # Show first 3 article IDs
                            sample_ids = index_result.article_ids[:3]
                            print(f"     Sample IDs: {', '.join(sample_ids)}")
                else:
                    print(f"   [FAIL] Failed: {', '.join(result.errors)}")
                    
            except Exception as e:
                print(f"   [ERROR] Error: {str(e)}")
            
            print()
        
        # ========================================================================
        # HARDCODED BOOLEAN QUERIES - Add your boolean queries here
        # ========================================================================
        boolean_queries = [
            "technology AND innovation",
            "health AND wellness",
            "startup OR entrepreneur",
            "artificial intelligence OR machine learning",
            "cybersecurity AND privacy",
            "Major Vineet Kumar AND CyberPeace Foundation",
            "Vineet Kumar AND CyberPeace AND Key Initiatives",
            "CyberQuest 2025",
        ]
        
        print("3. Testing Boolean Queries")
        print("-" * 30)
        
        for query in boolean_queries:
            try:
                print(f"Testing query: {query}")
                result = engine.execute_query(query)
                
                if result.success:
                    print(f"   [OK] Success: {result.total_matches} matches in {result.execution_time_ms}ms")
                    
                    # Show sample results
                    for index_name, index_result in result.index_results.items():
                        if index_result.article_ids:
                            print(f"   - {index_name}: {len(index_result.article_ids)} articles")
                            # Show first 3 article IDs
                            sample_ids = index_result.article_ids[:3]
                            print(f"     Sample IDs: {', '.join(sample_ids)}")
                else:
                    print(f"   [FAIL] Failed: {', '.join(result.errors)}")
                    
            except Exception as e:
                print(f"   [ERROR] Error: {str(e)}")
            
            print()
        
        # ========================================================================
        # CUSTOM TESTING SECTION - Add your specific tests here
        # ========================================================================
        print("4. Custom Testing Section")
        print("-" * 30)
        
        # Example: Test specific company with different languages
        test_company = "CYBERPE865"
        languages = ["en", "hi", "bn"]
        
        for lang in languages:
            try:
                print(f"Testing {test_company} in {lang}")
                result = engine.execute_company_query(test_company, language=lang)
                print(f"   {lang}: {result.total_matches} matches")
            except Exception as e:
                print(f"   {lang}: Error - {str(e)}")
        
        print()
        
        # Example: Test complex boolean query
        complex_query = '(("Major Vineet Kumar" OR ("Vineet" AND "Kumar")) AND ("CyberPeace Foundation" OR "CyberPeace" OR ("Cyber" AND "Peace")) AND ("Key Initiatives" OR "CyberQuest 2025" OR ("CyberQuest" AND "2025")))'
        
        try:
            print(f"Testing complex query: {complex_query[:50]}...")
            result = engine.execute_query(complex_query)
            print(f"   Complex query: {result.total_matches} matches in {result.execution_time_ms}ms")
        except Exception as e:
            print(f"   Complex query error: {str(e)}")
        
        print()
        print("=" * 50)
        print("All tests completed!")
        
        return 0
        
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1

def quick_test():
    """Quick test function for rapid testing."""
    print("Quick Test Mode")
    print("-" * 20)
    
    try:
        config = ESPreviewConfig.from_env()
        engine = ESPreviewEngine(config)
        
        # Quick health check
        health = engine.health_check()
        print(f"Health: {health['status']}")
        
        # Quick company test
        result = engine.execute_company_query("CYBERPE865")
        print(f"CYBERPE865: {result.total_matches} matches")
        
        # Quick boolean test
        result = engine.execute_query("technology")
        print(f"Technology: {result.total_matches} matches")
        
        print("Quick test completed!")
        return 0
        
    except Exception as e:
        print(f"Quick test error: {e}")
        return 1

if __name__ == "__main__":
    # You can choose between full test or quick test
    if len(sys.argv) > 1 and sys.argv[1] == "--quick":
        sys.exit(quick_test())
    else:
        sys.exit(main())











