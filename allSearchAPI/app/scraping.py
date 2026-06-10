from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from goose3 import Goose
from langdetect import LangDetectException, detect
from newspaper import Article


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


class Scraper:
    """Scrape article content using Goose with a Newspaper fallback."""

    def __init__(self) -> None:
        self._goose = Goose()

    def scrape(self, url: str) -> ScrapedArticle:
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


