import pytest

from rentalscamchecker.contacts import (
    InvalidContact,
    contact_hash,
    normalize_email,
    normalize_phone,
)


@pytest.mark.parametrize(
    "raw",
    [
        "(416) 555-0123",
        "416-555-0123",
        "416.555.0123",
        "4165550123",
        "+1 416 555 0123",
        "1-416-555-0123",
        "  +14165550123  ",
    ],
)
def test_ways_of_writing_one_canadian_number_normalize_to_the_same_e164(raw: str) -> None:
    assert normalize_phone(raw, "CA") == "+14165550123"


def test_number_with_country_code_ignores_default_region() -> None:
    assert normalize_phone("+44 20 7946 0958", "CA") == "+442079460958"


@pytest.mark.parametrize("raw", ["", "hello", "123", "+1 999 555 0123"])
def test_invalid_phone_numbers_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidContact):
        normalize_phone(raw, "CA")


def test_email_is_trimmed_and_lowercased() -> None:
    assert normalize_email("  Landlord.Joe@Example.COM ") == "landlord.joe@example.com"


@pytest.mark.parametrize(
    "raw", ["", "no-at-sign", "two@@example.com", "a@nodot", "has space@example.com"]
)
def test_invalid_emails_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidContact):
        normalize_email(raw)


def test_email_longer_than_254_chars_is_rejected() -> None:
    with pytest.raises(InvalidContact):
        normalize_email("a" * 250 + "@example.com")


def test_contact_hash_is_deterministic_32_bytes() -> None:
    first = contact_hash("phone", "+14165550123", b"pepper-a")
    second = contact_hash("phone", "+14165550123", b"pepper-a")

    assert first == second
    assert len(first) == 32


def test_contact_hash_depends_on_pepper() -> None:
    assert contact_hash("phone", "+14165550123", b"pepper-a") != contact_hash(
        "phone", "+14165550123", b"pepper-b"
    )


def test_contact_hash_depends_on_kind() -> None:
    assert contact_hash("phone", "same", b"p") != contact_hash("email", "same", b"p")
