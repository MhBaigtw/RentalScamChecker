import pytest

from tests.db import integration_database_url, truncate_all_tables


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://rsc:pw@localhost:5434/rentalscamchecker",
        "postgresql+psycopg://rsc:pw@localhost:5434/postgres",
        "postgresql+psycopg://rsc:pw@localhost:5434/test_rentalscamchecker",
        'postgresql+psycopg://rsc:pw@localhost:5434/x"; DROP DATABASE y; --_test',
    ],
)
def test_refuses_to_truncate_anything_but_a_test_database(url: str) -> None:
    # Raises before connecting, so this needs no database.
    with pytest.raises(RuntimeError, match="must end in _test"):
        truncate_all_tables(url)


def test_default_test_database_is_the_dev_name_plus_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    assert integration_database_url().endswith("_test")


def test_test_database_url_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://a:b@h:1/other_test")

    assert integration_database_url() == "postgresql+psycopg://a:b@h:1/other_test"
