"""
Configuration management for esPreview system.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

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
    
    @classmethod
    def from_file(cls, config_path: str) -> 'ESPreviewConfig':
        """Create configuration from JSON configuration file."""
        config_file = Path(config_path)
        
        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        try:
            with open(config_file, 'r') as f:
                config_data = json.load(f)
            
            return cls.from_dict(config_data)
            
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in configuration file {config_path}: {str(e)}")
        except Exception as e:
            raise ValueError(f"Error loading configuration file {config_path}: {str(e)}")
    
    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'ESPreviewConfig':
        """Create configuration from dictionary."""
        return cls(**config_dict)
    
    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []
        
        if not self.target_indexes:
            errors.append("No target indexes configured")
        
        if not self.search_fields:
            errors.append("No search fields configured")
        
        if self.max_results_per_index <= 0:
            errors.append("max_results_per_index must be positive")
        
        if self.timeout_seconds <= 0:
            errors.append("timeout_seconds must be positive")
        
        if not self.es_host:
            errors.append("ES host not configured")
        
        return errors
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "max_results_per_index": self.max_results_per_index,
            "timeout_seconds": self.timeout_seconds,
            "enable_highlighting": self.enable_highlighting,
            "target_indexes": self.target_indexes,
            "search_fields": self.search_fields,
            "index_field_mappings": self.index_field_mappings,
            "es_host": self.es_host,
            "es_user": self.es_user,
            "es_password": "***" if self.es_password else "",
            "percolator_index": self.percolator_index,
            "log_level": self.log_level,
            "parallel_search": self.parallel_search,
            "connection_pool_size": self.connection_pool_size,
            "retry_attempts": self.retry_attempts,
            "retry_delay": self.retry_delay
        }
    
    def save_to_file(self, config_path: str):
        """Save configuration to JSON file."""
        config_file = Path(config_path)
        config_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(config_file, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
















