import datetime
import sys
import argparse
from functools import lru_cache
from typing import Dict, Any, List, Optional
import traceback
import time
import math
import os
from datetime import datetime, timezone, timedelta
from elasticsearch import helpers
import pymongo
from wordcloud import WordCloud
import warnings
from dotenv import load_dotenv
import threading
import logging
import concurrent.futures
from threading import Lock
from collections import defaultdict
import pandas as pd  

# Import existing database connections
from app.services.charts_api.database import mongo, elastic

# Load environment variables
load_dotenv()

warnings.filterwarnings("ignore", category=UserWarning, module="pymongo")

# Configure logging
class DualLogger:
    def __init__(self):
        homeDir = os.path.expanduser('~')
        logsDir = os.path.join(homeDir, 'log')
        os.makedirs(logsDir, exist_ok=True)

        # Get the base name of the running script without extension
        script_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
        # No date attached — single consistent log file
        self.fileName = os.path.join(logsDir, f'{script_name}.log')

    def write(self, message):
        if not message.strip():
            return
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if not message.endswith('\n'):
            message += '\n'
        log_entry = f"[{timestamp}] {message}"
        with open(self.fileName, 'a', encoding='utf-8') as f:
            f.write(log_entry)
        print(log_entry, end='')  # Also print to console

# Setup the logger wrapper
dual_logger = DualLogger()

class DualLoggerWrapper:
    @staticmethod
    def info(msg):
        dual_logger.write(f"[INFO] {msg}")

    @staticmethod
    def warning(msg):
        dual_logger.write(f"[WARNING] {msg}")

    @staticmethod
    def error(msg):
        dual_logger.write(f"[ERROR] {msg}")

    @staticmethod
    def debug(msg):
        dual_logger.write(f"[DEBUG] {msg}")

    @staticmethod
    def critical(msg):
        dual_logger.write(f"[CRITICAL] {msg}")

    @staticmethod
    def exception(msg, exc_info=True):
        import traceback
        dual_logger.write(f"[EXCEPTION] {msg}")
        if exc_info:
            dual_logger.write(traceback.format_exc())

logging.getLogger('elastic_transport').setLevel(logging.WARNING)
logging.getLogger('elasticsearch').setLevel(logging.WARNING)
logging.getLogger('elastic_transport.node_pool').setLevel(logging.ERROR)
logging.getLogger('elastic_transport.transport').setLevel(logging.ERROR)

# Override logger
logger = DualLoggerWrapper
IndexName = 'onlinearticlereport'
index_name = IndexName

# configuration
EXCEL_PATH = 'HDFC_ERGO/ONLINE/HDFC ERGO ONLINE APRIL 2025.xlsx'
MONGO_COLLECTION = 'socialFeed'
MONGO_TAG_COLLECTION = 'socialFeedTag'
ES_INDEX = 'onlinearticlereport'

