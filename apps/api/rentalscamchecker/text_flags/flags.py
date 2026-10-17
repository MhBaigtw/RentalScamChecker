"""The fixed set of text red flags (SPEC.md section 4) and the deterministic rules around them.

Each flag has a user-facing label and a strength. Severity is a plain rule over what was found
(ED-024): two or more strong flags -> high; one strong flag -> medium; only weak flags -> low.
"""

from dataclasses import dataclass
from typing import Literal

Strength = Literal["strong", "weak"]
FlagName = Literal[
    "deposit_before_viewing",
    "owner_unavailable_abroad",
    "keys_by_mail",
    "off_platform_payment",
    "refuses_viewing",
    "urgency_pressure",
    "too_good_to_be_true_language",
]


@dataclass(frozen=True)
class FlagDefinition:
    name: FlagName
    strength: Strength
    # Shown to users next to the quoted text.
    label: str
    # Given to the model: what counts, and what doesn't.
    definition: str


FLAGS: tuple[FlagDefinition, ...] = (
    FlagDefinition(
        "deposit_before_viewing",
        "strong",
        "Asks for a deposit or other payment before you can see the place",
        "The poster asks for a deposit, first month's rent, an application fee or any other "
        "payment before the renter has viewed the unit in person (including 'to hold it').",
    ),
    FlagDefinition(
        "owner_unavailable_abroad",
        "strong",
        "Says the owner is away and can't meet you",
        "The owner or landlord says they are out of the country, abroad, travelling, deployed, "
        "on a mission or otherwise unable to meet in person.",
    ),
    FlagDefinition(
        "keys_by_mail",
        "strong",
        "Offers to mail or courier the keys",
        "Keys will be mailed, couriered, shipped or left somewhere instead of handed over in "
        "person.",
    ),
    FlagDefinition(
        "off_platform_payment",
        "strong",
        "Asks for payment by gift card, cryptocurrency or wire transfer",
        "Payment by gift cards, cryptocurrency, wire transfer (Western Union, MoneyGram, bank "
        "wire) or another hard-to-reverse method. Interac e-Transfer on its own is common in "
        "Canada and does NOT count.",
    ),
    FlagDefinition(
        "refuses_viewing",
        "strong",
        "Won't allow an in-person viewing",
        "The poster says viewings are not possible or not allowed, or offers only photos or a "
        "video instead of seeing the unit.",
    ),
    FlagDefinition(
        "urgency_pressure",
        "weak",
        "Pressures you to decide or pay quickly",
        "Pressure to act fast: many other interested people, 'first to pay gets it', deadlines "
        "of hours, 'today only'.",
    ),
    FlagDefinition(
        "too_good_to_be_true_language",
        "weak",
        "Uses too-good-to-be-true language",
        "Explicit claims of an exceptional deal: 'way below market', 'too good to miss', "
        "'everything included for a fraction of the price'. A normal description of features "
        "does NOT count.",
    ),
)
BY_NAME: dict[str, FlagDefinition] = {f.name: f for f in FLAGS}
FLAG_NAMES: tuple[str, ...] = tuple(f.name for f in FLAGS)


@dataclass(frozen=True)
class FoundFlag:
    name: str
    # Exact text from the listing, as it appears there.
    quote: str
    start: int
    end: int


def severity_for(found: list[FoundFlag]) -> Literal["none", "low", "medium", "high"]:
    strong = {f.name for f in found if BY_NAME[f.name].strength == "strong"}
    if len(strong) >= 2:
        return "high"
    if strong:
        return "medium"
    return "low" if found else "none"
