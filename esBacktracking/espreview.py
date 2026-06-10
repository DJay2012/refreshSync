"""
esPreview - Simplified Elasticsearch Query Preview System

A consolidated reverse tagging system that allows users to test Elasticsearch DSL boolean queries
against article data from both print articles and social feed content.

This simplified version merges all core functionality into fewer files while maintaining
the same capabilities as the original complex structure.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import RequestError, ConnectionError
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# EXCEPTIONS
# ============================================================================

class ESPreviewError(Exception):
    """Base exception for esPreview system."""
    pass

class QueryValidationError(ESPreviewError):
    """Exception raised for query validation errors."""
    def __init__(self, message: str, query: str = None):
        super().__init__(message)
        self.query = query

# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class FieldMatches:
    """Represents field-level match information for an article."""
    score: float
    highlights: Dict[str, List[str]] = field(default_factory=dict)

@dataclass
class IndexResult:
    """Represents search results from a single index."""
    total_hits: int
    article_ids: List[str]
    execution_time_ms: int
    articles: List[Dict[str, Any]] = field(default_factory=list)  # Full article objects
    field_matches: Dict[str, FieldMatches] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

@dataclass
class ESPreviewResult:
    """Main result container for esPreview queries."""
    success: bool
    total_matches: int
    execution_time_ms: int
    query_info: Dict[str, Any] = field(default_factory=dict)
    index_results: Dict[str, IndexResult] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class ESPreviewConfig:
    """Configuration class for esPreview system."""
    
    # Result limits
    max_results_per_index: int = 50
    timeout_seconds: int = 30
    
    # Search configuration
    enable_highlighting: bool = True
    target_indexes: List[str] = field(default_factory=lambda: ["printarticleindex", "socialfeedindex"])
    search_fields: List[str] = field(default_factory=lambda: ["headlines", "summary", "text"])
    
    # Index-specific field mappings
    index_field_mappings: Dict[str, List[str]] = field(default_factory=lambda: {
        "printarticleindex": ["articleData.headlines", "articleData.summary", "articleData.text"],
        "socialfeedindex": ["feedData.headlines", "feedData.summary", "feedData.text"]
    })
    
    # Elasticsearch configuration
    es_host: str = field(default_factory=lambda: os.getenv("ES_HOST", "http://localhost:9200"))
    es_user: str = field(default_factory=lambda: os.getenv("ES_USER", "elastic"))
    es_password: str = field(default_factory=lambda: os.getenv("ES_PASSWORD", "New#pnq#Change!"))
    percolator_index: str = field(default_factory=lambda: os.getenv("ES_INDEX_NAME", "testindex"))
    
    # Logging configuration
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    
    # Performance settings
    parallel_search: bool = True
    connection_pool_size: int = 10
    retry_attempts: int = 3
    retry_delay: float = 1.0
    
    @classmethod
    def from_env(cls) -> 'ESPreviewConfig':
        """Create configuration from environment variables."""
        return cls(
            max_results_per_index=int(os.getenv("ESPREVIEW_MAX_RESULTS", "50")),
            timeout_seconds=int(os.getenv("ESPREVIEW_TIMEOUT", "30")),
            enable_highlighting=os.getenv("ESPREVIEW_HIGHLIGHTING", "true").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            parallel_search=os.getenv("ESPREVIEW_PARALLEL", "true").lower() == "true",
            connection_pool_size=int(os.getenv("ESPREVIEW_POOL_SIZE", "10")),
            retry_attempts=int(os.getenv("ESPREVIEW_RETRY_ATTEMPTS", "3")),
            retry_delay=float(os.getenv("ESPREVIEW_RETRY_DELAY", "1.0"))
        )

# ============================================================================
# UTILITIES
# ============================================================================

class Logger:
    """Simplified logging utility."""
    
    @staticmethod
    def setup_logging(level: str = "INFO") -> logging.Logger:
        """Setup logging configuration."""
        logging.basicConfig(
            level=getattr(logging, level.upper()),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        return logging.getLogger("esPreview")
    
    @staticmethod
    def get_logger(name: str, level: str = "INFO") -> logging.Logger:
        """Get a logger instance."""
        return logging.getLogger(name)

class InputFormatDetector:
    """Detects the format of user input (boolean string vs DSL JSON)."""
    
    def detect_format(self, user_input: str) -> Tuple[str, Dict[str, Any]]:
        """Detect if input is boolean string or DSL JSON."""
        user_input = user_input.strip()
        
        # Try to parse as JSON first
        try:
            json.loads(user_input)
            return "dsl_json", {"is_valid": True, "errors": []}
        except json.JSONDecodeError:
            pass
        
        # Check if it looks like a boolean string
        if self._is_boolean_string(user_input):
            return "boolean_string", {"is_valid": True, "errors": []}
        
        return "unknown", {"is_valid": False, "errors": ["Unable to determine input format"]}
    
    def _is_boolean_string(self, text: str) -> bool:
        """Check if text looks like a boolean query string."""
        # Basic heuristics for boolean strings
        boolean_indicators = [' AND ', ' OR ', ' NOT ', '(', ')', '"']
        
        # If it contains boolean operators, it's likely a boolean string
        if any(indicator in text for indicator in boolean_indicators):
            return True
        
        # If it's a simple word without JSON structure, treat as boolean
        if not text.startswith('{') and not text.startswith('['):
            return True
        
        return False

class BooleanToDSLConverter:
    """Converts boolean query strings to Elasticsearch DSL."""
    
    def convert(self, boolean_string: str, target_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Convert boolean string to Elasticsearch DSL query."""
        if not target_fields:
            target_fields = ["headlines", "summary", "text"]
        
        # Simple conversion - this is a basic implementation
        # For production, you'd want a more sophisticated parser
        query_parts = self._parse_boolean_string(boolean_string)
        return self._build_dsl_query(query_parts, target_fields)
    
    def _parse_boolean_string(self, boolean_string: str) -> List[Dict[str, Any]]:
        """Parse boolean string into query parts, respecting quoted phrases."""
        parts = []
        
        # Remove outer parentheses if present
        boolean_string = boolean_string.strip()
        if boolean_string.startswith('(') and boolean_string.endswith(')'):
            # Check if the entire string is wrapped in parentheses
            level = 0
            is_wrapped = True
            for i, char in enumerate(boolean_string):
                if char == '(':
                    level += 1
                elif char == ')':
                    level -= 1
                    if level == 0 and i < len(boolean_string) - 1:
                        is_wrapped = False
                        break
            if is_wrapped:
                boolean_string = boolean_string[1:-1].strip()
        
        # Tokenize respecting quoted phrases and parentheses
        tokens = self._tokenize_with_quotes(boolean_string)
        
        # Check if we have OR operators - if so, all parts should be "should"
        has_or = any(token.upper() == 'OR' for token in tokens)
        has_and = any(token.upper() == 'AND' for token in tokens)
        
        # Determine default operator type
        if has_or and not has_and:
            default_operator = "should"
        else:
            default_operator = "must"
        
        # Group tokens by operators
        current_group = []
        current_operator = None
        
        i = 0
        while i < len(tokens):
            token = tokens[i]
            
            if token.upper() == 'AND':
                if current_group:
                    # Preserve quoted phrases when joining - if group has only one quoted token, keep it quoted
                    if len(current_group) == 1 and current_group[0].startswith('"') and current_group[0].endswith('"'):
                        query_text = current_group[0]  # Keep the quoted phrase as-is
                    else:
                        query_text = " ".join(current_group).strip()
                    if query_text:
                        parts.append({"type": "must", "query": query_text})
                    current_group = []
                current_operator = "must"
            elif token.upper() == 'OR':
                if current_group:
                    # Preserve quoted phrases when joining - if group has only one quoted token, keep it quoted
                    if len(current_group) == 1 and current_group[0].startswith('"') and current_group[0].endswith('"'):
                        query_text = current_group[0]  # Keep the quoted phrase as-is
                    else:
                        query_text = " ".join(current_group).strip()
                    if query_text:
                        parts.append({"type": "should", "query": query_text})
                    current_group = []
                current_operator = "should"
            else:
                current_group.append(token)
            
            i += 1
        
        # Add the last group
        if current_group:
            # Preserve quoted phrases when joining - if group has only one quoted token, keep it quoted
            if len(current_group) == 1 and current_group[0].startswith('"') and current_group[0].endswith('"'):
                query_text = current_group[0]  # Keep the quoted phrase as-is
            else:
                query_text = " ".join(current_group).strip()
            if query_text:
                if current_operator is None:
                    parts.append({"type": default_operator, "query": query_text})
                else:
                    parts.append({"type": current_operator, "query": query_text})
        
        # If no parts were created, treat entire string as single query
        if not parts:
            parts.append({"type": default_operator, "query": boolean_string.strip()})
        
        return parts
    
    def _tokenize_with_quotes(self, text: str) -> List[str]:
        """Tokenize text while preserving quoted phrases."""
        tokens = []
        i = 0
        text_len = len(text)
        
        while i < text_len:
            # Skip whitespace
            if text[i].isspace():
                i += 1
                continue
            
            # Handle quoted strings
            if text[i] == '"':
                # Find the closing quote
                end_quote = text.find('"', i + 1)
                if end_quote != -1:
                    quoted_text = text[i:end_quote + 1]  # Include both quotes
                    tokens.append(quoted_text)
                    i = end_quote + 1
                    continue
                else:
                    # Unclosed quote - treat as regular text
                    tokens.append(text[i])
                    i += 1
                    continue
            
            # Handle parentheses
            if text[i] in ['(', ')']:
                tokens.append(text[i])
                i += 1
                continue
            
            # Handle operators (AND, OR, NOT) - case insensitive matching
            if i + 2 < text_len and text[i:i+3].upper() == 'AND':
                if (i == 0 or text[i-1].isspace()) and (i + 3 >= text_len or text[i+3].isspace()):
                    tokens.append('AND')
                    i += 3
                    continue
            if i + 1 < text_len and text[i:i+2].upper() == 'OR':
                if (i == 0 or text[i-1].isspace()) and (i + 2 >= text_len or text[i+2].isspace()):
                    tokens.append('OR')
                    i += 2
                    continue
            if i + 2 < text_len and text[i:i+3].upper() == 'NOT':
                if (i == 0 or text[i-1].isspace()) and (i + 3 >= text_len or text[i+3].isspace()):
                    tokens.append('NOT')
                    i += 3
                    continue
            
            # Collect regular word/token
            start = i
            while i < text_len and not text[i].isspace() and text[i] not in ['"', '(', ')']:
                # Check if we've hit an operator - we need to break before consuming it
                if i + 2 < text_len and text[i:i+3].upper() == 'AND':
                    if (i == 0 or text[i-1].isspace()) and (i + 3 >= text_len or text[i+3].isspace() or text[i+3] in ['"', '(', ')']):
                        break
                if i + 1 < text_len and text[i:i+2].upper() == 'OR':
                    if (i == 0 or text[i-1].isspace()) and (i + 2 >= text_len or text[i+2].isspace() or text[i+2] in ['"', '(', ')']):
                        break
                if i + 2 < text_len and text[i:i+3].upper() == 'NOT':
                    if (i == 0 or text[i-1].isspace()) and (i + 3 >= text_len or text[i+3].isspace() or text[i+3] in ['"', '(', ')']):
                        break
                i += 1
            
            if i > start:
                token = text[start:i].strip()
                if token:
                    tokens.append(token)
        
        return tokens
    
    def _build_dsl_query(self, parts: List[Dict[str, Any]], target_fields: List[str]) -> Dict[str, Any]:
        """Build Elasticsearch DSL query from parsed parts."""
        if len(parts) == 1:
            return self._create_field_query(parts[0]["query"], target_fields)
        
        bool_clauses = {"must": [], "should": [], "must_not": []}
        
        for part in parts:
            field_query = self._create_field_query(part["query"], target_fields)
            bool_clauses[part["type"]].append(field_query)
        
        # Flatten nested bool.should queries if we have multiple should clauses
        # This helps with highlighting - Elasticsearch can better track which fields matched
        if len(bool_clauses["should"]) > 1:
            # Check if all should clauses are themselves bool.should queries
            # If so, flatten them into a single level for better highlighting
            flattened_should = []
            for clause in bool_clauses["should"]:
                if isinstance(clause, dict) and "bool" in clause and "should" in clause["bool"]:
                    # Flatten: add all inner should clauses to the outer should
                    flattened_should.extend(clause["bool"]["should"])
                else:
                    flattened_should.append(clause)
            
            if flattened_should:
                bool_clauses["should"] = flattened_should
        
        return {"bool": {k: v for k, v in bool_clauses.items() if v}}
    
    def _create_field_query(self, query_text: str, target_fields: List[str]) -> Dict[str, Any]:
        """Create a multi-field query using percolator-style matching.
        
        Uses percolator-style approach: combines fields into a single content field
        using runtime fields, then applies match_phrase for exact phrase matching.
        This matches exactly how the percolator tagger works.
        """
        query_text = query_text.strip()
        
        # Check if this is a quoted phrase (exact phrase match required)
        is_quoted = query_text.startswith('"') and query_text.endswith('"')
        if is_quoted:
            # Remove quotes and use match_phrase for exact phrase matching
            query_text = query_text.strip('"')
            
            # Use percolator-style matching: create a runtime field that combines
            # headline, summary, and text (matching percolator's content field structure)
            # Then apply match_phrase on this combined field
            if len(target_fields) == 1:
                # Single field - use direct match_phrase with strict settings
                is_single_word = ' ' not in query_text.strip()
                if is_single_word:
                    # For single compound words, use match_phrase to require tokens in sequence
                    # This ensures "DataDog" (analyzed as ["data", "dog"]) requires "data" immediately followed by "dog"
                    # Articles with just "dog" won't match because they lack "data" before it
                    return {
                        "match_phrase": {
                            target_fields[0]: {
                                "query": query_text,
                                "slop": 0,  # No word reordering
                                "zero_terms_query": "none"
                            }
                        }
                    }
                else:
                    return {
                        "match_phrase": {
                            target_fields[0]: {
                                "query": query_text,
                                "slop": 0,  # No word reordering allowed
                                "zero_terms_query": "none"  # Don't match if analyzer removes all terms
                            }
                        }
                    }
            
            # For multiple fields, use individual match_phrase queries per field in a bool.should clause
            # This allows Elasticsearch to track which fields matched via highlighting
            # and provides better percolator-style exact phrase matching per field
            # Check if query is a single word or multi-word phrase for strict matching
            is_single_word = ' ' not in query_text.strip()
            
            should_clauses = []
            for field in target_fields:
                if is_single_word:
                    # For single compound words, use match_phrase to require tokens in sequence
                    # This ensures "DataDog" (analyzed as ["data", "dog"]) requires "data" immediately followed by "dog"
                    # Articles with just "dog" won't match because they lack "data" before it
                    should_clauses.append({
                        "match_phrase": {
                            field: {
                                "query": query_text,
                                "slop": 0,  # No word reordering
                                "zero_terms_query": "none"
                            }
                        }
                    })
                else:
                    # For multi-word phrases, use match_phrase
                    should_clauses.append({
                        "match_phrase": {
                            field: {
                                "query": query_text,
                                "slop": 0,  # No word reordering allowed
                                "zero_terms_query": "none"  # Don't match if analyzer removes all terms
                            }
                        }
                    })
            
            # Use bool.should so Elasticsearch can track which fields matched (via highlighting)
            return {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1
                }
            }
        else:
            # Regular term-based matching (may match words separately)
            if len(target_fields) == 1:
                return {"match": {target_fields[0]: query_text}}
            
            return {
                "multi_match": {
                    "query": query_text,
                    "fields": target_fields,
                    "type": "best_fields"
                }
            }

