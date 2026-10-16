"""Contact normalization, extraction from free text, and keyed hashing (ED-004, ED-016).
Deterministic; no network.

Raw phone numbers and emails never reach the database: the API normalizes and hashes them
while handling the request and stores only the 32-byte HMAC. Contacts found inside the listing
text are hashed the same way, and the stored text has them replaced by placeholders.
"""

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Literal

import phonenumbers

ContactKind = Literal["phone", "email"]

# Deliberately loose: one "@", no spaces, a dot in the domain. Deliverability is not our concern;
# the goal is that the same address typed twice produces the same hash.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LENGTH = 254


class InvalidContact(ValueError):
    pass


def normalize_phone(raw: str, default_region: str) -> str:
    """Return the number in E.164 form (e.g. +14165550123).

    Numbers without a country code are read as `default_region` (CA in production).
    Raises InvalidContact if the number can't be parsed or isn't a valid number.
    """
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException as exc:
        raise InvalidContact("not a recognizable phone number") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise InvalidContact("not a valid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_email(raw: str) -> str:
    """Trim and lowercase the whole address.

    Lowercasing the local part is technically not RFC 5321-safe, but no major provider
    treats case as significant, and a scammer reusing an address shouldn't evade matching
    by changing case. Raises InvalidContact for anything that doesn't look like an email.
    """
    email = raw.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not _EMAIL_PATTERN.match(email):
        raise InvalidContact("not a valid email address")
    return email


def contact_hash(kind: ContactKind, normalized: str, pepper: bytes) -> bytes:
    """HMAC-SHA256 of "<kind>:<normalized value>", keyed with the secret pepper.

    The kind prefix keeps a phone and an email that happen to be the same string apart.
    """
    message = f"{kind}:{normalized}".encode()
    return hmac.new(pepper, message, hashlib.sha256).digest()


# Finds email-like substrings in free text; each match is then checked by normalize_email.
_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_PLACEHOLDER = "[phone]"
EMAIL_PLACEHOLDER = "[email]"
# Every occurrence is redacted, but at most this many distinct values per kind are kept for the
# contact graph, so a text stuffed with numbers can't create hundreds of rows.
MAX_TEXT_CONTACTS_PER_KIND = 10


@dataclass(frozen=True)
class ExtractedText:
    redacted: str
    # Normalized values in order of first appearance, without duplicates.
    phones: list[str]
    emails: list[str]


def extract_contacts(text: str, default_region: str) -> ExtractedText:
    """Find phone numbers and emails in free text; return them normalized, and the text with
    each occurrence replaced by [phone] / [email].

    Emails are replaced first, so digits inside an address (4165550123@gmail.com) aren't also
    read as a phone number. Phones are found by libphonenumber's matcher, which only accepts
    valid numbers, so prices, dates and postal codes are left alone. Numbers written to dodge
    detection ("four one six...", "416 555 O123") are not caught.
    """
    emails: list[str] = []

    def replace_email(match: re.Match[str]) -> str:
        try:
            email = normalize_email(match.group(0))
        except InvalidContact:
            return match.group(0)
        _append_unique(emails, email)
        return EMAIL_PLACEHOLDER

    without_emails = _EMAIL_IN_TEXT.sub(replace_email, text)

    phones: list[str] = []
    pieces: list[str] = []
    cursor = 0
    for match in phonenumbers.PhoneNumberMatcher(without_emails, default_region):
        _append_unique(
            phones, phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
        )
        pieces.append(without_emails[cursor : match.start])
        pieces.append(PHONE_PLACEHOLDER)
        cursor = match.end
    pieces.append(without_emails[cursor:])

    return ExtractedText(
        redacted="".join(pieces),
        phones=phones[:MAX_TEXT_CONTACTS_PER_KIND],
        emails=emails[:MAX_TEXT_CONTACTS_PER_KIND],
    )


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
