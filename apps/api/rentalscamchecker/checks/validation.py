"""Validation of the listing fields in a `POST /checks` submission. Pure functions, no I/O.

Every field arrives as an optional string (multipart form). We validate all fields and report
every problem at once, so the user can fix the whole form in one go.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlsplit

from rentalscamchecker.contacts import (
    ContactKind,
    InvalidContact,
    extract_contacts,
    normalize_email,
    normalize_phone,
)
from rentalscamchecker.errors import FieldError

MAX_LISTING_TEXT = 20_000
MAX_ADDRESS = 300
MAX_CITY = 100
MAX_URL = 2_000
MAX_BEDROOMS = 10
MAX_RENT_CENTS = 20_000 * 100

# A field validator takes (field name, raw value) and returns the clean value or raises FieldError.
type Validator[T] = Callable[[str, str | None], T]


class InvalidSubmission(ValueError):
    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__(f"{len(errors)} invalid field(s)")
        self.errors = errors


@dataclass(frozen=True)
class RawSubmission:
    listing_text: str | None = None
    monthly_rent: str | None = None
    bedrooms: str | None = None
    address_text: str | None = None
    city: str | None = None
    submitted_url: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    unit_type: str | None = None


ContactSource = Literal["field", "text"]
UnitType = Literal["whole_unit", "room", "basement"]
UNIT_TYPE_VALUES: tuple[UnitType, ...] = ("whole_unit", "room", "basement")


@dataclass(frozen=True)
class Contact:
    kind: ContactKind
    # Normalized (E.164 / lowercased) but not yet hashed. Lives in memory only for the request.
    value: str
    source: ContactSource


@dataclass(frozen=True)
class ValidSubmission:
    # With every phone number and email replaced by [phone] / [email] (ED-016).
    listing_text: str
    rent_cents: int
    bedrooms: int
    unit_type: UnitType
    address_text: str
    city: str
    submitted_url: str | None
    # Distinct by (kind, value): a number given in the phone field and repeated in the text
    # appears once, with source "field".
    contacts: list[Contact]


def validate_submission(raw: RawSubmission, phone_region: str) -> ValidSubmission:
    """Return the cleaned submission, or raise InvalidSubmission listing every bad field."""
    errors: list[FieldError] = []

    def run[T](field: str, fn: Validator[T], value: str | None) -> T | None:
        try:
            return fn(field, value)
        except FieldError as exc:
            errors.append(exc)
            return None

    listing_text = run("listing_text", _required_text(MAX_LISTING_TEXT), raw.listing_text)
    rent_cents = run("monthly_rent", _rent_cents, raw.monthly_rent)
    bedrooms = run("bedrooms", _bedrooms, raw.bedrooms)
    unit_type = run("unit_type", _unit_type, raw.unit_type)
    address_text = run("address_text", _required_text(MAX_ADDRESS, min_length=3), raw.address_text)
    city = run("city", _required_text(MAX_CITY, min_length=2), raw.city)
    submitted_url = run("submitted_url", _optional_url, raw.submitted_url)
    phone = run("contact_phone", _optional_phone(phone_region), raw.contact_phone)
    email = run("contact_email", _optional_email, raw.contact_email)

    if errors:
        raise InvalidSubmission(errors)
    # All required validators returned values (they raise otherwise); assert for the type checker.
    assert listing_text is not None and rent_cents is not None and bedrooms is not None  # noqa: S101
    assert address_text is not None and city is not None and unit_type is not None  # noqa: S101
    extracted = extract_contacts(listing_text, phone_region)
    contacts = _merge_contacts(
        field_phone=phone,
        field_email=email,
        text_phones=extracted.phones,
        text_emails=extracted.emails,
    )
    return ValidSubmission(
        listing_text=extracted.redacted,
        rent_cents=rent_cents,
        bedrooms=bedrooms,
        unit_type=unit_type,
        address_text=address_text,
        city=city,
        submitted_url=submitted_url,
        contacts=contacts,
    )


def _merge_contacts(
    field_phone: str | None,
    field_email: str | None,
    text_phones: list[str],
    text_emails: list[str],
) -> list[Contact]:
    """Field values first, then text values not already present."""
    candidates: list[Contact] = []
    if field_phone is not None:
        candidates.append(Contact("phone", field_phone, "field"))
    if field_email is not None:
        candidates.append(Contact("email", field_email, "field"))
    candidates += [Contact("phone", value, "text") for value in text_phones]
    candidates += [Contact("email", value, "text") for value in text_emails]

    merged: list[Contact] = []
    seen: set[tuple[str, str]] = set()
    for contact in candidates:
        if (contact.kind, contact.value) not in seen:
            seen.add((contact.kind, contact.value))
            merged.append(contact)
    return merged


def _blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def _required_text(max_length: int, min_length: int = 1) -> Validator[str]:
    def validate(field: str, value: str | None) -> str:
        if value is None or _blank(value):
            raise FieldError(field, "This field is required.")
        cleaned = value.strip()
        if len(cleaned) < min_length:
            raise FieldError(field, f"Must be at least {min_length} characters.")
        if len(cleaned) > max_length:
            raise FieldError(field, f"Must be at most {max_length} characters.")
        return cleaned

    return validate


def _rent_cents(field: str, value: str | None) -> int:
    if value is None or _blank(value):
        raise FieldError(field, "This field is required.")
    # Accept what people type: "1850", "1,850.00", "$1850".
    cleaned = value.strip().removeprefix("$").replace(",", "").strip()
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise FieldError(field, "Must be a number of dollars, e.g. 1850 or 1850.00.") from exc
    if not amount.is_finite():
        raise FieldError(field, "Must be a number of dollars, e.g. 1850 or 1850.00.")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise FieldError(field, "Use at most two decimal places.")
    if cents <= 0:
        raise FieldError(field, "Must be greater than zero.")
    if cents > MAX_RENT_CENTS:
        raise FieldError(field, f"Must be at most {MAX_RENT_CENTS // 100} dollars per month.")
    return int(cents)


def _bedrooms(field: str, value: str | None) -> int:
    if value is None or _blank(value):
        raise FieldError(field, "This field is required.")
    try:
        count = int(value.strip())
    except ValueError as exc:
        raise FieldError(field, "Must be a whole number (0 for a studio).") from exc
    if not 0 <= count <= MAX_BEDROOMS:
        raise FieldError(field, f"Must be between 0 and {MAX_BEDROOMS}.")
    return count


def _unit_type(field: str, value: str | None) -> UnitType:
    """Optional; a whole unit unless the user says it's a room or a basement apartment."""
    if value is None or _blank(value):
        return "whole_unit"
    cleaned = value.strip().lower()
    for allowed in UNIT_TYPE_VALUES:
        if cleaned == allowed:
            return allowed
    raise FieldError(field, f"Must be one of: {', '.join(UNIT_TYPE_VALUES)}.")


def _optional_url(field: str, value: str | None) -> str | None:
    if value is None or _blank(value):
        return None
    url = value.strip()
    if len(url) > MAX_URL:
        raise FieldError(field, f"Must be at most {MAX_URL} characters.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(c.isspace() for c in url):
        raise FieldError(field, "Must be a web address starting with http:// or https://.")
    return url


def _optional_phone(region: str) -> Validator[str | None]:
    def validate(field: str, value: str | None) -> str | None:
        if value is None or _blank(value):
            return None
        try:
            return normalize_phone(value, region)
        except InvalidContact as exc:
            raise FieldError(field, "Not a valid phone number.") from exc

    return validate


def _optional_email(field: str, value: str | None) -> str | None:
    if value is None or _blank(value):
        return None
    try:
        return normalize_email(value)
    except InvalidContact as exc:
        raise FieldError(field, "Not a valid email address.") from exc
