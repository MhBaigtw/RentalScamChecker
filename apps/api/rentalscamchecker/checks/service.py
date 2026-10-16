"""Create and load checks. Owns the order of writes between photo storage and the database."""

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from rentalscamchecker.checks.validation import ValidSubmission
from rentalscamchecker.contacts import ContactKind, contact_hash
from rentalscamchecker.images import ProcessedPhoto
from rentalscamchecker.models import Check, CheckContact, CheckPhoto
from rentalscamchecker.storage import PhotoStorage

log = structlog.get_logger("rentalscamchecker.checks")


def photo_key(check_id: uuid.UUID, photo_id: uuid.UUID) -> str:
    return f"checks/{check_id}/{photo_id}.jpg"


def create_check(
    session: Session,
    storage: PhotoStorage,
    submission: ValidSubmission,
    photos: list[ProcessedPhoto],
    pepper: bytes,
) -> uuid.UUID:
    """Store the photos, then insert the check, its photos and its contact hashes in one
    transaction.

    Files are written first: if the commit fails we delete them, so we never have rows pointing
    at missing files. (A crash between the two steps can leave orphan files without rows; the
    retention job in M8 is the place to sweep those.)
    """
    check_id = uuid.uuid4()
    check = Check(
        id=check_id,
        submitted_url=submission.submitted_url,
        listing_text=submission.listing_text,
        rent_cents=submission.rent_cents,
        bedrooms=submission.bedrooms,
        address_text=submission.address_text,
        city=submission.city,
    )
    stored_keys: list[str] = []
    try:
        for position, photo in enumerate(photos):
            photo_id = uuid.uuid4()
            key = photo_key(check_id, photo_id)
            storage.put(key, photo.jpeg_bytes)
            stored_keys.append(key)
            check.photos.append(
                CheckPhoto(
                    id=photo_id,
                    position=position,
                    storage_key=key,
                    width=photo.width,
                    height=photo.height,
                )
            )
        check.contacts.extend(_contact_rows(submission, pepper))
        session.add(check)
        session.commit()
    except Exception:
        session.rollback()
        for key in stored_keys:
            storage.delete(key)
        raise
    log.info(
        "check_created",
        check_id=str(check_id),
        photos=len(photos),
        contacts=[c.kind for c in check.contacts],
    )
    return check_id


def _contact_rows(submission: ValidSubmission, pepper: bytes) -> list[CheckContact]:
    values: list[tuple[ContactKind, str | None]] = [
        ("phone", submission.phone_e164),
        ("email", submission.email_normalized),
    ]
    return [
        CheckContact(kind=kind, hmac_hash=contact_hash(kind, value, pepper))
        for kind, value in values
        if value is not None
    ]


def load_check(session: Session, check_id: uuid.UUID) -> Check | None:
    return session.scalar(
        select(Check)
        .where(Check.id == check_id)
        .options(
            selectinload(Check.photos),
            selectinload(Check.contacts),
            selectinload(Check.signal_results),
        )
    )
