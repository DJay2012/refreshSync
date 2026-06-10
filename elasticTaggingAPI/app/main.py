import logging
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, status

from legacy.core.Config import INDEX_NAME, es

from .config import Settings, get_settings
from .models import (
    CompanyKeywordLookupRequest,
    CompanyKeywordLookupResponse,
    TaggingRequest,
    TaggingResponse,
)
from .persistence import PersistenceError, PersistenceResult, persist_tagging_results
from .tagging_service import execute_tagging

logger = logging.getLogger(__name__)
settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.get("/health")
def health(settings: Settings = Depends(get_settings)):
    try:
        es_status = bool(es.ping())
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Elasticsearch ping failed: %s", exc)
        es_status = False

    return {
        "status": "ok" if es_status else "degraded",
        "elasticsearch": es_status,
        "index": INDEX_NAME,
        "useOptimizedByDefault": settings.default_use_optimized,
    }


@app.post("/tag", response_model=TaggingResponse, status_code=status.HTTP_200_OK)
def tag_endpoint(
    payload: TaggingRequest,
    settings: Settings = Depends(get_settings),
):
    use_optimized = payload.use_optimized
    if use_optimized is None:
        use_optimized = settings.default_use_optimized

    try:
        tags, duration_ms, used_optimized, raw_tags = execute_tagging(
            payload.article_id,
            payload.headline,
            payload.summary,
            payload.content,
            payload.language,
            use_optimized,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Tagging failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Tagging failed") from exc

    persistence_result: Optional[PersistenceResult] = None
    if payload.write_to_db:
        if payload.article_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="articleId is required when writeToDb is true.",
            )
        try:
            persistence_result = persist_tagging_results(
                payload.article_id,
                payload.headline,
                payload.summary,
                payload.content,
                payload.language,
                raw_tags,
            )
        except PersistenceError as exc:
            logger.exception("Persistence failed")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return TaggingResponse(
        articleId=payload.article_id,
        tags=tags,
        tagCount=len(tags),
        durationMs=duration_ms,
        usedOptimized=used_optimized,
        persisted=persistence_result.persisted if persistence_result else False,
        postgresUpdateType=persistence_result.postgres_update_type if persistence_result else None,
        mongoUpserted=persistence_result.mongo_upserted if persistence_result else None,
    )


@app.post(
    "/tag/company-keywords",
    response_model=CompanyKeywordLookupResponse,
    status_code=status.HTTP_200_OK,
)
def company_keyword_lookup_endpoint(
    payload: CompanyKeywordLookupRequest,
    settings: Settings = Depends(get_settings),
):
    use_optimized = payload.use_optimized
    if use_optimized is None:
        use_optimized = settings.default_use_optimized

    try:
        tags, _, _, _ = execute_tagging(
            article_id=None,
            headline=payload.headline,
            summary=payload.summary,
            content=payload.content,
            language=payload.language,
            use_optimized=use_optimized,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Company keyword lookup failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Tagging failed",
        ) from exc

    requested_company_id = str(payload.company_id).strip()
    matching_tag = next(
        (
            tag
            for tag in tags
            if tag.company_id is not None
            and str(tag.company_id).strip() == requested_company_id
        ),
        None,
    )

    if matching_tag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No tags found for supplied companyId.",
        )

    return CompanyKeywordLookupResponse(
        companyId=matching_tag.company_id,
        companyName=matching_tag.company_name,
        keywords=matching_tag.keywords,
        keywordSources=matching_tag.keyword_sources,
    )




