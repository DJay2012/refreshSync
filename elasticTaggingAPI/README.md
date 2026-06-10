# elasticTaggingAPI

FastAPI wrapper around the legacy `elasticTagging` taggers.  
Send an article payload and receive the companies/keywords detected by the Elasticsearch percolator index.

## Setup

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   pip install -r requirements.txt
   ```

2. Provide the required environment variables (e.g. via `.env` in this folder):

   ```
   APP_NAME=elasticTagging API
   ES_HOST=http://elastic-host:9200
   ES_USER=elastic
   ES_PASSWORD=secret
   ES_INDEX_NAME=companyboolreseachalllangtestv1
   TAGGING_USE_OPTIMIZED_DEFAULT=false
   ```

   If you want the optimized tagger (which enriches company names via MongoDB) set `TAGGING_USE_OPTIMIZED_DEFAULT=true` and ensure the legacy `MONGODB_*` settings are also present.

3. Run the API:

   ```bash
   uvicorn app.main:app --reload
   ```

4. Test tagging:

   ```bash
   curl -X POST http://localhost:8000/tag ^
     -H "Content-Type: application/json" ^
     -d "{\"headline\":\"Sample title\",\"content\":\"Body text\",\"summary\":\"\", \"language\":\"en\"}"
   ```

## Company keyword lookup

Use `/tag/company-keywords` to inspect how a specific company was tagged and which article section triggered each keyword.

```bash
curl -X POST http://localhost:8000/tag/company-keywords ^
  -H "Content-Type: application/json" ^
  -d "{\
        \"companyId\":\"12345\",\
        \"headline\":\"Sample title\",\
        \"content\":\"Body text\",\
        \"summary\":\"Optional summary\"\
      }"
```

The response returns the company metadata plus `keywordSources`, listing each keyword alongside the section(s) that matched (headline/content/summary).

## Notes

- The optimized tagger depends on MongoDB (for cached company names). If the connection fails the service falls back to the standard tagger when `useOptimized=false`.
- Existing environment files under `elasticTagging/.env` are picked up automatically if present.
- Only tagging is exposed; preview/backtracking flows remain unchanged.




