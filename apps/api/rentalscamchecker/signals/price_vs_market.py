"""price_vs_market: compare the asking rent with CMHC's average rent for the same city and
bedroom count (SPEC 4). A deterministic rule on public data; no model, no network (ED-021)."""

from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.reference_rents import Reference, bedroom_bucket, find_reference
from rentalscamchecker.signals.base import CheckInput, Severity, SignalContext, SignalResult

MONTH_NAMES = {4: "April", 10: "October"}
BEDROOM_LABELS = {0: "studio", 1: "1-bedroom", 2: "2-bedroom", 3: "3+ bedroom"}
SURVEY_CAVEAT = (
    "CMHC's average covers purpose-built rental buildings; basements, rooms and condos often rent "
    "for a different price, so a lower rent on its own proves nothing."
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
        with self._sessions() as session:
            reference = find_reference(session, check.city, check.bedrooms)
        label = BEDROOM_LABELS[bedroom_bucket(check.bedrooms)]
        if reference is None:
            return SignalResult(
                name=self.name,
                version=self.version,
                status="skipped",
                severity=None,
                evidence={"city": check.city, "unit_type": label, "reason": "no_reference_data"},
                explanation=(
                    f"We don't have CMHC reference rents for a {label} in {check.city} yet, so "
                    "the price wasn't compared with the local average."
                ),
                next_step=(
                    "Compare the price with several similar listings in the same area. Be careful "
                    "if it's much cheaper than all of them."
                ),
            )
        percent_below = (reference.rent_cents - check.rent_cents) / reference.rent_cents * 100
        severity = self._severity(percent_below)
        return SignalResult(
            name=self.name,
            version=self.version,
            status="ok",
            severity=severity,
            evidence=_evidence(check, reference, label, percent_below),
            explanation=_explanation(check, reference, label, percent_below, severity),
            next_step=_next_step(severity),
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


def _evidence(
    check: CheckInput, reference: Reference, label: str, percent_below: float
) -> dict[str, object]:
    # No thresholds here: evidence is shown to users, and SPEC 10 says not to expose rule
    # thresholds that someone could tune a listing against.
    return {
        "listing_rent_cents": check.rent_cents,
        "reference_rent_cents": reference.rent_cents,
        "percent_below_reference": round(percent_below, 1),
        "unit_type": label,
        "city": check.city,
        "reference_geography": reference.geography_name,
        "reference_level": reference.level,
        "cma": reference.cma_name,
        "survey": _survey(reference),
        "survey_year": reference.survey_year,
        "reliability": reference.reliability,
        "source": reference.source,
        "source_url": reference.source_url,
        "attribution": (
            f"Adapted from Canada Mortgage and Housing Corporation, Rental Market Survey, "
            f"{_survey(reference)}. This does not constitute an endorsement by Canada Mortgage "
            "and Housing Corporation of this product."
        ),
    }


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.0f}"


def _explanation(
    check: CheckInput, reference: Reference, label: str, percent_below: float, severity: Severity
) -> str:
    compared = (
        f"We compared the rent ({_dollars(check.rent_cents)}/month) with CMHC's "
        f"{_survey(reference)} average for a {label} in {_place(reference)} "
        f"({_dollars(reference.rent_cents)}/month)."
    )
    if percent_below <= 0:
        return f"{compared} The rent is {abs(percent_below):.0f}% above that average."
    found = f"{compared} The rent is {percent_below:.0f}% below that average."
    if severity == "none":
        return found
    return (
        f"{found} Rents far below the local average are a common feature of listings that turn "
        f"out not to be what they claim. {SURVEY_CAVEAT}"
    )


def _next_step(severity: Severity) -> str:
    if severity == "none":
        return "Nothing to do about the price. As always, see the unit before paying anything."
    return (
        "Ask the landlord why the rent is this low, view the unit in person before paying "
        "anything, and don't send a deposit to hold a place you haven't seen."
    )
