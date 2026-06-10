"""
esPreview - Simplified Elasticsearch Query Preview System

A consolidated reverse tagging system that allows users to test Elasticsearch DSL boolean queries
against article data from both print articles and social feed content.

This simplified version merges all core functionality into fewer files while maintaining
the same capabilities as the original complex structure.
"""

__version__ = "2.0.0"
__author__ = "esPreview Team"

from esPreview import ESPreviewEngine, ESPreviewConfig, ESPreviewResult, IndexResult, FieldMatches

__all__ = [
    "ESPreviewEngine",
    "ESPreviewConfig", 
    "ESPreviewResult",
    "IndexResult",
    "FieldMatches"
]











