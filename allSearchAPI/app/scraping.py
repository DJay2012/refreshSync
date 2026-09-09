from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests
from goose3 import Goose
from langdetect import LangDetectException, detect
from newspaper import Article

SCRAPINGDOG_SCRAPE_URL = "https://api.scrapingdog.com/scrape"
SCRAPINGDOG_AI_QUERY = "Give me the article headlines and content verbatim"


@dataclass
class ScrapedArticle:
    url: str
    title: str
    text: str
    summary: str
    language: str
    author: Optional[str]
    image_url: Optional[str]
    published_at: Optional[datetime]


def _clean_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _build_summary(text: str, max_chars: int = 500) -> str:
    snippet = _clean_whitespace(text)
    if len(snippet) <= max_chars:
        return snippet
    return snippet[:max_chars].rsplit(" ", 1)[0]


def _detect_language(text: str) -> str:
    try:
        return detect(text)
    except LangDetectException:
        return "und"


def _extract_ai_article(response_text: str) -> tuple[str, str]:
    text = response_text.strip()
    title = ""
    content = ""

    try:
        payload = response_text and json.loads(response_text)
    except Exception:
        payload = None

    # ScrapingDog's AI query sometimes wraps results in a list under a key like
    # "article_headlines_and_content": [{"headline": ..., "content": ...}, ...]
    # or "article_headlines": ["headline1", "headline2", ...] (plain strings,
    # no content) instead of returning a flat {"headline": ..., "content": ...}
    # object.
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], (dict, str)):
                payload = value
                break

    if isinstance(payload, list) and payload and isinstance(payload[0], str):
        title = _clean_whitespace(" | ".join(str(v) for v in payload))
        content = title
    elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
        titles = []
        contents = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            entry_title = entry.get("headline") or entry.get("title")
            if entry_title:
                titles.append(str(entry_title).strip())
            entry_content = entry.get("content") or entry.get("text")
            if entry_content:
                contents.append(str(entry_content))
        title = _clean_whitespace(" | ".join(titles))
        content = _clean_whitespace(" ".join(contents))
    elif isinstance(payload, dict):
        title = str(
            payload.get("headline")
            or payload.get("headlines")
            or payload.get("title")
            or payload.get("article_headline")
            or ""
        ).strip()
        content_value = (
            payload.get("content")
            or payload.get("article_content")
            or payload.get("text")
            or payload.get("answer")
            or payload.get("result")
            or payload.get("data")
            or ""
        )
        content = content_value if isinstance(content_value, str) else str(content_value)
        content = _clean_whitespace(content)
    elif isinstance(payload, list):
        content = _clean_whitespace(" ".join(str(item) for item in payload))

    if not content:
        content = _clean_whitespace(text)

    if not title:
        headline_match = re.search(r"(?im)^\s*(?:article\s+)?headlines?\s*:\s*(.+)$", content)
        title_match = re.search(r"(?im)^\s*title\s*:\s*(.+)$", content)
        match = headline_match or title_match
        if match:
            title = _clean_whitespace(match.group(1))

    if not title:
        for line in re.split(r"[\r\n]+", text):
            candidate = _clean_whitespace(line.strip(" -*#"))
            if 5 <= len(candidate) <= 220:
                title = candidate
                break

    return _clean_titles(title), content


class Scraper:
    """Scrape article content using ScrapingDog, with legacy extractors retained."""

    def __init__(self) -> None:
        self._goose = Goose()

    def scrape(self, url: str) -> ScrapedArticle:
        api_key = os.getenv("SCRAPINGDOG_API_KEY")
        if not api_key:
            raise ValueError("SCRAPINGDOG_API_KEY environment variable not set")

        base_params = {
            "api_key": api_key,
            "url": url,
            "ai_query": SCRAPINGDOG_AI_QUERY,
        }
        attempts = [
            {"dynamic": "false"},
            {"dynamic": "true"},
            {"dynamic": "true", "stealth_mode": "true"},
        ]

        last_error = None
        response = None
        for attempt in attempts:
            response = requests.get(
                SCRAPINGDOG_SCRAPE_URL,
                params={**base_params, **attempt},
                timeout=60,
            )
            if response.status_code == 200:
                break
            last_error = f"ScrapingDog returned status {response.status_code}: {response.text[:200]}"
        else:
            raise ValueError(last_error or "ScrapingDog request failed")

        title, text = _extract_ai_article(response.text)
        if len(text) < 40:
            raise ValueError("Article text is too short to be useful.")

        return ScrapedArticle(
            url=url,
            title=title,
            text=text,
            summary=_build_summary(text),
            language=_detect_language(text),
            author=None,
            image_url=None,
            published_at=None,
        )

    def scrape_legacy(self, url: str) -> ScrapedArticle:
        try:
            goose_article = self._goose.extract(url=url)
        except Exception:
            goose_article = None

        if goose_article and goose_article.cleaned_text:
            title = goose_article.title or ""
            text = _clean_whitespace(goose_article.cleaned_text)
            if len(text) < 40:
                raise ValueError("Article text is too short to be useful.")
            summary = (
                goose_article.meta_description
                or (goose_article.meta_tags or {}).get("description", "")
                or _build_summary(text)
            )
            language = (
                goose_article.opengraph.get("locale", "en").split("_")[0]
                if goose_article.opengraph
                else "en"
            )
            if not language or len(language) > 5:
                language = _detect_language(text)

            image = None
            if goose_article.opengraph:
                image = goose_article.opengraph.get("image")
            if not image and goose_article.top_image:
                image = goose_article.top_image.src

            author = ", ".join(goose_article.authors) if goose_article.authors else None
            return ScrapedArticle(
                url=url,
                title=_clean_titles(title),
                text=text,
                summary=_build_summary(summary),
                language=language,
                author=author,
                image_url=image,
                published_at=goose_article.publish_date,
            )

        # Goose failed, fallback to Newspaper
        article = Article(url)
        article.download()
        article.parse()

        title = article.title or ""
        text = _clean_whitespace(article.text)
        if len(text) < 40:
            raise ValueError("Article text is too short to be useful.")

        summary = _build_summary(text)
        language = article.meta_lang or _detect_language(text)
        image = article.top_image or None
        author = ", ".join(article.authors) if article.authors else None

        return ScrapedArticle(
            url=url,
            title=_clean_titles(title),
            text=text,
            summary=summary,
            language=language,
            author=author,
            image_url=image,
            published_at=article.publish_date,
        )


def _clean_titles(title: str) -> str:
    """Normalise article titles to avoid stray whitespace."""

    return _clean_whitespace(title)


def generate_txn_number() -> str:
    """Generate a random 14-digit transaction number."""

    return str(random.randint(10_000_000_000_000, 99_999_999_999_999))


def count_words(text: str) -> int:
    return len(text.split())
