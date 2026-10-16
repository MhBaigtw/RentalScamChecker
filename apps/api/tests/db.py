"""The integration-test database: separate from the dev database, created and migrated on demand,
emptied after every integration test (docs/testing.md)."""

import os
import re
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import make_url

from alembic import command
from rentalscamchecker.config import get_settings
from rentalscamchecker.models import Base

API_DIR = Path(__file__).resolve().parents[1]
_SAFE_NAME = re.compile(r"^[a-z0-9_]+_test$")


def integration_database_url() -> str:
    """TEST_DATABASE_URL if set, else DATABASE_URL with "_test" appended to the database name."""
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


def _database_name(url: str) -> str:
    name = make_url(url).database or ""
    # Guard for everything below: we create, migrate and TRUNCATE this database, so refuse
    # anything that isn't clearly a test database.
    if not _SAFE_NAME.match(name):
        raise RuntimeError(f"refusing to use {name!r} as the test database (must end in _test)")
    return name


def ensure_database_exists(url: str) -> None:
    name = _database_name(url)
    # CREATE DATABASE can't run in a transaction, and needs a connection to another database.
    admin_url = make_url(url).set(database="postgres")
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if not exists:
                # The name matched _SAFE_NAME, so it is safe to put in the statement.
                conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()


def migrate_to_head(url: str) -> None:
    _database_name(url)
    config = Config()
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


def truncate_all_tables(url: str) -> None:
    _database_name(url)
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    finally:
        engine.dispose()
