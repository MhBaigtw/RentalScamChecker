"""Settings, read from environment variables (and the repo-root .env file in development)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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
    # Secret key for HMAC-SHA256 of phone numbers and emails (ED-004). Changing it breaks
    # matching against every contact hash stored before the change.
    contact_hash_pepper: SecretStr = Field(min_length=32)
    # Region used to parse phone numbers written without a country code.
    contact_default_region: str = "CA"
    # Root folder for the local-disk photo storage (dev). Relative paths resolve from the repo root.
    photo_storage_dir: Path = Path("var/photos")

    @property
    def photo_storage_path(self) -> Path:
        if self.photo_storage_dir.is_absolute():
            return self.photo_storage_dir
        return REPO_ROOT / self.photo_storage_dir


@lru_cache
def get_settings() -> Settings:
    # mypy can't see that BaseSettings fills database_url from the environment.
    return Settings()  # type: ignore[call-arg]
