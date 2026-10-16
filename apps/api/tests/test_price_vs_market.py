"""price_vs_market against the synthetic reference data (tests/fixtures/rms_synthetic.csv)."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.reference_rents import load_rows, read_csv
from rentalscamchecker.signals.base import CheckInput, SignalContext
from rentalscamchecker.signals.price_vs_market import PriceVsMarketSignal
from tests.helpers import make_settings

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parent / "fixtures" / "rms_synthetic.csv"


@pytest.fixture
def signal(session_factory: sessionmaker[Session]) -> PriceVsMarketSignal:
    with session_factory.begin() as s:
        load_rows(s, read_csv(FIXTURE))
    return PriceVsMarketSignal(session_factory, medium_below_percent=30, high_below_percent=50)


def check(rent_dollars: int, city: str = "Oakville", bedrooms: int = 2) -> CheckInput:
    return CheckInput(uuid.uuid4(), "text", rent_dollars * 100, bedrooms, "1 Main St", city, None)


CTX = SignalContext(settings=make_settings())


@pytest.mark.parametrize(
    ("rent", "severity", "percent_below"),
    [
        (1100, "high", 54.2),  # Oakville 2-bed reference (synthetic): $2,400
        (1200, "high", 50.0),  # exactly at the high threshold
        (1500, "medium", 37.5),
        (1680, "medium", 30.0),  # exactly at the medium threshold
        (2000, "none", 16.7),
        (2600, "none", -8.3),  # above the reference
    ],
)
def test_severity_by_percent_below(
    signal: PriceVsMarketSignal, rent: int, severity: str, percent_below: float
) -> None:
    result = signal.run(check(rent), CTX)

    assert (result.status, result.severity) == ("ok", severity)
    assert result.evidence["percent_below_reference"] == percent_below


def test_evidence_has_reference_rent_year_cma_and_percent(signal: PriceVsMarketSignal) -> None:
    result = signal.run(check(1500), CTX)

    assert result.evidence == {
        "listing_rent_cents": 150000,
        "reference_rent_cents": 240000,
        "percent_below_reference": 37.5,
        "unit_type": "2-bedroom",
        "city": "Oakville",
        "reference_geography": "Oakville",
        "reference_level": "municipality",
        "cma": "Toronto",
        "survey": "October 2025",
        "survey_year": 2025,
        "reliability": "b",
        "source": "SYNTHETIC TEST DATA (not CMHC figures)",
        "source_url": "https://example.invalid/oakville",
        "attribution": (
            "Adapted from Canada Mortgage and Housing Corporation, Rental Market Survey, "
            "October 2025. This does not constitute an endorsement by Canada Mortgage and Housing "
            "Corporation of this product."
        ),
    }
    assert result.explanation == (
        "We compared the rent ($1,500/month) with CMHC's October 2025 average for a 2-bedroom in "
        "Oakville ($2,400/month). The rent is 38% below that average. Rents far below the local "
        "average are a common feature of listings that turn out not to be what they claim. "
        "CMHC's average covers purpose-built rental buildings; basements, rooms and condos often "
        "rent for a different price, so a lower rent on its own proves nothing."
    )


def test_cma_fallback_is_named_in_the_explanation(signal: PriceVsMarketSignal) -> None:
    result = signal.run(check(1000, city="Caledon"), CTX)  # Caledon's own value is suppressed

    assert result.evidence["reference_level"] == "cma"
    assert result.explanation is not None
    assert "in the Toronto metropolitan area ($2,000/month)" in result.explanation


def test_rent_above_reference_says_above(signal: PriceVsMarketSignal) -> None:
    result = signal.run(check(2600), CTX)

    assert result.explanation is not None
    assert result.explanation.endswith("The rent is 8% above that average.")


def test_unknown_city_is_skipped_with_a_next_step(signal: PriceVsMarketSignal) -> None:
    result = signal.run(check(1000, city="Kingston"), CTX)

    assert (result.status, result.severity) == ("skipped", None)
    assert result.evidence == {
        "city": "Kingston",
        "unit_type": "2-bedroom",
        "reason": "no_reference_data",
    }
    assert result.next_step


def test_thresholds_are_never_in_user_facing_output(signal: PriceVsMarketSignal) -> None:
    result = signal.run(check(1500), CTX)

    shown = f"{result.evidence} {result.explanation} {result.next_step}".lower()
    assert "threshold" not in shown
    assert "30%" not in shown and "50%" not in shown


@pytest.mark.parametrize("rent", [1100, 1500, 2000, 2600])
def test_wording_never_calls_the_listing_a_scam(signal: PriceVsMarketSignal, rent: int) -> None:
    result = signal.run(check(rent), CTX)

    assert "scam" not in f"{result.explanation} {result.next_step}".lower()
