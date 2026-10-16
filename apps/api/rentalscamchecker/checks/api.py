"""HTTP endpoints: `POST /checks` and `GET /checks/{id}` (SPEC.md section 9)."""

import uuid
from datetime import datetime
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
from rentalscamchecker.images import MAX_PHOTO_BYTES, PhotoRejected, ProcessedPhoto, process_photo
from rentalscamchecker.models import Check
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


def build_router(
    session_factory: sessionmaker[Session], storage: PhotoStorage, settings: Settings
) -> APIRouter:
    router = APIRouter()
    pepper = settings.contact_hash_pepper.get_secret_value().encode()

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
            check_id = create_check(session, storage, submission, processed, pepper)
        return CheckCreated(check_id=check_id)

    @router.get("/checks/{check_id}")
    def get_check(check_id: uuid.UUID) -> CheckOut:
        with session_factory() as session:
            check = load_check(session, check_id)
            if check is None:
                raise ApiError(404, "not_found", "No check with this id.")
            return _to_out(check)

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


def _to_out(check: Check) -> CheckOut:
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
    )
