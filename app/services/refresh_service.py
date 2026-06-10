"""
Core refresh service for handling Elasticsearch document refresh operations.
"""

import asyncio
import logging
import time
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import lru_cache
from threading import Lock

from pymongo import MongoClient
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

from app.config import settings
from app.models.schemas import RefreshResult, BatchRefreshResults
from app.utils.database import get_mongo_client, get_elasticsearch_client, reset_mongo_client
from app.utils.logger import get_logger

logger = get_logger(__name__)


class MasterDataCache:
    """In-memory cache for master data lookups (publications, companies, etc.)"""
    
    def __init__(self):
        self._publications: Dict[str, Dict[str, Any]] = {}
        self._companies: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()
        self._cache_loaded = False
    
    async def load_all_master_data(self, mongo_client: MongoClient, db_name: str):
        """Pre-load all master data into cache."""
        if self._cache_loaded:
            return
        
        with self._lock:
            if self._cache_loaded:
                return
            
            try:
                db = mongo_client[db_name]
                
                # Try actual master collection names used in the codebase
                # For publications: publicationMaster (print) and publicationMasterOnline (social/online)
                publication_collections = [
                    "publicationMaster", 
                    "publicationMasterOnline",
                    "publication", 
                    "publications", 
                    "Publication"
                ]
                for coll_name in publication_collections:
                    try:
                        if coll_name in db.list_collection_names():
                            # Try different field structures based on collection
                            if "Master" in coll_name:
                                # Master collections have nested structure: publicationInfo.publicationId
                                publications_cursor = await asyncio.to_thread(
                                    db[coll_name].find, 
                                    {}, 
                                    {"_id": 0, "publicationInfo.publicationId": 1, "publicationInfo.publicationName": 1, "publicationInfo.pubGroupId": 1, "publicationInfo.pubGroupName": 1}
                                )
                                for pub in await asyncio.to_thread(list, publications_cursor):
                                    pub_info = pub.get("publicationInfo", {})
                                    pub_id = str(pub_info.get("publicationId", ""))
                                    if pub_id:
                                        self._publications[pub_id] = {
                                            "publicationId": pub_id,
                                            "publicationName": pub_info.get("publicationName", ""),
                                            "pubGroupId": pub_info.get("pubGroupId", ""),
                                            "pubGroupName": pub_info.get("pubGroupName", "")
                                        }
                            else:
                                # Regular collections: direct fields
                                publications_cursor = await asyncio.to_thread(
                                    db[coll_name].find, 
                                    {}, 
                                    {"_id": 0, "publicationId": 1, "publicationName": 1, "pubGroupId": 1, "pubGroupName": 1}
                                )
                                for pub in await asyncio.to_thread(list, publications_cursor):
                                    pub_id = str(pub.get("publicationId", ""))
                                    if pub_id:
                                        self._publications[pub_id] = pub
                            
                            if len(self._publications) > 0:
                                logger.info(f"Loaded {len(self._publications)} publications from {coll_name}")
                                break
                    except Exception as e:
                        logger.debug(f"Could not load from {coll_name}: {e}")
                        continue
                
                # Try actual master collection names for companies
                # companyMaster uses _id as companyId
                company_collections = [
                    "companyMaster",
                    "company", 
                    "companies", 
                    "Company"
                ]
                for coll_name in company_collections:
                    try:
                        if coll_name in db.list_collection_names():
                            if coll_name == "companyMaster":
                                # companyMaster uses _id as companyId
                                companies_cursor = await asyncio.to_thread(
                                    db[coll_name].find, 
                                    {}, 
                                    {"_id": 1, "companyInfo.companyName": 1, "companyInfo.shortName": 1}
                                )
                                for company in await asyncio.to_thread(list, companies_cursor):
                                    company_id = str(company.get("_id", ""))
                                    company_info = company.get("companyInfo", {})
                                    if company_id:
                                        self._companies[company_id] = {
                                            "companyId": company_id,
                                            "companyName": company_info.get("companyName", ""),
                                            "shortName": company_info.get("shortName", "")
                                        }
                            else:
                                # Regular collections: direct fields
                                companies_cursor = await asyncio.to_thread(
                                    db[coll_name].find, 
                                    {}, 
                                    {"_id": 0, "companyId": 1, "companyName": 1}
                                )
                                for company in await asyncio.to_thread(list, companies_cursor):
                                    company_id = str(company.get("companyId", ""))
                                    if company_id:
                                        self._companies[company_id] = company
                            
                            if len(self._companies) > 0:
                                logger.info(f"Loaded {len(self._companies)} companies from {coll_name}")
                                break
                    except Exception as e:
                        logger.debug(f"Could not load from {coll_name}: {e}")
                        continue
                
                self._cache_loaded = True
                logger.info(f"Master cache loaded: {len(self._publications)} publications and {len(self._companies)} companies")
            except Exception as e:
                logger.error(f"Error loading master data cache: {e}")
                import traceback
                logger.debug(traceback.format_exc())
                # Continue without cache if loading fails - it's not critical
                self._cache_loaded = True  # Mark as loaded to prevent retries
    
    def get_publication(self, pub_id: str) -> Optional[Dict[str, Any]]:
        """Get publication from cache."""
        return self._publications.get(str(pub_id))
    
    def get_company(self, company_id: str) -> Optional[Dict[str, Any]]:
        """Get company from cache."""
        return self._companies.get(str(company_id))
    
    def clear(self):
        """Clear the cache."""
        with self._lock:
            self._publications.clear()
            self._companies.clear()
            self._cache_loaded = False


def _clamp_max_workers(requested: Optional[int]) -> int:
    """Ensure worker counts stay within configured min/max bounds."""
    max_workers = settings.MAX_WORKERS
    min_workers = max(1, min(settings.MIN_BATCH_WORKERS, max_workers))
    candidate = requested or max_workers
    if candidate < min_workers:
        candidate = min_workers
    return min(candidate, max_workers)


