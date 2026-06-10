from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Attempt to load environment variables from common locations so the legacy
# tagging modules pick them up when imported.
ENV_CANDIDATES = [
    Path(__file__).resolve().parent.parent / ".env",
]

for candidate in ENV_CANDIDATES:
    if candidate.exists():
        load_dotenv(candidate, override=False)


class Settings(BaseSettings):
    """Configuration required by the tagging API."""

    model_config = SettingsConfigDict(case_sensitive=False)

    app_name: str = Field("elasticTagging API", env="APP_NAME")
    es_host: str = Field(..., env="ES_HOST")
    es_user: str = Field(..., env="ES_USER")
    es_password: str = Field(..., env="ES_PASSWORD")
    es_index_name: str = Field(..., env="ES_INDEX_NAME")
    default_use_optimized: bool = Field(False, env="TAGGING_USE_OPTIMIZED_DEFAULT")
    mongo_enabled: Optional[bool] = Field(default=None, env="MONGODB_ENABLED")


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()




