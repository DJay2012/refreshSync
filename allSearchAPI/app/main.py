import logging
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, status

from .config import get_settings
from .db import close_pool, get_cursor, init_pool
from .models import ArticlePayload, ScrapeRequest, ScrapeResponse
from .publications import (
    PublicationRegistry,
    determine_social_feed_type,
    extract_main_domain,
    extract_subdomain,
)
from .scraping import Scraper, count_words, generate_txn_number

logger = logging.getLogger(__name__)
settings = get_settings()
app = FastAPI(title=settings.app_name)
scraper = Scraper()
registry = PublicationRegistry(settings.publication_paths)


def get_registry() -> PublicationRegistry:
    return registry


@app.on_event("startup")
def on_startup() -> None:
    init_pool()
    registry.refresh()
    if registry.last_error:
        logger.warning("Publication registry loaded with warnings: %s", registry.last_error)


@app.on_event("shutdown")
def on_shutdown() -> None:
    close_pool()


@app.get("/health")
def health(registry: PublicationRegistry = Depends(get_registry)):
    return {
        "status": "ok",
        "name": settings.app_name,
        "timestamp": datetime.utcnow(),
        "knownPublications": len(registry.domains),
        "publicationLoadError": registry.last_error,
    }


@app.post("/scrape", response_model=ScrapeResponse, status_code=status.HTTP_201_CREATED)
def scrape_endpoint(
    payload: ScrapeRequest,
    registry: PublicationRegistry = Depends(get_registry),
):
    url = str(payload.url)
    article_date = payload.article_date or datetime.utcnow()

    publication: Optional[str] = payload.publication_override
    if not payload.skip_publication_check:
        allowed, matched = registry.is_allowed(url)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL is not part of a recognised publication list.",
            )
        publication = publication or matched
    else:
        if publication is None:
            _, matched = registry.is_allowed(url)
            publication = matched

    if not publication:
        fallback_publication = extract_subdomain(url) or extract_main_domain(url)
        publication = fallback_publication if fallback_publication else None

    try:
        scraped = scraper.scrape(url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to scrape article") from exc

    word_count = count_words(scraped.text)
    article_payload = ArticlePayload(
        title=scraped.title,
        summary=scraped.summary,
        text=scraped.text,
        language=scraped.language,
        author=scraped.author,
        image_url=scraped.image_url,
        word_count=word_count,
        published_at=scraped.published_at,
    )

    db_publication = publication or "Unknown"

    if payload.dry_run:
        return ScrapeResponse(
            status="dry-run",
            inserted=False,
            publication=db_publication,
            message="Dry run enabled. No data inserted.",
            article=article_payload,
        )

    with get_cursor(commit=True) as cur:
        cur.execute('SELECT COUNT(*) FROM "SOCIALFEEDHEADER_LOCAL" WHERE "LINK" = %s', (url,))
        if cur.fetchone()[0]:
            return ScrapeResponse(
                status="duplicate",
                inserted=False,
                publication=db_publication,
                message="URL already exists in SOCIALFEEDHEADER_LOCAL.",
                article=article_payload,
            )

        txn_number = None
        for _ in range(5):
            candidate = generate_txn_number()
            cur.execute('SELECT COUNT(*) FROM "SOCIALFEEDHEADER_LOCAL" WHERE "TXNNUMBER" = %s', (candidate,))
            if cur.fetchone()[0] == 0:
                txn_number = candidate
                break
        if txn_number is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to allocate txn number")

        cur.execute('SELECT nextval(\'"SEQUENCE_SOCIALFEEDID"\')')
        social_feed_id = cur.fetchone()[0]

        has_image = 1 if scraped.image_url else None
        insert_query = """
            INSERT INTO "SOCIALFEEDHEADER_LOCAL"(
                "TXNNUMBER", "SOCIALFEEDID", "LINK", "FEEDDATE", "HEADLINE_SNIPPET",
                "SUMMARY_SNIPPET", "HEADLINE", "SUMMARY", "PUBLICATION",
                "PUBLICATIONID", "LANGUAGE", "IMAGE", "KEYWORD_MATCHED",
                "CREATEDBY", "CREATEDON", "UPDATECIRRUS", "ISTAGGINGDONE",
                "HASIMAGE", "UPLOADDATE", "FEEDDATETIME", "ISACTIVE",
                "CONTENT", "REFERENCE_NO", "REFERENCE_SOURCE",
                "COMPANYIDS_TAGGING", "WORDCOUNT", "SOCIALFEEDTYPE","AUTHORNAME"
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s, %s, %s)
        """
        insert_values = (
            txn_number,
            social_feed_id,
            url,
            article_date,
            scraped.title,
            scraped.summary,
            scraped.title,
            scraped.summary,
            db_publication,
            1,
            scraped.language,
            scraped.image_url,
            payload.keyword,
            payload.source,
            datetime.utcnow(),
            0,
            0,
            has_image,
            datetime.utcnow(),
            article_date,
            "Y",
            scraped.text,
            None,
            None,
            "NONE",
            word_count,
            determine_social_feed_type(url),
            scraped.author,
        )
        cur.execute(insert_query, insert_values)

    return ScrapeResponse(
        status="inserted",
        inserted=True,
        publication=db_publication,
        social_feed_id=social_feed_id,
        txn_number=txn_number,
        article=article_payload,
    )

