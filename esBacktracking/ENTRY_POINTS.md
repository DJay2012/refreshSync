# esPreview Simplified - Entry Points Guide

## 🚀 Quick Start Entry Points

The simplified esPreview version provides multiple entry points for different use cases:

### 1. **Main Entry Point** - `main.py`
**Best for**: Comprehensive testing with hardcoded company IDs and queries

```bash
# Run full test suite
python main.py

# Run quick test
python main.py --quick
```

**Features:**
- Hardcoded company IDs for testing
- Hardcoded boolean queries for testing
- Health check
- Custom testing section
- Comprehensive results display

### 2. **Custom Template** - `my_tests.py`
**Best for**: Your personalized testing with your specific company IDs and queries

```bash
python my_tests.py
```

**Features:**
- Easy to customize with your company IDs
- Easy to customize with your boolean queries
- Clean, focused output
- Perfect for daily testing

### 3. **CLI Interface** - `cli.py`
**Best for**: Interactive use and one-off queries

```bash
# Health check
python cli.py --health

# Boolean queries
python cli.py "technology AND innovation" --format summary

# Company queries
python cli.py --company "CYBERPE865" --format ids

# Interactive mode
python cli.py --interactive
```

### 4. **Programmatic Usage**
**Best for**: Integration into your own scripts

```python
from esPreview_simplified import ESPreviewEngine, ESPreviewConfig

config = ESPreviewConfig.from_env()
engine = ESPreviewEngine(config)

# Execute queries
result = engine.execute_query("your query here")
result = engine.execute_company_query("COMPANY_ID")
```

## 📝 Customization Guide

### Customizing `my_tests.py` (Recommended)

1. **Edit Company IDs**:
```python
my_company_ids = [
    "CYBERPE865",  # CyberPeace Foundation
    "HUL",         # Hindustan Unilever
    "TATA",        # Tata Group
    # Add your company IDs here...
]
```

2. **Edit Boolean Queries**:
```python
my_boolean_queries = [
    "technology AND innovation",
    "Major Vineet Kumar AND CyberPeace Foundation",
    "Vineet Kumar AND CyberPeace AND Key Initiatives",
    "CyberQuest 2025",
    # Add your boolean queries here...
]
```

3. **Run Your Tests**:
```bash
python my_tests.py
```

### Customizing `main.py`

The `main.py` file has the same structure but with more comprehensive testing. Edit the hardcoded lists:

```python
# Company IDs section
company_ids = [
    "CYBERPE865",
    "HUL",
    # Add more...
]

# Boolean queries section
boolean_queries = [
    "technology AND innovation",
    "Major Vineet Kumar AND CyberPeace Foundation",
    # Add more...
]
```

## 🎯 Use Cases

### Daily Testing
```bash
python my_tests.py
```
- Quick, focused results
- Your specific company IDs and queries
- Clean output format

### Comprehensive Testing
```bash
python main.py
```
- Full test suite
- Multiple scenarios
- Detailed results

### Interactive Exploration
```bash
python cli.py --interactive
```
- Test queries on the fly
- Explore different formats
- Experiment with new queries

### Integration
```python
from esPreview_simplified import ESPreviewEngine, ESPreviewConfig
# Use in your own scripts
```

## 📊 Output Examples

### Company Query Results
```
Testing: CYBERPE865
   Success: 2 matches in 1401ms
   - socialfeedindex: 2 articles
     IDs: 18203226565, 18201914578
```

### Boolean Query Results
```
Testing: technology AND innovation
   Success: 100 matches in 594ms
   - printarticleindex: 50 articles
     IDs: 84667161, 84780522, 84467705
   - socialfeedindex: 50 articles
     IDs: 18201917649, 18217113496, 18212613620
```

## 🔧 Quick Setup

1. **Copy the template**:
```bash
cp my_tests.py my_custom_tests.py
```

2. **Edit your company IDs and queries** in `my_custom_tests.py`

3. **Run your tests**:
```bash
python my_custom_tests.py
```

## 💡 Pro Tips

1. **Start with `my_tests.py`** - It's the easiest to customize
2. **Use `--quick` flag** with `main.py` for faster testing
3. **Use interactive mode** for exploring new queries
4. **Save results to files** using `--output` flag with CLI
5. **Test different languages** by modifying the language parameter

## 🚀 Ready to Use!

Choose the entry point that best fits your needs:
- **Daily testing**: `my_tests.py`
- **Comprehensive testing**: `main.py`
- **Interactive use**: `cli.py --interactive`
- **Integration**: Import the modules directly

All entry points maintain the same functionality and performance as the original complex version!
















