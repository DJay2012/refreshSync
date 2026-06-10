# ES Boolean Translator API

A comprehensive FastAPI-based system for managing boolean query translations and Elasticsearch operations. This system provides APIs for uploading, managing, and testing boolean queries across multiple languages.

## 🚀 Features

### Core API Features
- **File Upload**: Upload JSON files containing boolean query translations
- **CRUD Operations**: Create, read, update, and delete company boolean queries
- **Health Monitoring**: Check API and Elasticsearch connection status
- **Multi-language Support**: Handle translations in multiple languages

### ES Preview System
- **Query Testing**: Test boolean queries against Elasticsearch indexes
- **Company Queries**: Execute stored queries for specific companies
- **Real-time Results**: Get immediate feedback on query performance
- **Multi-index Search**: Search across multiple Elasticsearch indexes

## 📋 Requirements

- Python 3.8+
- Elasticsearch 7.17+
- FastAPI
- Uvicorn

## 🛠️ Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd esBooleanTranslator
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Environment Setup**
   ```bash
   cp env.example .env
   # Edit .env with your Elasticsearch credentials
   ```

4. **Elasticsearch Configuration**
   Update `src/utils/Config.py` with your Elasticsearch connection details:
   ```python
   es = Elasticsearch(
       hosts=["https://your-elasticsearch-host/"], 
       http_auth=("username", "password") 
   )
   INDEX_NAME = 'your_index_name'
   ```

## 🚀 Running the API

### Start the API Server
```bash
python run_api.py
```

The API will be available at: `http://localhost:8000`

### Interactive API Documentation
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

## 📚 API Endpoints

### 1. Root Endpoint
**GET** `/`
Returns API information and available endpoints.

### 2. Health Check
**GET** `/health`
Check API and Elasticsearch health status.

**Response:**
```json
{
  "status": "healthy",
  "message": "API is running",
  "elasticsearch": true,
  "index": "testindex"
}
```

### 3. Upload Boolean Query
**POST** `/upload`

Upload a JSON file containing boolean query translations.

**Parameters:**
- `file`: JSON file (multipart/form-data)
- `index_name`: Optional Elasticsearch index name (query parameter)
- `delete_existing`: Whether to delete existing document (default: true)

**JSON Format:**
```json
{
  "companyId": "COMPANY_ID",
  "companyName": "Company Name",
  "originalQuery": "India Maritime Week",
  "translations": {
    "hi": "\"इंडिया मैरिटाइम वीक\"",
    "bn": "\"ইন্ডিয়া মারিটাইম উইক ২৫\""
  }
}
```

**Response:**
```json
{
  "success": true,
  "message": "Document inserted successfully",
  "companyId": "COMPANY_ID",
  "companyName": "Company Name",
  "index": "testindex",
  "documentExists": true,
  "deletedExisting": true,
  "languages": 3,
  "languageCodes": ["lang_en", "lang_hi", "lang_bn"]
}
```

### 4. Get Company Boolean
**GET** `/company/{company_id}`

Retrieve a boolean query document by company ID.

**Response:**
```json
{
  "success": true,
  "companyId": "COMPANY_ID",
  "index": "testindex",
  "document": {
    "companyId": "COMPANY_ID",
    "companyName": "Company Name",
    "lang_en": {
      "match_phrase": {
        "content": {
          "query": "India Maritime Week"
        }
      }
    },
    "lang_hi": {
      "match_phrase": {
        "content": {
          "query": "इंडिया मैरिटाइम वीक"
        }
      }
    }
  }
}
```

### 5. Delete Company Boolean
**DELETE** `/company/{company_id}`

Delete a boolean query document by company ID.

**Response:**
```json
{
  "success": true,
  "message": "Document with company ID 'COMPANY_ID' deleted successfully",
  "companyId": "COMPANY_ID",
  "index": "testindex",
  "result": {...}
}
```

## 🔍 ES Preview System

### 6. ES Preview Health Check
**GET** `/espreview/health`

Check esPreview system health.

### 7. Execute Boolean Query
**POST** `/espreview/query`

Execute a boolean query using esPreview.

**Request Body:**
```json
{
  "query": "technology AND innovation",
  "indexes": ["printarticleindex", "socialfeedindex"],
  "limit": 50,
  "include_content": false
}
```

**Response:**
```json
{
  "success": true,
  "total_matches": 150,
  "execution_time_ms": 45,
  "query_info": {
    "query_type": "boolean_string",
    "original_input": "technology AND innovation"
  },
  "index_results": {
    "printarticleindex": {
      "total_hits": 100,
      "article_ids": ["art_001", "art_002"],
      "articles": [...],
      "execution_time_ms": 25,
      "errors": []
    },
    "socialfeedindex": {
      "total_hits": 50,
      "article_ids": ["feed_001", "feed_002"],
      "articles": [...],
      "execution_time_ms": 20,
      "errors": []
    }
  },
  "errors": []
}
```

### 8. Execute Query from File
**POST** `/espreview/query/file`

Execute a boolean query from a JSON file.

**Parameters:**
- `file`: JSON file containing query
- `indexes`: Comma-separated list of indexes (optional)
- `limit`: Maximum results per index (optional)
- `language`: Language code (optional, defaults to 'en')