class FieldMapper:
    """Maps logical field names to actual Elasticsearch field names."""
    
    def __init__(self, index_mappings: Dict[str, List[str]]):
        self.index_mappings = index_mappings
    
    def map_query_to_article_fields(self, query: Dict[str, Any], target_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Map query to use actual article field names."""
        if not target_fields:
            target_fields = ["headlines", "summary", "text"]
        
        # For each index, create a separate query with mapped fields
        index_queries = {}
        
        for index_name, actual_fields in self.index_mappings.items():
            mapped_query = self._map_fields_in_query(query, target_fields, actual_fields)
            index_queries[index_name] = mapped_query
        
        return index_queries
    
    def _map_fields_in_query(self, query: Dict[str, Any], logical_fields: List[str], actual_fields: List[str]) -> Dict[str, Any]:
        """Map logical field names to actual field names in a query."""
        # Handle script_score queries specially (they contain field names in script source)
        if "script_score" in query:
            mapped_query = query.copy()
            script_score = mapped_query["script_score"].copy()
            script = script_score.get("script", {})
            
            # Update field names in script source
            if "source" in script:
                script_source = script["source"]
                for logical, actual in zip(logical_fields, actual_fields):
                    # Replace field name in script source (handles both 'field' and "field" quotes)
                    script_source = script_source.replace(f"'{logical}'", f"'{actual}'")
                    script_source = script_source.replace(f'"{logical}"', f'"{actual}"')
                    script_source = script_source.replace(f"doc['{logical}']", f"doc['{actual}']")
                    script_source = script_source.replace(f'doc["{logical}"]', f'doc["{actual}"]')
                
                script = {**script, "source": script_source}
                script_score = {**script_score, "script": script}
                mapped_query["script_score"] = script_score
            return mapped_query
        
        # For other query types, use simple string replacement
        query_str = json.dumps(query)
        
        for logical, actual in zip(logical_fields, actual_fields):
            query_str = query_str.replace(f'"{logical}"', f'"{actual}"')
        
        return json.loads(query_str)

# ============================================================================
# COMPANY QUERY RETRIEVER
# ============================================================================

class CompanyQueryRetriever:
    """Retrieves stored boolean queries from percolator index by companyId."""
    
    def __init__(self, es_client: Elasticsearch, percolator_index: str):
        self.es_client = es_client
        self.percolator_index = percolator_index
        self.logger = Logger.get_logger(__name__)
    
    def get_company_query(self, company_id: str, language: str = "en") -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Retrieve stored boolean query for a company."""
        try:
            self.logger.info(f"Retrieving query for company {company_id}, language {language}")
            
            # Search for company by ID
            search_query = {
                "query": {"term": {"companyId": company_id}},
                "size": 1,
                "_source": True
            }
            
            response = self.es_client.search(index=self.percolator_index, body=search_query)
            hits = response.get('hits', {}).get('hits', [])
            
            if not hits:
                raise ESPreviewError(f"Company with ID '{company_id}' not found in percolator index")
            
            company_doc = hits[0]['_source']
            company_info = {
                "companyId": company_doc.get('companyId'),
                "companyName": company_doc.get('companyName', 'Unknown'),
                "available_languages": [key for key in company_doc.keys() if key.startswith('lang_')]
            }
            
            # Get language-specific query
            lang_field = f"lang_{language}"
            if lang_field not in company_doc:
                available_langs = [key.replace('lang_', '') for key in company_doc.keys() if key.startswith('lang_')]
                raise ESPreviewError(
                    f"Language '{language}' not available for company '{company_id}'. "
                    f"Available languages: {', '.join(available_langs)}"
                )
            
            query_dict = company_doc[lang_field]
            self.logger.info(f"Successfully retrieved query for {company_id} ({company_info['companyName']})")
            
            return query_dict, company_info
            
        except Exception as e:
            self.logger.error(f"Failed to retrieve company query: {e}")
            raise ESPreviewError(f"Company query retrieval failed: {str(e)}")
    
    def list_companies(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List available companies."""
        try:
            search_query = {
                "query": {"match_all": {}},
                "size": limit,
                "_source": ["companyId", "companyName"]
            }
            
            response = self.es_client.search(index=self.percolator_index, body=search_query)
            hits = response.get('hits', {}).get('hits', [])
            
            companies = []
            for hit in hits:
                source = hit['_source']
                companies.append({
                    "companyId": source.get('companyId'),
                    "companyName": source.get('companyName', 'Unknown')
                })
            
            self.logger.info(f"Retrieved {len(companies)} companies")
            return companies
            
        except Exception as e:
            self.logger.error(f"Failed to list companies: {e}")
            raise ESPreviewError(f"Company listing failed: {str(e)}")
    
    def search_companies(self, search_term: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search for companies by name or ID."""
        try:
            search_query = {
                "query": {
                    "multi_match": {
                        "query": search_term,
                        "fields": ["companyId", "companyName"],
                        "type": "best_fields"
                    }
                },
                "size": limit,
                "_source": ["companyId", "companyName"]
            }
            
            response = self.es_client.search(index=self.percolator_index, body=search_query)
            hits = response.get('hits', {}).get('hits', [])
            
            companies = []
            for hit in hits:
                source = hit['_source']
                companies.append({
                    "companyId": source.get('companyId'),
                    "companyName": source.get('companyName', 'Unknown'),
                    "score": hit.get('_score', 0.0)
                })
            
            self.logger.info(f"Found {len(companies)} companies matching '{search_term}'")
            return companies
            
        except Exception as e:
            self.logger.error(f"Failed to search companies: {e}")
            raise ESPreviewError(f"Company search failed: {str(e)}")

# ============================================================================
# SEARCH ENGINE
# ============================================================================

class SearchEngine:
    """Handles Elasticsearch search operations."""
    
    def __init__(self, es_client: Elasticsearch, config: ESPreviewConfig):
        self.es_client = es_client
        self.config = config
        self.logger = Logger.get_logger(__name__)
        self.field_mapper = FieldMapper(config.index_field_mappings)
    
    def execute_search(self, query: Dict[str, Any], indexes: Optional[List[str]] = None, include_content: bool = True) -> Dict[str, Any]:
        """Execute search against specified indexes."""
        if not indexes:
            indexes = self.config.target_indexes
        
        self.logger.info(f"Executing parallel search against {len(indexes)} indexes: {indexes}")
        
        if self.config.parallel_search and len(indexes) > 1:
            return self._execute_parallel_search(query, indexes, include_content)
        else:
            return self._execute_sequential_search(query, indexes, include_content)
    
    def _execute_parallel_search(self, query: Dict[str, Any], indexes: List[str], include_content: bool = True) -> Dict[str, Any]:
        """Execute search in parallel across multiple indexes."""
        results = {}
        
        with ThreadPoolExecutor(max_workers=len(indexes)) as executor:
            future_to_index = {
                executor.submit(self._search_single_index, query, index, include_content): index
                for index in indexes
            }
            
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception as e:
                    self.logger.error(f"Search failed for index {index}: {e}")
                    results[index] = {
                        "total_hits": 0,
                        "articles": [],
                        "execution_time_ms": 0,
                        "errors": [str(e)]
                    }
        
        return results
    
    def _execute_sequential_search(self, query: Dict[str, Any], indexes: List[str], include_content: bool = True) -> Dict[str, Any]:
        """Execute search sequentially across indexes."""
        results = {}
        
        for index in indexes:
            try:
                results[index] = self._search_single_index(query, index, include_content)
            except Exception as e:
                self.logger.error(f"Search failed for index {index}: {e}")
                results[index] = {
                    "total_hits": 0,
                    "articles": [],
                    "execution_time_ms": 0,
                    "errors": [str(e)]
                }
        
        return results
    
    def _search_single_index(self, query: Dict[str, Any], index: str, include_content: bool = True) -> Dict[str, Any]:
        """Execute search against a single index."""
        start_time = time.time()
        
        self.logger.info(f"=== _search_single_index CALLED ===")
        self.logger.info(f"Index: {index}, include_content: {include_content} (type: {type(include_content)})")
        self.logger.info(f"Executing search against index: {index}")
        
        # Map query to index-specific fields
        mapped_query = self.field_mapper.map_query_to_article_fields(query)
        index_query = mapped_query.get(index, query)
        
        # Log the index-specific query for debugging
        self.logger.info(f"Index-specific query for {index}: {json.dumps(index_query, indent=2)}")
        
        
        # Build search request
        source_fields = ["_id"]
        if include_content:
            # Include all fields needed to determine matches and return article content
            source_fields.extend([
                "headlines", "summary", "text",
                "articleData.headlines", "articleData.summary", "articleData.text",
                "feedData.headlines", "feedData.summary", "feedData.text"
            ])
            # Add index-specific fields
            if index == "printarticleindex":
                source_fields.extend(["articleInfo.articleDate", "uploadInfo.imageId"])
            elif index == "socialfeedindex":
                source_fields.extend(["feedData.feedDate", "feedData.feedDateTime", "feedData.articleDateNumber", "feedInfo.link"])
        
        search_request = {
            "query": index_query,
            "size": self.config.max_results_per_index,
            "_source": source_fields
        }
        
        # Add sort by date descending based on index type
        if index == "printarticleindex":
            search_request["sort"] = [
                {"articleInfo.articleDate": {"order": "desc", "unmapped_type": "date", "missing": "_last"}},
                {"_id": {"order": "desc"}}
            ]
        elif index == "socialfeedindex":
            search_request["sort"] = [
                {"feedData.feedDateTime": {"order": "desc", "unmapped_type": "date", "missing": "_last"}},
                {"feedData.feedDate": {"order": "desc", "unmapped_type": "date", "missing": "_last"}},
                {"feedData.articleDateNumber": {"order": "desc", "unmapped_type": "long", "missing": "_last"}},
                {"_id": {"order": "desc"}}
            ]
        
        # Always enable highlighting for matched_fields detection (even if config says otherwise when include_content=True)
        if include_content or self.config.enable_highlighting:
            # Request highlighting for the specific fields we're searching
            highlight_fields = {}
            if include_content:
                # Highlight the exact fields we're searching on
                for field in ["headlines", "summary", "text", "articleData.headlines", "articleData.summary", "articleData.text", "feedData.headlines", "feedData.summary", "feedData.text"]:
                    highlight_fields[field] = {
                        "fragment_size": 150,
                        "number_of_fragments": 3
                    }
            else:
                # Fallback to all fields if not including content
                highlight_fields["*"] = {
                    "fragment_size": 150,
                    "number_of_fragments": 3
                }
            
            search_request["highlight"] = {
                "fields": highlight_fields,
                "require_field_match": False  # Highlight all fields that contain the query, even if they didn't match the exact query structure
            }
        
        try:
            response = self.es_client.search(index=index, body=search_request)
            
            # Process results
            hits = response.get('hits', {}).get('hits', [])
            total_hits = response.get('hits', {}).get('total', {}).get('value', 0)
            
            articles = []
            highlights_dict = {}
            
            for hit in hits:
                article_id = hit['_id']
                
                if include_content:
                    self.logger.info(f"Processing article {article_id} with include_content=True (total articles so far: {len(articles)})")
                    source = hit.get('_source', {})
                    
                    # Extract article content from different possible field structures
                    article_content = {
                        "id": article_id,
                        "headlines": source.get('headlines') or source.get('articleData', {}).get('headlines') or source.get('feedData', {}).get('headlines'),
                        "summary": source.get('summary') or source.get('articleData', {}).get('summary') or source.get('feedData', {}).get('summary'),
                        "text": source.get('text') or source.get('articleData', {}).get('text') or source.get('feedData', {}).get('text')
                    }
                    
                    # Add index-specific fields
                    if index == "printarticleindex":
                        article_content["articleDate"] = source.get('articleInfo', {}).get('articleDate')
                        article_content["imageId"] = source.get('uploadInfo', {}).get('imageId')
                    elif index == "socialfeedindex":
                        article_content["feedDate"] = source.get('feedData', {}).get('feedDate')
                        article_content["feedDateTime"] = source.get('feedData', {}).get('feedDateTime')
                        article_content["articleDateNumber"] = source.get('feedData', {}).get('articleDateNumber')
                        article_content["links"] = source.get('feedInfo', {}).get('link')
                    
                    articles.append(article_content)
                else:
                    articles.append(article_id)
                
                # Store highlights if available (for debugging)
                if hit.get('highlight'):
                    highlights_dict[article_id] = hit.get('highlight', {})
                    if len(articles) < 3:  # Log first few for debugging
                        self.logger.info(f"🔍 Article {article_id} has highlights stored: {list(hit.get('highlight', {}).keys())}")
                else:
                    if len(articles) < 3:  # Log first few for debugging
                        self.logger.warning(f"⚠️ Article {article_id} has NO highlights! Hit keys: {list(hit.keys())}")
            
            execution_time = int((time.time() - start_time) * 1000)
            
            self.logger.info(f"Processed {len(articles)} articles from index {index} (requested: {self.config.max_results_per_index}, total available: {total_hits})")
            self.logger.info(f"Search completed for index {index}: {total_hits} total hits, {len(articles)} returned, in {execution_time}ms")
            
            # Log if we got fewer results than requested and there are more available
            if len(articles) < self.config.max_results_per_index and total_hits > len(articles):
                self.logger.warning(
                    f"Index {index}: Only returned {len(articles)} results out of {total_hits} total matches. "
                    f"Requested limit was {self.config.max_results_per_index}. "
                    f"This might indicate the query is too restrictive or there's an issue with result retrieval."
                )
            
            return {
                "total_hits": total_hits,
                "articles": articles,
                "execution_time_ms": execution_time,
                "highlights": highlights_dict,
                "errors": []
            }
            
        except Exception as e:
            execution_time = int((time.time() - start_time) * 1000)
            self.logger.error(f"Search error for index {index}: {e}")
            
            return {
                "total_hits": 0,
                "articles": [],
                "execution_time_ms": execution_time,
                "highlights": {},
                "errors": [str(e)]
            }
    
    def _extract_query_phrases_removed(self, query: Dict[str, Any]) -> List[str]:
        """Extract query phrases from the Elasticsearch query for field matching."""
        phrases = []
        self.logger.info(f"_extract_query_phrases called with query type: {type(query)}")
        
        def extract_from_dict(obj, depth=0):
            if depth > 10:  # Prevent infinite recursion
                return
            if isinstance(obj, dict):
                # Check for match_phrase queries
                if "match_phrase" in obj:
                    match_phrase = obj["match_phrase"]
                    for field, value in match_phrase.items():
                        if isinstance(value, dict) and "query" in value:
                            phrase = value["query"]
                            self.logger.info(f"  Found match_phrase query: '{phrase}'")
                            phrases.append(phrase)
                        elif isinstance(value, str):
                            self.logger.info(f"  Found match_phrase string: '{value}'")
                            phrases.append(value)
                
                # Check script_score queries (for percolator-style matching)
                if "script_score" in obj:
                    self.logger.info(f"  Found script_score query")
                    script_score_obj = obj["script_score"]
                    script = script_score_obj.get("script", {})
                    self.logger.info(f"    Script type: {type(script)}, keys: {list(script.keys()) if isinstance(script, dict) else 'N/A'}")
                    if isinstance(script, dict):
                        if "params" in script:
                            params = script["params"]
                            self.logger.info(f"    Params keys: {list(params.keys())}")
                            if "query" in params:
                                phrase = params["query"]
                                self.logger.info(f"  ✅ Found script_score query param: '{phrase}'")
                                phrases.append(phrase)
                            else:
                                self.logger.warning(f"    ⚠️ 'query' not found in params! Available keys: {list(params.keys())}")
                        else:
                            self.logger.warning(f"    ⚠️ 'params' not found in script! Available keys: {list(script.keys())}")
                    elif isinstance(script, str):
                        # Script might be a string in some cases - try to extract from source
                        # Note: This won't work for complex scripts, but covers simple cases
                        self.logger.warning(f"    ⚠️ Script is a string, cannot extract params.query")
                    else:
                        self.logger.warning(f"    ⚠️ Unexpected script type: {type(script)}")
                
                # Check bool queries with should/must clauses (OR/AND queries)
                if "bool" in obj:
                    bool_query = obj["bool"]
                    # Extract from should clauses (OR)
                    if "should" in bool_query:
                        self.logger.info(f"  Found bool.should with {len(bool_query['should'])} clauses")
                        for idx, clause in enumerate(bool_query["should"]):
                            self.logger.info(f"    Processing should clause {idx}: {type(clause)}, keys: {list(clause.keys()) if isinstance(clause, dict) else 'N/A'}")
                            extract_from_dict(clause, depth + 1)
                    # Extract from must clauses (AND)
                    if "must" in bool_query:
                        self.logger.info(f"  Found bool.must with {len(bool_query['must'])} clauses")
                        for idx, clause in enumerate(bool_query["must"]):
                            self.logger.info(f"    Processing must clause {idx}: {type(clause)}, keys: {list(clause.keys()) if isinstance(clause, dict) else 'N/A'}")
                            extract_from_dict(clause, depth + 1)
                
                # Recursively check nested structures (but only if not already handled above)
                if "bool" not in obj and "match_phrase" not in obj and "script_score" not in obj:
                    for key, value in obj.items():
                        if key not in ["query", "script"]:  # Skip these to avoid recursion loops
                            extract_from_dict(value, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    extract_from_dict(item, depth + 1)
        
        extract_from_dict(query)
        # Remove duplicates while preserving order
        seen = set()
        unique_phrases = []
        for phrase in phrases:
            if phrase and phrase not in seen:
                seen.add(phrase)
                unique_phrases.append(phrase)
        
        # Log extracted phrases for debugging (use self.logger if available)
        if unique_phrases:
            self.logger.info(f"✅ Extracted {len(unique_phrases)} unique query phrases: {unique_phrases}")
        else:
            self.logger.warning(f"❌ No query phrases extracted from query!")
            self.logger.warning(f"Query structure: {json.dumps(query, indent=2)}")
        
        return unique_phrases
    
    def _detect_matched_fields_removed(self, article_content: Dict[str, Any], query_phrases: List[str]) -> List[str]:
        """Detect which fields contain the query phrases."""
        matched_fields = []
        
        # Log input for debugging
        self.logger.info(f"_detect_matched_fields called with {len(query_phrases)} phrases: {query_phrases}")
        
        # Field mapping: article field -> field name
        fields_to_check = {
            "headlines": article_content.get("headlines", ""),
            "summary": article_content.get("summary", ""),
            "text": article_content.get("text", "")
        }
        
        # Log field content presence
        for field_name, field_content in fields_to_check.items():
            has_content = bool(field_content)
            content_preview = str(field_content)[:50] if field_content else "EMPTY"
            self.logger.debug(f"  Field '{field_name}': has_content={has_content}, preview='{content_preview}...'")
        
        if not query_phrases:
            self.logger.warning(f"⚠️ _detect_matched_fields called with empty query_phrases list!")
            return []
        
        for phrase in query_phrases:
            phrase_lower = phrase.lower().strip()
            if not phrase_lower:
                continue
            
            # Check if phrase is single word or multi-word
            is_single_word = ' ' not in phrase_lower
            self.logger.debug(f"  Checking phrase '{phrase}' (single_word={is_single_word})")
            
            for field_name, field_content in fields_to_check.items():
                if not field_content:
                    continue
                
                field_content_lower = str(field_content).lower()
                
                if field_name in matched_fields:
                    continue  # Already matched
                
                if is_single_word:
                    # For single words, use word boundaries to prevent substring matches
                    # e.g., "dog" should not match "datadog"
                    pattern = r'\b' + re.escape(phrase_lower) + r'\b'
                    if re.search(pattern, field_content_lower):
                        self.logger.info(f"  ✅ Matched '{phrase}' in field '{field_name}'")
                        matched_fields.append(field_name)
                else:
                    # For multi-word phrases, check if the phrase exists (with flexible whitespace)
                    normalized_phrase = re.sub(r'\s+', ' ', phrase_lower)
                    normalized_content = re.sub(r'\s+', ' ', field_content_lower)
                    if normalized_phrase in normalized_content:
                        self.logger.info(f"  ✅ Matched '{phrase}' in field '{field_name}'")
                        matched_fields.append(field_name)
        
        self.logger.info(f"_detect_matched_fields returning: {matched_fields}")
        return matched_fields

# ============================================================================
# MAIN ENGINE
# ============================================================================

class ESPreviewEngine:
    """Main orchestrator for esPreview query execution and result processing."""
    
    def __init__(self, config: ESPreviewConfig, es_client: Optional[Elasticsearch] = None):
        self.config = config
        self.logger = Logger.setup_logging(config.log_level)
        
        # Initialize Elasticsearch client
        if es_client:
            self.es_client = es_client
        else:
            self.es_client = Elasticsearch(
                hosts=[config.es_host],
                http_auth=(config.es_user, config.es_password),
                request_timeout=config.timeout_seconds,
                headers={"Accept": "application/vnd.elasticsearch+json; compatible-with=8"}
            )
        
        # Initialize components
        self.search_engine = SearchEngine(self.es_client, config)
        self.company_retriever = CompanyQueryRetriever(self.es_client, config.percolator_index)
        self.input_detector = InputFormatDetector()
        self.boolean_converter = BooleanToDSLConverter()
        
        self.logger.info("ESPreview engine initialized successfully")
    
    def health_check(self) -> Dict[str, Any]:
        """Perform system health check."""
        try:
            # Test Elasticsearch connection
            es_status = "connected" if self.es_client.ping() else "disconnected"
            
            # Test configuration
            config_errors = []
            if not self.config.target_indexes:
                config_errors.append("No target indexes configured")
            if not self.config.search_fields:
                config_errors.append("No search fields configured")
            
            config_status = "valid" if not config_errors else "invalid"
            
            # Overall status
            overall_status = "healthy" if es_status == "connected" and config_status == "valid" else "unhealthy"
            
            return {
                "status": overall_status,
                "timestamp": time.time(),
                "elasticsearch": {"status": es_status},
                "configuration": {"status": config_status},
                "errors": config_errors
            }
            
        except Exception as e:
            return {
                "status": "unhealthy",
                "timestamp": time.time(),
                "elasticsearch": {"status": "error"},
                "configuration": {"status": "unknown"},
                "errors": [str(e)]
            }
    
    def execute_query(self, user_input: str, indexes: Optional[List[str]] = None, include_content: bool = True) -> ESPreviewResult:
        """Execute a query from user input."""
        start_time = time.time()
        
        self.logger.info(f"=== EXECUTE_QUERY CALLED ===")
        self.logger.info(f"include_content parameter value: {include_content} (type: {type(include_content)})")
        self.logger.info(f"Executing query against indexes: {indexes or self.config.target_indexes}")
        self.logger.info(f"User input query: {user_input[:100] if len(user_input) > 100 else user_input}")
        
        try:
            # Detect input format
            input_format, format_validation = self.input_detector.detect_format(user_input)
            
            if not format_validation["is_valid"]:
                raise QueryValidationError(
                    f"Input validation failed: {'; '.join(format_validation['errors'])}",
                    query=user_input
                )
            
            # Process query based on format
            if input_format == "boolean_string":
                query = self.boolean_converter.convert(user_input, self.config.search_fields)
                # Log the generated Elasticsearch query for debugging (use INFO level so it's always visible)
                self.logger.info(f"Generated Elasticsearch query: {json.dumps(query, indent=2)}")
            else:  # DSL JSON
                query = json.loads(user_input)
            
            self.logger.info(f"Processed query - Format: {input_format}")
            
            # Execute search with include_content parameter
            self.logger.info(f"🔍 About to call execute_search with include_content={include_content}")
            search_results = self.search_engine.execute_search(query, indexes, include_content=include_content)
            self.logger.info(f"🔍 execute_search returned, checking results...")
            
            # Process results
            result = self._process_search_results(search_results, start_time)
            result.query_info = {
                "original_input": user_input,
                "input_format": input_format,
                "target_indexes": indexes or self.config.target_indexes,
                "search_fields": self.config.search_fields,
                "query_type": "user_input"
            }
            
            self.logger.info(f"Query execution completed in {result.execution_time_ms}ms - Total matches: {result.total_matches}")
            return result
            
        except Exception as e:
            execution_time = int((time.time() - start_time) * 1000)
            self.logger.error(f"Query execution failed: {e}")
            
            return ESPreviewResult(
                success=False,
                total_matches=0,
                execution_time_ms=execution_time,
                errors=[str(e)]
            )
    
    def execute_company_query(self, company_id: str, language: str = "en", indexes: Optional[List[str]] = None, include_content: bool = True) -> ESPreviewResult:
        """Execute a stored company query."""
        start_time = time.time()
        
        self.logger.info(f"Executing company query for {company_id} (lang: {language}) against indexes: {indexes or self.config.target_indexes}")
        
        try:
            # Retrieve company query
            company_query, company_info = self.company_retriever.get_company_query(company_id, language)
            
            self.logger.info(f"Retrieved query for company: {company_info['companyName']} ({company_id})")
            
            # Execute search (always include content to detect matched fields)
            search_results = self.search_engine.execute_search(company_query, indexes, include_content=True)
            
            # Process results
            result = self._process_search_results(search_results, start_time)
            result.query_info = {
                "company_id": company_id,
                "company_name": company_info['companyName'],
                "language": language,
                "available_languages": company_info['available_languages'],
                "target_indexes": indexes or self.config.target_indexes,
                "search_fields": self.config.search_fields,
                "query_type": "company_stored_query"
            }
            
            self.logger.info(f"Company query execution completed in {result.execution_time_ms}ms - Total matches: {result.total_matches}")
            return result
            
        except Exception as e:
            execution_time = int((time.time() - start_time) * 1000)
            self.logger.error(f"Company query execution failed: {e}")
            
            return ESPreviewResult(
                success=False,
                total_matches=0,
                execution_time_ms=execution_time,
                errors=[str(e)]
            )
    
    def list_companies(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List available companies."""
        return self.company_retriever.list_companies(limit)
    
    def search_companies(self, search_term: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search for companies by name or ID."""
        return self.company_retriever.search_companies(search_term, limit)
    
    def _process_search_results(self, search_results: Dict[str, Any], start_time: float) -> ESPreviewResult:
        """Process raw search results into ESPreviewResult."""
        total_matches = 0
        total_available = 0
        index_results = {}
        
        for index_name, index_data in search_results.items():
            # Create IndexResult
            total_hits_for_index = index_data.get("total_hits", 0)
            articles_for_index = index_data.get("articles", [])
            
            # Extract article IDs and full article objects
            article_ids = []
            article_objects = []
            
            # Log what we received for debugging
            self.logger.info(f"_process_search_results: Processing {len(articles_for_index)} articles for index {index_name}")
            if articles_for_index and len(articles_for_index) > 0:
                first_article = articles_for_index[0]
                self.logger.info(f"First article type: {type(first_article)}, is_dict: {isinstance(first_article, dict)}")
                if isinstance(first_article, dict):
                    self.logger.info(f"First article keys: {list(first_article.keys())}")
                    self.logger.info(f"First article has matched_fields: {'matched_fields' in first_article}")
            
            for article in articles_for_index:
                if isinstance(article, dict):
                    # Full article object with content
                    article_id = article.get("id", article.get("_id", ""))
                    article_ids.append(article_id)
                    
                    article_objects.append(article)
                else:
                    # Just an ID string
                    article_ids.append(str(article))
                    # Create minimal article object with just ID
                    article_objects.append({"id": str(article)})
            
            index_result = IndexResult(
                total_hits=total_hits_for_index,
                article_ids=article_ids,
                articles=article_objects,  # Include full article objects
                execution_time_ms=index_data.get("execution_time_ms", 0),
                errors=index_data.get("errors", [])
            )
            
            # Process highlights if available
            highlights = index_data.get("highlights", {})
            for article_id, article_highlights in highlights.items():
                if article_id in index_result.article_ids:
                    field_matches = FieldMatches(
                        score=1.0,  # Default score
                        highlights=article_highlights
                    )
                    index_result.field_matches[article_id] = field_matches
            
            index_results[index_name] = index_result
            total_matches += len(articles_for_index)
            total_available += total_hits_for_index
        
        execution_time = int((time.time() - start_time) * 1000)
        
        # Log summary
        self.logger.info(
            f"Search summary: {total_matches} articles returned out of {total_available} total matches "
            f"across {len(index_results)} index(es)"
        )
        
        return ESPreviewResult(
            success=True,
            total_matches=total_matches,
            execution_time_ms=execution_time,
            index_results=index_results
        )

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point for testing."""
    try:
        config = ESPreviewConfig.from_env()
        engine = ESPreviewEngine(config)
        
        # Health check
        health = engine.health_check()
        print(f"System Status: {health['status']}")
        
        # Test query
        result = engine.execute_query("technology AND innovation")
        print(f"Test query results: {result.total_matches} matches")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
