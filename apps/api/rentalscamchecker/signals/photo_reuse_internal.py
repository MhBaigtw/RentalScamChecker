"""photo_reuse_internal: have these photos been submitted before, for a different place?

Combines DETERMINISTIC perceptual hashes with a PRETRAINED MODEL WE RUN (CLIP ViT-B/32, ONNX,
on CPU) (SPEC 4, ED-025). For each photo of the check:
1. compute and store pHash, dHash and the embedding (skipped if already stored: re-runs are cheap
   and idempotent);
2. find photos of OTHER checks that match under photos/matching.py: a Postgres bit_count scan for
   the hashes, the pgvector HNSW index for the embedding;
3. store every match in photo_links (both directions) for the check graph (graph.py, ED-026);
4. keep matches whose check is for a different street address or city, or was rated high risk.
   Matches at the same address are reported as information (a landlord re-posting their own
   listing is normal), not as a warning.

Other checks appear in the evidence by `public_ref` only, never by id (graph.py explains why).

Severity: a hash match at another address -> high; an embedding-only match (less certain) ->
medium; otherwise none.
"""

import io
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import structlog
from PIL import Image
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.addresses import same_address
from rentalscamchecker.graph import NewPhotoLink, public_ref, store_photo_links
from rentalscamchecker.models import CheckPhoto
from rentalscamchecker.photos.embeddings import ClipEmbedder
from rentalscamchecker.photos.hashing import hash_image
from rentalscamchecker.photos.matching import DEFAULT, Thresholds
from rentalscamchecker.signals.base import CheckInput, Severity, SignalContext, SignalResult
from rentalscamchecker.storage import PhotoStorage

log = structlog.get_logger("rentalscamchecker.photo_reuse_internal")

# Nearest neighbours fetched per photo from the HNSW index before applying the threshold.
EMBEDDING_CANDIDATES = 20

_HASH_SQL = text(
    """
    SELECT p.id AS photo_id, p.check_id, c.city, c.address_text, c.risk_level, c.created_at,
           bit_count((p.phash # :phash)::bit(64)) AS phash_distance,
           bit_count((p.dhash # :dhash)::bit(64)) AS dhash_distance
    FROM check_photos p JOIN checks c ON c.id = p.check_id
    WHERE p.check_id <> :check_id AND p.phash IS NOT NULL AND p.dhash IS NOT NULL
      AND (bit_count((p.phash # :phash)::bit(64)) <= :single
           OR bit_count((p.dhash # :dhash)::bit(64)) <= :single
           OR (bit_count((p.phash # :phash)::bit(64)) <= :both
               AND bit_count((p.dhash # :dhash)::bit(64)) <= :both))
    """
)
_EMBEDDING_SQL = text(
    """
    SELECT p.id AS photo_id, p.check_id, c.city, c.address_text, c.risk_level, c.created_at,
           p.phash, p.dhash, 1 - (p.embedding <=> CAST(:embedding AS vector)) AS cosine
    FROM check_photos p JOIN checks c ON c.id = p.check_id
    WHERE p.check_id <> :check_id AND p.embedding IS NOT NULL
    ORDER BY p.embedding <=> CAST(:embedding AS vector)
    LIMIT :k
    """
)


@dataclass
class Match:
    position: int
    photo_id: uuid.UUID
    other_photo_id: uuid.UUID
    other_check_id: uuid.UUID
    other_city: str
    other_same_address: bool
    other_high_risk: bool
    kind: str
    phash_distance: int | None
    dhash_distance: int | None
    cosine: float | None


class PhotoReuseInternalSignal:
    name = "photo_reuse_internal"
    # 2 (M6): matches are stored in photo_links; evidence shows public_ref instead of check ids.
    version = "2"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        storage: PhotoStorage,
        embedder: ClipEmbedder,
        thresholds: Thresholds = DEFAULT,
    ) -> None:
        self._sessions = session_factory
        self._storage = storage
        self._embedder = embedder
        self._t = thresholds

    def run(self, check: CheckInput, ctx: SignalContext) -> SignalResult:
        photos = self._fingerprint(check)
        matches = self._find_matches(check, photos)
        with self._sessions.begin() as session:
            store_photo_links(session, [_link(check, m) for m in matches])
        return _result(self, len(photos), matches)

    def _fingerprint(self, check: CheckInput) -> list[CheckPhoto]:
        with self._sessions() as session:
            photos = list(
                session.scalars(
                    select(CheckPhoto)
                    .where(CheckPhoto.check_id == check.check_id)
                    .order_by(CheckPhoto.position)
                )
            )
        missing = [p for p in photos if p.phash is None or p.dhash is None or p.embedding is None]
        if missing:
            images = [_load(self._storage.get(p.storage_key)) for p in missing]
            vectors = self._embedder.embed_images(images)
            with self._sessions.begin() as session:
                for photo, image, vector in zip(missing, images, vectors, strict=True):
                    hashes = hash_image(image)
                    photo.phash, photo.dhash = hashes.phash, hashes.dhash
                    photo.embedding = vector
                    session.execute(
                        update(CheckPhoto)
                        .where(CheckPhoto.id == photo.id)
                        .values(phash=hashes.phash, dhash=hashes.dhash, embedding=vector)
                    )
            log.info("photos_fingerprinted", count=len(missing))
        return photos

    def _find_matches(self, check: CheckInput, photos: list[CheckPhoto]) -> list[Match]:
        found: dict[tuple[int, Any], Match] = {}
        with self._sessions() as session:
            for photo in photos:
                if photo.phash is None or photo.dhash is None or photo.embedding is None:
                    continue  # fingerprinted above; only a concurrent delete could cause this
                rows = session.execute(
                    _HASH_SQL,
                    {
                        "check_id": check.check_id,
                        "phash": photo.phash,
                        "dhash": photo.dhash,
                        "single": self._t.single_hash,
                        "both": self._t.both_hashes,
                    },
                ).mappings()
                for r in rows:
                    found[(photo.position, r["photo_id"])] = _match(
                        check,
                        photo,
                        r,
                        "hash",
                        r["phash_distance"],
                        r["dhash_distance"],
                        None,
                    )
                # pgvector's text format, e.g. "[0.012,-0.034,...]"; cast to vector in the SQL.
                embedding = "[" + ",".join(f"{x:.6f}" for x in photo.embedding) + "]"
                rows = session.execute(
                    _EMBEDDING_SQL,
                    {"check_id": check.check_id, "embedding": embedding, "k": EMBEDDING_CANDIDATES},
                ).mappings()
                for r in rows:
                    cosine = float(r["cosine"])
                    key = (photo.position, r["photo_id"])
                    if key in found:
                        found[key].cosine = round(cosine, 4)
                        continue
                    if cosine >= self._t.cosine:  # no hash match for this pair: embedding only
                        found[key] = _match(check, photo, r, "embedding", None, None, cosine)
        return sorted(
            found.values(), key=lambda m: (m.position, m.kind != "hash", m.other_check_id)
        )


