"""CORS (M7, ED-028): the browser calls the API directly, so only the web app's origin may."""

from fastapi.testclient import TestClient

from rentalscamchecker.main import create_app
from tests.helpers import UNREACHABLE_DB_URL, make_settings

WEB = "http://127.0.0.1:3100"


def preflight(origin: str) -> dict[str, str]:
    client = TestClient(create_app(make_settings(UNREACHABLE_DB_URL, web_origins=WEB)))
    response = client.options(
        "/checks",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    return dict(response.headers)


def test_the_web_origin_may_call_the_api() -> None:
    headers = preflight(WEB)
    assert headers["access-control-allow-origin"] == WEB
    assert "POST" in headers["access-control-allow-methods"]
    assert "access-control-allow-credentials" not in headers  # no cookies until accounts (M8)


def test_other_origins_are_not_allowed() -> None:
    assert "access-control-allow-origin" not in preflight("https://example.com")
