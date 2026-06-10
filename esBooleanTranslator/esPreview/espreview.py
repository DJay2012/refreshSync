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
    matched_fields: List[str]
    score: float
    highlights: Dict[str, List[str]] = field(default_factory=dict)

@dataclass
class IndexResult:
    """Represents search results from a single index."""
    total_hits: int
    article_ids: List[str]
    execution_time_ms: int
    articles: List[Dict[str, Any]] = field(default_factory=list)  # Added article content
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
        "printarticleindex": ["articleData.headline", "articleData.headlines", "articleData.content", "articleData.text"],
        "socialfeedindex": ["feedData.headlineSnippet", "feedData.headlines", "feedData.text"]
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
        
        # Use recursive parser
        boolean_string = boolean_string.strip()
        tokens = self._normalize_tokens(self._tokenize_with_quotes(boolean_string))
        return self._build_recursive_dsl(tokens, target_fields)
    
    def _parse_boolean_string(self, boolean_string: str) -> List[Dict[str, Any]]:
        """Parse boolean string into query parts, respecting quoted phrases and parentheses."""
        # Remove outer parentheses if present
        boolean_string = boolean_string.strip()
        
        # Tokenize respecting quoted phrases and parentheses
        tokens = self._tokenize_with_quotes(boolean_string)
        
        # If wrapped in parentheses, strip them
        if len(tokens) >= 2 and tokens[0] == '(' and tokens[-1] == ')':
            # Check if they are matching pair (not just start and end)
            level = 0
            is_wrapped = True
            for i, token in enumerate(tokens):
                if token == '(':
                    level += 1
                elif token == ')':
                    level -= 1
                    if level == 0 and i < len(tokens) - 1:
                        is_wrapped = False
                        break
            if is_wrapped:
                tokens = tokens[1:-1]
        
        return self._parse_tokens(tokens)

    def _parse_tokens(self, tokens: List[str]) -> List[Dict[str, Any]]:
        """Recursively parse a list of tokens."""
        if not tokens:
            return []
            
        # Check for top-level ORs first (lowest precedence)
        # We need to find ORs that are at level 0 of parentheses
        or_indices = []
        level = 0
        for i, token in enumerate(tokens):
            if token == '(':
                level += 1
            elif token == ')':
                level -= 1
            elif token.upper() == 'OR' and level == 0:
                or_indices.append(i)
        
        if or_indices:
            # Split by OR
            parts = []
            start = 0
            for idx in or_indices:
                segment = tokens[start:idx]
                if segment:
                    # Recursively parse the segment
                    # If segment is complex, it becomes a sub-query
                    sub_parts = self._parse_tokens(segment)
                    if len(sub_parts) == 1:
                        parts.append({"type": "should", "query": sub_parts[0]["query"]})
                    else:
                        # It returned multiple parts (implicit AND or explicit ANDs)
                        # Wrap them in a bool/must
                        # Actually, _parse_tokens returns a list of parts.
                        # If we are splitting by OR, each segment is a SHOULD clause.
                        # But the segment itself might be "A AND B".
                        # So we need to build the query from the sub_parts.
                        
                        # If sub_parts has multiple items, they are implicitly ANDed (or explicit AND)
                        # We need to convert this list of parts into a single query object to put in "should"
                        
                        # Construct a bool query from sub_parts
                        bool_query = {"bool": {"must": [], "should": [], "must_not": []}}
                        has_clauses = False
                        for p in sub_parts:
                            # This is tricky because 'query' in parts is just a string or a dict?
                            # In the original code, 'query' was a string.
                            # But now we need to support nested structures.
                            # Let's change the return type of _parse_tokens to return DSL objects directly?
                            # Or keep it as parts but allow 'query' to be a dict (DSL).
                            pass
                        
                        # Let's simplify: _parse_tokens should return the list of parts as before,
                        # but we need to handle the recursion better.
                        pass
            
            # Let's restart this logic.
            # The goal is to return a list of parts that _build_dsl_query can use.
            # _build_dsl_query expects [{"type": "must/should", "query": "text"}]
            # But "query" needs to support nested DSL if we have recursion.
            
            # Actually, let's look at _build_dsl_query. It calls _create_field_query(part["query"]).
            # _create_field_query expects a string.
            # This means the current architecture assumes a flat list of string queries combined by a single operator type.
            # This is insufficient for nested logic like (A OR B) AND (C OR D).
            
            # We need to change _build_dsl_query to accept nested structures,
            # OR we need to perform the DSL construction recursively here.
            pass
        
        # Since I cannot easily change the entire architecture in one go, 
        # I will implement a recursive parser that builds the DSL directly.
        # But I need to respect the existing method signature if possible, or change the caller.
        # The caller is `convert`.
        
        # Let's change `convert` to call a new method `_build_recursive_dsl` instead of `_parse_boolean_string` + `_build_dsl_query`.
        pass
        
        return [] # Placeholder, will be replaced by the actual implementation below
        
    def _build_recursive_dsl(self, tokens: List[str], target_fields: List[str]) -> Dict[str, Any]:
        """Build DSL recursively from tokens."""
        if not tokens:
            return {}
            
        # 1. Handle wrapping parentheses
        while len(tokens) >= 2 and tokens[0] == '(' and tokens[-1] == ')':
            level = 0
            is_wrapped = True
            for i, token in enumerate(tokens):
                if token == '(':
                    level += 1
                elif token == ')':
                    level -= 1
                    if level == 0 and i < len(tokens) - 1:
                        is_wrapped = False
                        break
            if is_wrapped:
                tokens = tokens[1:-1]
            else:
                break
        
        if not tokens:
            return {}
            
        # 2. Split by OR (lowest precedence)
        or_indices = []
        level = 0
        for i, token in enumerate(tokens):
            if token == '(':
                level += 1
            elif token == ')':
                level -= 1
            elif token.upper() == 'OR' and level == 0:
                or_indices.append(i)
        
        if or_indices:
            should_clauses = []
            start = 0
            # Add a dummy index to handle the last segment
            for idx in or_indices + [len(tokens)]:
                segment = tokens[start:idx]
                if segment:
                    clause = self._build_recursive_dsl(segment, target_fields)
                    if clause:
                        should_clauses.append(clause)
                start = idx + 1
            
            if len(should_clauses) == 1:
                return should_clauses[0]
            return {"bool": {"should": should_clauses, "minimum_should_match": 1}}
            
        # 3. Split by AND
        and_indices = []
        level = 0
        for i, token in enumerate(tokens):
            if token == '(':
                level += 1
            elif token == ')':
                level -= 1
            elif token.upper() == 'AND' and level == 0:
                and_indices.append(i)
        
        if and_indices:
            must_clauses = []
            start = 0
            for idx in and_indices + [len(tokens)]:
                segment = tokens[start:idx]
                if segment:
                    clause = self._build_recursive_dsl(segment, target_fields)
                    if clause:
                        must_clauses.append(clause)
                start = idx + 1
            
            if len(must_clauses) == 1:
                return must_clauses[0]
            return {"bool": {"must": must_clauses}}
            
            # 3b. Split by NEAR (higher precedence than AND/OR in this parser's stack, 
        # but processed AFTER splitting by AND because A AND B NEAR C -> A AND (B NEAR C))
        # Wait, splitting order:
        # 1. OR (Split A OR B -> [A, B])
        # 2. AND (Split A AND B -> [A, B])
        # 3. NEAR (Within A, check for NEAR)
        
        near_indices = []
        level = 0
        for i, token in enumerate(tokens):
            if token == '(':
                level += 1
            elif token == ')':
                level -= 1
            elif token.upper().startswith('NEAR/') and level == 0:
                near_indices.append(i)
                
        if near_indices:
            # Split at the first NEAR index found
            idx = near_indices[0]
            left_tokens = tokens[:idx]
            right_tokens = tokens[idx+1:]
            op = tokens[idx]
            
            try:
                slop = int(op.upper().split('/')[1])
            except (IndexError, ValueError):
                slop = 10
                
            return self._create_near_query(left_tokens, right_tokens, slop, target_fields)

        # 4. Handle NOT (unary operator)
        # Check if first token is NOT
        if tokens[0].upper() == 'NOT':
            # Everything after NOT is negated
            # But we need to be careful about scope.
            # Since we already split by OR and AND, "NOT A B" means "NOT A" AND "B" (implicit AND)?
            # Or "NOT (A B)"?
            # Usually NOT binds tighter than AND.
            # But here we are at the leaf level (no top-level AND/OR).
            # So "NOT A" is valid. "NOT A B" -> "NOT A" AND "B"?
            
            # Let's assume "NOT" applies to the immediate next token (or parenthesized group).
            # If there are more tokens, they are implicitly ANDed.
            
            negated_segment = [tokens[1]]
            remaining = tokens[2:]
            
            # If token[1] is '(', we need to find the matching ')'
            if tokens[1] == '(':
                # Find matching paren
                level = 1
                for i in range(2, len(tokens)):
                    if tokens[i] == '(':
                        level += 1
                    elif tokens[i] == ')':
                        level -= 1
                        if level == 0:
                            negated_segment = tokens[1:i+1]
                            remaining = tokens[i+1:]
                            break
            
            must_not_clause = self._build_recursive_dsl(negated_segment, target_fields)
            
            result = {"bool": {"must_not": [must_not_clause]}}
            
            if remaining:
                # Implicit AND with remaining
                remaining_clause = self._build_recursive_dsl(remaining, target_fields)
                return {"bool": {"must": [result, remaining_clause]}}
            
            return result

        # 5. Implicit AND (multiple tokens side-by-side)
        # e.g. "A B" -> "A AND B"
        # But wait, if we are here, we have no explicit AND/OR.
        # So "A B" is treated as "A" AND "B".
        if len(tokens) > 1:
            must_clauses = []
            for token in tokens:
                # Treat each token as a separate query
                # This handles "A B" as "A" AND "B"
                clause = self._create_field_query(token, target_fields)
                must_clauses.append(clause)
            return {"bool": {"must": must_clauses}}
        
        # 6. Single token
        return self._create_field_query(tokens[0], target_fields)
    
    def _create_near_query(self, left_tokens: List[str], right_tokens: List[str], slop: int, target_fields: List[str]) -> Dict[str, Any]:
        """Create a boolean query containing span_near queries for each target field."""
        should_clauses = []
        
        for field in target_fields:
            # Build span query for left side
            left_span = self._build_span_query(left_tokens, field)
            # Build span query for right side
            right_span = self._build_span_query(right_tokens, field)
            
            if left_span and right_span:
                span_near = {
                    "span_near": {
                        "clauses": [left_span, right_span],
                        "slop": slop,
                        "in_order": False
                    }
                }
                should_clauses.append(span_near)
                
        if not should_clauses:
            return {}
            
        return {"bool": {"should": should_clauses, "minimum_should_match": 1}}

    def _build_span_query(self, tokens: List[str], field: str) -> Optional[Dict[str, Any]]:
        """Recursively build a span query for a specific field."""
        if not tokens:
            return None
            
        # 1. Handle wrapping parentheses
        while len(tokens) >= 2 and tokens[0] == '(' and tokens[-1] == ')':
            level = 0
            is_wrapped = True
            for i, token in enumerate(tokens):
                if token == '(':
                    level += 1
                elif token == ')':
                    level -= 1
                    if level == 0 and i < len(tokens) - 1:
                        is_wrapped = False
                        break
            if is_wrapped:
                tokens = tokens[1:-1]
            else:
                break
                
        if not tokens:
            return None

        # 2. Split by OR (lowest precedence)
        or_indices = []
        level = 0
        for i, token in enumerate(tokens):
            if token == '(':
                level += 1
            elif token == ')':
                level -= 1
            elif token.upper() == 'OR' and level == 0:
                or_indices.append(i)
                
        if or_indices:
            clauses = []
            start = 0
            for idx in or_indices + [len(tokens)]:
                segment = tokens[start:idx]
                if segment:
                    clause = self._build_span_query(segment, field)
                    if clause:
                        clauses.append(clause)
                start = idx + 1
            
            if not clauses:
                return None
            if len(clauses) == 1:
                return clauses[0]
            return {"span_or": {"clauses": clauses}}

        # 3. Simple AND / Implicit AND (treated as unordered near with slop 0? or just multiple terms?)
        # Span queries don't have direct AND. We used nested span_near with slop=0?
        # Or just treat as phrase?
        # If we have "A B", usually means phrase "A B".
        # If we have "A AND B", it means A and B anywhere? Hard in span.
        # For now, treat multiple tokens as a sequence (phrase) if they are adjacent.
        # If explicit AND, maybe fallback to unordered near with large slop? 
        # Let's assume user uses NEAR for proximity and OR for choices.
        # Implicit AND of "A B" -> span_near([A, B], slop=0, in_order=True) (Phrase)
        
        # Let's handle generic tokens
        # If explicit AND is present, it's problematic for pure SPAN.
        # We will treat "A AND B" effectively as "A B" in span context (phrase-like or near-0)
        # But usually inside NEAR it's just "term OR term".
        
        # Process remaining tokens as a phrase if multiple
        if len(tokens) > 1:
            # Check for explicit operators strictly
            # If there are AND/NOT/NEAR inside, we might struggle.
            # But assuming leaf node is terms.
            sub_spans = []
            for token in tokens:
                if token.upper() in ['AND', 'NOT', 'OR']: 
                    continue # Skip operators if malformed
                # Handle nested NEAR?
                if token.upper().startswith('NEAR/'):
                    continue # Skip nested operators for now or handle them?
                
                # Create span term
                # clean token
                t = token.replace('"', '').replace("'", "")
                # Handle ++ (case sensitive)
                if t.startswith('++'):
                    t = t[2:]
                    # For ++, maybe keep case? But if index is standard, we must lowercase.
                    # Unless user explicitly wants case sensitive (which requires keyword field usually).
                    # esPreview targets text fields. So lowercase everything.
                    t = t.lower()
                elif t.startswith('+'):
                    t = t[1:].lower()
                else:
                    t = t.lower()
                    
                sub_spans.append({"span_term": {field: t}})
            
            if not sub_spans:
                return None
            if len(sub_spans) == 1:
                return sub_spans[0]
            # Phrase
            return {"span_near": {"clauses": sub_spans, "slop": 0, "in_order": True}}
            
        # Single token
        token = tokens[0]
        t = token.replace('"', '').replace("'", "")
        if t.startswith('++'):
            t = t[2:].lower()
        elif t.startswith('+'):
            t = t[1:].lower()
        else:
            t = t.lower()
        return {"span_term": {field: t}}

        
    def _tokenize_with_quotes(self, text: str) -> List[str]:
        """Tokenize text while preserving quoted phrases and parentheses structure."""
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
            
            # Handle parentheses - treat them as separate tokens
            if text[i] in ['(', ')']:
                tokens.append(text[i])
                i += 1
                continue
            
            # Handle NEAR operator (NEAR/n)
            near_match = re.match(r'^NEAR/(\d+)', text[i:], re.IGNORECASE)
            if near_match:
                start_ok = (i == 0 or text[i-1].isspace() or text[i-1] in ['(', ')'])
                if start_ok:
                    op_str = near_match.group(0)
                    tokens.append(op_str)
                    i += len(op_str)
                    continue

            # Handle operators (AND, OR, NOT) - case insensitive matching
            # Only treat as operators if they are surrounded by whitespace or parentheses/quotes
            is_operator = False
            for op in ['AND', 'OR', 'NOT']:
                op_len = len(op)
                if i + op_len <= text_len and text[i:i+op_len].upper() == op:
                    # Check boundaries
                    start_ok = (i == 0 or text[i-1].isspace() or text[i-1] in ['(', ')'])
                    end_ok = (i + op_len >= text_len or text[i+op_len].isspace() or text[i+op_len] in ['(', ')', '"'])
                    
                    if start_ok and end_ok:
                        tokens.append(op)
                        i += op_len
                        is_operator = True
                        break
            
            if is_operator:
                continue
            
            # Collect regular word/token
            start = i
            while i < text_len:
                char = text[i]
                if char.isspace():
                    break
                if char in ['(', ')', '"']:
                    break
                
                # Check if we've hit an operator
                is_op_start = False
                for op in ['AND', 'OR', 'NOT']:
                    op_len = len(op)
                    if i + op_len <= text_len and text[i:i+op_len].upper() == op:
                        # Check end boundary only (start boundary is implied by being in this loop)
                        # Actually, for a word like "LAND", "AND" is inside but not an operator.
                        # Operators must be distinct words.
                        # So we only stop if we see a clear operator boundary.
                        # But here we are consuming a word. If we see " AND ", that's a stop.
                        pass 
                
                i += 1
            
            if i > start:
                token = text[start:i]
                if token:
                    tokens.append(token)
        
        return tokens

    def _normalize_tokens(self, tokens: List[str]) -> List[str]:
        """Clean tokens by stripping leading +/++ markers and dropping empty artifacts."""
        normalized = []
        for token in tokens:
            # Drop standalone plus tokens
            if token in ('+', '++'):
                continue

            stripped = token
            if token.startswith('++'):
                stripped = token[2:]
            elif token.startswith('+'):
                stripped = token[1:]

            stripped = stripped.strip()
            if stripped:
                normalized.append(stripped)

        return normalized
    
    def _build_dsl_query(self, parts: List[Dict[str, Any]], target_fields: List[str]) -> Dict[str, Any]:
        """Build Elasticsearch DSL query from parsed parts."""
        if len(parts) == 1:
            return self._create_field_query(parts[0]["query"], target_fields)
        
        bool_clauses = {"must": [], "should": [], "must_not": []}
        
        for part in parts:
            field_query = self._create_field_query(part["query"], target_fields)
            bool_clauses[part["type"]].append(field_query)
        
        return {"bool": {k: v for k, v in bool_clauses.items() if v}}
    
    def _create_field_query(self, query_text: str, target_fields: List[str]) -> Dict[str, Any]:
        """Create a multi-field query for the given text.
        
        Uses match_phrase for quoted phrases to ensure exact phrase matching,
        otherwise uses match/multi_match for term-based matching.
        """
        query_text = query_text.strip()
        
        # Check if this is a quoted phrase (exact phrase match required)
        is_quoted = query_text.startswith('"') and query_text.endswith('"')
        if is_quoted:
            # Remove quotes and use match_phrase for exact phrase matching
            query_text = query_text.strip('"')
            
            # Use strict phrase matching similar to percolator queries
            # match_phrase requires ALL terms to be present in exact order
            # Add zero_terms_query to prevent matching when no terms are found
            if len(target_fields) == 1:
                return {
                    "match_phrase": {
                        target_fields[0]: {
                            "query": query_text,
                            "slop": 0,  # No word reordering allowed
                            "zero_terms_query": "none"  # Don't match if analyzer removes all terms
                        }
                    }
                }
            
            # For multiple fields, use bool with should clauses for each field
            # Each field must match the complete phrase
            return {
                "bool": {
                    "should": [
                        {
                            "match_phrase": {
                                field: {
                                    "query": query_text,
                                    "slop": 0,  # No word reordering allowed
                                    "zero_terms_query": "none"  # Don't match if analyzer removes all terms
                                }
                            }
                        }
                        for field in target_fields
                    ],
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
        # This is a simplified implementation
        # In production, you'd want to recursively traverse the query structure
        query_str = json.dumps(query)
        
        # Map content field to the first actual field (usually headlines)
        if len(actual_fields) > 0:
            query_str = query_str.replace('"content"', f'"{actual_fields[0]}"')
        
        # Map other logical fields
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
    
    def execute_search(self, query: Dict[str, Any], indexes: Optional[List[str]] = None, include_content: bool = False) -> Dict[str, Any]:
        """Execute search against specified indexes."""
        if not indexes:
            indexes = self.config.target_indexes
        
        self.logger.info(f"Executing parallel search against {len(indexes)} indexes: {indexes}")
        
        if self.config.parallel_search and len(indexes) > 1:
            return self._execute_parallel_search(query, indexes, include_content)
        else:
            return self._execute_sequential_search(query, indexes, include_content)
    
    def _execute_parallel_search(self, query: Dict[str, Any], indexes: List[str], include_content: bool = False) -> Dict[str, Any]:
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
    
    def _execute_sequential_search(self, query: Dict[str, Any], indexes: List[str], include_content: bool = False) -> Dict[str, Any]:
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
    
    def _search_single_index(self, query: Dict[str, Any], index: str, include_content: bool = False) -> Dict[str, Any]:
        """Execute search against a single index."""
        start_time = time.time()
        
        self.logger.info(f"Executing search against index: {index}")
        
        # Map query to index-specific fields
        mapped_query = self.field_mapper.map_query_to_article_fields(query)
        index_query = mapped_query.get(index, query)
        
        # Build search request
        if include_content:
            # Include additional fields based on index type
            source_fields = ["_id", "headlines", "summary", "text", "articleData.headlines", "articleData.summary", "articleData.text", "feedData.headlines", "feedData.summary", "feedData.text"]
            
            # Add article-specific fields for printarticleindex
            if index == "printarticleindex":
                source_fields.extend(["articleInfo.articleDate", "uploadInfo.imageId"])
            
            # Add socialFeed-specific fields for socialfeedindex
            if index == "socialfeedindex":
                source_fields.extend(["feedData.feedDate", "feedData.feedDateTime", "feedData.articleDateNumber", "feedInfo.link"])
            
            search_request = {
                "query": index_query,
                "size": self.config.max_results_per_index,
                "_source": source_fields
            }
        else:
            search_request = {
                "query": index_query,
                "size": self.config.max_results_per_index,
                "_source": ["_id"]
            }
            
        # Add sort by date descending based on index type
        if index == "printarticleindex":
            search_request["sort"] = [
                {"articleInfo.articleDate": {"order": "desc", "unmapped_type": "date", "missing": "_last"}},
                {"_id": {"order": "desc"}}
            ]
        elif index == "socialfeedindex":
            # Prefer the most precise timestamp first, then date buckets, then deterministic id order.
            search_request["sort"] = [
                {"feedData.feedDateTime": {"order": "desc", "unmapped_type": "date", "missing": "_last"}},
                {"feedData.feedDate": {"order": "desc", "unmapped_type": "date", "missing": "_last"}},
                {"feedData.articleDateNumber": {"order": "desc", "unmapped_type": "long", "missing": "_last"}},
                {"_id": {"order": "desc"}}
            ]
        
        # Add highlighting if enabled
        if self.config.enable_highlighting:
            search_request["highlight"] = {
                "fields": {
                    "*": {
                        "fragment_size": 150,
                        "number_of_fragments": 3
                    }
                }
            }
        
        try:
            response = self.es_client.search(index=index, body=search_request)
            
            # Process results
            hits = response.get('hits', {}).get('hits', [])
            total_hits = response.get('hits', {}).get('total', {}).get('value', 0)
            
            articles = []
            for hit in hits:
                article_id = hit['_id']
                
                if include_content:
                    source = hit.get('_source', {})
                    # Extract article content from different possible field structures
                    article_content = {
                        "id": article_id,
                        "headlines": source.get('headlines') or source.get('articleData', {}).get('headlines') or source.get('feedData', {}).get('headlines'),
                        "summary": source.get('summary') or source.get('articleData', {}).get('summary') or source.get('feedData', {}).get('summary'),
                        "text": source.get('text') or source.get('articleData', {}).get('text') or source.get('feedData', {}).get('text')
                    }
                    
                    # Add article-specific fields for printarticleindex
                    if index == "printarticleindex":
                        article_content["articleDate"] = source.get('articleInfo', {}).get('articleDate')
                        article_content["imageId"] = source.get('uploadInfo', {}).get('imageId')
                    
                    # Add socialFeed-specific fields for socialfeedindex
                    if index == "socialfeedindex":
                        article_content["feedDate"] = source.get('feedData', {}).get('feedDate')
                        article_content["feedDateTime"] = source.get('feedData', {}).get('feedDateTime')
                        article_content["articleDateNumber"] = source.get('feedData', {}).get('articleDateNumber')
                        article_content["links"] = source.get('feedInfo', {}).get('link')
                    
                    articles.append(article_content)
                else:
                    # Just return the ID for faster performance
                    articles.append({"id": article_id})
            
            execution_time = int((time.time() - start_time) * 1000)
            
            self.logger.info(f"Processed {len(articles)} articles from index {index}")
            self.logger.info(f"Search completed for index {index}: {total_hits} hits in {execution_time}ms")
            
            return {
                "total_hits": total_hits,
                "articles": articles,
                "execution_time_ms": execution_time,
                "highlights": {hit['_id']: hit.get('highlight', {}) for hit in hits},
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
    
    def execute_query(self, user_input: str, indexes: Optional[List[str]] = None, include_content: bool = False) -> ESPreviewResult:
        """Execute a query from user input."""
        start_time = time.time()
        
        self.logger.info(f"Executing query against indexes: {indexes or self.config.target_indexes}")
        
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
            else:  # DSL JSON
                query = json.loads(user_input)
            
            self.logger.info(f"Processed query - Format: {input_format}")
            
            # Execute search
            search_results = self.search_engine.execute_search(query, indexes, include_content)
            
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
    
    def execute_company_query(self, company_id: str, language: str = "en", indexes: Optional[List[str]] = None, include_content: bool = False) -> ESPreviewResult:
        """Execute a stored company query."""
        start_time = time.time()
        
        self.logger.info(f"Executing company query for {company_id} (lang: {language}) against indexes: {indexes or self.config.target_indexes}")
        
        try:
            # Retrieve company query
            company_query, company_info = self.company_retriever.get_company_query(company_id, language)
            
            self.logger.info(f"Retrieved query for company: {company_info['companyName']} ({company_id})")
            
            # The company_query is already in DSL format, so we can use it directly
            # No need to convert from boolean string since it's already DSL
            
            # Execute search
            search_results = self.search_engine.execute_search(company_query, indexes, include_content)
            
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
        index_results = {}
        
        for index_name, index_data in search_results.items():
            # Create IndexResult
            index_result = IndexResult(
                total_hits=index_data.get("total_hits", 0),
                article_ids=[article.get("id") for article in index_data.get("articles", [])],
                execution_time_ms=index_data.get("execution_time_ms", 0),
                articles=index_data.get("articles", []),  # Include full article content
                errors=index_data.get("errors", [])
            )
            
            # Process highlights if available
            highlights = index_data.get("highlights", {})
            for article_id, article_highlights in highlights.items():
                if article_id in index_result.article_ids:
                    field_matches = FieldMatches(
                        matched_fields=list(article_highlights.keys()),
                        score=1.0,  # Default score
                        highlights=article_highlights
                    )
                    index_result.field_matches[article_id] = field_matches
            
            index_results[index_name] = index_result
            # total_matches should represent full match volume (not just returned page size)
            total_matches += int(index_result.total_hits or 0)
        
        execution_time = int((time.time() - start_time) * 1000)
        
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


