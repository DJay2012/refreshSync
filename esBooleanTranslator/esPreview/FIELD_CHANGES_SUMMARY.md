# Field Changes Summary - esPreview

## Overview
Updated esPreview to include additional fields in the response based on the document type (article vs socialFeed).

## Changes Made

### File Modified
- `esBooleanTranslator/esPreview/espreview.py`

### Modifications

#### 1. Updated `_search_single_index` method (lines 470-493)
Added logic to include additional fields in the Elasticsearch `_source` based on the index type:

**For printarticleindex (articles):**
- `articleInfo.articleDate` - The article publication date
- `uploadInfo.imageId` - The image ID associated with the article

**For socialfeedindex (socialFeed):**
- `feedData.feedDate` - The feed date
- `feedInfo.link` - The link to the social feed post

#### 2. Updated article content extraction (lines 527-535)
Added logic to extract and include these new fields in the response object:

**For articles:**
```python
article_content["articleDate"] = source.get('articleInfo', {}).get('articleDate')
article_content["imageId"] = source.get('uploadInfo', {}).get('imageId')
```

**For socialFeed:**
```python
article_content["feedDate"] = source.get('feedData', {}).get('feedDate')
article_content["links"] = source.get('feedInfo', {}).get('link')
```

## API Response Structure

### Articles (printarticleindex)
When querying articles with `include_content=true`, the response now includes:
```json
{
  "id": "article_id",
  "headlines": "...",
  "summary": "...",
  "text": "...",
  "articleDate": "2024-01-15T00:00:00",  // NEW
  "imageId": "image_123"                  // NEW
}
```

### Social Feeds (socialfeedindex)
When querying social feeds with `include_content=true`, the response now includes:
```json
{
  "id": "feed_id",
  "headlines": "...",
  "summary": "...",
  "text": "...",
  "feedDate": "2024-01-15T00:00:00",     // NEW
  "links": "https://..."                  // NEW
}
```

## Testing

A test script has been created at `esBooleanTranslator/esPreview/test_fields.py` to verify these changes.

To run the test:
```bash
cd esBooleanTranslator/esPreview
python test_fields.py
```

## API Endpoints Affected

The following API endpoints will automatically return the new fields when `include_content=true`:
- `POST /espreview/query` - Execute boolean query
- `POST /espreview/company/{company_id}` - Execute company query
- `POST /boolean/espreview/query` - Execute boolean query (main API)
- `POST /boolean/espreview/company/{company_id}` - Execute company query (main API)

## Notes

- Fields are only included when `include_content=true` is set in the request
- If fields are not present in the Elasticsearch document, they will be `null` in the response
- The changes are backward compatible - existing functionality remains unchanged















