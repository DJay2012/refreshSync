#!/usr/bin/env python3
"""
Percolator-based Backtracking Engine for esPreview Simplified Version

This module uses the same percolator approach as the main esTagging system:
1. Creates combined content documents (headline + content + summary)
2. Uses percolator queries to find matches
3. Processes results and creates MongoDB tags

This matches exactly how the main tagger works.
"""

import sys
import os
import json
import time
import logging
import traceback
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

logger = logging.getLogger("backtracking")

# Load environment variables from .env file (local directory)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

# Add the current directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from espreview import ESPreviewEngine, ESPreviewConfig
from backtracking_engine import BacktrackingConfig, MongoTagCreator, MongoCheckpointManager

"""
Import tag_article with priority:
1) esBacktracking.core.tagger (in this repo)
2) elasticTagging repo via ELASTIC_TAGGING_PATH, /elasticTagging, or sibling path
"""

tag_article = None
_import_error_msg = None
try:
    # First, prefer in-repo tagger under esBacktracking/core/tagger.py
    from esBacktracking.core.tagger import tag_article  # type: ignore
except Exception as _inrepo_err:
    try:
        current_file_dir = os.path.dirname(os.path.abspath(__file__))
        refresh_es_api_dir = os.path.dirname(current_file_dir)

        candidate_roots = []
        if os.getenv("ELASTIC_TAGGING_PATH"):
            candidate_roots.append(os.getenv("ELASTIC_TAGGING_PATH"))
        candidate_roots.append("/elasticTagging")
        candidate_roots.append(os.path.join(os.path.dirname(refresh_es_api_dir), 'elasticTagging'))

        found_src_path = None
        attempted = []
        for root in candidate_roots:
            src_path = os.path.join(root, 'src')
            tagger_file = os.path.join(src_path, 'core', 'tagger.py')
            attempted.append(tagger_file)
            if os.path.exists(tagger_file):
                found_src_path = src_path
                break

        if not found_src_path:
            raise ImportError(
                f"esBacktracking.core.tagger not found ({_inrepo_err}). Also, elasticTagging tagger.py not found. Tried: " + "; ".join(attempted)
            )

        if found_src_path not in sys.path:
            sys.path.insert(0, found_src_path)
        from core.tagger import tag_article  # type: ignore
    except Exception as e:
        _import_error_msg = str(e)
        raise
    
    # Ensure required environment variables are set for Config.py initialization
    # Config.py initializes Elasticsearch during import, so we need ES_HOST, ES_USER, ES_PASSWORD
    # Map from refresh_es_api environment variables/config to elasticTagging variables
    
    # Load environment variables from .env file if it exists
    env_file = os.path.join(refresh_es_api_dir, '.env')
    if os.path.exists(env_file):
        load_dotenv(env_file)
    
    # Also try loading from elasticTagging directory (if using env var, derive root)
    elastic_root = os.getenv("ELASTIC_TAGGING_PATH") or os.path.dirname(found_src_path)
    elasticTagging_env = os.path.join(elastic_root, '.env')
    if os.path.exists(elasticTagging_env):
        load_dotenv(elasticTagging_env)
    
    # Try to get ES config from app.config.settings (used by refresh_es_api)
    try:
        # Add refresh_es_api to path so we can import app.config
        if refresh_es_api_dir not in sys.path:
            sys.path.insert(0, refresh_es_api_dir)
        
        from app.config import settings
        
        # Map from app.config to elasticTagging env vars
        if not os.getenv("ES_HOST"):
            if hasattr(settings, 'ELASTICSEARCH_URL') and settings.ELASTICSEARCH_URL:
                os.environ["ES_HOST"] = str(settings.ELASTICSEARCH_URL)
        
        if not os.getenv("ES_USER"):
            if hasattr(settings, 'ES_USERNAME') and settings.ES_USERNAME:
                os.environ["ES_USER"] = str(settings.ES_USERNAME)
            else:
                os.environ["ES_USER"] = "elastic"  # Default
        
        if not os.getenv("ES_PASSWORD"):
            if hasattr(settings, 'ES_PASSWORD') and settings.ES_PASSWORD:
                os.environ["ES_PASSWORD"] = str(settings.ES_PASSWORD)
            else:
                os.environ["ES_PASSWORD"] = "New#pnq#Change!"  # Default password
        
    except ImportError:
        # If app.config not available, try env vars directly
        if not os.getenv("ES_HOST"):
            es_host = (os.getenv("ES_HOST") or 
                      os.getenv("ELASTICSEARCH_URL") or 
                      os.getenv("ELASTICSEARCH_HOST") or 
                      os.getenv("ES_HOSTS"))
            if es_host:
                if isinstance(es_host, list):
                    es_host = es_host[0]
                os.environ["ES_HOST"] = str(es_host).strip()
        
        if not os.getenv("ES_USER"):
            es_user = (os.getenv("ES_USERNAME") or 
                      os.getenv("ELASTICSEARCH_USER") or 
                      os.getenv("ES_USER") or 
                      "elastic")
            os.environ["ES_USER"] = str(es_user)
        
        if not os.getenv("ES_PASSWORD"):
            es_password = (os.getenv("ES_PASSWORD") or 
                          os.getenv("ELASTICSEARCH_PASSWORD") or 
                          "New#pnq#Change!")
            os.environ["ES_PASSWORD"] = str(es_password)
    
    # Set ES_INDEX_NAME if not set
    if not os.getenv("ES_INDEX_NAME"):
        es_index = (os.getenv("ES_INDEX_NAME") or 
                   os.getenv("ELASTICSEARCH_INDEX") or 
                   "companyboolreseachalllangtestv1")
        os.environ["ES_INDEX_NAME"] = str(es_index)
    
    # Verify ES_HOST is valid (not None or empty string)
    es_host_value = os.getenv("ES_HOST")
    if not es_host_value or es_host_value.strip() == "" or es_host_value.lower() == "none":
        raise ImportError(f"ES_HOST environment variable is invalid: '{es_host_value}'. Cannot initialize tagger. Please set ELASTICSEARCH_URL in app.config or ES_HOST in environment.")
    
    # Debug: Print what we're using (don't print password)
    print(f"Importing tagger with ES_HOST: {os.getenv('ES_HOST')}")
    print(f"Using ES_USER: {os.getenv('ES_USER')}")
    print(f"ES_PASSWORD set: {'Yes' if os.getenv('ES_PASSWORD') else 'No'}")
    print(f"Using ES_INDEX_NAME: {os.getenv('ES_INDEX_NAME')}")
    
    # Import tag_article from elasticTagging/src/core/tagger
    # Since we added elasticTagging/src to path, we import as core.tagger
    from core.tagger import tag_article
    print("Successfully imported tag_article")
except ImportError as e:
    _import_error_msg = f"ImportError: {str(e)}"
    print(f"ERROR: Could not import tag_article: {e}")
    import traceback
    traceback.print_exc()
    tag_article = None
except Exception as e:
    _import_error_msg = f"Exception: {str(e)}"
    print(f"ERROR: Unexpected error importing tag_article: {e}")
    import traceback
    traceback.print_exc()
    tag_article = None

# Ensure tag_article is available - raise error if not
if tag_article is None:
    error_msg = _import_error_msg or "Unknown import error"
    raise ImportError(f"CRITICAL: tag_article is required but could not be imported. {error_msg}. Please ensure elasticTagging is available and Config.py can initialize.")

# ============================================================================
# PERCOLATOR BACKTRACKING ENGINE
# ============================================================================

