from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlparse

import requests
from goose3 import Goose
from langdetect import LangDetectException, detect
from newspaper import Article

SCRAPINGDOG_SCRAPE_URL = "https://api.scrapingdog.com/scrape"
SCRAPINGDOG_AI_QUERY = "Give me the article headlines and content verbatim"

# ScrapingDog normally returns the answer to ``ai_query`` as JSON.  Some sites
# (notably NDTV) instead return the complete page rendered as Markdown.  The
# latter starts with navigation/advertising and has hundreds of links before
# the actual article, so it must never be treated as article text directly.
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_INLINE_RELATED_RE = re.compile(
    r"(?:ये भी पढ़ें|ये भी पढ़ें|यह भी पढ़ें|यह भी पढ़ें)\s*:\s*\[[^\]]+\]\([^)]*\)"
    r"|(?:\*\*)?\[Read:\s*[^\]]+\]\([^)]*\)(?:\*\*)?",
    re.IGNORECASE,
)
# The provider can flatten the Markdown to one line, so headings cannot rely
# on newline boundaries.  A hash preceded by whitespace and followed by a
# space still reliably identifies an H1/H2 marker.
_MARKDOWN_HEADING_RE = re.compile(
    r"(?<!\S)#\s+(.+?)(?=\s+#{1,6}\s+|\s+By\s+\[[^\]]+\]\([^)]*/author/"
    r"|\s+-\s+Share this Article\b|\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+\d{1,2},\s+\d{4}\b|\r?\n|$)"
)
_MARKDOWN_STANDFIRST_RE = re.compile(r"(?<!\S)##\s+(.+?)(?=\s+#{1,6}\s+|\r?\n|$)")
_ARTICLE_DATELINE_RE = re.compile(r"\*\*[A-Z][^*:\n]{1,60}:\*\*\s+")
_ARTICLE_METADATA_RE = re.compile(
    r"\s+-\s+\[[^\]]+\]\([^)]*/authors/[^)]*\)|\bRead Time:\s*\d+\s*(?:mins?|minutes?)\b",
    re.IGNORECASE,
)
_ARTICLE_END_RE = re.compile(
    r"(?im)(?<!\S)(?:\[\s*)?(?:#{1,6}\s+)?(?:got a follow[\-\u2011 ]?up question|related (?:news|articles)|trending news|quick links|more from|read more|advertisement|about (?:the )?author)\b"
    r"|(?<!\S)(?:\[\s*)?#{1,6}\s+लेखक के बारे में"
    r"|(?<!\S)\[Share\]\(https?://(?:www\.)?facebook\.com/sharer"
    r"|(?<!\S)-news(?=[A-Z])"
)
_NON_ARTICLE_HEADLINES = {"advertisement", "latest news", "news", "home"}


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
    source: str = "unknown"

    @property
    def content_type(self) -> str:
        if self.source == "video-transcript":
            return "video_transcript"
        if self.source == "video-description" or "/video" in urlparse(self.url).path.lower():
            return "video_description"
        return "article"


def _clean_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _build_summary(text: str, max_chars: int = 500) -> str:
    snippet = _clean_whitespace(text)
    if len(snippet) <= max_chars:
        return snippet
    return snippet[:max_chars].rsplit(" ", 1)[0]


def _first_sentence(text: str) -> str:
    """Use the article lead when the page has no separate standfirst."""
    match = re.search(r"[.!?।](?=\s+\S)", text[:500])
    return text[:match.end()] if match else _build_summary(text)


def _standfirst_summary(text: str) -> str:
    """Keep a short standfirst, including Hindi text with danda punctuation."""
    text = _clean_whitespace(text)
    # Some publishers put a romanized topic label before a Hindi standfirst.
    label = re.match(r"[A-Za-z0-9\s]+:\s*(?=[\u0900-\u097f])", text)
    if label:
        text = text[label.end():]
    endings = list(re.finditer(r"[.!?।](?=\s+\S|$)", text[:500]))
    if endings:
        return text[:endings[min(2, len(endings) - 1)].end()]
    return _build_summary(text, max_chars=300)


def _detect_language(text: str) -> str:
    try:
        return detect(text)
    except LangDetectException:
        return "und"


