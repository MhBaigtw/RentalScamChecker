"""SQLAlchemy ORM models for the SPEC.md section 8 data model.

Not here yet: `users`, `outcomes` and `disputes` (M8), and `check_photos.embedding` (M5, once
the embedding model and therefore the vector dimension are chosen). See docs/database-design.md.

Enumerated values are TEXT columns with CHECK constraints rather than Postgres ENUM types:
adding a value later is one constraint swap instead of an ALTER TYPE (ED-012).

Postgres doesn't index foreign keys automatically. Each child table's unique constraint starts
with check_id, and that unique index also serves "all rows for this check" lookups.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
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
RISK_LEVELS = ("low", "some", "high", "incomplete")
CONTACT_KINDS = ("phone", "email")
CONTACT_SOURCES = ("field", "text")
SIGNAL_STATUSES = ("ok", "skipped", "error")
SEVERITIES = ("none", "low", "medium", "high")
JOB_STATUSES = ("queued", "running", "done", "failed", "dead")


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


class CheckPhoto(Base):
    __tablename__ = "check_photos"
    __table_args__ = (
        UniqueConstraint("check_id", "position", name="uq_check_photos_check_id_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"))
    # Order the user uploaded them in, 0-based.
    position: Mapped[int] = mapped_column(SmallInteger)
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    # 64-bit perceptual hashes, filled in M5. Stored as signed BIGINT (Postgres has no unsigned).
    phash: Mapped[int | None] = mapped_column(BigInteger)
    dhash: Mapped[int | None] = mapped_column(BigInteger)
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
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    # "field": the contact phone/email form fields. "text": found inside the listing text.
    # If a value is in both, the single row says "field".
    source: Mapped[str] = mapped_column(Text)
    hmac_hash: Mapped[bytes] = mapped_column(LargeBinary)

    check: Mapped[Check] = relationship(back_populates="contacts")


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
    """Background job queue row (ED-002). The repository and claim index arrive in M2."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="status"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
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
    """CMHC average rent per census metropolitan area and bedroom count (loaded in M3)."""

    __tablename__ = "reference_rents"
    __table_args__ = (
        CheckConstraint("bedrooms BETWEEN 0 AND 3", name="bedrooms_range"),
        UniqueConstraint(
            "cma", "bedrooms", "survey_year", name="uq_reference_rents_cma_bedrooms_survey_year"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    cma: Mapped[str] = mapped_column(Text)
    # CMHC buckets: 0 = bachelor, 1, 2, 3 = "3 bedroom +".
    bedrooms: Mapped[int] = mapped_column(SmallInteger)
    avg_rent_cents: Mapped[int] = mapped_column(Integer)
    survey_year: Mapped[int] = mapped_column(SmallInteger)
    source: Mapped[str] = mapped_column(Text)


class LlmCache(Base):
    """Cached LLM responses keyed by content hash, prompt version and model (M4)."""

    __tablename__ = "llm_cache"

    content_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    prompt_version: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