def replace_nan_with_null(data):
    """Replace NaN values with None for Elasticsearch compatibility"""
    if isinstance(data, dict):
        return {k: replace_nan_with_null(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [replace_nan_with_null(item) for item in data]
    elif isinstance(data, float) and math.isnan(data):
        return None  # Replace NaN with Elasticsearch-compatible null
    return data

def convert_boolean_fields(data):
    """Convert string boolean values to actual booleans"""
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if k == 'isActive' and isinstance(v, str):
                result[k] = v.upper() in ['Y', 'YES', 'TRUE', '1']
            else:
                result[k] = convert_boolean_fields(v)
        return result
    elif isinstance(data, list):
        return [convert_boolean_fields(item) for item in data]
    return data

def clean_string_field(value, default=""):
    """Clean string field values, treating NA/N/A as null"""
    if is_null_or_na(value):
        return default
    return str(value).strip() if value is not None else default

def is_null_or_na(value):
    """Check if value is null, NaN, or represents NA/N/A"""
    if value is None or pd.isna(value):
        return True
    if isinstance(value, str):
        return value.strip().lower() in ['nan', 'na', 'n/a', '']
    return False

def convert_data_types(data):
    """Convert data types to match Elasticsearch mapping"""
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            # Convert specific fields to correct types
            if k == 'socialFeedId':
                try:
                    result[k] = int(float(v)) if not is_null_or_na(v) else 0
                except (ValueError, TypeError):
                    result[k] = 0
            elif k in ['engagement', 'pageViews', 'reach', 'uniqueVisitors', 'urlViews', 'wordCount']:
                try:
                    result[k] = int(float(v)) if not is_null_or_na(v) else 0
                except (ValueError, TypeError):
                    result[k] = 0
            elif k == 'sentiment':
                try:
                    result[k] = float(v) if not is_null_or_na(v) else 0.0
                except (ValueError, TypeError):
                    result[k] = 0.0
            elif k == 'countryCode':
                try:
                    result[k] = int(float(v)) if not is_null_or_na(v) else 0
                except (ValueError, TypeError):
                    result[k] = 0
            else:
                result[k] = convert_data_types(v)
        return result
    elif isinstance(data, list):
        return [convert_data_types(item) for item in data]
    return data

class ExcelToElasticsearchSocialInserter:
    """
    Handles reading Excel, merging with MongoDB, and inserting into Elasticsearch for Social feeds.
    """
    
    def __init__(self, excel_path, source_collection_name, tag_collection_name, es_index_name):
        self.excel_path = excel_path
        self.mongo_db = mongo()
        self.es = elastic()
        self.index_name = es_index_name
        self.social_feed_collection = self.mongo_db[source_collection_name]
        self.social_feed_tag_collection = self.mongo_db[tag_collection_name]
        self.static_collections = {
            'clientMaster': self.mongo_db["clientMaster"],
            'companyMaster': self.mongo_db["companyMaster"],
            'cityMaster': self.mongo_db["cityMaster"],
            'stateMaster': self.mongo_db["stateMaster"],
            'publicationMasterOnline': self.mongo_db["publicationMasterOnline"]
        }
        
        # Cache for frequently accessed data
        self._company_cache = {}
        self._city_cache = {}
        self._state_cache = {}
        self._publication_cache = {}
        
        # Track all skipped/missing records for export
        self.skipped_records = []
        
        # Load publication domains for country detection
        self._load_publication_domains()
        
        # Create index with proper mapping
        self._create_index_mapping()

    def _load_publication_domains(self):
        """Load and cache publication domains for country detection"""
        try:
            self.india_domains = set()
            self.usa_domains = set()
            
            # Try to load India domains
            try:
                if os.path.exists("Publications.xlsx"):
                    india_df = pd.read_excel("Publications.xlsx")
                    self.india_domains = set(india_df['Domain'].dropna())
                    logger.info(f"Loaded {len(self.india_domains)} India domains")
            except Exception as e:
                logger.warning(f"Could not load Publications.xlsx: {str(e)}")
            
            # Try to load USA domains
            try:
                if os.path.exists("PublicationsUSA.xlsx"):
                    usa_df = pd.read_excel("PublicationsUSA.xlsx")
                    self.usa_domains = set(usa_df['Domain'].dropna())
                    logger.info(f"Loaded {len(self.usa_domains)} USA domains")
            except Exception as e:
                logger.warning(f"Could not load PublicationsUSA.xlsx: {str(e)}")
                
        except Exception as e:
            logger.error(f"Error loading domains: {str(e)}")
            self.india_domains = set()
            self.usa_domains = set()

    def _create_index_mapping(self, force_recreate=False):
        """Create Elasticsearch index with proper mapping"""
        try:
            # Check if index already exists
            if self.es.indices.exists(index=self.index_name):
                if force_recreate:
                    logger.info(f"Deleting existing index {self.index_name} to recreate with new mapping")
                    self.es.indices.delete(index=self.index_name)
                else:
                    logger.info(f"Index {self.index_name} already exists, skipping mapping creation")
                    return
            
            # Define the mapping to match the exact desired structure
            mapping = {
                "mappings": {
                    "properties": {
                        "author": {
                            "properties": {
                                "gender": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                },
                                "id": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                },
                                "name": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                }
                            }
                        },
                        "companyTag": {
                            "type": "nested",
                            "properties": {
                                "chartData": {
                                    "properties": {
                                        "engagement": {"type": "long"},
                                        "isOnlineArticle": {"type": "boolean"},
                                        "keywords": {
                                            "type": "text",
                                            "fields": {
                                                "keyword": {
                                                    "type": "keyword",
                                                    "ignore_above": 256
                                                }
                                            }
                                        },
                                        "pageViews": {"type": "long"},
                                        "prominence": {"type": "float"},
                                        "qcFlag": {
                                            "type": "text",
                                            "fields": {
                                                "keyword": {
                                                    "type": "keyword",
                                                    "ignore_above": 256
                                                }
                                            }
                                        },
                                        "reach": {"type": "long"},
                                        "reportingSubject": {
                                            "type": "text",
                                            "fields": {
                                                "keyword": {
                                                    "type": "keyword",
                                                    "ignore_above": 256
                                                }
                                            }
                                        },
                                        "reportingTone": {"type": "float"},
                                        "sentiment": {"type": "float"},
                                        "uniqueVisitors": {"type": "long"},
                                        "urlViews": {"type": "long"},
                                        "wordCount": {"type": "long"}
                                    }
                                },
                                "id": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                },
                                "name": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                },
                                "shortName": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                }
                            }
                        },
                        "crossLanguageInvertedToken": {
                            "type": "text",
                            "fields": {
                                "keyword": {
                                    "type": "keyword",
                                    "ignore_above": 256
                                }
                            }
                        },
                        "feedData": {
                            "properties": {
                                "articleDateNumber": {"type": "long"},
                                "feedDate": {"type": "date"},
                                "feedDateTime": {"type": "date"},
                                "language": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                }
                            }
                        },
                        "feedInfo": {
                            "properties": {
                                "isActive": {"type": "boolean"},
                                "socialFeedType": {"type": "long"},
                                "txnNumber": {"type": "long"}
                            }
                        },
                        "location": {
                            "properties": {
                                "city": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                },
                                "cityid": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                },
                                "countryCode": {"type": "long"},
                                "countryName": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                }
                            }
                        },
                        "publicationInfo": {
                            "properties": {
                                "id": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                },
                                "name": {
                                    "type": "text",
                                    "fields": {
                                        "keyword": {
                                            "type": "keyword",
                                            "ignore_above": 256
                                        }
                                    }
                                }
                            }
                        },
                        "socialFeedId": {"type": "long"}
                    }
                }
            }
            
            # Create the index with mapping
            self.es.indices.create(index=self.index_name, body=mapping)
            logger.info(f"Created index {self.index_name} with proper mapping")
            
        except Exception as e:
            logger.error(f"Error creating index mapping: {str(e)}")
            # Continue execution even if mapping creation fails

    def get_company_info(self, company_id):
        if not company_id or company_id in self._company_cache:
            return self._company_cache.get(company_id, {})
        
        company_info = self.static_collections['companyMaster'].find_one(
            {"_id": str(company_id)}
        ) or {}
        self._company_cache[company_id] = company_info
        return company_info

    def get_city_info(self, city_name):
        if not city_name or city_name in self._city_cache:
            return self._city_cache.get(city_name, {})
        
        city_info = self.static_collections['cityMaster'].find_one(
            {"cityInfo.cityName": city_name}
        ) or {}
        self._city_cache[city_name] = city_info
        return city_info

    def get_country_info(self, publication_name):
        """Get country code and name based on publication domain"""
        if publication_name in self.india_domains:
            return 91, "India"
        elif publication_name in self.usa_domains:
            return 1, "USA"
        
        # Try to get from publicationMasterOnline collection
        try:
            if publication_name not in self._publication_cache:
                pub_info = self.static_collections['publicationMasterOnline'].find_one(
                    {"publicationInfo.publicationName": publication_name}
                ) or {}
                self._publication_cache[publication_name] = pub_info
            
            pub_info = self._publication_cache[publication_name]
            if pub_info and pub_info.get("publicationInfo"):
                # Use actual country data from the database if available
                country_code = pub_info.get("publicationInfo", {}).get("countryCode")
                country_name = pub_info.get("publicationInfo", {}).get("countryName")
                if country_code and country_name:
                    return country_code, country_name
        except Exception as e:
            logger.warning(f"Error looking up publication {publication_name}: {e}")
        
        # Return empty values if no data found
        return None, None

    def _get_edition_type_name(self, edition_type):
        """Map edition type code to name"""
        EDITION_TYPE_MAPPING = {
            "ND": "National Daily",
            "TM": "Trade Magazine", 
            "RD": "Regional Daily",
            "PIM": "Popular Interest Magazine",
            "BD": "Business Daily",
            "TAB": "Tabloid",
            "WP": "Web Publication",
            "BIM": "Business Interest Magazine",
        }
        return EDITION_TYPE_MAPPING.get(edition_type, "Others")
    
    def _get_region_from_city(self, city_id):
        """Get region/zone from city ID"""
        try:
            if city_id:
                city_info = self.get_city_info_by_id(city_id)
                if city_info:
                    state_id = city_info.get('stateId')
                    if state_id:
                        state_info = self._state_cache.get(state_id)
                        if not state_info:
                            state_info = self.static_collections['stateMaster'].find_one({'stateId': state_id})
                            if state_info:
                                self._state_cache[state_id] = state_info
                        return state_info.get('zone', 'Others') if state_info else 'Others'
            return 'Others'
        except Exception as e:
            logger.warning(f"Error getting region for city {city_id}: {e}")
            return 'Others'
    
    def get_city_info_by_id(self, city_id):
        """Get city info by city ID"""
        if not city_id:
            return {}
        
        if city_id not in self._city_cache:
            city_info = self.static_collections['cityMaster'].find_one(
                {"cityInfo.cityId": city_id}
            ) or {}
            self._city_cache[city_id] = city_info
        
        return self._city_cache[city_id].get("cityInfo", {}) if self._city_cache[city_id] else {}

    def get_state_info(self, state_id):
        """Get state info by state ID"""
        if not state_id:
            return {}
        
        if state_id not in self._state_cache:
            state_info = self.static_collections['stateMaster'].find_one(
                {"stateInfo.stateId": state_id}
            ) or {}
            self._state_cache[state_id] = state_info
        
        return self._state_cache[state_id].get("stateInfo", {}) if self._state_cache[state_id] else {}

    def clean_reporting_subject(self, text, field_name=""):
        """Clean and normalize reporting subject text"""
        try:
            # Handle pandas NaN values explicitly
            if text is None or pd.isna(text) or str(text).lower() in ['nan', 'none', '', 'na', 'n/a']:
                return ""
            
            # Clean the text
            clean_text = str(text).strip()
            if len(clean_text) < 2:
                return ""
            
            # Enhanced text cleaning
            import re
            # Remove URLs and email addresses
            clean_text = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', clean_text)
            clean_text = re.sub(r'\S+@\S+', '', clean_text)  # Remove emails
            # Keep alphanumeric and basic punctuation, but remove excessive special chars
            clean_text = re.sub(r'[^\w\s\-\.\,\;\:]', ' ', clean_text)  # Keep basic punctuation
            # Remove standalone numbers but keep numbers that are part of words
            clean_text = re.sub(r'\b\d+\b', '', clean_text)  # Remove standalone numbers only
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()  # Normalize whitespace
            
            # Capitalize first letter of each word for consistency
            clean_text = ' '.join(word.capitalize() for word in clean_text.split())
            
            result = clean_text if len(clean_text) >= 2 else ""
            return result
            
        except Exception as e:
            logger.warning(f"Error cleaning reporting subject for field '{field_name}': {str(e)}")
            return ""
    
    def safe_get_value(self, row, *column_names, default=""):
        """Safely get value from pandas row, handling NaN values properly"""
        for col_name in column_names:
            if col_name in row:
                value = row[col_name]
                if pd.notna(value) and str(value).strip() and str(value).lower() not in ['nan', 'na', 'n/a']:
                    return value
        return default

    def generate_word_cloud(self, text):
        """Generate cleaned keywords from text using WordCloud"""
        try:
            if not text or not str(text).strip():
                return ""
            
            # Clean the text and limit length to avoid processing issues
            clean_text = str(text).strip()[:10000]  # Increased limit for better keyword extraction
            if len(clean_text) < 3:  # Too short for meaningful word cloud
                return ""
            
            # Enhanced text cleaning
            import re
            # Remove URLs and email addresses
            clean_text = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', clean_text)
            clean_text = re.sub(r'\S+@\S+', '', clean_text)  # Remove emails
            # Keep alphanumeric and basic punctuation, but remove excessive special chars
            clean_text = re.sub(r'[^\w\s\-\.]', ' ', clean_text)  # Keep hyphens and periods
            # Remove standalone numbers but keep numbers that are part of words
            clean_text = re.sub(r'\b\d+\b', '', clean_text)  # Remove standalone numbers only
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()  # Normalize whitespace
            
            if not clean_text or len(clean_text) < 3:
                return ""
            
            # Common stop words to exclude
            stop_words = {
                'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by',
                'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
                'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can', 'this', 'that', 'these', 'those',
                'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her', 'us', 'them', 'my', 'your', 'his',
                'her', 'its', 'our', 'their', 'said', 'says', 'also', 'more', 'very', 'so', 'just', 'now', 'then',
                'than', 'only', 'even', 'back', 'good', 'well', 'way', 'much', 'go', 'get', 'make', 'take', 'come',
                'see', 'know', 'time', 'year', 'day', 'work', 'life', 'world', 'hand', 'part', 'child', 'eye',
                'woman', 'man', 'place', 'work', 'week', 'case', 'point', 'government', 'company'
            }
                
            wordcloud = WordCloud(
                width=400, 
                height=200, 
                background_color="white",
                max_words=50,  # Increased for better selection
                relative_scaling=0.5,
                min_font_size=8,
                stopwords=stop_words,
                regexp=r'\b[a-zA-Z]{3,20}\b',  # Only words with 3-20 letters
                collocations=False
            ).generate(clean_text)
            
            # Get top words and filter out very common/generic terms
            words = list(wordcloud.words_.keys())
            
            # Additional filtering for quality keywords
            filtered_words = []
            generic_words = {'new', 'news', 'report', 'reports', 'today', 'yesterday', 'tomorrow', 'here', 'there', 'where', 'when', 'what', 'how', 'why', 'who'}
            
            for word in words:
                if (len(word) >= 3 and 
                    word.lower() not in generic_words and 
                    not word.isdigit() and 
                    word.isalpha()):
                    filtered_words.append(word)
                    if len(filtered_words) >= 20:  # Limit to top 20 quality keywords
                        break
            
            return ", ".join(filtered_words) if filtered_words else ""
            
        except Exception as e:
            # If WordCloud fails, try simple word extraction
            try:
                import re
                if text and str(text).strip():
                    # Simple word extraction as fallback
                    words = re.findall(r'\b[a-zA-Z]{3,15}\b', str(text))
                    # Remove common stop words
                    stop_words = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had', 'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his', 'how', 'man', 'new', 'now', 'old', 'see', 'two', 'way', 'who', 'boy', 'did', 'its', 'let', 'put', 'say', 'she', 'too', 'use'}
                    filtered_words = [w for w in words if w.lower() not in stop_words]
                    # Get unique words and limit to 15
                    unique_words = list(dict.fromkeys(filtered_words))[:15]
                    return ", ".join(unique_words) if unique_words else ""
            except:
                pass
            logger.warning(f"Error generating word cloud for text length {len(str(text)) if text else 0}: {str(e)}")
            return ""

    def batch_fetch_social_feeds(self, social_feed_ids):
        """Fetch all social feed documents in a single query"""
        docs = {}
        
        # Convert IDs to both string and int formats for matching
        sfid_strs = [str(sfid) for sfid in social_feed_ids]
        sfid_ints = []
        for sfid_str in sfid_strs:
            if sfid_str.isdigit():
                sfid_ints.append(int(sfid_str))
        
        # Try multiple query formats to find documents
        for query_field, query_values in [
            ("socialFeedId", sfid_strs + sfid_ints),
            ("_id", sfid_strs + sfid_ints)
        ]:
            cursor = self.social_feed_collection.find({query_field: {"$in": query_values}})
            for doc in cursor:
                feed_id = str(doc.get("socialFeedId", doc.get("_id", "")))
                if feed_id:
                    docs[feed_id] = doc
        
        return docs
    
    def batch_fetch_social_feed_tags(self, social_feed_ids, company_ids=None):
        """Fetch all social feed tags using proper socialFeedId and company.id fields"""
        tags = defaultdict(list)
        
        if company_ids:
            # Create query conditions for socialFeedId and company.id combinations
            query_conditions = []
            valid_combinations = []
            
            for sfid, company_id in zip(social_feed_ids, company_ids):
                if not is_null_or_na(company_id):
                    # Convert socialFeedId to int for MongoDB query
                    try:
                        sfid_int = int(float(sfid))
                        company_id_str = str(company_id).strip()
                        query_conditions.append({
                            "socialFeedId": sfid_int,
                            "company.id": company_id_str
                        })
                        valid_combinations.append(f"{sfid_int}+{company_id_str}")
                    except (ValueError, OverflowError):
                        logger.warning(f"Invalid socialFeedId for tag search: {sfid}")
            
            logger.info(f"Searching for {len(query_conditions)} socialFeedId+company.id combinations in socialFeedTag collection")
            
            if query_conditions:
                cursor = self.social_feed_tag_collection.find({
                    "$or": query_conditions
                })
                
                found_tags = 0
                found_combinations = set()
                for tag in cursor:
                    # Extract socialFeedId from the tag to map back to feed_id
                    feed_id = str(tag.get("socialFeedId", ""))
                    company_id = tag.get("company", {}).get("id", "")
                    if feed_id:
                        tags[feed_id].append(tag)
                        found_tags += 1
                        found_combinations.add(f"{feed_id}+{company_id}")
                
                logger.info(f"Found {found_tags} company tags in MongoDB socialFeedTag collection")
                
                # Log missing combinations for debugging
                if found_tags < len(valid_combinations):
                    missing_combinations = set(valid_combinations) - found_combinations
                    logger.warning(f"Missing company tags: {len(missing_combinations)} combinations not found")
                    logger.debug(f"Missing combinations: {sorted(list(missing_combinations))[:10]}...")  # Show first 10
        else:
            # Fallback: search by socialFeedId only
            sfid_ints = []
            for sfid in social_feed_ids:
                try:
                    sfid_int = int(float(sfid))
                    sfid_ints.append(sfid_int)
                except (ValueError, OverflowError):
                    logger.warning(f"Invalid socialFeedId for tag search: {sfid}")
            
            logger.info(f"Searching for {len(sfid_ints)} socialFeedIds in socialFeedTag collection (no company filter)")
            
            if sfid_ints:
                cursor = self.social_feed_tag_collection.find({
                    "socialFeedId": {"$in": sfid_ints}
                })
                
                found_tags = 0
                for tag in cursor:
                    feed_id = str(tag.get("socialFeedId", ""))
                    if feed_id:
                        tags[feed_id].append(tag)
                        found_tags += 1
                
                logger.info(f"Found {found_tags} tags in MongoDB socialFeedTag collection")
        
        return tags
    
    def export_skipped_records_to_excel(self, filename="skipped_records_report.xlsx"):
        """Export all skipped/missing records to Excel file"""
        try:
            if not self.skipped_records:
                logger.info("No skipped records to export")
                return
            
            # Create DataFrame from skipped records
            df = pd.DataFrame(self.skipped_records)
            
            # Sort by skip reason for better organization
            df = df.sort_values(['SkipReason', 'SocialFeedId'], na_position='last')
            
            # Export to Excel
            df.to_excel(filename, index=False)
            logger.info(f"Exported {len(self.skipped_records)} skipped records to {filename}")
            
            # Log summary by skip reason
            skip_summary = df['SkipReason'].value_counts()
            logger.info("Skip reasons summary:")
            for reason, count in skip_summary.items():
                logger.info(f"  - {reason}: {count} records")
            
        except Exception as e:
            logger.error(f"Error exporting skipped records to Excel: {str(e)}")
    
    def add_skipped_record(self, row_data, social_feed_id, company_id, skip_reason, row_number=None):
        """Add a skipped record to the collection for export"""
        try:
            # Handle pandas Series properly
            if row_data is not None and hasattr(row_data, 'get'):
                # It's a pandas Series, we can use .get()
                publication_name = row_data.get('PUBLICATIONNAME', '')
                feed_date = row_data.get('FEEDDATE', '')
                author = row_data.get('AUTHOR', '')
                headline = row_data.get('HEADLINE', '')
                summary = row_data.get('SUMMARY', '')
                content = str(row_data.get('CONTENT', ''))[:200] + '...' if pd.notnull(row_data.get('CONTENT')) and row_data.get('CONTENT') else ''
            else:
                # No row data available
                publication_name = feed_date = author = headline = summary = content = ''
            
            skipped_record = {
                'RowNumber': row_number if row_number else '',
                'SocialFeedId': social_feed_id if social_feed_id else '',
                'CompanyId': company_id if company_id else '',
                'PublicationName': publication_name,
                'FeedDate': feed_date,
                'Author': author,
                'Headline': headline,
                'Summary': summary,
                'Content': content,
                'SkipReason': skip_reason
            }
            self.skipped_records.append(skipped_record)
            # Debug logging (remove after testing)
            if len(self.skipped_records) % 10 == 0:  # Log every 10th record
                logger.debug(f"DEBUG: Added skipped record #{len(self.skipped_records)}: {skip_reason}")
        except Exception as e:
            logger.warning(f"Error adding skipped record: {str(e)}")    

    def check_document_exists(self, document_id):
        """Check if document already exists in Elasticsearch"""
        try:
            return self.es.exists(index=self.index_name, id=document_id)
        except Exception as e:
            logger.warning(f"Error checking document existence for {document_id}: {str(e)}")
            return False

    def get_existing_document(self, document_id):
        """Get existing document from Elasticsearch"""
        try:
            response = self.es.get(index=self.index_name, id=document_id)
            return response['_source']
        except Exception as e:
            logger.warning(f"Error getting existing document {document_id}: {str(e)}")
            return None

    def merge_company_tags(self, existing_company_tags, new_company_tags):
        """Merge new company tags with existing ones, avoiding duplicates"""
        if not existing_company_tags:
            return new_company_tags
        
        # Create a map of existing company IDs for quick lookup
        existing_company_ids = {tag.get("id") for tag in existing_company_tags if tag.get("id")}
        
        # Start with existing tags
        merged_tags = list(existing_company_tags)
        
        # Add new tags that don't already exist
        for new_tag in new_company_tags:
            company_id = new_tag.get("id")
            if company_id and company_id not in existing_company_ids:
                merged_tags.append(new_tag)
                logger.info(f"Adding new company tag for company {company_id}")
            elif company_id:
                # Update existing company tag with new data
                for i, existing_tag in enumerate(merged_tags):
                    if existing_tag.get("id") == company_id:
                        # Update the existing tag with new chartData
                        merged_tags[i] = new_tag
                        logger.info(f"Updating existing company tag for company {company_id}")
                        break
        
        return merged_tags

    def build_es_payload_multi_company(self, document, company_tag_data, excel_rows):
        """Build ES payload with multiple companies for a single social feed"""
        try:
            social_feed_id = str(document.get("socialFeedId", document.get("_id", "")))
            
            # Check if document already exists
            document_exists = self.check_document_exists(social_feed_id)
            
            # Process all social feeds regardless of QC tag presence
            if not company_tag_data:
                company_tag_data = []  # Use empty list to continue processing
            
            publication_name = document.get("publicationInfo", {}).get("name", "")
            
            # Get publication info from publicationMasterOnline first
            if publication_name not in self._publication_cache:
                pub_master_info = self.static_collections['publicationMasterOnline'].find_one(
                    {"publicationInfo.publicationName": publication_name}
                ) or {}
                self._publication_cache[publication_name] = pub_master_info
            else:
                pub_master_info = self._publication_cache[publication_name]
            
            # Get location info (same as original method)
            city_id = ""
            city_name = ""
            city_info = {}
            state_id = ""
            state_name = ""
            state_info = {}
            country_code = ""
            country_name = ""
            
            if pub_master_info and pub_master_info.get("publicationInfo", {}).get("cityId"):
                city_id = pub_master_info.get("publicationInfo", {}).get("cityId", "")
                city_info = self.get_city_info_by_id(city_id)
                city_name = city_info.get("cityName", "") if city_info else ""
            else:
                city_name = document.get("location", {}).get("city", "")
                if city_name:
                    city_info_by_name = self.get_city_info(city_name)
                    city_id = city_info_by_name.get("cityInfo", {}).get("cityId", "") if city_info_by_name else ""
                    city_info = city_info_by_name.get("cityInfo", {}) if city_info_by_name else {}
            
            if city_info:
                country_code = city_info.get("countryID", "")
                state_id = city_info.get("stateID", "")
                if state_id:
                    state_info = self.get_state_info(state_id)
                    state_name = state_info.get("stateName", "") if state_info else ""
            
            if not country_code:
                fallback_country_code, fallback_country_name = self.get_country_info(publication_name)
                country_code = fallback_country_code if fallback_country_code else ""
                country_name = fallback_country_name if fallback_country_name else ""
            
            # Generate keywords from content
            headlines = document.get("feedData", {}).get("headlineSnippet", "")
            text = document.get("feedData", {}).get("text", "")
            keywords = self.generate_word_cloud(f"{headlines} {text}")
            
            # Build enriched company tags from all Excel rows for this social feed
            new_company_tags = []
            
            for excel_row in excel_rows:
                company_id = str(excel_row['COMPANYID']) if pd.notnull(excel_row['COMPANYID']) else None
                
                # Skip if company ID is blank or invalid
                if is_null_or_na(company_id):
                    continue
                
                if company_id:
                    company_info = self.get_company_info(company_id)
                    
                    # Find matching tag data for this company
                    matching_tag = None
                    for tag in company_tag_data:
                        if tag.get("company", {}).get("id") == company_id:
                            matching_tag = tag
                            break
                    
                    # Create company entry regardless of QC tag presence
                    if True:  # Always create entry
                        # Build enriched company tag combining all sources
                        enriched_tag = {
                            "id": company_id,
                            "name": clean_string_field(company_info.get("companyInfo", {}).get("companyName", "")),
                            "shortName": clean_string_field(company_info.get("companyInfo", {}).get("shortCompany", "")),
                            "clientArticleTag": matching_tag.get("clientArticleTag", []) if matching_tag and isinstance(matching_tag.get("clientArticleTag", []), list) else [],
                            "chartData": {
                                # From Excel (primary source)
                                "iscore": excel_row.get('ISCORE', 0),
                                "vscore": excel_row.get('VSCORE', 0),
                                "reportingSubject": self.clean_reporting_subject(
                                    self.safe_get_value(excel_row, 'REPORTING_SUBJECT', 'REPORTINGSUBJECT', 'REPORTING SUBJECT'),
                                    'reportingSubject'
                                ),
                                "reportingTone": excel_row.get('REPORTINGTONE', 0),
                                "prominence": excel_row.get('PROMINENCE', 0),
                                "mailerReportingSubject": self.clean_reporting_subject(
                                    self.safe_get_value(excel_row, 'MAILER_REPORTING_SUBJECT', 'REPORTING_SUBJECT', 'REPORTING SUBJECT', 'MAILERREPORTINGSUBJECT'),
                                    'mailerReportingSubject'
                                ),
                                "reach": excel_row.get('REACH', 0),
                                "wordCount": excel_row.get('WORD_COUNT', 0),
                                
                                # From MongoDB document
                                "uniqueVisitors": document.get("socialMediaInfo", {}).get("alexaStats", {}).get("uniqueVisitors", 0),
                                "pageViews": document.get("socialMediaInfo", {}).get("alexaStats", {}).get("pageViews", 0),
                                "sentiment": float(document.get("socialMetrics", {}).get("sentiment", 0.0)),
                                "engagement": document.get("socialMetrics", {}).get("engagement", 0),
                                "urlViews": document.get("socialMediaInfo", {}).get("youtube", {}).get("views", 0),
                                
                                # Generated/computed fields
                                "isOnlineArticle": True,
                                "keywords": keywords,
                                "qcFlag": "RESEARCH"
                            }
                        }
                        new_company_tags.append(enriched_tag)
            
            # If no valid company tags found, return None
            if not new_company_tags:
                logger.info(f"No valid company tags found for social feed ID: {social_feed_id}")
                return None
            
            # Handle existing document - merge company tags
            if document_exists:
                existing_doc = self.get_existing_document(social_feed_id)
                if existing_doc:
                    existing_company_tags = existing_doc.get("companyTag", [])
                    merged_company_tags = self.merge_company_tags(existing_company_tags, new_company_tags)
                    logger.info(f"Document {social_feed_id} exists - merging {len(new_company_tags)} new company tags with {len(existing_company_tags)} existing ones")
                else:
                    merged_company_tags = new_company_tags
                    logger.info(f"Document {social_feed_id} exists but couldn't retrieve - using new company tags only")
            else:
                merged_company_tags = new_company_tags
                logger.info(f"Document {social_feed_id} is new - using {len(new_company_tags)} company tags")
            
            # Debug: Log when we have multiple companies
            if len(merged_company_tags) > 1:
                company_ids = [tag["id"] for tag in merged_company_tags]
                logger.info(f"Multi-company document for social feed {social_feed_id}: {company_ids}")
            
            # Use the first Excel row for headline/link overrides
            first_row = excel_rows[0]
            
            # Build final payload - use upsert for existing documents
            if document_exists:
                # For existing documents, use update with upsert
                payload = {
                    "_op_type": "update",
                    "_index": self.index_name,
                    "_id": social_feed_id,
                    "upsert": {
                        "socialFeedId": int(document.get("socialFeedId", 0)),
                        "feedInfo": {
                            "txnNumber": document.get("feedInfo", {}).get("txnNumber", 0),
                            "socialFeedType": document.get("feedInfo", {}).get("socialFeedType", 0),
                            "isActive": document.get("feedInfo", {}).get("isActive", False)
                        },
                        "feedData": {
                            "feedDate": document.get("feedData", {}).get("feedDate", datetime.now(timezone.utc).strftime('%Y-%m-%d')),
                            "feedDateTime": document.get("feedData", {}).get("feedDateTime", datetime.now(timezone.utc).isoformat()),
                            "articleDateNumber": document.get("feedData", {}).get("articleDateNumber", 0),
                            "language": clean_string_field(document.get("feedData", {}).get("language", "")),
                            "headlineSnippet": clean_string_field(first_row.get('HEADLINE', document.get("feedData", {}).get("headlineSnippet", ""))),
                            "text": clean_string_field(document.get("feedData", {}).get("text", "")),
                            "link": clean_string_field(first_row.get('LINK', document.get("feedData", {}).get("link", "")))
                        },
                        "publicationInfo": {
                            "id": clean_string_field(document.get("publicationInfo", {}).get("id", "")),
                            "name": clean_string_field(first_row.get('PUBLICATION', publication_name))
                        },
                        "location": {
                            "countryCode": country_code,
                            "countryName": clean_string_field(country_name),
                            "city": clean_string_field(city_name),
                            "cityid": clean_string_field(str(city_id) if city_id else "")
                        },
                        "companyTag": merged_company_tags,
                        "author": {
                            "id": clean_string_field(document.get("author", {}).get("id", "")),
                            "name": clean_string_field(document.get("author", {}).get("name", "")),
                            "gender": clean_string_field(document.get("author", {}).get("gender", ""))
                        },
                        "crossLanguageInvertedToken": clean_string_field(document.get("crossLanguageInvertedToken", ""))
                    },
                    "script": {
                        "source": "ctx._source.companyTag = params.newCompanyTags",
                        "params": {
                            "newCompanyTags": merged_company_tags
                        }
                    }
                }
            else:
                # For new documents, use index
                payload = {
                    "_op_type": "index",
                    "_index": self.index_name,
                    "_id": social_feed_id,
                    "_source": {
                        "socialFeedId": int(document.get("socialFeedId", 0)),
                        "feedInfo": {
                            "txnNumber": document.get("feedInfo", {}).get("txnNumber", 0),
                            "socialFeedType": document.get("feedInfo", {}).get("socialFeedType", 0),
                            "isActive": document.get("feedInfo", {}).get("isActive", False)
                        },
                        "feedData": {
                            "feedDate": document.get("feedData", {}).get("feedDate", datetime.now(timezone.utc).strftime('%Y-%m-%d')),
                            "feedDateTime": document.get("feedData", {}).get("feedDateTime", datetime.now(timezone.utc).isoformat()),
                            "articleDateNumber": document.get("feedData", {}).get("articleDateNumber", 0),
                            "language": clean_string_field(document.get("feedData", {}).get("language", "")),
                            "headlineSnippet": clean_string_field(first_row.get('HEADLINE', document.get("feedData", {}).get("headlineSnippet", ""))),
                            "text": clean_string_field(document.get("feedData", {}).get("text", "")),
                            "link": clean_string_field(first_row.get('LINK', document.get("feedData", {}).get("link", "")))
                        },
                        "publicationInfo": {
                            "id": clean_string_field(document.get("publicationInfo", {}).get("id", "")),
                            "name": clean_string_field(first_row.get('PUBLICATION', publication_name))
                        },
                        "location": {
                            "countryCode": country_code,
                            "countryName": clean_string_field(country_name),
                            "city": clean_string_field(city_name),
                            "cityid": clean_string_field(str(city_id) if city_id else "")
                        },
                        "companyTag": merged_company_tags,
                        "author": {
                            "id": clean_string_field(document.get("author", {}).get("id", "")),
                            "name": clean_string_field(document.get("author", {}).get("name", "")),
                            "gender": clean_string_field(document.get("author", {}).get("gender", ""))
                        },
                        "crossLanguageInvertedToken": clean_string_field(document.get("crossLanguageInvertedToken", ""))
                    }
                }
            
            return convert_data_types(convert_boolean_fields(replace_nan_with_null(payload)))
        except Exception as e:
            logger.error(f"Error building multi-company payload for social feed {document.get('socialFeedId', 'unknown')}: {str(e)}")
            logger.exception("Full error details:")
            return None

    def build_es_payload(self, document, company_tag_data, excel_row):
        try:
            social_feed_id = str(document.get("socialFeedId", document.get("_id", "")))
            
            # Process all social feeds regardless of QC tag presence
            if not company_tag_data:
                company_tag_data = []  # Use empty list to continue processing
            
            publication_name = document.get("publicationInfo", {}).get("name", "")
            
            # Get publication info from publicationMasterOnline first
            if publication_name not in self._publication_cache:
                pub_master_info = self.static_collections['publicationMasterOnline'].find_one(
                    {"publicationInfo.publicationName": publication_name}
                ) or {}
                self._publication_cache[publication_name] = pub_master_info
            else:
                pub_master_info = self._publication_cache[publication_name]
            
            # Get city ID from publicationMasterOnline, fallback to social feed document
            city_id = ""
            city_name = ""
            city_info = {}
            state_id = ""
            state_name = ""
            state_info = {}
            country_code = ""
            country_name = ""
            
            if pub_master_info and pub_master_info.get("publicationInfo", {}).get("cityId"):
                # Use city ID from publicationMasterOnline
                city_id = pub_master_info.get("publicationInfo", {}).get("cityId", "")
                city_info = self.get_city_info_by_id(city_id)
                city_name = city_info.get("cityName", "") if city_info else ""
            else:
                # Fallback to city name from social feed document
                city_name = document.get("location", {}).get("city", "")
                if city_name:
                    city_info_by_name = self.get_city_info(city_name)
                    city_id = city_info_by_name.get("cityInfo", {}).get("cityId", "") if city_info_by_name else ""
                    city_info = city_info_by_name.get("cityInfo", {}) if city_info_by_name else {}
            
            # Get state and country info from cityMaster
            if city_info:
                # Get country info from cityMaster
                country_code = city_info.get("countryID", "")
                
                # Get state info from cityMaster and then stateMaster
                state_id = city_info.get("stateID", "")
                if state_id:
                    state_info = self.get_state_info(state_id)
                    state_name = state_info.get("stateName", "") if state_info else ""
            
            # Fallback to get_country_info if no country found in cityMaster
            if not country_code:
                fallback_country_code, fallback_country_name = self.get_country_info(publication_name)
                country_code = fallback_country_code if fallback_country_code else ""
                country_name = fallback_country_name if fallback_country_name else ""
            
            # Generate keywords from content
            headlines = document.get("feedData", {}).get("headlineSnippet", "")
            text = document.get("feedData", {}).get("text", "")
            keywords = self.generate_word_cloud(f"{headlines} {text}")
            
            # Build enriched company tag from all sources
            enriched_company_tags = []
            
            # Get company info from Excel
            company_id = str(excel_row['COMPANYID']) if pd.notnull(excel_row['COMPANYID']) else None
            
            # Skip if company ID is blank or invalid
            if is_null_or_na(company_id):
                return None
            
            if company_id:
                company_info = self.get_company_info(company_id)
                

                
                # Find matching tag data for this company
                matching_tag = None
                for tag in company_tag_data:
                    if tag.get("company", {}).get("id") == company_id:
                        matching_tag = tag
                        break
                
                # Create company entry regardless of QC tag presence
                if True:  # Always create entry
                    # Build enriched company tag combining all sources
                    enriched_tag = {
                        "id": company_id,
                        "name": company_info.get("companyInfo", {}).get("companyName", ""),
                        "shortName": company_info.get("companyInfo", {}).get("shortCompany", ""),
                        "clientArticleTag": matching_tag.get("clientArticleTag", []) if matching_tag and isinstance(matching_tag.get("clientArticleTag", []), list) else [],
                        "chartData": {
                            # From Excel (primary source)
                            "iscore": excel_row.get('ISCORE', 0),
                            "vscore": excel_row.get('VSCORE', 0),
                            "reportingTone": excel_row.get('REPORTINGTONE', 0),
                            "prominence": excel_row.get('PROMINENCE', 0),
                            "mailerReportingSubject": self.clean_reporting_subject(
                                self.safe_get_value(excel_row, 'MAILER_REPORTING_SUBJECT', 'REPORTING_SUBJECT', 'REPORTING SUBJECT', 'MAILERREPORTINGSUBJECT'),
                                'mailerReportingSubject'
                            ),
                            "reach": excel_row.get('REACH', 0),
                            "wordCount": excel_row.get('WORDCOUNT', 0),
                            
                            # From MongoDB document
                            "uniqueVisitors": document.get("socialMediaInfo", {}).get("alexaStats", {}).get("uniqueVisitors", 0),
                            "pageViews": document.get("socialMediaInfo", {}).get("alexaStats", {}).get("pageViews", 0),
                            "sentiment": float(document.get("socialMetrics", {}).get("sentiment", 0.0)),
                            "engagement": document.get("socialMetrics", {}).get("engagement", 0),
                            "urlViews": document.get("socialMediaInfo", {}).get("youtube", {}).get("views", 0),
                            
                            # Generated/computed fields
                            "isOnlineArticle": True,
                            "keywords": keywords,
                            "qcFlag": "RESEARCH"
                        }
                    }
                    enriched_company_tags.append(enriched_tag)
            
            # Build final payload
            payload = {
                "_op_type": "index",
                "_index": self.index_name,
                "_id": social_feed_id,
                "_source": {
                    "socialFeedId": int(document.get("socialFeedId", 0)),
                    "feedInfo": {
                        "txnNumber": document.get("feedInfo", {}).get("txnNumber", 0),
                        "socialFeedType": document.get("feedInfo", {}).get("socialFeedType", 0),
                        "isActive": document.get("feedInfo", {}).get("isActive", False)
                    },
                    "feedData": {
                        "feedDate": document.get("feedData", {}).get("feedDate", datetime.now(timezone.utc).strftime('%Y-%m-%d')),
                        "feedDateTime": document.get("feedData", {}).get("feedDateTime", datetime.now(timezone.utc).isoformat()),
                        "articleDateNumber": document.get("feedData", {}).get("articleDateNumber", 0),
                        "language": clean_string_field(document.get("feedData", {}).get("language", "")),
                        "headlineSnippet": clean_string_field(excel_row.get('HEADLINE', document.get("feedData", {}).get("headlineSnippet", ""))),
                        "text": clean_string_field(document.get("feedData", {}).get("text", "")),
                        "link": clean_string_field(excel_row.get('LINK', document.get("feedData", {}).get("link", "")))
                    },
                    "publicationInfo": {
                        "id": clean_string_field(document.get("publicationInfo", {}).get("id", "")),
                        "name": clean_string_field(excel_row.get('PUBLICATION', publication_name))
                    },
                    "location": {
                        "countryCode": country_code,
                        "countryName": clean_string_field(country_name),
                        "city": clean_string_field(city_name),
                        "cityid": clean_string_field(str(city_id) if city_id else "")
                    },
                    "companyTag": enriched_company_tags,
                    "author": {
                        "id": clean_string_field(document.get("author", {}).get("id", "")),
                        "name": clean_string_field(document.get("author", {}).get("name", "")),
                        "gender": clean_string_field(document.get("author", {}).get("gender", ""))
                    },
                    "crossLanguageInvertedToken": clean_string_field(document.get("crossLanguageInvertedToken", ""))
                }
            }
            
            return convert_data_types(convert_boolean_fields(replace_nan_with_null(payload)))
        except Exception as e:
            logger.error(f"Error building payload for social feed {document.get('socialFeedId', 'unknown')}: {str(e)}")
            logger.exception("Full error details:")
            return None

    def run(self):
        logger.info(f"Reading Excel file: {self.excel_path}")
        
        df = pd.read_excel(self.excel_path)
        df.columns = df.columns.str.strip().str.upper()  # Clean and uppercase all column names
        logger.info(f"Excel columns: {df.columns.tolist()}")
        
        # Check for reporting subject columns specifically
        reporting_subject_cols = [col for col in df.columns if 'REPORTING' in col and 'SUBJECT' in col]
        if reporting_subject_cols:
            logger.info(f"Found reporting subject columns: {reporting_subject_cols}")
        else:
            logger.warning("No reporting subject columns found in Excel file")

        # Filter rows where REPEATITION == 'Unique'
        if 'REPEATITION' in df.columns:
            df = df[df['REPEATITION'] == 'Unique']

        # Drop the first column if it's not a feed ID column
        if df.columns[0] not in ['SOCIAL_FEED_ID', 'SOCIALFEEDID', 'SOCIAL FEED ID']:
            df = df.iloc[:, 1:]

        total_rows = len(df)
        logger.info(f"Processing {total_rows} rows from Excel")
        
        # Process in batches for better performance
        BATCH_SIZE = 500  # Increase back to 500 since it's working well
        total_inserted = 0
        
        for batch_start in range(0, total_rows, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total_rows)
            batch_df = df.iloc[batch_start:batch_end]
            
            logger.info(f"Processing batch {batch_start//BATCH_SIZE + 1}: rows {batch_start+1}-{batch_end}")
            
            # Get all social feed IDs for this batch - handle different column name formats
            social_feed_id_col = None
            for col in ['SOCIAL_FEED_ID', 'SOCIALFEEDID', 'SOCIAL FEED ID', 'SOCIAL_FEED_ID', 'SOCIALFEEDID']:
                if col in batch_df.columns:
                    social_feed_id_col = col
                    break
            
            if not social_feed_id_col:
                logger.error("Could not find social feed ID column in Excel")
                logger.error(f"Available columns: {batch_df.columns.tolist()}")
                continue
                
            # Convert scientific notation to integers properly - only collect valid IDs
            social_feed_ids = []
            
            for idx_val, (row_idx, val) in enumerate(zip(batch_df.index, batch_df[social_feed_id_col])):
                if pd.notnull(val) and not pd.isna(val):
                    try:
                        # Convert scientific notation to integer
                        current_id = int(float(val))
                        social_feed_ids.append(current_id)
                    except (ValueError, OverflowError) as e:
                        logger.warning(f"Invalid social feed ID value: {val}, error: {e}")
                else:
                    # Skip null/missing social feed IDs - do not use fallback logic
                    logger.info(f"SKIP: Row {row_idx+1} - null/missing social feed ID")
                    # Add to skipped records (we don't have row data here, so pass None)
                    self.add_skipped_record(None, None, None, "Null/Missing Social Feed ID in Excel", row_idx+1)
            
            # Batch fetch MongoDB documents
            social_feeds = self.batch_fetch_social_feeds(social_feed_ids)
            logger.info(f"Found {len(social_feeds)} social feeds in MongoDB out of {len(social_feed_ids)} requested")
            
            # Log missing IDs for debugging
            if len(social_feeds) < len(social_feed_ids):
                found_ids = set(str(key) for key in social_feeds.keys())
                requested_ids = set(str(id) for id in social_feed_ids)
                missing_ids = requested_ids - found_ids
                logger.info(f"Missing social feed IDs: {sorted(missing_ids, key=lambda x: int(x) if x.isdigit() else 0)}")
            
            # Extract company IDs from batch for articleidcompanyid search
            company_ids = []
            for idx, row in batch_df.iterrows():
                company_id = str(row['COMPANYID']) if pd.notnull(row['COMPANYID']) else None
                # Only include valid company IDs
                if not is_null_or_na(company_id):
                    company_ids.append(company_id)
                else:
                    company_ids.append(None)  # Keep index alignment
            
            # Batch fetch social feed tags using articleidcompanyid in _id field
            social_feed_tags = self.batch_fetch_social_feed_tags(social_feed_ids, company_ids)
            logger.info(f"Found tags for {len(social_feed_tags)} social feeds out of {len(set(social_feed_ids))} unique social feed IDs")
            
            # Log which social feeds have no tags
            feeds_with_tags = set(social_feed_tags.keys())
            feeds_without_tags = set(str(sfid) for sfid in social_feed_ids) - feeds_with_tags
            if feeds_without_tags:
                logger.warning(f"Social feeds without company tags: {sorted(feeds_without_tags)}")
            
            # Group Excel rows by social feed ID to handle multiple companies per feed
            feed_groups = {}
            
            for idx, row in batch_df.iterrows():
                # Skip rows with blank company ID
                company_id = str(row['COMPANYID']) if pd.notnull(row['COMPANYID']) else None
                if is_null_or_na(company_id):
                    logger.info(f"SKIP: Row {idx+1} - blank or invalid company ID: '{company_id}'")
                    self.add_skipped_record(row, row.get(social_feed_id_col), company_id, "Blank/Invalid Company ID", idx+1)
                    continue
                
                # Skip rows with null/missing social feed ID - no fallback logic
                if pd.isnull(row[social_feed_id_col]) or pd.isna(row[social_feed_id_col]):
                    logger.info(f"SKIP: Row {idx+1} - null/missing social feed ID")
                    self.add_skipped_record(row, None, company_id, "Null/Missing Social Feed ID", idx+1)
                    continue
                
                # Convert scientific notation to integer properly
                try:
                    social_feed_id = int(float(row[social_feed_id_col]))
                except (ValueError, OverflowError) as e:
                    logger.warning(f"Invalid social feed ID at row {idx}: {row[social_feed_id_col]}, error: {e}")
                    continue
                
                # Group rows by social feed ID
                if social_feed_id not in feed_groups:
                    feed_groups[social_feed_id] = []
                feed_groups[social_feed_id].append(row)
            
            # Build payloads for each social feed (with all its companies)
            payloads = []
            
            for social_feed_id, rows in feed_groups.items():
                social_feed_doc = social_feeds.get(str(social_feed_id))
                if not social_feed_doc:
                    company_ids_in_group = [str(row['COMPANYID']) for row in rows if pd.notnull(row['COMPANYID'])]
                    logger.info(f"SKIP: Social Feed ID {social_feed_id} not found in MongoDB - affects {len(rows)} row(s) with company IDs: {', '.join(company_ids_in_group)}")
                    # Add all rows in this group to skipped records
                    for row in rows:
                        company_id = str(row['COMPANYID']) if pd.notnull(row['COMPANYID']) else None
                        self.add_skipped_record(row, social_feed_id, company_id, "Social Feed ID not found in MongoDB")
                    continue
                
                # Get social feed tag data for this feed (all companies)
                social_feed_tag_data = social_feed_tags.get(str(social_feed_id), [])
                
                # Log company tag availability and collect missing tags data
                if not social_feed_tag_data:
                    company_ids_in_group = [str(row['COMPANYID']) for row in rows if pd.notnull(row['COMPANYID'])]
                    logger.info(f"NO TAGS: Social Feed ID {social_feed_id} has no company tags in MongoDB for company IDs: {', '.join(company_ids_in_group)}")
                    
                    # Collect missing tags data for export
                    for row in rows:
                        if pd.notnull(row['COMPANYID']):
                            company_id = str(row['COMPANYID'])
                            self.add_skipped_record(row, social_feed_id, company_id, "Missing Company Tags in MongoDB")
                else:
                    logger.info(f"TAGS FOUND: Social Feed ID {social_feed_id} has {len(social_feed_tag_data)} company tag(s)")
                
                # Build payload with all companies for this social feed
                payload = self.build_es_payload_multi_company(social_feed_doc, social_feed_tag_data, rows)
                    
                if payload:
                    payloads.append(payload)
                    # Log successful payload creation with details
                    company_ids_in_payload = [str(row['COMPANYID']) for row in rows if pd.notnull(row['COMPANYID'])]
                    logger.info(f"PREPARED: Social Feed ID {social_feed_id} with {len(rows)} row(s) and company IDs: {', '.join(company_ids_in_payload)}")
                else:
                    company_ids_in_group = [str(row['COMPANYID']) for row in rows if pd.notnull(row['COMPANYID'])]
                    logger.info(f"SKIP: No payload generated for Social Feed ID {social_feed_id} - affects {len(rows)} row(s) with company IDs: {', '.join(company_ids_in_group)}")
                    # Add all rows in this group to skipped records
                    for row in rows:
                        company_id = str(row['COMPANYID']) if pd.notnull(row['COMPANYID']) else None
                        self.add_skipped_record(row, social_feed_id, company_id, "No Payload Generated")
            
            # Bulk insert this batch
            if payloads:
                try:
                    from elasticsearch import helpers
                    success_count, failed_items = helpers.bulk(
                        self.es, 
                        payloads,
                        chunk_size=100,  # Process in smaller chunks within the batch
                        initial_backoff=2,
                        max_backoff=600,
                        request_timeout=60,
                        max_retries=3
                    )
                    total_inserted += success_count
                    
                    # Log each successful insertion
                    for payload in payloads[:success_count]:
                        social_feed_id = payload.get('_id', 'unknown')
                        logger.info(f"INSERTED: Social Feed ID {social_feed_id} successfully inserted to Elasticsearch")
                    
                    if failed_items:
                        logger.warning(f"Failed to insert {len(failed_items)} documents in batch")
                        
                except helpers.BulkIndexError as e:
                    # Get detailed error information
                    success_count = 0
                    failed_count = 0
                    
                    for error in e.errors:
                        error_detail = error.get('index', {}).get('error', {})
                        social_feed_id = error.get('index', {}).get('_id', 'unknown')
                        status = error.get('index', {}).get('status')
                        
                        if status in [200, 201]:
                            success_count += 1
                            logger.info(f"INSERTED: Social Feed ID {social_feed_id} successfully inserted to Elasticsearch")
                        else:
                            failed_count += 1
                            logger.error(f"FAILED: Social Feed ID {social_feed_id} - {error_detail.get('type', 'unknown')}: {error_detail.get('reason', 'no reason')}")
                    
                    total_inserted += success_count
                    
                except Exception as e:
                    logger.error(f"FAILED: Batch insertion error - {str(e)}")
            else:
                logger.info(f"BATCH: No valid payloads to insert in this batch")
            
            logger.info(f"BATCH SUMMARY: {len(payloads)} documents prepared, {len(payloads)} attempted insertions, running total inserted: {total_inserted}")
        
        logger.info(f"Completed: {total_inserted} documents inserted into Elasticsearch index '{self.index_name}'.")
        
        # Debug: Log skipped records count
        logger.info(f"DEBUG: Total skipped records collected: {len(self.skipped_records)}")
        
        # Export all skipped records to Excel
        if self.skipped_records:
            logger.info(f"Exporting {len(self.skipped_records)} skipped records to Excel...")
            self.export_skipped_records_to_excel("skipped_records_report.xlsx")
        else:
            logger.info("No skipped records found - all records were processed successfully")


    
def main():
    parser = argparse.ArgumentParser(description="Sync Excel data with MongoDB social feeds to Elasticsearch")
    parser.add_argument(
        "--recreate-index", 
        action="store_true", 
        help="Delete and recreate the Elasticsearch index with new mapping"
    )
    
    args = parser.parse_args()
    
    inserter = ExcelToElasticsearchSocialInserter(EXCEL_PATH, MONGO_COLLECTION, MONGO_TAG_COLLECTION, ES_INDEX)
    
    # Force recreate index if requested
    if args.recreate_index:
        inserter._create_index_mapping(force_recreate=True)
    
    inserter.run()

if __name__ == "__main__":
    main()