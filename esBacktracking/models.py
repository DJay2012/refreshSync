"""
Data models for esPreview system.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any

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
















