# Changes Made to esPreview

## Summary
Modified esPreview to include additional fields in the response based on document type (article vs socialFeed).

## Files Modified

### 1. `esBooleanTranslator/esPreview/espreview.py`

#### Changes Made:

**For Articles (printarticleindex):**
- Added `articleDate` field from `articleInfo.articleDate`
- Added `imageId` field from `uploadInfo.imageId`

**For Social Feeds (socialfeedindex):**
- Added `feedDate` field from `feedData.feedDate`
- Added `links` field from `feedInfo.link`

#### Code Locations:

1. **Lines 470-493**: Updated `_source` fields to fetch additional fields from Elasticsearch
2. **Lines 527-535**: Updated article content extraction to include new fields in response

## Elasticsearch Document Structure

### Articles (printarticleindex)
```
{
  "articleInfo": {
    "articleDate": "2024-01-15T00:00:00"
  },
  "uploadInfo": {
    "imageId": "image_123"
  },
  "articleData": {
    "headlines": "...",
    "summary": "...",
    "text": "..."
  }
}
```

### Social Feeds (socialfeedindex)
```
{
  "feedData": {
    "feedDate": "2024-01-15T00:00:00"
  },
  "feedInfo": {
    "link": "https://..."
  },
  "feedData": {
    "headlines": "...",
    "summary": "...",
    "text": "..."
  }
}
```

## Response Structure

### Before Changes
```json
{
  "id": "doc_id",
  "headlines": "...",
  "summary": "...",
  "text": "..."
}
```

### After Changes - Articles
```json
{
  "id": "doc_id",
  "headlines": "...",
  "summary": "...",
  "text": "...",
  "articleDate": "2024-01-15T00:00:00",
  "imageId": "image_123 he"
}
```

### After Changes - Social Feeds
```json
{
  "id": "doc_id",
  "headlines": "...",
  "summary": "...",
  "text": "...",
  "feedDate": "2024-01-15T00:00:00",
  "links": "https://..."
}
```

## Testing

Run the test script to verify the changes:
```bash
cd esBooleanTranslator/esPreview
python test_fields.py
```

## Notes

- Fields are only included when `include_content=true` is set in the API request
- If fields don't exist in Elasticsearch, they will be `null` in the response
- All changes are backward compatible
- No breaking changes to existing functionality















