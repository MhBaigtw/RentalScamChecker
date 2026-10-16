"""The interface every signal implements (SPEC.md section 4)."""

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from rentalscamchecker.config import Settings

SignalStatus = Literal["ok", "skipped", "error"]
Severity = Literal["none", "low", "medium", "high"]


@dataclass(frozen=True)
class CheckInput:
    """What a signal may read about a check. Contacts appear only as placeholders in the text;
    signals that need contacts (M6) read the HMACs from the database."""

    check_id: uuid.UUID
    listing_text: str
    rent_cents: int
    bedrooms: int
    address_text: str
    city: str
    submitted_url: str | None
    # "whole_unit" (default), "room" or "basement".
    unit_type: str = "whole_unit"


@dataclass(frozen=True)
class SignalContext:
    settings: Settings


@dataclass(frozen=True)
class SignalResult:
    name: str
    version: str
    status: SignalStatus
    # None when status is "error" or "skipped".
    severity: Severity | None
    evidence: dict[str, Any] = field(default_factory=dict)
    # User-facing text: what was checked and what was found, and what to do next.
    explanation: str | None = None
    next_step: str | None = None


class Signal(Protocol):
    name: str
    # Bump when the logic changes: results are stored per (check, name, version), and the job
    # idempotency key includes it.
    version: str

    def run(self, check: CheckInput, ctx: SignalContext) -> SignalResult:
        """Do the check. Runs in a worker thread, outside any DB transaction.

        Raise to have the job retried with backoff (e.g. a network error). Return a result with
        status "error" for a failure that retrying won't fix.
        """
        ...
