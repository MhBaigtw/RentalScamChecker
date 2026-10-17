"""contact_reuse: has this phone number or email been used in other checks, and for what places?

DETERMINISTIC graph lookup (SPEC 4 and 6, ED-026). Contacts are compared only as HMAC-SHA256 hashes
of the normalized value (contacts.py, ED-004); raw numbers and emails are never stored or shown.
Both the contact form fields and contacts found inside the listing text count (ED-016).

Severity (hand-chosen, not tuned on data):
- high: a linked check is high risk FROM ITS OWN EVIDENCE (its `independent_risk_level`: photo,
  price, text or address signals, never its own contact_reuse). Reading the full `risk_level`
  instead would let risk cascade A -> B -> C (ED-027);
- medium: the contact was used for a different address in another city;
- low: the contact was used for other addresses in the same city (landlords and property
  managers with several units do this legitimately, so on its own it is weak);
- none: never seen before, or seen only for this same address (a listing posted again).

Every result is marked `depends_on_other_checks`: the scorer leaves it out of this check's
independent level and lets it raise the overall level by at most one step, so a fake listing that
uses a real landlord's number can't make the landlord's other listings "high" on its own.
"""

import uuid
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.addresses import normalize_street, same_address
from rentalscamchecker.graph import public_ref
from rentalscamchecker.reference_rents import normalize_city
from rentalscamchecker.signals.base import CheckInput, Severity, SignalContext, SignalResult

# Plain words for the signals that can make a linked check high risk on its own evidence.
OWN_EVIDENCE_WORDS = {
    "photo_reuse_internal": "photos used for another address",
    "photo_reuse_web": "photos found elsewhere online",
    "price_vs_market": "a rent far below the local average",
    "text_red_flags": "warning signs in the listing text",
    "address_sanity": "an address that didn't check out",
}

# Linked checks listed individually in the evidence (all are counted).
MAX_LISTED = 20

_OWN_SQL = text("SELECT kind, source FROM check_contacts WHERE check_id = :check_id ORDER BY kind")
_LINKED_SQL = text(
    """
    SELECT m.kind, m.source, o.check_id, c.created_at, c.city, c.address_text,
           c.independent_risk_level
    FROM check_contacts m
    JOIN check_contacts o ON o.hmac_hash = m.hmac_hash AND o.check_id <> m.check_id
    JOIN checks c ON c.id = o.check_id
    WHERE m.check_id = :check_id
    ORDER BY c.created_at DESC, c.id
    """
)
# For linked checks that are high on their own evidence: which of their signals made them so.
_OWN_SIGNALS_SQL = text(
    """
    SELECT check_id, name
    FROM signal_results
    WHERE check_id = ANY(:ids) AND status = 'ok' AND severity IN ('medium', 'high')
      AND (evidence ->> 'depends_on_other_checks') IS DISTINCT FROM 'true'
    ORDER BY check_id, name
    """
)


@dataclass
class _Linked:
    check_id: uuid.UUID
    city: str
    address_text: str
    independent_level: str | None
    same_address: bool
    kinds: set[str]
    # For independently high checks: their own signals at medium or high.
    own_signals: list[str]


class ContactReuseSignal:
    name = "contact_reuse"
    # 2 (M7): "high" reads linked checks' independent_risk_level; results depend on other checks.
    version = "2"

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def run(self, check: CheckInput, ctx: SignalContext) -> SignalResult:
        with self._sessions() as session:
            own = session.execute(_OWN_SQL, {"check_id": check.check_id}).all()
            rows = session.execute(_LINKED_SQL, {"check_id": check.check_id}).mappings().all()
            high_ids = list({r["check_id"] for r in rows if r["independent_risk_level"] == "high"})
            own_signals: dict[uuid.UUID, list[str]] = {}
            for check_id, name in session.execute(_OWN_SIGNALS_SQL, {"ids": high_ids}).all():
                own_signals.setdefault(check_id, []).append(name)
        if not own:
            return SignalResult(
                name=self.name,
                version=self.version,
                status="skipped",
                severity=None,
                evidence={"reason": "no_contacts"},
                explanation=(
                    "No phone number or email was given in the form or found in the listing "
                    "text, so we couldn't check whether they were used in other listings."
                ),
                next_step=(
                    "If you have the landlord's phone number or email, add it to a new check: "
                    "reused contacts are one of the clearest warning signs we can look for."
                ),
            )
        linked: dict[uuid.UUID, _Linked] = {}
        per_contact: Counter[tuple[str, str]] = Counter()
        for r in rows:
            per_contact[(r["kind"], r["source"])] += 1
            entry = linked.get(r["check_id"])
            if entry is None:
                entry = linked[r["check_id"]] = _Linked(
                    check_id=r["check_id"],
                    city=r["city"],
                    address_text=r["address_text"],
                    independent_level=r["independent_risk_level"],
                    same_address=same_address(
                        check.address_text, check.city, r["address_text"], r["city"]
                    ),
                    kinds=set(),
                    own_signals=own_signals.get(r["check_id"], []),
                )
            entry.kinds.add(r["kind"])
        return _result(self, check, [(k, s) for k, s in own], per_contact, list(linked.values()))


