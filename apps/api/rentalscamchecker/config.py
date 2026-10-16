"""Settings, read from environment variables (and the repo-root .env file in development)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/rentalscamchecker/config.py -> repo root is three levels above this package.
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        # .env also holds Postgres and web variables that the API does not use.
        extra="ignore",
    )

    database_url: str
    database_connect_timeout: int = 2
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    api_port: int = 8100


@lru_cache
def get_settings() -> Settings:
    # mypy can't see that BaseSettings fills database_url from the environment.
    return Settings()  # type: ignore[call-arg]
