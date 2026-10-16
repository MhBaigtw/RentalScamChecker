"""A fake Nominatim for tests: an httpx MockTransport that records every request.

The response bodies follow Nominatim's `format=jsonv2` shape, but they are hand-written test
fixtures, not recorded API output.
"""

import threading
import time
from typing import Any

import httpx


def place(
    *,
    city: str,
    category: str = "building",
    type_: str = "residential",
    road: str = "Trafalgar Road",
    house_number: str | None = "1430",
    extra_address: dict[str, str] | None = None,
    lat: str = "43.4675",
    lon: str = "-79.6877",
) -> dict[str, Any]:
    address: dict[str, str] = {"road": road, "city": city, "state": "Ontario", "country": "Canada"}
    if house_number:
        address["house_number"] = house_number
    address.update(extra_address or {})
    name = ", ".join(v for v in (house_number, road, city, "Ontario", "Canada") if v)
    return {
        "lat": lat,
        "lon": lon,
        "category": category,
        "type": type_,
        "display_name": name,
        "address": address,
    }


class FakeNominatim:
    """Answers /search with `results_by_query[q]` (default: no results)."""

    def __init__(
        self, results_by_query: dict[str, list[dict[str, Any]]] | None = None, status: int = 200
    ) -> None:
        self.results_by_query = results_by_query or {}
        self.status = status
        self.requests: list[httpx.Request] = []
        self.request_times: list[float] = []
        self._lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.requests.append(request)
            self.request_times.append(time.monotonic())
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "fake failure"})
        q = request.url.params["q"]
        return httpx.Response(200, json=self.results_by_query.get(q, []))

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
