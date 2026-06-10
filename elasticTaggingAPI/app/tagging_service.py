from __future__ import annotations

from time import perf_counter
from typing import List, Tuple

from legacy.core.optimized_tagger import tag_article_optimized
from legacy.core.tagger import tag_article

from .models import KeywordSource, TagResult


def _normalise_keywords(raw_keywords) -> List[str]:
    if not raw_keywords:
        return []
    if isinstance(raw_keywords, str):
        return [kw.strip() for kw in raw_keywords.split(",") if kw.strip()]
    if isinstance(raw_keywords, (list, tuple, set)):
        return [str(kw).strip() for kw in raw_keywords if str(kw).strip()]
    return [str(raw_keywords).strip()]


def execute_tagging(
    article_id: str | None,
    headline: str,
    summary: str | None,
    content: str,
    language: str | None,
    use_optimized: bool,
) -> Tuple[List[TagResult], float, bool, List[dict]]:
    """
    Run tagging via the legacy modules and transform results into API models.

    Returns:
        tags (List[TagResult]): normalised tagging output
        duration_ms (float): execution duration in milliseconds
        used_optimized (bool): whether the optimized tagger was used
        raw_tags (List[dict]): legacy tagger payload used for persistence
    """

    summary = summary or ""
    language = language or "en"

    start = perf_counter()
    if use_optimized:
        raw_tags = tag_article_optimized(article_id, headline, summary, content, language)
        used_optimized = True
    else:
        raw_tags = tag_article(article_id, headline, summary, content, language)
        used_optimized = False
    duration_ms = (perf_counter() - start) * 1000

    tag_results: List[TagResult] = []
    serialisable_tags: List[TagResult] = []
    processed_raw_tags: List[dict] = raw_tags if isinstance(raw_tags, list) else []

    if processed_raw_tags:
        for item in processed_raw_tags:
            company_id = item.get("COMPANYID")
            company_name = item.get("COMPANYNAME")
            keywords = _normalise_keywords(item.get("KEYWORDS"))
            sources = item.get("SOURCES") or {}

            keyword_sources = [
                KeywordSource(keyword=keyword, sources=[str(src) for src in sources.get(keyword, [])])
                for keyword in keywords
            ]

            serialisable_tags.append(
                TagResult(
                    companyId=str(company_id) if company_id is not None else None,
                    companyName=company_name,
                    keywords=keywords,
                    keywordSources=keyword_sources,
                )
            )

    return serialisable_tags, duration_ms, used_optimized, processed_raw_tags