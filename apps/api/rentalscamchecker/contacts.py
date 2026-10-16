"""Contact normalization and keyed hashing (ED-004). Deterministic; no network.

Raw phone numbers and emails never reach the database: the API normalizes and hashes them
while handling the request and stores only the 32-byte HMAC.
"""

import hashlib
import hmac
import re
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
