"""SQLAlchemy ORM models for the SPEC.md section 8 data model.

Not here yet: `users`, `outcomes` and `disputes` (M8). See docs/database-design.md.

Enumerated values are TEXT columns with CHECK constraints rather than Postgres ENUM types:
adding a value later is one constraint swap instead of an ALTER TYPE (ED-012).

Postgres doesn't index foreign keys automatically. Each child table's unique constraint starts
with check_id, and that unique index also serves "all rows for this check" lookups.
"""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Predictable constraint names, so migrations can refer to them (and autogenerate is stable).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

CHECK_STATUSES = ("pending", "running", "complete")
# What is being rented: a whole unit (apartment, house, condo), a room in a shared place, or a
# basement apartment. CMHC averages describe whole purpose-built units only (ED-021).
UNIT_TYPES = ("whole_unit", "room", "basement")
RISK_LEVELS = ("low", "some", "high", "incomplete")
CONTACT_KINDS = ("phone", "email")
CONTACT_SOURCES = ("field", "text")
SIGNAL_STATUSES = ("ok", "skipped", "error")
SEVERITIES = ("none", "low", "medium", "high")
JOB_STATUSES = ("queued", "running", "done", "failed", "dead")
LLM_CALL_OUTCOMES = ("started", "ok", "refused", "truncated", "invalid", "error")
GEOGRAPHY_TYPES = ("cma", "csd")
RENT_BASES = ("vacant_units", "all_units")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Check(Base):
    """One submitted listing and, later, its risk report."""

    __tablename__ = "checks"
    __table_args__ = (
        CheckConstraint(_in("status", CHECK_STATUSES), name="status"),
        CheckConstraint(_in("risk_level", RISK_LEVELS), name="risk_level"),
        CheckConstraint("rent_cents > 0", name="rent_positive"),
        CheckConstraint("bedrooms BETWEEN 0 AND 10", name="bedrooms_range"),
        CheckConstraint(_in("unit_type", UNIT_TYPES), name="unit_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    risk_level: Mapped[str | None] = mapped_column(Text)
    scoring_version: Mapped[str | None] = mapped_column(Text)
    # No foreign key until the users table exists (M8).
    user_id: Mapped[uuid.UUID | None]
    # Stored as text only; never fetched (no scraping, CLAUDE.md product rules).
    submitted_url: Mapped[str | None] = mapped_column(Text)
    listing_text: Mapped[str] = mapped_column(Text)
    rent_cents: Mapped[int] = mapped_column(Integer)
    # 0 means a studio/bachelor unit.
    bedrooms: Mapped[int] = mapped_column(SmallInteger)
    unit_type: Mapped[str] = mapped_column(Text, server_default="whole_unit")
    address_text: Mapped[str] = mapped_column(Text)
    city: Mapped[str] = mapped_column(Text)
    # Filled by the geocoder in M3.
    geocoded_lat: Mapped[float | None] = mapped_column(Double)
    geocoded_lon: Mapped[float | None] = mapped_column(Double)
    geocoded_city: Mapped[str | None] = mapped_column(Text)
    geocoded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    photos: Mapped[list["CheckPhoto"]] = relationship(
        back_populates="check", order_by="CheckPhoto.position"
    )
    contacts: Mapped[list["CheckContact"]] = relationship(back_populates="check")
    signal_results: Mapped[list["SignalResult"]] = relationship(back_populates="check")


# CLIP ViT-B/32 image embeddings (photos/embeddings.py, ED-025).
EMBEDDING_DIMENSIONS = 512


class CheckPhoto(Base):
    __tablename__ = "check_photos"
    __table_args__ = (
        UniqueConstraint("check_id", "position", name="uq_check_photos_check_id_position"),
        # Approximate nearest-neighbour search by cosine distance (pgvector HNSW).
        Index(
            "ix_check_photos_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"))
    # Order the user uploaded them in, 0-based.
    position: Mapped[int] = mapped_column(SmallInteger)
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    # 64-bit perceptual hashes (photos/hashing.py), as signed BIGINT (Postgres has no unsigned).
    # NULL until photo_reuse_internal has processed the photo.
    phash: Mapped[int | None] = mapped_column(BigInteger)
    dhash: Mapped[int | None] = mapped_column(BigInteger)
    # L2-normalised CLIP embedding; NULL until processed.
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)

    check: Mapped[Check] = relationship(back_populates="photos")


class CheckContact(Base):
    """A phone number or email from a check, stored only as an HMAC (ED-004, ED-016)."""

    __tablename__ = "check_contacts"
    __table_args__ = (
        CheckConstraint(_in("kind", CONTACT_KINDS), name="kind"),
        CheckConstraint(_in("source", CONTACT_SOURCES), name="source"),
        CheckConstraint("octet_length(hmac_hash) = 32", name="hmac_hash_length"),
        # One row per distinct contact per check, however many times it appears.
        UniqueConstraint(
            "check_id", "kind", "hmac_hash", name="uq_check_contacts_check_id_kind_hmac_hash"
        ),
        # The contact graph (M6): "which checks used this contact?". check_id is included so the
        # lookup is an index-only scan (EXPLAIN in docs/database-design.md). kind isn't needed:
        # the HMAC input starts with the kind, so equal hashes always have equal kinds.
        Index("ix_check_contacts_hmac_hash_check_id", "hmac_hash", "check_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    # "field": the contact phone/email form fields. "text": found inside the listing text.
    # If a value is in both, the single row says "field".
    source: Mapped[str] = mapped_column(Text)
    hmac_hash: Mapped[bytes] = mapped_column(LargeBinary)

    check: Mapped[Check] = relationship(back_populates="contacts")


PHOTO_MATCH_TYPES = ("hash", "embedding")


class PhotoLink(Base):
    """A pair of photos from two different checks that match under the photo rule of M5
    (photos/matching.py: perceptual hashes or CLIP embedding, ED-025). Written by
    photo_reuse_internal; read by the check graph (graph.py, ED-026).

    Stored in both directions (A->B and B->A), like an adjacency list, so "links of check X" is
    one index range scan on check_id. check_id/other_check_id duplicate what the photo rows
    already say, so graph queries don't need to join check_photos.
    """

    __tablename__ = "photo_links"
    __table_args__ = (
        CheckConstraint(_in("match_type", PHOTO_MATCH_TYPES), name="match_type"),
        CheckConstraint("check_id <> other_check_id", name="different_checks"),
        Index("ix_photo_links_check_id_other_check_id", "check_id", "other_check_id"),
    )

    photo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("check_photos.id", ondelete="CASCADE"), primary_key=True
    )
    other_photo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("check_photos.id", ondelete="CASCADE"), primary_key=True
    )
    check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"))
    other_check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"))
    match_type: Mapped[str] = mapped_column(Text)
    phash_distance: Mapped[int | None] = mapped_column(SmallInteger)
    dhash_distance: Mapped[int | None] = mapped_column(SmallInteger)
    cosine: Mapped[float | None] = mapped_column(Double)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SignalResult(Base):
    __tablename__ = "signal_results"
    __table_args__ = (
        CheckConstraint(_in("status", SIGNAL_STATUSES), name="status"),
        CheckConstraint(_in("severity", SEVERITIES), name="severity"),
        UniqueConstraint(
            "check_id", "name", "version", name="uq_signal_results_check_id_name_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    # NULL when the signal errored or was skipped.
    severity: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    explanation: Mapped[str | None] = mapped_column(Text)
    next_step: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    check: Mapped[Check] = relationship(back_populates="signal_results")


class Job(Base):
    """Background job queue row (ED-002, ED-017). Repository: jobs/queue.py.

    Statuses: queued -> running -> done; a failed attempt -> failed (retried at run_after);
    attempts used up or a permanent error -> dead (dead-letter, kept for inspection)."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="status"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # The claim query: due jobs in run_after order. Partial, so the index only holds jobs
        # that can still be claimed; done/dead rows (the vast majority over time) stay out.
        Index(
            "ix_jobs_claimable",
            "run_after",
            "id",
            postgresql_where=text("status IN ('queued', 'failed')"),
        ),
        # The lease reaper: running jobs by lease expiry.
        Index("ix_jobs_running_lease", "locked_until", postgresql_where=text("status = 'running'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="5")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # onupdate only fires for ORM updates; raw SQL updates (M2's claim query) must set it.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReferenceRent(Base):
    """CMHC Rental Market Survey average rent for one geography, rent basis, bedroom type and
    survey (M3).

    Geographies: a census metropolitan area (`cma`) or a municipality (census subdivision,
    `csd`); `source_geography` is the CMHC row the value was read from (a municipality may share a
    CMHC zone with its neighbours). Rent basis: `vacant_units` (asking rents of empty units) or
    `all_units` (overall average, mostly sitting tenants). Suppressed values (CMHC "**") are kept
    with NULL rent and reliability, so the row still links the municipality to its CMA (ED-021).
    """

    __tablename__ = "reference_rents"
    __table_args__ = (
        CheckConstraint(_in("geography_type", GEOGRAPHY_TYPES), name="geography_type"),
        CheckConstraint(_in("rent_basis", RENT_BASES), name="rent_basis"),
        CheckConstraint("bedrooms BETWEEN 0 AND 3", name="bedrooms_range"),
        CheckConstraint("survey_month BETWEEN 1 AND 12", name="survey_month_range"),
        CheckConstraint("reliability IN ('a', 'b', 'c', 'd')", name="reliability"),
        CheckConstraint(
            "(avg_rent_cents IS NULL) = (reliability IS NULL)", name="suppressed_consistently"
        ),
        UniqueConstraint(
            "geography_type",
            "geography_code",
            "rent_basis",
            "bedrooms",
            "survey_year",
            "survey_month",
            name="uq_reference_rents_geography_basis_bedrooms_survey",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    geography_type: Mapped[str] = mapped_column(Text)
    # StatCan CSD code for municipalities; HMIP's own id for CMAs.
    geography_code: Mapped[str] = mapped_column(Text)
    geography_name: Mapped[str] = mapped_column(Text)
    cma_name: Mapped[str] = mapped_column(Text)
    # The CMHC table row the value comes from, e.g. "Zone 29 - Milton/Halton Hills".
    source_geography: Mapped[str] = mapped_column(Text)
    rent_basis: Mapped[str] = mapped_column(Text)
    # CMHC buckets: 0 = studio/bachelor, 1, 2, 3 = "3 bedroom +".
    bedrooms: Mapped[int] = mapped_column(SmallInteger)
    avg_rent_cents: Mapped[int | None] = mapped_column(Integer)
    # CMHC letter code: a excellent, b very good, c good, d poor (use with caution).
    reliability: Mapped[str | None] = mapped_column(Text)
    survey_year: Mapped[int] = mapped_column(SmallInteger)
    survey_month: Mapped[int] = mapped_column(SmallInteger)
    source: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)


class GeocodeCache(Base):
    """Raw geocoder responses by normalized query, including empty ones (M3). Nominatim's usage
    policy requires caching and forbids repeating the same query. Data: OpenStreetMap, ODbL."""

    __tablename__ = "geocode_cache"

    query_key: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    response: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateLimit(Base):
    """Last request time per external service, shared by every worker process (M3). Callers lock
    the row, wait until the minimum interval has passed, call the service, then update it."""

    __tablename__ = "rate_limits"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_request_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    """One row per worker process: liveness and counters, written every few seconds by the worker
    and read by the API's /internal/metrics (M3). Rows of stopped workers keep stopped_at."""

    __tablename__ = "worker_heartbeats"

    # "<hostname>:<pid>:<random>", unique per process start.
    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    concurrency: Mapped[int] = mapped_column(Integer)
    jobs_done_total: Mapped[int] = mapped_column(Integer)
    jobs_failed_total: Mapped[int] = mapped_column(Integer)
    jobs_timed_out_total: Mapped[int] = mapped_column(Integer)
    abandoned_threads_alive: Mapped[int] = mapped_column(Integer)


class LlmCall(Base):
    """One row per Anthropic API call attempt (M4, ED-024): the daily cap counts these rows, and
    the token counts make spend auditable. Reserved (outcome 'started') before the request."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint(_in("outcome", LLM_CALL_OUTCOMES), name="outcome"),
        Index("ix_llm_calls_called_at", "called_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, server_default="started")
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class WebImageSearch(Base):
    """One row per paid reverse-image-search call (photo_reuse_web, M5): the daily cap counts these
    rows. Reserved (outcome 'started') before the request, like llm_calls."""

    __tablename__ = "web_image_searches"
    __table_args__ = (
        CheckConstraint(_in("outcome", ("started", "ok", "error")), name="outcome"),
        Index("ix_web_image_searches_called_at", "called_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    provider: Mapped[str] = mapped_column(Text)
    image_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    outcome: Mapped[str] = mapped_column(Text, server_default="started")
    result_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class WebImageSearchCache(Base):
    """Normalised reverse-image-search results by image hash and provider (photo_reuse_web)."""

    __tablename__ = "web_image_search_cache"

    image_sha256: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    matches: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmCache(Base):
    """Cached LLM responses keyed by content hash, prompt version and model (M4)."""

    __tablename__ = "llm_cache"

    content_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    prompt_version: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
