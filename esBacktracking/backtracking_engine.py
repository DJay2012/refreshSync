#!/usr/bin/env python3
"""
Backtracking Engine for esPreview Simplified Version

This module extends the simplified esPreview system to support historical data processing
and MongoDB tag creation for backtracking scenarios.

Features:
- Process historical articles by date range
- Create MongoDB tags for new matches
- Support for both print articles and social feeds
- Batch processing for performance
- Configurable date ranges and company IDs
"""

import sys
import os
import json
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

# Load environment variables from .env file (local directory)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

# Add the current directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from espreview import ESPreviewEngine, ESPreviewConfig
from pymongo import MongoClient, UpdateOne, InsertOne
from pymongo.errors import BulkWriteError
from bson.int64 import Int64
import psycopg2
from psycopg2.extras import RealDictCursor

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class BacktrackingConfig:
    """Configuration for backtracking operations."""
    
    # Date range settings
    start_date: str = "2025-01-01"
    end_date: str = "2025-01-10"
    days_back: int = 10
    
    # Processing settings
    batch_size: int = 100
    max_workers: int = 4
    parallel_processing: bool = True
    process_print: bool = True
    process_online: bool = True
    
    # Company settings
    company_ids: List[str] = None
    language: str = "en"
    
    # MongoDB settings
    mongo_uri: str = os.getenv("PG_MONGO_URI", "mongodb://localhost:27017/")
    mongo_db: str = os.getenv("PG_MONGO_DB", "pnq")
    
    # PostgreSQL settings
    pg_host: str = os.getenv("PG_HOST", "localhost")
    pg_port: int = 5432
    pg_db: str = "your_database"
    pg_user: str = "your_user"
    pg_password: str = "your_password"
    
    # Output settings
    dry_run: bool = False
    verbose: bool = True
    save_results: bool = True
    results_file: str = "backtracking_results.json"
    
    def __post_init__(self):
        if self.company_ids is None:
            self.company_ids = []

# ============================================================================
# MONGODB TAG CREATION
# ============================================================================