### 9. Execute Company Query
**POST** `/espreview/company/{company_id}`

Execute a stored query for a specific company.

**Request Body:**
```json
{
  "language": "en",
  "indexes": ["printarticleindex", "socialfeedindex"],
  "limit": 50,
  "include_content": false
}
```

### 10. List Companies
**GET** `/espreview/companies`

List available companies with stored queries.

**Parameters:**
- `limit`: Maximum number of companies to return (default: 100)

**Response:**
```json
{
  "success": true,
  "count": 25,
  "companies": [
    {
      "companyId": "CYBERPE865",
      "companyName": "CyberPeace Foundation"
    },
    {
      "companyId": "HUL",
      "companyName": "Hindustan Unilever"
    }
  ]
}
```

## 🏗️ Architecture

### Core Components

1. **API Layer** (`src/api/boolean_inserter_api.py`)
   - FastAPI application with all endpoints
   - Request/response handling
   - Error management

2. **ES Preview System** (`esPreview/`)
   - Query execution engine
   - Multi-index search capabilities
   - Result processing and formatting

3. **Configuration** (`src/utils/Config.py`)
   - Elasticsearch connection settings
   - Index configuration

4. **Transformer** (`src/utils/txt_to_es_transformer.py`)
   - JSON to Elasticsearch DSL conversion
   - Query parsing and validation

### Data Flow

1. **Upload Process:**
   ```
   JSON File → Parse → Convert to DSL → Insert to ES
   ```

2. **Query Process:**
   ```
   Boolean Query → Parse → Execute → Format Results
   ```

3. **Company Query Process:**
   ```
   Company ID → Retrieve Stored Query → Execute → Format Results
   ```

## 🔧 Configuration

### Environment Variables
Create a `.env` file with the following variables:

```env
# Elasticsearch Configuration
ES_HOST=https://your-elasticsearch-host/
ES_USER=your_username
ES_PASSWORD=your_password
ES_INDEX_NAME=testindex

# Logging
LOG_LEVEL=INFO

# Performance Settings
MAX_RESULTS_PER_INDEX=50
TIMEOUT_SECONDS=30
```

### Elasticsearch Index Structure
The system expects documents with the following structure:

```json
{
  "companyId": "string",
  "companyName": "string",
  "lang_en": {
    "match_phrase": {
      "content": {
        "query": "string"
      }
    }
  },
  "lang_hi": {
    "match_phrase": {
      "content": {
        "query": "string"
      }
    }
  }
}
```

## 🧪 Testing

### Manual Testing
Use the interactive API documentation at `http://localhost:8000/docs` to test endpoints.

### Example cURL Commands

**Health Check:**
```bash
curl http://localhost:8000/health
```

**Upload File:**
```bash
curl -X POST "http://localhost:8000/upload" \
  -F "file=@company_data.json"
```

**Execute Query:**
```bash
curl -X POST "http://localhost:8000/espreview/query" \
  -H "Content-Type: application/json" \
  -d '{"query": "technology AND innovation", "limit": 10}'
```

**Get Company:**
```bash
curl http://localhost:8000/company/CYBERPE865
```

## 🚨 Error Handling

The API provides comprehensive error handling:

- **400 Bad Request**: Invalid file format or missing required fields
- **404 Not Found**: Company ID not found
- **500 Internal Server Error**: Elasticsearch connection issues or processing errors
- **503 Service Unavailable**: Elasticsearch unavailable

All errors include descriptive messages and error codes for easy debugging.

## 📊 Performance

### Optimization Features
- **Parallel Search**: Multi-index searches run in parallel
- **Connection Pooling**: Efficient Elasticsearch connection management
- **Result Limiting**: Configurable result limits to prevent memory issues
- **Timeout Handling**: Prevents hanging requests

### Monitoring
- Health check endpoints for system monitoring
- Execution time tracking for performance analysis
- Error logging for debugging

## 🔒 Security

- **Authentication**: Elasticsearch authentication via username/password
- **Input Validation**: All inputs are validated before processing
- **Error Sanitization**: Error messages don't expose sensitive information

## 📝 Development

### Project Structure
```
esBooleanTranslator/
├── src/
│   ├── api/
│   │   └── boolean_inserter_api.py    # Main API
│   └── utils/
│       ├── Config.py                  # Configuration
│       └── txt_to_es_transformer.py   # Data transformation
├── esPreview/
│   ├── __init__.py
│   ├── espreview.py                   # ES Preview engine
│   ├── cli.py                         # Command-line interface
│   └── main.py                        # Main entry point
├── run_api.py                         # API server runner
├── requirements.txt                   # Dependencies
└── README.md                          # This file
```

### Adding New Features
1. Add new endpoints in `src/api/boolean_inserter_api.py`
2. Implement business logic in appropriate utility modules
3. Update this documentation
4. Test thoroughly using the interactive API docs

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License.

## 🆘 Support

For issues and questions:
1. Check the API documentation at `/docs`
2. Review the health check endpoint
3. Check Elasticsearch connectivity
4. Review logs for detailed error information

---

**Last Updated**: January 2025
**Version**: 1.0.0

