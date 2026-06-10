"""
Charts API service for Excel validation and upload.
Integrates Excel upload functionality for Social and Print articles.
"""

import os
import uuid
import logging
import traceback
from datetime import datetime
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor
import asyncio

import pandas as pd
import numpy as np

from app.utils.logger import get_logger
from app.services.charts_api.database import mongo, elastic
from app.services.charts_api.print_uploader import ExcelToElasticsearchInserter as PrintInserter
from app.services.charts_api.social_uploader import ExcelToElasticsearchSocialInserter as SocialInserter

logger = get_logger(__name__)

# Thread pool for CPU-intensive tasks
executor = ThreadPoolExecutor(max_workers=4)

# In-memory job store for background uploads
upload_jobs: Dict[str, Dict[str, Any]] = {}

def convert_numpy_types(obj):
    """Convert numpy types to native Python types for JSON serialization"""
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    else:
        return obj


class ExcelValidator:
    """Validates Excel files for both Social and Print upload scripts"""
    
    def __init__(self):
        self.mongo_db = mongo()
        self.reporting_subject_collection = self.mongo_db["reportingSubject"]
        self._reporting_subject_cache = {}
        
        # Define required columns for each upload type
        self.social_required_columns = {
            'SOCIAL_FEED_ID': 'Social Feed ID (primary key)',
            'SOCIALFEEDID': 'Social Feed ID (alt)',
            'SOCIAL FEED ID': 'Social Feed ID (alt)',
            'COMPANYID': 'Company ID',
            'COMPANYNAME': 'Company Name',
            'PUBLICATIONNAME': 'Publication Name',
            'PUBLICATION': 'Publication (alt)',
            'FEEDDATE': 'Feed Date',
            'FEEDDATETIME': 'Feed Date Time (alt)',
            'AUTHOR': 'Author',
            'AUTHOR NAME': 'Author Name (alt)',
            'HEADLINE': 'Headline',
            'LINK': 'Link',
            'ISCORE': 'I-Score',
            'VSCORE': 'V-Score',
            'REPORTINGTONE': 'Reporting Tone',
            'PROMINENCE': 'Prominence',
            'REACH': 'Reach',
            'WORDCOUNT': 'Word Count',
            'WORD_COUNT': 'Word Count (alt)',
            'MAILER_REPORTING_SUBJECT': 'Mailer Reporting Subject',
            'MAILERREPORTINGSUBJECT': 'Mailer Reporting Subject (alt)',
            'THEME': 'Theme',
            'SF': 'SF'
        }
        
        self.print_required_columns = {
            'ARTICLEID': 'Article ID (primary key)',
            'UPLOADID': 'Upload ID (alt)',
            'COMPANYID': 'Company ID',
            'COMPANYNAME': 'Company Name',
            'ADRATES': 'Ad Rates',
            'ADVALUE': 'Ad Value',
            'BOXVALUE': 'Box Value',
            'CIRCULATION': 'Circulation',
            'HEIGHT': 'Height',
            'WIDTH': 'Width',
            'PAGENUMBERS': 'Page Numbers',
            'PAGEVALUE': 'Page Value',
            'PHOTOVALUE': 'Photo Value',
            'SPACE': 'Space',
            'PROMINENCE': 'Prominence',
            'REPORTINGSUBJECT': 'Reporting Subject',
            'REPORTINGTONE': 'Reporting Tone',
            'PUBTSCORE': 'Publication Score',
            'PHOTO': 'Photo (Y/N)',
            'ISCORE': 'I-Score',
            'VSCORE': 'V-Score',
            'HEADLINES': 'Headlines',
            'ARTICLESUMMARY': 'Article Summary',
            'ARTICLECONTENT': 'Article Content',
            'JOURNALIST': 'Journalist',
            'LANGUAGE': 'Language'
        }
        
        # Alternative column names
        self.social_alternative_columns = {
            'SOCIAL_FEED_ID': ['SOCIALFEEDID', 'SOCIAL FEED ID'],
            'SOCIALFEEDID': ['SOCIAL_FEED_ID', 'SOCIAL FEED ID'],
            'SOCIAL FEED ID': ['SOCIAL_FEED_ID', 'SOCIALFEEDID'],
            'PUBLICATIONNAME': ['PUBLICATION'],
            'PUBLICATION': ['PUBLICATIONNAME'],
            'FEEDDATE': ['FEEDDATETIME'],
            'FEEDDATETIME': ['FEEDDATE'],
            'AUTHOR': ['AUTHOR NAME'],
            'AUTHOR NAME': ['AUTHOR'],
            'WORDCOUNT': ['WORD_COUNT'],
            'WORD_COUNT': ['WORDCOUNT'],
            'MAILERREPORTINGSUBJECT': ['MAILER_REPORTING_SUBJECT'],
            'MAILER_REPORTING_SUBJECT': ['MAILERREPORTINGSUBJECT']
        }
        
        self.print_alternative_columns = {
            'PUBTSCORE': ['PUBLICATIONSCORE'],
            'PUBLICATIONSCORE': ['PUBTSCORE']
        }

    async def validate_reporting_subject(self, reporting_subject_name):
        """Check if reportingSubject exists in MongoDB collection"""
        if not reporting_subject_name or str(reporting_subject_name).strip() == '':
            return False, "Empty reporting subject"
            
        # Use cache to avoid repeated database queries
        if reporting_subject_name not in self._reporting_subject_cache:
            try:
                # Run MongoDB query in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                exists = await loop.run_in_executor(
                    executor,
                    lambda: self.reporting_subject_collection.find_one(
                        {"reportingSubjectInfo.name": reporting_subject_name}
                    ) is not None
                )
                self._reporting_subject_cache[reporting_subject_name] = exists
            except Exception as e:
                logger.error(f"Error validating reportingSubject '{reporting_subject_name}': {str(e)}")
                self._reporting_subject_cache[reporting_subject_name] = False
                
        is_valid = self._reporting_subject_cache[reporting_subject_name]
        return is_valid, "Valid" if is_valid else "Not found in database"

    def detect_upload_type(self, df_columns):
        """Detect whether this is a Social or Print upload based on column names"""
        social_score = 0
        print_score = 0
        
        # Check for Social-specific columns
        social_indicators = ['SOCIAL_FEED_ID', 'SOCIALFEEDID', 'SOCIAL FEED ID', 'FEEDDATE', 'FEEDDATETIME', 'ISCORE', 'VSCORE', 'REPORTINGTONE', 'PROMINENCE', 'REACH', 'MAILER_REPORTING_SUBJECT', 'THEME', 'SF']
        for col in social_indicators:
            if col in df_columns:
                social_score += 1
        
        # Check for Print-specific columns
        print_indicators = ['ARTICLEID', 'ARTICLEDATE', 'HEADLINES', 'ADRATES', 'CIRCULATION', 'PAGENUMBERS', 'PAGEVALUE', 'PHOTOVALUE', 'SPACE', 'HEIGHT', 'WIDTH']
        for col in print_indicators:
            if col in df_columns:
                print_score += 1
        
        # Primary identification: Social Feed ID vs Article ID
        if any(col in df_columns for col in ['SOCIAL_FEED_ID', 'SOCIALFEEDID', 'SOCIAL FEED ID']):
            return 'social'
        elif 'ARTICLEID' in df_columns:
            return 'print'
        elif social_score > print_score:
            return 'social'
        elif print_score > social_score:
            return 'print'
        else:
            return 'unknown'

    def validate_columns(self, df, upload_type):
        """Validate column names and structure"""
        validation_results = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'missing_required': [],
            'column_mapping': {}
        }
        
        df_columns = [col.strip().upper() for col in df.columns]
        
        if upload_type == 'social':
            required_cols = self.social_required_columns
            alternative_cols = self.social_alternative_columns
        elif upload_type == 'print':
            required_cols = self.print_required_columns
            alternative_cols = self.print_alternative_columns
        else:
            validation_results['valid'] = False
            validation_results['errors'].append("Could not determine upload type from column names")
            return validation_results
        
        # Check for required columns
        for col_name, description in required_cols.items():
            # Check if the exact column name exists
            if col_name in df_columns:
                validation_results['column_mapping'][col_name] = description
                continue
            
            # Check if this column has alternatives
            if col_name in alternative_cols:
                # Check if any alternative exists
                alternative_found = False
                for alt_col in alternative_cols[col_name]:
                    if alt_col in df_columns:
                        alternative_found = True
                        validation_results['column_mapping'][alt_col] = description
                        break
                
                if not alternative_found:
                    # List all possible alternatives in the error
                    all_alternatives = [col_name] + alternative_cols[col_name]
                    validation_results['missing_required'].append({
                        'column': ' or '.join(all_alternatives),
                        'description': description
                    })
                    validation_results['valid'] = False
            else:
                # No alternatives, column is truly missing
                validation_results['missing_required'].append({
                    'column': col_name,
                    'description': description
                })
                validation_results['valid'] = False
        
        return validation_results

    async def validate_data_quality(self, df, upload_type):
        """Validate data quality and content"""
        validation_results = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'data_stats': {},
            'reporting_subject_validation': [],
            'duplicate_combinations': [],
            'company_row_counts': {}
        }
        
        try:
            # Basic data statistics
            validation_results['data_stats'] = {
                'total_rows': int(len(df)),
                'total_columns': int(len(df.columns)),
                'empty_rows': int(df.isnull().all(axis=1).sum()),
                'duplicate_rows': int(df.duplicated().sum())
            }
            
            # Check for empty primary key column
            if upload_type == 'social':
                primary_key_col = None
                for col in ['SOCIAL_FEED_ID', 'SOCIALFEEDID', 'SOCIAL FEED ID']:
                    if col in df.columns:
                        primary_key_col = col
                        break
            else:  # print
                primary_key_col = 'ARTICLEID'
            
            if primary_key_col:
                null_primary_keys = int(df[primary_key_col].isnull().sum())
                if null_primary_keys > 0:
                    validation_results['errors'].append(f"Found {null_primary_keys} rows with null {primary_key_col}")
                    validation_results['valid'] = False
            
            # Check for empty company IDs
            if 'COMPANYID' in df.columns:
                null_company_ids = int(df['COMPANYID'].isnull().sum())
                if null_company_ids > 0:
                    validation_results['warnings'].append(f"Found {null_company_ids} rows with null COMPANYID")
            
        except Exception as e:
            validation_results['valid'] = False
            validation_results['errors'].append(f"Error during data quality validation: {str(e)}")
            logger.error(f"Data quality validation error: {str(e)}")
            logger.error(traceback.format_exc())
        
        return validation_results

    async def validate_excel_file(self, file_path, upload_type_override: Optional[str] = None):
        """Complete validation of Excel file"""
        try:
            # Read Excel file in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(executor, self._read_excel_file, file_path)
            
            # Detect or override upload type
            if upload_type_override in {"social", "print"}:
                upload_type = upload_type_override
            else:
                upload_type = self.detect_upload_type(df.columns)
            
            # Validate columns
            column_validation = self.validate_columns(df, upload_type)
            
            # Validate data quality
            data_validation = await self.validate_data_quality(df, upload_type)
            
            # Combine results
            validation_results = {
                'file_path': file_path,
                'upload_type': upload_type,
                'timestamp': datetime.now().isoformat(),
                'column_validation': column_validation,
                'data_validation': data_validation,
                'overall_valid': column_validation['valid'] and data_validation['valid'],
                'summary': {
                    'total_rows': int(len(df)),
                    'total_columns': int(len(df.columns)),
                    'upload_type_detected': upload_type,
                    'ready_for_upload': column_validation['valid'] and data_validation['valid']
                }
            }
            
            # Convert any remaining numpy types to native Python types
            return convert_numpy_types(validation_results)
            
        except Exception as e:
            logger.error(f"Excel validation error: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                'file_path': file_path,
                'upload_type': 'unknown',
                'timestamp': datetime.now().isoformat(),
                'overall_valid': False,
                'error': str(e),
                'summary': {
                    'ready_for_upload': False,
                    'error_message': str(e)
                }
            }
    
    def _read_excel_file(self, file_path):
        """Helper method to read Excel file in thread pool"""
        df = pd.read_excel(file_path)
        df.columns = df.columns.str.strip().str.upper()
        return df


# Initialize validator
validator = ExcelValidator()

# Mongo job history collection
mongo_db = mongo()
charts_jobs = mongo_db["chartsAndGraphs"]