def _load(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGB")


def _match(
    check: CheckInput,
    photo: CheckPhoto,
    row: Any,
    kind: str,
    phash_distance: int | None,
    dhash_distance: int | None,
    cosine: float | None,
) -> Match:
    return Match(
        position=photo.position,
        photo_id=photo.id,
        other_photo_id=row["photo_id"],
        other_check_id=row["check_id"],
        other_city=row["city"],
        other_same_address=same_address(
            check.address_text, check.city, row["address_text"], row["city"]
        ),
        other_high_risk=row["risk_level"] == "high",
        kind=kind,
        phash_distance=phash_distance,
        dhash_distance=dhash_distance,
        cosine=round(cosine, 4) if cosine is not None else None,
    )


def _link(check: CheckInput, m: Match) -> NewPhotoLink:
    return NewPhotoLink(
        photo_id=m.photo_id,
        check_id=check.check_id,
        other_photo_id=m.other_photo_id,
        other_check_id=m.other_check_id,
        match_type=m.kind,
        phash_distance=m.phash_distance,
        dhash_distance=m.dhash_distance,
        cosine=m.cosine,
    )


def _result(
    signal: PhotoReuseInternalSignal, photo_count: int, matches: list[Match]
) -> SignalResult:
    concerning = [m for m in matches if not m.other_same_address or m.other_high_risk]
    same_place = [m for m in matches if m.other_same_address and not m.other_high_risk]
    severity: Severity = "none"
    if any(m.kind == "hash" for m in concerning):
        severity = "high"
    elif concerning:
        severity = "medium"
    by_check: dict[uuid.UUID, set[int]] = defaultdict(set)
    for m in concerning:
        by_check[m.other_check_id].add(m.position)
    cities = sorted({m.other_city for m in concerning})
    evidence: dict[str, object] = {
        "method": "perceptual_hash+clip_embedding",
        "photos_checked": photo_count,
        "matches": [
            {
                "photo": m.position + 1,
                "matched_check": public_ref(m.other_check_id),
                "matched_city": m.other_city,
                "same_address": m.other_same_address,
                "earlier_check_high_risk": m.other_high_risk,
                "match_type": m.kind,
                "phash_distance": m.phash_distance,
                "dhash_distance": m.dhash_distance,
                "cosine_similarity": m.cosine,
            }
            for m in matches
        ],
        "other_checks_at_different_address": len(by_check),
        "other_cities": cities,
        "same_address_matches": len(same_place),
    }
    if concerning:
        photos = sorted({m.position + 1 for m in concerning})
        where = ", ".join(cities)
        plural = len(photos) > 1
        numbers = (
            " and ".join(map(str, photos))
            if len(photos) <= 2
            else (", ".join(map(str, photos[:-1])) + f" and {photos[-1]}")
        )
        if severity == "high":
            strength = (
                "match earlier photos almost exactly"
                if plural
                else ("matches an earlier photo almost exactly")
            )
        else:
            strength = (
                "look very much like earlier photos"
                if plural
                else ("looks very much like an earlier photo")
            )
        explanation = (
            f"We compared the photos with all photos submitted to this service before. "
            f"Photo{'s' if plural else ''} {numbers} {strength} from {len(by_check)} other "
            f"check{'s' if len(by_check) > 1 else ''} for a different address ({where}). Photos "
            "copied from another place are a common sign that a listing isn't what it claims."
        )
        next_step = (
            "Ask the landlord for a live video call walking through the unit, or visit in person, "
            "before paying anything. You can also search the photos yourself with Google Lens or "
            "TinEye."
        )
    elif same_place:
        explanation = (
            "We compared the photos with all photos submitted to this service before. Some were "
            "seen in earlier checks for the same address, which is normal when a listing is "
            "posted again. None appeared for a different address."
        )
        next_step = "Nothing to do about the photos. Still see the place before paying anything."
    else:
        explanation = (
            f"We compared the {photo_count} photo{'s' if photo_count != 1 else ''} with all photos "
            "submitted to this service before and found no copies used for a different address. "
            "This only covers photos we have seen; it can't rule out photos copied from elsewhere "
            "on the web."
        )
        next_step = (
            "Nothing to do about these photos. For extra certainty, search them yourself with "
            "Google Lens or TinEye."
        )
    return SignalResult(
        name=signal.name,
        version=signal.version,
        status="ok",
        severity=severity,
        evidence=evidence,
        explanation=explanation,
        next_step=next_step,
    )
