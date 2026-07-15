from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field, HttpUrl, ConfigDict, validator


class ScrapeRequest(BaseModel):
    url: HttpUrl
    article_date: Optional[datetime] = Field(default=None, alias="articleDate")
    keyword: Optional[str] = None
    source: Optional[str] = "allSearchAPI"
    publication_override: Optional[str] = Field(default=None, alias="publication")
    skip_publication_check: bool = Field(default=False, alias="skipPublicationCheck")
    dry_run: bool = Field(default=False, alias="dryRun")
    request_type: Optional[str] = Field(default=None, alias="requestType")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @validator("keyword", "source", "publication_override", pre=True)
    def _strip_strings(cls, value):  # noqa: N805
        if isinstance(value, str):
            return value.strip() or None
        return value

    @validator("article_date", pre=True)
    def _parse_article_date(cls, value):  # noqa: N805
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError("articleDate must be ISO formatted") from exc


class ArticlePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    title: str
    summary: str
    text: str
    language: str
    author: Optional[str]
    image_url: Optional[str] = Field(None, alias="imageUrl")
    word_count: int = Field(..., alias="wordCount")
    published_at: Optional[datetime] = Field(None, alias="publishedAt")

    @validator("published_at", pre=True)
    def _parse_published_at(cls, value):  # noqa: N805
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            # Try ISO format first
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
            # Try common date formats
            date_formats = [
                "%A, %d %B %Y",  # Sunday, 16 November 2025
                "%d %B %Y",  # 16 November 2025
                "%B %d, %Y",  # November 16, 2025
                "%Y-%m-%d",  # 2025-11-16
                "%Y-%m-%d %H:%M:%S",  # 2025-11-16 12:00:00
                "%d/%m/%Y",  # 16/11/2025
                "%m/%d/%Y",  # 11/16/2025
            ]
            for fmt in date_formats:
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
            # If all parsing fails, return None instead of raising an error
            # This allows the field to be optional
            return None
        return value


class ScrapeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: str
    inserted: bool
    publication: Optional[str]
    social_feed_id: Optional[int] = Field(None, alias="socialFeedId")
    txn_number: Optional[str] = Field(None, alias="txnNumber")
    message: Optional[str] = None
    article: Optional[ArticlePayload] = None


class AdhocScrapeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    headline: Optional[str] = None
    summary: Optional[str] = None
    content: Optional[str] = None
    publication: Optional[str] = None
    articledate: Optional[datetime] = Field(None, alias="articleDate")


class InstagramScrapeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    urls: List[HttpUrl] = Field(..., min_length=1)

    @validator("urls")
    def _validate_instagram_urls(cls, value):  # noqa: N805
        for url in value:
            if "instagram.com" not in str(url):
                raise ValueError(f"Not an Instagram URL: {url}")
        return value


class InstagramPostPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    url: Optional[str] = None
    post_id: Optional[str] = Field(None, alias="postId")
    shortcode: Optional[str] = None
    content_type: Optional[str] = Field(None, alias="contentType")
    caption: Optional[str] = None
    hashtags: Optional[List[str]] = None
    mentions: Optional[List[str]] = None
    likes: Optional[int] = None
    num_comments: Optional[int] = Field(None, alias="numComments")
    video_view_count: Optional[int] = Field(None, alias="videoViewCount")
    video_play_count: Optional[int] = Field(None, alias="videoPlayCount")
    is_video: Optional[bool] = Field(None, alias="isVideo")
    video_url: Optional[str] = Field(None, alias="videoUrl")
    display_url: Optional[str] = Field(None, alias="displayUrl")
    images: Optional[List[str]] = None
    owner_username: Optional[str] = Field(None, alias="ownerUsername")
    owner_full_name: Optional[str] = Field(None, alias="ownerFullName")
    owner_id: Optional[str] = Field(None, alias="ownerId")
    followers: Optional[int] = None
    is_verified: Optional[bool] = Field(None, alias="isVerified")
    location: Optional[str] = None
    published_at: Optional[datetime] = Field(None, alias="publishedAt")

    @validator("published_at", pre=True)
    def _parse_published_at(cls, value):  # noqa: N805
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return value

    @validator("images", pre=True)
    def _coerce_images(cls, value):  # noqa: N805
        if not isinstance(value, list):
            return None
        urls = []
        for entry in value:
            if isinstance(entry, str):
                urls.append(entry)
            elif isinstance(entry, dict):
                url = entry.get("url")
                if url:
                    urls.append(url)
        return urls or None

    @validator("mentions", pre=True)
    def _coerce_mentions(cls, value):  # noqa: N805
        if not isinstance(value, list):
            return None
        names = []
        for entry in value:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict):
                username = entry.get("username") or entry.get("full_name")
                if username:
                    names.append(username)
        return names or None

    @validator("location", pre=True)
    def _coerce_location(cls, value):  # noqa: N805
        if value is None:
            return None
        if isinstance(value, str):
            return value or None
        if isinstance(value, list):
            names = [str(v) for v in value if v]
            return ", ".join(names) or None
        if isinstance(value, dict):
            return value.get("name") or None
        return None

    @validator("hashtags", pre=True)
    def _coerce_lists(cls, value):  # noqa: N805
        if value is None:
            return None
        if isinstance(value, list):
            return value
        return None


class InstagramScrapeResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: str
    url: str
    post: Optional[InstagramPostPayload] = None
    error: Optional[str] = None


class InstagramScrapeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    results: List[InstagramScrapeResult]


