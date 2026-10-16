"""Nominatim client: identification, caching, the global rate limit, and errors. No live network."""

import threading

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.geocoding import GeocodingUnavailable, NominatimGeocoder, query_key
from tests.nominatim_fake import FakeNominatim, place

pytestmark = pytest.mark.integration

EMAIL = "ops@example.invalid"
OAKVILLE_Q = query_key("1430 Trafalgar Rd", "Oakville")


def geocoder(
    sf: sessionmaker[Session], transport: httpx.BaseTransport, interval: float = 1.0
) -> NominatimGeocoder:
    return NominatimGeocoder(
        sf,
        contact_email=EMAIL,
        base_url="https://nominatim.example.invalid",
        min_interval_seconds=interval,
        timeout_seconds=5,
        transport=transport,
    )


def test_query_key_is_normalized() -> None:
    assert query_key("  1430  Trafalgar Rd ,Oakville ", "Oakville") == (
        "1430 trafalgar rd, oakville, oakville, canada"
    )
    assert OAKVILLE_Q == "1430 trafalgar rd, oakville, canada"


def test_request_identifies_the_app_and_asks_for_canada_only(
    session_factory: sessionmaker[Session],
) -> None:
    fake = FakeNominatim({OAKVILLE_Q: [place(city="Oakville")]})

    result = geocoder(session_factory, fake.transport).geocode("1430 Trafalgar Rd", "Oakville")

    assert result is not None and result.address["city"] == "Oakville"
    (request,) = fake.requests
    assert request.url.path == "/search"
    assert dict(request.url.params) == {
        "q": OAKVILLE_Q,
        "format": "jsonv2",
        "addressdetails": "1",
        "limit": "1",
        "countrycodes": "ca",
        "accept-language": "en",
    }
    user_agent = request.headers["User-Agent"]
    assert user_agent.startswith("RentalScamChecker/0.1 (+https://github.com/MhBaigtw/")
    assert EMAIL in user_agent
    assert "python-httpx" not in user_agent  # policy: stock library User-Agents won't do


def test_repeated_query_is_served_from_the_cache(session_factory: sessionmaker[Session]) -> None:
    fake = FakeNominatim({OAKVILLE_Q: [place(city="Oakville")]})
    first = geocoder(session_factory, fake.transport)
    second = geocoder(session_factory, fake.transport)  # e.g. another worker process

    a = first.geocode("1430 Trafalgar Rd", "Oakville")
    b = second.geocode("1430  trafalgar rd", "OAKVILLE")

    assert a == b
    assert len(fake.requests) == 1


def test_not_found_is_cached_too(session_factory: sessionmaker[Session]) -> None:
    fake = FakeNominatim()
    g = geocoder(session_factory, fake.transport)

    assert g.geocode("999 Nowhere Lane", "Oakville") is None
    assert g.geocode("999 Nowhere Lane", "Oakville") is None
    assert len(fake.requests) == 1


def test_requests_are_spaced_by_the_minimum_interval_across_instances(
    session_factory: sessionmaker[Session],
) -> None:
    fake = FakeNominatim()
    # Three geocoders (as if in three worker processes) asking three different addresses at once.
    geocoders = [geocoder(session_factory, fake.transport, interval=1.0) for _ in range(3)]
    threads = [
        threading.Thread(target=g.geocode, args=(f"{n} Main St", "Oakville"))
        for n, g in enumerate(geocoders)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    times = sorted(fake.request_times)
    assert len(times) == 3
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert all(gap >= 0.95 for gap in gaps), gaps


@pytest.mark.parametrize("status", [429, 500, 503])
def test_http_error_is_retryable_and_not_cached(
    session_factory: sessionmaker[Session], status: int
) -> None:
    fake = FakeNominatim(status=status)

    with pytest.raises(GeocodingUnavailable, match=f"HTTP {status}"):
        geocoder(session_factory, fake.transport).geocode("1 Main St", "Oakville")

    with session_factory() as s:
        assert s.execute(sa.text("SELECT count(*) FROM geocode_cache")).scalar_one() == 0


def test_network_error_is_retryable(session_factory: sessionmaker[Session]) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(GeocodingUnavailable, match="ConnectError"):
        geocoder(session_factory, httpx.MockTransport(refuse)).geocode("1 Main St", "Oakville")
