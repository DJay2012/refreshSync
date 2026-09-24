"""
Router for allSearchAPI endpoints integrated into the refresh API.
"""
import json
import html
import logging
import os
import re
from datetime import datetime
from typing import Optional, Union
from urllib.parse import parse_qs, urlparse

import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException, status

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
    YouTubeVideoScrapeRequest,
    YouTubeVideoScrapeResponse,
    YouTubeTranscriptScrapeResponse,
)
from allSearchAPI.app.publications import (
    PublicationRegistry,
    extract_main_domain,
    extract_subdomain,
)
from allSearchAPI.app.scraping import Scraper, count_words

logger = logging.getLogger(__name__)


# --- ScrapingDog helpers (copied from NewScrapper, no import dependency) ---

def _scrape_with_scrapingdog(url: str) -> dict:
    """Fetch an article via the shared ScrapingDog AI scrape implementation."""
    scraped = scraper.scrape(url)
    parsed = urlparse(url)
    return {
        "title": scraped.title,
        "content": scraped.text,
        "content_type": scraped.content_type,
        "summary": scraped.summary,
        "language": scraped.language,
        "publication_date": scraped.published_at,
        "site_name": parsed.netloc.replace("www.", ""),
    }


# --- BrightData helpers (Instagram post/reel scraping) ---

BRIGHTDATA_SCRAPE_URL = "https://api.brightdata.com/datasets/v3/scrape"
SCRAPINGDOG_YOUTUBE_VIDEO_URL = "https://api.scrapingdog.com/youtube/video"
SCRAPINGDOG_YOUTUBE_TRANSCRIPTS_URL = "https://api.scrapingdog.com/youtube/transcripts"


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
    except ValueError:
        # BrightData returns newline-delimited JSON (one object per line)
        # when multiple input URLs are scraped in one call.
        data = []
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data.append(json.loads(line))
            except ValueError as exc:
                raise ValueError(f"BrightData returned unparseable response: {resp.text[:500]}") from exc

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


_ISO8601_DURATION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def _iso8601_duration_to_seconds(duration: str) -> Optional[float]:
    """Convert a YouTube Data API contentDetails.duration (e.g. PT1H12M30S) to seconds."""
    match = _ISO8601_DURATION_RE.match(duration or "")
    if not match:
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


def _fetch_youtube_duration_seconds(video_id: str) -> Optional[float]:
    """Fetch exact video duration via YouTube Data API v3 videos.list (1 quota unit)."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    try:
        response = http_requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "contentDetails", "id": video_id, "key": api_key},
            timeout=15,
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        if not items:
            return None
        return _iso8601_duration_to_seconds(items[0]["contentDetails"]["duration"])
    except (http_requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("YouTube Data API duration lookup failed for %s: %s", video_id, exc)
        return None


def _extract_youtube_video_id(url: str) -> str:
    """Extract a video ID from common YouTube video URL formats."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            video_id = parts[1] if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"} else ""
    else:
        video_id = ""

    if not video_id:
        raise ValueError("Could not extract a video ID from the YouTube URL")
    return video_id


def _scrape_youtube_video(url: str) -> dict:
    """Fetch YouTube video metadata from ScrapingDog and filter its response."""
    api_key = os.getenv("SCRAPINGDOG_API_KEY")
    if not api_key:
        raise RuntimeError("SCRAPINGDOG_API_KEY environment variable not set")

    response = http_requests.get(
        SCRAPINGDOG_YOUTUBE_VIDEO_URL,
        params={
            "api_key": api_key,
            "v": _extract_youtube_video_id(url),
            "country": "in",
        },
        timeout=60,
    )
    if response.status_code != 200:
        provider_detail = None
        try:
            error_payload = response.json()
            if isinstance(error_payload, dict):
                provider_detail = (
                    error_payload.get("error")
                    or error_payload.get("message")
                    or error_payload.get("detail")
                )
        except ValueError:
            provider_detail = response.text.strip()[:300]

        if provider_detail:
            provider_detail = str(provider_detail).replace(api_key, "[REDACTED]")
            raise ValueError(
                f"ScrapingDog returned status {response.status_code}: {provider_detail}"
            )
        raise ValueError(f"ScrapingDog returned status {response.status_code} without error details")

    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError("ScrapingDog returned an invalid JSON response") from exc
    if not isinstance(data, dict):
        raise ValueError("ScrapingDog returned an unexpected response")

    raw_video = data.get("video") if isinstance(data.get("video"), dict) else {}
    video = dict(raw_video)
    video_id = video.get("id") or _extract_youtube_video_id(url)
    video_defaults = {
        "id": "not available",
        "title": "not available",
        "views": "not available",
        "likes": "not available",
        "author": "not available",
        "published_time": "not available",
        "description": "not available",
        "keywords": [],
        "thumbnail": "not available",
    }
    for key, fallback in video_defaults.items():
        if video.get(key) is None or video.get(key) == "":
            video[key] = fallback

    duration_seconds = _fetch_youtube_duration_seconds(video_id) if video_id else None
    video["duration_seconds"] = duration_seconds if duration_seconds is not None else "not available"

    raw_channel = data.get("channel") if isinstance(data.get("channel"), dict) else {}
    channel = {
        key: raw_channel.get(key) or "not available"
        for key in ("id", "name", "link")
    }

    raw_comment = data.get("comment") if isinstance(data.get("comment"), dict) else {}
    comment = dict(raw_comment)
    if comment.get("total") is None or comment.get("total") == "":
        comment["total"] = "not available"

    return {"video": video, "channel": channel, "comment": comment}


