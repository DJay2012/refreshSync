from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    app_name: str = Field("allSearch API", env="APP_NAME")

    postgres_host: str = Field(..., env="POSTGRES_HOST")
    postgres_port: int = Field(5432, env="POSTGRES_PORT")
    postgres_db: str = Field(..., env="POSTGRES_DB")
    postgres_user: str = Field(..., env="POSTGRES_USER")
    postgres_password: str = Field(..., env="POSTGRES_PASSWORD")
    postgres_min_pool_size: int = Field(1, env="POSTGRES_MIN_POOL_SIZE")
    postgres_max_pool_size: int = Field(5, env="POSTGRES_MAX_POOL_SIZE")

    publication_paths: List[Path] = Field(default_factory=list)

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
    )

    @validator("publication_paths", pre=True, always=True)
    def _default_publication_paths(cls, value):  # noqa: N805
        if value:
            if isinstance(value, str):
                return [Path(p.strip()) for p in value.split(",") if p.strip()]
            return [Path(p) for p in value]

        project_root = Path(__file__).resolve().parent.parent
        defaults = [
            project_root.parent / "allSearchScrapper" / "Publications.xlsx",
            project_root.parent / "allSearchScrapper" / "PublicationsUSA.xlsx",
            project_root.parent / "allSearchScrapper" / "PublicationsAll.xlsx",
        ]
        return defaults


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()


