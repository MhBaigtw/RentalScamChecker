import pytest
from fastapi.testclient import TestClient

from rentalscamchecker.config import Settings, get_settings
from rentalscamchecker.main import create_app, error_body

# Port 1 on localhost: nothing listens there, so connecting is refused immediately.
UNREACHABLE_DB_URL = "postgresql+psycopg://nobody:nopass@127.0.0.1:1/nodb"


def test_error_body_has_the_documented_shape() -> None:
    assert error_body("x_code", "Something failed.") == {
        "error": {"code": "x_code", "message": "Something failed.", "details": {}}
    }


@pytest.mark.integration
def test_health_ok_when_database_is_up() -> None:
    client = TestClient(create_app(get_settings()))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_returns_503_with_error_shape_when_database_is_down() -> None:
    settings = Settings(database_url=UNREACHABLE_DB_URL, database_connect_timeout=1)
    client = TestClient(create_app(settings))

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "database_unavailable",
            "message": "The database is not reachable.",
            "details": {"database": "unavailable"},
        }
    }


def test_request_id_is_echoed_back() -> None:
    settings = Settings(database_url=UNREACHABLE_DB_URL, database_connect_timeout=1)
    client = TestClient(create_app(settings))

    response = client.get("/health", headers={"X-Request-ID": "test-123"})

    assert response.headers["X-Request-ID"] == "test-123"