def _result(
    signal: ContactReuseSignal,
    check: CheckInput,
    own: list[tuple[str, str]],
    per_contact: Counter[tuple[str, str]],
    linked: list[_Linked],
) -> SignalResult:
    elsewhere = [c for c in linked if not c.same_address]
    other_addresses = {
        (normalize_street(c.address_text), normalize_city(c.city)) for c in elsewhere
    }
    own_city = normalize_city(check.city)
    cities_by_key: dict[str, str] = {}
    for c in elsewhere:
        cities_by_key.setdefault(normalize_city(c.city), c.city)
    other_cities = sorted(v for k, v in cities_by_key.items() if k != own_city)
    high_risk = [c for c in linked if c.independent_level == "high"]

    severity: Severity = "none"
    if high_risk:
        severity = "high"
    elif other_cities:
        severity = "medium"
    elif elsewhere:
        severity = "low"

    levels: Counter[str] = Counter(c.independent_level or "not_scored" for c in linked)
    evidence: dict[str, object] = {
        "method": "hmac_contact_lookup",
        # Read by the scorer (ED-027): this result is about other checks, not this listing.
        "depends_on_other_checks": True,
        "contacts_checked": [
            {"kind": kind, "source": source, "other_checks": per_contact[(kind, source)]}
            for kind, source in own
        ],
        "other_checks": len(linked),
        "other_checks_at_different_address": len(elsewhere),
        "other_addresses": len(other_addresses),
        "other_cities": other_cities,
        # Linked checks' levels from their own evidence (not_scored: still running).
        "linked_independent_levels": dict(sorted(levels.items())),
        "linked_high_risk": [
            {"check": public_ref(c.check_id), "own_signals": c.own_signals} for c in high_risk
        ],
        "linked_checks": [
            {
                "check": public_ref(c.check_id),
                "linked_by": sorted(c.kinds),
                "city": c.city,
                "same_address": c.same_address,
                "independent_risk_level": c.independent_level,
            }
            for c in linked[:MAX_LISTED]
        ],
    }
    what = "the same " + _contact_words({kind for c in linked for kind in c.kinds})
    checked = (
        f"We compared the {_contact_words({kind for kind, _ in own})} in this listing with all "
        "earlier checks."
    )
    if severity == "high":
        reasons = sorted({w for c in high_risk for w in map(_own_words, c.own_signals) if w})
        because = f" ({', '.join(reasons)})" if reasons else ""
        explanation = (
            f"{checked} {what.capitalize()} also appears in {_n_checks(len(high_risk))} that "
            f"had high risk signals from their own evidence{because}. This is about another "
            "listing that used the same contact, not proof about this one: someone may have "
            "copied the contact details."
        )
        next_step = (
            "Don't send any money or personal documents yet. Call the number yourself, meet at "
            "the unit, and confirm the person owns or manages it before paying anything."
        )
    elif severity == "medium":
        explanation = (
            f"{checked} {what.capitalize()} was also used for {_n_places(len(other_addresses))}, "
            f"including in another city ({', '.join(other_cities)}). Some property managers work "
            "across cities, but one contact behind listings in several cities is also how copied "
            "listings spread."
        )
        next_step = (
            "Ask the landlord which listings are theirs and why the same contact appears in other "
            "cities. View the unit in person and confirm they own or manage it before paying."
        )
    elif severity == "low":
        explanation = (
            f"{checked} {what.capitalize()} was also used for {_n_places(len(other_addresses))} "
            "in the same city. That is common for landlords and property managers with several "
            "units, so on its own it is a weak signal."
        )
        next_step = (
            "Ask whether they manage several units, and view this one in person before paying."
        )
    elif linked:
        explanation = (
            f"{checked} {what.capitalize()} appeared before only in checks for this same address, "
            "which is normal when a listing is posted again."
        )
        next_step = "Nothing to do about the contact details. Still see the place before paying."
    else:
        them = "them" if len(own) > 1 else "it"
        explanation = (
            f"{checked} We haven't seen {them} in any other check. This only covers listings "
            "submitted to this service."
        )
        next_step = "Nothing to do about the contact details. Still see the place before paying."
    return SignalResult(
        name=signal.name,
        version=signal.version,
        status="ok",
        severity=severity,
        evidence=evidence,
        explanation=explanation,
        next_step=next_step,
    )


def _contact_words(kinds: set[str]) -> str:
    if kinds == {"email", "phone"}:
        return "phone number and email"
    if kinds == {"email"}:
        return "email"
    return "phone number"


def _own_words(signal: str) -> str | None:
    return OWN_EVIDENCE_WORDS.get(signal)


def _n_checks(n: int) -> str:
    return f"{n} earlier check{'s' if n != 1 else ''}"


def _n_places(n: int) -> str:
    return f"{n} other address{'es' if n != 1 else ''}"
