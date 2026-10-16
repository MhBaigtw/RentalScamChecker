"""Shared test helpers (imported explicitly; fixtures live in conftest.py)."""

import io
from pathlib import Path

from PIL import Image
from pydantic import SecretStr

from rentalscamchecker.config import Settings
from tests.db import integration_database_url

TEST_PEPPER = "test-pepper-not-a-secret-0123456789abcdef"
# Port 1 on localhost: nothing listens there, so connecting is refused immediately.
UNREACHABLE_DB_URL = "postgresql+psycopg://nobody:nopass@127.0.0.1:1/nodb"


def make_settings(
    database_url: str | None = None, photo_dir: Path | None = None, **overrides: object
) -> Settings:
    """Settings for tests: explicit pepper and photo folder; the test database by default."""
    return Settings(
        database_url=database_url or integration_database_url(),
        contact_hash_pepper=SecretStr(TEST_PEPPER),
        photo_storage_dir=photo_dir or Path("var/test-photos"),
        **overrides,  # type: ignore[arg-type]
    )


def image_bytes(
    size: tuple[int, int] = (64, 48),
    fmt: str = "JPEG",
    mode: str = "RGB",
    exif: Image.Exif | None = None,
) -> bytes:
    img = Image.new(mode, size, color=(200, 120, 40) if mode == "RGB" else None)
    out = io.BytesIO()
    if exif is not None:
        img.save(out, format=fmt, exif=exif)
    else:
        img.save(out, format=fmt)
    return out.getvalue()
