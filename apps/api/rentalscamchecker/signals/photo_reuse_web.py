"""photo_reuse_web: do the listing's photos appear elsewhere on the web? (SPEC 4)

An EXTERNAL PAID API (TinEye reverse image search) behind TINEYE_ENABLED, which is OFF by default.
When off, the signal returns `skipped` without any network call. When on: at most
TINEYE_MAX_PHOTOS_PER_CHECK photos are searched, results are cached by image hash, and a daily
cap on paid searches applies (photos/web_search.py). The TinEye adapter is unverified against the
live API (see that module).

Severity: photos found on other websites -> medium (it can be legitimate, e.g. the landlord's own
listing on another site, so it is not "high" on its own).
"""

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.models import CheckPhoto
from rentalscamchecker.photos.web_search import CachedCappedSearch, WebMatch, WebSearchUnavailable
from rentalscamchecker.signals.base import CheckInput, Severity, SignalContext, SignalResult
from rentalscamchecker.storage import PhotoStorage

log = structlog.get_logger("rentalscamchecker.photo_reuse_web")

DIY_NEXT_STEP = (
    "You can search the photos yourself with Google Lens or TinEye's website: if they show up in "
    "other listings or for other cities, ask the landlord why."
)


class PhotoReuseWebSignal:
    name = "photo_reuse_web"
    version = "1"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        storage: PhotoStorage,
        search: CachedCappedSearch | None,
        max_photos: int,
    ) -> None:
        """`search` None means the feature flag is off."""
        self._sessions = session_factory
        self._storage = storage
        self._search = search
        self._max_photos = max_photos

    def run(self, check: CheckInput, ctx: SignalContext) -> SignalResult:
        if self._search is None:
            return self._skipped("disabled", "Searching the web for these photos is turned off.")
        with self._sessions() as session:
            keys = list(
                session.scalars(
                    select(CheckPhoto.storage_key)
                    .where(CheckPhoto.check_id == check.check_id)
                    .order_by(CheckPhoto.position)
                )
            )
        searched: list[tuple[int, bool, list[WebMatch]]] = []
        for position, key in enumerate(keys[: self._max_photos], start=1):
            try:
                matches, cached = self._search.search(self._storage.get(key))
            except WebSearchUnavailable as exc:
                log.warning("web_search_unavailable", reason=exc.reason)
                if not searched:
                    return self._skipped(exc.reason, "The web photo search wasn't available.")
                break
            searched.append((position, cached, matches))
        found = [(pos, ms) for pos, _, ms in searched if ms]
        domains = sorted({m.domain for _, ms in found for m in ms if m.domain})
        evidence: dict[str, object] = {
            "method": "external_api:tineye",
            "photos_searched": len(searched),
            "photos_not_searched": max(0, len(keys) - len(searched)),
            "results": [
                {
                    "photo": pos,
                    "cached": cached,
                    "matches": len(ms),
                    "domains": sorted({m.domain for m in ms if m.domain})[:10],
                    "examples": [m.as_dict() for m in ms[:3]],
                }
                for pos, cached, ms in searched
            ],
        }
        severity: Severity
        if found:
            explanation = (
                f"We searched the web for {len(searched)} of the photos. "
                f"{len(found)} of them also appear on other websites ({', '.join(domains[:5])})."
            )
            next_step = (
                "Open those pages: if the same photos are used for another address or another "
                "landlord, ask why before paying anything."
            )
            severity = "medium"
        else:
            explanation = (
                f"We searched the web for {len(searched)} of the photos and found no copies."
            )
            next_step = DIY_NEXT_STEP
            severity = "none"
        return SignalResult(
            name=self.name,
            version=self.version,
            status="ok",
            severity=severity,
            evidence=evidence,
            explanation=explanation,
            next_step=next_step,
        )

    def _skipped(self, reason: str, explanation: str) -> SignalResult:
        return SignalResult(
            name=self.name,
            version=self.version,
            status="skipped",
            severity=None,
            evidence={"method": "external_api:tineye", "reason": reason},
            explanation=explanation,
            next_step=DIY_NEXT_STEP,
        )
