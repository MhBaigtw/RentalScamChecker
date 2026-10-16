"""Geocoding through OpenStreetMap's Nominatim, following its usage policy (ED-022):

- identify the application in the User-Agent, with a contact email from NOMINATIM_CONTACT_EMAIL;
- at most 1 request per second, across every worker thread and process (a Postgres row lock);
- cache every response, including "not found", so a query is never repeated;
- show attribution: results carry "© OpenStreetMap contributors" (data under ODbL).

This is an external API call (OSM data), not a model of ours.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.models import GeocodeCache, RateLimit

PROVIDER = "nominatim"
ATTRIBUTION = "© OpenStreetMap contributors (data under the ODbL)"
APP_URL = "https://github.com/MhBaigtw/RentalScamChecker"

log = structlog.get_logger("rentalscamchecker.geocoding")


class GeocodingUnavailable(Exception):
    """The geocoder couldn't be reached or answered with an error. Retryable."""


@dataclass(frozen=True)
class GeocodeResult:
    lat: float
    lon: float
    display_name: str
    # OSM feature class and type, e.g. ("building", "residential"), ("amenity", "school").
    category: str
    type: str
    # Nominatim's address breakdown: house_number, road, suburb, city, town, state, ...
    address: dict[str, str] = field(default_factory=dict)


def query_key(address: str, city: str) -> str:
    """Normalized query text: the cache key and what is sent to the geocoder."""
    joined = f"{address}, {city}, Canada"
    return " ".join(re.sub(r"\s*,\s*", ", ", joined).split()).casefold()


class NominatimGeocoder:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        contact_email: str,
        base_url: str,
        min_interval_seconds: float,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._sessions = session_factory
        self._min_interval = min_interval_seconds
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={"User-Agent": f"RentalScamChecker/0.1 (+{APP_URL}; {contact_email})"},
            transport=transport,
        )

    def geocode(self, address: str, city: str) -> GeocodeResult | None:
        """Best match for the address, or None if nothing was found."""
        key = query_key(address, city)
        cached = self._cached(key)
        if cached is None:
            cached = self._fetch_rate_limited(key)
        else:
            log.info("geocode_cache_hit")
        return _parse(cached[0]) if cached else None

    def _cached(self, key: str) -> list[dict[str, Any]] | None:
        with self._sessions() as session:
            return session.scalar(
                select(GeocodeCache.response).where(
                    GeocodeCache.query_key == key, GeocodeCache.provider == PROVIDER
                )
            )

    def _fetch_rate_limited(self, key: str) -> list[dict[str, Any]]:
        with self._sessions.begin() as session:
            # Fail rather than queue forever behind other geocoding calls (job timeout is 45 s).
            session.execute(text("SET LOCAL lock_timeout = '20s'"))
            session.execute(
                insert(RateLimit)
                .values(name=PROVIDER, last_request_at=func.to_timestamp(0))
                .on_conflict_do_nothing()
            )
            # Holding this row lock across the HTTP call serializes all our Nominatim requests,
            # in every worker process; the sleep spaces them at least min_interval apart.
            last = session.scalar(
                select(RateLimit.last_request_at)
                .where(RateLimit.name == PROVIDER)
                .with_for_update()
            )
            now = session.scalar(select(func.clock_timestamp()))
            assert last is not None and now is not None  # noqa: S101
            wait = self._min_interval - (now - last).total_seconds()
            if wait > 0:
                time.sleep(wait)
            try:
                response = self._client.get(
                    "/search",
                    params={
                        "q": key,
                        "format": "jsonv2",
                        "addressdetails": 1,
                        "limit": 1,
                        "countrycodes": "ca",
                        "accept-language": "en",
                    },
                )
            except httpx.HTTPError as exc:  # timeout, refused connection, DNS failure, ...
                raise GeocodingUnavailable(f"Nominatim unreachable: {type(exc).__name__}") from exc
            finally:
                # Count the attempt even if it failed: a failed request still hit the server.
                session.execute(
                    update(RateLimit)
                    .where(RateLimit.name == PROVIDER)
                    .values(last_request_at=func.clock_timestamp())
                )
            if response.status_code != 200:
                raise GeocodingUnavailable(f"Nominatim answered HTTP {response.status_code}")
            results: list[dict[str, Any]] = response.json()
            session.execute(
                insert(GeocodeCache)
                .values(query_key=key, provider=PROVIDER, response=results)
                .on_conflict_do_nothing()
            )
        log.info("geocode_fetched", found=bool(results))
        return results

    def close(self) -> None:
        self._client.close()


def _parse(raw: dict[str, Any]) -> GeocodeResult:
    return GeocodeResult(
        lat=float(raw["lat"]),
        lon=float(raw["lon"]),
        display_name=str(raw.get("display_name", "")),
        category=str(raw.get("category", raw.get("class", ""))),
        type=str(raw.get("type", "")),
        address={k: str(v) for k, v in (raw.get("address") or {}).items()},
    )
