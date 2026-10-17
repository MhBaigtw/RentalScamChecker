"""The check graph (SPEC section 6, ED-026): which checks are connected through a shared photo or a
shared contact. DETERMINISTIC; plain SQL over Postgres tables, no graph database.

Nodes are checks. Two checks are linked when
- a photo of one matches a photo of the other under the M5 photo rule (photos/matching.py:
  perceptual hashes OR CLIP embedding). photo_reuse_internal stores each match in `photo_links`
  (both directions) when it runs, because recomputing image matches at read time would be slow;
- they have a phone number or email with the same HMAC in `check_contacts`, whether it was typed
  in the contact fields or found in the listing text (contacts.py). These need no extra table:
  `check_contacts` already is the check-to-contact edge list.

Queries:
- `related_checks`: checks one link away, with what links them (photo / phone / email).
- `cluster_size`: how many checks are reachable through any chain of links (a connected
  component), found breadth-first with two queries per level and a cap.

Other checks are never exposed by id: an id lets anyone fetch that check's listing (GET
/checks/{id}). Reports show `public_ref` instead, a short one-way tag.
"""

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rentalscamchecker.addresses import same_address
from rentalscamchecker.models import PhotoLink

# A report lists at most this many related checks (newest first); the total is still counted.
RELATED_LIMIT = 50
# The cluster search stops once it has seen this many checks; the report then says "at least".
CLUSTER_MAX_CHECKS = 500


def public_ref(check_id: uuid.UUID) -> str:
    """A stable short tag for showing another check in a report without revealing its id.

    SHA-256 is one-way and check ids are random UUIDs (122 random bits), so the tag can't be
    turned back into an id. 10 hex characters (40 bits) make accidental collisions negligible for
    the number of checks a single report shows.
    """
    return hashlib.sha256(b"check-ref:" + check_id.bytes).hexdigest()[:10]


@dataclass(frozen=True)
class RelatedCheck:
    check_id: uuid.UUID
    created_at: datetime
    city: str
    address_text: str
    risk_level: str | None
    # Subset of ("photo", "phone", "email"), sorted.
    linked_by: tuple[str, ...]
    photo_links: int
    same_address: bool


_RELATED_SQL = text(
    """
    WITH links AS (
        SELECT o.check_id AS other_id, o.kind AS via
        FROM check_contacts m
        JOIN check_contacts o ON o.hmac_hash = m.hmac_hash
        WHERE m.check_id = :check_id AND o.check_id <> :check_id
        UNION ALL
        SELECT other_check_id, 'photo'
        FROM photo_links
        WHERE check_id = :check_id
    )
    SELECT c.id, c.created_at, c.city, c.address_text, c.risk_level,
           array_agg(DISTINCT l.via ORDER BY l.via) AS linked_by,
           count(*) FILTER (WHERE l.via = 'photo') AS photo_links,
           count(*) OVER () AS total
    FROM links l
    JOIN checks c ON c.id = l.other_id
    GROUP BY c.id
    ORDER BY c.created_at DESC, c.id
    LIMIT :limit
    """
)


def related_checks(
    session: Session,
    check_id: uuid.UUID,
    address_text: str,
    city: str,
    limit: int = RELATED_LIMIT,
) -> tuple[list[RelatedCheck], int]:
    """Checks one link away from `check_id` (newest first, at most `limit`) and their total."""
    rows = session.execute(_RELATED_SQL, {"check_id": check_id, "limit": limit}).mappings().all()
    related = [
        RelatedCheck(
            check_id=r["id"],
            created_at=r["created_at"],
            city=r["city"],
            address_text=r["address_text"],
            risk_level=r["risk_level"],
            linked_by=tuple(r["linked_by"]),
            photo_links=r["photo_links"],
            same_address=same_address(address_text, city, r["address_text"], r["city"]),
        )
        for r in rows
    ]
    return related, (rows[0]["total"] if rows else 0)


# Cluster search, one level = two queries. Contacts are expanded as nodes of their own (each hash
# at most once), not as check-to-check pairs: 300 checks sharing one phone would otherwise be
# 300 x 300 joined rows at the second level (measured: eval/reports/graph_explain.md).
_CONTACTS_OF_SQL = text(
    "SELECT DISTINCT hmac_hash FROM check_contacts WHERE check_id = ANY(:checks)"
)
_CHECKS_LINKED_SQL = text(
    """
    SELECT check_id
    FROM check_contacts
    WHERE hmac_hash = ANY(:hashes)
    UNION
    SELECT other_check_id
    FROM photo_links
    WHERE check_id = ANY(:checks)
    """
)


@dataclass(frozen=True)
class ClusterSize:
    # Checks in the connected component, including the starting check.
    size: int
    # True when the search stopped at the cap: the real size is at least `size`.
    capped: bool


def cluster_size(
    session: Session, check_id: uuid.UUID, max_checks: int = CLUSTER_MAX_CHECKS
) -> ClusterSize:
    """Breadth-first search over a graph of checks and contacts (photos link checks directly).

    Each level asks for the neighbours of all checks found in the previous level at once, so the
    number of queries grows with the depth of the cluster, not its size. `seen_checks` and
    `seen_hashes` make cycles harmless and expand every check and contact once.
    """
    seen_checks = {check_id}
    seen_hashes: set[bytes] = set()
    frontier = [check_id]
    while frontier:
        hashes = session.execute(_CONTACTS_OF_SQL, {"checks": frontier}).scalars().all()
        new_hashes = [h for h in hashes if h not in seen_hashes]
        seen_hashes.update(new_hashes)
        found = session.execute(
            _CHECKS_LINKED_SQL, {"hashes": new_hashes, "checks": frontier}
        ).scalars()
        frontier = [c for c in found if c not in seen_checks]
        seen_checks.update(frontier)
        if len(seen_checks) >= max_checks:
            return ClusterSize(size=max_checks, capped=True)
    return ClusterSize(size=len(seen_checks), capped=False)


@dataclass(frozen=True)
class NewPhotoLink:
    photo_id: uuid.UUID
    check_id: uuid.UUID
    other_photo_id: uuid.UUID
    other_check_id: uuid.UUID
    match_type: str
    phash_distance: int | None
    dhash_distance: int | None
    cosine: float | None


def store_photo_links(session: Session, links: Sequence[NewPhotoLink]) -> None:
    """Insert each link in both directions. Idempotent: a re-run of the signal (at-least-once job
    delivery, ED-017) finds the rows already there and changes nothing."""
    rows = []
    for link in links:
        common = {
            "match_type": link.match_type,
            "phash_distance": link.phash_distance,
            "dhash_distance": link.dhash_distance,
            "cosine": link.cosine,
        }
        rows.append(
            {
                "photo_id": link.photo_id,
                "other_photo_id": link.other_photo_id,
                "check_id": link.check_id,
                "other_check_id": link.other_check_id,
                **common,
            }
        )
        rows.append(
            {
                "photo_id": link.other_photo_id,
                "other_photo_id": link.photo_id,
                "check_id": link.other_check_id,
                "other_check_id": link.check_id,
                **common,
            }
        )
    if rows:
        session.execute(insert(PhotoLink).values(rows).on_conflict_do_nothing())