class PercolatorBacktrackingEngine:
    """Backtracking engine that uses the same percolator approach as the main tagger."""
    
    def __init__(self, config: BacktrackingConfig):
        self.config = config
        
        # Initialize components
        self.espreview_config = ESPreviewConfig.from_env()
        self.espreview_engine = ESPreviewEngine(self.espreview_config)
        self.mongo_creator = MongoTagCreator(config)
        
        # Thread pool executor for async tag_article calls
        self.tag_executor = ThreadPoolExecutor(max_workers=max(1, self.config.tag_workers))
        self.mongo_batch_buffer: List[Dict[str, Any]] = []
        
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
    
        # In-memory caches for MongoDB ID resolution — avoids repeated find_one calls when
        # the same article/feed matches multiple companies in the same job.
        self._article_id_cache: Dict[Any, Optional[str]] = {}
        self._social_feed_id_cache: Dict[Any, Optional[str]] = {}

        # Checkpoint/resume tracking
        self.checkpoint_data = None
        self.processed_article_ids = set()  # Track processed article ES IDs
        self.processed_social_feed_ids = set()  # Track processed social feed ES IDs
        self.processed_companies = set()  # Track which companies have been fully processed
        self.mongo_checkpoint_manager = None
        
        if self.config.enable_checkpoints:
            if self.config.use_mongo_checkpoints:
                # Use MongoDB for checkpoints
                self.mongo_checkpoint_manager = MongoCheckpointManager(
                    self.mongo_creator.mongo_db,
                    self.config.checkpoint_id
                )
                self._load_checkpoint()
            else:
                # Use filesystem for checkpoints
                self._load_checkpoint()
    
    async def run_percolator_backtracking(self, resume: bool = True) -> Dict[str, Any]:
        """Run backtracking using the percolator approach with checkpoint support."""

        current_company_ids = set(self.config.company_ids)
        logger.info("Starting Percolator Backtracking Process")
        logger.info(f"Date range: {self.config.start_date} to {self.config.end_date}")
        logger.info(f"Companies: {', '.join(self.config.company_ids)}")
        logger.info(f"Language: {'all languages' if self.config.language is None else self.config.language}")
        logger.info(f"Process print: {self.config.process_print}  |  Process online: {self.config.process_online}  |  Dry run: {self.config.dry_run}")

        # Check if resuming
        if resume and self.checkpoint_data:
            logger.info("*** RESUMING FROM CHECKPOINT ***")
            logger.info(f"Last checkpoint: {self.checkpoint_data.get('checkpoint_time', 'unknown')}")
            logger.info(f"Processed articles: {len(self.processed_article_ids):,}  |  social feeds: {len(self.processed_social_feed_ids):,}")
            # Restore results
            if 'total_articles_processed' in self.checkpoint_data:
                self.results['total_articles_processed'] = self.checkpoint_data.get('total_articles_processed', 0)
            if 'total_social_feeds_processed' in self.checkpoint_data:
                self.results['total_social_feeds_processed'] = self.checkpoint_data.get('total_social_feeds_processed', 0)
            if 'total_tags_created' in self.checkpoint_data:
                self.results['total_tags_created'] = self.checkpoint_data.get('total_tags_created', 0)
            if 'company_results' in self.checkpoint_data:
                # Only restore results for companies in the current job — drop stale entries
                loaded = self.checkpoint_data.get('company_results', {})
                self.results['company_results'] = {k: v for k, v in loaded.items() if k in current_company_ids}
        
        start_time = time.time()
        
        try:
            total_tags_created = self.results.get('total_tags_created', 0)

            # Initialize per-company result buckets once, then process all data in a single pass.
            company_stats = {}
            active_company_ids = []
            for company_id in self.config.company_ids:
                self.results["company_results"].setdefault(
                    company_id,
                    {
                        "company_id": company_id,
                        "articles_processed": 0,
                        "social_feeds_processed": 0,
                        "tags_created": 0,
                        "errors": []
                    }
                )
                logger.info(f"Validating percolator query for company: {company_id}")
                company_query = self._get_company_percolator_query(company_id)
                if not company_query:
                    msg = f"No percolator query found for {company_id}"
                    logger.warning(f"  {msg}")
                    self.results["company_results"][company_id]["errors"].append(msg)
                    continue
                logger.info(f"  Found percolator query for {company_id}")
                active_company_ids.append(company_id)
                company_stats[company_id] = {
                    "articles_matched": 0,
                    "social_matched": 0,
                    "tags_created": 0
                }

            if not active_company_ids:
                self.results["errors"].append("No valid companies with percolator queries to process")
            else:
                tasks_to_run = []
                if self.config.process_print:
                    logger.info("Processing print articles (single pass for all companies)...")
                    tasks_to_run.append(self._process_articles_streaming(active_company_ids, company_stats))
                else:
                    logger.info("Skipping print articles (process_print=False)")

                if self.config.process_online:
                    logger.info("Processing online/social feeds (single pass for all companies)...")
                    tasks_to_run.append(self._process_social_feeds_streaming(active_company_ids, company_stats))
                else:
                    logger.info("Skipping online/social feeds (process_online=False)")

                if tasks_to_run:
                    task_results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
                    for result in task_results:
                        if isinstance(result, Exception):
                            self.results["errors"].append(str(result))
                            continue
                        self.results["total_articles_processed"] += result.get("articles_processed", 0)
                        self.results["total_social_feeds_processed"] += result.get("social_feeds_processed", 0)
                        total_tags_created += result.get("tags_created", 0)

                # Flush any buffered Mongo writes at end of scan.
                flushed = self._flush_mongo_batch()
                total_tags_created += flushed

                for company_id in active_company_ids:
                    company_result = self.results["company_results"][company_id]
                    company_result["articles_processed"] = company_stats[company_id]["articles_matched"]
                    company_result["social_feeds_processed"] = company_stats[company_id]["social_matched"]
                    company_result["tags_created"] = company_stats[company_id]["tags_created"]
                    logger.info(f"  Company {company_id}: {company_result['tags_created']} tags created")

            # Update results
            self.results["end_time"] = datetime.now()
            self.results["total_tags_created"] = total_tags_created
            self.results["processing_time_seconds"] = time.time() - start_time

            if self.config.enable_checkpoints:
                self._clear_checkpoint()
            if self.config.save_results:
                self._save_results()

            logger.info(f"Backtracking completed — tags: {self.results['total_tags_created']}  time: {self.results['processing_time_seconds']:.1f}s")
            return self.results

        except KeyboardInterrupt:
            logger.warning("Interrupted by user — saving checkpoint")
            if self.config.enable_checkpoints:
                self._save_checkpoint()
            self.results["errors"].append("Interrupted by user")
            raise
        except Exception as e:
            logger.error(f"Percolator backtracking failed: {e}")
            logger.error(traceback.format_exc())
            if self.config.enable_checkpoints:
                self._save_checkpoint()
            self.results["errors"].append(str(e))
            return self.results
    
    async def _process_company_with_percolator(self, company_id: str) -> Dict[str, Any]:
        """Process a single company using percolator approach."""
        
        results = {
            "company_id": company_id,
            "articles_processed": 0,
            "social_feeds_processed": 0,
            "tags_created": 0,
            "errors": []
        }
        
        try:
            # Get company query from percolator index
            print(f"  Getting percolator query for {company_id}...")
            company_query = self._get_company_percolator_query(company_id)
            
            if not company_query:
                print(f"  Error: No percolator query found for {company_id}")
                results["errors"].append(f"No percolator query found for {company_id}")
                return results
            
            print(f"  Success: Found percolator query for {company_id}")
            
            # Process articles and social feeds based on config flags
            tasks_to_run = []
            task_names = []
            
            # Add article task if process_print is enabled
            if self.config.process_print:
                print(f"  Processing print articles...")
                single_company_stats = {company_id: {"articles_matched": 0, "social_matched": 0, "tags_created": 0}}
                article_task = self._process_articles_streaming([company_id], single_company_stats)
                tasks_to_run.append(article_task)
                task_names.append("articles")
            else:
                print(f"  Skipping print articles (process_print=False)")
                article_results = {"processed": 0, "tags_created": 0, "errors": []}
            
            # Add social feed task if process_online is enabled
            if self.config.process_online:
                print(f"  Processing online/social feeds...")
                single_company_stats = {company_id: {"articles_matched": 0, "social_matched": 0, "tags_created": 0}}
                social_feed_task = self._process_social_feeds_streaming([company_id], single_company_stats)
                tasks_to_run.append(social_feed_task)
                task_names.append("social_feeds")
            else:
                print(f"  Skipping online/social feeds (process_online=False)")
                social_feed_results = {"processed": 0, "tags_created": 0, "errors": []}
            
            # Run enabled tasks concurrently
            if tasks_to_run:
                task_results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
                
                # Map results back to article_results and social_feed_results
                result_idx = 0
                if self.config.process_print:
                    article_results = task_results[result_idx]
                    result_idx += 1
                if self.config.process_online:
                    social_feed_results = task_results[result_idx]
                    result_idx += 1
            else:
                # Both are disabled
                article_results = {"processed": 0, "tags_created": 0, "errors": []}
                social_feed_results = {"processed": 0, "tags_created": 0, "errors": []}
            
            # Handle results
            if isinstance(article_results, Exception):
                print(f"  Error processing articles: {article_results}")
                article_results = {"processed": 0, "tags_created": 0, "errors": [str(article_results)]}
            if isinstance(social_feed_results, Exception):
                print(f"  Error processing social feeds: {social_feed_results}")
                social_feed_results = {"processed": 0, "tags_created": 0, "errors": [str(social_feed_results)]}
            
            results["articles_processed"] = article_results["processed"]
            results["tags_created"] += article_results["tags_created"]
            results["errors"].extend(article_results["errors"])
            
            results["social_feeds_processed"] = social_feed_results["processed"]
            results["tags_created"] += social_feed_results["tags_created"]
            results["errors"].extend(social_feed_results["errors"])
            
            print(f"  Success: Company {company_id}: {results['tags_created']} tags created")
            
        except Exception as e:
            error_msg = f"Error processing company {company_id}: {str(e)}"
            print(f"  Error: {error_msg}")
            results["errors"].append(error_msg)
        
        return results
    
    def _get_company_percolator_query(self, company_id: str) -> Optional[Dict[str, Any]]:
        """Get the percolator query for a company.
        
        When language is None (all languages), returns lang_en as base query.
        The tagger will use each article's actual language for matching.
        """
        
        try:
            # When processing all languages, use lang_en as base (English is always checked by tagger)
            # The tagger will automatically use each article's actual language
            lang_field = "lang_en" if self.config.language is None else f"lang_{self.config.language}"
            
            # Search for company in percolator index
            search_query = {
                "query": {"term": {"companyId": company_id}},
                "size": 1,
                "_source": ["companyId", "companyName", lang_field]
            }
            
            response = self.espreview_engine.es_client.search(
                index=self.espreview_config.percolator_index, 
                body=search_query
            )
            
            hits = response.get('hits', {}).get('hits', [])
            if not hits:
                return None
            
            company_doc = hits[0]['_source']
            query = company_doc.get(lang_field)
            
            # If specific language not found and we're processing all languages, fallback to lang_en
            if query is None and self.config.language is None:
                query = company_doc.get('lang_en')
            
            return query
            
        except Exception as e:
            print(f"Error getting percolator query: {e}")
            return None
    
    def _get_historical_articles_with_content(self) -> List[Dict[str, Any]]:
        """Get ALL historical articles with full content for percolator matching."""
        
        try:
            # Build date range query
            date_query = {
                "range": {
                    "articleInfo.articleDate": {
                        "gte": self.config.start_date,
                        "lte": self.config.end_date,
                        "format": "yyyy-MM-dd"
                    }
                }
            }
            
            print(f"  Getting ALL articles from {self.config.start_date} to {self.config.end_date}")
            
            # Use scroll API to get ALL articles
            search_request = {
                "query": {
                    "bool": {
                        "must": [date_query]
                    }
                },
                "size": 5000,  # Increased to 5000 for maximum throughput - fewer round trips
                "sort": [{"articleInfo.articleDate": {"order": "desc"}}],
                "_source": [
                    "_id",
                    "articleId",
                    "articleData.headlines", 
                    "articleData.summary",
                    "articleData.text",
                    "articleInfo.articleDate",
                    "articleData.language"
                ]
            }
            
            # Initialize scroll
            response = self.espreview_engine.es_client.search(
                index="printarticleindex", 
                body=search_request,
                scroll='2m'  # Keep scroll alive for 2 minutes
            )
            
            articles = []
            scroll_id = response.get('_scroll_id')
            total_hits = response.get('hits', {}).get('total', {}).get('value', 0)
            
            print(f"  Total articles available: {total_hits:,}")
            
            # Process first batch
            for hit in response.get('hits', {}).get('hits', []):
                source = hit['_source']
                article_data = source.get('articleData', {})
                article_info = source.get('articleInfo', {})
                
                article = {
                    'es_id': hit['_id'],
                    'articleid': source.get('articleId'),
                    'headlines': article_data.get('headlines', ''),
                    'summary': article_data.get('summary', ''),
                    'content': article_data.get('text', ''),
                    'articlelang': article_data.get('language', 'en'),
                    'articledate': article_info.get('articleDate')
                }
                articles.append(article)
            
            print(f"  Processed {len(articles):,} articles so far...")
            
            # Continue scrolling to get ALL articles
            while len(response.get('hits', {}).get('hits', [])) > 0:
                response = self.espreview_engine.es_client.scroll(
                    scroll_id=scroll_id,
                    scroll='2m'
                )
                
                for hit in response.get('hits', {}).get('hits', []):
                    source = hit['_source']
                    article_data = source.get('articleData', {})
                    article_info = source.get('articleInfo', {})
                    
                    article = {
                        'es_id': hit['_id'],
                        'articleid': source.get('articleId'),
                        'headlines': article_data.get('headlines', ''),
                        'summary': article_data.get('summary', ''),
                        'content': article_data.get('text', ''),
                        'articlelang': article_data.get('language', 'en'),
                        'articledate': article_info.get('articleDate')
                    }
                    articles.append(article)
                
                if len(articles) % 10000 == 0:
                    print(f"  Processed {len(articles):,} articles so far...")
            
            # Clear scroll
            if scroll_id:
                try:
                    self.espreview_engine.es_client.clear_scroll(scroll_id=scroll_id)
                except Exception as e:
                    err_text = str(e)
                    # Treat ES NotFoundError for already-freed/expired scroll as informational
                    if ("NotFoundError" in err_text or "404" in err_text) and ("'succeeded': True" in err_text or '"succeeded": true' in err_text):
                        print("    Scroll already cleared/expired (informational) during article retrieval")
                    else:
                        raise
            
            print(f"  Found {len(articles):,} articles total")
            return articles
            
        except Exception as e:
            print(f"Error retrieving articles: {e}")
            return []
    
    def _get_historical_social_feeds_with_content(self) -> List[Dict[str, Any]]:
        """Get ALL historical social feeds with full content for percolator matching."""
        
        try:
            # Build date range query
            date_query = {
                "range": {
                    "feedData.feedDate": {
                        "gte": self.config.start_date,
                        "lte": self.config.end_date,
                        "format": "yyyy-MM-dd"
                    }
                }
            }
            
            print(f"  Getting ALL social feeds from {self.config.start_date} to {self.config.end_date}")
            
            # Use scroll API to get ALL social feeds
            search_request = {
                "query": {
                    "bool": {
                        "must": [date_query]
                    }
                },
                "size": 5000,  # Increased to 5000 for maximum throughput - fewer round trips
                "sort": [{"feedData.feedDate": {"order": "desc"}}],
                "_source": [
                    "_id",
                    "socialFeedId",
                    "feedData.headlines",
                    "feedData.summary", 
                    "feedData.text",
                    "feedData.feedDate",
                    "feedData.language"
                ]
            }
            
            # Initialize scroll with longer timeout for large batches
            response = self.espreview_engine.es_client.search(
                index="socialfeedindex", 
                body=search_request,
                scroll='5m'  # Increased from 2m to 5m for better reliability with large datasets
            )
            
            social_feeds = []
            scroll_id = response.get('_scroll_id')
            total_hits = response.get('hits', {}).get('total', {}).get('value', 0)
            
            print(f"  Total social feeds available: {total_hits:,}")
            
            # Process first batch
            for hit in response.get('hits', {}).get('hits', []):
                source = hit['_source']
                feed_data = source.get('feedData', {})
                
                social_feed = {
                    'es_id': hit['_id'],
                    'SOCIALFEEDID': source.get('socialFeedId'),
                    'HEADLINE': feed_data.get('headlines', ''),
                    'SUMMARY': feed_data.get('summary', ''),
                    'CONTENT': feed_data.get('text', ''),
                    'LANGUAGE': feed_data.get('language', 'en'),
                    'FEEDDATE': feed_data.get('feedDate')
                }
                social_feeds.append(social_feed)
            
            print(f"  Processed {len(social_feeds):,} social feeds so far...")
            
            # Continue scrolling to get ALL social feeds
            while len(response.get('hits', {}).get('hits', [])) > 0:
                try:
                    response = self.espreview_engine.es_client.scroll(
                        scroll_id=scroll_id,
                        scroll='5m'  # Increase scroll timeout to 5 minutes for large batches
                    )
                except Exception as scroll_error:
                    # If scroll context expired, log and break (checkpoint will save progress)
                    error_str = str(scroll_error)
                    if "search_phase_execution_exception" in error_str or "No search context" in error_str:
                        processed_count = len(social_feeds)  # Use the list length as processed count
                        print(f"    Scroll context expired after retrieving {processed_count:,} social feeds - checkpoint saved, can resume")
                        break
                    else:
                        raise
                
                for hit in response.get('hits', {}).get('hits', []):
                    source = hit['_source']
                    feed_data = source.get('feedData', {})
                    
                    social_feed = {
                        'es_id': hit['_id'],
                        'SOCIALFEEDID': source.get('socialFeedId'),
                        'HEADLINE': feed_data.get('headlines', ''),
                        'SUMMARY': feed_data.get('summary', ''),
                        'CONTENT': feed_data.get('text', ''),
                        'LANGUAGE': feed_data.get('language', 'en'),
                        'FEEDDATE': feed_data.get('feedDate')
                    }
                    social_feeds.append(social_feed)
                
                if len(social_feeds) % 10000 == 0:
                    print(f"  Processed {len(social_feeds):,} social feeds so far...")
            
            # Clear scroll
            if scroll_id:
                try:
                    self.espreview_engine.es_client.clear_scroll(scroll_id=scroll_id)
                except Exception as e:
                    err_text = str(e)
                    if ("NotFoundError" in err_text or "404" in err_text) and ("'succeeded': True" in err_text or '"succeeded": true' in err_text):
                        print("    Scroll already cleared/expired (informational) during social feed retrieval")
                    else:
                        raise
            
            print(f"  Found {len(social_feeds):,} social feeds total")
            return social_feeds
            
        except Exception as e:
            print(f"Error retrieving social feeds: {e}")
            return []
    
    # Language code normalisation — mirrors tagger.py's language_mapping
    _LANG_MAP = {
        'Hindi': 'hi', 'English': 'en', 'Gujarati': 'gu', 'Telugu': 'te',
        'Marathi': 'mr', 'Punjabi': 'pa', 'Malayalam': 'ml', 'Kannada': 'kn',
        'Bengali': 'bn', 'Tamil': 'ta', 'Urdu': 'ur', 'Odia': 'or',
        'Assamese': 'as', 'Maithili': 'mai', 'Dogri': 'doi',
        'Chinese': 'en', 'Vietnamese': 'en',
        'hi': 'hi', 'en': 'en', 'gu': 'gu', 'te': 'te', 'mr': 'mr',
        'pa': 'pa', 'ml': 'ml', 'kn': 'kn', 'bn': 'bn', 'ta': 'ta',
        'ur': 'ur', 'or': 'or', 'as': 'as', 'mai': 'mai', 'doi': 'doi',
        'zh': 'en', 'vi': 'en',
        'ENGLISH': 'en', 'HINDI': 'hi', 'GUJARATI': 'gu', 'TELUGU': 'te',
        'MARATHI': 'mr', 'PUNJABI': 'pa', 'MALAYALAM': 'ml', 'KANNADA': 'kn',
        'BENGALI': 'bn', 'TAMIL': 'ta', 'URDU': 'ur', 'ODIA': 'or',
        'ASSAMESE': 'as', 'MAITHILI': 'mai', 'DOGRI': 'doi',
        'CHINESE': 'en', 'VIETNAMESE': 'en',
    }

    def _tag_article_for_companies(
        self,
        article_id: str,
        headline: str,
        summary: str,
        content: str,
        language: str,
        company_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """Percolator search filtered to just the requested company IDs.

        Identical logic to tagger.py Tag() + tag_article() but adds a
        ``terms`` filter so ES only evaluates the N_job percolator documents
        instead of all 14,655.  For a 5-company job this is ~3000× less work
        per article.
        """
        import re
        from collections import defaultdict

        lang_code = self._LANG_MAP.get(language, 'en')
        languages_to_search = [lang_code]
        if lang_code != 'en':
            languages_to_search.append('en')

        headline = headline or ""
        content  = content  or ""
        summary  = summary  or ""

        combined = f"{headline}\n{content}\n{summary}"
        document = {"content": combined, "content_case_sensitive": combined}

        headline_end   = len(headline)
        content_start  = headline_end + 1
        content_end    = content_start + len(content)
        summary_start  = content_end  + 1
        summary_end    = summary_start + len(summary)

        skip_kw = {
            "The","the","is","or","and","a","an","in","on","at","to","of","for",
            "with","by","from","about","as","into","like","after","over","under",
            "between","through","during","before","above","below","up","down",
            "out","off","then","but","so","yet","nor","M"
        }

        percolator_index = self.espreview_config.percolator_index
        es_client        = self.espreview_engine.es_client
        company_ids_list = list(company_ids)

        all_hits: List[Dict] = []
        for current_lang in languages_to_search:
            field = f"lang_{current_lang}"
            query = {
                "query": {
                    "bool": {
                        "must": {
                            "percolate": {"field": field, "document": document}
                        },
                        "filter": {
                            "terms": {"companyId": company_ids_list}
                        }
                    }
                },
                "_source": ["companyId", "companyName", field],
                "highlight": {
                    "require_field_match": False,
                    "fields": {
                        "content":                {"number_of_fragments": 0},
                        "content_case_sensitive": {"number_of_fragments": 0},
                    },
                    "pre_tags":  ["<em>"],
                    "post_tags": ["</em>"],
                },
                "size": max(len(company_ids_list) * 2, 10),
            }
            try:
                resp = es_client.search(index=percolator_index, body=query)
            except Exception as e:
                logger.error(f"Filtered percolator search error: {e}")
                continue

            hits = resp.body.get('hits', {}).get('hits', []) if hasattr(resp, 'body') else resp.get('hits', {}).get('hits', [])

            for hit in hits:
                source = hit.get('_source', {})
                highlighted_words_with_source = []

                if "highlight" not in hit:
                    # Fallback: extract phrases from the stored percolator query
                    def _extract_phrases(obj):
                        phrases = []
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if k == "match_phrase" and "content" in v and "query" in v["content"]:
                                    phrases.append(v["content"]["query"])
                                elif isinstance(v, (dict, list)):
                                    phrases.extend(_extract_phrases(v))
                        elif isinstance(obj, list):
                            for item in obj:
                                phrases.extend(_extract_phrases(item))
                        return phrases

                    for phrase in _extract_phrases(source.get(field, {})):
                        phrase_lower = phrase.lower()
                        if phrase_lower in headline.lower():
                            highlighted_words_with_source.append((phrase, "headline"))
                        elif phrase_lower in content.lower():
                            highlighted_words_with_source.append((phrase, "content"))
                        elif phrase_lower in summary.lower():
                            highlighted_words_with_source.append((phrase, "summary"))
                else:
                    for hl_field in ("content", "content_case_sensitive"):
                        for fragment in hit["highlight"].get(hl_field, []):
                            for match in re.finditer(r'<em>(.*?)</em>', fragment):
                                word = match.group(1)
                                pos  = combined.find(word)
                                if pos == -1:
                                    src = "unknown"
                                elif pos < headline_end:
                                    src = "headline"
                                elif pos < content_end:
                                    src = "content"
                                elif pos < summary_end:
                                    src = "summary"
                                else:
                                    src = "unknown"
                                highlighted_words_with_source.append((word, src))

                if not highlighted_words_with_source:
                    continue

                keywords = [w for w, _ in highlighted_words_with_source]
                if not any(kw not in skip_kw for kw in keywords):
                    continue

                all_hits.append({
                    "company":           source,
                    "search_language":   current_lang,
                    "highlight_sources": [
                        {"keyword": w, "source": s} for w, s in highlighted_words_with_source
                    ],
                })

        # Merge by company (same as tag_article)
        from collections import defaultdict
        company_results: Dict = defaultdict(lambda: {
            "ARTICLEID":  article_id,
            "COMPANYID":  "",
            "COMPANYNAME": "",
            "KEYWORDS":   set(),
            "SOURCES":    defaultdict(set),
        })
        for item in all_hits:
            cid  = item["company"].get("companyId", "")
            cname= item["company"].get("companyName", "")
            key  = (cid, cname)
            company_results[key]["COMPANYID"]   = cid
            company_results[key]["COMPANYNAME"] = cname
            for hs in item["highlight_sources"]:
                kw  = hs["keyword"]
                src = hs["source"]
                company_results[key]["KEYWORDS"].add(kw)
                company_results[key]["SOURCES"][kw].add(src)

        results = []
        for v in company_results.values():
            v["KEYWORDS"] = ", ".join(sorted(v["KEYWORDS"]))
            v["SOURCES"]  = {k: list(s) for k, s in v["SOURCES"].items()}
            results.append(v)
        return results

    def _tag_articles_batch_msearch(
        self,
        items: List[Dict[str, Any]],
        company_ids: List[str],
        is_article: bool = True,
    ) -> List[List[Dict[str, Any]]]:
        """Batch-percolate a list of articles/feeds via a single msearch request.

        Returns a list of per-item tag result lists (same format as
        _tag_article_for_companies) so the caller can map results back by index.

        Instead of one ES round trip per document (500 calls for a page of 500),
        this sends one msearch with all percolate queries, reducing ES round trips
        from O(N) to O(1) per batch.  Non-English items get an extra msearch body
        for their native-language field — still just one HTTP request total.
        """
        import re
        from collections import defaultdict

        if not items:
            return []

        percolator_index = self.espreview_config.percolator_index
        es_client = self.espreview_engine.es_client
        company_ids_list = list(company_ids)

        skip_kw = {
            "The", "the", "is", "or", "and", "a", "an", "in", "on", "at", "to",
            "of", "for", "with", "by", "from", "about", "as", "into", "like",
            "after", "over", "under", "between", "through", "during", "before",
            "above", "below", "up", "down", "out", "off", "then", "but", "so",
            "yet", "nor", "M",
        }

        # ── Build per-item metadata ──────────────────────────────────────────
        item_data = []
        for item in items:
            if is_article:
                headline = item.get("headlines", "") or ""
                content  = item.get("content",   "") or ""
                summary  = item.get("summary",   "") or ""
                lang_raw = item.get("articlelang", "en")
            else:
                headline = item.get("HEADLINE", "") or ""
                content  = item.get("CONTENT",  "") or ""
                summary  = item.get("SUMMARY",  "") or ""
                lang_raw = item.get("LANGUAGE",  "en")

            lang_code = self._LANG_MAP.get(lang_raw, "en")
            combined  = f"{headline}\n{content}\n{summary}"
            headline_end  = len(headline)
            content_end   = headline_end + 1 + len(content)
            summary_end   = content_end  + 1 + len(summary)

            item_data.append({
                "id":           str(item.get("es_id", "")),
                "headline":     headline,
                "content":      content,
                "summary":      summary,
                "combined":     combined,
                "lang_code":    lang_code,
                "headline_end": headline_end,
                "content_end":  content_end,
                "summary_end":  summary_end,
                "doc":          {"content": combined, "content_case_sensitive": combined},
            })

        def _build_percolate_body(field: str, doc: dict) -> dict:
            return {
                "query": {
                    "bool": {
                        "must":   {"percolate": {"field": field, "document": doc}},
                        "filter": {"terms": {"companyId": company_ids_list}},
                    }
                },
                "_source": ["companyId", "companyName", field],
                "highlight": {
                    "require_field_match": False,
                    "fields": {
                        "content":                {"number_of_fragments": 0},
                        "content_case_sensitive": {"number_of_fragments": 0},
                    },
                    "pre_tags":  ["<em>"],
                    "post_tags": ["</em>"],
                },
                "size": max(len(company_ids_list) * 2, 10),
            }

        # ── Build msearch bodies ─────────────────────────────────────────────
        # Phase 1 (indices 0..N-1): every item against lang_en
        # Phase 2 (indices N..N+M-1): non-en items against their native lang
        msearch_bodies: List[dict] = []
        non_en_items: List[tuple] = []   # (original_index, item_d)

        for idx, d in enumerate(item_data):
            msearch_bodies.append({"index": percolator_index})
            msearch_bodies.append(_build_percolate_body("lang_en", d["doc"]))
            if d["lang_code"] != "en":
                non_en_items.append((idx, d))

        non_en_start = len(item_data)    # response offset for phase-2 entries
        for orig_idx, d in non_en_items:
            field = f"lang_{d['lang_code']}"
            msearch_bodies.append({"index": percolator_index})
            msearch_bodies.append(_build_percolate_body(field, d["doc"]))

        # ── Execute single msearch ───────────────────────────────────────────
        try:
            resp = es_client.msearch(body=msearch_bodies)
            responses = (
                resp.body.get("responses", [])
                if hasattr(resp, "body")
                else resp.get("responses", [])
            )
        except Exception as e:
            logger.error(f"msearch batch error: {e}")
            return [[] for _ in items]

        # ── Parse responses ──────────────────────────────────────────────────
        per_item_hits: Dict[int, list] = defaultdict(list)

        def _parse_resp(resp_obj: dict, item_idx: int, field: str) -> None:
            d    = item_data[item_idx]
            hits = resp_obj.get("hits", {}).get("hits", [])
            for hit in hits:
                source   = hit.get("_source", {})
                hl_words = []

                if "highlight" not in hit:
                    # Fallback: scan stored percolator query for match_phrase values
                    def _phrases(obj):
                        p = []
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if (k == "match_phrase"
                                        and isinstance(v, dict)
                                        and "content" in v
                                        and isinstance(v["content"], dict)
                                        and "query" in v["content"]):
                                    p.append(v["content"]["query"])
                                else:
                                    p.extend(_phrases(v))
                        elif isinstance(obj, list):
                            for elem in obj:
                                p.extend(_phrases(elem))
                        return p

                    for phrase in _phrases(source.get(field, {})):
                        pl = phrase.lower()
                        if pl in d["headline"].lower():
                            hl_words.append((phrase, "headline"))
                        elif pl in d["content"].lower():
                            hl_words.append((phrase, "content"))
                        elif pl in d["summary"].lower():
                            hl_words.append((phrase, "summary"))
                else:
                    for hl_field in ("content", "content_case_sensitive"):
                        for fragment in hit["highlight"].get(hl_field, []):
                            for m in re.finditer(r"<em>(.*?)</em>", fragment):
                                word = m.group(1)
                                pos  = d["combined"].find(word)
                                if pos == -1:
                                    src = "unknown"
                                elif pos < d["headline_end"]:
                                    src = "headline"
                                elif pos < d["content_end"]:
                                    src = "content"
                                elif pos < d["summary_end"]:
                                    src = "summary"
                                else:
                                    src = "unknown"
                                hl_words.append((word, src))

                if not hl_words:
                    continue
                if not any(kw not in skip_kw for kw, _ in hl_words):
                    continue

                per_item_hits[item_idx].append({
                    "company":           source,
                    "highlight_sources": [{"keyword": w, "source": s} for w, s in hl_words],
                })

        # Phase 1: lang_en for every item
        for idx in range(len(item_data)):
            if idx < len(responses):
                _parse_resp(responses[idx], idx, "lang_en")

        # Phase 2: native lang for non-en items
        for j, (orig_idx, d) in enumerate(non_en_items):
            resp_idx = non_en_start + j
            if resp_idx < len(responses):
                _parse_resp(responses[resp_idx], orig_idx, f"lang_{d['lang_code']}")

        # ── Build final per-item tag results ─────────────────────────────────
        all_results: List[List[Dict[str, Any]]] = []
        for item_idx in range(len(items)):
            article_id = item_data[item_idx]["id"]
            hits       = per_item_hits.get(item_idx, [])

            company_map: Dict = defaultdict(lambda: {
                "ARTICLEID":   article_id,
                "COMPANYID":   "",
                "COMPANYNAME": "",
                "KEYWORDS":    set(),
                "SOURCES":     defaultdict(set),
            })
            for hit in hits:
                cid   = hit["company"].get("companyId",   "")
                cname = hit["company"].get("companyName", "")
                key   = (cid, cname)
                company_map[key]["COMPANYID"]   = cid
                company_map[key]["COMPANYNAME"] = cname
                for hs in hit["highlight_sources"]:
                    company_map[key]["KEYWORDS"].add(hs["keyword"])
                    company_map[key]["SOURCES"][hs["keyword"]].add(hs["source"])

            item_results = []
            for v in company_map.values():
                v["KEYWORDS"] = ", ".join(sorted(v["KEYWORDS"]))
                v["SOURCES"]  = {k: list(s) for k, s in v["SOURCES"].items()}
                item_results.append(v)

            all_results.append(item_results)

        return all_results

    async def _process_single_article(self, article: Dict[str, Any], company_ids: List[str], company_stats: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
        """Process a single article once and fan out tags to requested companies."""
        try:
            requested_company_ids = set(company_ids)
            tag_results = []
            try:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.get_event_loop()

                tag_results = await loop.run_in_executor(
                    self.tag_executor,
                    self._tag_article_for_companies,
                    str(article['es_id']),
                    article.get('headlines', ''),
                    article.get('summary', ''),
                    article.get('content', ''),
                    article.get('articlelang', 'en'),
                    requested_company_ids,
                )
            except Exception as e:
                return {"processed": True, "tags_created": 0, "error": f"Error calling tag_article: {e}"}

            tags_created = 0
            matched_company_ids = set()
            for tag_data in tag_results:
                company_id = tag_data.get("COMPANYID")
                if company_id not in requested_company_ids:
                    continue
                matched_company_ids.add(company_id)
                tag_result = self._create_article_tag_from_tagger(article, tag_data)
                if tag_result:
                    tags_created += 1
                    company_stats[company_id]["tags_created"] += 1
                    self._buffer_tag_result(tag_result)

            for company_id in matched_company_ids:
                company_stats[company_id]["articles_matched"] += 1

            return {"processed": True, "tags_created": tags_created, "error": None}
            
        except Exception as e:
            return {"processed": True, "tags_created": 0, "error": f"Article {article.get('articleid', 'unknown')}: {str(e)}"}
    
    async def _process_articles_with_percolator(self, articles: List[Dict[str, Any]], company_ids: List[str], company_stats: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
        """Process all articles using batched msearch percolation.

        Splits the page into msearch sub-batches (config.msearch_batch_size) and
        runs them concurrently in the thread pool — one ES round trip per sub-batch
        instead of one per document.
        """

        results = {
            "processed": 0,
            "tags_created": 0,
            "errors": []
        }

        if not articles:
            return results

        requested_company_ids = set(company_ids)
        msearch_batch = max(1, getattr(self.config, "msearch_batch_size", 100))
        loop = asyncio.get_running_loop()

        # Split page into sub-batches, run each as one msearch in the thread pool
        sub_batches = [articles[i:i + msearch_batch] for i in range(0, len(articles), msearch_batch)]
        sub_futures = [
            loop.run_in_executor(
                self.tag_executor,
                self._tag_articles_batch_msearch,
                batch,
                list(company_ids),
                True,   # is_article
            )
            for batch in sub_batches
        ]
        sub_results = await asyncio.gather(*sub_futures, return_exceptions=True)

        for batch_idx, batch_result in enumerate(sub_results):
            batch = sub_batches[batch_idx]
            if isinstance(batch_result, Exception):
                results["errors"].append(f"msearch batch {batch_idx} error: {batch_result}")
                results["processed"] += len(batch)
                continue

            for item_idx, article in enumerate(batch):
                results["processed"] += 1
                item_tag_results = batch_result[item_idx] if item_idx < len(batch_result) else []

                matched_company_ids = set()
                for tag_data in item_tag_results:
                    company_id = tag_data.get("COMPANYID")
                    if company_id not in requested_company_ids:
                        continue
                    matched_company_ids.add(company_id)
                    tag_result = self._create_article_tag_from_tagger(article, tag_data)
                    if tag_result:
                        results["tags_created"] += 1
                        company_stats[company_id]["tags_created"] += 1
                        self._buffer_tag_result(tag_result)

                for company_id in matched_company_ids:
                    company_stats[company_id]["articles_matched"] += 1

        return results
    
    async def _process_articles_streaming(self, company_ids: List[str], company_stats: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
        """Scan articles once and tag against all requested companies.

        Pipelined: the next ES page is fetched in a background thread while the
        current page is being tagged, so ES I/O and CPU/network tagging overlap.
        """

        results = {
            "processed": 0,
            "tags_created": 0,
            "errors": [],
            "articles_processed": 0,
            "social_feeds_processed": 0
        }

        try:
            page_size = max(100, min(self.config.es_page_size, 5000))
            keepalive = f"{max(1, self.config.es_keepalive_minutes)}m"
            logger.info(f"Scanning articles: {self.config.start_date} → {self.config.end_date}")

            date_query = {"range": {"articleInfo.articleDate": {"gte": self.config.start_date, "lte": self.config.end_date, "format": "yyyy-MM-dd"}}}
            source_fields = ["_id", "articleId", "articleData.headlines", "articleData.summary", "articleData.text", "articleInfo.articleDate", "articleData.language"]

            pit = self.espreview_engine.es_client.open_point_in_time(index="printarticleindex", keep_alive=keepalive)
            pit_id = pit["id"]
            total_hits = 0
            progress_started_at = time.time()
            last_progress_logged = 0
            batch_count = 0
            loop = asyncio.get_running_loop()

            def _es_search(body):
                return self.espreview_engine.es_client.search(body=body)

            def _build_body(current_pit_id, after):
                b = {
                    "size": page_size,
                    "query": {"bool": {"must": [date_query]}},
                    "sort": [{"articleInfo.articleDate": {"order": "desc"}}, {"_shard_doc": {"order": "desc"}}],
                    "_source": source_fields,
                    "pit": {"id": current_pit_id, "keep_alive": keepalive}
                }
                if after:
                    b["search_after"] = after
                return b

            def _hits_to_articles(hits):
                articles = []
                for hit in hits:
                    es_id = hit["_id"]
                    if es_id in self.processed_article_ids:
                        continue
                    source = hit["_source"]
                    article_data = source.get("articleData", {})
                    article_info = source.get("articleInfo", {})
                    articles.append({
                        "es_id": es_id,
                        "articleid": source.get("articleId"),
                        "headlines": article_data.get("headlines", ""),
                        "summary": article_data.get("summary", ""),
                        "content": article_data.get("text", ""),
                        "articlelang": article_data.get("language", "en"),
                        "articledate": article_info.get("articleDate")
                    })
                return articles

            # Fetch first page
            response = await loop.run_in_executor(None, _es_search, _build_body(pit_id, None))
            pit_id = response.get("pit_id", pit_id)
            total_hits = response.get("hits", {}).get("total", {}).get("value", 0)
            logger.info(f"Total articles in date range: {total_hits:,}")

            while True:
                hits = response.get("hits", {}).get("hits", [])
                if not hits:
                    break
                search_after = hits[-1].get("sort")

                batch_articles = _hits_to_articles(hits)
                logger.info(f"[articles] page {batch_count+1} — fetched {len(hits)} docs, {len(batch_articles)} new  (processed so far: {results['processed']:,}/{total_hits:,})")

                # Kick off next-page fetch immediately (runs in thread pool, non-blocking)
                next_fetch = loop.run_in_executor(None, _es_search, _build_body(pit_id, search_after))

                # Tag current batch while next page is being fetched
                if batch_articles:
                    batch_results = await self._process_articles_with_percolator(batch_articles, company_ids, company_stats)
                    results["processed"] += batch_results["processed"]
                    results["tags_created"] += batch_results["tags_created"]
                    results["errors"].extend(batch_results["errors"])
                    for article in batch_articles:
                        self.processed_article_ids.add(article["es_id"])

                # Wait for next page (likely already done)
                response = await next_fetch
                pit_id = response.get("pit_id", pit_id)

                batch_count += 1
                elapsed = max(time.time() - progress_started_at, 1e-6)
                rate = results["processed"] / elapsed
                percent = (results["processed"] / total_hits * 100.0) if total_hits else 0.0
                logger.info(f"[articles] page {batch_count} done — {results['processed']:,}/{total_hits:,} ({percent:.1f}%) | {rate:.1f} docs/sec | tags: {results['tags_created']:,}")
                if self.config.enable_checkpoints and (batch_count % 20 == 0 or results["processed"] % 20000 == 0):
                    self._save_checkpoint()

            try:
                self.espreview_engine.es_client.close_point_in_time(body={"id": pit_id})
            except Exception:
                pass

            logger.info(f"Article scan done: {results['processed']:,} scanned, {results['tags_created']:,} matched")
            results["articles_processed"] = results["processed"]

        except Exception as e:
            logger.error(f"Error in streaming article processing: {e}")
            results["errors"].append(str(e))

        return results
    
    async def _process_social_feeds_streaming(self, company_ids: List[str], company_stats: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
        """Scan social feeds once and tag against all requested companies.

        Pipelined: the next ES page is fetched in a background thread while the
        current page is being tagged, so ES I/O and tagging overlap.
        """

        results = {
            "processed": 0,
            "tags_created": 0,
            "errors": [],
            "articles_processed": 0,
            "social_feeds_processed": 0
        }

        try:
            page_size = max(100, min(self.config.es_page_size, 5000))
            keepalive = f"{max(1, self.config.es_keepalive_minutes)}m"
            progress_log_interval = max(1000, self.config.progress_log_interval)
            logger.info(f"Scanning social feeds: {self.config.start_date} → {self.config.end_date}")

            date_query = {"range": {"feedData.feedDate": {"gte": self.config.start_date, "lte": self.config.end_date, "format": "yyyy-MM-dd"}}}
            source_fields = ["_id", "socialFeedId", "feedData.headlines", "feedData.summary", "feedData.text", "feedData.feedDate", "feedData.language"]

            pit = self.espreview_engine.es_client.open_point_in_time(index="socialfeedindex", keep_alive=keepalive)
            pit_id = pit["id"]
            total_hits = 0
            progress_started_at = time.time()
            last_progress_logged = 0
            batch_count = 0
            loop = asyncio.get_running_loop()

            def _es_search(body):
                return self.espreview_engine.es_client.search(body=body)

            def _build_body(current_pit_id, after):
                b = {
                    "size": page_size,
                    "query": {"bool": {"must": [date_query]}},
                    "sort": [{"feedData.feedDate": {"order": "desc"}}, {"_shard_doc": {"order": "desc"}}],
                    "_source": source_fields,
                    "pit": {"id": current_pit_id, "keep_alive": keepalive}
                }
                if after:
                    b["search_after"] = after
                return b

            def _hits_to_feeds(hits):
                feeds = []
                for hit in hits:
                    es_id = hit["_id"]
                    if es_id in self.processed_social_feed_ids:
                        continue
                    source = hit["_source"]
                    feed_data = source.get("feedData", {})
                    feeds.append({
                        "es_id": es_id,
                        "SOCIALFEEDID": source.get("socialFeedId"),
                        "HEADLINE": feed_data.get("headlines", ""),
                        "SUMMARY": feed_data.get("summary", ""),
                        "CONTENT": feed_data.get("text", ""),
                        "LANGUAGE": feed_data.get("language", "en"),
                        "FEEDDATE": feed_data.get("feedDate")
                    })
                return feeds

            # Fetch first page
            response = await loop.run_in_executor(None, _es_search, _build_body(pit_id, None))
            pit_id = response.get("pit_id", pit_id)
            total_hits = response.get("hits", {}).get("total", {}).get("value", 0)
            logger.info(f"Total social feeds in date range: {total_hits:,}")

            while True:
                hits = response.get("hits", {}).get("hits", [])
                if not hits:
                    break
                search_after = hits[-1].get("sort")

                batch_social_feeds = _hits_to_feeds(hits)
                logger.info(f"[social]   page {batch_count+1} — fetched {len(hits)} docs, {len(batch_social_feeds)} new  (processed so far: {results['processed']:,}/{total_hits:,})")

                # Kick off next-page fetch immediately (runs in thread pool, non-blocking)
                next_fetch = loop.run_in_executor(None, _es_search, _build_body(pit_id, search_after))

                # Tag current batch while next page is being fetched
                if batch_social_feeds:
                    batch_results = await self._process_social_feeds_with_percolator(batch_social_feeds, company_ids, company_stats)
                    results["processed"] += batch_results["processed"]
                    results["tags_created"] += batch_results["tags_created"]
                    results["errors"].extend(batch_results["errors"])
                    for social_feed in batch_social_feeds:
                        self.processed_social_feed_ids.add(social_feed["es_id"])

                # Wait for next page (likely already done)
                response = await next_fetch
                pit_id = response.get("pit_id", pit_id)

                batch_count += 1
                elapsed = max(time.time() - progress_started_at, 1e-6)
                rate = results["processed"] / elapsed
                percent = (results["processed"] / total_hits * 100.0) if total_hits else 0.0
                logger.info(f"[social]   page {batch_count} done — {results['processed']:,}/{total_hits:,} ({percent:.1f}%) | {rate:.1f} docs/sec | tags: {results['tags_created']:,}")
                if self.config.enable_checkpoints and (batch_count % 20 == 0 or results["processed"] % 20000 == 0):
                    self._save_checkpoint()

            try:
                self.espreview_engine.es_client.close_point_in_time(body={"id": pit_id})
            except Exception:
                pass

            logger.info(f"Social feed scan done: {results['processed']:,} scanned, {results['tags_created']:,} matched")
            results["social_feeds_processed"] = results["processed"]

        except Exception as e:
            logger.error(f"Error in streaming social feed processing: {e}")
            results["errors"].append(str(e))

        return results
    
    async def _process_social_feeds_with_percolator(self, social_feeds: List[Dict[str, Any]], company_ids: List[str], company_stats: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
        """Process all social feeds using batched msearch percolation.

        Splits the page into msearch sub-batches (config.msearch_batch_size) and
        runs them concurrently in the thread pool — one ES round trip per sub-batch
        instead of one per document.
        """

        results = {
            "processed": 0,
            "tags_created": 0,
            "errors": []
        }

        if not social_feeds:
            return results

        requested_company_ids = set(company_ids)
        msearch_batch = max(1, getattr(self.config, "msearch_batch_size", 100))
        loop = asyncio.get_running_loop()

        # Split page into sub-batches, run each as one msearch in the thread pool
        sub_batches = [social_feeds[i:i + msearch_batch] for i in range(0, len(social_feeds), msearch_batch)]
        sub_futures = [
            loop.run_in_executor(
                self.tag_executor,
                self._tag_articles_batch_msearch,
                batch,
                list(company_ids),
                False,  # is_article=False (social feeds)
            )
            for batch in sub_batches
        ]
        sub_results = await asyncio.gather(*sub_futures, return_exceptions=True)

        for batch_idx, batch_result in enumerate(sub_results):
            batch = sub_batches[batch_idx]
            if isinstance(batch_result, Exception):
                results["errors"].append(f"msearch batch {batch_idx} error: {batch_result}")
                results["processed"] += len(batch)
                continue

            for item_idx, social_feed in enumerate(batch):
                results["processed"] += 1
                item_tag_results = batch_result[item_idx] if item_idx < len(batch_result) else []

                matched_company_ids = set()
                for tag_data in item_tag_results:
                    company_id = tag_data.get("COMPANYID")
                    if company_id not in requested_company_ids:
                        continue
                    matched_company_ids.add(company_id)
                    tag_result = self._create_social_feed_tag_from_tagger(social_feed, tag_data)
                    if tag_result:
                        results["tags_created"] += 1
                        company_stats[company_id]["tags_created"] += 1
                        self._buffer_tag_result(tag_result)

                for company_id in matched_company_ids:
                    company_stats[company_id]["social_matched"] += 1

        return results
    
    async def _process_single_social_feed(self, social_feed: Dict[str, Any], company_ids: List[str], company_stats: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
        """Process a single social feed once and fan out tags to requested companies."""
        try:
            requested_company_ids = set(company_ids)
            tag_results = []
            try:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.get_event_loop()

                tag_results = await loop.run_in_executor(
                    self.tag_executor,
                    self._tag_article_for_companies,
                    str(social_feed['es_id']),
                    social_feed.get('HEADLINE', ''),
                    social_feed.get('SUMMARY', ''),
                    social_feed.get('CONTENT', ''),
                    social_feed.get('LANGUAGE', 'en'),
                    requested_company_ids,
                )
            except Exception as e:
                return {"processed": True, "tags_created": 0, "error": f"Error calling tag_article: {e}"}

            tags_created = 0
            matched_company_ids = set()
            for tag_data in tag_results:
                company_id = tag_data.get("COMPANYID")
                if company_id not in requested_company_ids:
                    continue
                matched_company_ids.add(company_id)
                tag_result = self._create_social_feed_tag_from_tagger(social_feed, tag_data)
                if tag_result:
                    tags_created += 1
                    company_stats[company_id]["tags_created"] += 1
                    self._buffer_tag_result(tag_result)

            for company_id in matched_company_ids:
                company_stats[company_id]["social_matched"] += 1

            return {"processed": True, "tags_created": tags_created, "error": None}
            
        except Exception as e:
            return {"processed": True, "tags_created": 0, "error": f"Social feed {social_feed.get('SOCIALFEEDID', 'unknown')}: {str(e)}"}
    
    def _test_percolator_match(self, document: Dict[str, Any], company_query: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Test if a document matches the specific percolator query."""
        
        try:
            # Instead of using percolate, directly test the query against the document
            # This is more accurate and matches exactly what the main tagger does
            
            # Create a test index document with the same structure as the main tagger
            test_doc = {
                "content": document["content"],
                "content_case_sensitive": document["content_case_sensitive"]
            }
            
            # Test the specific company query against the document
            search_query = {
                "query": company_query,  # Use the specific company's query
                "size": 1
            }
            
            # We need to test this against a temporary document
            # Since we can't easily create a temporary index, we'll use a different approach
            # Let's check if the document content contains the required phrases
            
            content = document["content"].lower()
            
            # Extract required phrases from the company query
            required_phrases = self._extract_required_phrases(company_query)
            
            # Check if all required phrase groups are satisfied and collect matched phrases
            all_matched_phrases = []
            
            for phrase_group in required_phrases:
                matches, matched_phrases = self._check_phrase_group(content, phrase_group)
                if not matches:
                    return False, []
                all_matched_phrases.extend(matched_phrases)
            
            # Return unique matched phrases
            unique_phrases = list(set(all_matched_phrases))
            return True, unique_phrases
            
        except Exception as e:
            print(f"Error testing percolator match: {e}")
            return False, []
    
    def _extract_required_phrases(self, query: Dict[str, Any]) -> List[List[str]]:
        """Extract required phrases from the percolator query."""
        
        def extract_phrases_from_clause(clause):
            """Recursively extract phrases from any clause type."""
            phrases = []
            
            if "match_phrase" in clause:
                phrase = clause["match_phrase"]["content"]["query"]
                phrases.append(phrase.lower())
            elif "bool" in clause:
                bool_clause = clause["bool"]
                
                # Handle must clauses (AND logic)
                if "must" in bool_clause:
                    and_phrases = []
                    for must_item in bool_clause["must"]:
                        item_phrases = extract_phrases_from_clause(must_item)
                        and_phrases.extend(item_phrases)
                    if and_phrases:
                        phrases.append(" AND ".join(and_phrases))
                
                # Handle should clauses (OR logic)
                elif "should" in bool_clause:
                    or_phrases = []
                    for should_item in bool_clause["should"]:
                        item_phrases = extract_phrases_from_clause(should_item)
                        or_phrases.extend(item_phrases)
                    if or_phrases:
                        phrases.extend(or_phrases)
            
            return phrases
        
        required_groups = []
        
        if "bool" in query:
            bool_query = query["bool"]
            
            # Handle must clauses (AND logic) - like CYBERPE865
            if "must" in bool_query:
                for must_clause in bool_query["must"]:
                    phrases = extract_phrases_from_clause(must_clause)
                    if phrases:
                        required_groups.append(phrases)
            
            # Handle should clauses (OR logic) - like INDIA124
            elif "should" in bool_query:
                phrases = extract_phrases_from_clause(query)
                if phrases:
                    required_groups.append(phrases)
        
        return required_groups
    
    def _check_phrase_group(self, content: str, phrase_group: List[str]) -> Tuple[bool, List[str]]:
        """Check if content matches at least one phrase in the group and return matched phrases."""
        
        matched_phrases = []
        content_lower = content.lower()
        
        for phrase in phrase_group:
            if " AND " in phrase:
                # This is an AND phrase - all parts must be present
                parts = phrase.split(" AND ")
                if all(part.strip().lower() in content_lower for part in parts):
                    matched_phrases.append(phrase)
                    return True, matched_phrases
            else:
                # This is a simple phrase
                if phrase.lower() in content_lower:
                    matched_phrases.append(phrase)
                    return True, matched_phrases
        
        return False, matched_phrases

    def _buffer_tag_result(self, tag_result: Optional[Dict[str, Any]]) -> int:
        """Buffer tag results and flush in bulk. Returns count flushed (tags created/updated)."""
        if not tag_result:
            return 0
        self.mongo_batch_buffer.append(tag_result)
        if len(self.mongo_batch_buffer) >= max(1, self.config.mongo_bulk_batch_size):
            return self._flush_mongo_batch()
        return 0

    def _flush_mongo_batch(self) -> int:
        """Flush buffered Mongo tag operations in one bulk call."""
        if not self.mongo_batch_buffer:
            return 0
        batch = self.mongo_batch_buffer
        self.mongo_batch_buffer = []
        ok = self.mongo_creator.save_tags_to_mongo(batch, update_type=1)
        if not ok:
            return 0
        return len(batch)
    
    def _create_article_tag_from_tagger(self, article: Dict[str, Any], tag_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build MongoDB tag payload for an article using main tagger results."""

        try:
            company_id = tag_data.get('COMPANYID')
            keywords_text = tag_data.get('KEYWORDS', '')
            sources = tag_data.get('SOURCES', {})
            pg_articleid = article['articleid']

            # Cache check — avoids a MongoDB find_one per company when one article
            # matches multiple companies in the same job.
            if pg_articleid in self._article_id_cache:
                mongo_article_id = self._article_id_cache[pg_articleid]
            else:
                mongo_article_id = self.mongo_creator.ensure_article_exists_in_mongo(
                    pg_articleid=pg_articleid,
                    headline=article.get('headlines', ''),
                    summary=article.get('summary', ''),
                    content=article.get('content', ''),
                    language=article.get('articlelang', 'en'),
                    article_date=article.get('articledate')
                )
                self._article_id_cache[pg_articleid] = mongo_article_id

            if not mongo_article_id:
                logger.debug(f"Failed to ensure article {pg_articleid} exists in MongoDB")
                return None

            tag_doc = self.mongo_creator.create_article_tag(
                article_id=mongo_article_id,
                pg_article_id=pg_articleid,
                company_id=company_id,
                article_date=article.get('articledate'),
                tag_data={"KEYWORDS": keywords_text, "COMPANYID": company_id, "SOURCES": sources},
                sources=sources,
                content=article.get('content', ''),
                is_new=True
            )

            logger.debug(f"Article tag queued: {tag_doc['_id']} article={article['es_id']} kw={keywords_text}")

            return {
                "tag_id": tag_doc["_id"],
                "tag_doc": tag_doc,
                "article_id": mongo_article_id,
                "company_id": company_id,
                "company_name": tag_doc["company"]["name"],
                "is_article": True
            }

        except Exception as e:
            logger.warning(f"Error creating article tag: {e}")
            return None
    
    def _create_social_feed_tag_from_tagger(self, social_feed: Dict[str, Any], tag_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build MongoDB tag payload for a social feed using main tagger results."""

        try:
            company_id = tag_data.get('COMPANYID')
            keywords_text = tag_data.get('KEYWORDS', '')
            sources = tag_data.get('SOURCES', {})
            pg_socialfeedid = social_feed['SOCIALFEEDID']

            # Cache check — avoids repeated MongoDB find_one when the same feed
            # matches multiple companies in the same job.
            if pg_socialfeedid in self._social_feed_id_cache:
                mongo_social_feed_id = self._social_feed_id_cache[pg_socialfeedid]
            else:
                mongo_social_feed_id = self.mongo_creator.ensure_social_feed_exists_in_mongo(
                    pg_socialfeedid=pg_socialfeedid,
                    headline=social_feed.get('HEADLINE', ''),
                    summary=social_feed.get('SUMMARY', ''),
                    content=social_feed.get('CONTENT', ''),
                    language=social_feed.get('LANGUAGE', 'en'),
                    feed_date=social_feed.get('FEEDDATE')
                )
                self._social_feed_id_cache[pg_socialfeedid] = mongo_social_feed_id

            if not mongo_social_feed_id:
                logger.debug(f"Failed to ensure social feed {pg_socialfeedid} exists in MongoDB")
                return None

            tag_doc = self.mongo_creator.create_social_tag(
                social_feed_id=mongo_social_feed_id,
                pg_social_feed_id=pg_socialfeedid,
                company_id=company_id,
                feed_date=social_feed.get('FEEDDATE'),
                tag_data={"KEYWORDS": keywords_text, "COMPANYID": company_id, "SOURCES": sources},
                sources=sources,
                content=social_feed.get('CONTENT', ''),
                is_new=True
            )

            logger.debug(f"Social feed tag queued: {tag_doc['_id']} feed={social_feed['es_id']} kw={keywords_text}")

            return {
                "tag_id": tag_doc["_id"],
                "tag_doc": tag_doc,
                "social_feed_id": mongo_social_feed_id,
                "company_id": company_id,
                "company_name": tag_doc["company"]["name"],
                "is_article": False
            }

        except Exception as e:
            logger.warning(f"Error creating social feed tag: {e}")
            return None
    
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
    
    def _remap_query_for_article_index(self, query):
        """Remap a percolator query (uses 'content'/'content_case_sensitive' fields)
        to work against printarticleindex fields: articleData.headlines, articleData.text, articleData.summary.
        Recursively walks the query tree and replaces match_phrase on content fields."""
        ARTICLE_FIELDS = ["articleData.headlines", "articleData.text", "articleData.summary"]
        if isinstance(query, dict):
            if "match_phrase" in query:
                phrase_q = query["match_phrase"]
                for src_field in ("content", "content_case_sensitive"):
                    if src_field in phrase_q:
                        val = phrase_q[src_field]
                        query_text = val.get("query", val) if isinstance(val, dict) else val
                        slop = val.get("slop", 0) if isinstance(val, dict) else 0
                        should_clauses = [
                            {"match_phrase": {f: {"query": query_text, "slop": slop} if slop else query_text}}
                            for f in ARTICLE_FIELDS
                        ]
                        return {"bool": {"should": should_clauses, "minimum_should_match": 1}}
                return query  # match_phrase on unknown field — leave as-is
            return {k: self._remap_query_for_article_index(v) for k, v in query.items()}
        elif isinstance(query, list):
            return [self._remap_query_for_article_index(item) for item in query]
        return query

    def _remap_query_for_social_index(self, query):
        """Remap a percolator query (uses 'content'/'content_case_sensitive' fields)
        to work against socialfeedindex fields: feedData.headlines, feedData.text, feedData.summary.
        Recursively walks the query tree and replaces match_phrase on content fields."""
        SOCIAL_FIELDS = ["feedData.headlines", "feedData.text", "feedData.summary"]
        if isinstance(query, dict):
            if "match_phrase" in query:
                phrase_q = query["match_phrase"]
                for src_field in ("content", "content_case_sensitive"):
                    if src_field in phrase_q:
                        val = phrase_q[src_field]
                        query_text = val.get("query", val) if isinstance(val, dict) else val
                        slop = val.get("slop", 0) if isinstance(val, dict) else 0
                        should_clauses = [
                            {"match_phrase": {f: {"query": query_text, "slop": slop} if slop else query_text}}
                            for f in SOCIAL_FIELDS
                        ]
                        return {"bool": {"should": should_clauses, "minimum_should_match": 1}}
                return query  # match_phrase on unknown field — leave as-is
            return {k: self._remap_query_for_social_index(v) for k, v in query.items()}
        elif isinstance(query, list):
            return [self._remap_query_for_social_index(item) for item in query]
        return query

    def _load_checkpoint(self):
        """Load checkpoint data if it exists."""
        if not self.config.enable_checkpoints:
            return
        
        if self.config.use_mongo_checkpoints:
            # Load from MongoDB
            if self.mongo_checkpoint_manager:
                self.checkpoint_data = self.mongo_checkpoint_manager.load_checkpoint()
                if self.checkpoint_data:
                    print(f"Loaded checkpoint from MongoDB: {self.config.checkpoint_id}")
                    print(f"  Last checkpoint: {self.checkpoint_data.get('checkpoint_time', 'unknown')}")
                    print(f"  Processed companies: {len(self.checkpoint_data.get('processed_companies', []))}")
                    print(f"  Processed articles: {len(self.checkpoint_data.get('processed_article_ids', []))}")
                    print(f"  Processed social feeds: {len(self.checkpoint_data.get('processed_social_feed_ids', []))}")
                    # Restore processed sets
                    self.processed_article_ids = set(self.checkpoint_data.get('processed_article_ids', []))
                    self.processed_social_feed_ids = set(self.checkpoint_data.get('processed_social_feed_ids', []))
                    self.processed_companies = set(self.checkpoint_data.get('processed_companies', []))
        else:
            # Load from filesystem
            if not self.config.checkpoint_file:
                return
            
            checkpoint_path = Path(self.config.checkpoint_file)
            if checkpoint_path.exists():
                try:
                    with open(checkpoint_path, 'r') as f:
                        self.checkpoint_data = json.load(f)
                    print(f"Loaded checkpoint from: {checkpoint_path}")
                    print(f"  Last checkpoint: {self.checkpoint_data.get('checkpoint_time', 'unknown')}")
                    # Restore processed sets
                    self.processed_article_ids = set(self.checkpoint_data.get('processed_article_ids', []))
                    self.processed_social_feed_ids = set(self.checkpoint_data.get('processed_social_feed_ids', []))
                    self.processed_companies = set(self.checkpoint_data.get('processed_companies', []))
                except Exception as e:
                    print(f"Warning: Could not load checkpoint: {e}")
                    self.checkpoint_data = None
    
    def _save_checkpoint(self):
        """Save current state to checkpoint (MongoDB or filesystem)."""
        if not self.config.enable_checkpoints:
            return
        
        try:
            checkpoint_data = {
                "config": {
                    "start_date": self.config.start_date,
                    "end_date": self.config.end_date,
                    "company_ids": self.config.company_ids
                },
                "processed_companies": list(self.processed_companies),
                "processed_article_ids": list(self.processed_article_ids),
                "processed_social_feed_ids": list(self.processed_social_feed_ids),
                "total_articles_processed": self.results.get("total_articles_processed", 0),
                "total_social_feeds_processed": self.results.get("total_social_feeds_processed", 0),
                "total_tags_created": self.results.get("total_tags_created", 0),
                "company_results": self.results.get("company_results", {}),
                "checkpoint_time": datetime.now().isoformat(),
                "errors": self.results.get("errors", [])
            }
            
            if self.config.use_mongo_checkpoints:
                # Save to MongoDB
                if self.mongo_checkpoint_manager:
                    self.mongo_checkpoint_manager.save_checkpoint(checkpoint_data)
                    if self.config.verbose:
                        print(f"Checkpoint saved to MongoDB: {self.config.checkpoint_id}")
            else:
                # Save to filesystem
                if not self.config.checkpoint_file:
                    return
                
                checkpoint_path = Path(self.config.checkpoint_file)
                with open(checkpoint_path, 'w') as f:
                    json.dump(checkpoint_data, f, indent=2, default=str)
                
                if self.config.verbose:
                    print(f"Checkpoint saved to: {checkpoint_path}")
        except Exception as e:
            print(f"Error saving checkpoint: {e}")
    
    def _clear_checkpoint(self):
        """Clear checkpoint after successful completion."""
        if not self.config.enable_checkpoints:
            return
        
        try:
            if self.config.use_mongo_checkpoints:
                if self.mongo_checkpoint_manager:
                    self.mongo_checkpoint_manager.clear_checkpoint()
            else:
                if self.config.checkpoint_file:
                    checkpoint_path = Path(self.config.checkpoint_file)
                    if checkpoint_path.exists():
                        checkpoint_path.unlink()
                        print(f"Checkpoint cleared: {checkpoint_path}")
        except Exception as e:
            print(f"Error clearing checkpoint: {e}")
    
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
    """Main entry point for percolator backtracking."""
    
    # Configuration - Customize this for your needs
    config = BacktrackingConfig(
        # Date range (last 2 days)
        start_date=(datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
        end_date=datetime.now().strftime("%Y-%m-%d"),
        
        # Company IDs to process
        company_ids=[
            "INDUSTRYA",
        ],
        
        # Processing settings
        batch_size=100,
        max_workers=4,
        parallel_processing=True,
        
        # Output settings
        dry_run=False,  # Set to True for testing
        verbose=True,
        save_results=True,
        results_file="percolator_backtracking_results.json"
    )
    
    # Run percolator backtracking
    engine = PercolatorBacktrackingEngine(config)
    results = engine.run_percolator_backtracking()
    
    return results

if __name__ == "__main__":
    main()
