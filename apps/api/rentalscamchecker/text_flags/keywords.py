"""Deterministic keyword fallback for text_red_flags (ED-024): regular expressions, no model.

Used when the Anthropic API can't be used (error, timeout, refusal, daily cap, input too long).
It favours precision over recall: patterns only match phrasings that are hard to read innocently,
so it misses paraphrases the model would catch. Each match returns the matched text as evidence.
"""

import re

from rentalscamchecker.text_flags.flags import FoundFlag

_I = re.IGNORECASE
PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "deposit_before_viewing": (
        re.compile(
            r"\b(deposit|first month'?s? rent|payment)\b[^.!?\n]{0,60}\b(before|prior to|to hold|"
            r"to reserve|to secure)\b[^.,;!?\n]{0,25}",
            _I,
        ),
        re.compile(
            r"\b(send|pay|transfer)\b[^.!?\n]{0,30}\bdeposit\b[^.!?\n]{0,40}\b(hold|reserve|secure)\b",
            _I,
        ),
    ),
    "owner_unavailable_abroad": (
        re.compile(
            r"\b(i am|i'm|we are|we're|owner is|landlord is|currently)\b[^.!?\n]{0,40}"
            r"\b(out of (the )?country|abroad|overseas|outside (of )?canada|"
            r"on (a )?mission|deployed)\b",
            _I,
        ),
    ),
    "keys_by_mail": (
        re.compile(
            r"\bkeys?\b[^.!?\n]{0,40}\b(mail(ed)?|courier(ed)?|ship(ped)?|fed ?ex|ups|canada post|"
            r"post(ed)?)\b",
            _I,
        ),
        re.compile(r"\b(mail|courier|ship|send)\b[^.!?\n]{0,20}\bthe keys?\b", _I),
    ),
    "off_platform_payment": (
        re.compile(
            r"\b(gift ?cards?|bitcoin|btc|crypto(currency)?|usdt|western union|moneygram|"
            r"wire transfer|bank wire)\b",
            _I,
        ),
    ),
    "refuses_viewing": (
        re.compile(r"\bno (in[- ]person )?(viewings?|showings?)\b", _I),
        re.compile(r"\b(viewings?|showings?) (are |is )?not (possible|allowed|available)\b", _I),
        re.compile(
            r"\b(can'?t|cannot|unable to)\b[^.!?\n]{0,15}\b(show|view)\b[^.!?\n]{0,10}"
            r"\b(it|the (unit|place|apartment|room|house))\b",
            _I,
        ),
    ),
    "urgency_pressure": (
        re.compile(
            r"\b(first come,? first served|first to (pay|send)|act fast|hurry|won'?t last|"
            r"many (people|others|applicants) (are )?interested|today only)\b",
            _I,
        ),
    ),
    "too_good_to_be_true_language": (
        re.compile(
            r"\b(way below market|far below market|too good to (miss|pass up)|steal of a deal|"
            r"unbeatable price)\b",
            _I,
        ),
    ),
}


def keyword_flags(text: str) -> list[FoundFlag]:
    """First match per flag, in the order flags are defined."""
    found: list[FoundFlag] = []
    for name, patterns in PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                found.append(FoundFlag(name, match.group(0).strip(), match.start(), match.end()))
                break
    return found
