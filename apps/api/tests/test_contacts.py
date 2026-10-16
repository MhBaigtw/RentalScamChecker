import pytest

from rentalscamchecker.contacts import (
    MAX_TEXT_CONTACTS_PER_KIND,
    InvalidContact,
    contact_hash,
    extract_contacts,
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


def test_extract_finds_phones_in_common_formats() -> None:
    text = "Call 416-555-0123, (905) 555-0142, +1 647 555 0199 or 6475550188. UK: +44 20 7946 0958"

    result = extract_contacts(text, "CA")

    assert result.phones == [
        "+14165550123",
        "+19055550142",
        "+16475550199",
        "+16475550188",
        "+442079460958",
    ]
    assert result.redacted == "Call [phone], [phone], [phone] or [phone]. UK: [phone]"


def test_extract_leaves_prices_dates_postal_codes_and_units_alone() -> None:
    text = (
        "Rent $1,850/month, available 2026-11-01. 2 bed, 1 bath, 850 sq ft. "
        "Postal L6H 2L1. Unit 1430-2201. Deposit 1850 + first 1850 = 3700."
    )

    result = extract_contacts(text, "CA")

    assert result.phones == []
    assert result.emails == []
    assert result.redacted == text


def test_digits_inside_an_email_are_not_also_a_phone() -> None:
    result = extract_contacts("Reach me: 4165550123@Gmail.com", "CA")

    assert result.emails == ["4165550123@gmail.com"]
    assert result.phones == []
    assert result.redacted == "Reach me: [email]"


def test_repeated_contacts_are_listed_once_but_every_occurrence_is_redacted() -> None:
    result = extract_contacts("a@b.ca 416-555-0123 A@B.CA 4165550123", "CA")

    assert result.emails == ["a@b.ca"]
    assert result.phones == ["+14165550123"]
    assert result.redacted == "[email] [phone] [email] [phone]"


def test_at_most_ten_distinct_contacts_per_kind_are_kept_but_all_are_redacted() -> None:
    text = " ".join(f"416-555-01{i:02d}" for i in range(15))

    result = extract_contacts(text, "CA")

    assert len(result.phones) == MAX_TEXT_CONTACTS_PER_KIND
    assert result.redacted == " ".join(["[phone]"] * 15)
