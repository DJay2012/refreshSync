#!/usr/bin/env python3
"""
Check how much data is actually available in the last 10 days
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

from espreview import ESPreviewEngine, ESPreviewConfig

def check_data_availability():
    """Check how much data is available in the last 10 days."""
    
    print("Checking Data Availability (Last 10 Days)")
    print("=" * 50)
    
    # Initialize esPreview engine
    config = ESPreviewConfig.from_env()
    engine = ESPreviewEngine(config)
    
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")
    
    print(f"Date range: {start_date} to {end_date}")
    print()
    
    # Check articles
    print("1. CHECKING ARTICLES")
    print("-" * 30)
    
    date_query = {
        "range": {
            "articleInfo.articleDate": {
                "gte": start_date,
                "lte": end_date,
                "format": "yyyy-MM-dd"
            }
        }
    }
    
    # Count query for articles
    count_query = {
        "query": {
            "bool": {
                "must": [date_query]
            }
        }
    }
    
    try:
        response = engine.es_client.count(index="printarticleindex", body=count_query)
        article_count = response['count']
        print(f"Total articles in last 10 days: {article_count}")
        
        # Get sample of articles
        sample_query = {
            "query": {
                "bool": {
                    "must": [date_query]
                }
            },
            "size": 5,
            "sort": [{"articleInfo.articleDate": {"order": "desc"}}],
            "_source": ["articleId", "articleData.headlines", "articleInfo.articleDate"]
        }
        
        response = engine.es_client.search(index="printarticleindex", body=sample_query)
        hits = response.get('hits', {}).get('hits', [])
        
        print("Sample articles:")
        for hit in hits:
            source = hit['_source']
            article_data = source.get('articleData', {})
            article_info = source.get('articleInfo', {})
            print(f"  - {source.get('articleId')}: {article_data.get('headlines', 'No headline')[:50]}... ({article_info.get('articleDate')})")
        
    except Exception as e:
        print(f"Error checking articles: {e}")
    
    print()
    
    # Check social feeds
    print("2. CHECKING SOCIAL FEEDS")
    print("-" * 30)
    
    social_date_query = {
        "range": {
            "feedData.feedDate": {
                "gte": start_date,
                "lte": end_date,
                "format": "yyyy-MM-dd"
            }
        }
    }
    
    # Count query for social feeds
    social_count_query = {
        "query": {
            "bool": {
                "must": [social_date_query]
            }
        }
    }
    
    try:
        response = engine.es_client.count(index="socialfeedindex", body=social_count_query)
        social_count = response['count']
        print(f"Total social feeds in last 10 days: {social_count}")
        
        # Get sample of social feeds
        sample_query = {
            "query": {
                "bool": {
                    "must": [social_date_query]
                }
            },
            "size": 5,
            "sort": [{"feedData.feedDate": {"order": "desc"}}],
            "_source": ["socialFeedId", "feedData.headlines", "feedData.feedDate"]
        }
        
        response = engine.es_client.search(index="socialfeedindex", body=sample_query)
        hits = response.get('hits', {}).get('hits', [])
        
        print("Sample social feeds:")
        for hit in hits:
            source = hit['_source']
            feed_data = source.get('feedData', {})
            print(f"  - {source.get('socialFeedId')}: {feed_data.get('headlines', 'No headline')[:50]}... ({feed_data.get('feedDate')})")
        
    except Exception as e:
        print(f"Error checking social feeds: {e}")
    
    print()
    
    # Check date distribution
    print("3. DATE DISTRIBUTION")
    print("-" * 30)
    
    # Articles by date
    try:
        agg_query = {
            "query": {
                "bool": {
                    "must": [date_query]
                }
            },
            "size": 0,
            "aggs": {
                "articles_by_date": {
                    "date_histogram": {
                        "field": "articleInfo.articleDate",
                        "calendar_interval": "day",
                        "format": "yyyy-MM-dd"
                    }
                }
            }
        }
        
        response = engine.es_client.search(index="printarticleindex", body=agg_query)
        buckets = response.get('aggregations', {}).get('articles_by_date', {}).get('buckets', [])
        
        print("Articles by date:")
        for bucket in buckets:
            print(f"  {bucket['key_as_string']}: {bucket['doc_count']} articles")
        
    except Exception as e:
        print(f"Error checking date distribution: {e}")
    
    print()
    
    # Social feeds by date
    try:
        agg_query = {
            "query": {
                "bool": {
                    "must": [social_date_query]
                }
            },
            "size": 0,
            "aggs": {
                "social_feeds_by_date": {
                    "date_histogram": {
                        "field": "feedData.feedDate",
                        "calendar_interval": "day",
                        "format": "yyyy-MM-dd"
                    }
                }
            }
        }
        
        response = engine.es_client.search(index="socialfeedindex", body=agg_query)
        buckets = response.get('aggregations', {}).get('social_feeds_by_date', {}).get('buckets', [])
        
        print("Social feeds by date:")
        for bucket in buckets:
            print(f"  {bucket['key_as_string']}: {bucket['doc_count']} social feeds")
        
    except Exception as e:
        print(f"Error checking social feed date distribution: {e}")

if __name__ == "__main__":
    check_data_availability()
















