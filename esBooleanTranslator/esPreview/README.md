# esPreview - Simplified Elasticsearch Query Preview System

A consolidated reverse tagging system that allows users to test Elasticsearch DSL boolean queries against article data from both print articles and social feed content.

## 📁 Directory Structure

```
esPreview/
├── __init__.py          # Package initialization and exports
├── espreview.py         # Main engine with all core functionality
├── cli.py              # Command-line interface
├── main.py             # Main entry point for testing
├── example_usage.py    # Example usage templates
├── my_tests.py         # Customizable test template
└── test_simplified.py  # Simplified test suite
```

## 🚀 Quick Start

### 1. Health Check
```bash
python -m esPreview.cli --health
```

### 2. Simple Boolean Query
```bash
python -m esPreview.cli "technology AND innovation" --format summary
```

### 3. Company Query
```bash
python -m esPreview.cli --company "CYBERPE865" --format ids
```

## 📖 Usage Examples

### Boolean Queries
```bash
# Simple term search
python -m esPreview.cli "technology"

# Boolean operators
python -m esPreview.cli "health AND wellness"
python -m esPreview.cli "startup OR entrepreneur"
python -m esPreview.cli "technology AND NOT failure"

# Quoted phrases
python -m esPreview.cli '"machine learning" OR "artificial intelligence"'

# Complex boolean expressions
python -m esPreview.cli "(technology OR innovation) AND (startup OR entrepreneur)"
```

### Company Queries
```bash
# Execute company query (defaults to English)
python -m esPreview.cli --company "CYBERPE865"

# Specify language
python -m esPreview.cli --company "CYBERPE865" --language hi

# List available companies
python -m esPreview.cli --list-companies --limit 20

# Search for companies
python -m esPreview.cli --search-companies "CYBER"
```

### Interactive Mode
```bash
# Start interactive session
python -m esPreview.cli --interactive

# In interactive mode, you can:
# - Enter queries directly
# - Switch between boolean and company queries
# - Change output formats on the fly
# - Exit with 'quit' or 'exit'
```

## 📊 Output Formats

#### Summary Format (Human-readable)
```bash
python -m esPreview.cli "technology" --format summary
```

#### Table Format (Compact)
```bash
python -m esPreview.cli "technology" --format table
```

#### JSON Format (Detailed)
```bash
python -m esPreview.cli "technology" --format json --pretty
```

#### IDs Format (Article IDs Only)
```bash
python -m esPreview.cli --company "CYBERPE865" --format ids
```

## ⚙️ Configuration

Set environment variables in your `.env` file:

```bash
# Elasticsearch connection
export ES_HOST=http://localhost:9200
export ES_USER=elastic
export ES_PASSWORD=your-password
export ES_INDEX_NAME=your-percolator-index

# esPreview settings
export ESPREVIEW_MAX_RESULTS=100
export ESPREVIEW_TIMEOUT=60
export ESPREVIEW_HIGHLIGHTING=true
export ESPREVIEW_PARALLEL=true
export LOG_LEVEL=DEBUG
```

## 🔧 Advanced Options

```bash
# Specify target indexes
python -m esPreview.cli "technology" --indexes printarticleindex

# Set result limit per index
python -m esPreview.cli "health" --limit 25

# Set query timeout
python -m esPreview.cli "innovation" --timeout 60

# Save results to file
python -m esPreview.cli "startup" --output results.json

# Verbose logging
python -m esPreview.cli "tech" --verbose
```

## 💻 Programmatic Usage

### Basic Usage
```python
from esPreview import ESPreviewEngine, ESPreviewConfig

# Initialize
config = ESPreviewConfig.from_env()
engine = ESPreviewEngine(config)

# Execute boolean query
result = engine.execute_query("technology AND innovation")

# Execute company query
result = engine.execute_company_query("CYBERPE865", language="en")

# Process results
print(f"Total matches: {result.total_matches}")
for index_name, index_result in result.index_results.items():
    print(f"{index_name}: {index_result.total_hits} hits")
    for article_id in index_result.article_ids:
        print(f"  - {article_id}")
```

### Advanced Usage
```python
from esPreview import ESPreviewEngine, ESPreviewConfig, ESPreviewResult

# Custom configuration
config = ESPreviewConfig(
    max_results_per_index=100,
    timeout_seconds=60,
    enable_highlighting=True,
    target_indexes=["printarticleindex", "socialfeedindex"]
)

# Initialize engine
engine = ESPreviewEngine(config)

# Health check
health = engine.health_check()
print(f"System status: {health['status']}")

# List companies
companies = engine.list_companies(limit=50)
for company in companies:
    print(f"{company['companyId']}: {company['companyName']}")

# Search companies
results = engine.search_companies("CYBER", limit=10)
for company in results:
    print(f"{company['companyId']}: {company['companyName']} (score: {company['score']})")
```

## 🧪 Testing

### Run Main Test Suite
```bash
python -m esPreview.main
```

### Run Custom Tests
```bash
python -m esPreview.my_tests
```

### Run Example Usage
```bash
python -m esPreview.example_usage
```

### Run Simplified Tests
```bash
python -m esPreview.test_simplified
```

## 📚 API Reference

### ESPreviewEngine

Main orchestrator for esPreview query execution and result processing.

#### Methods

- `execute_query(user_input: str, indexes: Optional[List[str]] = None) -> ESPreviewResult`
  - Execute a query from user input (boolean string or DSL JSON)
  
- `execute_company_query(company_id: str, language: str = "en", indexes: Optional[List[str]] = None) -> ESPreviewResult`
  - Execute a stored company query
  
- `health_check() -> Dict[str, Any]`
  - Perform system health check
  
- `list_companies(limit: int = 100) -> List[Dict[str, Any]]`
  - List available companies
  
- `search_companies(search_term: str, limit: int = 20) -> List[Dict[str, Any]]`
  - Search for companies by name or ID

### ESPreviewConfig

Configuration class for esPreview system.

#### Properties

- `max_results_per_index`: Maximum results to return per index (default: 50)
- `timeout_seconds`: Query timeout in seconds (default: 30)
- `enable_highlighting`: Enable search result highlighting (default: True)
- `target_indexes`: List of target indexes to search (default: ["printarticleindex", "socialfeedindex"])
- `search_fields`: List of fields to search (default: ["headlines", "summary", "text"])
- `parallel_search`: Enable parallel search across indexes (default: True)

### ESPreviewResult

Main result container for esPreview queries.

#### Properties

- `success`: Whether the query execution was successful
- `total_matches`: Total number of matching articles
- `execution_time_ms`: Query execution time in milliseconds
- `query_info`: Query information dictionary
- `index_results`: Dictionary of IndexResult objects per index
- `errors`: List of error messages

## 🔗 Related Files

- Entry points guide: `ENTRY_POINTS.md`
- Migration guide: `MIGRATION_GUIDE.md`
- Main README: `README.md`
