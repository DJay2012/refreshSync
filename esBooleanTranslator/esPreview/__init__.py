"""
esPreview - Simplified Elasticsearch Query Preview System

A consolidated reverse tagging system that allows users to test Elasticsearch DSL boolean queries
against article data from both print articles and social feed content.
"""

from .espreview import (
    ESPreviewEngine,
    ESPreviewConfig,
    ESPreviewResult,
    IndexResult,
    FieldMatches,
    ESPreviewError,
    QueryValidationError,
)

__all__ = [
    'ESPreviewEngine',
    'ESPreviewConfig',
    'ESPreviewResult',
    'IndexResult',
    'FieldMatches',
    'ESPreviewError',
    'QueryValidationError',
]

__version__ = '1.0.0'



