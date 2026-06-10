from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class TaggingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    article_id: Optional[str] = Field(default=None, alias="articleId")
    headline: str
    content: str
    summary: Optional[str] = None
    language: Optional[str] = "en"
    use_optimized: Optional[bool] = Field(default=None, alias="useOptimized")
    write_to_db: bool = Field(default=False, alias="writeToDb")


class CompanyKeywordLookupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    company_id: str = Field(alias="companyId")
    headline: str
    content: str
    summary: Optional[str] = None
    language: Optional[str] = "en"
    use_optimized: Optional[bool] = Field(default=None, alias="useOptimized")


class KeywordSource(BaseModel):
    keyword: str
    sources: List[str]


class TagResult(BaseModel):
    company_id: Optional[str] = Field(default=None, alias="companyId")
    company_name: Optional[str] = Field(default=None, alias="companyName")
    keywords: List[str]
    keyword_sources: List[KeywordSource] = Field(alias="keywordSources", default_factory=list)


class TaggingResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    article_id: Optional[str] = Field(default=None, alias="articleId")
    tags: List[TagResult]
    tag_count: int = Field(alias="tagCount")
    duration_ms: float = Field(alias="durationMs")
    used_optimized: bool = Field(alias="usedOptimized")
    persisted: bool = False
    postgres_update_type: Optional[int] = Field(default=None, alias="postgresUpdateType")
    mongo_upserted: Optional[bool] = Field(default=None, alias="mongoUpserted")


class CompanyKeywordLookupResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    company_id: Optional[str] = Field(default=None, alias="companyId")
    company_name: Optional[str] = Field(default=None, alias="companyName")
    keywords: List[str]
    keyword_sources: List[KeywordSource] = Field(alias="keywordSources", default_factory=list)




