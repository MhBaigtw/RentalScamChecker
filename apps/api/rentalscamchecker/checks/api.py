"""HTTP endpoints: `POST /checks` and `GET /checks/{id}` (SPEC.md section 9)."""

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.checks.service import create_check, load_check
from rentalscamchecker.checks.validation import (
    InvalidSubmission,
    RawSubmission,
    validate_submission,
)
from rentalscamchecker.config import Settings
from rentalscamchecker.errors import ApiError, FieldError, validation_error
from rentalscamchecker.graph import cluster_size, public_ref, related_checks
from rentalscamchecker.images import MAX_PHOTO_BYTES, PhotoRejected, ProcessedPhoto, process_photo
from rentalscamchecker.models import Check
from rentalscamchecker.pipeline import enqueue_check_jobs
from rentalscamchecker.signals.registry import enabled_signals
from rentalscamchecker.storage import PhotoStorage

MIN_PHOTOS = 1
MAX_PHOTOS = 10

OptionalForm = Annotated[str | None, Form()]


class CheckCreated(BaseModel):
    check_id: uuid.UUID


class ListingOut(BaseModel):
    submitted_url: str | None
    listing_text: str
    rent_cents: int
    bedrooms: int
    unit_type: str
    address_text: str
    city: str


class PhotoOut(BaseModel):
    id: uuid.UUID
    position: int
    width: int
    height: int


class ContactOut(BaseModel):
    # Kind and source only: hashes are internal and raw values are never stored.
    kind: str
    source: str


class SignalOut(BaseModel):
    name: str
    version: str
    status: str
    severity: str | None
    evidence: dict[str, Any]
    explanation: str | None
    next_step: str | None
    finished_at: datetime | None


class RelatedCheckOut(BaseModel):
    # public_ref, never the id: an id would let the reader fetch someone else's listing.
    check: str
    created_on: date
    city: str
    risk_level: str | None
    # "photo", "phone", "email".
    linked_by: list[str]
    same_address: bool


class RelatedOut(BaseModel):
    """Other checks linked to this one through a photo or a contact (the check graph, ED-026).
    Computed when the report is read, so an earlier check's report also lists later checks.
    Context only: it doesn't change the risk level (the photo and contact signals do that)."""

    explanation: str
    next_step: str
    total: int
    at_different_address: int
    # Newest first, at most graph.RELATED_LIMIT.
    checks: list[RelatedCheckOut]
    # Checks reachable through any chain of links, including this one.
    cluster_size: int
    cluster_size_capped: bool


