#!/usr/bin/env python3
"""
Efficient Backtracking Engine for esPreview Simplified Version

This module provides a more efficient approach to backtracking by:
1. Using company queries to find matching articles directly from Elasticsearch
2. Filtering results by date range
3. Creating MongoDB tags only for articles that actually match

This is much faster than processing all articles and checking each one.
"""

import sys
import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from .env file (local directory)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

# Add the current directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from espreview import ESPreviewEngine, ESPreviewConfig
from backtracking_engine import BacktrackingConfig, MongoTagCreator

# ============================================================================
# EFFICIENT BACKTRACKING ENGINE
# ============================================================================

class EfficientBacktrackingEngine:
    """Efficient backtracking engine that uses company queries to find matches directly."""
    
    def __init__(self, config: BacktrackingConfig):
        self.config = config
        
        # Initialize components
        self.espreview_config = ESPreviewConfig.from_env()
        self.espreview_engine = ESPreviewEngine(self.espreview_config)
        self.mongo_creator = MongoTagCreator(config)
        
        # Results tracking
        self.results = {
            "start_time": datetime.now(),
            "end_time": None,
            "total_articles_processed": 0,
            "total_social_feeds_processed": 0,
            "total_tags_created": 0,
            "company_results": {},
            "errors": []
        }
    
    def run_efficient_backtracking(self) -> Dict[str, Any]:
        """Run efficient backtracking using company queries."""
        
        print("Starting Efficient Backtracking Process")
        print("=" * 50)
        print(f"Date range: {self.config.start_date} to {self.config.end_date}")
        print(f"Companies: {', '.join(self.config.company_ids)}")
        print(f"Dry run: {self.config.dry_run}")
        
        start_time = time.time()
        
        try:
            total_tags_created = 0
            
            # Process each company
            for company_id in self.config.company_ids:
                print(f"\nProcessing company: {company_id}")
                print("-" * 30)
                
                company_results = self._process_company_efficiently(company_id)
                total_tags_created += company_results.get("tags_created", 0)
                
                self.results["company_results"][company_id] = company_results
            
            # Update results
            self.results["end_time"] = datetime.now()
            self.results["total_tags_created"] = total_tags_created
            self.results["processing_time_seconds"] = time.time() - start_time
            
            # Save results if configured
            if self.config.save_results:
                self._save_results()
            
            print(f"\nEfficient backtracking completed!")
            print(f"Total tags created: {self.results['total_tags_created']}")
            print(f"Processing time: {self.results['processing_time_seconds']:.2f} seconds")
            
            return self.results
            
        except Exception as e:
            print(f"Efficient backtracking failed: {e}")
            self.results["errors"].append(str(e))
            return self.results
    
    def _process_company_efficiently(self, company_id: str) -> Dict[str, Any]:
        """Process a single company efficiently using its stored query."""
        
        results = {
            "company_id": company_id,
            "articles_processed": 0,
            "social_feeds_processed": 0,
            "tags_created": 0,
            "errors": []
        }
        
        try:
            # Execute company query to get all matching articles
            print(f"  Executing company query for {company_id}...")
            query_result = self.espreview_engine.execute_company_query(
                company_id, 
                language=self.config.language
            )
            
            if not query_result.success:
                print(f"  Error Company query failed: {', '.join(query_result.errors)}")
                results["errors"].extend(query_result.errors)
                return results
            
            print(f"  Success Company query successful: {query_result.total_matches} total matches")
            
            # Process articles from printarticleindex
            if "printarticleindex" in query_result.index_results:
                article_results = self._process_company_articles(
                    company_id, query_result.index_results["printarticleindex"]
                )
                results["articles_processed"] = article_results["processed"]
                results["tags_created"] += article_results["tags_created"]
                results["errors"].extend(article_results["errors"])
            
            # Process social feeds from socialfeedindex
            if "socialfeedindex" in query_result.index_results:
                social_feed_results = self._process_company_social_feeds(
                    company_id, query_result.index_results["socialfeedindex"]
                )
                results["social_feeds_processed"] = social_feed_results["processed"]
                results["tags_created"] += social_feed_results["tags_created"]
                results["errors"].extend(social_feed_results["errors"])
            
            print(f"  Success Company {company_id}: {results['tags_created']} tags created")
            
        except Exception as e:
            error_msg = f"Error processing company {company_id}: {str(e)}"
            print(f"  Error {error_msg}")
            results["errors"].append(error_msg)
        
        return results
    
    def _process_company_articles(self, company_id: str, index_result) -> Dict[str, Any]:
        """Process articles for a company from the query results."""
        
        results = {
            "processed": 0,
            "tags_created": 0,
            "errors": []
        }
        
        if not index_result.article_ids:
            return results
        
        print(f"    Processing {len(index_result.article_ids)} articles from printarticleindex...")
        
        # Get article details from Elasticsearch
        articles = self._get_articles_by_ids(index_result.article_ids)
        
        # Filter articles by date range
        filtered_articles = self._filter_articles_by_date(articles)
        
        print(f"    Found {len(filtered_articles)} articles in date range")
        
        # Create tags for filtered articles
        for article in filtered_articles:
            try:
                tag_created = self._create_article_tag_efficiently(article, company_id, index_result)
                if tag_created:
                    results["tags_created"] += 1
                results["processed"] += 1
            except Exception as e:
                results["errors"].append(f"Article {article.get('articleid', 'unknown')}: {str(e)}")
        
        return results
    
    def _process_company_social_feeds(self, company_id: str, index_result) -> Dict[str, Any]:
        """Process social feeds for a company from the query results."""
        
        results = {
            "processed": 0,
            "tags_created": 0,
            "errors": []
        }
        
        if not index_result.article_ids:
            return results
        
        print(f"    Processing {len(index_result.article_ids)} social feeds from socialfeedindex...")
        
        # Get social feed details from Elasticsearch
        social_feeds = self._get_social_feeds_by_ids(index_result.article_ids)
        
        # Filter social feeds by date range
        filtered_social_feeds = self._filter_social_feeds_by_date(social_feeds)
        
        print(f"    Found {len(filtered_social_feeds)} social feeds in date range")
        
        # Create tags for filtered social feeds
        for social_feed in filtered_social_feeds:
            try:
                tag_created = self._create_social_feed_tag_efficiently(social_feed, company_id, index_result)
                if tag_created:
                    results["tags_created"] += 1
                results["processed"] += 1
            except Exception as e:
                results["errors"].append(f"Social feed {social_feed.get('SOCIALFEEDID', 'unknown')}: {str(e)}")
        
        return results
    
    def _get_articles_by_ids(self, article_ids: List[str]) -> List[Dict[str, Any]]:
        """Get article details from Elasticsearch by IDs."""
        
        if not article_ids:
            return []
        
        try:
            search_request = {
                "query": {
                    "terms": {
                        "_id": article_ids
                    }
                },
                "size": len(article_ids),
                "_source": [
                    "_id",
                    "articleData.articleId",
                    "articleData.headlines",
                    "articleData.summary",
                    "articleData.text",
                    "articleData.articleDate",
                    "articleData.articleLang"
                ]
            }
            
            response = self.espreview_engine.es_client.search(index="printarticleindex", body=search_request)
            
            articles = []
            for hit in response.get('hits', {}).get('hits', []):
                source = hit['_source']
                article_data = source.get('articleData', {})
                
                article = {
                    'es_id': hit['_id'],
                    'articleid': article_data.get('articleId'),
                    'headlines': article_data.get('headlines', ''),
                    'summary': article_data.get('summary', ''),
                    'content': article_data.get('text', ''),
                    'articlelang': article_data.get('articleLang', 'en'),
                    'articledate': article_data.get('articleDate')
                }
                articles.append(article)
            
            return articles
            
        except Exception as e:
            print(f"Error retrieving articles by IDs: {e}")
            return []
    
    def _get_social_feeds_by_ids(self, social_feed_ids: List[str]) -> List[Dict[str, Any]]:
        """Get social feed details from Elasticsearch by IDs."""
        
        if not social_feed_ids:
            return []
        
        try:
            search_request = {
                "query": {
                    "terms": {
                        "_id": social_feed_ids
                    }
                },
                "size": len(social_feed_ids),
                "_source": [
                    "_id",
                    "feedData.socialFeedId",
                    "feedData.headlines",
                    "feedData.summary",
                    "feedData.text",
                    "feedData.feedDate",
                    "feedData.language"
                ]
            }
            
            response = self.espreview_engine.es_client.search(index="socialfeedindex", body=search_request)
            
            social_feeds = []
            for hit in response.get('hits', {}).get('hits', []):
                source = hit['_source']
                feed_data = source.get('feedData', {})
                
                social_feed = {
                    'es_id': hit['_id'],
                    'SOCIALFEEDID': feed_data.get('socialFeedId'),
                    'HEADLINE': feed_data.get('headlines', ''),
                    'SUMMARY': feed_data.get('summary', ''),
                    'CONTENT': feed_data.get('text', ''),
                    'LANGUAGE': feed_data.get('language', 'en'),
                    'FEEDDATE': feed_data.get('feedDate')
                }
                social_feeds.append(social_feed)
            
            return social_feeds
            
        except Exception as e:
            print(f"Error retrieving social feeds by IDs: {e}")
            return []
    
    def _filter_articles_by_date(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter articles by date range."""
        
        filtered_articles = []
        start_date = datetime.strptime(self.config.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(self.config.end_date, "%Y-%m-%d")
        
        for article in articles:
            article_date_str = article.get('articledate')
            if article_date_str:
                try:
                    # Parse the article date (assuming it's in ISO format)
                    article_date = datetime.fromisoformat(article_date_str.replace('Z', '+00:00'))
                    if start_date <= article_date <= end_date:
                        filtered_articles.append(article)
                except Exception as e:
                    # Skip articles with invalid dates
                    continue
        
        return filtered_articles
    
    def _filter_social_feeds_by_date(self, social_feeds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter social feeds by date range."""
        
        filtered_social_feeds = []
        start_date = datetime.strptime(self.config.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(self.config.end_date, "%Y-%m-%d")
        
        for social_feed in social_feeds:
            feed_date_str = social_feed.get('FEEDDATE')
            if feed_date_str:
                try:
                    # Parse the feed date (assuming it's in ISO format)
                    feed_date = datetime.fromisoformat(feed_date_str.replace('Z', '+00:00'))
                    if start_date <= feed_date <= end_date:
                        filtered_social_feeds.append(social_feed)
                except Exception as e:
                    # Skip social feeds with invalid dates
                    continue
        
        return filtered_social_feeds
    
    def _create_article_tag_efficiently(self, article: Dict[str, Any], company_id: str, index_result) -> bool:
        """Create MongoDB tag for an article efficiently."""
        
        try:
            # Get company name
            company_name = self._get_company_name(company_id)
            
            # Create tag data
            tag_data = {
                "KEYWORDS": f"Efficient backtracking match for {company_id}",
                "COMPANYID": company_id
            }
            
            # Create sources based on field matches
            article_id = article['es_id']
            field_matches = index_result.field_matches.get(article_id, {})
            matched_fields = field_matches.get("matched_fields", [])
            
            sources = {
                "headline": ["headline"] if "headline" in matched_fields else [],
                "content": ["content"] if "content" in matched_fields else [],
                "summary": ["summary"] if "summary" in matched_fields else []
            }
            
            # Create tag document
            tag_doc = self.mongo_creator.create_article_tag(
                article_id=article_id,
                pg_article_id=article['articleid'],
                company_id=company_id,
                company_name=company_name,
                tag_data=tag_data,
                sources=sources,
                is_new=True
            )
            
            # Save to MongoDB
            tag_result = {
                "tag_id": tag_doc["_id"],
                "tag_doc": tag_doc,
                "article_id": article_id,
                "company_id": company_id,
                "company_name": company_name,
                "is_article": True
            }
            
            return self.mongo_creator.save_tags_to_mongo([tag_result], update_type=1)
            
        except Exception as e:
            print(f"Error creating article tag: {e}")
            return False
    
    def _create_social_feed_tag_efficiently(self, social_feed: Dict[str, Any], company_id: str, index_result) -> bool:
        """Create MongoDB tag for a social feed efficiently."""
        
        try:
            # Get company name
            company_name = self._get_company_name(company_id)
            
            # Create tag data
            tag_data = {
                "KEYWORDS": f"Efficient backtracking match for {company_id}",
                "COMPANYID": company_id
            }
            
            # Create sources based on field matches
            social_feed_id = social_feed['es_id']
            field_matches = index_result.field_matches.get(social_feed_id, {})
            matched_fields = field_matches.get("matched_fields", [])
            
            sources = {
                "headline": ["headline"] if "headline" in matched_fields else [],
                "content": ["content"] if "content" in matched_fields else [],
                "summary": ["summary"] if "summary" in matched_fields else []
            }
            
            # Create tag document
            tag_doc = self.mongo_creator.create_social_tag(
                social_feed_id=social_feed_id,
                pg_social_feed_id=social_feed['SOCIALFEEDID'],
                company_id=company_id,
                company_name=company_name,
                tag_data=tag_data,
                sources=sources,
                is_new=True
            )
            
            # Save to MongoDB
            tag_result = {
                "tag_id": tag_doc["_id"],
                "tag_doc": tag_doc,
                "social_feed_id": social_feed_id,
                "company_id": company_id,
                "company_name": company_name,
                "is_article": False
            }
            
            return self.mongo_creator.save_tags_to_mongo([tag_result], update_type=1)
            
        except Exception as e:
            print(f"Error creating social feed tag: {e}")
            return False
    
    def _get_company_name(self, company_id: str) -> str:
        """Get company name from company ID."""
        
        try:
            companies = self.espreview_engine.list_companies(limit=1000)
            for company in companies:
                if company.get('companyId') == company_id:
                    return company.get('companyName', company_id)
            return company_id
        except Exception as e:
            print(f"Error getting company name for {company_id}: {e}")
            return company_id
    
    def _save_results(self):
        """Save results to file."""
        
        try:
            results_file = Path(self.config.results_file)
            with open(results_file, 'w') as f:
                json.dump(self.results, f, indent=2, default=str)
            print(f"Results saved to: {results_file}")
        except Exception as e:
            print(f"Error saving results: {e}")

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point for efficient backtracking."""
    
    # Configuration - Customize this for your needs
    config = BacktrackingConfig(
        # Date range (last 10 days)
        start_date=(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"),
        end_date=datetime.now().strftime("%Y-%m-%d"),
        
        # Company IDs to process
        company_ids=[
            "CYBERPE865",  # CyberPeace Foundation
            "HUL",         # Hindustan Unilever
            "TATA",        # Tata Group
            # Add more company IDs here...
        ],
        
        # Processing settings
        batch_size=100,
        max_workers=4,
        parallel_processing=True,
        
        # Output settings
        dry_run=False,  # Set to True for testing
        verbose=True,
        save_results=True,
        results_file="efficient_backtracking_results.json"
    )
    
    # Run efficient backtracking
    engine = EfficientBacktrackingEngine(config)
    results = engine.run_efficient_backtracking()
    
    return results

if __name__ == "__main__":
    main()
