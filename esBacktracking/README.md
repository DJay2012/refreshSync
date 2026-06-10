# esPreview - Simplified Elasticsearch Query Preview System

A consolidated reverse tagging system that allows users to test Elasticsearch DSL boolean queries against article data from both print articles and social feed content.

## 🚀 What's New in Simplified Version

This simplified version reduces the project complexity from **20+ files** to just **5 core files** while maintaining all functionality:

- **espreview.py** - Main engine with all core functionality merged
- **cli.py** - Command-line interface
- **config.py** - Configuration management
- **models.py** - Data models
- **__init__.py** - Package initialization

## ✨ Features

- **Boolean Query Processing**: Execute complex boolean queries with AND, OR, NOT operators
- **Company Stored Queries**: Retrieve and execute pre-stored boolean queries by company ID
- **Multi-Index Search**: Search across multiple Elasticsearch indexes simultaneously
- **Multi-Language Support**: Support for 16+ languages in company queries
- **Multiple Output Formats**: JSON, table, summary, and ID-only formats
- **Interactive Mode**: Test queries interactively
- **Health Monitoring**: System health checks and diagnostics

## 📦 Installation

1. Ensure you have Python 3.7+ installed
2. Install required dependencies:
   ```bash
   pip install elasticsearch python-dotenv psycopg2 pymongo
   ```
3. Set up environment variables in `.env` file:
   ```bash
   ES_HOST=https://your-elasticsearch-host:9200
   ES_USER=your-username
   ES_PASSWORD=your-password
   ES_INDEX_NAME=your-percolator-index-name
   ```

## 🚀 Quick Start

### 1. Health Check
```bash
python -m esPreview_simplified.cli --health
```

### 2. Simple Boolean Query
```bash
python -m esPreview_simplified.cli "technology AND innovation" --format summary
```

### 3. Company Query
```bash
python -m esPreview_simplified.cli --company "CYBERPE865" --format ids
```

## 📖 Usage Examples

### Boolean Queries
```bash
# Simple term search
python -m esPreview_simplified.cli "technology"

# Boolean operators
python -m esPreview_simplified.cli "health AND wellness"
python -m esPreview_simplified.cli "startup OR entrepreneur"
python -m esPreview_simplified.cli "technology AND NOT failure"

# Quoted phrases
python -m esPreview_simplified.cli '"machine learning" OR "artificial intelligence"'

# Complex boolean expressions
python -m esPreview_simplified.cli "(technology OR innovation) AND (startup OR entrepreneur)"
```

### Company Stored Queries
```bash
# Execute company query (defaults to English)
python -m esPreview_simplified.cli --company "CYBERPE865"

# Specify language
python -m esPreview_simplified.cli --company "CYBERPE865" --language hi

# List available companies
python -m esPreview_simplified.cli --list-companies --limit 20

# Search for companies
python -m esPreview_simplified.cli --search-companies "CYBER"
```

### Interactive Mode
```bash
# Start interactive session
python -m esPreview_simplified.cli --interactive

# In interactive mode, you can:
# - Enter queries: technology AND innovation
# - Use commands: :help, :config, :quit
# - Test different queries without restarting
```

### Output Formats

#### Summary Format (Human-readable)
```bash
python -m esPreview_simplified.cli "technology" --format summary
```
Output:
```
Query Results Summary
Execution Time: 1765ms
Total Matches: 10

Index: printarticleindex
  Matches: 10000
  Articles: 5
  Sample IDs: 89280624, 89840977, 86648027, 86648088, 87673514

Index: socialfeedindex
  Matches: 10000
  Articles: 5
  Sample IDs: 18199423162, 18207675630, 18221545791, 18211681723, 18221552938
```

#### Table Format (Compact)
```bash
python -m esPreview_simplified.cli "technology" --format table
```

#### JSON Format (Detailed)
```bash
python -m esPreview_simplified.cli "technology" --format json --pretty
```

#### IDs Format (Article IDs Only)
```bash
python -m esPreview_simplified.cli --company "CYBERPE865" --format ids
```

## ⚙️ Configuration

### Environment Variables
```bash
# Elasticsearch settings
export ES_HOST=https://your-elasticsearch-host:9200
export ES_USER=your-username
export ES_PASSWORD=your-password
export ES_INDEX_NAME=your-percolator-index

# esPreview settings
export ESPREVIEW_MAX_RESULTS=100
export ESPREVIEW_TIMEOUT=60
export ESPREVIEW_HIGHLIGHTING=true
export ESPREVIEW_PARALLEL=true
export LOG_LEVEL=DEBUG
```

