"""
Router for allSearchAPI endpoints integrated into the refresh API.
"""
import logging
import os
from datetime import datetime
from typing import Optional, Union
from urllib.parse import urlparse

import requests as http_requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, status
from langdetect import detect, LangDetectException

from allSearchAPI.app.config import get_settings as get_allsearch_settings
from allSearchAPI.app.models import AdhocScrapeResponse, ArticlePayload, ScrapeRequest, ScrapeResponse
from allSearchAPI.app.publications import (
    PublicationRegistry,
    extract_main_domain,
    extract_subdomain,
)
from allSearchAPI.app.scraping import Scraper, count_words

logger = logging.getLogger(__name__)


# --- ScrapingDog helpers (copied from NewScrapper, no import dependency) ---

def _detect_language(text: str) -> Optional[str]:
    try:
        return detect(text)
    except (LangDetectException, Exception):
        return None


def _summarize(text: str, num_sentences: int = 2) -> str:
    if not text:
        return ""
    try:
        from nltk.tokenize import sent_tokenize
        sentences = sent_tokenize(text)
    except Exception:
        sentences = text.split(".")
    return " ".join(s.strip() for s in sentences[:num_sentences]).strip()


def _extract_publication_date(pub_date_str: str) -> Optional[datetime]:
    if not pub_date_str:
        return None
    try:
        from dateutil import parser as date_parser
        return date_parser.parse(pub_date_str, fuzzy=True)
    except Exception:
        return None


def _scrape_with_scrapingdog(url: str) -> dict:
    """Fetch and parse an article via ScrapingDog API."""
    api_key = os.getenv("SCRAPINGDOG_API_KEY")
    if not api_key:
        raise ValueError("SCRAPINGDOG_API_KEY environment variable not set")

    params = {
        "api_key": api_key,
        "url": url,
        "dynamic": "true",
    }
    response = http_requests.get(
        "https://api.scrapingdog.com/scrape",
        params=params,
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    if response.status_code != 200:
        raise ValueError(f"ScrapingDog returned status {response.status_code}: {response.text[:200]}")

    html_content = response.text
    if not html_content or len(html_content) < 200:
        raise ValueError("Insufficient content returned from ScrapingDog")

    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    # Title
    title = None
    if soup.title:
        title = soup.title.get_text(strip=True)
    if not title or len(title) < 5:
        og_title = soup.find("meta", property="og:title")
        if og_title:
            title = og_title.get("content", "").strip()
    if not title or len(title) < 5:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    # Content
    content = None
    article_tags = soup.find_all(
        ["article", "main", "div"],
        class_=lambda x: x and any(k in x.lower() for k in ["article", "content", "post", "story", "entry"]),
    )
    if article_tags:
        article_tags.sort(key=lambda x: len(x.get_text()), reverse=True)
        content = article_tags[0].get_text(separator=" ", strip=True)
    if not content or len(content) < 200:
        body = soup.find("body")
        if body:
            for el in body.find_all(["nav", "header", "footer", "aside", "script", "style"]):
                el.decompose()
            content = body.get_text(separator=" ", strip=True)
    if content:
        content = " ".join(content.split())

    # Publication date
    publication_date = None
    date_meta = soup.find("meta", property="article:published_time")
    if date_meta:
        publication_date = _extract_publication_date(date_meta.get("content", ""))
    if not publication_date:
        date_meta = soup.find("meta", {"name": "publish-date"})
        if date_meta:
            publication_date = _extract_publication_date(date_meta.get("content", ""))

    # Site name / publication
    site_name = None
    og_site = soup.find("meta", property="og:site_name")
    if og_site:
        site_name = og_site.get("content", "").strip()
    if not site_name:
        parsed = urlparse(url)
        site_name = parsed.netloc.replace("www.", "")

    summary_text = _summarize(content or "")
    lang = _detect_language(content or "")

    return {
        "title": title,
        "content": content,
        "summary": summary_text,
        "language": lang,
        "publication_date": publication_date,
        "site_name": site_name,
    }


# --- Initialize allSearchAPI components
allsearch_settings = get_allsearch_settings()
scraper = Scraper()
registry = PublicationRegistry(allsearch_settings.publication_paths)

# Create router
router = APIRouter(prefix="/allsearch", tags=["allSearch"])


def get_registry() -> PublicationRegistry:
    """Dependency to get publication registry."""
    return registry


@router.get("/health")
def allsearch_health(registry: PublicationRegistry = Depends(get_registry)):
    """Health check endpoint for allSearchAPI."""
    return {
        "status": "ok",
        "name": allsearch_settings.app_name,
        "timestamp": datetime.utcnow(),
        "knownPublications": len(registry.domains),
        "publicationLoadError": registry.last_error,
    }


@router.post("/scrape", response_model=Union[ScrapeResponse, AdhocScrapeResponse], status_code=status.HTTP_200_OK)
def scrape_endpoint(
    payload: ScrapeRequest,
    registry: PublicationRegistry = Depends(get_registry),
):
    """Scrape article content from a URL and return the scraped data."""
    url = str(payload.url)

    # --- Adhoc path: use ScrapingDog directly, skip publication check ---
    if payload.request_type == "adhoc":
        try:
            data = _scrape_with_scrapingdog(url)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ScrapingDog scrape failed") from exc

        publication = data.get("site_name") or extract_subdomain(url) or extract_main_domain(url)
        return AdhocScrapeResponse(
            headline=data.get("title"),
            summary=data.get("summary"),
            content=data.get("content"),
            publication=publication,
            articledate=data.get("publication_date"),
        )

    # --- Standard path ---
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

    detected_publication = publication or "Unknown"

    return ScrapeResponse(
        status="scraped",
        inserted=False,
        publication=detected_publication,
        social_feed_id=None,
        txn_number=None,
        message="Article scraped successfully.",
        article=article_payload,
    )

