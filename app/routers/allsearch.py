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
from allSearchAPI.app.models import (
    AdhocScrapeResponse,
    ArticlePayload,
    InstagramPostPayload,
    InstagramScrapeRequest,
    InstagramScrapeResponse,
    InstagramScrapeResult,
    ScrapeRequest,
    ScrapeResponse,
)
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

    base_params = {
        "api_key": api_key,
        "url": url,
    }
    response = None
    last_error = None
    for dynamic in ("true", "false"):
        resp = http_requests.get(
            "https://api.scrapingdog.com/scrape",
            params={**base_params, "dynamic": dynamic},
            timeout=60,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        if resp.status_code == 200:
            response = resp
            break
        last_error = f"ScrapingDog returned status {resp.status_code}: {resp.text[:200]}"
        logger.warning("ScrapingDog dynamic=%s failed for %s: %s", dynamic, url, last_error)

    if response is None:
        raise ValueError(last_error)

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


# --- BrightData helpers (Instagram post/reel scraping) ---

BRIGHTDATA_SCRAPE_URL = "https://api.brightdata.com/datasets/v3/scrape"


def _map_brightdata_instagram_item(item: dict) -> InstagramPostPayload:
    """Map a raw BrightData Instagram dataset item onto our InstagramPostPayload.

    Field names below reflect BrightData dataset gd_lk5ns7kz21pck8jpis's actual
    response shape (verified against live /datasets/v3/scrape calls for both a
    photo-carousel post and a reel): user_posted, description, num_comments,
    date_posted, likes, photos[] (plain URL strings), videos[] (plain URL
    strings), post_id, shortcode, content_type/product_type, thumbnail,
    followers, is_verified, user_posted_id, tagged_users, input.url.
    "images" duplicates "photos" but as {"url": ...} objects — used only as a
    defensive fallback since BrightData's own docs are inconsistent here.
    """
    item_input = item.get("input")
    input_url = item_input.get("url") if isinstance(item_input, dict) else None
    videos = item.get("videos") or []
    photos = item.get("photos") or []
    return InstagramPostPayload(
        url=item.get("url") or input_url,
        post_id=item.get("post_id"),
        shortcode=item.get("shortcode"),
        content_type=item.get("content_type") or item.get("product_type"),
        caption=item.get("description") or item.get("caption"),
        hashtags=item.get("hashtags"),
        mentions=item.get("tagged_users") or item.get("mentions"),
        likes=item.get("likes"),
        num_comments=item.get("num_comments"),
        video_view_count=item.get("video_view_count"),
        video_play_count=item.get("video_play_count"),
        is_video=bool(videos) or item.get("content_type") == "Reel",
        video_url=videos[0] if videos else None,
        display_url=item.get("thumbnail") or item.get("display_url"),
        images=photos or item.get("images"),
        owner_username=item.get("user_posted") or item.get("owner_username"),
        owner_full_name=item.get("profile_name") or item.get("owner_full_name"),
        owner_id=item.get("user_posted_id") or item.get("owner_id"),
        followers=item.get("followers"),
        is_verified=item.get("is_verified"),
        location=item.get("location"),
        published_at=item.get("date_posted") or item.get("timestamp"),
    )


def _scrape_instagram_with_brightdata(urls: list) -> dict:
    """Trigger a BrightData dataset scrape for one or more Instagram post/reel URLs.

    Returns a dict keyed by URL -> either {"post": InstagramPostPayload} or {"error": str}.
    """
    api_key = os.getenv("BRIGHTDATA_API_KEY")
    if not api_key:
        raise ValueError("BRIGHTDATA_API_KEY environment variable not set")

    dataset_id = os.getenv("BRIGHTDATA_INSTAGRAM_DATASET_ID", "gd_lk5ns7kz21pck8jpis")

    resp = http_requests.post(
        BRIGHTDATA_SCRAPE_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        params={
            "dataset_id": dataset_id,
            "notify": "false",
            "include_errors": "true",
        },
        json={
            "input": [{"url": url} for url in urls],
            "limit_per_input": None,
        },
        timeout=300,
    )

    if resp.status_code != 200:
        raise ValueError(f"BrightData returned status {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise ValueError(f"BrightData returned non-JSON response: {resp.text[:500]}") from exc

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # BrightData returns a bare object (not list-wrapped) when only one
        # item comes back; only unwrap "data"/"results" if that key actually
        # holds a list (some BrightData endpoints wrap batches that way).
        if isinstance(data.get("data"), list):
            items = data["data"]
        elif isinstance(data.get("results"), list):
            items = data["results"]
        else:
            items = [data]
    else:
        items = []

    results_by_url = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_url = item.get("url") or item.get("input_url")
        if isinstance(item.get("input"), dict):
            item_url = item_url or item["input"].get("url")
        if item.get("error") or item.get("error_code"):
            results_by_url[item_url] = {"error": item.get("error") or item.get("error_code")}
        else:
            results_by_url[item_url] = {"post": _map_brightdata_instagram_item(item)}

    return results_by_url


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


@router.post("/scrape/instagram", response_model=InstagramScrapeResponse, status_code=status.HTTP_200_OK)
def scrape_instagram_endpoint(payload: InstagramScrapeRequest):
    """Scrape Instagram post/reel data via BrightData and return the processed info."""
    urls = [str(u) for u in payload.urls]

    try:
        results_by_url = _scrape_instagram_with_brightdata(urls)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="BrightData Instagram scrape failed") from exc

    results = []
    for url in urls:
        entry = results_by_url.get(url)
        if entry is None:
            results.append(InstagramScrapeResult(status="missing", url=url, error="No data returned by BrightData for this URL."))
        elif "error" in entry:
            results.append(InstagramScrapeResult(status="error", url=url, error=str(entry["error"])))
        else:
            results.append(InstagramScrapeResult(status="scraped", url=url, post=entry["post"]))

    return InstagramScrapeResponse(results=results)

