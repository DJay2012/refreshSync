"""
Caching layer for frequently accessed data
"""
import threading
import time
from collections import defaultdict
from .mongo_helpers import mongoConnection

class TaggingCache:
    """Cache for frequently accessed tagging data"""
    
    def __init__(self, ttl=300):  # 5 minute TTL
        self.ttl = ttl
        self.lock = threading.Lock()
        
        # Caches
        self.company_names = {}  # company_id -> name
        self.mongo_article_ids = {}  # pg_article_id -> mongo_article_id
        self.mongo_social_ids = {}  # pg_social_id -> mongo_social_id
        self.existing_tags = defaultdict(set)  # article_id -> set of company_ids
        
        # Timestamps for TTL
        self.company_names_ts = {}
        self.mongo_article_ids_ts = {}
        self.mongo_social_ids_ts = {}
        self.existing_tags_ts = {}
    
    def _is_expired(self, timestamp):
        """Check if cache entry is expired"""
        return time.time() - timestamp > self.ttl
    
    def get_company_names(self, company_ids):
        """Get company names with caching"""
        with self.lock:
            # Check cache first
            cached_names = {}
            missing_ids = []
            
            for company_id in company_ids:
                if (company_id in self.company_names and 
                    company_id in self.company_names_ts and
                    not self._is_expired(self.company_names_ts[company_id])):
                    cached_names[company_id] = self.company_names[company_id]
                else:
                    missing_ids.append(company_id)
            
            # Fetch missing from database
            if missing_ids:
                try:
                    mongo_db = mongoConnection()
                    if not isinstance(mongo_db, Exception):
                        company_col = mongo_db["companyMaster"]
                        companies_cursor = company_col.find(
                            {"_id": {"$in": missing_ids}},
                            {"_id": 1, "companyInfo.companyName": 1}
                        )
                        
                        current_time = time.time()
                        for doc in companies_cursor:
                            company_id = str(doc["_id"])
                            company_name = doc.get("companyInfo", {}).get("companyName", "")
                            
                            # Cache the result
                            self.company_names[company_id] = company_name
                            self.company_names_ts[company_id] = current_time
                            cached_names[company_id] = company_name
                            
                except Exception as e:
                    print(f"Error fetching company names: {e}")
            
            return cached_names
    
    def get_mongo_article_id(self, pg_article_id):
        """Get MongoDB article ID with caching"""
        with self.lock:
            if (pg_article_id in self.mongo_article_ids and
                pg_article_id in self.mongo_article_ids_ts and
                not self._is_expired(self.mongo_article_ids_ts[pg_article_id])):
                return self.mongo_article_ids[pg_article_id]
            
            # Fetch from database
            try:
                mongo_db = mongoConnection()
                if not isinstance(mongo_db, Exception):
                    article_col = mongo_db["article"]
                    article = article_col.find_one(
                        {"sourceArticleId": pg_article_id},
                        {"_id": 1}
                    )
                    
                    if article:
                        mongo_id = article["_id"]
                        # Cache the result
                        self.mongo_article_ids[pg_article_id] = mongo_id
                        self.mongo_article_ids_ts[pg_article_id] = time.time()
                        return mongo_id
                        
            except Exception as e:
                print(f"Error fetching mongo article ID: {e}")
            
            return None
    
    def get_existing_tags(self, article_id, company_ids):
        """Get existing tag IDs with caching"""
        with self.lock:
            cache_key = f"article_{article_id}"
            
            if (cache_key in self.existing_tags and
                cache_key in self.existing_tags_ts and
                not self._is_expired(self.existing_tags_ts[cache_key])):
                cached_set = self.existing_tags[cache_key]
                return {f"{article_id}{cid}" for cid in company_ids if cid in cached_set}
            
            # Fetch from database
            try:
                mongo_db = mongoConnection()
                if not isinstance(mongo_db, Exception):
                    tag_col = mongo_db["articleTag"]
                    tag_ids_to_check = [f"{article_id}{cid}" for cid in company_ids]
                    
                    existing_tags = tag_col.find(
                        {"_id": {"$in": tag_ids_to_check}},
                        {"_id": 1}
                    )
                    
                    existing_set = {doc["_id"] for doc in existing_tags}
                    
                    # Cache the result (store company IDs for this article)
                    company_set = {tag_id.replace(str(article_id), "") for tag_id in existing_set}
                    self.existing_tags[cache_key] = company_set
                    self.existing_tags_ts[cache_key] = time.time()
                    
                    return existing_set
                    
            except Exception as e:
                print(f"Error fetching existing tags: {e}")
            
            return set()
    
    def invalidate_article(self, pg_article_id):
        """Invalidate cache entries for an article"""
        with self.lock:
            # Remove from caches
            self.mongo_article_ids.pop(pg_article_id, None)
            self.mongo_article_ids_ts.pop(pg_article_id, None)
            
            # Find and remove existing tags cache
            mongo_id = self.mongo_article_ids.get(pg_article_id)
            if mongo_id:
                cache_key = f"article_{mongo_id}"
                self.existing_tags.pop(cache_key, None)
                self.existing_tags_ts.pop(cache_key, None)
    
    def clear_expired(self):
        """Clear expired cache entries"""
        with self.lock:
            current_time = time.time()
            
            # Clear expired company names
            expired_companies = [
                cid for cid, ts in self.company_names_ts.items()
                if current_time - ts > self.ttl
            ]
            for cid in expired_companies:
                self.company_names.pop(cid, None)
                self.company_names_ts.pop(cid, None)
            
            # Clear expired article IDs
            expired_articles = [
                aid for aid, ts in self.mongo_article_ids_ts.items()
                if current_time - ts > self.ttl
            ]
            for aid in expired_articles:
                self.mongo_article_ids.pop(aid, None)
                self.mongo_article_ids_ts.pop(aid, None)
            
            # Clear expired tags
            expired_tags = [
                key for key, ts in self.existing_tags_ts.items()
                if current_time - ts > self.ttl
            ]
            for key in expired_tags:
                self.existing_tags.pop(key, None)
                self.existing_tags_ts.pop(key, None)

# Global cache instance
tagging_cache = None

def get_tagging_cache():
    """Get or create global tagging cache"""
    global tagging_cache
    if tagging_cache is None:
        tagging_cache = TaggingCache()
    return tagging_cache
