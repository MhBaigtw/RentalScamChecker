"""Scoring (scoring/, ED-027): the rule, the versioned config, and the graph constraint that risk
must not cascade through linked checks. The graph cases use the real contact_reuse signal and the
real aggregator (`finalize_check`) against the test database."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.contacts import contact_hash
from rentalscamchecker.models import Check, CheckContact
from rentalscamchecker.models import SignalResult as SignalResultRow
from rentalscamchecker.pipeline import finalize_check
from rentalscamchecker.scoring.config import (
    SCORING_VERSION,
    ScoringConfigError,
    load_config,
    parse_config,
)
from rentalscamchecker.scoring.rules import SignalOutcome, score
from rentalscamchecker.signals.base import CheckInput, SignalContext
from rentalscamchecker.signals.contact_reuse import ContactReuseSignal
from tests.helpers import TEST_PEPPER, UNREACHABLE_DB_URL, make_settings

CONFIG = load_config()
CTX = SignalContext(settings=make_settings(UNREACHABLE_DB_URL))
GRAPH = {"depends_on_other_checks": True}


def ok(name: str, severity: str, evidence: dict[str, Any] | None = None) -> SignalOutcome:
    return SignalOutcome(name, "ok", severity, evidence or {})


# --- the rule (unit) ---


def test_no_signals_or_only_none_is_low() -> None:
    assert score([], CONFIG).level == "low"
    assert score([ok("price_vs_market", "none"), ok("text_red_flags", "none")], CONFIG).level == (
        "low"
    )


@pytest.mark.parametrize(
    ("severities", "level"),
    [
        (["low"], "some"),
        (["medium"], "some"),
        (["low", "low", "low"], "some"),
        (["medium", "medium"], "high"),
        (["high"], "high"),
    ],
)
def test_levels_from_own_signals(severities: list[str], level: str) -> None:
    names = ["price_vs_market", "text_red_flags", "photo_reuse_internal"]
    outcomes = [ok(n, s) for n, s in zip(names, severities, strict=False)]
    assert score(outcomes, CONFIG).level == level


def test_address_sanity_alone_never_reaches_high() -> None:
    assert score([ok("address_sanity", "high")], CONFIG).level == "some"


def test_skipped_signals_add_nothing_and_are_not_missing() -> None:
    result = score([SignalOutcome("price_vs_market", "skipped", None)], CONFIG)
    assert (result.level, result.missing) == ("low", [])


def test_error_in_a_signal_makes_a_non_high_check_incomplete() -> None:
    outcomes = [ok("price_vs_market", "low"), SignalOutcome("text_red_flags", "error", None)]
    result = score(outcomes, CONFIG)
    assert (result.level, result.missing) == ("incomplete", ["text_red_flags"])


def test_a_high_level_stands_even_if_another_signal_errored() -> None:
    outcomes = [ok("photo_reuse_internal", "high"), SignalOutcome("text_red_flags", "error", None)]
    assert score(outcomes, CONFIG).level == "high"


def test_optional_signal_errors_do_not_make_a_check_incomplete() -> None:
    outcomes = [ok("price_vs_market", "none"), SignalOutcome("photo_reuse_web", "error", None)]
    assert score(outcomes, CONFIG).level == "low"


def test_graph_dependent_high_alone_is_capped_at_some() -> None:
    result = score([ok("contact_reuse", "high", GRAPH)], CONFIG)
    assert (result.level, result.independent_level, result.graph_capped) == ("some", "low", True)


def test_graph_dependent_high_can_lift_own_some_to_high() -> None:
    result = score([ok("contact_reuse", "high", GRAPH), ok("text_red_flags", "medium")], CONFIG)
    assert (result.level, result.independent_level, result.graph_capped) == ("high", "some", False)


def test_independent_level_ignores_graph_dependent_results() -> None:
    photo_from_link = ok("photo_reuse_internal", "high", GRAPH)
    result = score([photo_from_link, ok("price_vs_market", "none")], CONFIG)
    assert (result.level, result.independent_level) == ("some", "low")


def test_contributing_lists_names_strongest_first_without_points() -> None:
    result = score(
        [
            ok("text_red_flags", "low"),
            ok("photo_reuse_internal", "high"),
            ok("price_vs_market", "none"),
        ],
        CONFIG,
    )
    assert result.contributing == ["photo_reuse_internal", "text_red_flags"]


# --- the config ---


def test_v1_config_loads_and_is_the_current_version() -> None:
    assert (SCORING_VERSION, CONFIG.version, CONFIG.max_steps_up) == ("v1", "v1", 1)
    assert "photo_reuse_web" in CONFIG.optional_signals


def raw_v1(**changes: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "version": "v1",
        "severity_points": {"none": 0, "low": 1, "medium": 2, "high": 4},
        "levels": {"some": 1, "high": 4},
        "weights": {},
        "graph": {"max_steps_up": 1},
        "incomplete": {"optional_signals": []},
    }
    raw.update(changes)
    return raw


@pytest.mark.parametrize(
    "changes",
    [
        {"version": "v2"},
        {"severity_points": {"none": 0, "low": 1, "medium": 2}},
        {"severity_points": {"none": 1, "low": 1, "medium": 2, "high": 4}},
        {"severity_points": {"none": 0, "low": 3, "medium": 2, "high": 4}},
        {"levels": {"some": 4, "high": 1}},
        {"levels": {"some": "1", "high": 4}},
        {"weights": {"price_vs_market": -1}},
        {"graph": {"max_steps_up": 5}},
        {"graph": {}},
        {"incomplete": {"optional_signals": "photo_reuse_web"}},
    ],
)
def test_invalid_configs_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ScoringConfigError):
        parse_config(raw_v1(**changes), "v1")


def test_missing_config_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ScoringConfigError, match="can't read scoring config"):
        load_config("v1", tmp_path)


# --- the graph constraint, with the real contact_reuse and aggregator ---

PEPPER = TEST_PEPPER.encode()


class Graph:
    """Seeds checks with contacts and own signal results, runs contact_reuse and scores them the
    way the worker does: contact_reuse at submission, then the aggregator."""

    def __init__(self, sf: sessionmaker[Session]) -> None:
        self.sf = sf

    def check(
        self,
        address: str,
        city: str,
        contacts: tuple[str, ...],
        own: dict[str, tuple[str, dict[str, Any]]] | None = None,
    ) -> uuid.UUID:
        check_id = uuid.uuid4()
        with self.sf.begin() as s:
            s.add(
                Check(
                    id=check_id,
                    listing_text="x",
                    rent_cents=150000,
                    bedrooms=1,
                    address_text=address,
                    city=city,
                )
            )
            s.flush()
            for value in contacts:
                kind = "email" if "@" in value else "phone"
                s.add(
                    CheckContact(
                        check_id=check_id,
                        kind=kind,
                        source="field",
                        hmac_hash=contact_hash(kind, value, PEPPER),  # type: ignore[arg-type]
                    )
                )
        names = {"price_vs_market", "text_red_flags", "photo_reuse_internal", "address_sanity"}
        results: dict[str, tuple[str, dict[str, Any]]] = {n: ("none", {}) for n in names}
        results |= own or {}
        with self.sf.begin() as s:
            for name, (severity, evidence) in results.items():
                s.add(self._row(check_id, name, severity, evidence))
        contact = ContactReuseSignal(self.sf).run(
            CheckInput(check_id, "x", 150000, 1, address, city, None), CTX
        )
        with self.sf.begin() as s:
            s.add(
                SignalResultRow(
                    check_id=check_id,
                    name=contact.name,
                    version=contact.version,
                    status=contact.status,
                    severity=contact.severity,
                    evidence=contact.evidence,
                    explanation=contact.explanation,
                    next_step=contact.next_step,
                )
            )
            expected = [(n, "1") for n in results] + [(contact.name, contact.version)]
            finalize_check(s, check_id, expected, CONFIG)
        return check_id

    @staticmethod
    def _row(
        check_id: uuid.UUID, name: str, severity: str, evidence: dict[str, Any]
    ) -> SignalResultRow:
        return SignalResultRow(
            check_id=check_id,
            name=name,
            version="1",
            status="ok",
            severity=severity,
            evidence=evidence,
        )

    def get(self, check_id: uuid.UUID) -> tuple[str | None, str | None, dict[str, Any]]:
        with self.sf() as s:
            check = s.get_one(Check, check_id)
            contact = next(r for r in check.signal_results if r.name == "contact_reuse")
            return (
                check.risk_level,
                check.independent_risk_level,
                {"severity": contact.severity, **contact.evidence},
            )


LANDLORD_PHONE = "+19055550142"
STOLEN_PHOTOS = {"photo_reuse_internal": ("high", {"depends_on_other_checks": False})}


@pytest.mark.integration
def test_a_fake_high_risk_check_using_a_landlords_phone_does_not_make_their_listings_high(
    session_factory: sessionmaker[Session],
) -> None:
    """(a) Poisoning: someone posts a fake listing (stolen photos, rent far below market) with a
    real landlord's phone number. The landlord's next clean listing gets a high contact_reuse
    result, but its level stays "some": the link alone can raise it one step, not to "high"."""
    g = Graph(session_factory)
    first = g.check("12 King St W", "Toronto", (LANDLORD_PHONE,))
    second = g.check("40 Bay St", "Toronto", (LANDLORD_PHONE,))
    fake = g.check(
        "1 Front St",
        "Toronto",
        (LANDLORD_PHONE,),
        {**STOLEN_PHOTOS, "price_vs_market": ("high", {})},
    )
    later_clean = g.check("88 Queen St E", "Toronto", (LANDLORD_PHONE,))

    assert g.get(fake)[:2] == ("high", "high")
    level, independent, contact = g.get(later_clean)
    assert contact["severity"] == "high"
    assert (level, independent) == ("some", "low")
    # Listings scored before the fake existed keep their levels (reports are not re-scored).
    assert g.get(first)[0] == "low"
    assert g.get(second)[0] == "some"  # its own contact_reuse: same phone, other address, low


@pytest.mark.integration
def test_risk_does_not_chain_from_a_through_b_to_c(session_factory: sessionmaker[Session]) -> None:
    """(b) A is high on its own evidence. B shares a phone with A and has its own medium signal,
    so B's level is "high", but B's independent level is "some". C shares only an email with B:
    C's contact_reuse reads B's independent level, so A's risk does not reach C."""
    g = Graph(session_factory)
    a = g.check("1 Main St", "Milton", ("+19055550100",), {"price_vs_market": ("high", {})})
    b = g.check(
        "9 Elm Ave",
        "Oakville",
        ("+19055550100", "b@example.com"),
        {"text_red_flags": ("medium", {})},
    )
    c = g.check("3 Oak Rd", "Guelph", ("b@example.com",))

    assert g.get(a)[:2] == ("high", "high")
    b_level, b_independent, b_contact = g.get(b)
    assert (b_contact["severity"], b_level, b_independent) == ("high", "high", "some")
    c_level, c_independent, c_contact = g.get(c)
    assert c_contact["severity"] == "medium"  # other address in another city; not "high"
    assert c_contact["linked_high_risk"] == []
    assert (c_level, c_independent) == ("some", "low")


