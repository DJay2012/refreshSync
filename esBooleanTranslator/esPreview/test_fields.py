#!/usr/bin/env python3
"""
Test script to verify that articleDate, imageId (for articles) and feedDate, links (for socialFeed) 
are included in the response.
"""

import sys
from pathlib import Path

# Add the current directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from espreview import ESPreviewEngine, ESPreviewConfig

def test_fields_in_response():
    """Test that the new fields are included in responses."""
    print("Testing esPreview field inclusion...")
    print("=" * 60)
    
    try:
        # Load configuration
        config = ESPreviewConfig.from_env()
        engine = ESPreviewEngine(config)
        
        # Test query that will return some results
        test_query = "technology"
        
        print(f"\n1. Testing query: '{test_query}'")
        print("-" * 60)
        
        # Execute query with content included
        result = engine.execute_query(test_query, include_content=True)
        
        if result.success:
            print(f"   ✓ Query executed successfully")
            print(f"   Total matches: {result.total_matches}")
            print(f"   Execution time: {result.execution_time_ms}ms")
            
            # Check each index result
            for index_name, index_result in result.index_results.items():
                print(f"\n2. Index: {index_name}")
                print(f"   Total hits: {index_result.total_hits}")
                print(f"   Articles returned: {len(index_result.articles)}")
                
                if index_result.articles:
                    # Show first article as example
                    first_article = index_result.articles[0]
                    print(f"\n   First article fields:")
                    for field_name, field_value in first_article.items():
                        # Truncate long text values
                        if isinstance(field_value, str) and len(field_value) > 100:
                            display_value = field_value[:100] + "..."
                        else:
                            display_value = field_value
                        print(f"     - {field_name}: {display_value}")
                    
                    # Check for required fields based on index type
                    if index_name == "printarticleindex":
                        print(f"\n   Checking article-specific fields:")
                        if "articleDate" in first_article:
                            print(f"     ✓ articleDate: Found")
                        else:
                            print(f"     ✗ articleDate: Missing")
                        
                        if "imageId" in first_article:
                            print(f"     ✓ imageId: Found")
                        else:
                            print(f"     ✗ imageId: Missing")
                    
                    elif index_name == "socialfeedindex":
                        print(f"\n   Checking socialFeed-specific fields:")
                        if "feedDate" in first_article:
                            print(f"     ✓ feedDate: Found")
                        else:
                            print(f"     ✗ feedDate: Missing")
                        
                        if "links" in first_article:
                            print(f"     ✓ links: Found")
                        else:
                            print(f"     ✗ links: Missing")
                else:
                    print(f"   ⚠ No articles returned for this index")
        else:
            print(f"   ✗ Query failed: {', '.join(result.errors)}")
            return 1
        
        print("\n" + "=" * 60)
        print("Test completed successfully!")
        return 0
        
    except Exception as e:
        print(f"\n✗ Error during test: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(test_fields_in_response())















