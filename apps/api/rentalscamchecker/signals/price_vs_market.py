"""price_vs_market: compare the asking rent with CMHC's average rent for the same city and
bedroom count (SPEC 4). A deterministic rule on public data; no model, no network (ED-021).

Baseline: the vacant-unit average (what landlords ask for empty units, closest to what a new
tenant pays), estimated from local averages when CMHC suppressed it (see find_reference). Rooms
are skipped: CMHC averages describe whole units only. Basements are compared with a caveat.
"""

from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.reference_rents import (
    ESTIMATED_VACANT,
    Reference,
    bedroom_bucket,
    find_reference,
)
from rentalscamchecker.signals.base import CheckInput, Severity, SignalContext, SignalResult

MONTH_NAMES = {4: "April", 10: "October"}
BEDROOM_LABELS = {0: "studio", 1: "1-bedroom", 2: "2-bedroom", 3: "3+ bedroom"}
BASELINE_LABELS = {
    "vacant_units": "average asking rent for vacant",
    ESTIMATED_VACANT: "estimated average asking rent (estimated from local averages) for vacant",
    "all_units": "average rent (all units, including long-time tenants) for",
}
SURVEY_CAVEAT = (
    "CMHC's figures cover purpose-built rental buildings; condos and basement apartments can "
    "rent for a different price, so a lower rent on its own proves nothing."
)
BASEMENT_CAVEAT = (
    "This listing is a basement apartment. Basements are not in CMHC's survey and often rent "
    "for less than these averages, so read this comparison with care."
)
ESTIMATE_NOTE = (
    "CMHC doesn't publish a vacant-unit figure for this area, so it was estimated from local "
    "averages: the area's overall average, scaled by how much more vacant units cost than the "
    "overall average across the {cma} metropolitan area ({ratio:.2f}x)."
)


class PriceVsMarketSignal:
    name = "price_vs_market"
    version = "1"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        medium_below_percent: float,
        high_below_percent: float,
    ) -> None:
        self._sessions = session_factory
        self._medium = medium_below_percent
        self._high = high_below_percent

    def run(self, check: CheckInput, ctx: SignalContext) -> SignalResult:
        bedroom_type = BEDROOM_LABELS[bedroom_bucket(check.bedrooms)]
        if check.unit_type == "room":
            return self._skipped(
                {"city": check.city, "unit_type": "room", "reason": "room_not_comparable"},
                "This listing is a room in a shared place. CMHC's rent data covers whole units "
                "only, so the price wasn't compared with a local average.",
                "Compare the price with other rooms offered nearby. Be careful if it's much "
                "cheaper than all of them, and don't pay before seeing the room.",
            )
        with self._sessions() as session:
            reference = find_reference(session, check.city, check.bedrooms)
        if reference is None:
            return self._skipped(
                {
                    "city": check.city,
                    "bedroom_type": bedroom_type,
                    "unit_type": check.unit_type,
                    "reason": "no_reference_data",
                },
                f"We don't have CMHC reference rents for a {bedroom_type} in {check.city} yet, "
                "so the price wasn't compared with the local average.",
                "Compare the price with several similar listings in the same area. Be careful "
                "if it's much cheaper than all of them.",
            )
        percent_below = (reference.rent_cents - check.rent_cents) / reference.rent_cents * 100
        severity = self._severity(percent_below)
        return SignalResult(
            name=self.name,
            version=self.version,
            status="ok",
            severity=severity,
            evidence=_evidence(check, reference, bedroom_type, percent_below),
            explanation=_explanation(check, reference, bedroom_type, percent_below, severity),
            next_step=_next_step(severity),
        )

    def _skipped(
        self, evidence: dict[str, object], explanation: str, next_step: str
    ) -> SignalResult:
        return SignalResult(
            name=self.name,
            version=self.version,
            status="skipped",
            severity=None,
            evidence=evidence,
            explanation=explanation,
            next_step=next_step,
        )

    def _severity(self, percent_below: float) -> Severity:
        if percent_below >= self._high:
            return "high"
        if percent_below >= self._medium:
            return "medium"
        return "none"