### Command-line Options
```bash
# Specify target indexes
python -m esPreview_simplified.cli "technology" --indexes printarticleindex

# Set result limit per index
python -m esPreview_simplified.cli "health" --limit 25

# Set query timeout
python -m esPreview_simplified.cli "innovation" --timeout 60

# Save results to file
python -m esPreview_simplified.cli "startup" --output results.json

# Verbose logging
python -m esPreview_simplified.cli "tech" --verbose
```

## 🔧 Programmatic Usage

### Basic Usage
```python
from esPreview_simplified import ESPreviewEngine, ESPreviewConfig

# Initialize
config = ESPreviewConfig.from_env()
engine = ESPreviewEngine(config)

# Execute boolean query
result = engine.execute_query("technology AND innovation")
print(f"Found {result.total_matches} matches")

# Execute company query
result = engine.execute_company_query("CYBERPE865", language="en")
for index_name, index_result in result.index_results.items():
    print(f"{index_name}: {len(index_result.article_ids)} articles")

# List companies
companies = engine.list_companies(limit=50)
for company in companies:
    print(f"{company['companyId']}: {company['companyName']}")
```

### Main Entry Point for Hardcoded Testing
```bash
# Run full test suite with hardcoded company IDs and queries
python main.py

# Run quick test
python main.py --quick
```

The `main.py` file allows you to hardcode company IDs and boolean queries for quick testing:

```python
# In main.py - customize these lists
company_ids = [
    "CYBERPE865",  # CyberPeace Foundation
    "HUL",         # Hindustan Unilever
    "TATA",        # Tata Group
    # Add your company IDs here...
]

boolean_queries = [
    "technology AND innovation",
    "Major Vineet Kumar AND CyberPeace Foundation",
    "CyberQuest 2025",
    # Add your boolean queries here...
]
```

## 🏗️ Architecture

### Simplified File Structure
```
esPreview_simplified/
├── __init__.py          # Package initialization and exports
├── espreview.py         # Main engine with all core functionality
├── cli.py              # Command-line interface
├── config.py           # Configuration management
├── models.py           # Data models
└── README.md           # This documentation
```

### Core Components (All in espreview.py)
- **ESPreviewEngine**: Main orchestrator
- **SearchEngine**: Elasticsearch search operations
- **CompanyQueryRetriever**: Company query management
- **BooleanToDSLConverter**: Query conversion
- **InputFormatDetector**: Input format detection
- **FieldMapper**: Field mapping utilities
- **Logger**: Logging utilities

## 🔄 Migration from Complex Version

If you're migrating from the complex version:

1. **Replace imports**:
   ```python
   # Old
   from esPreview.engine import ESPreviewEngine
   from esPreview.config import ESPreviewConfig
   
   # New
   from esPreview_simplified import ESPreviewEngine, ESPreviewConfig
   ```

2. **Update CLI usage**:
   ```bash
   # Old
   python -m esPreview.cli "query"
   
   # New
   python -m esPreview_simplified.cli "query"
   ```

3. **Configuration remains the same** - all environment variables and settings work identically

## 🐛 Troubleshooting

### Common Issues

1. **Connection Errors**
   ```bash
   # Check system health
   python -m esPreview_simplified.cli --health
   ```

2. **No Results Found**
   ```bash
   # Test with simpler query
   python -m esPreview_simplified.cli "technology" --format summary
   ```

3. **Query Syntax Errors**
   ```bash
   # Use interactive mode to test queries
   python -m esPreview_simplified.cli --interactive
   ```

## 📊 Performance

The simplified version maintains the same performance characteristics as the original:
- **Parallel search** across multiple indexes
- **Connection pooling** for Elasticsearch
- **Configurable timeouts** and retry logic
- **Efficient result processing**

## 🤝 Contributing

The simplified structure makes it easier to:
- Understand the codebase
- Add new features
- Debug issues
- Maintain the system

## 📄 License

This project is part of the PNQ ETL Server system for article tagging and analysis.

## 🔗 Related

- Original complex version: `esPreview/`
- Main project: `elasticTagging/`
- Documentation: See individual component files for detailed API documentation
