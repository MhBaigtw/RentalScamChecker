from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.db import (
    ensure_database_exists,
    integration_database_url,
    migrate_to_head,
    truncate_all_tables,
)


@pytest.fixture
def photo_dir(tmp_path: Path) -> Path:
    return tmp_path / "photos"


@pytest.fixture(scope="session")
def test_database() -> str:
    """Create (if missing) and migrate the test database once per test session, starting empty."""
    url = integration_database_url()
    ensure_database_exists(url)
    migrate_to_head(url)
    truncate_all_tables(url)  # in case an earlier run was interrupted before cleaning up
    return url


@pytest.fixture(autouse=True)
def _clean_test_database(request: pytest.FixtureRequest) -> Iterator[None]:
    """Integration tests get the test database, and it is emptied after each one."""
    if request.node.get_closest_marker("integration") is None:
        yield
        return
    url: str = request.getfixturevalue("test_database")
    yield
    truncate_all_tables(url)