def _scrape_youtube_transcript(url: str) -> tuple:
    """Fetch transcript segments from ScrapingDog and combine their text.

    Video duration comes from the YouTube Data API (exact); if that's
    unavailable, falls back to estimating from the last transcript
    segment's start + duration.
    """
    api_key = os.getenv("SCRAPINGDOG_API_KEY")
    if not api_key:
        raise RuntimeError("SCRAPINGDOG_API_KEY environment variable not set")

    video_id = _extract_youtube_video_id(url)
    response = http_requests.get(
        SCRAPINGDOG_YOUTUBE_TRANSCRIPTS_URL,
        params={
            "api_key": api_key,
            "v": video_id,
            "country": "in",
        },
        timeout=60,
    )
    if response.status_code != 200:
        provider_detail = None
        try:
            error_payload = response.json()
            if isinstance(error_payload, dict):
                provider_detail = (
                    error_payload.get("error")
                    or error_payload.get("message")
                    or error_payload.get("detail")
                )
        except ValueError:
            provider_detail = response.text.strip()[:300]

        if provider_detail:
            provider_detail = str(provider_detail).replace(api_key, "[REDACTED]")
            raise ValueError(
                f"ScrapingDog returned status {response.status_code}: {provider_detail}"
            )
        raise ValueError(f"ScrapingDog returned status {response.status_code} without error details")

    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError("ScrapingDog returned an invalid JSON response") from exc
    if not isinstance(data, dict) or not isinstance(data.get("transcripts"), list):
        raise ValueError("ScrapingDog returned an unexpected transcript response")

    text_segments = []
    estimated_duration = None
    for segment in data["transcripts"]:
        if not isinstance(segment, dict):
            continue
        start = segment.get("start")
        length = segment.get("duration")
        if isinstance(start, (int, float)) and isinstance(length, (int, float)):
            estimated_duration = max(estimated_duration or 0, start + length)
        if not isinstance(segment.get("text"), str):
            continue
        normalized_text = _clean_transcript_text(segment["text"])
        if normalized_text:
            text_segments.append(normalized_text)

    duration_seconds = _fetch_youtube_duration_seconds(video_id)
    if duration_seconds is None:
        duration_seconds = estimated_duration

    if not text_segments:
        return "not available", duration_seconds

    cleaned_transcript = _clean_transcript_text(" ".join(text_segments))
    return _format_transcript_paragraphs(cleaned_transcript), duration_seconds


def _clean_transcript_text(text: str) -> str:
    """Decode caption artifacts and normalize transcript text for reading."""
    cleaned = text
    for _ in range(3):
        decoded = html.unescape(cleaned)
        if decoded == cleaned:
            break
        cleaned = decoded

    cleaned = re.sub(
        r"\[(?:music|applause|laughter|cheering|silence)\]",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.replace(">>", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    return cleaned.strip()


def _format_transcript_paragraphs(text: str) -> str:
    """Group transcript sentences into readable, topic-aware paragraphs."""
    sentences = re.split(r"(?<=[.!?])\s+(?=[\"']?[A-Z0-9])", text)
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
    if not sentences:
        return text

    transition_pattern = re.compile(
        r"^(?:now\b|moving on\b|shifting focus\b|in terms of\b|as for\b|"
        r"features-wise\b|importantly\b|interestingly\b|then again\b|"
        r"long story short\b|of the other things\b|prices?\b)",
        flags=re.IGNORECASE,
    )

    paragraphs = []
    current = []
    for sentence in sentences:
        starts_new_topic = bool(transition_pattern.match(sentence))
        if current and (len(current) >= 6 or (len(current) >= 2 and starts_new_topic)):
            paragraphs.append(" ".join(current))
            current = []
        current.append(sentence)

    if current:
        paragraphs.append(" ".join(current))

    return "\n\n".join(paragraphs)


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
            content_type=data.get("content_type"),
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


@router.post("/scrape/youtube-video", response_model=YouTubeVideoScrapeResponse, status_code=status.HTTP_200_OK)
def scrape_youtube_video_endpoint(payload: YouTubeVideoScrapeRequest):
    """Scrape selected YouTube video metadata via ScrapingDog."""
    try:
        return YouTubeVideoScrapeResponse(**_scrape_youtube_video(str(payload.url)))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except http_requests.RequestException as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ScrapingDog request failed") from exc


@router.post(
    "/scrape/youtube-transcript",
    response_model=YouTubeTranscriptScrapeResponse,
    status_code=status.HTTP_200_OK,
)
def scrape_youtube_transcript_endpoint(payload: YouTubeVideoScrapeRequest):
    """Scrape and combine a YouTube video's transcript via ScrapingDog."""
    try:
        transcript, duration_seconds = _scrape_youtube_transcript(str(payload.url))
        return YouTubeTranscriptScrapeResponse(content=transcript, duration_seconds=duration_seconds)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except http_requests.RequestException as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ScrapingDog request failed") from exc
