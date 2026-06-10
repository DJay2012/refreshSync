# allSearchAPI

FastAPI service that accepts a URL, scrapes the article content, and stores it in the `SOCIALFEEDHEADER_LOCAL` PostgreSQL table used by the existing allSearch pipeline.

## Quick start

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and update the database credentials and optional publication file overrides.

3. Run the API:

   ```bash
   uvicorn app.main:app --reload
   ```

4. Test the endpoint:

   ```bash
   curl -X POST http://localhost:8000/scrape ^
     -H "Content-Type: application/json" ^
     -d "{\"url\": \"https://example.com/article\"}"
   ```

## Environment variables

| Variable | Description |
| --- | --- |
| `POSTGRES_HOST` | PostgreSQL host |
| `POSTGRES_PORT` | PostgreSQL port (default 5432) |
| `POSTGRES_DB` | PostgreSQL database name |
| `POSTGRES_USER` | PostgreSQL user |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `POSTGRES_MIN_POOL_SIZE` | Minimum connection pool size (default 1) |
| `POSTGRES_MAX_POOL_SIZE` | Maximum connection pool size (default 5) |
| `PUBLICATION_PATHS` | Optional comma-separated override for publication Excel files |

By default the service loads `Publications.xlsx`, `PublicationsUSA.xlsx`, and `PublicationsAll.xlsx` from the legacy `allSearchScrapper` project to validate incoming domains.

## Notes

- Oracle duplicate checks were intentionally removed, as requested.
- Use the `dryRun` flag in the request payload to validate scraping without inserting records.
- The service relies on the existing `SEQUENCE_SOCIALFEEDID` sequence and the `SOCIALFEEDHEADER_LOCAL` table created by the legacy pipeline.