class CheckOut(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime
    risk_level: str | None
    scoring_version: str | None
    listing: ListingOut
    photos: list[PhotoOut]
    contacts: list[ContactOut]
    signals: list[SignalOut]
    # None until the check is complete (photo links are stored while its signals run).
    related: RelatedOut | None


def build_router(
    session_factory: sessionmaker[Session], storage: PhotoStorage, settings: Settings
) -> APIRouter:
    router = APIRouter()
    pepper = settings.contact_hash_pepper.get_secret_value().encode()
    signals = enabled_signals(settings, session_factory)

    def enqueue_jobs(session: Session, check_id: uuid.UUID) -> None:
        enqueue_check_jobs(
            session,
            check_id,
            signals,
            check_timeout_seconds=settings.check_timeout_seconds,
            max_attempts=settings.job_max_attempts,
        )

    # Plain `def` (not async): FastAPI runs it in a thread pool, so the blocking DB and
    # image work doesn't stall the event loop.
    @router.post("/checks", status_code=202)
    def post_check(
        listing_text: OptionalForm = None,
        monthly_rent: OptionalForm = None,
        bedrooms: OptionalForm = None,
        address_text: OptionalForm = None,
        city: OptionalForm = None,
        submitted_url: OptionalForm = None,
        contact_phone: OptionalForm = None,
        contact_email: OptionalForm = None,
        unit_type: OptionalForm = None,
        photos: Annotated[list[UploadFile] | None, File()] = None,
    ) -> CheckCreated:
        raw = RawSubmission(
            listing_text=listing_text,
            monthly_rent=monthly_rent,
            bedrooms=bedrooms,
            address_text=address_text,
            city=city,
            submitted_url=submitted_url,
            contact_phone=contact_phone,
            contact_email=contact_email,
            unit_type=unit_type,
        )
        errors: list[FieldError] = []
        try:
            submission = validate_submission(raw, settings.contact_default_region)
        except InvalidSubmission as exc:
            errors.extend(exc.errors)
        processed = _process_photos(photos or [], errors)
        if errors:
            raise validation_error(errors)

        with session_factory() as session:
            check_id = create_check(session, storage, submission, processed, pepper, enqueue_jobs)
        return CheckCreated(check_id=check_id)

    @router.get("/checks/{check_id}")
    def get_check(check_id: uuid.UUID) -> CheckOut:
        with session_factory() as session:
            check = load_check(session, check_id)
            if check is None:
                raise ApiError(404, "not_found", "No check with this id.")
            related = _related(session, check) if check.status == "complete" else None
            return _to_out(check, related)

    return router


def _process_photos(uploads: list[UploadFile], errors: list[FieldError]) -> list[ProcessedPhoto]:
    if not MIN_PHOTOS <= len(uploads) <= MAX_PHOTOS:
        errors.append(FieldError("photos", f"Upload between {MIN_PHOTOS} and {MAX_PHOTOS} photos."))
        return []
    processed: list[ProcessedPhoto] = []
    for index, upload in enumerate(uploads):
        # Read one byte past the limit: enough to know it's too big without reading it all.
        data = upload.file.read(MAX_PHOTO_BYTES + 1)
        try:
            processed.append(process_photo(data))
        except PhotoRejected as exc:
            errors.append(FieldError(f"photos.{index}", f"Photo {index + 1}: {exc}."))
    return processed


def _related(session: Session, check: Check) -> RelatedOut:
    related, total = related_checks(session, check.id, check.address_text, check.city)
    cluster = cluster_size(session, check.id)
    # Counted over the listed checks; exact unless more than graph.RELATED_LIMIT are linked.
    different = sum(1 for r in related if not r.same_address)
    if total == 0:
        explanation = (
            "No other check submitted to this service shares a photo, phone number or email with "
            "this one."
        )
    else:
        group = f"at least {cluster.size}" if cluster.capped else str(cluster.size)
        explanation = (
            f"{total} other check{'s' if total != 1 else ''} share{'' if total != 1 else 's'} a "
            f"photo, phone number or email with this one ({different} for a different address). "
            f"Following those links further, {group} checks are connected, including this one."
        )
    return RelatedOut(
        explanation=explanation,
        next_step=(
            "See the photo and contact results for what these links mean. Links at the same "
            "address are usually the same listing posted again."
        ),
        total=total,
        at_different_address=different,
        checks=[
            RelatedCheckOut(
                check=public_ref(r.check_id),
                created_on=r.created_at.date(),
                city=r.city,
                risk_level=r.risk_level,
                linked_by=list(r.linked_by),
                same_address=r.same_address,
            )
            for r in related
        ],
        cluster_size=cluster.size,
        cluster_size_capped=cluster.capped,
    )


def _to_out(check: Check, related: RelatedOut | None) -> CheckOut:
    return CheckOut(
        id=check.id,
        status=check.status,
        created_at=check.created_at,
        risk_level=check.risk_level,
        scoring_version=check.scoring_version,
        listing=ListingOut(
            submitted_url=check.submitted_url,
            listing_text=check.listing_text,
            rent_cents=check.rent_cents,
            bedrooms=check.bedrooms,
            unit_type=check.unit_type,
            address_text=check.address_text,
            city=check.city,
        ),
        photos=[
            PhotoOut(id=p.id, position=p.position, width=p.width, height=p.height)
            for p in check.photos
        ],
        contacts=[ContactOut(kind=c.kind, source=c.source) for c in check.contacts],
        signals=[
            SignalOut(
                name=s.name,
                version=s.version,
                status=s.status,
                severity=s.severity,
                evidence=s.evidence,
                explanation=s.explanation,
                next_step=s.next_step,
                finished_at=s.finished_at,
            )
            for s in check.signal_results
        ],
        related=related,
    )