def _valid_article(title: str, summary: str, content: str) -> bool:
    """Reject common navigation/error pages before returning HTTP success."""
    title = _clean_whitespace(title)
    summary = _clean_whitespace(summary)
    content = _clean_whitespace(content)
    if not 8 <= len(title) <= 300 or len(content) < 40:
        return False
    if title.casefold() in _NON_ARTICLE_HEADLINES or not any(char.isalpha() for char in title):
        return False
    if any(markup in title for markup in ("](", "<a ", "http://", "https://")):
        return False
    if content.casefold().startswith(("advertisement", "live tv", "access denied")):
        return False
    if "](" in summary or "](" in content[:500]:
        return False
    if len(re.findall(r"https?://", content)) > max(2, len(content) // 500):
        return False
    return True


def _plain_html_text(value: str) -> str:
    # Some publishers encode entities inside JSON-LD which is itself encoded
    # in the HTML response (for example &amp;#039; for an apostrophe).
    value = re.sub(r"<[^>]*>", " ", value)
    return _clean_whitespace(unescape(unescape(value)))


class _PageHTMLParser(HTMLParser):
    """Collect structured data, metadata and prose inside article elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.scripts: list[str] = []
        self.paragraphs: list[str] = []
        self.headings: list[str] = []
        self._script: Optional[list[str]] = None
        self._heading: Optional[list[str]] = None
        self._paragraph: Optional[list[str]] = None
        self._paragraph_tag = ""
        self._article_depth = 0
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        if tag == "meta":
            key = attributes.get("property") or attributes.get("name")
            if key and attributes.get("content"):
                self.meta[key.lower()] = attributes["content"]
        if tag == "script" and (attributes.get("type") or "").lower() == "application/ld+json":
            self._script = []
        if tag == "h1":
            self._heading = []
        if tag == "article":
            self._article_depth += 1
        if self._article_depth and tag in {"aside", "nav", "footer", "script", "style", "form", "button", "figure"}:
            self._ignored += 1
        if self._article_depth and not self._ignored and tag in {"p", "h2", "h3"}:
            self._paragraph_tag = tag
            self._paragraph = []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)
        if self._heading is not None:
            self._heading.append(data)
        if self._paragraph is not None and not self._ignored:
            self._paragraph.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None
        if tag == "h1" and self._heading is not None:
            self.headings.append(_clean_whitespace(" ".join(self._heading)))
            self._heading = None
        if tag == self._paragraph_tag and self._paragraph is not None:
            paragraph = _clean_whitespace(" ".join(self._paragraph))
            if paragraph:
                self.paragraphs.append(paragraph)
            self._paragraph = None
            self._paragraph_tag = ""
        if self._article_depth and tag in {"aside", "nav", "footer", "script", "style", "form", "button", "figure"}:
            self._ignored = max(0, self._ignored - 1)
        if tag == "article" and self._article_depth:
            self._article_depth -= 1


def _structured_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from _structured_nodes(item)
    elif isinstance(value, dict):
        if isinstance(value.get("@graph"), list):
            yield from _structured_nodes(value["@graph"])
        yield value


def _node_url(node: dict) -> str:
    for value in (node.get("url"), node.get("mainEntityOfPage"), node.get("@id")):
        if isinstance(value, dict):
            value = value.get("@id") or value.get("url")
        if isinstance(value, str) and value.startswith("http"):
            return value
    return ""


def _same_page(first: str, second: str) -> bool:
    a, b = urlparse(first), urlparse(second)
    return (
        (a.hostname or "").removeprefix("www.") == (b.hostname or "").removeprefix("www.")
        and a.path.rstrip("/") == b.path.rstrip("/")
    )


def _strip_site_suffix(title: str, url: str) -> str:
    if " | " not in title:
        return title
    headline, suffix = title.rsplit(" | ", 1)
    hostname = (urlparse(url).hostname or "").removeprefix("www.")
    brand = re.sub(r"\W", "", hostname.split(".")[0].casefold())
    suffix_key = re.sub(r"\W", "", suffix.casefold())
    if brand and suffix_key in {brand, brand + "news", brand + "breakingnews"}:
        return headline
    return title


def _clean_video_description(value: str) -> str:
    value = _plain_html_text(value)
    value = re.sub(r"(?<=[a-z])\.(?=[A-Z])", ". ", value)
    value = re.split(
        r"(?i)(?<!\S)-news(?=[A-Z])|\bSubscribe Now\b|\bFollow Every Breaking Story\b",
        value,
        maxsplit=1,
    )[0]
    return _clean_whitespace(value)


def _extract_html_article(raw_html: str, url: str, goose: Goose) -> Optional[ScrapedArticle]:
    if "<html" not in raw_html[:2000].lower():
        return None
    page = _PageHTMLParser()
    page.feed(raw_html)
    nodes = []
    for script in page.scripts:
        try:
            nodes.extend(_structured_nodes(json.loads(script)))
        except ValueError:
            continue
    article_types = {"Article", "NewsArticle", "BlogPosting", "VideoObject"}
    candidates = [
        node for node in nodes
        if article_types.intersection(
            {str(kind).rsplit("/", 1)[-1] for kind in (
                node.get("@type") if isinstance(node.get("@type"), list) else [node.get("@type")]
            )}
        )
    ]
    video_page = "/video" in urlparse(url).path.lower()
    candidates.sort(
        key=lambda node: (
            _same_page(_node_url(node), url),
            bool(node.get("articleBody") or node.get("transcript")),
            video_page and "VideoObject" in str(node.get("@type")),
        ),
        reverse=True,
    )
    selected = candidates[0] if candidates else {}
    kind = selected.get("@type") or ""
    is_video = video_page or any(
        str(item).rsplit("/", 1)[-1] == "VideoObject"
        for item in (kind if isinstance(kind, list) else [kind])
    )

    title = _plain_html_text(str(
        selected.get("headline") or selected.get("name")
        or (page.headings[0] if page.headings else "")
        or page.meta.get("og:title", "")
    ))
    title = re.sub(r"\s+\|\s+[^|]{0,50}\b(?:Breaking News|News)\s*$", "", title, flags=re.IGNORECASE)
    title = _strip_site_suffix(title, url)
    description = selected.get("description") or page.meta.get("og:description") or page.meta.get("description") or ""
    description = _plain_html_text(str(description))
    content = selected.get("articleBody") or selected.get("transcript") or ""
    source = "video-transcript" if selected.get("transcript") else "structured-body" if content else ""
    if isinstance(content, str):
        content = _plain_html_text(content)
    else:
        content = ""
    if not content and is_video:
        content = _clean_video_description(description)
        source = "video-description"
    if not content:
        paragraphs = []
        for paragraph in page.paragraphs:
            if re.match(r"(?i)^(?:related (?:articles|news)|about (?:the )?author|लेखक के बारे में)\b", paragraph):
                break
            paragraphs.append(paragraph)
        content = _clean_whitespace(" ".join(paragraphs))
        source = "article-dom"
    if not _valid_article(title, description or _first_sentence(content), content):
        try:
            extracted = goose.extract(raw_html=raw_html)
            content = _clean_whitespace(extracted.cleaned_text or "")
            title = title or _clean_whitespace(extracted.title or "")
            description = description or _clean_whitespace(extracted.meta_description or "")
            source = "goose-html"
        except Exception:
            return None
    summary = _first_sentence(content) if is_video else _standfirst_summary(description) if description else _first_sentence(content)
    if not _valid_article(title, summary, content):
        return None
    published_at = None
    date_value = selected.get("datePublished")
    if isinstance(date_value, str):
        try:
            published_at = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return ScrapedArticle(
        url=url,
        title=title,
        text=content,
        summary=summary,
        language=_detect_language(content),
        author=None,
        image_url=None,
        published_at=published_at,
        source=source,
    )


def _markdown_to_text(value: str) -> str:
    """Remove common Markdown syntax while preserving readable article text."""
    value = _INLINE_RELATED_RE.sub("", value)
    value = _MARKDOWN_IMAGE_RE.sub("", value)
    value = _MARKDOWN_LINK_RE.sub(lambda match: match.group(1), value)
    value = re.sub(r"<([^>]+)>", r"\1", value)  # e.g. <mailto:...>
    value = re.sub(r"(?<!\S)#{1,6}\s+", "", value)
    value = re.sub(r"(?m)^\s*[-*+]\s+", "", value)
    value = re.sub(r"(?<!\w)[*_`]{1,3}|[*_`]{1,3}(?!\w)", "", value)
    value = re.sub(r"(?<=[a-z])\.(?=[A-Z])", ". ", value)
    return _clean_whitespace(value)


def _extract_article_from_markdown(value: str) -> tuple[str, str, str]:
    """Extract an article region when a provider returned page Markdown.

    Article pages commonly expose their H1 as a ``#`` heading.  Selecting the
    content after that heading avoids header navigation and advertisements;
    stopping at related-news/footer headings avoids returning the rest of the
    site as the article.
    """
    heading_match = None
    for candidate in _MARKDOWN_HEADING_RE.finditer(value):
        headline = _markdown_to_text(candidate.group(1)).strip(" -")
        if (
            8 <= len(headline) <= 300
            and headline.casefold() not in _NON_ARTICLE_HEADLINES
            and not headline.startswith("[")
        ):
            heading_match = candidate
            break

    if not heading_match:
        return "", "", ""

    title = _markdown_to_text(heading_match.group(1)).strip(" -")
    title = re.sub(r"\s+\|\s+[^|]{0,50}\b(?:Breaking News|News)\s*$", "", title, flags=re.IGNORECASE)
    article_region = value[heading_match.end():]
    standfirst_match = _MARKDOWN_STANDFIRST_RE.search(article_region[:600])
    if standfirst_match and re.search(
        r"\b(?:Highlights|Table of Contents)\b", article_region[:standfirst_match.start()], re.IGNORECASE
    ):
        standfirst_match = None
    summary = ""
    if standfirst_match:
        standfirst = standfirst_match.group(1)
        metadata_match = _ARTICLE_METADATA_RE.search(standfirst)
        if metadata_match:
            standfirst = standfirst[:metadata_match.start()]
        summary = _standfirst_summary(_markdown_to_text(standfirst).strip(" -"))

    end_match = _ARTICLE_END_RE.search(article_region)
    if end_match:
        article_region = article_region[:end_match.start()]

    # In flattened page Markdown the standfirst, byline, date, images and
    # highlights all share one line with the story.  A bold dateline is one
    # reliable boundary for the actual article paragraphs.
    dateline_match = _ARTICLE_DATELINE_RE.search(article_region)
    if dateline_match:
        article_region = article_region[dateline_match.start():]
    else:
        highlights_match = re.search(r"\bHighlights\s+-\s+", article_region)
        toc_match = re.search(r"\bTable of Contents\s+-\s+", article_region)
        share_controls = re.search(r"\bcopy link\b", article_region, re.IGNORECASE)
        if share_controls and share_controls.start() < 3000:
            # Video pages commonly place the description after their sharing
            # toolbar and have no separate standfirst or article sections.
            article_region = article_region[share_controls.end():]
        elif highlights_match and toc_match and toc_match.start() > highlights_match.end():
            # The last highlights bullet ends before the article's lead.
            # A sentence break followed by a capitalized word separates them
            # even when the provider flattens the whole page to one line.
            highlights = article_region[highlights_match.end():toc_match.start()]
            last_bullet = highlights.rsplit(" - ", 1)[-1]
            bullet_end = re.search(r"[.!?](?=\s+[A-Z])", last_bullet)
            if not bullet_end:
                return "", "", ""
            article_region = article_region[
                toc_match.start() - len(last_bullet) + bullet_end.end():
            ]
        elif standfirst_match:
            if standfirst_match.start() < 10 and not _ARTICLE_METADATA_RE.search(article_region):
                # Multiline Markdown can have the body directly after the H2.
                article_region = article_region[standfirst_match.end():]
            else:
                # When metadata precedes the H2, its prose is the article lead.
                article_region = article_region[standfirst_match.start():]
                article_region = re.sub(
                    r"^##\s+[A-Za-z0-9\s]+:\s*(?=[\u0900-\u097f])",
                    "",
                    article_region,
                    count=1,
                )
        else:
            return "", "", ""

    # A table of contents is page furniture between the lead and later
    # sections, not part of the story itself.
    article_region = re.sub(
        r"\bTable of Contents\s+-\s+.*?(?=\s+##\s+)",
        "",
        article_region,
        count=1,
        flags=re.DOTALL,
    )

    content = _markdown_to_text(article_region)
    if len(content) < 40:
        return "", "", ""
    return title, summary or _first_sentence(content), content


def _looks_like_page_markdown(value: str) -> bool:
    """Return true for a full-page Markdown response, not article prose."""
    if len(_MARKDOWN_LINK_RE.findall(value)) < 8:
        return False
    return (
        bool(_MARKDOWN_HEADING_RE.search(value))
        or value.lstrip().lower().startswith("advertisement")
        or len(_MARKDOWN_LINK_RE.findall(value[:1200])) >= 5
    )


def _extract_ai_article(response_text: str) -> tuple[str, str, str]:
    text = response_text.strip()
    title = ""
    content = ""
    summary = ""

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
        summary_value = payload.get("summary") or payload.get("description")
        if isinstance(summary_value, str):
            summary = _clean_whitespace(summary_value)
    elif isinstance(payload, list):
        content = _clean_whitespace(" ".join(str(item) for item in payload))

    if not content:
        content = text

    # Recover a usable article from the full-page Markdown fallback before the
    # generic "first line is a title" heuristic can select "Advertisement".
    # Check ``content`` too because a provider may JSON-wrap the raw Markdown.
    markdown_source = content if _looks_like_page_markdown(content) else text
    if _looks_like_page_markdown(markdown_source):
        markdown_title, markdown_summary, markdown_content = _extract_article_from_markdown(markdown_source)
        if markdown_title and markdown_content:
            return _clean_titles(markdown_title), markdown_summary, markdown_content
        raise ValueError("Could not identify article content in the scraped page.")

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

    content = _clean_whitespace(content)
    return _clean_titles(title), summary or _build_summary(content), content


class Scraper:
    """Extract structured HTML first, then fall back to ScrapingDog's AI output."""

    def __init__(self) -> None:
        self._goose = Goose()

    def scrape(self, url: str) -> ScrapedArticle:
        api_key = os.getenv("SCRAPINGDOG_API_KEY")
        if not api_key:
            raise ValueError("SCRAPINGDOG_API_KEY environment variable not set")

        base_params = {"api_key": api_key, "url": url}
        # Raw HTML is cheaper and preserves article JSON-LD and DOM structure.
        # A rendered retry covers sites whose article appears only after JS.
        html_candidate = None
        for dynamic in ("false", "true"):
            try:
                response = requests.get(
                    SCRAPINGDOG_SCRAPE_URL,
                    params={**base_params, "dynamic": dynamic, "formats": "html"},
                    timeout=60,
                )
                if response.status_code == 200:
                    article = _extract_html_article(response.text, url, self._goose)
                    if article:
                        if article.source in {"structured-body", "video-transcript", "video-description"}:
                            return article
                        html_candidate = article
                        break
            except requests.RequestException:
                continue

        attempts = [
            {"dynamic": "false"},
            {"dynamic": "true"},
            {"dynamic": "true", "stealth_mode": "true"},
        ]

        last_error = None
        for attempt in attempts:
            try:
                response = requests.get(
                    SCRAPINGDOG_SCRAPE_URL,
                    params={**base_params, "ai_query": SCRAPINGDOG_AI_QUERY, **attempt},
                    timeout=60,
                )
                if response.status_code != 200:
                    last_error = f"ScrapingDog returned status {response.status_code}"
                    continue
                title, summary, text = _extract_ai_article(response.text)
                if not _valid_article(title, summary, text):
                    last_error = "Scraped page did not contain a reliable article."
                    continue
                markdown_candidate = ScrapedArticle(
                    url=url,
                    title=title,
                    text=text,
                    summary=summary,
                    language=_detect_language(text),
                    author=None,
                    image_url=None,
                    published_at=None,
                    source="markdown",
                )
                if html_candidate and len(html_candidate.text) >= len(text) * 0.85:
                    return html_candidate
                return markdown_candidate
            except requests.RequestException:
                last_error = "ScrapingDog request failed"
            except ValueError as exc:
                last_error = str(exc)
        if html_candidate:
            return html_candidate
        raise ValueError(last_error or "Could not extract article content.")

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
