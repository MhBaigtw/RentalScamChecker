"""Wording rule (CLAUDE.md product rules, M7): the product reports risk signals with evidence and
never calls a listing a scam. This scans every string literal in the API package (explanations,
next steps, labels, error messages; f-string parts included; docstrings excluded, they aren't
shown to users) and the report produced for a real check.

The brand name "RentalScamChecker" is allowed: the patterns look for verdict phrasing and for
"scam" as a word of its own, which the brand name never is.
"""

import ast
import re
from pathlib import Path

import pytest

import rentalscamchecker
from rentalscamchecker.scoring import wording

PACKAGE = Path(rentalscamchecker.__file__).resolve().parent

VERDICT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(is|looks like|appears to be|seems to be|must be|probably|likely|definitely|certainly)"
        r"\s+(an?\s+)?(scam|fraud|fake)\b",
        r"\bscam\s+(detected|confirmed|alert|listing|warning|likely)\b",
        r"\b(likely|probable|possible|confirmed|known|obvious)\s+scam\b",
        r"\b(fraudulent|fake|bogus)\s+(listing|ad|post|landlord|rental)\b",
        r"\bthis\s+(listing|ad|post|rental)\s+is\s+(fraud|fake|fraudulent|a\s+scam)\b",
    )
]
# Stricter than the verdict patterns: "scam" (or scams, scammer...) as a word on its own.
SCAM_WORD = re.compile(r"\bscam(s|mer|mers|ming|my)?\b", re.IGNORECASE)


def problems(text: str) -> list[str]:
    found = [p.pattern for p in VERDICT_PATTERNS if p.search(text)]
    if SCAM_WORD.search(text):
        found.append("the word 'scam'")
    return found


def user_facing_strings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_checker_itself_catches_verdicts_and_allows_the_brand() -> None:
    for bad in (
        "This listing is a scam.",
        "Scam detected!",
        "Likely scam: do not pay.",
        "This looks like a fraudulent listing.",
        "Beware of scammers.",
        "this ad is fake",
    ):
        assert problems(bad), bad
    for fine in (
        "RentalScamChecker",
        "rentalscamchecker.pipeline",
        "We found some risk signals.",
        "High risk signals",
        "Photos copied from another place are a common sign that a listing isn't what it claims.",
    ):
        assert problems(fine) == [], fine


def test_no_string_in_the_api_package_gives_a_verdict() -> None:
    files = sorted(PACKAGE.rglob("*.py"))
    assert len(files) > 30  # the scan really covers the package
    offending = [
        f"{path.relative_to(PACKAGE)}:{line}: {text!r} ({', '.join(found)})"
        for path in files
        for line, text in user_facing_strings(path)
        if (found := problems(text))
    ]
    assert offending == []


def test_risk_labels_are_exactly_the_allowed_levels() -> None:
    assert sorted(wording.LABELS.values()) == sorted(
        ["Low risk", "Some risk signals", "High risk signals", "Incomplete"]
    )


@pytest.mark.parametrize("level", ["low", "some", "high", "incomplete"])
@pytest.mark.parametrize("from_other_listings", [False, True])
def test_every_summary_is_clean(level: str, from_other_listings: bool) -> None:
    assert problems(wording.summary(level, from_other_listings)) == []
