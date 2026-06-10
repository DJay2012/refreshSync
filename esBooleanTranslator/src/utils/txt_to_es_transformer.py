#!/usr/bin/env python3
"""
Translation TXT to Elasticsearch DSL Transformer
Transforms translation.txt files into ES DSL format and inserts into index
"""

import json
import re
import os
from typing import Dict, List, Any, Optional
from collections import defaultdict

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not available, continue without it

# Try to import Elasticsearch
try:
    from elasticsearch import Elasticsearch
    ELASTICSEARCH_AVAILABLE = True
except ImportError:
    ELASTICSEARCH_AVAILABLE = False
    print("Warning: Elasticsearch library not available. Install with: pip install elasticsearch")

class TranslationToESTransformer:
    def __init__(self):
        self.es_client = None
        self.default_index = None
        self._connect_from_config()
    
    def _connect_from_config(self):
        """Connect to Elasticsearch using settings from Config.py"""
        try:
            # Try relative import first (when run as module)
            try:
                from ..utils.Config import es, INDEX_NAME
            except ImportError:
                # Fallback to absolute import (when run directly)
                import sys
                import os
                # Add the src directory to the path
                current_dir = os.path.dirname(os.path.abspath(__file__))
                src_dir = os.path.join(current_dir, '..')
                if src_dir not in sys.path:
                    sys.path.insert(0, src_dir)
                from utils.Config import es, INDEX_NAME
            
            self.es_client = es
            self.default_index = INDEX_NAME
            
            # Test connection
            if self.es_client.ping():
                print(f"Connected to Elasticsearch using Config.py settings")
                print(f"Default index: {self.default_index}")
            else:
                print("Failed to connect to Elasticsearch")
                self.es_client = None
                
        except Exception as e:
            print(f"Error connecting to Elasticsearch: {e}")
            self.es_client = None
    
    def parse_translation_file(self, file_path: str) -> Dict[str, Any]:
        """
        Parse the translation.txt file and extract company info and translations
        
        Args:
            file_path (str): Path to the translation.txt file
            
        Returns:
            Dict containing company info and translations
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            lines = content.strip().split('\n')
            
            # Extract company info
            company_id = None
            company_name = None
            original_query = None
            translations = {}
            
            for line in lines:
                line = line.strip()
                if line.startswith('Company ID:'):
                    company_id = line.replace('Company ID:', '').strip()
                elif line.startswith('Company Name:'):
                    company_name = line.replace('Company Name:', '').strip()
                elif line.startswith('Original Query:'):
                    original_query = line.replace('Original Query:', '').strip()
                elif ':' in line and not line.startswith('=') and not line.startswith('TRANSLATIONS'):
                    # This is a translation line
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        lang_code = parts[0].strip().lower()
                        translation = parts[1].strip()
                        translations[lang_code] = translation
            
            return {
                'companyId': company_id,
                'companyName': company_name,
                'originalQuery': original_query,
                'translations': translations
            }
            
        except Exception as e:
            print(f"Error parsing translation file: {e}")
            return {}
    
    def parse_boolean_query_to_elasticsearch(self, boolean_query: str) -> Dict[str, Any]:
        """
        Parse a boolean query string and convert it to Elasticsearch query structure
        Uses the same approach as insert_final_booleans.py
        """
        try:
            # Import the QueryCreator components
            try:
                from ..core.QueryCreator import tokenize, Parser, convert_node
            except ImportError:
                # Fallback to absolute import (when run directly)
                import sys
                import os
                # Add the src directory to the path
                current_dir = os.path.dirname(os.path.abspath(__file__))
                src_dir = os.path.join(current_dir, '..')
                if src_dir not in sys.path:
                    sys.path.insert(0, src_dir)
                from core.QueryCreator import tokenize, Parser, convert_node
            
            # Tokenize the query
            tokens = tokenize(boolean_query)
            
            # Parse into AST
            parser = Parser(tokens)
            ast = parser.parse_expression()
            
            # Convert AST to Elasticsearch query
            return convert_node(ast)
            
        except ImportError:
            print("QueryCreator not found, using fallback method")
            # Fallback to improved boolean query parser
            return self.parse_boolean_fallback(boolean_query)
        except Exception as e:
            # If it's a parsing error (ValueError, etc.), re-raise it
            # Do NOT fallback to lenient parser for syntax errors
            print(f"Error parsing query: {e}")
            raise
    
    def parse_boolean_fallback(self, boolean_query: str) -> Dict[str, Any]:
        """
        Improved fallback parser that handles NEAR operators and basic boolean logic
        """
        try:
            # Parse the query into a structure that handles NEAR operators
            return self._parse_boolean_with_near(boolean_query)
        except Exception as e:
            print(f"Fallback parser failed: {e}")
            # Ultimate fallback - extract terms and create simple query
            terms = self.extract_terms_from_boolean(boolean_query)
            print(f"Extracted terms: {terms}")
            return self.create_elasticsearch_query(terms)
    
    def _parse_boolean_with_near(self, boolean_query: str) -> Dict[str, Any]:
        """
        Parse boolean query handling NEAR operators properly
        """
        # Find all NEAR/ patterns and their surrounding terms
        # Pattern matches: "term1" "NEAR/3" "term2" with optional whitespace
        near_pattern = r'"([^"]+)"\s+"NEAR/(\d+)"\s+"([^"]+)"'
        near_matches = re.findall(near_pattern, boolean_query)
        
        # Find all quoted terms that are not part of NEAR operations
        all_quoted = re.findall(r'"([^"]+)"', boolean_query)
        near_terms = set()
        for match in near_matches:
            near_terms.add(match[0])  # left term
            near_terms.add(match[2])  # right term
        
        # Get standalone quoted terms (not part of NEAR)
        standalone_terms = [term for term in all_quoted if term not in near_terms]
        
        # Build the query structure
        should_clauses = []
        
        # Add standalone terms as match_phrase queries
        for term in standalone_terms:
            should_clauses.append({
                "match_phrase": {
                    "content": {
                        "query": term
                    }
                }
            })
        
        # Add NEAR operations as match_phrase with slop
        for left_term, slop, right_term in near_matches:
            combined_query = f"{left_term} {right_term}"
            should_clauses.append({
                "match_phrase": {
                    "content": {
                        "query": combined_query,
                        "slop": int(slop)
                    }
                }
            })
        
        # If we have clauses, return bool query, otherwise match_all
        if should_clauses:
            return {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1
                }
            }
        else:
            return {"match_all": {}}
    
    def extract_terms_from_boolean(self, boolean_query: str) -> List[str]:
        """Extract individual terms from a boolean query (fallback method)"""
        try:
            terms = []
            
            # First, extract quoted strings (person names, etc.)
            quoted_terms = re.findall(r'"([^"]*)"', boolean_query)
            for term in quoted_terms:
                if term.strip():
                    terms.append(term.strip())
            
            # Remove quoted strings from the query for further processing
            query_without_quotes = re.sub(r'"[^"]*"', '', boolean_query)
            
            # Remove boolean operators and parentheses
            reserved_words = ['AND', 'OR', 'NOT']
            reserved_tokens = [':', '*', '?', '++', '+', '(', ')', '~']
            
            # Split by operators and reserved tokens (but not NEAR/ which we handle separately)
            keyword_array = re.split(r'(\s*(?: AND | OR | NOT |\++|\+|\?|\*|\(|\)|[a-zA-Z]+:|\~)\s*)', query_without_quotes)
            
            for part in keyword_array:
                part = part.strip()
                if part.upper() in reserved_words or ':' in part or part.upper() in reserved_tokens:
                    continue
                if part and not part.isspace():
                    # Clean up the term
                    term = part.strip('"').strip()
                    if term and not term.startswith('NEAR/'):
                        terms.append(term)
            
            # Remove duplicates while preserving order
            seen = set()
            unique_terms = []
            for term in terms:
                if term not in seen:
                    seen.add(term)
                    unique_terms.append(term)
            
            return unique_terms
        except Exception as e:
            print(f"Error extracting terms: {e}")
            return []
    
    def create_elasticsearch_query(self, terms: List[str]) -> Dict[str, Any]:
        """Create a simple Elasticsearch query from terms (fallback method)"""
        if not terms:
            return {"match_all": {}}
        
        return {
            "bool": {
                "should": [
                    {"match_phrase": {"content": {"query": term}}} 
                    for term in terms
                ],
                "minimum_should_match": 1
            }
        }
    
    def create_es_document(self, parsed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create ES document using the same approach as insert_final_booleans.py
        
        Args:
            parsed_data (Dict): Parsed translation data
            
        Returns:
            Dict: ES document structure
        """
        if not parsed_data:
            return {}
        
        # Create base document
        document = {
            "companyId": parsed_data.get('companyId', ''),
            "companyName": parsed_data.get('companyName', '')
        }
        
        # Add English version (original query)
        original_query = parsed_data.get('originalQuery', '')
        if original_query:
            try:
                es_query = self.parse_boolean_query_to_elasticsearch(original_query)
                document["lang_en"] = es_query
            except Exception as e:
                print(f"ERROR: Error processing EN for {parsed_data.get('companyId', 'Unknown')}: {e}")
        
        # Add translations for each language
        translations = parsed_data.get('translations', {})
        for lang_code, translation in translations.items():
            if translation:
                try:
                    # Parse the translated boolean to Elasticsearch query
                    es_query = self.parse_boolean_query_to_elasticsearch(translation)
                    lang_key = f"lang_{lang_code}"
                    document[lang_key] = es_query
                except Exception as e:
                    print(f"ERROR: Error processing {lang_code} for {parsed_data.get('companyId', 'Unknown')}: {e}")
                    continue
        
        return document
    
    def upsert_data_to_es_single(self, data: Dict[str, Any], doc_id: str, index_name: Optional[str] = None) -> bool:
        """
        Upsert a single document to Elasticsearch using companyId as document ID
        Uses the same approach as insert_final_booleans.py
        """
        if not self.es_client:
            print("ERROR: Elasticsearch client not available")
            return False
        
        try:
            index = index_name or self.default_index
            response = self.es_client.update(
                index=index,
                id=doc_id,
                doc=data,
                doc_as_upsert=True
            )
            print(f"Document upserted: {doc_id}")
            return True
        except Exception as e:
            print(f"Error upserting document {doc_id}: {e}")
            return False
    
    def transform_and_insert(self, txt_file_path: str, index_name: Optional[str] = None, 
                           save_json: bool = True) -> bool:
        """
        Complete transformation and insertion process
        
        Args:
            txt_file_path (str): Path to translation.txt file
            index_name (str, optional): ES index name
            save_json (bool): Whether to save the ES document as JSON file
            
        Returns:
            bool: True if successful, False otherwise
        """
        print("Starting transformation process...")
        print(f"Input file: {txt_file_path}")
        
        # Step 1: Parse the translation file
        print("\nStep 1: Parsing translation file...")
        parsed_data = self.parse_translation_file(txt_file_path)
        if not parsed_data:
            print("Failed to parse translation file")
            return False
        
        print(f"Parsed data for company: {parsed_data.get('companyId', 'Unknown')}")
        print(f"   Languages found: {len(parsed_data.get('translations', {}))}")
        
        # Step 2: Create ES document
        print("\nStep 2: Creating ES document...")
        es_document = self.create_es_document(parsed_data)
        if not es_document:
            print("Failed to create ES document")
            return False
        
        print(f"ES document created with {len([k for k in es_document.keys() if k.startswith('lang_')])} languages")
        
        # Step 3: Save as JSON (optional)
        if save_json:
            json_file = txt_file_path.replace('.txt', '_es_document.json')
            try:
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(es_document, f, indent=2, ensure_ascii=False)
                print(f"ES document saved to: {json_file}")
            except Exception as e:
                print(f"Warning: Could not save JSON file: {e}")
        
        # Step 4: Insert into Elasticsearch
        print("\nStep 3: Inserting into Elasticsearch...")
        company_id = es_document.get('companyId', 'unknown')
        success = self.upsert_data_to_es_single(es_document, company_id, index_name)
        
        if success:
            print("\nTransformation and insertion completed successfully!")
        else:
            print("\nTransformation completed but insertion failed")
        
        return success