class MongoCheckpointManager:
    """Manages checkpoints in MongoDB."""
    
    def __init__(self, mongo_db, checkpoint_id: str):
        self.mongo_db = mongo_db
        self.checkpoint_id = checkpoint_id
        self.collection = mongo_db.backtrackingCheckpoints
    
    def load_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Load checkpoint from MongoDB."""
        try:
            checkpoint_doc = self.collection.find_one({"_id": self.checkpoint_id})
            if checkpoint_doc:
                # Remove _id from the document for easier handling
                checkpoint_doc.pop("_id", None)
                return checkpoint_doc
            return None
        except Exception as e:
            print(f"Error loading checkpoint from MongoDB: {e}")
            return None
    
    def save_checkpoint(self, checkpoint_data: Dict[str, Any]):
        """Save checkpoint to MongoDB."""
        try:
            checkpoint_data["_id"] = self.checkpoint_id
            checkpoint_data["updated_at"] = datetime.now().isoformat()
            
            self.collection.replace_one(
                {"_id": self.checkpoint_id},
                checkpoint_data,
                upsert=True
            )
            if self.mongo_db is not None and hasattr(self.mongo_db, 'verbose'):
                print(f"Checkpoint saved to MongoDB: {self.checkpoint_id}")
        except Exception as e:
            print(f"Error saving checkpoint to MongoDB: {e}")
    
    def clear_checkpoint(self):
        """Clear checkpoint from MongoDB."""
        try:
            self.collection.delete_one({"_id": self.checkpoint_id})
            print(f"Checkpoint cleared from MongoDB: {self.checkpoint_id}")
        except Exception as e:
            print(f"Error clearing checkpoint from MongoDB: {e}")

class MongoTagCreator:
    """Handles MongoDB tag creation for backtracking results."""
    
    def __init__(self, config: BacktrackingConfig):
        self.config = config
        self.mongo_client = None
        self.mongo_db = None
        self._connect_mongo()
        
        # Cache for company names from companyMaster collection
        self.company_name_cache = {}
    
    def _connect_mongo(self):
        """Connect to MongoDB."""
        try:
            self.mongo_client = MongoClient(
                self.config.mongo_uri,
                serverSelectionTimeoutMS=5000
            )
            self.mongo_db = self.mongo_client[self.config.mongo_db]
            
            # Test connection
            self.mongo_client.admin.command('ping')
            print(f"Connected to MongoDB: {self.config.mongo_uri}")
            
        except Exception as e:
            print(f"MongoDB connection failed: {e}")
            self.mongo_client = None
            self.mongo_db = None
    
    def get_company_name(self, company_id: str) -> str:
        """Get company name from companyMaster collection with caching."""
        
        # Check cache first
        if company_id in self.company_name_cache:
            return self.company_name_cache[company_id]
        
        if self.mongo_db is None:
            print(f"MongoDB not connected, using company_id as name for {company_id}")
            return company_id
        
        try:
            # Query companyMaster collection
            company_doc = self.mongo_db.companyMaster.find_one({"_id": company_id})
            
            if company_doc and "companyInfo" in company_doc:
                company_name = company_doc["companyInfo"].get("companyName", company_id)
                # Cache the result
                self.company_name_cache[company_id] = company_name
                return company_name
            else:
                print(f"Company not found in companyMaster: {company_id}")
                # Cache the fallback
                self.company_name_cache[company_id] = company_id
                return company_id
                
        except Exception as e:
            print(f"Error getting company name for {company_id}: {e}")
            # Cache the fallback
            self.company_name_cache[company_id] = company_id
            return company_id
    
    def create_article_tag(self, article_id: str, pg_article_id: int, company_id: str, 
                          article_date: str, tag_data: Dict[str, Any], 
                          sources: Dict[str, List[str]], content: str = None, is_new: bool = True) -> Dict[str, Any]:
        """Create MongoDB tag document for article."""
        
        tag_id = f"{article_id}{company_id}"
        current_timestamp = datetime.now()
        
        # Get company name from companyMaster collection
        company_name = self.get_company_name(company_id)
        
        # Parse article date - slice to first 10 chars to handle ISO datetime strings from ES
        try:
            if article_date:
                article_date_obj = datetime.strptime(str(article_date)[:10], '%Y-%m-%d')
            else:
                article_date_obj = current_timestamp
        except:
            article_date_obj = current_timestamp
        
        # Extract source flags
        headline_flag = any(field == 'headline' for source_locations in sources.values() for field in source_locations)
        content_flag = any(field == 'content' for source_locations in sources.values() for field in source_locations)
        summary_flag = any(field == 'summary' for source_locations in sources.values() for field in source_locations)
        sources_json = json.dumps(sources)
        
        # Create detailSummary if content is provided
        detail_summary = None
        if content and tag_data.get('KEYWORDS'):
            try:
                # Import the sentence extractor
                import sys
                import os
                # Add elasticTagging/src to path if not already there
                current_file_dir = os.path.dirname(os.path.abspath(__file__))  # esBacktracking directory
                refresh_es_api_dir = os.path.dirname(current_file_dir)  # refresh_es_api directory
                pnq_etl_server_dir = os.path.dirname(refresh_es_api_dir)  # pnqETLServer directory
                elasticTagging_src_path = os.path.join(pnq_etl_server_dir, 'elasticTagging', 'src')
                if elasticTagging_src_path not in sys.path:
                    sys.path.insert(0, elasticTagging_src_path)
                from utils.sentence_extractor import create_detail_summary_for_tag
                print(f"DEBUG: Creating detailSummary for {tag_id}")
                print(f"DEBUG: Content length: {len(content) if content else 0}")
                print(f"DEBUG: Keywords: {tag_data.get('KEYWORDS', 'None')}")
                print(f"DEBUG: Tag data: {tag_data}")
                detail_summary = create_detail_summary_for_tag(tag_data, content)
                print(f"DEBUG: DetailSummary result: {detail_summary[:100] if detail_summary else 'None'}...")
            except Exception as e:
                print(f"Warning: Failed to create detailSummary for article tag {tag_id}: {e}")
                import traceback
                traceback.print_exc()
                detail_summary = None
        else:
            print(f"DEBUG: Skipping detailSummary for {tag_id} - content: {bool(content)}, keywords: {bool(tag_data.get('KEYWORDS'))}")
        
        tag_doc = {
            "_id": tag_id,
            "articleId": int(article_id),  # Convert to Int32
            "articleDate": article_date_obj.replace(hour=0, minute=0, second=0, microsecond=0),
            "sortOrder": 1,
            "sourceArticleId": pg_article_id,
            "company": {"id": company_id, "name": company_name},
            "tagInfo": {
                "keyword": tag_data.get('KEYWORDS', ''),
                "reportingTone": None,
                "reportingSubject": None,
                "subcategory": None,
                "prominence": None,
                "detailSummary": detail_summary,
                "adArticleDate": article_date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
            },
            "qc": {
                "qc1Status": False,
                "qc2Status": False,
                "qc3Status": False,
                "qc1": [{"name": None, "on": None}],
                "qc2": [{"name": None, "on": None}],
                "qc3": [{"name": None, "on": None}]
            },
            "uploadInfo": {"ipadress": None, "macaddress": None},
            "auditInfo": {
                "created": {"name": "esBacktrack", "on": current_timestamp},
                "modified": [] if is_new else [{"name": "esBacktrack", "on": current_timestamp}]
            },
            "sourceTagInfo": {
                "elasticTagger": True,
                "isHeadlineTagged": headline_flag,
                "isContentTagged": content_flag,
                "isSummaryTagged": summary_flag,
                "source": sources_json
            }
        }
        
        return tag_doc
    
    def create_social_tag(self, social_feed_id: str, pg_social_feed_id: int, company_id: str,
                         feed_date: str, tag_data: Dict[str, Any],
                         sources: Dict[str, List[str]], content: str = None, is_new: bool = True) -> Dict[str, Any]:
        """Create MongoDB tag document for social feed."""
        
        tag_id = f"{social_feed_id}{company_id}"
        current_timestamp = datetime.now()
        
        # Get company name from companyMaster collection
        company_name = self.get_company_name(company_id)
        
        # Parse feed date - slice to first 10 chars to handle ISO datetime strings from ES
        try:
            if feed_date:
                feed_date_obj = datetime.strptime(str(feed_date)[:10], '%Y-%m-%d')
            else:
                feed_date_obj = current_timestamp
        except:
            feed_date_obj = current_timestamp
        
        # Extract source flags
        headline_flag = any(field == 'headline' for source_locations in sources.values() for field in source_locations)
        content_flag = any(field == 'content' for source_locations in sources.values() for field in source_locations)
        summary_flag = any(field == 'summary' for source_locations in sources.values() for field in source_locations)
        sources_json = json.dumps(sources)
        
        # Create detailSummary if content is provided
        detail_summary = None
        if content and tag_data.get('KEYWORDS'):
            try:
                # Import the sentence extractor
                import sys
                import os
                # Add elasticTagging/src to path if not already there
                current_file_dir = os.path.dirname(os.path.abspath(__file__))  # esBacktracking directory
                refresh_es_api_dir = os.path.dirname(current_file_dir)  # refresh_es_api directory
                pnq_etl_server_dir = os.path.dirname(refresh_es_api_dir)  # pnqETLServer directory
                elasticTagging_src_path = os.path.join(pnq_etl_server_dir, 'elasticTagging', 'src')
                if elasticTagging_src_path not in sys.path:
                    sys.path.insert(0, elasticTagging_src_path)
                from utils.sentence_extractor import create_detail_summary_for_tag
                detail_summary = create_detail_summary_for_tag(tag_data, content)
            except Exception as e:
                print(f"Warning: Failed to create detailSummary for social feed tag {tag_id}: {e}")
                detail_summary = None
        
        tag_doc = {
            "_id": tag_id,
            "socialFeedId": int(social_feed_id),  # Convert to INT64
            "feedDate": feed_date_obj.replace(hour=0, minute=0, second=0, microsecond=0),
            "company": {"id": company_id, "name": company_name},
            "tagInfo": {
                "keyword": tag_data.get('KEYWORDS', ''),
                "reportingTone": None,
                "reportingSubject": None,
                "subcategory": None,
                "prominence": None,
                "detailSummary": detail_summary,
                "adFeedDate": feed_date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
            },
            "qc": {
                "qc1Status": False,
                "qc2Status": False,
                "qc3Status": False,
                "qc1": [{"name": None, "on": None}],
                "qc2": [{"name": None, "on": None}],
                "qc3": [{"name": None, "on": None}]
            },
            "auditInfo": {
                "created": {"name": "esBacktrack", "on": current_timestamp},
                "modified": [] if is_new else [{"name": "esBacktrack", "on": current_timestamp}]
            },
            "sourceTagInfo": {
                "elasticTagger": True,
                "isHeadlineTagged": headline_flag,
                "isContentTagged": content_flag,
                "isSummaryTagged": summary_flag,
                "source": sources_json
            },
            "sourceArticleId": pg_social_feed_id
        }
        
        return tag_doc
    
    def ensure_article_exists_in_mongo(self, pg_articleid: int, headline: str, summary: str, content: str, language: str, article_date: str = None) -> str:
        """Ensure article exists in MongoDB, create if missing."""
        try:
            if self.mongo_db is None:
                return None
            
            article_col = self.mongo_db["article"]

            # Check if article already exists - try multiple field names since ETL pipeline
            # uses articleId/_id for the PG article ID while sourceArticleId may differ
            existing_article = article_col.find_one({
                "$or": [
                    {"_id": pg_articleid},
                    {"articleId": pg_articleid},
                    {"sourceArticleId": pg_articleid}
                ]
            })
            if existing_article:
                return existing_article["_id"]
            
            # Create new article using Elasticsearch _id as MongoDB _id
            article_id = str(pg_articleid)  # Use PG article ID as MongoDB _id
            
            # Parse article date - slice to first 10 chars to handle ISO datetime strings from ES
            try:
                if article_date:
                    article_date_obj = datetime.strptime(str(article_date)[:10], '%Y-%m-%d')
                else:
                    article_date_obj = datetime.now()
            except:
                article_date_obj = datetime.now()
            
            article_doc = {
                "_id": article_id,
                "sourceArticleId": pg_articleid,
                "articleData": {
                    "headlines": headline or "",
                    "summary": summary or "",
                    "content": content or "",
                    "language": language or "en"
                },
                "articleInfo": {
                    "articleDate": article_date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
                },
                "companyTag": []
            }
            
            article_col.insert_one(article_doc)
            print(f"Created new MongoDB article: {article_id} (PG: {pg_articleid})")
            return article_id
            
        except Exception as e:
            print(f"Error ensuring article exists in MongoDB: {e}")
            return None
    
    def ensure_social_feed_exists_in_mongo(self, pg_socialfeedid: int, headline: str, summary: str, content: str, language: str, feed_date: str = None) -> str:
        """Ensure social feed exists in MongoDB, create if missing."""
        try:
            if self.mongo_db is None:
                return None
            
            social_feed_col = self.mongo_db["socialFeed"]

            # Check if social feed already exists - try multiple field names since ETL pipeline
            # uses socialFeedId/_id for the PG feed ID while sourceArticleId may differ
            existing_feed = social_feed_col.find_one({
                "$or": [
                    {"_id": pg_socialfeedid},
                    {"socialFeedId": pg_socialfeedid},
                    {"sourceArticleId": Int64(pg_socialfeedid)}
                ]
            })
            if existing_feed:
                return existing_feed["_id"]
            
            # Create new social feed using Elasticsearch _id as MongoDB _id
            social_feed_id = str(pg_socialfeedid)  # Use PG social feed ID as MongoDB _id
            
            # Parse feed date - slice to first 10 chars to handle ISO datetime strings from ES
            try:
                if feed_date:
                    feed_date_obj = datetime.strptime(str(feed_date)[:10], '%Y-%m-%d')
                else:
                    feed_date_obj = datetime.now()
            except:
                feed_date_obj = datetime.now()
            
            feed_doc = {
                "_id": social_feed_id,
                "sourceArticleId": Int64(pg_socialfeedid),
                "feedData": {
                    "headline": headline or "",
                    "summary": summary or "",
                    "content": content or "",
                    "language": language or "en",
                    "feedDate": feed_date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
                },
                "companyTag": []
            }
            
            social_feed_col.insert_one(feed_doc)
            print(f"Created new MongoDB social feed: {social_feed_id} (PG: {pg_socialfeedid})")
            return social_feed_id
            
        except Exception as e:
            print(f"Error ensuring social feed exists in MongoDB: {e}")
            return None
    
    def save_tags_to_mongo(self, tag_results: List[Dict[str, Any]], update_type: int = 1) -> bool:
        """Save tags to MongoDB in bulk."""
        
        if self.mongo_db is None:
            print("MongoDB not connected, skipping tag creation")
            return False
        
        if self.config.dry_run:
            print(f"DRY RUN: Would save {len(tag_results)} tags to MongoDB")
            return True
        
        try:
            tag_col = self.mongo_db["articleTag"]
            social_tag_col = self.mongo_db["socialFeedTag"]
            article_col = self.mongo_db["article"]
            social_feed_col = self.mongo_db["socialFeed"]
            
            # Prepare bulk operations
            tag_operations = []
            social_tag_operations = []
            article_operations = []
            social_feed_operations = []
            
            # Check existing tags if update_type == 1 - check both articleTag and socialFeedTag collections
            existing_tag_ids = set()
            if update_type == 1:
                tag_ids_to_check = [tag_result['tag_id'] for tag_result in tag_results]
                if tag_ids_to_check:
                    # Check articleTag collection
                    existing_article_tags = tag_col.find({"_id": {"$in": tag_ids_to_check}}, {"_id": 1})
                    existing_tag_ids.update({doc["_id"] for doc in existing_article_tags})
                    
                    # Check socialFeedTag collection
                    existing_social_tags = social_tag_col.find({"_id": {"$in": tag_ids_to_check}}, {"_id": 1})
                    existing_tag_ids.update({doc["_id"] for doc in existing_social_tags})
            
            for tag_result in tag_results:
                tag_id = tag_result['tag_id']
                tag_doc = tag_result['tag_doc']
                is_article = tag_result.get('is_article', True)
                
                if is_article:
                    if update_type == 1 and tag_id not in existing_tag_ids:
                        # Create new article tag
                        tag_operations.append(InsertOne(tag_doc))
                        
                        # Add to article's company tags list
                        article_operations.append(UpdateOne(
                            {"_id": tag_result['article_id'], "companyTag.id": {"$ne": tag_result['company_id']}},
                            {"$push": {"companyTag": {"id": tag_result['company_id'], "name": tag_result['company_name']}}}
                        ))
                    else:
                        # Update existing tag
                        current_timestamp = datetime.now()
                        tag_operations.append(UpdateOne(
                            {"_id": tag_id},
                            {
                                "$set": {
                                    "sourceTagInfo.elasticTagger": True,
                                    "sourceTagInfo.isHeadlineTagged": tag_doc['sourceTagInfo']['isHeadlineTagged'],
                                    "sourceTagInfo.isContentTagged": tag_doc['sourceTagInfo']['isContentTagged'],
                                    "sourceTagInfo.isSummaryTagged": tag_doc['sourceTagInfo']['isSummaryTagged'],
                                    "sourceTagInfo.source": tag_doc['sourceTagInfo']['source']
                                },
                                "$push": {
                                    "auditInfo.modified": {
                                        "name": "esBacktrack",
                                        "on": current_timestamp
                                    }
                                }
                            }
                        ))
                        # Also update article's companyTag array (even for existing tags)
                        article_operations.append(UpdateOne(
                            {"_id": tag_result['article_id'], "companyTag.id": {"$ne": tag_result['company_id']}},
                            {"$push": {"companyTag": {"id": tag_result['company_id'], "name": tag_result['company_name']}}}
                        ))
                else:
                    # Social feed tag
                    if update_type == 1 and tag_id not in existing_tag_ids:
                        social_tag_operations.append(InsertOne(tag_doc))
                        
                        # Add to social feed's company tags list
                        social_feed_operations.append(UpdateOne(
                            {"_id": tag_result['social_feed_id'], "companyTag.id": {"$ne": tag_result['company_id']}},
                            {"$addToSet": {"companyTag": {"id": tag_result['company_id'], "name": tag_result['company_name']}}}
                        ))
                    else:
                        # Update existing social feed tag
                        current_timestamp = datetime.now()
                        social_tag_operations.append(UpdateOne(
                            {"_id": tag_id},
                            {
                                "$set": {
                                    "sourceTagInfo.elasticTagger": True,
                                    "sourceTagInfo.isHeadlineTagged": tag_doc['sourceTagInfo']['isHeadlineTagged'],
                                    "sourceTagInfo.isContentTagged": tag_doc['sourceTagInfo']['isContentTagged'],
                                    "sourceTagInfo.isSummaryTagged": tag_doc['sourceTagInfo']['isSummaryTagged'],
                                    "sourceTagInfo.source": tag_doc['sourceTagInfo']['source']
                                },
                                "$push": {
                                    "auditInfo.modified": {
                                        "name": "esBacktrack",
                                        "on": current_timestamp
                                    }
                                }
                            }
                        ))
                        # Also update social feed's companyTag array (even for existing tags)
                        social_feed_operations.append(UpdateOne(
                            {"_id": tag_result['social_feed_id'], "companyTag.id": {"$ne": tag_result['company_id']}},
                            {"$addToSet": {"companyTag": {"id": tag_result['company_id'], "name": tag_result['company_name']}}}
                        ))
            
            # Execute bulk operations with error handling for duplicates
            results = []
            if tag_operations:
                try:
                    result = tag_col.bulk_write(tag_operations, ordered=False)  # ordered=False allows processing even if some fail
                    results.append(f"Article tags: {result.inserted_count} inserted, {result.modified_count} modified")
                    print(f"MongoDB operations completed: Article tags: {result.inserted_count} inserted, {result.modified_count} modified")
                except BulkWriteError as e:
                    # Handle duplicate key errors gracefully
                    write_errors_count = len(e.details.get('writeErrors', []))
                    successful = len(tag_operations) - write_errors_count
                    # Count duplicates vs other errors
                    duplicates = sum(1 for err in e.details.get('writeErrors', []) if err.get('code') == 11000)
                    other_errors = write_errors_count - duplicates
                    
                    if duplicates > 0:
                        print(f"MongoDB bulk write: {successful} article tags processed, {duplicates} duplicates skipped")
                    if other_errors > 0:
                        print(f"MongoDB bulk write: {other_errors} article tag operations failed (non-duplicate errors)")
                    
                    # Still use the successful operations from the result
                    if successful > 0:
                        results.append(f"Article tags: {successful} processed ({duplicates} duplicates skipped)")
                except Exception as e:
                    print(f"MongoDB bulk write error for article tags: {e}")
                    raise
            
            if social_tag_operations:
                try:
                    result = social_tag_col.bulk_write(social_tag_operations, ordered=False)  # ordered=False allows processing even if some fail
                    results.append(f"Social feed tags: {result.inserted_count} inserted, {result.modified_count} modified")
                    print(f"MongoDB operations completed: Social feed tags: {result.inserted_count} inserted, {result.modified_count} modified")
                except BulkWriteError as e:
                    # Handle duplicate key errors gracefully
                    write_errors_count = len(e.details.get('writeErrors', []))
                    successful = len(social_tag_operations) - write_errors_count
                    # Count duplicates vs other errors
                    duplicates = sum(1 for err in e.details.get('writeErrors', []) if err.get('code') == 11000)
                    other_errors = write_errors_count - duplicates
                    
                    if duplicates > 0:
                        print(f"MongoDB bulk write: {successful} social feed tags processed, {duplicates} duplicates skipped")
                    if other_errors > 0:
                        print(f"MongoDB bulk write: {other_errors} social feed tag operations failed (non-duplicate errors)")
                    
                    # Still use the successful operations from the result
                    if successful > 0:
                        results.append(f"Social feed tags: {successful} processed ({duplicates} duplicates skipped)")
                except Exception as e:
                    print(f"MongoDB bulk write error for social feed tags: {e}")
                    raise
            
            if article_operations:
                result = article_col.bulk_write(article_operations)
                results.append(f"Article updates: {result.modified_count} modified")
                print(f"MongoDB operations completed: Article companyTag arrays: {result.modified_count} updated")
            
            if social_feed_operations:
                result = social_feed_col.bulk_write(social_feed_operations)
                results.append(f"Social feed updates: {result.modified_count} modified")
                print(f"MongoDB operations completed: Social feed companyTag arrays: {result.modified_count} updated")
            
            print(f"MongoDB operations completed: {', '.join(results)}")
            return True
            
        except BulkWriteError as e:
            print(f"MongoDB bulk write error: {e}")
            return False
        except Exception as e:
            print(f"MongoDB save error: {e}")
            return False

# ============================================================================
# ELASTICSEARCH DATA RETRIEVAL
# ============================================================================

class ElasticsearchDataRetriever:
    """Handles Elasticsearch data retrieval for historical articles and social feeds."""
    
    def __init__(self, config: BacktrackingConfig, es_client):
        self.config = config
        self.es_client = es_client
        self.print_index = "printarticleindex"
        self.social_index = "socialfeedindex"
    
    def get_historical_articles(self, start_date: str, end_date: str, batch_size: int = 100) -> List[Dict[str, Any]]:
        """Get historical articles from Elasticsearch printarticleindex."""
        
        try:
            # Build date range query
            date_query = {
                "range": {
                    "articleData.articleDate": {
                        "gte": start_date,
                        "lte": end_date,
                        "format": "yyyy-MM-dd"
                    }
                }
            }
            
            # Build search request
            search_request = {
                "query": {
                    "bool": {
                        "must": [date_query]
                    }
                },
                "size": batch_size,
                "sort": [{"articleData.articleDate": {"order": "desc"}}],
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
            
            print(f"Searching printarticleindex for articles from {start_date} to {end_date}")
            response = self.es_client.search(index=self.print_index, body=search_request)
            
            articles = []
            for hit in response.get('hits', {}).get('hits', []):
                source = hit['_source']
                article_data = source.get('articleData', {})
                
                article = {
                    'articleid': article_data.get('articleId'),
                    'headlines': article_data.get('headlines', ''),
                    'summary': article_data.get('summary', ''),
                    'content': article_data.get('text', ''),
                    'articlelang': article_data.get('articleLang', 'en'),
                    'articledate': article_data.get('articleDate'),
                    'es_id': hit['_id']
                }
                articles.append(article)
            
            print(f"Found {len(articles)} articles in printarticleindex")
            return articles
            
        except Exception as e:
            print(f"Error retrieving articles from Elasticsearch: {e}")
            return []
    
    def get_historical_social_feeds(self, start_date: str, end_date: str, batch_size: int = 100) -> List[Dict[str, Any]]:
        """Get historical social feeds from Elasticsearch socialfeedindex."""
        
        try:
            # Build date range query
            date_query = {
                "range": {
                    "feedData.feedDate": {
                        "gte": start_date,
                        "lte": end_date,
                        "format": "yyyy-MM-dd"
                    }
                }
            }
            
            # Build search request
            search_request = {
                "query": {
                    "bool": {
                        "must": [date_query]
                    }
                },
                "size": batch_size,
                "sort": [{"feedData.feedDate": {"order": "desc"}}],
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
            
            print(f"Searching socialfeedindex for feeds from {start_date} to {end_date}")
            response = self.es_client.search(index=self.social_index, body=search_request)
            
            social_feeds = []
            for hit in response.get('hits', {}).get('hits', []):
                source = hit['_source']
                feed_data = source.get('feedData', {})
                
                social_feed = {
                    'SOCIALFEEDID': feed_data.get('socialFeedId'),
                    'HEADLINE': feed_data.get('headlines', ''),
                    'SUMMARY': feed_data.get('summary', ''),
                    'CONTENT': feed_data.get('text', ''),
                    'LANGUAGE': feed_data.get('language', 'en'),
                    'FEEDDATE': feed_data.get('feedDate'),
                    'es_id': hit['_id']
                }
                social_feeds.append(social_feed)
            
            print(f"Found {len(social_feeds)} social feeds in socialfeedindex")
            return social_feeds
            
        except Exception as e:
            print(f"Error retrieving social feeds from Elasticsearch: {e}")
            return []
    
    def get_articles_by_ids(self, article_ids: List[str]) -> List[Dict[str, Any]]:
        """Get specific articles by their IDs from Elasticsearch."""
        
        if not article_ids:
            return []
        
        try:
            search_request = {
                "query": {
                    "terms": {
                        "articleData.articleId": article_ids
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
            
            response = self.es_client.search(index=self.print_index, body=search_request)
            
            articles = []
            for hit in response.get('hits', {}).get('hits', []):
                source = hit['_source']
                article_data = source.get('articleData', {})
                
                article = {
                    'articleid': article_data.get('articleId'),
                    'headlines': article_data.get('headlines', ''),
                    'summary': article_data.get('summary', ''),
                    'content': article_data.get('text', ''),
                    'articlelang': article_data.get('articleLang', 'en'),
                    'articledate': article_data.get('articleDate'),
                    'es_id': hit['_id']
                }
                articles.append(article)
            
            return articles
            
        except Exception as e:
            print(f"Error retrieving articles by IDs from Elasticsearch: {e}")
            return []
    
    def get_social_feeds_by_ids(self, social_feed_ids: List[str]) -> List[Dict[str, Any]]:
        """Get specific social feeds by their IDs from Elasticsearch."""
        
        if not social_feed_ids:
            return []
        
        try:
            search_request = {
                "query": {
                    "terms": {
                        "feedData.socialFeedId": social_feed_ids
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
            
            response = self.es_client.search(index=self.social_index, body=search_request)
            
            social_feeds = []
            for hit in response.get('hits', {}).get('hits', []):
                source = hit['_source']
                feed_data = source.get('feedData', {})
                
                social_feed = {
                    'SOCIALFEEDID': feed_data.get('socialFeedId'),
                    'HEADLINE': feed_data.get('headlines', ''),
                    'SUMMARY': feed_data.get('summary', ''),
                    'CONTENT': feed_data.get('text', ''),
                    'LANGUAGE': feed_data.get('language', 'en'),
                    'FEEDDATE': feed_data.get('feedDate'),
                    'es_id': hit['_id']
                }
                social_feeds.append(social_feed)
            
            return social_feeds
            
        except Exception as e:
            print(f"Error retrieving social feeds by IDs from Elasticsearch: {e}")
            return []

# ============================================================================
# BACKTRACKING ENGINE
# ============================================================================

class BacktrackingEngine:
    """Main backtracking engine that orchestrates the entire process."""
    
    def __init__(self, config: BacktrackingConfig):
        self.config = config
        
        # Initialize components
        self.espreview_config = ESPreviewConfig.from_env()
        self.espreview_engine = ESPreviewEngine(self.espreview_config)
        self.mongo_creator = MongoTagCreator(config)
        self.es_retriever = ElasticsearchDataRetriever(config, self.espreview_engine.es_client)
        
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
        
        # Checkpoint/resume tracking
        self.checkpoint_data = None
        self.processed_date_ranges = set()  # Track which date ranges have been processed
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
    
    def process_date_range(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Process articles and social feeds for a specific date range."""
        
        print(f"Processing date range: {start_date} to {end_date}")
        
        # Process articles
        article_results = {"processed": 0, "tags_created": 0, "errors": []}
        articles = []
        if self.config.process_print:
            articles = self.es_retriever.get_historical_articles(start_date, end_date, self.config.batch_size)
            article_results = self._process_articles(articles)
        
        # Process social feeds
        social_feed_results = {"processed": 0, "tags_created": 0, "errors": []}
        social_feeds = []
        if self.config.process_online:
            social_feeds = self.es_retriever.get_historical_social_feeds(start_date, end_date, self.config.batch_size)
            social_feed_results = self._process_social_feeds(social_feeds)
        
        # Combine results
        combined_results = {
            "articles": article_results,
            "social_feeds": social_feed_results,
            "total_processed": len(articles) + len(social_feeds),
            "total_tags_created": article_results.get("tags_created", 0) + social_feed_results.get("tags_created", 0)
        }
        
        return combined_results
    
    def _process_articles(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process a batch of articles."""
        
        if not articles:
            return {"processed": 0, "tags_created": 0, "errors": []}
        
        print(f"Processing {len(articles)} articles...")
        
        results = {
            "processed": 0,
            "tags_created": 0,
            "errors": []
        }
        
        if self.config.parallel_processing:
            results = self._process_articles_parallel(articles)
        else:
            results = self._process_articles_sequential(articles)
        
        return results
    
    def _process_articles_parallel(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process articles in parallel."""
        
        results = {
            "processed": 0,
            "tags_created": 0,
            "errors": []
        }
        
        # OPTIMIZATION: Execute company queries once, cache results, then check articles
        # This avoids executing the same query hundreds of times
        company_query_cache = {}
        for company_id in self.config.company_ids:
            try:
                print(f"Executing company query for {company_id}...")
                query_result = self.espreview_engine.execute_company_query(
                    company_id, 
                    language=self.config.language
                )
                if query_result.success and query_result.total_matches > 0:
                    company_query_cache[company_id] = query_result
                    print(f"Company {company_id}: {query_result.total_matches} total matches found")
                else:
                    print(f"Company {company_id}: 0 matches - skipping articles for this company")
                    company_query_cache[company_id] = None
            except Exception as e:
                print(f"Error executing company query for {company_id}: {e}")
                company_query_cache[company_id] = None
        
        # Now process articles in parallel using cached query results
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            future_to_article = {
                executor.submit(self._process_single_article_with_cache, article, company_query_cache): article
                for article in articles
            }
            
            for future in as_completed(future_to_article):
                article = future_to_article[future]
                try:
                    article_result = future.result()
                    results["processed"] += 1
                    results["tags_created"] += article_result.get("tags_created", 0)
                except Exception as e:
                    results["errors"].append(f"Article {article.get('articleid', 'unknown')}: {str(e)}")
        
        return results
    
    def _process_articles_sequential(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process articles sequentially."""
        
        results = {
            "processed": 0,
            "tags_created": 0,
            "errors": []
        }
        
        # OPTIMIZATION: Execute company queries once, cache results, then check articles
        # This avoids executing the same query hundreds of times
        company_query_cache = {}
        for company_id in self.config.company_ids:
            try:
                print(f"Executing company query for {company_id}...")
                query_result = self.espreview_engine.execute_company_query(
                    company_id, 
                    language=self.config.language
                )
                if query_result.success and query_result.total_matches > 0:
                    company_query_cache[company_id] = query_result
                    print(f"Company {company_id}: {query_result.total_matches} total matches found")
                else:
                    print(f"Company {company_id}: 0 matches - skipping articles for this company")
                    company_query_cache[company_id] = None
            except Exception as e:
                print(f"Error executing company query for {company_id}: {e}")
                company_query_cache[company_id] = None
        
        # Now process articles using cached query results
        for article in articles:
            try:
                article_result = self._process_single_article_with_cache(article, company_query_cache)
                results["processed"] += 1
                results["tags_created"] += article_result.get("tags_created", 0)
            except Exception as e:
                results["errors"].append(f"Article {article.get('articleid', 'unknown')}: {str(e)}")
        
        return results
    
    def _process_single_article(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single article (legacy method - kept for compatibility)."""
        # Create a cache with all companies having None (will execute query for each)
        company_query_cache = {cid: None for cid in self.config.company_ids}
        return self._process_single_article_with_cache(article, company_query_cache)
    
    def _process_single_article_with_cache(self, article: Dict[str, Any], company_query_cache: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single article using cached company query results."""
        
        article_id = str(article['articleid'])
        headline = article.get('headlines', '')
        summary = article.get('summary', '')
        content = article.get('content', '')
        language = article.get('articlelang', 'en')
        
        results = {
            "article_id": article_id,
            "tags_created": 0,
            "company_matches": {}
        }
        
        # Process each company using cached query results
        for company_id in self.config.company_ids:
            try:
                query_result = company_query_cache.get(company_id)

                # If cache is None, execute query (backward compatibility)
                if query_result is None:
                    query_result = self.espreview_engine.execute_company_query(
                        company_id,
                        language=self.config.language
                    )
                    if query_result.success and query_result.total_matches > 0:
                        company_query_cache[company_id] = query_result
                    else:
                        continue  # Skip if 0 matches

                # Check if this article matches using Elasticsearch _id
                article_matches = self._check_article_matches(article['es_id'], query_result)

                if article_matches:
                    # Create tag
                    tag_created = self._create_article_tag(
                        article, company_id, article_matches
                    )

                    if tag_created:
                        results["tags_created"] += 1
                        results["company_matches"][company_id] = article_matches
                
            except Exception as e:
                print(f"Error processing company {company_id} for article {article_id}: {e}")
        
        return results
    
    def _process_social_feeds(self, social_feeds: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process a batch of social feeds."""
        
        if not social_feeds:
            return {"processed": 0, "tags_created": 0, "errors": []}
        
        print(f"Processing {len(social_feeds)} social feeds...")
        
        results = {
            "processed": 0,
            "tags_created": 0,
            "errors": []
        }
        
        # OPTIMIZATION: Execute company queries once, cache results, then check social feeds
        # This avoids executing the same query hundreds of times
        company_query_cache = {}
        for company_id in self.config.company_ids:
            try:
                print(f"Executing company query for {company_id}...")
                query_result = self.espreview_engine.execute_company_query(
                    company_id, 
                    language=self.config.language
                )
                if query_result.success and query_result.total_matches > 0:
                    company_query_cache[company_id] = query_result
                    print(f"Company {company_id}: {query_result.total_matches} total matches found")
                else:
                    print(f"Company {company_id}: 0 matches - skipping social feeds for this company")
                    company_query_cache[company_id] = None
            except Exception as e:
                print(f"Error executing company query for {company_id}: {e}")
                company_query_cache[company_id] = None
        
        # Now process social feeds using cached query results
        for social_feed in social_feeds:
            try:
                social_feed_result = self._process_single_social_feed_with_cache(social_feed, company_query_cache)
                results["processed"] += 1
                results["tags_created"] += social_feed_result.get("tags_created", 0)
            except Exception as e:
                results["errors"].append(f"Social feed {social_feed.get('SOCIALFEEDID', 'unknown')}: {str(e)}")
        
        return results
    
    def _process_single_social_feed(self, social_feed: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single social feed (legacy method - kept for compatibility)."""
        # Create a cache with all companies having None (will execute query for each)
        company_query_cache = {cid: None for cid in self.config.company_ids}
        return self._process_single_social_feed_with_cache(social_feed, company_query_cache)
    
    def _process_single_social_feed_with_cache(self, social_feed: Dict[str, Any], company_query_cache: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single social feed using cached company query results."""
        
        social_feed_id = str(social_feed['SOCIALFEEDID'])
        headline = social_feed.get('HEADLINE', '')
        summary = social_feed.get('SUMMARY', '')
        content = social_feed.get('CONTENT', '')
        language = social_feed.get('LANGUAGE', 'en')
        
        results = {
            "social_feed_id": social_feed_id,
            "tags_created": 0,
            "company_matches": {}
        }
        
        # Process each company using cached query results
        for company_id in self.config.company_ids:
            try:
                query_result = company_query_cache.get(company_id)

                # If cache is None, execute query (backward compatibility)
                if query_result is None:
                    query_result = self.espreview_engine.execute_company_query(
                        company_id,
                        language=self.config.language
                    )
                    if query_result.success and query_result.total_matches > 0:
                        company_query_cache[company_id] = query_result
                    else:
                        continue  # Skip if 0 matches

                # Check if this social feed matches using Elasticsearch _id
                social_feed_matches = self._check_social_feed_matches(social_feed['es_id'], query_result)

                if social_feed_matches:
                    # Create tag
                    tag_created = self._create_social_feed_tag(
                        social_feed, company_id, social_feed_matches
                    )

                    if tag_created:
                        results["tags_created"] += 1
                        results["company_matches"][company_id] = social_feed_matches
                
            except Exception as e:
                print(f"Error processing company {company_id} for social feed {social_feed_id}: {e}")
        
        return results
    
    def _check_article_matches(self, article_es_id: str, query_result) -> Optional[Dict[str, Any]]:
        """Check if an article matches the query results."""
        
        for index_name, index_result in query_result.index_results.items():
            if index_name == "printarticleindex" and article_es_id in index_result.article_ids:
                return {
                    "index": index_name,
                    "article_id": article_es_id,
                    "field_matches": index_result.field_matches.get(article_es_id, {}),
                    "score": index_result.field_matches.get(article_es_id, {}).get("score", 0.0)
                }
        
        return None
    
    def _check_social_feed_matches(self, social_feed_es_id: str, query_result) -> Optional[Dict[str, Any]]:
        """Check if a social feed matches the query results."""
        
        for index_name, index_result in query_result.index_results.items():
            if index_name == "socialfeedindex" and social_feed_es_id in index_result.article_ids:
                return {
                    "index": index_name,
                    "social_feed_id": social_feed_es_id,
                    "field_matches": index_result.field_matches.get(social_feed_es_id, {}),
                    "score": index_result.field_matches.get(social_feed_es_id, {}).get("score", 0.0)
                }
        
        return None
    
    def _create_article_tag(self, article: Dict[str, Any], company_id: str, 
                           article_matches: Dict[str, Any]) -> bool:
        """Create MongoDB tag for an article."""
        
        try:
            # Get company name
            company_name = self._get_company_name(company_id)
            
            # Create tag data
            tag_data = {
                "KEYWORDS": f"Backtracking match for {company_id}",
                "COMPANYID": company_id
            }
            
            # Create sources
            sources = {
                "headline": ["headline"] if article_matches.get("field_matches", {}).get("matched_fields", []) else [],
                "content": ["content"] if article_matches.get("field_matches", {}).get("matched_fields", []) else [],
                "summary": ["summary"] if article_matches.get("field_matches", {}).get("matched_fields", []) else []
            }
            
            # Create tag document
            tag_doc = self.mongo_creator.create_article_tag(
                article_id=str(article['es_id']),  # Use Elasticsearch _id as MongoDB article_id
                pg_article_id=article['articleid'],  # Use the articleId from Elasticsearch
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
                "article_id": str(article['es_id']),  # Use Elasticsearch _id
                "company_id": company_id,
                "company_name": company_name,
                "is_article": True
            }
            
            return self.mongo_creator.save_tags_to_mongo([tag_result], update_type=1)
            
        except Exception as e:
            print(f"Error creating article tag: {e}")
            return False
    
    def _create_social_feed_tag(self, social_feed: Dict[str, Any], company_id: str,
                               social_feed_matches: Dict[str, Any]) -> bool:
        """Create MongoDB tag for a social feed."""
        
        try:
            # Get company name
            company_name = self._get_company_name(company_id)
            
            # Create tag data
            tag_data = {
                "KEYWORDS": f"Backtracking match for {company_id}",
                "COMPANYID": company_id
            }
            
            # Create sources
            sources = {
                "headline": ["headline"] if social_feed_matches.get("field_matches", {}).get("matched_fields", []) else [],
                "content": ["content"] if social_feed_matches.get("field_matches", {}).get("matched_fields", []) else [],
                "summary": ["summary"] if social_feed_matches.get("field_matches", {}).get("matched_fields", []) else []
            }
            
            # Create tag document
            tag_doc = self.mongo_creator.create_social_tag(
                social_feed_id=str(social_feed['es_id']),  # Use Elasticsearch _id as MongoDB social_feed_id
                pg_social_feed_id=social_feed['SOCIALFEEDID'],  # Use the socialFeedId from Elasticsearch
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
                "social_feed_id": str(social_feed['es_id']),  # Use Elasticsearch _id
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
    
    def run_backtracking(self, resume: bool = True) -> Dict[str, Any]:
        """Run the complete backtracking process with automatic resume on crash."""
        
        retry_count = 0
        max_retries = self.config.max_auto_retries if self.config.auto_resume_on_crash else 0
        
        while retry_count <= max_retries:
            try:
                return self._run_backtracking_attempt(resume, retry_count)
            except Exception as e:
                retry_count += 1
                
                if retry_count > max_retries:
                    print(f"\n*** MAX RETRIES EXCEEDED ({max_retries}) ***")
                    print(f"Final error: {e}")
                    if self.config.enable_checkpoints:
                        print("Saving final checkpoint...")
                        self._save_checkpoint()
                    self.results["errors"].append(f"Max retries exceeded. Last error: {str(e)}")
                    return self.results
                
                print(f"\n*** AUTOMATIC RESTART #{retry_count} ***")
                print(f"Error: {e}")
                print(f"Waiting {self.config.retry_delay_seconds} seconds before retry...")
                time.sleep(self.config.retry_delay_seconds)
                
                # Force resume on retry
                resume = True
                # Reload checkpoint before retry
                if self.config.enable_checkpoints:
                    self._load_checkpoint()
    
    def _run_backtracking_attempt(self, resume: bool = True, attempt_number: int = 0) -> Dict[str, Any]:
        """Single attempt at running backtracking."""
        
        if attempt_number > 0:
            print(f"\n=== RETRY ATTEMPT #{attempt_number} ===")
        
        print("Starting backtracking process...")
        print(f"Date range: {self.config.start_date} to {self.config.end_date}")
        print(f"Companies: {', '.join(self.config.company_ids)}")
        print(f"Batch size: {self.config.batch_size}")
        print(f"Parallel processing: {self.config.parallel_processing}")
        print(f"Dry run: {self.config.dry_run}")
        print(f"Checkpoints enabled: {self.config.enable_checkpoints}")
        print(f"Auto-resume on crash: {self.config.auto_resume_on_crash}")
        
        # Check if resuming
        if resume and self.checkpoint_data:
            print(f"\n*** RESUMING FROM CHECKPOINT ***")
            print(f"Last processed date: {self.checkpoint_data.get('last_processed_date', 'unknown')}")
            print(f"Progress: {self.checkpoint_data.get('chunks_processed', 0)} chunks completed")
            # Restore processed state
            self.processed_date_ranges = set(self.checkpoint_data.get('processed_date_ranges', []))
            # Restore results
            if 'total_articles_processed' in self.checkpoint_data:
                self.results['total_articles_processed'] = self.checkpoint_data.get('total_articles_processed', 0)
            if 'total_social_feeds_processed' in self.checkpoint_data:
                self.results['total_social_feeds_processed'] = self.checkpoint_data.get('total_social_feeds_processed', 0)
            if 'total_tags_created' in self.checkpoint_data:
                self.results['total_tags_created'] = self.checkpoint_data.get('total_tags_created', 0)
        
        start_time = time.time()
        
        try:
            # Process date range in chunks for resumability
            if self.config.enable_checkpoints:
                results = self.process_date_range_chunked(self.config.start_date, self.config.end_date)
            else:
                # Original behavior - process entire range at once
                results = self.process_date_range(self.config.start_date, self.config.end_date)
            
            # Update results
            self.results["end_time"] = datetime.now()
            self.results["total_articles_processed"] += results.get("articles", {}).get("processed", 0)
            self.results["total_social_feeds_processed"] += results.get("social_feeds", {}).get("processed", 0)
            self.results["total_tags_created"] += results.get("total_tags_created", 0)
            self.results["processing_time_seconds"] = time.time() - start_time
            
            # Clear checkpoint on successful completion
            if self.config.enable_checkpoints:
                self._clear_checkpoint()
            
            # Save results if configured
            if self.config.save_results:
                self._save_results()
            
            print(f"\nBacktracking completed!")
            print(f"Total articles processed: {self.results['total_articles_processed']}")
            print(f"Total social feeds processed: {self.results['total_social_feeds_processed']}")
            print(f"Total tags created: {self.results['total_tags_created']}")
            print(f"Processing time: {self.results['processing_time_seconds']:.2f} seconds")
            
            return self.results
            
        except KeyboardInterrupt:
            print(f"\n*** BACKTRACKING INTERRUPTED BY USER ***")
            if self.config.enable_checkpoints:
                print("Saving checkpoint...")
                self._save_checkpoint()
            self.results["errors"].append("Interrupted by user")
            raise  # Don't retry on user interrupt
        except Exception as e:
            print(f"Backtracking attempt failed: {e}")
            print(traceback.format_exc())
            if self.config.enable_checkpoints:
                print("Saving checkpoint before retry...")
                self._save_checkpoint()
            
            # Re-raise to trigger retry logic
            raise
    
    def process_date_range_chunked(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Process date range in chunks for resumability."""
        from datetime import datetime as dt
        
        start_dt = dt.strptime(start_date, '%Y-%m-%d')
        end_dt = dt.strptime(end_date, '%Y-%m-%d')
        chunk_days = self.config.chunk_days
        
        # Generate all date chunks
        date_chunks = []
        current_date = start_dt
        while current_date <= end_dt:
            chunk_end = min(current_date + timedelta(days=chunk_days - 1), end_dt)
            chunk_start_str = current_date.strftime('%Y-%m-%d')
            chunk_end_str = chunk_end.strftime('%Y-%m-%d')
            date_chunks.append((chunk_start_str, chunk_end_str))
            current_date = chunk_end + timedelta(days=1)
        
        print(f"Processing {len(date_chunks)} date chunks ({chunk_days} days each)")
        
        total_results = {
            "articles": {"processed": 0, "tags_created": 0, "errors": []},
            "social_feeds": {"processed": 0, "tags_created": 0, "errors": []},
            "total_processed": 0,
            "total_tags_created": 0
        }
        
        chunks_processed = 0
        for idx, (chunk_start, chunk_end) in enumerate(date_chunks, 1):
            chunk_key = f"{chunk_start}_{chunk_end}"
            
            # Skip if already processed
            if chunk_key in self.processed_date_ranges:
                print(f"Chunk {idx}/{len(date_chunks)}: Skipping {chunk_start} to {chunk_end} (already processed)")
                continue
            
            print(f"\nChunk {idx}/{len(date_chunks)}: Processing {chunk_start} to {chunk_end}")
            
            try:
                # Process this chunk
                chunk_results = self.process_date_range(chunk_start, chunk_end)
                
                # Accumulate results
                total_results["articles"]["processed"] += chunk_results.get("articles", {}).get("processed", 0)
                total_results["articles"]["tags_created"] += chunk_results.get("articles", {}).get("tags_created", 0)
                total_results["social_feeds"]["processed"] += chunk_results.get("social_feeds", {}).get("processed", 0)
                total_results["social_feeds"]["tags_created"] += chunk_results.get("social_feeds", {}).get("tags_created", 0)
                total_results["total_processed"] += chunk_results.get("total_processed", 0)
                total_results["total_tags_created"] += chunk_results.get("total_tags_created", 0)
                
                # Mark as processed
                self.processed_date_ranges.add(chunk_key)
                chunks_processed += 1
                
                # Save checkpoint periodically
                if chunks_processed % self.config.save_checkpoint_interval == 0:
                    print(f"Saving checkpoint after {chunks_processed} chunks...")
                    self._save_checkpoint()
                
            except Exception as e:
                error_msg = f"Error processing chunk {chunk_start} to {chunk_end}: {str(e)}"
                print(error_msg)
                total_results["articles"]["errors"].append(error_msg)
                # Save checkpoint even on error
                self._save_checkpoint()
                # Optionally continue with next chunk or raise
                if self.config.verbose:
                    print(f"Continuing with next chunk...")
        
        # Final checkpoint save
        if self.config.enable_checkpoints:
            self._save_checkpoint()
        
        return total_results
    
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
                    print(f"  Last processed date: {self.checkpoint_data.get('last_processed_date', 'unknown')}")
                    print(f"  Chunks processed: {self.checkpoint_data.get('chunks_processed', 0)}")
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
                    print(f"  Last processed date: {self.checkpoint_data.get('last_processed_date', 'unknown')}")
                    print(f"  Chunks processed: {self.checkpoint_data.get('chunks_processed', 0)}")
                except Exception as e:
                    print(f"Warning: Could not load checkpoint: {e}")
                    self.checkpoint_data = None
    
    def _save_checkpoint(self):
        """Save current state to checkpoint (MongoDB or filesystem)."""
        if not self.config.enable_checkpoints:
            return
        
        try:
            # Determine last processed date
            last_processed_date = self.config.start_date
            if self.processed_date_ranges:
                # Get the latest end date from processed ranges
                latest_end = max(
                    range_str.split('_')[1] 
                    for range_str in self.processed_date_ranges
                )
                last_processed_date = latest_end
            
            checkpoint_data = {
                "config": {
                    "start_date": self.config.start_date,
                    "end_date": self.config.end_date,
                    "company_ids": self.config.company_ids,
                    "chunk_days": self.config.chunk_days
                },
                "last_processed_date": last_processed_date,
                "processed_date_ranges": list(self.processed_date_ranges),
                "chunks_processed": len(self.processed_date_ranges),
                "total_articles_processed": self.results.get("total_articles_processed", 0),
                "total_social_feeds_processed": self.results.get("total_social_feeds_processed", 0),
                "total_tags_created": self.results.get("total_tags_created", 0),
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
                # Clear from MongoDB
                if self.mongo_checkpoint_manager:
                    self.mongo_checkpoint_manager.clear_checkpoint()
            else:
                # Clear from filesystem
                if not self.config.checkpoint_file:
                    return
                
                checkpoint_path = Path(self.config.checkpoint_file)
                if checkpoint_path.exists():
                    checkpoint_path.unlink()
                    print(f"Checkpoint cleared: {checkpoint_path}")
        except Exception as e:
            print(f"Warning: Could not clear checkpoint: {e}")
    
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
    """Main entry point for backtracking."""
    
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
        
        # MongoDB settings
        mongo_uri=os.getenv("PG_MONGO_URI", "mongodb://localhost:27017/"),
        mongo_db=os.getenv("PG_MONGO_DB", "pnq"),
        
        # PostgreSQL settings
        pg_host=os.getenv("PG_HOST", "localhost"),
        pg_port=int(os.getenv("PG_PORT", "5432")),
        pg_db=os.getenv("PG_DATABASE", "prod_admin"),
        pg_user=os.getenv("PG_USER", "prod_cirrus"),
        pg_password=os.getenv("PG_PASSWORD", ""),
        
        # Output settings
        dry_run=False,  # Set to True for testing
        verbose=True,
        save_results=True,
        results_file="backtracking_results.json"
    )
    
    # Run backtracking
    engine = BacktrackingEngine(config)
    results = engine.run_backtracking()
    
    return results

if __name__ == "__main__":
    main()
