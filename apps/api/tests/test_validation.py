from dataclasses import replace

import pytest

from rentalscamchecker.checks.validation import (
    Contact,
    InvalidSubmission,
    RawSubmission,
    validate_submission,
)

VALID = RawSubmission(
    listing_text="  Bright 2 bedroom near Sheridan Trafalgar campus.  ",
    monthly_rent="1850",
    bedrooms="2",
    address_text="1430 Trafalgar Rd",
    city="Oakville",
)


def invalid_fields(raw: RawSubmission) -> dict[str, str]:
    with pytest.raises(InvalidSubmission) as info:
        validate_submission(raw, "CA")
    return {e.field: e.message for e in info.value.errors}


def test_valid_submission_is_cleaned() -> None:
    result = validate_submission(VALID, "CA")

    assert result.listing_text == "Bright 2 bedroom near Sheridan Trafalgar campus."
    assert result.rent_cents == 185_000
    assert result.bedrooms == 2
    assert result.submitted_url is None
    assert result.contacts == []


def test_contacts_are_normalized() -> None:
    raw = replace(VALID, contact_phone="(905) 555-0199", contact_email=" A@B.CA ")

    result = validate_submission(raw, "CA")

    assert result.contacts == [
        Contact("phone", "+19055550199", "field"),
        Contact("email", "a@b.ca", "field"),
    ]


def test_blank_optional_fields_become_none() -> None:
    raw = replace(VALID, submitted_url="  ", contact_phone="", contact_email=" ")

    result = validate_submission(raw, "CA")

    assert (result.submitted_url, result.contacts) == (None, [])


def test_every_missing_required_field_is_reported_at_once() -> None:
    errors = invalid_fields(RawSubmission())

    assert set(errors) == {"listing_text", "monthly_rent", "bedrooms", "address_text", "city"}
    assert all(message == "This field is required." for message in errors.values())


@pytest.mark.parametrize(
    ("typed", "cents"),
    [("1850", 185_000), ("1,850.00", 185_000), ("$1850.5", 185_050), (" $ 999.99 ", 99_999)],
)
def test_rent_accepts_common_formats(typed: str, cents: int) -> None:
    assert validate_submission(replace(VALID, monthly_rent=typed), "CA").rent_cents == cents


@pytest.mark.parametrize(
    ("typed", "message_start"),
    [
        ("abc", "Must be a number"),
        ("NaN", "Must be a number"),
        ("Infinity", "Must be a number"),
        ("1850.001", "Use at most two"),
        ("0", "Must be greater"),
        ("-100", "Must be greater"),
        ("20000.01", "Must be at most"),
    ],
)
def test_bad_rent_values_are_rejected(typed: str, message_start: str) -> None:
    errors = invalid_fields(replace(VALID, monthly_rent=typed))

    assert errors["monthly_rent"].startswith(message_start)


@pytest.mark.parametrize("typed", ["-1", "11", "2.5", "two"])
def test_bad_bedroom_counts_are_rejected(typed: str) -> None:
    assert "bedrooms" in invalid_fields(replace(VALID, bedrooms=typed))


def test_studio_is_zero_bedrooms() -> None:
    assert validate_submission(replace(VALID, bedrooms="0"), "CA").bedrooms == 0


@pytest.mark.parametrize(
    "url", ["ftp://example.com/x", "javascript:alert(1)", "example.com", "https://a b.com"]
)
def test_non_web_urls_are_rejected(url: str) -> None:
    assert "submitted_url" in invalid_fields(replace(VALID, submitted_url=url))


def test_web_url_is_kept_as_text() -> None:
    url = "https://www.kijiji.ca/v-apartments-condos/oakville/123"

    assert validate_submission(replace(VALID, submitted_url=url), "CA").submitted_url == url


def test_overlong_listing_text_is_rejected() -> None:
    assert "listing_text" in invalid_fields(replace(VALID, listing_text="x" * 20_001))


def test_bad_contacts_are_reported_per_field() -> None:
    errors = invalid_fields(replace(VALID, contact_phone="12", contact_email="nope"))

    assert errors == {
        "contact_phone": "Not a valid phone number.",
        "contact_email": "Not a valid email address.",
    }


def test_contacts_in_listing_text_are_extracted_and_redacted() -> None:
    raw = replace(
        VALID,
        listing_text="Text me at 647-555-0199 or email Owner.Abroad@Mail.com. $1850/month.",
    )

    result = validate_submission(raw, "CA")

    assert result.listing_text == "Text me at [phone] or email [email]. $1850/month."
    assert result.contacts == [
        Contact("phone", "+16475550199", "text"),
        Contact("email", "owner.abroad@mail.com", "text"),
    ]


def test_same_number_in_field_and_text_gives_one_contact_from_the_field() -> None:
    raw = replace(
        VALID,
        listing_text="Call (416) 555-0123, or 416.555.0123 after 6pm.",
        contact_phone="+1 416 555 0123",
    )

    result = validate_submission(raw, "CA")

    assert result.listing_text == "Call [phone], or [phone] after 6pm."
    assert result.contacts == [Contact("phone", "+14165550123", "field")]


def test_different_number_in_text_is_kept_alongside_the_field() -> None:
    raw = replace(VALID, listing_text="Backup: 905-555-0142", contact_phone="416-555-0123")

    result = validate_submission(raw, "CA")

    assert result.contacts == [
        Contact("phone", "+14165550123", "field"),
        Contact("phone", "+19055550142", "text"),
    ]
