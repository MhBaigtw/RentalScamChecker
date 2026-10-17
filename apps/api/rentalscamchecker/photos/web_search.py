"""Reverse image search on the web: an EXTERNAL PAID API (TinEye), behind a feature flag that is
OFF by default (CLAUDE.md: paid services need Humayun's approval and a daily spend cap).

STATUS: UNVERIFIED AGAINST THE LIVE API. No TinEye account exists for this project and no request
has ever been sent. The response shape (results.matches[] with image_url, domain, score and
backlinks[] of url/backlink/crawl_date) follows TinEye's published client libraries; the request
(POST {api_url}search/ with the image as multipart field "image_upload" and the key in the
"x-api-key" header) follows the official Python client's convention but couldn't be checked
against TinEye's reference (their site didn't resolve from the dev machine). Verify both before
turning the flag on. Tests use fakes only.
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.models import WebImageSearch, WebImageSearchCache

PROVIDER = "tineye"
_CAP_LOCK_KEY = 7_402_311
APP_UA = "RentalScamChecker/0.1 (+https://github.com/MhBaigtw/RentalScamChecker)"


class WebSearchUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class WebMatch:
    image_url: str
    domain: str
    score: float | None
    backlinks: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_url": self.image_url,
            "domain": self.domain,
            "score": self.score,
            "backlinks": self.backlinks,
        }


class ReverseImageSearchClient(Protocol):
    def search(self, image_bytes: bytes) -> list[WebMatch]: ...


class TinEyeClient:
    def __init__(
        self,
        api_key: str,
        api_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=api_url,
            timeout=timeout_seconds,
            headers={"x-api-key": api_key, "User-Agent": APP_UA},
            transport=transport,
        )

    def search(self, image_bytes: bytes) -> list[WebMatch]:
        try:
            response = self._client.post(
                "search/", files={"image_upload": ("photo.jpg", image_bytes, "image/jpeg")}
            )
        except httpx.HTTPError as exc:
            raise WebSearchUnavailable(f"connection: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise WebSearchUnavailable(f"http_{response.status_code}")
        return parse_matches(response.json())


def parse_matches(body: dict[str, Any]) -> list[WebMatch]:
    matches = (body.get("results") or {}).get("matches") or []
    out = []
    for m in matches:
        backlinks = [str(b.get("backlink") or b.get("url") or "") for b in m.get("backlinks") or []]
        out.append(
            WebMatch(
                image_url=str(m.get("image_url", "")),
                domain=str(m.get("domain", "")),
                score=float(m["score"]) if m.get("score") is not None else None,
                backlinks=[b for b in backlinks if b][:5],
            )
        )
    return out


class CachedCappedSearch:
    """Cache by image SHA-256, and a daily cap on paid searches reserved under an advisory lock
    (the same pattern as the Anthropic daily cap, ED-024)."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        client: ReverseImageSearchClient,
        *,
        daily_cap: int,
        cap_lock_wait_seconds: float,
    ) -> None:
        self._sessions = session_factory
        self._client = client
        self._cap = daily_cap
        self._lock_wait_ms = int(cap_lock_wait_seconds * 1000)

    def search(self, image_bytes: bytes) -> tuple[list[WebMatch], bool]:
        """Returns (matches, served_from_cache). Raises WebSearchUnavailable."""
        digest = hashlib.sha256(image_bytes).digest()
        with self._sessions() as session:
            cached = session.scalar(
                select(WebImageSearchCache.matches).where(
                    WebImageSearchCache.image_sha256 == digest,
                    WebImageSearchCache.provider == PROVIDER,
                )
            )
        if cached is not None:
            return [WebMatch(**m) for m in cached], True
        call_id = self._reserve(digest)
        try:
            matches = self._client.search(image_bytes)
        except WebSearchUnavailable as exc:
            self._finish(call_id, "error", None, exc.reason)
            raise
        self._finish(call_id, "ok", len(matches), None)
        with self._sessions.begin() as session:
            session.execute(
                insert(WebImageSearchCache)
                .values(
                    image_sha256=digest,
                    provider=PROVIDER,
                    matches=[m.as_dict() for m in matches],
                )
                .on_conflict_do_nothing()
            )
        return matches, False

    def _reserve(self, digest: bytes) -> int:
        with self._sessions.begin() as session:
            session.execute(text(f"SET LOCAL lock_timeout = {self._lock_wait_ms}"))
            session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CAP_LOCK_KEY})
            today = session.scalar(
                select(func.count())
                .select_from(WebImageSearch)
                .where(WebImageSearch.called_at >= func.date_trunc("day", func.now(), "UTC"))
            )
            if (today or 0) >= self._cap:
                raise WebSearchUnavailable("daily_cap_reached")
            call_id = session.scalar(
                insert(WebImageSearch)
                .values(provider=PROVIDER, image_sha256=digest)
                .returning(WebImageSearch.id)
            )
        return int(call_id or 0)

    def _finish(self, call_id: int, outcome: str, count: int | None, error: str | None) -> None:
        with self._sessions.begin() as session:
            session.execute(
                update(WebImageSearch)
                .where(WebImageSearch.id == call_id)
                .values(outcome=outcome, result_count=count, error=error)
            )
