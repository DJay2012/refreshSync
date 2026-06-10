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

IndexName = 'printarticlereport'
index_name = IndexName

# configuration
EXCEL_PATH = 'HDFC_ERGO/PRINT/HDFC ERGO MAY 2025 - PRINT.xlsx'
MONGO_COLLECTION = 'article'
ES_INDEX = 'printarticlereport'

def replace_nan_with_null(data):
    """Replace NaN values with None for Elasticsearch compatibility"""
    if isinstance(data, dict):
        return {k: replace_nan_with_null(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [replace_nan_with_null(item) for item in data]
    elif isinstance(data, float) and math.isnan(data):
        return None  # Replace NaN with Elasticsearch-compatible null
    return data

class ExcelToElasticsearchInserter:
    """
    Handles reading Excel, merging with MongoDB, and inserting into Elasticsearch.
    Reuses payload-building logic from the old sync manager.
    """
    # Static mappings (copied from ChangeStreamSyncManager)
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
    PUBLICATION_CATEGORY_MAPPING = {
        "N": "News",
        "M": "Magazine",
        "S": "Sports",
        "P": "Politics",
    }

    def __init__(self, excel_path, source_collection_name, es_index_name):
        self.excel_path = excel_path
        self.mongo_db = mongo()
        self.es = elastic()
        self.index_name = es_index_name
        self.article_collection = self.mongo_db[source_collection_name]
        self.article_tag_collection = self.mongo_db["articleTag"]
        self.reporting_subject_collection = self.mongo_db["reportingSubject"]
        self.static_collections = {
            'clientMaster': self.mongo_db["clientMaster"],
            'companyMaster': self.mongo_db["companyMaster"],
            'cityMaster': self.mongo_db["cityMaster"],
            'stateMaster': self.mongo_db["stateMaster"],
            'publicationMaster': self.mongo_db["publicationMaster"],
            'articleSimilar': self.mongo_db["articleSimilar"]
        }
        
        # Cache for frequently accessed data
        self._city_cache = {}
        self._state_cache = {}
        self._publication_cache = {}
        self._company_cache = {}
        self._reporting_subject_cache = {}

    def process_company_tags(self, company_tag_data):
        # Process all company tags without QC filtering for Excel upload
        processed_tags = []
        for tag in company_tag_data:
            # Include all company tags regardless of QC status
            processed_tags.append(tag)
        return processed_tags

    def get_state_id_by_city_id(self, city_id):
        if city_id not in self._city_cache:
            city = self.static_collections['cityMaster'].find_one({'cityInfo.cityId': city_id})
            self._city_cache[city_id] = city
        city = self._city_cache[city_id]
        return city.get('cityInfo', {}).get('stateID') if city else None

    def get_zone_by_state_id(self, state_id):
        if state_id not in self._state_cache:
            state = self.static_collections['stateMaster'].find_one({'stateId': state_id})
            self._state_cache[state_id] = state
        state = self._state_cache[state_id]
        return state.get('zone') if state else None

    def get_publication_info(self, publication_id):
        if publication_id not in self._publication_cache:
            pub_info = self.static_collections['publicationMaster'].find_one({'publicationInfo.publicationId': publication_id}) or {}
            self._publication_cache[publication_id] = pub_info
        return self._publication_cache[publication_id]

    def get_country_id_by_city_id(self, city_id):
        if city_id not in self._city_cache:
            city = self.static_collections['cityMaster'].find_one({'cityInfo.cityId': city_id})
            self._city_cache[city_id] = city
        city = self._city_cache[city_id]
        return city.get('cityInfo', {}).get('countryID') if city else None

    def get_city_name_by_city_id(self, city_id):
        """Get proper city name from cityMaster collection"""
        if city_id not in self._city_cache:
            city = self.static_collections['cityMaster'].find_one({'cityInfo.cityId': city_id})
            self._city_cache[city_id] = city
        city = self._city_cache[city_id]
        return city.get('cityInfo', {}).get('cityName', '') if city else ''

    def get_company_info(self, company_id):
        """Get company name and shortName from companyMaster using company_id as _id"""
        if company_id not in self._company_cache:
            try:
                # Ensure company_id is treated as string to match MongoDB _id format
                company_id_str = str(company_id)
                company = self.static_collections['companyMaster'].find_one({'_id': company_id_str})
                if company:
                    company_info = company.get('companyInfo', {})
                    self._company_cache[company_id] = {
                        'name': company_info.get('companyName', ''),
                        'shortName': company_info.get('shortCompany', '')
                    }
                    logger.debug(f"Found company info for {company_id}: {company_info.get('companyName', '')} / {company_info.get('shortCompany', '')}")
                else:
                    self._company_cache[company_id] = {'name': '', 'shortName': ''}
                    logger.debug(f"Company {company_id} not found in companyMaster")
            except Exception as e:
                logger.warning(f"Error fetching company info for {company_id}: {str(e)}")
                self._company_cache[company_id] = {'name': '', 'shortName': ''}
        
        return self._company_cache[company_id]

    def validate_reporting_subject(self, reporting_subject_name):
        """
        Check if reportingSubject exists in MongoDB collection
        """
        if not reporting_subject_name or str(reporting_subject_name).strip() == '':
            return False
            
        reporting_subject_name = str(reporting_subject_name).strip()
        
        try:
            # Check cache first
            if reporting_subject_name in self._reporting_subject_cache:
                return self._reporting_subject_cache[reporting_subject_name]
            
            # Query MongoDB
            result = self.reporting_subject_collection.find_one(
                {"reportingSubjectInfo.name": reporting_subject_name}
            )
            
            is_valid = result is not None
            self._reporting_subject_cache[reporting_subject_name] = is_valid
            return is_valid
            
        except Exception as e:
            logger.error(f"Error validating reportingSubject '{reporting_subject_name}': {str(e)}")
            return False

    def getLanguageName(self, code):
        """Decode language codes to language codes (standardized)"""
        languages = [
            {'id': 'as', 'name': 'Assamese'}, 
            {'id': 'bn', 'name': 'Bengali'}, 
            {'id': 'gu', 'name': 'Gujarati'}, 
            {'id': 'hi', 'name': 'Hindi'}, 
            {'id': 'kn', 'name': 'Kannada'}, 
            {'id': 'en', 'name': 'English'}, 
            {'id': 'ml', 'name': 'Malayalam'}, 
            {'id': 'mni', 'name': 'Manipuri'}, 
            {'id': 'mr', 'name': 'Marathi'}, 
            {'id': 'ne', 'name': 'Nepali'}, 
            {'id': 'or', 'name': 'Odia (Oriya)'}, 
            {'id': 'pa', 'name': 'Punjabi'}, 
            {'id': 'sa', 'name': 'Sanskrit'}, 
            {'id': 'ta', 'name': 'Tamil'}, 
            {'id': 'te', 'name': 'Telugu'}, 
            {'id': 'ur', 'name': 'Urdu'}
        ]
        
        if not code or pd.isna(code) or str(code).strip() == '':
            return ""
            
        code_str = str(code).strip()
        
        # First try to match by language code (id) - return the code
        for language in languages:
            if language["id"].lower() == code_str.lower():
                return language["id"]
        
        # Then try to match by language name (case insensitive) - return the code
        for language in languages:
            if language["name"].upper() == code_str.upper():
                return language["id"]
        
        # If no match found, return the original code in lowercase
        return code_str.lower() if code_str else ""

    def format_language_to_sentence_case(self, language):
        """Format language string to sentence case"""
        if not language or pd.isna(language) or str(language).strip() == '':
            return ""
        
        language_str = str(language).strip().lower()
        
        # Filter out invalid language values
        invalid_values = ['n/a', 'na', 'null', 'none', '-', '--']
        if language_str in invalid_values:
            return ""
        
        # Capitalize first letter only
        return language_str.capitalize() if language_str else ""

    def clean_journalist_field(self, journalist_value):
        """Clean journalist field to handle NaN and empty values properly"""
        if pd.isna(journalist_value) or journalist_value is None:
            return None
        
        # Convert to string and clean
        journalist_str = str(journalist_value).strip()
        
        # Handle various representations of empty/null values
        invalid_values = ['nan', 'n/a', 'na', 'null', 'none', '-', '--', '']
        if journalist_str.lower() in invalid_values:
            return None
        
        # Return cleaned string or None if empty
        return journalist_str if journalist_str else None

    def parse_page_number(self, page_value):
        """
        Parse page number value to handle comma-separated values
        Returns the first page number as integer, or 0 if invalid
        """
        try:
            if pd.isna(page_value) or page_value == '':
                return 0
            
            # Convert to string and handle comma-separated values
            page_str = str(page_value).strip()
            if ',' in page_str:
                # Take the first page number from comma-separated list
                first_page = page_str.split(',')[0].strip()
                return int(float(first_page)) if first_page else 0
            else:
                # Single page number
                return int(float(page_str)) if page_str else 0
                
        except (ValueError, TypeError) as e:
            logger.warning(f"Could not parse page number '{page_value}': {str(e)}")
            return 0

    def parse_numeric_field(self, value):
        """
        Parse numeric field, handling cases where URLs or non-numeric data might be present
        Returns numeric value or 0 if invalid
        """
        try:
            if pd.isna(value) or value == '':
                return 0
            
            # Convert to string first
            value_str = str(value).strip()
            
            # Check if it's a URL or contains non-numeric characters
            if 'http' in value_str.lower() or 'www.' in value_str.lower():
                logger.debug(f"Skipping URL in numeric field: {value_str[:50]}...")
                return 0
            
            # Try to parse as float first, then convert to int
            return int(float(value_str)) if value_str else 0
                
        except (ValueError, TypeError) as e:
            logger.debug(f"Could not parse numeric field '{value}': {str(e)}")
            return 0

    def parse_decimal_field(self, value):
        """
        Parse decimal field, preserving decimal places for score fields
        Returns decimal value or 0.0 if invalid
        """
        try:
            if pd.isna(value) or value == '':
                return 0.0
            
            # Convert to string first
            value_str = str(value).strip()
            
            # Check if it's a URL or contains non-numeric characters
            if 'http' in value_str.lower() or 'www.' in value_str.lower():
                logger.debug(f"Skipping URL in decimal field: {value_str[:50]}...")
                return 0.0
            
            # Parse as float to preserve decimal places
            return float(value_str) if value_str else 0.0
                
        except (ValueError, TypeError) as e:
            logger.debug(f"Could not parse decimal field '{value}': {str(e)}")
            return 0.0

    def is_english_word(self, word):
        """Check if a word contains only English characters"""
        import re
        # Check if word contains only ASCII letters, numbers, and basic punctuation
        return bool(re.match(r'^[a-zA-Z0-9\s\-\'\.]+$', word.strip()))
    
    def generate_word_cloud(self, text):
        """Generate word cloud from text - English keywords only"""
        try:
            if not text or not str(text).strip():
                return ""
            
            # Clean the text and limit length to avoid processing issues
            clean_text = str(text).strip()[:5000]  # Reduced from 10000 to 5000
            if len(clean_text) < 3:
                return ""
            
            # Additional text cleaning to avoid WordCloud issues
            import re
            # Remove excessive whitespace and special characters that might cause issues
            clean_text = re.sub(r'\s+', ' ', clean_text)  # Normalize whitespace
            clean_text = re.sub(r'[^\w\s\-\.\,\;\:]', ' ', clean_text)  # Keep basic punctuation only
            
            if len(clean_text.strip()) < 3:
                return ""
            
            # Use WordCloud with more conservative settings
            wordcloud = WordCloud(
                width=400,  # Reduced from 800
                height=200, # Reduced from 400
                background_color="white",
                max_words=30,  # Reduced from 50
                relative_scaling=0.5,
                min_font_size=10,  # Increased from 8
                collocations=False,
                max_font_size=100,  # Add max font size limit
                prefer_horizontal=0.9  # Prefer horizontal text
            ).generate(clean_text)
            
            # Filter keywords to include only English words
            english_keywords = []
            for word in wordcloud.words_.keys():
                if self.is_english_word(word) and len(word.strip()) > 2:
                    english_keywords.append(word)
                    if len(english_keywords) >= 20:  # Limit to top 20 keywords
                        break
            
            # Return top English keywords as comma-separated string
            return ", ".join(english_keywords)
            
        except Exception as e:
            logger.debug(f"WordCloud generation failed: {str(e)}")
            return ""

    def create_document_from_excel(self, article_id, excel_row):
        """Create a minimal MongoDB-like document structure from Excel data"""
        try:
            from datetime import datetime
            import pandas as pd
            
            # Parse article date from Excel
            article_date = excel_row.get('ARTICLEDATE')
            if pd.notnull(article_date):
                if isinstance(article_date, str):
                    try:
                        article_date = datetime.strptime(article_date, '%Y-%m-%d')
                    except:
                        try:
                            article_date = datetime.strptime(article_date, '%d-%m-%Y')
                        except:
                            article_date = datetime.now()
                elif not isinstance(article_date, datetime):
                    article_date = datetime.now()
            else:
                article_date = datetime.now()
            
            # Get publication info from Excel
            publication_name = excel_row.get('PUBLICATIONNAME', 'Others')
            publication_city = excel_row.get('PUBLICATIONCITY', '')
            
            # Try to find publication in publicationMaster
            publication_info = {}
            if publication_name and publication_name != 'Others' and isinstance(publication_name, str):
                try:
                    pub_doc = self.static_collections['publicationMaster'].find_one({
                        'publicationInfo.publicationName': {'$regex': str(publication_name), '$options': 'i'}
                    })
                    if pub_doc:
                        publication_info = pub_doc.get('publicationInfo', {})
                except Exception as e:
                    logger.debug(f"Error looking up publication '{publication_name}': {str(e)}")
            
            # Get city info from Excel
            city_id = 0
            if publication_city and isinstance(publication_city, str):
                try:
                    city_doc = self.static_collections['cityMaster'].find_one({
                        'cityInfo.cityName': {'$regex': str(publication_city), '$options': 'i'}
                    })
                    if city_doc:
                        city_id = city_doc.get('cityInfo', {}).get('cityId', 0)
                except Exception as e:
                    logger.debug(f"Error looking up city '{publication_city}': {str(e)}")
            
            # Create minimal document structure
            document = {
                "articleId": article_id,
                "articleInfo": {
                    "articleDate": article_date,
                    "articleMonth": article_date.month,
                    "articleYear": article_date.year,
                    "journalist": self.clean_journalist_field(excel_row.get('JOURNALIST', '')),
                    "cityId": city_id,
                },
                "articleData": {
                    "headline": excel_row.get('HEADLINES', ''),
                    "text": excel_row.get('ARTICLECONTENT', ''),
                    "content": excel_row.get('ARTICLESUMMARY', ''),
                    "language": excel_row.get('LANGUAGE', ''),
                    "box": excel_row.get('BOX', ''),
                },
                "publicationInfo": {
                    "id": publication_info.get('publicationId', ''),
                    "name": publication_name,
                    "pubGroupId": publication_info.get('publicationGroupID', ''),
                    "editionType": publication_info.get('editionType', excel_row.get('EDITIONTYPE', '')),
                    "pubType": publication_info.get('publicationType', excel_row.get('PUBLICATIONTYPE', '')),
                },
                "crossLanguageInvertedToken": ""
            }
            
            logger.debug(f"Created document from Excel for article {article_id}")
            return document
            
        except Exception as e:
            logger.error(f"Error creating document from Excel for article {article_id}: {str(e)}")
            return None

    def build_es_payload(self, document, company_tag_data, excel_company_data, existing_company_tags=None, document_exists=False):
        try:
            article_id = document["articleId"]
            
            # Get basic info from article document
            city_id = document.get("articleInfo", {}).get("cityId", 0)
            state_id = self.get_state_id_by_city_id(city_id)
            zone = self.get_zone_by_state_id(state_id) if state_id else "Others"
            publication_id = document.get("publicationInfo", {}).get("id", "")
            publication_info = self.get_publication_info(publication_id)
            
            # Build enriched company tags for all companies specified in Excel
            enriched_company_tags = []
            
            # Group Excel data by company ID to handle duplicates
            company_excel_data = {}
            for excel_company in excel_company_data:
                company_id = excel_company['company_id']
                if company_id not in company_excel_data:
                    company_excel_data[company_id] = []
                company_excel_data[company_id].append(excel_company['row_data'])
            
            # Process each unique company from Excel data
            for company_id, excel_rows in company_excel_data.items():
                # Use the first row as primary data (or you could aggregate if needed)
                excel_row = excel_rows[0]
                
                if len(excel_rows) > 1:
                    logger.warning(f"Article {article_id}, Company {company_id}: Found {len(excel_rows)} duplicate rows, using first row data")
                
                # Find matching company tag data for this specific company from articleTag collection
                matching_tag_data = [tag for tag in company_tag_data if tag.get('company', {}).get('id') == company_id]
                
                # Get company name and shortName from companyMaster using company_id as _id
                company_info = self.get_company_info(company_id)
                company_name = company_info.get('name', '') or excel_row.get('COMPANYNAME', '')
                company_short_name = company_info.get('shortName', '') or excel_row.get('COMPANYNAME', '')
                
                # Generate keywords from available text content
                text_sources = []
                
                # Try to get text from Excel columns
                if excel_row.get('HEADLINES'):
                    text_sources.append(str(excel_row.get('HEADLINES', '')))
                if excel_row.get('ARTICLESUMMARY'):
                    text_sources.append(str(excel_row.get('ARTICLESUMMARY', '')))
                if excel_row.get('ARTICLECONTENT'):
                    text_sources.append(str(excel_row.get('ARTICLECONTENT', '')))
                
                # Try document fields
                if document.get("articleData", {}).get("text"):
                    text_sources.append(str(document.get("articleData", {}).get("text", "")))
                if document.get("articleData", {}).get("headline"):
                    text_sources.append(str(document.get("articleData", {}).get("headline", "")))
                if document.get("articleData", {}).get("content"):
                    text_sources.append(str(document.get("articleData", {}).get("content", "")))
                
                # Generate keywords from text or fallback to articleTag keywords
                combined_text = " ".join(text_sources).strip()
                if combined_text and len(combined_text) > 10:  # Ensure we have meaningful text
                    keywords = self.generate_word_cloud(combined_text)
                else:
                    # Fallback to articleTag keywords if no text content found
                    keywords = ""
                    if matching_tag_data:
                        tag_info = matching_tag_data[0].get('tagInfo', {})
                        keywords = tag_info.get('keyword', '')
                
                # Build enriched company tag combining all sources
                enriched_tag = {
                    "id": company_id,
                    "name": company_name,
                    "shortName": company_short_name,
                    "chartData": {
                        # From Excel (primary source)
                        "adRates": self.parse_numeric_field(excel_row.get('ADRATES', 0)),
                        "adValue": self.parse_numeric_field(excel_row.get('ADVALUE', 0)),
                        "boxValue": self.parse_numeric_field(excel_row.get('BOXVALUE', 0)),
                        "circulation": self.parse_numeric_field(excel_row.get('CIRCULATION', 0)),
                        "graphValue": 1 if excel_row.get('PHOTO', 'N') == 'Y' else 0,
                        "height": self.parse_numeric_field(excel_row.get('HEIGHT', 0)),
                        "width": self.parse_numeric_field(excel_row.get('WIDTH', 0)),
                        "imageSize": 0,  
                        "imageSizeText": "0 KB",
                        "isPrintArticle": True,
                        "keywords": keywords,  # generated
                        "pageNumber": self.parse_page_number(excel_row.get('PAGENUMBERS', 0)),
                        "pageValue": self.parse_numeric_field(excel_row.get('PAGEVALUE', 0)),
                        "photoValue": self.parse_numeric_field(excel_row.get('PHOTOVALUE', 0)),
                        "space": self.parse_numeric_field(excel_row.get('SPACE', 0)),
                        "manualProminence": self.parse_numeric_field(excel_row.get('PROMINENCE', 0)),
                        "reportingSubject": excel_row.get('REPORTINGSUBJECT', ''),
                        "reportingTone": self.parse_numeric_field(excel_row.get('REPORTINGTONE', 0)),
                        "pubTScore": self.parse_decimal_field(excel_row.get('PUBTSCORE', 0)),
                        "iscore": self.parse_decimal_field(excel_row.get('ISCORE', 0)),
                        "vscore": self.parse_decimal_field(excel_row.get('VSCORE', 0)),
                        "qcFlag": "RESEARCH"
                    },
                    "clientArticleTag": {
                        "clientId": "",
                        "tags": None  # Set to null to avoid type conflict with existing mapping
                    }
                }
                enriched_company_tags.append(enriched_tag)
            
            # Handle date formatting
            article_date = document.get("articleInfo", {}).get("articleDate", datetime.now())
            if isinstance(article_date, datetime):
                article_date_iso = article_date.isoformat()
            else:
                article_date_iso = str(article_date)
            
            # Merge with existing company tags if provided
            if existing_company_tags is not None:
                merged_company_tags = self.merge_company_tags(existing_company_tags, enriched_company_tags)
            else:
                merged_company_tags = enriched_company_tags
            
            # Build payload based on whether document exists
            if document_exists:
                # For existing documents, use update operation to only update companyTag
                payload = {
                    "_op_type": "update",
                    "_index": self.index_name,
                    "_id": article_id,
                    "script": {
                        "source": "ctx._source.companyTag = params.newCompanyTags",
                        "params": {
                            "newCompanyTags": merged_company_tags
                        }
                    }
                }
            else:
                # For new documents, use index operation with full document
                payload = {
                    "_op_type": "index",
                    "_index": self.index_name,
                    "_id": article_id,
                    "_source": {
                        "articleId": article_id,
                        "articleInfo": {
                            "articleDate": article_date_iso,
                            "articleMonth": document.get("articleInfo", {}).get("articleMonth", 0),
                            "articleYear": document.get("articleInfo", {}).get("articleYear", 0),
                            "journalist": self.clean_journalist_field(excel_row.get('JOURNALIST', '')) or document.get("articleInfo", {}).get("journalist", "") or None,
                            "cityId": city_id,
                        },
                        "articleData": {
                            "language": self.getLanguageName(excel_row.get('LANGUAGE', '') or publication_info.get("publicationInfo", {}).get("language", "")),
                            "box": document.get("articleData", {}).get("box", ""),
                            "headline": document.get("articleData", {}).get("headline", ""),
                            "text": document.get("articleData", {}).get("text", ""),
                            "content": document.get("articleData", {}).get("content", ""),
                        },
                        "uploadInfo": {
                            "countryCode": self.get_country_id_by_city_id(city_id),
                            "countryName": "India",
                            "cityId": city_id,
                            "city": self.get_city_name_by_city_id(city_id),
                        },
                        "publicationInfo": {
                            "id": publication_id,
                            "name": publication_info.get("publicationInfo", {}).get("publicationName", document.get("publicationInfo", {}).get("name", "Others")),
                            "pubGroupId": publication_info.get("publicationInfo", {}).get("publicationGroupID", document.get("publicationInfo", {}).get("pubGroupId", "")),
                            "pubGroupName": publication_info.get("publicationInfo", {}).get("publicationGroupName", ""),
                            "editionType": publication_info.get("publicationInfo", {}).get("editionType", document.get("publicationInfo", {}).get("editionType", "")),
                            "editionTypeName": self.EDITION_TYPE_MAPPING.get(
                                publication_info.get("publicationInfo", {}).get("editionType", document.get("publicationInfo", {}).get("editionType", "")), "Others"
                            ),
                            "publicationCategory": self.PUBLICATION_CATEGORY_MAPPING.get(
                                publication_info.get("publicationInfo", {}).get("publicationCategory", ""), "Others"
                            ),
                            "publicationType": publication_info.get("publicationInfo", {}).get("publicationType", document.get("publicationInfo", {}).get("pubType", "")),
                            "publicationLanguage": self.getLanguageName(publication_info.get("publicationInfo", {}).get("language", "")),
                            "region": publication_info.get("publicationInfo", {}).get("zone", zone),
                        },
                        "companyTag": merged_company_tags,
                        "crossLanguageInvertedToken": document.get("crossLanguageInvertedToken", "")
                    }
                }
            
            return replace_nan_with_null(payload)
        except Exception as e:
            logger.error(f"Error building payload for article {document.get('articleId', 'unknown')}: {str(e)}")
            logger.exception("Full error details:")
            return None

    def batch_fetch_mongo_docs(self, article_ids):
        """Fetch all MongoDB documents in a single query with retry logic"""
        docs = {}
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                cursor = self.article_collection.find(
                    {'articleId': {'$in': article_ids}},
                    no_cursor_timeout=True
                ).batch_size(100)
                
                for doc in cursor:
                    docs[doc['articleId']] = doc
                return docs
                
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed to fetch mongo docs: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
                else:
                    logger.error(f"Failed to fetch mongo docs after {max_retries} attempts")
                    return docs  # Return empty docs to continue processing
    
    def batch_fetch_company_tags(self, article_ids):
        """Fetch all company tags in a single query with retry logic"""
        tags = defaultdict(list)
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                cursor = self.article_tag_collection.find(
                    {'articleId': {'$in': article_ids}},
                    no_cursor_timeout=True
                ).batch_size(100)
                
                for tag in cursor:
                    tags[tag['articleId']].append(tag)
                return tags
                
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed to fetch company tags: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
                else:
                    logger.error(f"Failed to fetch company tags after {max_retries} attempts")
                    return tags  # Return empty tags to continue processing

    def check_document_exists(self, document_id):
        """Check if document exists in Elasticsearch"""
        try:
            return self.es.exists(index=self.index_name, id=document_id)
        except Exception as e:
            logger.warning(f"Error checking if document {document_id} exists: {str(e)}")
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
        """Merge new company tags with existing ones, updating existing company tags with Excel data"""
        merged_tags = existing_company_tags.copy() if existing_company_tags else []
        
        for new_tag in new_company_tags:
            company_id = new_tag.get("id")
            # Check if this company already exists
            existing_index = None
            for i, existing_tag in enumerate(merged_tags):
                if existing_tag.get("id") == company_id:
                    existing_index = i
                    break
            
            if existing_index is not None:
                # Update existing company tag with Excel data (Excel is source of truth)
                logger.info(f"Updating existing company tag for companyId {company_id} with Excel data")
                merged_tags[existing_index] = new_tag
            else:
                # Add new company tag
                logger.info(f"Adding new company tag for companyId {company_id}")
                merged_tags.append(new_tag)
        
        return merged_tags

    def run(self):
        logger.info(f"Reading Excel file: {self.excel_path}")
        
        try:
            df = pd.read_excel(self.excel_path)
        except Exception as e:
            logger.error(f"Failed to read Excel file '{self.excel_path}': {str(e)}")
            raise SystemExit(f"Cannot read Excel file: {str(e)}")
        
        df.columns = df.columns.str.strip().str.upper()  # Clean and uppercase all column names
        print("COLUMNS:", df.columns.tolist())  # Debug print

        # Define required columns (all columns accessed via excel_row.get())
        required_columns = [
            'ARTICLEID', 'COMPANYID', 'COMPANYNAME', 'ADRATES', 'ADVALUE', 
            'BOXVALUE', 'CIRCULATION', 'HEIGHT', 'WIDTH', 'PAGENUMBERS', 
            'PAGEVALUE', 'PHOTOVALUE', 'SPACE', 'PROMINENCE', 'REPORTINGSUBJECT', 
            'REPORTINGTONE', 'PUBTSCORE', 'PHOTO', 'ISCORE', 'VSCORE',
            'HEADLINES', 'ARTICLESUMMARY', 'ARTICLECONTENT', 'JOURNALIST', 'LANGUAGE'
        ]
        
        # Check for missing columns
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            error_msg = f"Missing required columns in Excel sheet: {', '.join(missing_columns)}"
            logger.error(error_msg)
            logger.error(f"Available columns: {', '.join(df.columns.tolist())}")
            raise SystemExit(f"Process stopped: {error_msg}")
        
        logger.info("All required columns found in Excel sheet")

        # Drop the first column if it's not ARTICLEID or UPLOADID
        if df.columns[0] not in ['ARTICLEID', 'UPLOADID']:
            df = df.iloc[:, 1:]

        total_rows = len(df)
        logger.info(f"Processing {total_rows} rows from Excel")
        
        # TEMPORARY: Filter to only process specific article IDs from jan-mayprint.txt
        # try:
        #     with open('jan-mayprint.txt', 'r') as f:
        #         target_article_ids = []
        #         for line in f:
        #             line = line.strip()
        #             if line and line.isdigit():
        #                 target_article_ids.append(int(line))
        #     
        #     logger.info(f"TEMPORARY FILTER: Loaded {len(target_article_ids)} article IDs from jan-mayprint.txt")
        #     
        #     # Filter DataFrame to only include target article IDs
        #     df_filtered = df[df['ARTICLEID'].isin(target_article_ids)]
        #     logger.info(f"Filtered from {len(df)} to {len(df_filtered)} rows")
        #     
        #     if len(df_filtered) == 0:
        #         logger.warning("No matching article IDs found in Excel file")
        #         return
        #         
        # except FileNotFoundError:
        #     logger.error("jan-mayprint.txt file not found. Processing all articles.")
        #     df_filtered = df
        # except Exception as e:
        #     logger.error(f"Error reading jan-mayprint.txt: {str(e)}. Processing all articles.")
        #     df_filtered = df
        
        # Process all articles (filtering logic commented out above)
        df_filtered = df
        
        # Group Excel data by articleId to handle multiple company tags per article
        article_groups = df_filtered.groupby('ARTICLEID')
        logger.info(f"Found {len(article_groups)} unique articles with company tags")
        
        # Process in batches for better performance
        BATCH_SIZE = 100  # Reduced batch size to avoid timeouts
        total_inserted = 0
        total_valid = 0
        total_invalid = 0
        
        article_ids_list = list(article_groups.groups.keys())
        
        for batch_start in range(0, len(article_ids_list), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(article_ids_list))
            batch_article_ids = article_ids_list[batch_start:batch_end]
            
            logger.info(f"Processing batch {batch_start//BATCH_SIZE + 1}: articles {batch_start+1}-{batch_end}")
            
            # Batch fetch MongoDB documents
            mongo_docs = self.batch_fetch_mongo_docs(batch_article_ids)
            
            # Batch fetch company tags
            company_tags = self.batch_fetch_company_tags(batch_article_ids)
            
            # Build payloads for this batch
            payloads = []
            
            for article_id in batch_article_ids:
                # Check if document already exists in Elasticsearch
                document_exists = self.check_document_exists(article_id)
                existing_company_tags = None
                
                if document_exists:
                    logger.info(f"Article {article_id} already exists in ES, will merge company tags")
                    existing_doc = self.get_existing_document(article_id)
                    if existing_doc:
                        existing_company_tags = existing_doc.get("companyTag", [])
                        logger.info(f"Found {len(existing_company_tags)} existing company tags for article {article_id}")
                
                mongo_doc = mongo_docs.get(article_id)
                if not mongo_doc:
                    logger.warning(f"ARTICLEID {article_id} not found in MongoDB, creating from Excel data.")
                    # Create document from Excel data only
                    article_rows = article_groups.get_group(article_id)
                    first_row = article_rows.iloc[0]
                    
                    # Create minimal document structure from Excel data
                    mongo_doc = self.create_document_from_excel(article_id, first_row)
                    if not mongo_doc:
                        logger.error(f"Failed to create document from Excel for article {article_id}")
                        continue
                
                # Get all Excel rows for this article (multiple company tags)
                article_rows = article_groups.get_group(article_id)
                
                # Collect all company-specific data from Excel rows
                excel_company_data = []
                
                for idx, row in article_rows.iterrows():
                    company_id = str(row['COMPANYID']).strip() if pd.notnull(row['COMPANYID']) else None
                    if company_id and company_id != 'nan':
                        excel_company_data.append({
                            'company_id': company_id,
                            'row_data': row
                        })
                
                if not excel_company_data:
                    logger.warning(f"No valid company data found for article {article_id}")
                    continue
                
                # Validate reporting subjects for all unique companies in this article
                validation_results = []
                unique_companies = set()
                
                for excel_company in excel_company_data:
                    company_id = excel_company['company_id']
                    if company_id not in unique_companies:
                        unique_companies.add(company_id)
                        excel_row = excel_company['row_data']
                        reporting_subject = excel_row.get('REPORTINGSUBJECT', '')
                        
                        is_valid = self.validate_reporting_subject(reporting_subject)
                        validation_results.append({
                            'company_id': company_id,
                            'reporting_subject': reporting_subject,
                            'is_valid': is_valid
                        })
                        
                        if is_valid:
                            total_valid += 1
                        else:
                            total_invalid += 1
                            logger.warning(f"Article {article_id}, Company {company_id}: Invalid reportingSubject '{reporting_subject}' - flagged but will still insert")
                
                # Only merge Excel fields into mongo_doc if document doesn't exist in ES
                if not document_exists:
                    first_row = article_rows.iloc[0]
                    for field in first_row.index:
                        if pd.notnull(first_row[field]) and field != 'COMPANYID':  # Skip company-specific fields
                            mongo_doc[field] = first_row[field]
                        
                # Get company tag data for this article
                company_tag_data = company_tags.get(article_id, [])
                
                payload = self.build_es_payload(mongo_doc, company_tag_data, excel_company_data, existing_company_tags, document_exists)
                    
                if payload:
                    payloads.append(payload)
                else:
                    logger.warning(f"No payload generated for article {article_id}")
            
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
                    
                    if failed_items:
                        logger.warning(f"Failed to insert {len(failed_items)} documents in batch")
                        
                except helpers.BulkIndexError as e:
                    # Get detailed error information
                    success_count = 0
                    for error in e.errors:
                        error_detail = error.get('index', {}).get('error', {})
                        article_id = error.get('index', {}).get('_id', 'unknown')
                        logger.error(f"Failed to insert article {article_id}: {error_detail.get('type', 'unknown')} - {error_detail.get('reason', 'no reason')}")
                    
                    # Count successful insertions
                    for item in e.errors:
                        if 'index' in item and item['index'].get('status') in [200, 201]:
                            success_count += 1
                    total_inserted += success_count
                    
                except Exception as e:
                    logger.error(f"Failed to insert batch: {str(e)}")
            
            logger.info(f"Batch completed: {len(payloads)} documents prepared, running total inserted: {total_inserted}")
        
        logger.info(f"Completed: {total_inserted} documents inserted into Elasticsearch index '{self.index_name}'.")
        
        # Log final summary
        total_articles = len(article_ids_list)
        logger.info(f"Summary: {total_articles} total articles processed, {total_inserted} successfully inserted into Elasticsearch")
        logger.info(f"ReportingSubject Validation Summary: {total_valid} valid, {total_invalid} invalid")


def main():
    inserter = ExcelToElasticsearchInserter(EXCEL_PATH, MONGO_COLLECTION, ES_INDEX)
    inserter.run()

if __name__ == "__main__":
    main()