def main():
    """Example usage of the transformer"""
    
    # =============================================================================
    # CONFIGURATION
    # =============================================================================
    TXT_FILE_PATH = "cyberpe865_translated.txt"  # Change this to your file
    INDEX_NAME = None  # Uses default from Config.py if None
    SAVE_JSON = True   # Whether to save the ES document as JSON
    
    # =============================================================================
    # TRANSFORMATION AND INSERTION
    # =============================================================================
    
    print("Translation TXT to Elasticsearch DSL Transformer")
    print("=" * 60)
    
    # Check if file exists
    if not os.path.exists(TXT_FILE_PATH):
        print(f"File not found: {TXT_FILE_PATH}")
        print("Please make sure the translation.txt file exists.")
        return
    
    # Create transformer instance
    transformer = TranslationToESTransformer()
    
    if not transformer.es_client:
        print("Cannot proceed without Elasticsearch connection")
        return
    
    # Perform transformation and insertion
    success = transformer.transform_and_insert(
        txt_file_path=TXT_FILE_PATH,
        index_name=INDEX_NAME,
        save_json=SAVE_JSON
    )
    
    if success:
        print(f"\nSuccessfully processed: {TXT_FILE_PATH}")
    else:
        print(f"\nFailed to process: {TXT_FILE_PATH}")

if __name__ == "__main__":
    main()
