"""address_sanity: geocode the listing's address with OpenStreetMap Nominatim and check that it
exists, is in the city the listing claims, and isn't a non-residential place (SPEC 4).

Deterministic rules on top of an external geocoding API (ED-022). Severity:
- address not found: low (typos and new builds are common);
- found in a different city than claimed: medium;
- found, but the place is non-residential (a shop, school, office...): medium;
- both of the last two: high.
"""

from typing import Protocol

from rentalscamchecker.geocoding import ATTRIBUTION, GeocodeResult
from rentalscamchecker.reference_rents import normalize_city
from rentalscamchecker.signals.base import CheckInput, Severity, SignalContext, SignalResult

# Address parts that can name the place a person would type as their "city".
LOCALITY_KEYS = (
    "city",
    "town",
    "village",
    "hamlet",
    "municipality",
    "city_district",
    "borough",
    "suburb",
    "quarter",
    "neighbourhood",
)
# OSM classes that are never homes.
NON_RESIDENTIAL_CATEGORIES = frozenset(
    {"amenity", "shop", "office", "tourism", "leisure", "aeroway", "railway", "craft", "industrial"}
)
NON_RESIDENTIAL_BUILDINGS = frozenset(
    {
        "commercial",
        "industrial",
        "retail",
        "office",
        "warehouse",
        "school",
        "university",
        "college",
        "hospital",
        "church",
        "supermarket",
        "hotel",
        "parking",
        "public",
        "civic",
        "government",
    }
)
NON_RESIDENTIAL_LANDUSE = frozenset({"commercial", "industrial", "retail", "military"})


class Geocoder(Protocol):
    def geocode(self, address: str, city: str) -> GeocodeResult | None: ...


class AddressSanitySignal:
    name = "address_sanity"
    version = "1"

    def __init__(self, geocoder: Geocoder) -> None:
        self._geocoder = geocoder

    def run(self, check: CheckInput, ctx: SignalContext) -> SignalResult:
        # GeocodingUnavailable propagates: the job is retried with backoff.
        result = self._geocoder.geocode(check.address_text, check.city)
        if result is None:
            return self._result(
                "low",
                {"address": check.address_text, "city": check.city, "found": False},
                f'We couldn\'t find "{check.address_text}, {check.city}" on the map '
                "(OpenStreetMap). It may be a typo or a new building, but listings for places "
                "that don't exist do happen.",
                "Ask for the full address with the street number, look it up on a map yourself, "
                "and be careful if the poster won't give one before you pay.",
            )
        localities = _localities(result)
        city_matches = normalize_city(check.city) in localities
        non_residential = is_non_residential(result)
        precision = "street" if result.category == "highway" else "address"
        severity: Severity = (
            "high"
            if not city_matches and non_residential
            else "medium"
            if not city_matches or non_residential
            else "none"
        )
        evidence: dict[str, object] = {
            "address": check.address_text,
            "city": check.city,
            "found": True,
            "matched_address": result.display_name,
            "matched_city": _main_locality(result),
            "city_matches": city_matches,
            "place_category": result.category,
            "place_type": result.type,
            "non_residential": non_residential,
            "precision": precision,
            "lat": result.lat,
            "lon": result.lon,
        }
        return self._result(
            severity,
            evidence,
            _explanation(check, result, city_matches, non_residential, precision),
            _next_step(city_matches, non_residential),
        )

    def _result(
        self, severity: Severity, evidence: dict[str, object], explanation: str, next_step: str
    ) -> SignalResult:
        return SignalResult(
            name=self.name,
            version=self.version,
            status="ok",
            severity=severity,
            evidence={**evidence, "attribution": ATTRIBUTION},
            explanation=explanation,
            next_step=next_step,
        )


def is_non_residential(result: GeocodeResult) -> bool:
    if result.category in NON_RESIDENTIAL_CATEGORIES:
        return True
    if result.category == "building" and result.type in NON_RESIDENTIAL_BUILDINGS:
        return True
    return result.category == "landuse" and result.type in NON_RESIDENTIAL_LANDUSE


def _localities(result: GeocodeResult) -> set[str]:
    return {normalize_city(result.address[k]) for k in LOCALITY_KEYS if k in result.address}


def _main_locality(result: GeocodeResult) -> str | None:
    for key in ("city", "town", "village", "municipality", "hamlet"):
        if key in result.address:
            return result.address[key]
    return None


def _explanation(
    check: CheckInput,
    result: GeocodeResult,
    city_matches: bool,
    non_residential: bool,
    precision: str,
) -> str:
    found = f"We looked up the address on the map (OpenStreetMap) and found: {result.display_name}."
    parts = [found]
    if not city_matches:
        parts.append(
            f"That is in {_main_locality(result) or 'a different place'}, not {check.city} as the "
            "listing says."
        )
    if non_residential:
        parts.append(
            f"The map lists this place as non-residential ({result.category}: {result.type}), "
            "not as a home."
        )
    if city_matches and not non_residential:
        parts.append(f"It is in {check.city}, as the listing says.")
        if precision == "street":
            parts.append("Only the street was matched, not the exact building.")
    return " ".join(parts)


def _next_step(city_matches: bool, non_residential: bool) -> str:
    if city_matches and not non_residential:
        return "Nothing to do about the address. Still visit the unit before paying anything."
    return (
        "Ask the landlord to confirm the exact address, check it on a map, and visit in person "
        "before paying anything. Be careful if the address keeps changing or they won't share it."
    )
