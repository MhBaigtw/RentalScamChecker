"""address_sanity rules, with a fake geocoder (no network, no DB)."""

import uuid

import pytest

from rentalscamchecker.geocoding import ATTRIBUTION, GeocodeResult
from rentalscamchecker.signals.address_sanity import AddressSanitySignal
from rentalscamchecker.signals.base import CheckInput, SignalContext
from tests.helpers import UNREACHABLE_DB_URL, make_settings
from tests.nominatim_fake import place

CTX = SignalContext(settings=make_settings(UNREACHABLE_DB_URL))


class FixedGeocoder:
    def __init__(self, raw: dict[str, object] | None) -> None:
        self.raw = raw
        self.calls: list[tuple[str, str]] = []

    def geocode(self, address: str, city: str) -> GeocodeResult | None:
        self.calls.append((address, city))
        if self.raw is None:
            return None
        address_parts = self.raw["address"]
        assert isinstance(address_parts, dict)
        return GeocodeResult(
            lat=float(str(self.raw["lat"])),
            lon=float(str(self.raw["lon"])),
            display_name=str(self.raw["display_name"]),
            category=str(self.raw["category"]),
            type=str(self.raw["type"]),
            address={str(k): str(v) for k, v in address_parts.items()},
        )


def run(
    raw: dict[str, object] | None, city: str = "Oakville"
) -> tuple[str | None, dict[str, object], str]:
    check = CheckInput(uuid.uuid4(), "text", 150000, 2, "1430 Trafalgar Rd", city, None)
    result = AddressSanitySignal(FixedGeocoder(raw)).run(check, CTX)
    assert result.status == "ok"
    assert result.explanation and result.next_step
    assert "scam" not in f"{result.explanation} {result.next_step}".lower()
    assert result.evidence["attribution"] == ATTRIBUTION
    return result.severity, result.evidence, result.explanation


def test_residential_address_in_the_claimed_city_is_fine() -> None:
    severity, evidence, explanation = run(place(city="Oakville"))

    assert severity == "none"
    assert (evidence["city_matches"], evidence["non_residential"]) == (True, False)
    assert evidence["matched_city"] == "Oakville"
    assert "It is in Oakville, as the listing says." in explanation


def test_address_not_found_is_low() -> None:
    severity, evidence, _ = run(None)

    assert severity == "low"
    assert evidence["found"] is False


def test_address_in_another_city_is_medium() -> None:
    severity, evidence, explanation = run(place(city="Mississauga"), city="Oakville")

    assert severity == "medium"
    assert evidence["city_matches"] is False
    assert "That is in Mississauga, not Oakville as the listing says." in explanation


@pytest.mark.parametrize(
    ("category", "type_"),
    [
        ("amenity", "school"),
        ("shop", "supermarket"),
        ("building", "commercial"),
        ("office", "company"),
    ],
)
def test_non_residential_place_is_medium(category: str, type_: str) -> None:
    severity, evidence, explanation = run(place(city="Oakville", category=category, type_=type_))

    assert severity == "medium"
    assert evidence["non_residential"] is True
    assert f"({category}: {type_})" in explanation


def test_wrong_city_and_non_residential_is_high() -> None:
    severity, _, _ = run(place(city="Brampton", category="amenity", type_="college"))

    assert severity == "high"


def test_typed_neighbourhood_matches_the_suburb() -> None:
    # People write "Streetsville" for an address Nominatim files under city=Mississauga.
    raw = place(city="Mississauga", extra_address={"suburb": "Streetsville"})

    severity, evidence, _ = run(raw, city="Streetsville")

    assert (severity, evidence["city_matches"]) == ("none", True)


def test_street_only_match_is_noted() -> None:
    raw = place(city="Oakville", category="highway", type_="primary", house_number=None)

    severity, evidence, explanation = run(raw)

    assert (severity, evidence["precision"]) == ("none", "street")
    assert "Only the street was matched" in explanation
