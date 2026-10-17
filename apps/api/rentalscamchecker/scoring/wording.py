"""User-facing words for the overall risk level (CLAUDE.md product rules).

The product reports risk signals with evidence, never a verdict about the listing. The only
levels shown are these labels; the wording test (tests/test_wording.py) scans this module.
"""

LABELS = {
    "low": "Low risk",
    "some": "Some risk signals",
    "high": "High risk signals",
    "incomplete": "Incomplete",
}

_SUMMARIES = {
    "low": (
        "We didn't find risk signals in the checks we ran. That can't prove a listing is genuine: "
        "still see the place and meet the landlord before you pay anything."
    ),
    "some": (
        "We found some risk signals. Read each result below and follow its suggested next step "
        "before you pay anything."
    ),
    "high": (
        "We found strong risk signals. Don't send money or personal documents until you have "
        "seen the place in person and confirmed who owns or manages it."
    ),
    "incomplete": (
        "Some checks couldn't finish, so this report is incomplete. The results we do have are "
        "below; treat the listing with care until you can check it again."
    ),
}

GRAPH_NOTE = (
    "Part of this comes from other listings that share a photo or contact with this one. On their "
    "own, links to other listings can raise the level by one step at most."
)


def summary(level: str, from_other_listings: bool) -> str:
    text = _SUMMARIES[level]
    return f"{text} {GRAPH_NOTE}" if from_other_listings else text