@pytest.mark.integration
def test_risk_does_not_chain_through_a_photo_link_either(
    session_factory: sessionmaker[Session],
) -> None:
    """(b, photo path) B's photo signal is concerning only because it matches a high-risk check
    at the same address (depends_on_other_checks). That doesn't count towards B's independent
    level, so C, linked to B by phone, gets no "high" from it."""
    g = Graph(session_factory)
    b = g.check(
        "5 Pine St",
        "Milton",
        ("+19055550111",),
        {"photo_reuse_internal": ("high", {"depends_on_other_checks": True})},
    )
    c = g.check("6 Birch St", "Hamilton", ("+19055550111",))

    assert g.get(b)[:2] == ("some", "low")
    c_level, _, c_contact = g.get(c)
    assert (c_contact["severity"], c_level) == ("medium", "some")


@pytest.mark.integration
def test_a_linked_check_with_its_own_photo_reuse_evidence_escalates_contact_reuse(
    session_factory: sessionmaker[Session],
) -> None:
    """(c) Legitimate case: B reused photos from another address (its own evidence), so B is high
    independently. C shares B's phone: C's contact_reuse is high and says why. On its own that
    makes C "some"; together with C's own medium signal, "high"."""
    g = Graph(session_factory)
    b = g.check("7 Lake Rd", "Kingston", ("+19055550123",), STOLEN_PHOTOS)
    c_alone = g.check("8 Hill St", "Kingston", ("+19055550123",))
    c_with_own = g.check(
        "9 Hill St", "Kingston", ("+19055550123",), {"price_vs_market": ("medium", {})}
    )

    assert g.get(b)[:2] == ("high", "high")
    level, independent, contact = g.get(c_alone)
    assert contact["severity"] == "high"
    assert [h["own_signals"] for h in contact["linked_high_risk"]] == [["photo_reuse_internal"]]
    assert (level, independent) == ("some", "low")
    assert g.get(c_with_own)[:2] == ("high", "some")


@pytest.mark.integration
def test_contact_reuse_explains_the_linked_evidence_without_a_verdict(
    session_factory: sessionmaker[Session],
) -> None:
    g = Graph(session_factory)
    g.check("7 Lake Rd", "Kingston", ("+19055550123",), STOLEN_PHOTOS)
    c = g.check("8 Hill St", "Kingston", ("+19055550123",))
    with session_factory() as s:
        contact = next(r for r in s.get_one(Check, c).signal_results if r.name == "contact_reuse")
    assert contact.explanation is not None
    assert "photos used for another address" in contact.explanation
    assert "not proof about this one" in contact.explanation