class RefreshService:
    """Service for refreshing documents in Elasticsearch."""
    
    def __init__(self):
        self.mongo_client: Optional[MongoClient] = None
        self.es_client: Optional[Elasticsearch] = None
        self.executor: Optional[ThreadPoolExecutor] = None
        self._initialized = False
        self.master_cache = MasterDataCache()  # Master data cache
        
    async def initialize(self):
        """Initialize the service with database connections."""
        if self._initialized:
            return
            
        try:
            # Initialize database connections
            self.mongo_client = await get_mongo_client()
            self.es_client = await get_elasticsearch_client()
            
            # Initialize thread pool
            self.executor = ThreadPoolExecutor(
                max_workers=settings.MAX_WORKERS,
                thread_name_prefix="refresh_worker"
            )
            
            # Pre-warm connections by doing a ping
            try:
                self.mongo_client.admin.command('ping')
                self.es_client.ping()
            except Exception as e:
                logger.warning(f"Connection pre-warm failed (non-critical): {e}")
            
            # Pre-load master data cache in background (non-blocking)
            asyncio.create_task(
                self.master_cache.load_all_master_data(self.mongo_client, settings.MONGODB_DATABASE)
            )
            
            self._initialized = True
            logger.info("RefreshService initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize RefreshService: {e}")
            raise
    
    async def health_check(self) -> bool:
        """Check if the service is healthy."""
        try:
            if not self._initialized:
                await self.initialize()
            
            # Check MongoDB connection
            if self.mongo_client:
                self.mongo_client.admin.command('ping')
            
            # Check Elasticsearch connection
            if self.es_client:
                self.es_client.ping()
            
            return True
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    async def _get_child_article_ids(self, article_id: int) -> List[int]:
        """Get child article IDs for a parent article."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection_article_similar = db.articleSimilar
            
            from bson.int64 import Int64
            
            # Try multiple ID formats
            article_similar = await asyncio.to_thread(
                collection_article_similar.find_one, {"parentArticleId": article_id}
            )
            if not article_similar:
                article_similar = await asyncio.to_thread(
                    collection_article_similar.find_one, {"parentArticleId": Int64(article_id)}
                )
            if not article_similar:
                article_similar = await asyncio.to_thread(
                    collection_article_similar.find_one, {"parentArticleId": str(article_id)}
                )
            
            child_ids = []
            if article_similar and article_similar.get('child'):
                for child in article_similar.get("child", []):
                    child_article_id = child.get("articleId")
                    if child_article_id:
                        try:
                            child_ids.append(int(child_article_id))
                        except (ValueError, TypeError):
                            child_ids.append(child_article_id)
            
            return child_ids
        except Exception as e:
            logger.error(f"Error getting child article IDs for {article_id}: {e}")
            return []
    
    async def _get_child_social_feed_ids(self, social_feed_id: int) -> List[int]:
        """Get child social feed IDs for a parent social feed."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection_social_feed_similar = db.socialFeedSimilar
            
            from bson.int64 import Int64
            
            # Try multiple ID formats
            social_feed_similar = await asyncio.to_thread(
                collection_social_feed_similar.find_one, {"parentSocialFeedId": social_feed_id}
            )
            if not social_feed_similar:
                social_feed_similar = await asyncio.to_thread(
                    collection_social_feed_similar.find_one, {"parentSocialFeedId": Int64(social_feed_id)}
                )
            if not social_feed_similar:
                social_feed_similar = await asyncio.to_thread(
                    collection_social_feed_similar.find_one, {"parentSocialFeedId": str(social_feed_id)}
                )
            
            child_ids = []
            if social_feed_similar and social_feed_similar.get('child'):
                for child in social_feed_similar.get("child", []):
                    child_social_feed_id = child.get("socialFeedId")
                    if child_social_feed_id:
                        try:
                            child_ids.append(int(child_social_feed_id))
                        except (ValueError, TypeError):
                            child_ids.append(child_social_feed_id)
            
            return child_ids
        except Exception as e:
            logger.error(f"Error getting child social feed IDs for {social_feed_id}: {e}")
            return []
    
    async def refresh_social_feed(self, social_feed_id: int, refreshed_ids: Optional[set] = None) -> RefreshResult:
        """Refresh a single social feed document and its children.
        
        Args:
            social_feed_id: The social feed ID to refresh
            refreshed_ids: Set of IDs already refreshed (to avoid infinite loops)
        """
        if refreshed_ids is None:
            refreshed_ids = set()
        
        # Avoid infinite loops
        if social_feed_id in refreshed_ids:
            return RefreshResult(
                document_id=str(social_feed_id),
                document_type="social",
                success=False,
                message=f"Social feed {social_feed_id} already being refreshed (circular reference)",
                processing_time=0.0,
                timestamp=datetime.utcnow()
            )
        
        refreshed_ids.add(social_feed_id)
        start_time = time.time()
        
        try:
            if not self._initialized:
                await self.initialize()
            
            # Get social feed document from MongoDB
            social_doc = await self._get_social_feed_document(social_feed_id)
            if not social_doc:
                return RefreshResult(
                    document_id=str(social_feed_id),
                    document_type="social",
                    success=False,
                    message=f"Social feed {social_feed_id} not found in MongoDB",
                    processing_time=time.time() - start_time,
                    timestamp=datetime.utcnow()
                )
            
            # Build enriched payload
            payload = await self._build_social_es_payload(social_doc)
            if not payload:
                return RefreshResult(
                    document_id=str(social_feed_id),
                    document_type="social",
                    success=False,
                    message=f"Failed to build payload for social feed {social_feed_id}",
                    processing_time=time.time() - start_time,
                    timestamp=datetime.utcnow()
                )
            
            # Index to Elasticsearch
            await self._index_to_elasticsearch(settings.ES_REFRESH_SOCIAL_INDEX, str(social_feed_id), payload)
            
            # Refresh child social feeds in parallel
            child_ids = await self._get_child_social_feed_ids(social_feed_id)
            child_results = []
            if child_ids:
                logger.info(f"Refreshing {len(child_ids)} child social feeds for parent {social_feed_id}")
                # Refresh all children in parallel
                child_tasks = [
                    self.refresh_social_feed(child_id, refreshed_ids.copy())
                    for child_id in child_ids
                ]
                child_results = await asyncio.gather(*child_tasks, return_exceptions=True)
                # Convert exceptions to failed results
                processed_results = []
                for i, result in enumerate(child_results):
                    if isinstance(result, Exception):
                        processed_results.append(RefreshResult(
                            document_id=str(child_ids[i]),
                            document_type="social",
                            success=False,
                            message=f"Exception: {str(result)}",
                            processing_time=0.0,
                            timestamp=datetime.utcnow()
                        ))
                    else:
                        processed_results.append(result)
                child_results = processed_results
            
            child_success_count = sum(1 for r in child_results if r.success)
            child_message = f" and {child_success_count}/{len(child_ids)} children" if child_ids else ""
            
            return RefreshResult(
                document_id=str(social_feed_id),
                document_type="social",
                success=True,
                message=f"Successfully refreshed social feed {social_feed_id}{child_message}",
                processing_time=time.time() - start_time,
                timestamp=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error refreshing social feed {social_feed_id}: {e}")
            return RefreshResult(
                document_id=str(social_feed_id),
                document_type="social",
                success=False,
                message=f"Error refreshing social feed {social_feed_id}: {str(e)}",
                processing_time=time.time() - start_time,
                timestamp=datetime.utcnow()
            )
    
    async def refresh_article(self, article_id: int, refreshed_ids: Optional[set] = None, refresh_children: bool = True) -> RefreshResult:
        """Refresh a single article document and its children.
        
        Args:
            article_id: The article ID to refresh
            refreshed_ids: Set of IDs already refreshed (to avoid infinite loops)
            refresh_children: Whether to recursively refresh children (default: True)
        """
        if refreshed_ids is None:
            refreshed_ids = set()
        
        # Avoid infinite loops
        if article_id in refreshed_ids:
            return RefreshResult(
                document_id=str(article_id),
                document_type="article",
                success=False,
                message=f"Article {article_id} already being refreshed (circular reference)",
                processing_time=0.0,
                timestamp=datetime.utcnow()
            )
        
        refreshed_ids.add(article_id)
        start_time = time.time()
        
        try:
            if not self._initialized:
                await self.initialize()
            
            # Get article document from MongoDB
            article_doc = await self._get_article_document(article_id)
            if not article_doc:
                return RefreshResult(
                    document_id=str(article_id),
                    document_type="article",
                    success=False,
                    message=f"Article {article_id} not found in MongoDB",
                    processing_time=time.time() - start_time,
                    timestamp=datetime.utcnow()
                )
            
            # Build enriched payload
            payload = await self._build_article_es_payload(article_doc)
            if not payload:
                return RefreshResult(
                    document_id=str(article_id),
                    document_type="article",
                    success=False,
                    message=f"Failed to build payload for article {article_id}",
                    processing_time=time.time() - start_time,
                    timestamp=datetime.utcnow()
                )
            
            # Start fetching child IDs in parallel while indexing parent to ES
            child_ids_task = asyncio.create_task(self._get_child_article_ids(article_id))
            
            # Index to Elasticsearch
            index_start = time.time()
            await self._index_to_elasticsearch(settings.ES_REFRESH_PRINT_INDEX, str(article_id), payload)
            index_time = time.time() - index_start
            logger.debug(f"Article {article_id}: ES indexing took {index_time:.3f}s")
            
            # Get child IDs (should be ready by now since we started it in parallel)
            child_ids = await child_ids_task
            
            # Refresh child articles using optimized bulk processing (only if refresh_children is True)
            child_results = []
            if child_ids and refresh_children:
                child_start_time = time.time()
                logger.info(f"Refreshing {len(child_ids)} child articles for parent {article_id} using bulk processing")
                # Use optimized batch processing for children (much faster than individual refreshes)
                # This bulk-fetches all data at once instead of individual queries per child
                if len(child_ids) >= 1:  # Use bulk processing for any number of children
                    batch_results = await self.refresh_articles_batch(child_ids, max_workers=settings.MAX_WORKERS)
                    child_results = batch_results.results
                else:
                    # Fallback to individual if somehow empty
                    child_results = []
                child_elapsed = time.time() - child_start_time
                logger.info(f"Completed refreshing {len(child_ids)} child articles in {child_elapsed:.2f}s (avg {child_elapsed/len(child_ids):.2f}s per child)")
                # Convert exceptions to failed results
                processed_results = []
                for i, result in enumerate(child_results):
                    if isinstance(result, Exception):
                        processed_results.append(RefreshResult(
                            document_id=str(child_ids[i]),
                            document_type="article",
                            success=False,
                            message=f"Exception: {str(result)}",
                            processing_time=0.0,
                            timestamp=datetime.utcnow()
                        ))
                    else:
                        processed_results.append(result)
                child_results = processed_results
            
            child_success_count = sum(1 for r in child_results if r.success)
            child_message = f" and {child_success_count}/{len(child_ids)} children" if child_ids else ""
            
            return RefreshResult(
                document_id=str(article_id),
                document_type="article",
                success=True,
                message=f"Successfully refreshed article {article_id}{child_message}",
                processing_time=time.time() - start_time,
                timestamp=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error refreshing article {article_id}: {e}")
            return RefreshResult(
                document_id=str(article_id),
                document_type="article",
                success=False,
                message=f"Error refreshing article {article_id}: {str(e)}",
                processing_time=time.time() - start_time,
                timestamp=datetime.utcnow()
            )
    
    async def refresh_social_feeds_batch(
        self, 
        social_feed_ids: List[int], 
        max_workers: Optional[int] = None
    ) -> BatchRefreshResults:
        """Refresh multiple social feed documents with optimized bulk processing."""
        if not social_feed_ids:
            return BatchRefreshResults(
                successful_count=0,
                failed_count=0,
                results=[]
            )
        
        max_workers = _clamp_max_workers(max_workers)
        
        # Use optimized bulk processing for ALL batches (even small ones are faster)
        # Lowered threshold from 50 to 10 for better performance on small batches
        if len(social_feed_ids) >= 10:
            return await self._process_large_social_batch(social_feed_ids, max_workers)
        else:
            # For very small batches (<10), still use optimized path but with minimal overhead
            # This ensures even tiny batches get bulk processing benefits
            return await self._process_large_social_batch(social_feed_ids, max_workers)
            
            successful_count = sum(1 for r in results if r.success)
            failed_count = len(results) - successful_count
            
            return BatchRefreshResults(
                successful_count=successful_count,
                failed_count=failed_count,
                results=results
            )
    
    async def refresh_articles_batch(
        self, 
        article_ids: List[int], 
        max_workers: Optional[int] = None
    ) -> BatchRefreshResults:
        """Refresh multiple article documents with optimized bulk processing."""
        if not article_ids:
            return BatchRefreshResults(
                successful_count=0,
                failed_count=0,
                results=[]
            )
        
        max_workers = _clamp_max_workers(max_workers)
        
        # Use optimized bulk processing for ALL batches (even small ones are faster)
        # Lowered threshold from 50 to 10 for better performance on small batches
        if len(article_ids) >= 10:
            return await self._process_large_article_batch(article_ids, max_workers)
        else:
            # For very small batches (<10), still use optimized path but with minimal overhead
            # This ensures even tiny batches get bulk processing benefits
            return await self._process_large_article_batch(article_ids, max_workers)
            
            successful_count = sum(1 for r in results if r.success)
            failed_count = len(results) - successful_count
            
            return BatchRefreshResults(
                successful_count=successful_count,
                failed_count=failed_count,
                results=results
            )
    
    async def _process_batch_social_feeds(
        self, 
        social_feed_ids: List[int], 
        max_workers: int
    ) -> List[RefreshResult]:
        """Process a batch of social feed IDs."""
        if not self._initialized:
            await self.initialize()

        semaphore = asyncio.Semaphore(max_workers or settings.MAX_WORKERS)

        async def worker(sf_id: int):
            async with semaphore:
                return await self.refresh_social_feed(sf_id)

        tasks = [asyncio.create_task(worker(sf_id)) for sf_id in social_feed_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convert exceptions to failed results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append(RefreshResult(
                    document_id=str(social_feed_ids[i]),
                    document_type="social",
                    success=False,
                    message=f"Exception: {str(result)}",
                    processing_time=0.0,
                    timestamp=datetime.utcnow()
                ))
            else:
                processed_results.append(result)
        
        return processed_results
    
    async def _process_batch_articles(
        self, 
        article_ids: List[int], 
        max_workers: int
    ) -> List[RefreshResult]:
        """Process a batch of article IDs."""
        if not self._initialized:
            await self.initialize()

        semaphore = asyncio.Semaphore(max_workers or settings.MAX_WORKERS)

        async def worker(article_id: int):
            async with semaphore:
                return await self.refresh_article(article_id)

        tasks = [asyncio.create_task(worker(article_id)) for article_id in article_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convert exceptions to failed results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append(RefreshResult(
                    document_id=str(article_ids[i]),
                    document_type="article",
                    success=False,
                    message=f"Exception: {str(result)}",
                    processing_time=0.0,
                    timestamp=datetime.utcnow()
                ))
            else:
                processed_results.append(result)
        
        return processed_results
    
    def _sync_refresh_social_feed(self, social_feed_id: int) -> RefreshResult:
        """Synchronous wrapper for social feed refresh."""
        return asyncio.run(self.refresh_social_feed(social_feed_id))
    
    def _sync_refresh_article(self, article_id: int) -> RefreshResult:
        """Synchronous wrapper for article refresh."""
        return asyncio.run(self.refresh_article(article_id))
    
    async def _get_social_feed_document(self, social_feed_id: int) -> Optional[Dict[str, Any]]:
        """Get social feed document from MongoDB."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.socialFeed  # Correct collection name from dedicatedOnlineEsSync.py
            
            # Try both int and long formats for social feed ID
            # MongoDB stores large numbers as Long, so we need to handle both
            from bson.int64 import Int64
            
            # First try as int
            doc = await asyncio.to_thread(collection.find_one, {"socialFeedId": social_feed_id})
            if doc:
                return doc
            
            # If not found, try as Int64 (Long)
            doc = await asyncio.to_thread(collection.find_one, {"socialFeedId": Int64(social_feed_id)})
            if doc:
                return doc
                
            # If still not found, try as string (some systems store as string)
            doc = await asyncio.to_thread(collection.find_one, {"socialFeedId": str(social_feed_id)})
            return doc
            
        except Exception as e:
            error_str = str(e)
            # Check if it's a timeout error
            if "timed out" in error_str or "timeout" in error_str.lower():
                logger.warning(f"MongoDB timeout detected while fetching social feed. Attempting to reset connection...")
                try:
                    await reset_mongo_client()
                    self.mongo_client = await get_mongo_client()
                    logger.info("MongoDB client reset successfully.")
                except Exception as reset_error:
                    logger.error(f"Failed to reset MongoDB client: {reset_error}")
            
            logger.error(f"Error fetching social feed {social_feed_id}: {e}")
            return None
    
    async def _get_article_document(self, article_id: int) -> Optional[Dict[str, Any]]:
        """Get article document from MongoDB."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.article  # Correct collection name from dedicatedPrintEsSync.py
            
            # Try both int and long formats for article ID
            from bson.int64 import Int64
            
            # First try as int
            doc = await asyncio.to_thread(collection.find_one, {"articleId": article_id})
            if doc:
                return doc
            
            # If not found, try as Int64 (Long)
            doc = await asyncio.to_thread(collection.find_one, {"articleId": Int64(article_id)})
            if doc:
                return doc
                
            # If still not found, try as string (some systems store as string)
            doc = await asyncio.to_thread(collection.find_one, {"articleId": str(article_id)})
            return doc
            
        except Exception as e:
            error_str = str(e)
            # Check if it's a timeout error
            if "timed out" in error_str or "timeout" in error_str.lower():
                logger.warning(f"MongoDB timeout detected while fetching article. Attempting to reset connection...")
                try:
                    await reset_mongo_client()
                    self.mongo_client = await get_mongo_client()
                    logger.info("MongoDB client reset successfully.")
                except Exception as reset_error:
                    logger.error(f"Failed to reset MongoDB client: {reset_error}")
            
            logger.error(f"Error fetching article {article_id}: {e}")
            return None
    
    async def _build_social_es_payload(self, social_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build Elasticsearch payload for social feed document using the actual logic from dedicatedOnlineEsSync.py."""
        try:
            social_feed_id = social_doc.get("socialFeedId")
            
            # Ensure MongoDB client is initialized
            if self.mongo_client is None:
                await self.initialize()
            
            # Get related collections
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection_social_feed_tag = db.socialFeedTag
            collection_social_feed_similar = db.socialFeedSimilar
            
            # Build Children Info
            social_feed_similar = collection_social_feed_similar.find_one({"parentSocialFeedId": social_feed_id})
            children = []
            if social_feed_similar and social_feed_similar.get('child'):
                for child in social_feed_similar.get("child", []):
                    if not child.get("socialFeedId"):
                        continue
                    children.append({
                        "socialFeedId": child.get("socialFeedId"),
                        "publicationId": child.get("publicationId") if child.get("publicationId") and str(child.get("publicationId")).strip() else None,
                        "publicationName": child.get("publicationName", "")
                    })

            # Build Company Tags
            company_tag_cursor = collection_social_feed_tag.find({"socialFeedId": social_feed_id})
            company_tags_from_doc = {tag.get("id"): tag.get("clientArticleTag", []) for tag in social_doc.get("companyTag", [])}
            company_tags = []
            
            for tag in company_tag_cursor:
                # Get companyId and companyName from tag document (direct fields)
                company_id = str(tag.get("companyId", "")) or str(tag.get("company", {}).get("id", ""))
                company_name = tag.get("companyName", "") or tag.get("company", {}).get("name", "")
                
                # Enrich with master cache if companyId exists but name is missing
                if company_id and not company_name:
                    company_info = self.master_cache.get_company(company_id)
                    if company_info:
                        company_name = company_info.get("companyName", "") or company_info.get("name", "")
                
                raw_keyword = tag.get("tagInfo", {}).get("keyword", "")
                
                # Use the keyword as-is for now (can add cleaning logic later)
                cleaned_keyword = raw_keyword

                company_tags.append({
                    "id": company_id,
                    "name": company_name,
                    "clientArticleTag": company_tags_from_doc.get(company_id, []),
                    "tagInfo": {
                        "keyword": cleaned_keyword,
                        "reportingTone": tag.get("tagInfo", {}).get("reportingTone", 0),
                        "prominence": tag.get("tagInfo", {}).get("prominence", 0),
                        "reportingSubject": tag.get("tagInfo", {}).get("reportingSubject", ""),
                        "subcategory": tag.get("tagInfo", {}).get("subcategory", ""),
                        "mailerReportingSubject": tag.get("tagInfo", {}).get("mailerReportingSubject", ""),
                        "remarks": tag.get("tagInfo", {}).get("remarks", ""),
                        "detailSummary": tag.get("tagInfo", {}).get("detailSummary", ""),
                        "detailId": tag.get("tagInfo", {}).get("detailId", 0)
                    },
                    "qc": {
                        "qc1Status": tag.get("qc", {}).get("qc1Status", False),
                        "qc2Status": tag.get("qc", {}).get("qc2Status", False),
                        "qc3Status": tag.get("qc", {}).get("qc3Status", False)
                    }
                })

            # Build the complete payload
            payload = {
                "socialFeedId": social_feed_id,
                "feedInfo": {
                    "txnNumber": social_doc.get("feedInfo", {}).get("txnNumber", 0),
                    "socialFeedType": social_doc.get("feedInfo", {}).get("socialFeedType", 0),
                    "link": social_doc.get("feedInfo", {}).get("link", ""),
                    "isActive": social_doc.get("feedInfo", {}).get("isActive", False)
                },
                "feedData": {
                    "headlineSnippet": social_doc.get("feedData", {}).get("headlineSnippet", ""),
                    "summarySnippet": social_doc.get("feedData", {}).get("summarySnippet", ""),
                    "headlines": social_doc.get("feedData", {}).get("headline", ""),
                    "summary": social_doc.get("feedData", {}).get("summary", ""),
                    "feedDate": self._extract_mongo_date(social_doc.get("feedData", {}).get("feedDate")),
                    "feedDateTime": self._extract_mongo_date(social_doc.get("feedData", {}).get("feedDateTime")),
                    "articleDateNumber": social_doc.get("feedData", {}).get("articleDateNumber", 0),
                    "language": social_doc.get("feedData", {}).get("language", ""),
                    "text": social_doc.get("feedData", {}).get("text", "")
                },
                "publicationInfo": {
                    "id": social_doc.get("publicationInfo", {}).get("id", ""),
                    "name": social_doc.get("publicationInfo", {}).get("name", ""),
                    "publicationCategory": social_doc.get("publicationInfo", {}).get("publicationCategory", "News")
                },
                "channelInfo": {
                    "id": social_doc.get("channelInfo", {}).get("id", None),
                    "name": social_doc.get("channelInfo", {}).get("name", None)
                },
                "companyTag": company_tags,
                "children": children,
                "image": {
                    "hasImage": social_doc.get("image", {}).get("hasImage", False),
                    "url": social_doc.get("image", {}).get("url", ""),
                    "filename": social_doc.get("image", {}).get("filename", "")
                },
                "video": {
                    "hasVideo": social_doc.get("video", {}).get("hasVideo", False)
                },
                "searchInfo": {
                    "keywordMatched": social_doc.get("searchInfo", {}).get("keywordMatched", []),
                    "sourceType": social_doc.get("searchInfo", {}).get("sourceType", "")
                },
                "socialMetrics": {
                    "wordCount": social_doc.get("socialMetrics", {}).get("wordCount", 0),
                    "sentiment": social_doc.get("socialMetrics", {}).get("sentiment", ""),
                    "reach": social_doc.get("socialMetrics", {}).get("reach", 0),
                    "engagement": social_doc.get("socialMetrics", {}).get("engagement", 0),
                    "urlViews": social_doc.get("socialMetrics", {}).get("urlViews", 0)
                },
                "socialMediaInfo": {
                    "alexaStats": {
                        "pageViews": social_doc.get("socialMediaInfo", {}).get("alexaStats", {}).get("pageViews", 0),
                        "uniqueVisitors": social_doc.get("socialMediaInfo", {}).get("alexaStats", {}).get("uniqueVisitors", 0)
                    },
                    "facebook": {},
                    "instagram": {},
                    "youtube": {},
                    "pinterest": {},
                    "twitter": {}
                },
                "location": social_doc.get("location", {}),
                "author": {
                    "id": social_doc.get("author", {}).get("id", ""),
                    "name": social_doc.get("author", {}).get("name", ""),
                    "gender": social_doc.get("author", {}).get("gender", "")
                },
                "extraSource": social_doc.get("extraSource", {}),
                "uploadInfo": {
                    "uploadDate": self._extract_mongo_date(social_doc.get("uploadInfo", {}).get("uploadDate")),
                    "uploadDateNumber": social_doc.get("uploadInfo", {}).get("uploadDateNumber", 0)
                },
                "qc": {
                    "qc1Status": social_doc.get("qc", {}).get("qc1Status", False),
                    "qc2Status": social_doc.get("qc", {}).get("qc2Status", False)
                },
                "crossLanguageInvertedToken": social_doc.get("crossLanguageInvertedToken", "")
            }
            
            return payload
            
        except Exception as e:
            error_str = str(e)
            # Check if it's a timeout error
            if "timed out" in error_str or "timeout" in error_str.lower():
                logger.warning(f"MongoDB timeout detected in social feed payload build. Attempting to reset connection...")
                try:
                    # Reset MongoDB client to get fresh connection with updated timeouts
                    await reset_mongo_client()
                    self.mongo_client = await get_mongo_client()
                    logger.info("MongoDB client reset successfully. New connection established.")
                except Exception as reset_error:
                    logger.error(f"Failed to reset MongoDB client: {reset_error}")
            
            logger.error(f"Error building social feed payload: {e}")
            return None
    
    def _extract_mongo_date(self, mongo_date):
        """Extract ISO date string from MongoDB date formats"""
        if isinstance(mongo_date, dict) and "$date" in mongo_date:
            # If Mongo extended JSON format
            return mongo_date["$date"]
        elif isinstance(mongo_date, datetime):
            # If it's already a Python datetime object
            return mongo_date.isoformat()
        elif isinstance(mongo_date, str):
            # If already a string
            return mongo_date
        else:
            return None
    
    async def _get_article_stitch_data(self, article_id: int, article_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get articleStitch data from MongoDB collection.
        
        Uses embedded articleStitch field as a hint to determine if query is needed.
        Returns stitch data if found, None otherwise.
        """
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection_article_stitch = db.articleStich
            
            from bson.int64 import Int64
            
            # Check embedded field first as optimization hint
            embedded_stitch = article_doc.get("articleStitch", {})
            is_main_hint = embedded_stitch.get("isMainArticle", False)
            is_child_hint = embedded_stitch.get("isChildArticle", False)
            
            stitch_doc = None
            
            # Helper function to try all ID formats for a query
            def try_query(query_template, query_type):
                """Try query with int, Int64, and str formats."""
                for id_format in [article_id, Int64(article_id), str(article_id)]:
                    query = query_template(id_format)
                    result = collection_article_stitch.find_one(query)
                    if result:
                        logger.debug(f"Article {article_id}: Found stitch doc as {query_type} with ID format {type(id_format).__name__}")
                        return result
                return None
            
            # Use hints to prioritize which query to try first
            if is_main_hint:
                # Try as main article first (hint suggests it's a main article)
                stitch_doc = try_query(lambda id_val: {"mainArticleId": id_val}, "main article (hint)")
                if stitch_doc:
                    return stitch_doc
                
                # If not found, try as child (maybe hint was wrong)
                stitch_doc = try_query(lambda id_val: {"child.articleId": id_val}, "child article (fallback)")
                if stitch_doc:
                    return stitch_doc
            elif is_child_hint:
                # Try as child article first (hint suggests it's a child)
                stitch_doc = try_query(lambda id_val: {"child.articleId": id_val}, "child article (hint)")
                if stitch_doc:
                    return stitch_doc
                
                # If not found, try as main (maybe hint was wrong)
                stitch_doc = try_query(lambda id_val: {"mainArticleId": id_val}, "main article (fallback)")
                if stitch_doc:
                    return stitch_doc
            else:
                # No hints, try both queries (main first, then child)
                stitch_doc = try_query(lambda id_val: {"mainArticleId": id_val}, "main article (no hint)")
                if stitch_doc:
                    return stitch_doc
                
                stitch_doc = try_query(lambda id_val: {"child.articleId": id_val}, "child article (no hint)")
                if stitch_doc:
                    return stitch_doc
            
            if stitch_doc:
                logger.debug(f"Article {article_id}: Successfully found stitch document in articleStitch collection")
            elif is_main_hint or is_child_hint:
                logger.warning(
                    f"Article {article_id} has embedded articleStitch hint (isMain={is_main_hint}, isChild={is_child_hint}) "
                    f"but no stitch document found in articleStitch collection. Tried queries with: int={article_id}, Int64={Int64(article_id)}, str='{str(article_id)}'"
                )
            
            return stitch_doc
            
        except Exception as e:
            logger.error(f"Error fetching articleStitch data for article {article_id}: {e}")
            return None
    
    def _build_article_stitch_payload(self, article_id: int, stitch_doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Build articleStitch payload from stitch document.
        
        Returns None if no stitch relationship exists (field won't be added to ES doc).
        """
        if not stitch_doc:
            return None
        
        # Normalize IDs for comparison (handle Int64, int, str)
        def normalize_id(id_val):
            if id_val is None:
                return None
            try:
                return int(id_val)
            except (ValueError, TypeError):
                return id_val
        
        main_article_id = normalize_id(stitch_doc.get("mainArticleId"))
        article_id_normalized = normalize_id(article_id)
        
        logger.debug(f"Article {article_id}: Comparing IDs - mainArticleId={main_article_id} (type={type(main_article_id).__name__}), article_id={article_id_normalized} (type={type(article_id_normalized).__name__}), match={main_article_id == article_id_normalized}")
        
        is_main_article = (main_article_id == article_id_normalized)
        is_child_article = False
        
        # Check if this article is in the child array
        child_array = stitch_doc.get("child", [])
        for child in child_array:
            child_article_id = normalize_id(child.get("articleId"))
            if child_article_id == article_id_normalized:
                is_child_article = True
                break
        
        # If article is neither main nor child, return None
        if not is_main_article and not is_child_article:
            logger.warning(
                f"Article {article_id} not found in stitch document (mainArticleId={main_article_id}, "
                f"children={[normalize_id(c.get('articleId')) for c in child_array]})"
            )
            return None
        
        # Build stitch payload - only include fields that are in the ES mapping
        # Current mapping only has: isMainArticle, isChildArticle
        # If you need more fields, update the ES mapping first
        stitch_payload = {
            "isMainArticle": is_main_article,
            "isChildArticle": is_child_article
        }
        
        # Note: The following fields are available but not in current ES mapping:
        # - mainArticleId
        # - mainArticleDate
        # - mainPublicationId
        # - mainPublicationName
        # - child (array)
        # To include these, update the ES mapping to add them
        
        return stitch_payload

    async def _build_article_es_payload(self, article_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build Elasticsearch payload for article document using the actual logic from dedicatedPrintEsSync.py."""
        try:
            article_id = article_doc.get("articleId")
            
            # Get related collections
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection_article_tag = db.articleTag
            collection_article_similar = db.articleSimilar
            
            # Build Children Info
            article_similar = collection_article_similar.find_one({"parentArticleId": article_id})
            children = []
            if article_similar and article_similar.get('child'):
                for child in article_similar.get("child", []):
                    child_article_id = child.get("articleId")
                    if not child_article_id:
                        continue
                    children.append({
                        "articleId": child_article_id,
                        "publicationId": child.get("publicationId") or None,
                        "publicationName": child.get("publicationName", "")
                    })
            else:
                children = article_doc.get('children', [])

            # Build Company Tags
            # Support multiple ID formats (int, Int64, str) to reliably fetch latest tags
            from bson.int64 import Int64
            company_tag_cursor = collection_article_tag.find({
                "$or": [
                    {"articleId": article_id},
                    {"articleId": Int64(article_id)},
                    {"articleId": str(article_id)}
                ]
            })
            company_tags = []
            company_client_tags = {
                tag.get("id"): tag.get("clientArticleTag", [])
                for tag in article_doc.get("companyTag", [])
            }
            
            for tag in company_tag_cursor:
                company_id = tag.get("company", {}).get("id", "")
                client_article_tags = company_client_tags.get(company_id, [])
                raw_keyword = tag.get("tagInfo", {}).get("keyword", "")
                
                # Use the keyword as-is for now (can add cleaning logic later)
                cleaned_keyword = raw_keyword
                
                company_tags.append({
                    "id": company_id,
                    "name": tag.get("company", {}).get("name", ""),
                    "clientArticleTag": client_article_tags,
                    "tagInfo": {
                        "space": tag.get("tagInfo", {}).get("space", 0),
                        "totalSpace": tag.get("tagInfo", {}).get("totalSpace", 0),
                        "iScore": tag.get("tagInfo", {}).get("iScore", 0),
                        "vScore": tag.get("tagInfo", {}).get("vScore", 0),
                        "keyword": cleaned_keyword,
                        "reportingTone": tag.get("tagInfo", {}).get("reportingTone", 0),
                        "reportingSubject": tag.get("tagInfo", {}).get("reportingSubject", ""),
                        "subcategory": tag.get("tagInfo", {}).get("subcategory", ""),
                        "prominence": tag.get("tagInfo", {}).get("prominence", 0),
                        "manualProminence": tag.get("tagInfo", {}).get("manualProminence", 0),
                        "systemProminence": tag.get("tagInfo", {}).get("systemProminence", 0),
                        "detailSummary": tag.get("tagInfo", {}).get("detailSummary", "")
                    }
                })

            # Build articleStitch data
            stitch_doc = await self._get_article_stitch_data(article_id, article_doc)
            article_stitch = self._build_article_stitch_payload(article_id, stitch_doc)
            
            if stitch_doc and article_stitch:
                logger.debug(f"Article {article_id}: Found articleStitch data, isMain={article_stitch.get('isMainArticle')}, isChild={article_stitch.get('isChildArticle')}")
            elif stitch_doc and not article_stitch:
                logger.warning(f"Article {article_id}: Found stitch document but payload build returned None")
            elif not stitch_doc:
                embedded_stitch = article_doc.get("articleStitch", {})
                if embedded_stitch:
                    logger.debug(f"Article {article_id}: Has embedded articleStitch hint but no stitch document found in collection")

            # Build the complete payload
            article_date_value = article_doc.get("articleInfo", {}).get("articleDate", None)
            payload = {
                "articleId": article_id,
                "articleInfo": {
                    "articleDate": self._extract_mongo_date(article_date_value) if article_date_value else datetime.utcnow().isoformat(),
                    "articleNumber": article_doc.get("articleInfo", {}).get("articleNumber", 0),
                    "articleMonth": article_doc.get("articleInfo", {}).get("articleMonth", 0),
                    "articleYear": article_doc.get("articleInfo", {}).get("articleYear", 0),
                    "reportingSubject": article_doc.get("articleInfo", {}).get("reportingSubject", ""),
                    "journalist": article_doc.get("author", {}).get("name", ""),
                    "cityId": article_doc.get("articleInfo", {}).get("cityId", 0),
                    "mailSent": article_doc.get("articleInfo", {}).get("mailSent", True),
                    "hasContinue": article_doc.get("articleInfo", {}).get("hasContinue", True),
                    "onlineType": article_doc.get("articleInfo", {}).get("onlineType", True),
                    "isChild": article_doc.get("articleInfo", {}).get("isChild", True),
                    "isTV": article_doc.get("articleInfo", {}).get("isTV", True),
                    "isActive": article_doc.get("articleInfo", {}).get("isActive", True)
                },
                "articleAttribute": {
                    "imageSize": article_doc.get("articleAttribute", {}).get("imageSize", 0),
                    "imageSizeText": article_doc.get("articleAttribute", {}).get("imageSizeText", ""),
                    "isGraph": article_doc.get("articleAttribute", {}).get("isGraph", True),
                    "graphValue": article_doc.get("articleAttribute", {}).get("graphValue", 0),
                    "width": article_doc.get("articleAttribute", {}).get("width", 0),
                    "height": article_doc.get("articleAttribute", {}).get("height", 0),
                    "hasPDF": article_doc.get("articleAttribute", {}).get("hasPDF", True),
                    "hasHTML": article_doc.get("articleAttribute", {}).get("hasHTML", True)
                },
                "articleData": {
                    "headlines": article_doc.get("articleData", {}).get("headlines", ""),
                    "summary": article_doc.get("articleData", {}).get("summary", ""),
                    "text": article_doc.get("articleData", {}).get("text", ""),
                    "box": article_doc.get("articleData", {}).get("box", ""),
                    "boxValue": article_doc.get("articleData", {}).get("boxValue", 0),
                    "photoValue": article_doc.get("articleData", {}).get("photoValue", 0),
                    "pageNumber": article_doc.get("articleData", {}).get("pageNumber", 0),
                    "pageValue": article_doc.get("articleData", {}).get("pageValue", 0),
                    "space": article_doc.get("articleData", {}).get("space", 0),
                    "language": article_doc.get("articleData", {}).get("language", ""),
                    "summarySnippet": article_doc.get("articleData", {}).get("summarySnippet", "")
                },
                "uploadInfo": {
                    "uploadId": article_doc.get("uploadInfo", {}).get("uploadId", ""),
                    "uploadDate": self._extract_mongo_date(article_doc.get("uploadInfo", {}).get("uploadDate")),
                    "imageId": article_doc.get("uploadInfo", {}).get("imageId", ""),
                    "cityId": article_doc.get("articleInfo", {}).get("cityId", 0),
                    "city": article_doc.get("uploadInfo", {}).get("city", ""),
                    "ipAddress": article_doc.get("uploadInfo", {}).get("ipAddress", "")
                },
                "publicationInfo": {
                    "id": article_doc.get("publicationInfo", {}).get("id", ""),
                    "name": article_doc.get("publicationInfo", {}).get("name", ""),
                    "pubGroupId": article_doc.get("publicationInfo", {}).get("pubGroupId", ""),
                    "pubGroupName": article_doc.get("publicationInfo", {}).get("pubGroupName", "")
                },
                "children": children,
                "companyTag": company_tags,
                "headlineInfo": {
                    "isUpdated": article_doc.get("headlineInfo", {}).get("isUpdated", None),
                    "updatedBy": article_doc.get("headlineInfo", {}).get("updatedBy", None),
                    "updatedOn": article_doc.get("headlineInfo", {}).get("updatedOn", None)
                },
                "link": article_doc.get("link", ""),
                "JPGLINK": article_doc.get("JPGLINK", ""),
                "HTMLLINK": article_doc.get("HTMLLINK", ""),
                "crossLanguageInvertedToken": article_doc.get("crossLanguageInvertedToken", "")
            }
            
            # Add articleStitch field only if stitch relationship exists
            if article_stitch:
                payload["articleStitch"] = article_stitch
                logger.info(f"Article {article_id}: Added articleStitch to ES payload (isMain={article_stitch.get('isMainArticle')}, isChild={article_stitch.get('isChildArticle')})")
            else:
                embedded_stitch = article_doc.get("articleStitch", {})
                if embedded_stitch:
                    logger.warning(f"Article {article_id}: Has embedded articleStitch hint but articleStitch not added to payload")
            
            return payload
            
        except Exception as e:
            error_str = str(e)
            # Check if it's a timeout error
            if "timed out" in error_str or "timeout" in error_str.lower():
                logger.warning(f"MongoDB timeout detected in article payload build. Attempting to reset connection...")
                try:
                    # Reset MongoDB client to get fresh connection with updated timeouts
                    await reset_mongo_client()
                    self.mongo_client = await get_mongo_client()
                    logger.info("MongoDB client reset successfully. New connection established.")
                except Exception as reset_error:
                    logger.error(f"Failed to reset MongoDB client: {reset_error}")
            
            logger.error(f"Error building article payload: {e}")
            return None
    
    async def _index_to_elasticsearch(
        self, 
        index_name: str, 
        doc_id: str, 
        payload: Dict[str, Any]
    ):
        """Index document to Elasticsearch."""
        try:
            # Offload sync ES client call to a thread to avoid blocking the event loop
            await asyncio.to_thread(
                self.es_client.index,
                index=index_name,
                id=doc_id,
                body=payload
            )
        except Exception as e:
            logger.error(f"Error indexing to Elasticsearch: {e}")
            raise
    
    async def _bulk_index_to_elasticsearch(
        self,
        index_name: str,
        documents: List[Dict[str, Any]]
    ):
        """Bulk index multiple documents to Elasticsearch.
        
        Args:
            index_name: Elasticsearch index name
            documents: List of dicts with 'id' and 'body' keys, e.g. [{'id': '123', 'body': {...}}, ...]
        """
        if not documents:
            return
        
        try:
            # Prepare bulk actions
            from elasticsearch.helpers import bulk
            actions = [
                {
                    "_index": index_name,
                    "_id": doc["id"],
                    "_source": doc["body"]
                }
                for doc in documents
            ]
            
            # Offload bulk operation to thread
            await asyncio.to_thread(bulk, self.es_client, actions)
            logger.debug(f"Bulk indexed {len(documents)} documents to {index_name}")
        except Exception as e:
            logger.error(f"Error bulk indexing to Elasticsearch: {e}")
            raise
    
    async def _bulk_index_to_elasticsearch_optimized(
        self,
        index_name: str,
        documents: List[Dict[str, Any]],
        chunk_size: int = 1000,
        max_retries: int = 3
    ):
        """Optimized bulk index with chunking, retry logic, and parallel processing."""
        if not documents:
            return
        
        try:
            from elasticsearch.helpers import bulk
            
            # Process in chunks to avoid memory issues
            # For very large batches, process chunks in parallel
            if len(documents) > chunk_size * 2:
                # Parallel chunk processing for large batches
                async def process_chunk(chunk_docs):
                    actions = [
                        {
                            "_index": index_name,
                            "_id": doc["id"],
                            "_source": doc["body"]
                        }
                        for doc in chunk_docs
                    ]
                    
                    # Retry logic for transient failures
                    for attempt in range(max_retries):
                        try:
                            await asyncio.to_thread(
                                bulk, 
                                self.es_client, 
                                actions, 
                                chunk_size=min(chunk_size, len(actions)),
                                request_timeout=60,
                                max_retries=0  # We handle retries ourselves
                            )
                            return True
                        except Exception as e:
                            if attempt < max_retries - 1:
                                wait_time = 2 ** attempt  # Exponential backoff
                                logger.warning(f"Bulk index attempt {attempt + 1} failed, retrying in {wait_time}s: {e}")
                                await asyncio.sleep(wait_time)
                            else:
                                logger.error(f"Bulk index failed after {max_retries} attempts: {e}")
                                raise
                    return False
                
                # Process chunks in parallel (limit to 2 concurrent chunks to avoid overwhelming ES)
                chunks = [documents[i:i + chunk_size] for i in range(0, len(documents), chunk_size)]
                semaphore = asyncio.Semaphore(2)  # Limit concurrent ES operations (reduced from 4 to 2)
                
                async def process_with_semaphore(chunk_docs):
                    async with semaphore:
                        return await process_chunk(chunk_docs)
                
                tasks = [process_with_semaphore(chunk) for chunk in chunks]
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                # Sequential processing for smaller batches
                actions = [
                    {
                        "_index": index_name,
                        "_id": doc["id"],
                        "_source": doc["body"]
                    }
                    for doc in documents
                ]
                
                # Retry logic
                for attempt in range(max_retries):
                    try:
                        await asyncio.to_thread(
                            bulk, 
                            self.es_client, 
                            actions, 
                            chunk_size=chunk_size,
                            request_timeout=60
                        )
                        break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            wait_time = 2 ** attempt
                            logger.warning(f"Bulk index attempt {attempt + 1} failed, retrying in {wait_time}s: {e}")
                            await asyncio.sleep(wait_time)
                        else:
                            raise
            
            logger.debug(f"Bulk indexed {len(documents)} documents to {index_name} (optimized)")
        except Exception as e:
            logger.error(f"Error bulk indexing to Elasticsearch: {e}")
            raise
    
    async def _process_large_article_batch(
        self, 
        article_ids: List[int], 
        max_workers: int
    ) -> BatchRefreshResults:
        """Optimized bulk processing for article batches - BLAZING FAST (works for any size)."""
        try:
            # Pre-load master data cache if not loaded
            await self.master_cache.load_all_master_data(self.mongo_client, settings.MONGODB_DATABASE)
            
            # Bulk fetch all data in parallel for maximum speed
            articles_task = self._bulk_fetch_articles(article_ids)
            tags_task = self._bulk_fetch_article_tags(article_ids)
            similars_task = self._bulk_fetch_article_similars(article_ids)
            
            # Wait for articles first (needed for stitches)
            articles = await articles_task
            
            # Now fetch stitches (needs articles)
            stitches_task = self._bulk_fetch_article_stitches(article_ids, articles)
            
            # Wait for all data fetches to complete
            article_tags, article_similars, article_stitches = await asyncio.gather(
                tags_task,
                similars_task,
                stitches_task
            )
            
            # Process in parallel chunks - optimize for maximum parallelism
            # Smaller chunks = more parallel processing = faster overall
            # For large batches, use smaller chunks to maximize parallelism
            if len(article_ids) <= 10:
                chunk_size = len(article_ids)  # Single chunk for very small batches
            elif len(article_ids) >= 500:
                # For large batches, use smaller chunks (50) to maximize parallelism
                # This allows up to 10 parallel chunks for 500 articles
                chunk_size = 50
            elif len(article_ids) >= 100:
                chunk_size = 25  # Smaller chunks for better parallelism
            else:
                chunk_size = max(10, len(article_ids) // max_workers)
            
            chunks = [article_ids[i:i + chunk_size] for i in range(0, len(article_ids), chunk_size)]
            
            # Shared refreshed_ids set across all chunks to prevent duplicate child processing
            # Initialize as empty - IDs are added as they're processed, not before
            shared_refreshed_ids = set()
            
            # Process chunks in parallel
            tasks = []
            for chunk in chunks:
                task = self._process_article_chunk(chunk, articles, article_tags, article_similars, article_stitches, shared_refreshed_ids)
                tasks.append(task)
            
            chunk_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Flatten results
            results = []
            for chunk_result in chunk_results:
                if isinstance(chunk_result, Exception):
                    logger.error(f"Chunk processing error: {chunk_result}")
                    continue
                results.extend(chunk_result)
            
            successful_count = sum(1 for r in results if r.success)
            failed_count = len(results) - successful_count
            
            return BatchRefreshResults(
                successful_count=successful_count,
                failed_count=failed_count,
                results=results
            )
            
        except Exception as e:
            logger.error(f"Error in large article batch processing: {e}")
            # Fallback to individual processing
            return await self._process_batch_articles(article_ids, max_workers)
    
    async def _bulk_fetch_articles(self, article_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Bulk fetch articles from MongoDB - OPTIMIZED with $in query."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.article
            
            # Use $in query - much faster than $or
            # Add projection to only fetch needed fields (reduces data transfer)
            projection = {
                "_id": 1,
                "articleId": 1,
                "articleInfo": 1,
                "articleAttribute": 1,
                "articleData": 1,
                "uploadInfo": 1,
                "publicationInfo": 1,
                "companyTag": 1,
                "headlineInfo": 1,
                "link": 1,
                "JPGLINK": 1,
                "HTMLLINK": 1,
                "crossLanguageInvertedToken": 1,
                "author": 1,
                "children": 1,
                "articleStitch": 1  # Hint field
            }
            
            # Try int format first (most common)
            articles_cursor = await asyncio.to_thread(
                collection.find, 
                {"articleId": {"$in": article_ids}},
                projection
            )
            articles = {}
            for doc in await asyncio.to_thread(list, articles_cursor):
                article_id = doc.get("articleId")
                try:
                    article_id_int = int(article_id)
                except (ValueError, TypeError):
                    article_id_int = article_id
                articles[article_id_int] = doc
            
            # If some articles not found, try other formats
            missing_ids = set(article_ids) - set(articles.keys())
            if missing_ids:
                from bson.int64 import Int64
                # Try Int64 format
                int64_ids = [Int64(aid) for aid in missing_ids]
                missing_cursor = await asyncio.to_thread(
                    collection.find,
                    {"articleId": {"$in": int64_ids}}
                )
                for doc in await asyncio.to_thread(list, missing_cursor):
                    article_id = doc.get("articleId")
                    try:
                        article_id_int = int(article_id)
                    except (ValueError, TypeError):
                        article_id_int = article_id
                    articles[article_id_int] = doc
                
                # Try string format for remaining
                still_missing = set(article_ids) - set(articles.keys())
                if still_missing:
                    str_ids = [str(aid) for aid in still_missing]
                    str_cursor = await asyncio.to_thread(
                        collection.find,
                        {"articleId": {"$in": str_ids}}
                    )
                    for doc in await asyncio.to_thread(list, str_cursor):
                        article_id = doc.get("articleId")
                        try:
                            article_id_int = int(article_id)
                        except (ValueError, TypeError):
                            article_id_int = article_id
                        articles[article_id_int] = doc
            
            logger.info(f"Bulk fetched {len(articles)}/{len(article_ids)} articles from MongoDB")
            return articles
            
        except Exception as e:
            logger.error(f"Error bulk fetching articles: {e}")
            return {}
    
    async def _bulk_fetch_article_tags(self, article_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Bulk fetch article tags from MongoDB - OPTIMIZED with $in query, field projections, and parallel queries."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.articleTag
            
            # For batches >50, split into parallel queries for better performance
            # Lowered threshold from 200 to 50 to improve performance on smaller batches
            if len(article_ids) > 50:
                # Split into chunks of 50 and query in parallel
                chunk_size = 50
                chunks = [article_ids[i:i + chunk_size] for i in range(0, len(article_ids), chunk_size)]
                
                async def fetch_chunk(chunk_ids):
                    return await self._fetch_tags_chunk(collection, chunk_ids)
                
                chunk_results = await asyncio.gather(*[fetch_chunk(chunk) for chunk in chunks])
                
                # Merge results
                tags_by_article = {}
                for chunk_result in chunk_results:
                    tags_by_article.update(chunk_result)
            else:
                tags_by_article = await self._fetch_tags_chunk(collection, article_ids)
            
            logger.info(f"Bulk fetched tags for {len(tags_by_article)} articles")
            return tags_by_article
            
        except Exception as e:
            logger.error(f"Error bulk fetching article tags: {e}")
            return {}
    
    async def _fetch_tags_chunk(self, collection, article_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Fetch tags for a chunk of article IDs with optimized query and field projections."""
        from bson.int64 import Int64
        
        # Field projection - fetch all tag fields (like single endpoint does)
        # Don't use projection for tags to ensure we get all nested company fields
        # The single endpoint works because it gets full documents without projection
        projection = None  # No projection = get all fields like single endpoint
        
        tags_by_article = {}
        
        # Try int format first
        # Use find() without projection if projection is None (like single endpoint)
        if projection:
            tags_cursor = await asyncio.to_thread(
                collection.find,
                {"articleId": {"$in": article_ids}},
                projection
            )
        else:
            tags_cursor = await asyncio.to_thread(
                collection.find,
                {"articleId": {"$in": article_ids}}
            )
        
        for tag in await asyncio.to_thread(list, tags_cursor):
            article_id = tag.get("articleId")
            try:
                article_id_int = int(article_id)
            except (ValueError, TypeError):
                article_id_int = article_id
            
            if article_id_int not in tags_by_article:
                tags_by_article[article_id_int] = []
            tags_by_article[article_id_int].append(tag)
        
        # Try Int64 format for missing IDs (only if needed)
        missing_ids = set(article_ids) - set(tags_by_article.keys())
        if missing_ids:
            int64_ids = [Int64(aid) for aid in missing_ids]
            # Use find() without projection if projection is None
            if projection:
                missing_cursor = await asyncio.to_thread(
                    collection.find,
                    {"articleId": {"$in": int64_ids}},
                    projection
                )
            else:
                missing_cursor = await asyncio.to_thread(
                    collection.find,
                    {"articleId": {"$in": int64_ids}}
                )
            for tag in await asyncio.to_thread(list, missing_cursor):
                article_id = tag.get("articleId")
                try:
                    article_id_int = int(article_id)
                except (ValueError, TypeError):
                    article_id_int = article_id
                
                if article_id_int not in tags_by_article:
                    tags_by_article[article_id_int] = []
                tags_by_article[article_id_int].append(tag)
        
        return tags_by_article
    
    async def _bulk_fetch_article_similars(self, article_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Bulk fetch article similars from MongoDB - OPTIMIZED with $in query."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.articleSimilar
            
            # Use $in query - much faster
            similars_cursor = await asyncio.to_thread(
                collection.find,
                {"parentArticleId": {"$in": article_ids}}
            )
            similars_by_article = {}
            for doc in await asyncio.to_thread(list, similars_cursor):
                parent_id = doc.get("parentArticleId")
                try:
                    parent_id_int = int(parent_id)
                except (ValueError, TypeError):
                    parent_id_int = parent_id
                similars_by_article[parent_id_int] = doc
            
            # Try other formats for missing
            missing_ids = set(article_ids) - set(similars_by_article.keys())
            if missing_ids:
                from bson.int64 import Int64
                int64_ids = [Int64(aid) for aid in missing_ids]
                missing_cursor = await asyncio.to_thread(
                    collection.find,
                    {"parentArticleId": {"$in": int64_ids}}
                )
                for doc in await asyncio.to_thread(list, missing_cursor):
                    parent_id = doc.get("parentArticleId")
                    try:
                        parent_id_int = int(parent_id)
                    except (ValueError, TypeError):
                        parent_id_int = parent_id
                    similars_by_article[parent_id_int] = doc
            
            logger.info(f"Bulk fetched similars for {len(similars_by_article)} articles")
            return similars_by_article
            
        except Exception as e:
            logger.error(f"Error bulk fetching article similars: {e}")
            return {}
    
    async def _bulk_fetch_article_stitches(
        self, 
        article_ids: List[int], 
        articles: Dict[int, Dict[str, Any]]
    ) -> Dict[int, Optional[Dict[str, Any]]]:
        """Bulk fetch article stitches from MongoDB and build payloads - OPTIMIZED.
        
        Returns a dictionary mapping article_id to articleStitch payload (or None if no stitch).
        """
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.articleStich  # Note: collection name is articleStich (not articleStitch)
            
            from bson.int64 import Int64
            
            # Use $in queries - much faster than $or
            # Query as main article
            main_stitches_cursor = await asyncio.to_thread(
                collection.find,
                {"mainArticleId": {"$in": article_ids}}
            )
            
            # Query as child article (need to check nested field)
            child_stitches_cursor = await asyncio.to_thread(
                collection.find,
                {"child.articleId": {"$in": article_ids}}
            )
            
            # Also try Int64 format for main articles
            int64_ids = [Int64(aid) for aid in article_ids]
            main_stitches_int64_cursor = await asyncio.to_thread(
                collection.find,
                {"mainArticleId": {"$in": int64_ids}}
            )
            
            # Combine all stitch documents
            all_stitches = []
            all_stitches.extend(await asyncio.to_thread(list, main_stitches_cursor))
            all_stitches.extend(await asyncio.to_thread(list, child_stitches_cursor))
            all_stitches.extend(await asyncio.to_thread(list, main_stitches_int64_cursor))
            
            # Deduplicate by _id
            seen_ids = set()
            unique_stitches = []
            for stitch in all_stitches:
                stitch_id = stitch.get("_id")
                if stitch_id and stitch_id not in seen_ids:
                    seen_ids.add(stitch_id)
                    unique_stitches.append(stitch)
            
            stitches_by_article = {}
            
            # Process all stitch documents
            for stitch_doc in unique_stitches:
                main_article_id = stitch_doc.get("mainArticleId")
                
                # Process main article
                try:
                    main_id_int = int(main_article_id)
                    if main_id_int in article_ids:
                        article_doc = articles.get(main_id_int, {})
                        stitch_payload = self._build_article_stitch_payload(main_id_int, stitch_doc)
                        if stitch_payload:
                            stitches_by_article[main_id_int] = stitch_payload
                except (ValueError, TypeError):
                    pass
                
                # Process child articles
                child_array = stitch_doc.get("child", [])
                for child in child_array:
                    child_article_id = child.get("articleId")
                    if child_article_id:
                        try:
                            child_id_int = int(child_article_id)
                            if child_id_int in article_ids:
                                article_doc = articles.get(child_id_int, {})
                                stitch_payload = self._build_article_stitch_payload(child_id_int, stitch_doc)
                                if stitch_payload:
                                    stitches_by_article[child_id_int] = stitch_payload
                        except (ValueError, TypeError):
                            pass
            
            logger.info(f"Bulk fetched stitches for {len(stitches_by_article)} articles")
            return stitches_by_article
            
        except Exception as e:
            logger.error(f"Error bulk fetching article stitches: {e}")
            return {}
    
    async def _process_article_chunk(
        self, 
        article_ids: List[int], 
        articles: Dict[int, Dict[str, Any]], 
        article_tags: Dict[int, List[Dict[str, Any]]], 
        article_similars: Dict[int, Dict[str, Any]],
        article_stitches: Dict[int, Optional[Dict[str, Any]]],
        refreshed_ids: Optional[set] = None
    ) -> List[RefreshResult]:
        """Process a chunk of articles with pre-fetched data and refresh children."""
        if refreshed_ids is None:
            refreshed_ids = set()
        
        results = []
        child_ids_to_refresh = set()
        
        # First pass: process requested articles and collect child IDs
        es_documents = []  # Collect documents for bulk indexing
        article_timings = {}  # Track timing per article
        
        for article_id in article_ids:
            if article_id in refreshed_ids:
                continue
                
            start_time = time.time()
            refreshed_ids.add(article_id)
            article_timings[article_id] = start_time
            
            try:
                article_doc = articles.get(article_id)
                if not article_doc:
                    results.append(RefreshResult(
                        document_id=str(article_id),
                        document_type="article",
                        success=False,
                        message=f"Article {article_id} not found in MongoDB",
                        processing_time=time.time() - start_time,
                        timestamp=datetime.utcnow()
                    ))
                    continue
                
                # Build payload with pre-fetched data
                payload = await self._build_article_es_payload_optimized(
                    article_doc, 
                    article_tags.get(article_id, []), 
                    article_similars.get(article_id),
                    article_stitches.get(article_id)
                )
                
                if not payload:
                    results.append(RefreshResult(
                        document_id=str(article_id),
                        document_type="article",
                        success=False,
                        message=f"Failed to build payload for article {article_id}",
                        processing_time=time.time() - start_time,
                        timestamp=datetime.utcnow()
                    ))
                    continue
                
                # Collect for bulk indexing instead of individual indexing
                es_documents.append({
                    "id": str(article_id),
                    "body": payload,
                    "article_id": article_id,
                    "start_time": start_time
                })
                
                # Collect child IDs for later refresh
                article_similar = article_similars.get(article_id)
                if article_similar and article_similar.get('child'):
                    for child in article_similar.get("child", []):
                        child_article_id = child.get("articleId")
                        if child_article_id:
                            try:
                                child_id_int = int(child_article_id)
                                if child_id_int not in refreshed_ids:
                                    child_ids_to_refresh.add(child_id_int)
                            except (ValueError, TypeError):
                                if child_article_id not in refreshed_ids:
                                    child_ids_to_refresh.add(child_article_id)
                
            except Exception as e:
                logger.error(f"Error processing article {article_id}: {e}")
                results.append(RefreshResult(
                    document_id=str(article_id),
                    document_type="article",
                    success=False,
                    message=f"Error refreshing article {article_id}: {str(e)}",
                    processing_time=time.time() - start_time,
                    timestamp=datetime.utcnow()
                ))
        
        # Bulk index all documents to Elasticsearch at once (much faster than individual indexing)
        if es_documents:
            bulk_start = time.time()
            try:
                # Use optimized bulk indexing
                await self._bulk_index_to_elasticsearch_optimized(
                    settings.ES_REFRESH_PRINT_INDEX,
                    [{"id": doc["id"], "body": doc["body"]} for doc in es_documents]
                )
                bulk_time = time.time() - bulk_start
                logger.info(f"[PERF] Bulk indexed {len(es_documents)} articles in {bulk_time:.3f}s ({len(es_documents)/bulk_time:.0f} docs/sec)")
                
                # Create success results for all bulk-indexed articles
                for doc in es_documents:
                    article_id = doc["article_id"]
                    start_time = doc["start_time"]
                    results.append(RefreshResult(
                        document_id=str(article_id),
                        document_type="article",
                        success=True,
                        message=f"Successfully refreshed article {article_id}",
                        processing_time=time.time() - start_time,
                        timestamp=datetime.utcnow()
                    ))
            except Exception as e:
                logger.error(f"Error bulk indexing articles: {e}")
                # Mark all as failed
                for doc in es_documents:
                    article_id = doc["article_id"]
                    start_time = doc["start_time"]
                    results.append(RefreshResult(
                        document_id=str(article_id),
                        document_type="article",
                        success=False,
                        message=f"Error bulk indexing article {article_id}: {str(e)}",
                        processing_time=time.time() - start_time,
                        timestamp=datetime.utcnow()
                    ))
        
        # Second pass: refresh child articles in BATCH (much faster than individual)
        if child_ids_to_refresh:
            child_ids_list = list(child_ids_to_refresh)
            
            # Filter out already refreshed IDs to prevent infinite loops
            child_ids_to_process = [cid for cid in child_ids_list if cid not in refreshed_ids]
            
            if child_ids_to_process:
                child_start_time = time.time()
                
                # For very small child batches (<=2), skip to avoid overhead
                # They'll be refreshed when processed as main articles in future batches
                if len(child_ids_to_process) <= 2:
                    logger.debug(f"Skipping refresh of {len(child_ids_to_process)} child articles (too small, will be refreshed as main articles)")
                    # Mark as seen to prevent infinite loops
                    refreshed_ids.update(child_ids_to_process)
                else:
                    logger.info(f"Refreshing {len(child_ids_to_process)} child articles from batch (using optimized batch processing)")
                    
                    # Use batch refresh - now uses optimized path for ALL sizes (threshold lowered to 10)
                    try:
                        child_batch_results = await self.refresh_articles_batch(child_ids_to_process, max_workers=settings.MAX_WORKERS)
                        results.extend(child_batch_results.results)
                        
                        # Add all child IDs to refreshed set to prevent re-processing
                        refreshed_ids.update(child_ids_to_process)
                        
                        child_elapsed = time.time() - child_start_time
                        logger.info(f"[PERF] Completed refreshing {len(child_ids_to_process)} child articles in {child_elapsed:.2f}s ({len(child_ids_to_process)/child_elapsed:.1f} articles/sec)")
                    except Exception as e:
                        logger.error(f"Error batch refreshing child articles: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        # Fallback: mark as failed
                        for child_id in child_ids_to_process:
                            results.append(RefreshResult(
                                document_id=str(child_id),
                                document_type="article",
                                success=False,
                                message=f"Error batch refreshing child: {str(e)}",
                                processing_time=0.0,
                                timestamp=datetime.utcnow()
                            ))
        
        return results
    
    async def _build_article_es_payload_optimized(
        self, 
        article_doc: Dict[str, Any], 
        article_tags: List[Dict[str, Any]], 
        article_similar: Optional[Dict[str, Any]],
        article_stitch: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Optimized article payload building with pre-fetched data."""
        try:
            article_id = article_doc.get("articleId")
            
            # Build Children Info (using pre-fetched data)
            children = []
            if article_similar and article_similar.get('child'):
                for child in article_similar.get("child", []):
                    child_article_id = child.get("articleId")
                    if not child_article_id:
                        continue
                    children.append({
                        "articleId": child_article_id,
                        "publicationId": child.get("publicationId") or None,
                        "publicationName": child.get("publicationName", "")
                    })
            else:
                children = article_doc.get('children', [])

            # Build Company Tags (using pre-fetched data)
            company_tags = []
            company_client_tags = {
                tag.get("id"): tag.get("clientArticleTag", [])
                for tag in article_doc.get("companyTag", [])
            }
            
            for tag in article_tags:
                # Get companyId and companyName from tag document - try multiple possible structures
                # 1. Direct fields (companyId, companyName)
                company_id = str(tag.get("companyId", "")) if tag.get("companyId") else ""
                company_name = tag.get("companyName", "") if tag.get("companyName") else ""
                
                # 2. Nested company object (company.id, company.name)
                if not company_id:
                    company_obj = tag.get("company", {})
                    if company_obj:
                        company_id = str(company_obj.get("id", "")) if company_obj.get("id") else ""
                        if not company_id:
                            company_id = str(company_obj.get("companyId", "")) if company_obj.get("companyId") else ""
                        if not company_id:
                            company_id = str(company_obj.get("_id", "")) if company_obj.get("_id") else ""
                        
                        if not company_name:
                            company_name = company_obj.get("name", "") if company_obj.get("name") else ""
                            if not company_name:
                                company_name = company_obj.get("companyName", "") if company_obj.get("companyName") else ""
                
                # 3. Enrich with master cache if companyId exists but name is missing
                if company_id and not company_name:
                    company_info = self.master_cache.get_company(company_id)
                    if company_info:
                        company_name = company_info.get("companyName", "") or company_info.get("name", "")
                
                # Log if company info is still missing for debugging
                if not company_id:
                    logger.debug(f"Tag missing companyId: {tag.get('tagInfo', {}).get('keyword', 'unknown')}")
                
                client_article_tags = company_client_tags.get(company_id, [])
                raw_keyword = tag.get("tagInfo", {}).get("keyword", "")
                
                company_tags.append({
                    "id": company_id,
                    "name": company_name,
                    "clientArticleTag": client_article_tags,
                    "tagInfo": {
                        "space": tag.get("tagInfo", {}).get("space", 0),
                        "totalSpace": tag.get("tagInfo", {}).get("totalSpace", 0),
                        "iScore": tag.get("tagInfo", {}).get("iScore", 0),
                        "vScore": tag.get("tagInfo", {}).get("vScore", 0),
                        "keyword": raw_keyword,
                        "reportingTone": tag.get("tagInfo", {}).get("reportingTone", 0),
                        "reportingSubject": tag.get("tagInfo", {}).get("reportingSubject", ""),
                        "subcategory": tag.get("tagInfo", {}).get("subcategory", ""),
                        "prominence": tag.get("tagInfo", {}).get("prominence", 0),
                        "manualProminence": tag.get("tagInfo", {}).get("manualProminence", 0),
                        "systemProminence": tag.get("tagInfo", {}).get("systemProminence", 0),
                        "detailSummary": tag.get("tagInfo", {}).get("detailSummary", "")
                    }
                })

            # Build the complete payload
            article_date_value = article_doc.get("articleInfo", {}).get("articleDate", None)
            payload = {
                "articleId": article_id,
                "articleInfo": {
                    "articleDate": self._extract_mongo_date(article_date_value) if article_date_value else datetime.utcnow().isoformat(),
                    "articleNumber": article_doc.get("articleInfo", {}).get("articleNumber", 0),
                    "articleMonth": article_doc.get("articleInfo", {}).get("articleMonth", 0),
                    "articleYear": article_doc.get("articleInfo", {}).get("articleYear", 0),
                    "reportingSubject": article_doc.get("articleInfo", {}).get("reportingSubject", ""),
                    "journalist": article_doc.get("author", {}).get("name", ""),
                    "cityId": article_doc.get("articleInfo", {}).get("cityId", 0),
                    "mailSent": article_doc.get("articleInfo", {}).get("mailSent", True),
                    "hasContinue": article_doc.get("articleInfo", {}).get("hasContinue", True),
                    "onlineType": article_doc.get("articleInfo", {}).get("onlineType", True),
                    "isChild": article_doc.get("articleInfo", {}).get("isChild", True),
                    "isTV": article_doc.get("articleInfo", {}).get("isTV", True),
                    "isActive": article_doc.get("articleInfo", {}).get("isActive", True)
                },
                "articleAttribute": {
                    "imageSize": article_doc.get("articleAttribute", {}).get("imageSize", 0),
                    "imageSizeText": article_doc.get("articleAttribute", {}).get("imageSizeText", ""),
                    "isGraph": article_doc.get("articleAttribute", {}).get("isGraph", True),
                    "graphValue": article_doc.get("articleAttribute", {}).get("graphValue", 0),
                    "width": article_doc.get("articleAttribute", {}).get("width", 0),
                    "height": article_doc.get("articleAttribute", {}).get("height", 0),
                    "hasPDF": article_doc.get("articleAttribute", {}).get("hasPDF", True),
                    "hasHTML": article_doc.get("articleAttribute", {}).get("hasHTML", True)
                },
                "articleData": {
                    "headlines": article_doc.get("articleData", {}).get("headlines", ""),
                    "summary": article_doc.get("articleData", {}).get("summary", ""),
                    "text": article_doc.get("articleData", {}).get("text", ""),
                    "box": article_doc.get("articleData", {}).get("box", ""),
                    "boxValue": article_doc.get("articleData", {}).get("boxValue", 0),
                    "photoValue": article_doc.get("articleData", {}).get("photoValue", 0),
                    "pageNumber": article_doc.get("articleData", {}).get("pageNumber", 0),
                    "pageValue": article_doc.get("articleData", {}).get("pageValue", 0),
                    "space": article_doc.get("articleData", {}).get("space", 0),
                    "language": article_doc.get("articleData", {}).get("language", ""),
                    "summarySnippet": article_doc.get("articleData", {}).get("summarySnippet", "")
                },
                "uploadInfo": {
                    "uploadId": article_doc.get("uploadInfo", {}).get("uploadId", ""),
                    "uploadDate": self._extract_mongo_date(article_doc.get("uploadInfo", {}).get("uploadDate")),
                    "imageId": article_doc.get("uploadInfo", {}).get("imageId", ""),
                    "cityId": article_doc.get("articleInfo", {}).get("cityId", 0),
                    "city": article_doc.get("uploadInfo", {}).get("city", ""),
                    "ipAddress": article_doc.get("uploadInfo", {}).get("ipAddress", "")
                },
                "publicationInfo": {
                    "id": article_doc.get("publicationInfo", {}).get("id", ""),
                    "name": article_doc.get("publicationInfo", {}).get("name", ""),
                    "pubGroupId": article_doc.get("publicationInfo", {}).get("pubGroupId", ""),
                    "pubGroupName": article_doc.get("publicationInfo", {}).get("pubGroupName", "")
                },
                "children": children,
                "companyTag": company_tags,
                "headlineInfo": {
                    "isUpdated": article_doc.get("headlineInfo", {}).get("isUpdated", None),
                    "updatedBy": article_doc.get("headlineInfo", {}).get("updatedBy", None),
                    "updatedOn": article_doc.get("headlineInfo", {}).get("updatedOn", None)
                },
                "link": article_doc.get("link", ""),
                "JPGLINK": article_doc.get("JPGLINK", ""),
                "HTMLLINK": article_doc.get("HTMLLINK", ""),
                "crossLanguageInvertedToken": article_doc.get("crossLanguageInvertedToken", "")
            }
            
            # Add articleStitch field only if stitch relationship exists
            if article_stitch:
                payload["articleStitch"] = article_stitch
                logger.info(f"Article {article_id}: Added articleStitch to ES payload (optimized) (isMain={article_stitch.get('isMainArticle')}, isChild={article_stitch.get('isChildArticle')})")
            
            return payload
            
        except Exception as e:
            logger.error(f"Error building optimized article payload: {e}")
            return None

    async def _process_large_social_batch(
        self, 
        social_feed_ids: List[int], 
        max_workers: int
    ) -> BatchRefreshResults:
        """Optimized bulk processing for social feed batches - BLAZING FAST (works for any size)."""
        try:
            # Pre-load master data cache if not loaded
            await self.master_cache.load_all_master_data(self.mongo_client, settings.MONGODB_DATABASE)
            
            # Bulk fetch all social feeds from MongoDB
            social_feeds = await self._bulk_fetch_social_feeds(social_feed_ids)
            
            # Bulk fetch all related data in parallel
            social_tags, social_similars = await asyncio.gather(
                self._bulk_fetch_social_tags(social_feed_ids),
                self._bulk_fetch_social_similars(social_feed_ids)
            )
            
            # Process in parallel chunks - optimize for maximum parallelism
            # Smaller chunks = more parallel processing = faster overall
            if len(social_feed_ids) <= 10:
                chunk_size = len(social_feed_ids)  # Single chunk for very small batches
            elif len(social_feed_ids) >= 500:
                # For large batches, use smaller chunks (50) to maximize parallelism
                chunk_size = 50
            elif len(social_feed_ids) >= 100:
                chunk_size = 25  # Smaller chunks for better parallelism
            else:
                chunk_size = max(10, len(social_feed_ids) // max_workers)
            
            chunks = [social_feed_ids[i:i + chunk_size] for i in range(0, len(social_feed_ids), chunk_size)]
            
            # Process chunks in parallel
            tasks = []
            for chunk in chunks:
                task = self._process_social_chunk(chunk, social_feeds, social_tags, social_similars, set())
                tasks.append(task)
            
            chunk_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Flatten results
            results = []
            for chunk_result in chunk_results:
                if isinstance(chunk_result, Exception):
                    logger.error(f"Chunk processing error: {chunk_result}")
                    continue
                results.extend(chunk_result)
            
            successful_count = sum(1 for r in results if r.success)
            failed_count = len(results) - successful_count
            
            return BatchRefreshResults(
                successful_count=successful_count,
                failed_count=failed_count,
                results=results
            )
            
        except Exception as e:
            logger.error(f"Error in large social batch processing: {e}")
            # Fallback to individual processing
            return await self._process_batch_social_feeds(social_feed_ids, max_workers)
    
    async def _bulk_fetch_social_feeds(self, social_feed_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Bulk fetch social feeds from MongoDB - OPTIMIZED with $in query."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.socialFeed
            
            # Use $in query - much faster
            # Add projection to only fetch needed fields (reduces data transfer)
            projection = {
                "_id": 1,
                "socialFeedId": 1,
                "feedInfo": 1,
                "feedData": 1,
                "publicationInfo": 1,
                "channelInfo": 1,
                "companyTag": 1,
                "image": 1,
                "video": 1,
                "searchInfo": 1,
                "socialMetrics": 1,
                "socialMediaInfo": 1,
                "location": 1,
                "author": 1,
                "extraSource": 1,
                "uploadInfo": 1,
                "qc": 1,
                "crossLanguageInvertedToken": 1
            }
            
            social_feeds_cursor = await asyncio.to_thread(
                collection.find,
                {"socialFeedId": {"$in": social_feed_ids}},
                projection
            )
            social_feeds = {}
            for doc in await asyncio.to_thread(list, social_feeds_cursor):
                social_feed_id = doc.get("socialFeedId")
                try:
                    social_feed_id_int = int(social_feed_id)
                except (ValueError, TypeError):
                    social_feed_id_int = social_feed_id
                social_feeds[social_feed_id_int] = doc
            
            # Try other formats for missing
            missing_ids = set(social_feed_ids) - set(social_feeds.keys())
            if missing_ids:
                from bson.int64 import Int64
                int64_ids = [Int64(sfid) for sfid in missing_ids]
                missing_cursor = await asyncio.to_thread(
                    collection.find,
                    {"socialFeedId": {"$in": int64_ids}}
                )
                for doc in await asyncio.to_thread(list, missing_cursor):
                    social_feed_id = doc.get("socialFeedId")
                    try:
                        social_feed_id_int = int(social_feed_id)
                    except (ValueError, TypeError):
                        social_feed_id_int = social_feed_id
                    social_feeds[social_feed_id_int] = doc
            
            logger.info(f"Bulk fetched {len(social_feeds)}/{len(social_feed_ids)} social feeds from MongoDB")
            return social_feeds
            
        except Exception as e:
            logger.error(f"Error bulk fetching social feeds: {e}")
            return {}
    
    async def _bulk_fetch_social_tags(self, social_feed_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Bulk fetch social feed tags from MongoDB - OPTIMIZED with $in query, field projections, and parallel queries."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.socialFeedTag
            
            # For batches >50, split into parallel queries for better performance
            # Lowered threshold from 200 to 50 to improve performance on smaller batches
            if len(social_feed_ids) > 50:
                chunk_size = 50
                chunks = [social_feed_ids[i:i + chunk_size] for i in range(0, len(social_feed_ids), chunk_size)]
                
                async def fetch_chunk(chunk_ids):
                    return await self._fetch_social_tags_chunk(collection, chunk_ids)
                
                chunk_results = await asyncio.gather(*[fetch_chunk(chunk) for chunk in chunks])
                
                # Merge results
                tags_by_social = {}
                for chunk_result in chunk_results:
                    tags_by_social.update(chunk_result)
            else:
                tags_by_social = await self._fetch_social_tags_chunk(collection, social_feed_ids)
            
            logger.info(f"Bulk fetched tags for {len(tags_by_social)} social feeds")
            return tags_by_social
            
        except Exception as e:
            logger.error(f"Error bulk fetching social tags: {e}")
            return {}
    
    async def _fetch_social_tags_chunk(self, collection, social_feed_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Fetch tags for a chunk of social feed IDs with optimized query and field projections."""
        from bson.int64 import Int64
        
        # Field projection - fetch all tag fields (like single endpoint does)
        # Don't use projection for tags to ensure we get all nested company fields
        # The single endpoint works because it gets full documents without projection
        projection = None  # No projection = get all fields like single endpoint
        
        tags_by_social = {}
        
        # Try int format first
        # Use find() without projection if projection is None (like single endpoint)
        if projection:
            tags_cursor = await asyncio.to_thread(
                collection.find,
                {"socialFeedId": {"$in": social_feed_ids}},
                projection
            )
        else:
            tags_cursor = await asyncio.to_thread(
                collection.find,
                {"socialFeedId": {"$in": social_feed_ids}}
            )
        
        for tag in await asyncio.to_thread(list, tags_cursor):
            social_feed_id = tag.get("socialFeedId")
            try:
                social_feed_id_int = int(social_feed_id)
            except (ValueError, TypeError):
                social_feed_id_int = social_feed_id
            
            if social_feed_id_int not in tags_by_social:
                tags_by_social[social_feed_id_int] = []
            tags_by_social[social_feed_id_int].append(tag)
        
        # Try Int64 format for missing IDs
        missing_ids = set(social_feed_ids) - set(tags_by_social.keys())
        if missing_ids:
            int64_ids = [Int64(sfid) for sfid in missing_ids]
            # Use find() without projection if projection is None
            if projection:
                missing_cursor = await asyncio.to_thread(
                    collection.find,
                    {"socialFeedId": {"$in": int64_ids}},
                    projection
                )
            else:
                missing_cursor = await asyncio.to_thread(
                    collection.find,
                    {"socialFeedId": {"$in": int64_ids}}
                )
            for tag in await asyncio.to_thread(list, missing_cursor):
                social_feed_id = tag.get("socialFeedId")
                try:
                    social_feed_id_int = int(social_feed_id)
                except (ValueError, TypeError):
                    social_feed_id_int = social_feed_id
                if social_feed_id_int not in tags_by_social:
                    tags_by_social[social_feed_id_int] = []
                tags_by_social[social_feed_id_int].append(tag)
        
        return tags_by_social
    
    async def _bulk_fetch_social_similars(self, social_feed_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Bulk fetch social feed similars from MongoDB - OPTIMIZED with $in query."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.socialFeedSimilar
            
            # Use $in query - much faster
            similars_cursor = await asyncio.to_thread(
                collection.find,
                {"parentSocialFeedId": {"$in": social_feed_ids}}
            )
            similars_by_social = {}
            for doc in await asyncio.to_thread(list, similars_cursor):
                parent_id = doc.get("parentSocialFeedId")
                try:
                    parent_id_int = int(parent_id)
                except (ValueError, TypeError):
                    parent_id_int = parent_id
                similars_by_social[parent_id_int] = doc
            
            # Try other formats for missing
            missing_ids = set(social_feed_ids) - set(similars_by_social.keys())
            if missing_ids:
                from bson.int64 import Int64
                int64_ids = [Int64(sfid) for sfid in missing_ids]
                missing_cursor = await asyncio.to_thread(
                    collection.find,
                    {"parentSocialFeedId": {"$in": int64_ids}}
                )
                for doc in await asyncio.to_thread(list, missing_cursor):
                    parent_id = doc.get("parentSocialFeedId")
                    try:
                        parent_id_int = int(parent_id)
                    except (ValueError, TypeError):
                        parent_id_int = parent_id
                    similars_by_social[parent_id_int] = doc
            
            logger.info(f"Bulk fetched similars for {len(similars_by_social)} social feeds")
            return similars_by_social
            
        except Exception as e:
            logger.error(f"Error bulk fetching social similars: {e}")
            return {}
    
    async def _process_social_chunk(
        self, 
        social_feed_ids: List[int], 
        social_feeds: Dict[int, Dict[str, Any]], 
        social_tags: Dict[int, List[Dict[str, Any]]], 
        social_similars: Dict[int, Dict[str, Any]],
        refreshed_ids: Optional[set] = None
    ) -> List[RefreshResult]:
        """Process a chunk of social feeds with pre-fetched data and refresh children."""
        if refreshed_ids is None:
            refreshed_ids = set()
        
        results = []
        child_ids_to_refresh = set()
        
        # First pass: process requested social feeds and collect child IDs
        es_documents = []  # Collect documents for bulk indexing
        social_timings = {}  # Track timing per social feed
        
        for social_feed_id in social_feed_ids:
            if social_feed_id in refreshed_ids:
                continue
                
            start_time = time.time()
            refreshed_ids.add(social_feed_id)
            social_timings[social_feed_id] = start_time
            
            try:
                social_doc = social_feeds.get(social_feed_id)
                if not social_doc:
                    results.append(RefreshResult(
                        document_id=str(social_feed_id),
                        document_type="social",
                        success=False,
                        message=f"Social feed {social_feed_id} not found in MongoDB",
                        processing_time=time.time() - start_time,
                        timestamp=datetime.utcnow()
                    ))
                    continue
                
                # Build payload with pre-fetched data
                payload = await self._build_social_es_payload_optimized(
                    social_doc, 
                    social_tags.get(social_feed_id, []), 
                    social_similars.get(social_feed_id)
                )
                
                if not payload:
                    results.append(RefreshResult(
                        document_id=str(social_feed_id),
                        document_type="social",
                        success=False,
                        message=f"Failed to build payload for social feed {social_feed_id}",
                        processing_time=time.time() - start_time,
                        timestamp=datetime.utcnow()
                    ))
                    continue
                
                # Collect for bulk indexing instead of individual indexing
                es_documents.append({
                    "id": str(social_feed_id),
                    "body": payload,
                    "social_feed_id": social_feed_id,
                    "start_time": start_time
                })
                
                # Collect child IDs for later refresh
                social_similar = social_similars.get(social_feed_id)
                if social_similar and social_similar.get('child'):
                    for child in social_similar.get("child", []):
                        child_social_feed_id = child.get("socialFeedId")
                        if child_social_feed_id:
                            try:
                                child_id_int = int(child_social_feed_id)
                                if child_id_int not in refreshed_ids:
                                    child_ids_to_refresh.add(child_id_int)
                            except (ValueError, TypeError):
                                if child_social_feed_id not in refreshed_ids:
                                    child_ids_to_refresh.add(child_social_feed_id)
                
            except Exception as e:
                logger.error(f"Error processing social feed {social_feed_id}: {e}")
                results.append(RefreshResult(
                    document_id=str(social_feed_id),
                    document_type="social",
                    success=False,
                    message=f"Error refreshing social feed {social_feed_id}: {str(e)}",
                    processing_time=time.time() - start_time,
                    timestamp=datetime.utcnow()
                ))
        
        # Bulk index all documents to Elasticsearch at once (much faster than individual indexing)
        if es_documents:
            bulk_start = time.time()
            try:
                # Use optimized bulk indexing
                await self._bulk_index_to_elasticsearch_optimized(
                    settings.ES_REFRESH_SOCIAL_INDEX,
                    [{"id": doc["id"], "body": doc["body"]} for doc in es_documents]
                )
                bulk_time = time.time() - bulk_start
                logger.info(f"[PERF] Bulk indexed {len(es_documents)} social feeds in {bulk_time:.3f}s ({len(es_documents)/bulk_time:.0f} docs/sec)")
                
                # Create success results for all bulk-indexed social feeds
                for doc in es_documents:
                    social_feed_id = doc["social_feed_id"]
                    start_time = doc["start_time"]
                    results.append(RefreshResult(
                        document_id=str(social_feed_id),
                        document_type="social",
                        success=True,
                        message=f"Successfully refreshed social feed {social_feed_id}",
                        processing_time=time.time() - start_time,
                        timestamp=datetime.utcnow()
                    ))
            except Exception as e:
                logger.error(f"Error bulk indexing social feeds: {e}")
                # Mark all as failed
                for doc in es_documents:
                    social_feed_id = doc["social_feed_id"]
                    start_time = doc["start_time"]
                    results.append(RefreshResult(
                        document_id=str(social_feed_id),
                        document_type="social",
                        success=False,
                        message=f"Error bulk indexing social feed {social_feed_id}: {str(e)}",
                        processing_time=time.time() - start_time,
                        timestamp=datetime.utcnow()
                    ))
        
        # Second pass: refresh child social feeds in BATCH (much faster than individual)
        # Skip child refresh if batch is very small to avoid overhead
        if child_ids_to_refresh and len(child_ids_to_refresh) > 0:
            child_ids_list = list(child_ids_to_refresh)
            
            # Filter out already refreshed IDs to prevent infinite loops
            child_ids_to_process = [cid for cid in child_ids_list if cid not in refreshed_ids]
            
            if child_ids_to_process:
                child_start_time = time.time()
                
                # For very small child batches, skip refresh to avoid overhead
                if len(child_ids_to_process) <= 2:
                    logger.debug(f"Skipping refresh of {len(child_ids_to_process)} child social feeds (too small, will be refreshed as main feeds)")
                    # Mark as seen to prevent loops
                    for child_id in child_ids_to_process:
                        refreshed_ids.add(child_id)
                else:
                    logger.info(f"Refreshing {len(child_ids_to_process)} child social feeds from batch (using optimized batch processing)")
                    
                    # Use batch refresh - now uses optimized path for ALL sizes
                    try:
                        child_batch_results = await self.refresh_social_feeds_batch(child_ids_to_process, max_workers=settings.MAX_WORKERS)
                        results.extend(child_batch_results.results)
                        
                        # Add all child IDs to refreshed set to prevent re-processing
                        refreshed_ids.update(child_ids_to_process)
                        
                        child_elapsed = time.time() - child_start_time
                        logger.info(f"[PERF] Completed refreshing {len(child_ids_to_process)} child social feeds in {child_elapsed:.2f}s ({len(child_ids_to_process)/child_elapsed:.1f} feeds/sec)")
                    except Exception as e:
                        logger.error(f"Error batch refreshing child social feeds: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        # Fallback: mark as failed
                        for child_id in child_ids_to_process:
                            results.append(RefreshResult(
                                document_id=str(child_id),
                                document_type="social",
                                success=False,
                                message=f"Error batch refreshing child: {str(e)}",
                                processing_time=0.0,
                                timestamp=datetime.utcnow()
                            ))
        
        return results
    
    async def _build_social_es_payload_optimized(
        self, 
        social_doc: Dict[str, Any], 
        social_tags: List[Dict[str, Any]], 
        social_similar: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Optimized social feed payload building with pre-fetched data."""
        try:
            social_feed_id = social_doc.get("socialFeedId")
            
            # Build Children Info (using pre-fetched data)
            children = []
            if social_similar and social_similar.get('child'):
                for child in social_similar.get("child", []):
                    if not child.get("socialFeedId"):
                        continue
                    children.append({
                        "socialFeedId": child.get("socialFeedId"),
                        "publicationId": child.get("publicationId") if child.get("publicationId") and str(child.get("publicationId")).strip() else None,
                        "publicationName": child.get("publicationName", "")
                    })
            else:
                children = social_doc.get('children', [])

            # Build Company Tags (using pre-fetched data)
            company_tags = []
            company_client_tags = {
                tag.get("id"): tag.get("clientArticleTag", [])
                for tag in social_doc.get("companyTag", [])
            }
            
            for tag in social_tags:
                # Get companyId and companyName from tag document - try multiple possible structures
                # 1. Direct fields (companyId, companyName)
                company_id = str(tag.get("companyId", "")) if tag.get("companyId") else ""
                company_name = tag.get("companyName", "") if tag.get("companyName") else ""
                
                # 2. Nested company object (company.id, company.name)
                if not company_id:
                    company_obj = tag.get("company", {})
                    if company_obj:
                        company_id = str(company_obj.get("id", "")) if company_obj.get("id") else ""
                        if not company_id:
                            company_id = str(company_obj.get("companyId", "")) if company_obj.get("companyId") else ""
                        if not company_id:
                            company_id = str(company_obj.get("_id", "")) if company_obj.get("_id") else ""
                        
                        if not company_name:
                            company_name = company_obj.get("name", "") if company_obj.get("name") else ""
                            if not company_name:
                                company_name = company_obj.get("companyName", "") if company_obj.get("companyName") else ""
                
                # 3. Enrich with master cache if companyId exists but name is missing
                if company_id and not company_name:
                    company_info = self.master_cache.get_company(company_id)
                    if company_info:
                        company_name = company_info.get("companyName", "") or company_info.get("name", "")
                
                # Log if company info is still missing for debugging
                if not company_id:
                    logger.debug(f"Tag missing companyId: {tag.get('tagInfo', {}).get('keyword', 'unknown')}")
                
                client_article_tags = company_client_tags.get(company_id, [])
                raw_keyword = tag.get("tagInfo", {}).get("keyword", "")
                
                company_tags.append({
                    "id": company_id,
                    "name": company_name,
                    "clientArticleTag": client_article_tags,
                    "tagInfo": {
                        "keyword": raw_keyword,
                        "reportingTone": tag.get("tagInfo", {}).get("reportingTone", 0),
                        "prominence": tag.get("tagInfo", {}).get("prominence", 0),
                        "reportingSubject": tag.get("tagInfo", {}).get("reportingSubject", ""),
                        "subcategory": tag.get("tagInfo", {}).get("subcategory", ""),
                        "mailerReportingSubject": tag.get("tagInfo", {}).get("mailerReportingSubject", ""),
                        "remarks": tag.get("tagInfo", {}).get("remarks", ""),
                        "detailSummary": tag.get("tagInfo", {}).get("detailSummary", ""),
                        "detailId": tag.get("tagInfo", {}).get("detailId", 0)
                    },
                    "qc": {
                        "qc1Status": tag.get("qc", {}).get("qc1Status", False),
                        "qc2Status": tag.get("qc", {}).get("qc2Status", False),
                        "qc3Status": tag.get("qc", {}).get("qc3Status", False)
                    }
                })

            # Build the complete payload
            payload = {
                "socialFeedId": social_feed_id,
                "feedInfo": {
                    "txnNumber": social_doc.get("feedInfo", {}).get("txnNumber", 0),
                    "socialFeedType": social_doc.get("feedInfo", {}).get("socialFeedType", 0),
                    "link": social_doc.get("feedInfo", {}).get("link", ""),
                    "isActive": social_doc.get("feedInfo", {}).get("isActive", False)
                },
                "feedData": {
                    "headlineSnippet": social_doc.get("feedData", {}).get("headlineSnippet", ""),
                    "summarySnippet": social_doc.get("feedData", {}).get("summarySnippet", ""),
                    "headlines": social_doc.get("feedData", {}).get("headline", ""),
                    "summary": social_doc.get("feedData", {}).get("summary", ""),
                    "feedDate": self._extract_mongo_date(social_doc.get("feedData", {}).get("feedDate")),
                    "feedDateTime": self._extract_mongo_date(social_doc.get("feedData", {}).get("feedDateTime")),
                    "articleDateNumber": social_doc.get("feedData", {}).get("articleDateNumber", 0),
                    "language": social_doc.get("feedData", {}).get("language", ""),
                    "text": social_doc.get("feedData", {}).get("text", "")
                },
                "publicationInfo": {
                    "id": social_doc.get("publicationInfo", {}).get("id", ""),
                    "name": social_doc.get("publicationInfo", {}).get("name", ""),
                    "publicationCategory": social_doc.get("publicationInfo", {}).get("publicationCategory", "News")
                },
                "channelInfo": {
                    "id": social_doc.get("channelInfo", {}).get("id", None),
                    "name": social_doc.get("channelInfo", {}).get("name", None)
                },
                "companyTag": company_tags,
                "children": children,
                "image": {
                    "hasImage": social_doc.get("image", {}).get("hasImage", False),
                    "url": social_doc.get("image", {}).get("url", ""),
                    "filename": social_doc.get("image", {}).get("filename", "")
                },
                "video": {
                    "hasVideo": social_doc.get("video", {}).get("hasVideo", False)
                },
                "searchInfo": {
                    "keywordMatched": social_doc.get("searchInfo", {}).get("keywordMatched", []),
                    "sourceType": social_doc.get("searchInfo", {}).get("sourceType", "")
                },
                "socialMetrics": {
                    "wordCount": social_doc.get("socialMetrics", {}).get("wordCount", 0),
                    "sentiment": social_doc.get("socialMetrics", {}).get("sentiment", ""),
                    "reach": social_doc.get("socialMetrics", {}).get("reach", 0),
                    "engagement": social_doc.get("socialMetrics", {}).get("engagement", 0),
                    "urlViews": social_doc.get("socialMetrics", {}).get("urlViews", 0)
                },
                "socialMediaInfo": {
                    "alexaStats": {
                        "pageViews": social_doc.get("socialMediaInfo", {}).get("alexaStats", {}).get("pageViews", 0),
                        "uniqueVisitors": social_doc.get("socialMediaInfo", {}).get("alexaStats", {}).get("uniqueVisitors", 0)
                    },
                    "facebook": {},
                    "instagram": {},
                    "youtube": {},
                    "pinterest": {},
                    "twitter": {}
                },
                "location": social_doc.get("location", {}),
                "author": {
                    "id": social_doc.get("author", {}).get("id", ""),
                    "name": social_doc.get("author", {}).get("name", ""),
                    "gender": social_doc.get("author", {}).get("gender", "")
                },
                "extraSource": social_doc.get("extraSource", {}),
                "uploadInfo": {
                    "uploadDate": self._extract_mongo_date(social_doc.get("uploadInfo", {}).get("uploadDate")),
                    "uploadDateNumber": social_doc.get("uploadInfo", {}).get("uploadDateNumber", 0)
                },
                "qc": {
                    "qc1Status": social_doc.get("qc", {}).get("qc1Status", False),
                    "qc2Status": social_doc.get("qc", {}).get("qc2Status", False)
                },
                "crossLanguageInvertedToken": social_doc.get("crossLanguageInvertedToken", "")
            }
            
            return payload
            
        except Exception as e:
            logger.error(f"Error building optimized social payload: {e}")
            return None

    async def cleanup(self):
        """Cleanup resources."""
        try:
            if self.executor:
                self.executor.shutdown(wait=True)
            
            if self.mongo_client:
                self.mongo_client.close()
            
            if self.es_client:
                self.es_client.close()
            
            self._initialized = False
            logger.info("RefreshService cleanup completed")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