def _survey(reference: Reference) -> str:
    month = MONTH_NAMES.get(reference.survey_month, f"month {reference.survey_month}")
    return f"{month} {reference.survey_year}"


def _place(reference: Reference) -> str:
    if reference.level == "cma":
        return f"the {reference.cma_name} metropolitan area"
    return reference.geography_name


def _baseline(reference: Reference, bedroom_type: str) -> str:
    """E.g. "average asking rent for vacant 2-bedroom units in Oakville"."""
    return f"{BASELINE_LABELS[reference.rent_basis]} {bedroom_type} units in {_place(reference)}"


def _evidence(
    check: CheckInput, reference: Reference, bedroom_type: str, percent_below: float
) -> dict[str, object]:
    # No thresholds here: evidence is shown to users, and SPEC 10 says not to expose rule
    # thresholds that someone could tune a listing against.
    return {
        "listing_rent_cents": check.rent_cents,
        "reference_rent_cents": reference.rent_cents,
        "percent_below_reference": round(percent_below, 1),
        "bedroom_type": bedroom_type,
        "unit_type": check.unit_type,
        "city": check.city,
        # Which baseline was used (ED-021): vacant_units preferred, all_units as fallback.
        "rent_basis": reference.rent_basis,
        "baseline": _baseline(reference, bedroom_type),
        "reference_geography": reference.geography_name,
        "reference_level": reference.level,
        "cmhc_geography": reference.source_geography,
        "cma": reference.cma_name,
        "survey": _survey(reference),
        "survey_year": reference.survey_year,
        "reliability": reference.reliability,
        "source": reference.source,
        "source_url": reference.source_url,
        **_estimate_evidence(reference),
        "attribution": (
            f"Adapted from Canada Mortgage and Housing Corporation, Rental Market Survey, "
            f"{_survey(reference)}. This does not constitute an endorsement by Canada Mortgage "
            "and Housing Corporation of this product."
        ),
    }


def _estimate_evidence(reference: Reference) -> dict[str, object]:
    if reference.estimate is None:
        return {}
    e = reference.estimate
    return {
        "estimate_ratio": e.ratio,
        "estimate_inputs": {
            "municipality_all_units_cents": e.municipality_all_units_cents,
            "cma_vacant_cents": e.cma_vacant_cents,
            "cma_all_units_cents": e.cma_all_units_cents,
        },
    }


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.0f}"


def _explanation(
    check: CheckInput,
    reference: Reference,
    bedroom_type: str,
    percent_below: float,
    severity: Severity,
) -> str:
    compared = (
        f"We compared the rent ({_dollars(check.rent_cents)}/month) with CMHC's "
        f"{_survey(reference)} {_baseline(reference, bedroom_type)} "
        f"({_dollars(reference.rent_cents)}/month)."
    )
    if percent_below <= 0:
        parts = [compared, f"The rent is {abs(percent_below):.0f}% above that average."]
    else:
        parts = [compared, f"The rent is {percent_below:.0f}% below that average."]
        if severity != "none":
            parts.append(
                "Rents far below the local average are a common feature of listings that turn "
                "out not to be what they claim."
            )
            parts.append(SURVEY_CAVEAT)
    if reference.estimate is not None:
        parts.append(ESTIMATE_NOTE.format(cma=reference.cma_name, ratio=reference.estimate.ratio))
    if check.unit_type == "basement":
        parts.append(BASEMENT_CAVEAT)
    return " ".join(parts)


def _next_step(severity: Severity) -> str:
    if severity == "none":
        return "Nothing to do about the price. As always, see the unit before paying anything."
    return (
        "Ask the landlord why the rent is this low, view the unit in person before paying "
        "anything, and don't send a deposit to hold a place you haven't seen."
    )
