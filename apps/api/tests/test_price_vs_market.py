"""price_vs_market against the synthetic reference data (tests/fixtures/rms_synthetic.csv).
Oakville 2-bedroom (synthetic): vacant units $2,600, all units $2,400."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.reference_rents import load_rows, read_csv
from rentalscamchecker.signals.base import CheckInput, SignalContext
from rentalscamchecker.signals.price_vs_market import (
    BASEMENT_CAVEAT,
    SURVEY_CAVEAT,
    PriceVsMarketSignal,
)
from tests.helpers import make_settings

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parent / "fixtures" / "rms_synthetic.csv"


@pytest.fixture
def signal(session_factory: sessionmaker[Session]) -> PriceVsMarketSignal:
    with session_factory.begin() as s:
        load_rows(s, read_csv(FIXTURE))
    return PriceVsMarketSignal(session_factory, medium_below_percent=30, high_below_percent=50)


def check(
    rent_dollars: int, city: str = "Oakville", bedrooms: int = 2, unit_type: str = "whole_unit"
) -> CheckInput:
    return CheckInput(
        uuid.uuid4(), "text", rent_dollars * 100, bedrooms, "1 Main St", city, None, unit_type
    )


CTX = SignalContext(settings=make_settings())


@pytest.mark.parametrize(
    ("rent", "severity", "percent_below"),
    [
        (1100, "high", 57.7),
        (1300, "high", 50.0),  # exactly at the high threshold
        (1500, "medium", 42.3),
        (1820, "medium", 30.0),  # exactly at the medium threshold
        (2200, "none", 15.4),
        (2800, "none", -7.7),  # above the reference
    ],
)
def test_severity_by_percent_below_the_vacant_unit_baseline(
    signal: PriceVsMarketSignal, rent: int, severity: str, percent_below: float
) -> None:
    result = signal.run(check(rent), CTX)

    assert (result.status, result.severity) == ("ok", severity)
    assert result.evidence["percent_below_reference"] == percent_below
    assert result.evidence["rent_basis"] == "vacant_units"


def test_evidence_has_baseline_reference_rent_year_cma_and_percent(
    signal: PriceVsMarketSignal,
) -> None:
    result = signal.run(check(1500), CTX)

    assert result.evidence == {
        "listing_rent_cents": 150000,
        "reference_rent_cents": 260000,
        "percent_below_reference": 42.3,
        "bedroom_type": "2-bedroom",
        "unit_type": "whole_unit",
        "city": "Oakville",
        "rent_basis": "vacant_units",
        "baseline": "average asking rent for vacant 2-bedroom units in Oakville",
        "reference_geography": "Oakville",
        "reference_level": "municipality",
        "cmhc_geography": "Zone 23 - Oakville",
        "cma": "Toronto",
        "survey": "October 2025",
        "survey_year": 2025,
        "reliability": "b",
        "source": "SYNTHETIC TEST DATA (not CMHC figures)",
        "source_url": "https://example.invalid/oakville-vacant_units",
        "attribution": (
            "Adapted from Canada Mortgage and Housing Corporation, Rental Market Survey, "
            "October 2025. This does not constitute an endorsement by Canada Mortgage and Housing "
            "Corporation of this product."
        ),
    }
    assert result.explanation == (
        "We compared the rent ($1,500/month) with CMHC's October 2025 average asking rent for "
        "vacant 2-bedroom units in Oakville ($2,600/month). The rent is 42% below that average. "
        "Rents far below the local average are a common feature of listings that turn out not to "
        f"be what they claim. {SURVEY_CAVEAT}"
    )


def test_cma_fallback_is_named_in_the_explanation(signal: PriceVsMarketSignal) -> None:
    result = signal.run(check(1000, city="Caledon"), CTX)  # Caledon's own values are suppressed

    assert (result.evidence["reference_level"], result.evidence["rent_basis"]) == (
        "cma",
        "vacant_units",
    )
    assert result.explanation is not None
    assert (
        "vacant 2-bedroom units in the Toronto metropolitan area ($2,500/month)"
        in result.explanation
    )


def test_all_units_fallback_says_so(signal: PriceVsMarketSignal) -> None:
    result = signal.run(check(1000, city="Hamilton"), CTX)  # no vacant figure for Hamilton

    assert result.evidence["rent_basis"] == "all_units"
    assert result.evidence["baseline"] == (
        "average rent (all units, including long-time tenants) for 2-bedroom units in Hamilton"
    )


def test_rent_above_reference_says_above(signal: PriceVsMarketSignal) -> None:
    result = signal.run(check(2800), CTX)

    assert result.explanation is not None
    assert result.explanation.endswith("The rent is 8% above that average.")


def test_room_is_skipped_because_cmhc_covers_whole_units_only(
    signal: PriceVsMarketSignal,
) -> None:
    result = signal.run(check(700, unit_type="room"), CTX)

    assert (result.status, result.severity) == ("skipped", None)
    assert result.evidence == {
        "city": "Oakville",
        "unit_type": "room",
        "reason": "room_not_comparable",
    }
    assert result.explanation is not None
    assert "CMHC's rent data covers whole units only" in result.explanation
    assert result.next_step


def test_basement_is_compared_with_a_caveat(signal: PriceVsMarketSignal) -> None:
    result = signal.run(check(1500, unit_type="basement"), CTX)

    assert (result.status, result.severity) == ("ok", "medium")
    assert result.evidence["unit_type"] == "basement"
    assert result.explanation is not None
    assert result.explanation.endswith(BASEMENT_CAVEAT)


def test_unknown_city_is_skipped_with_a_next_step(signal: PriceVsMarketSignal) -> None:
    result = signal.run(check(1000, city="Kingston"), CTX)

    assert (result.status, result.severity) == ("skipped", None)
    assert result.evidence == {
        "city": "Kingston",
        "bedroom_type": "2-bedroom",
        "unit_type": "whole_unit",
        "reason": "no_reference_data",
    }
    assert result.next_step


def test_thresholds_are_never_in_user_facing_output(signal: PriceVsMarketSignal) -> None:
    result = signal.run(check(1500), CTX)

    shown = f"{result.evidence} {result.explanation} {result.next_step}".lower()
    assert "threshold" not in shown
    assert "30%" not in shown and "50%" not in shown


@pytest.mark.parametrize(
    ("rent", "unit_type"),
    [(1100, "whole_unit"), (1500, "basement"), (2200, "whole_unit"), (700, "room")],
)
def test_wording_never_calls_the_listing_a_scam(
    signal: PriceVsMarketSignal, rent: int, unit_type: str
) -> None:
    result = signal.run(check(rent, unit_type=unit_type), CTX)

    assert "scam" not in f"{result.explanation} {result.next_step}".lower()
