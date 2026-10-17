"""text_red_flags: warning signs in the listing text (SPEC 4, ED-024).

Two paths, and the result always says which one ran (evidence.method):
- "external_ai_api": Anthropic's Claude reads the text (an external AI API we call; not our
  model) and returns flags with quotes. Every quote is checked against the listing; flags whose
  quote isn't in the text are dropped and counted.
- "keyword_rules": deterministic regular expressions, used when the API can't be used (error,
  timeout, refusal, daily cap, input too long). Less recall, so its severity is capped at medium.
Severity from the flags found is a plain rule (text_flags/flags.py: severity_for).
"""

from typing import Protocol

import structlog

from rentalscamchecker.signals.base import CheckInput, Severity, SignalContext, SignalResult
from rentalscamchecker.text_flags.flags import BY_NAME, FoundFlag, severity_for
from rentalscamchecker.text_flags.keywords import keyword_flags
from rentalscamchecker.text_flags.llm import LlmFlags, LlmUnavailable
from rentalscamchecker.text_flags.spans import locate_quote, snippet

log = structlog.get_logger("rentalscamchecker.text_red_flags")

AI_NOTE = (
    "An AI language model (Anthropic's Claude, an external service) read the listing text; "
    "each passage quoted below was checked against the listing."
)
RULES_NOTE = (
    "The listing text was checked with fixed keyword rules (the AI check wasn't available this "
    "time), which can miss signs worded differently."
)


class FlagExtractor(Protocol):
    model: str

    def extract(self, listing_text: str) -> LlmFlags: ...


class TextRedFlagsSignal:
    name = "text_red_flags"
    version = "1"

    def __init__(self, extractor: FlagExtractor) -> None:
        self._extractor = extractor

    def run(self, check: CheckInput, ctx: SignalContext) -> SignalResult:
        text = check.listing_text
        try:
            llm = self._extractor.extract(text)
        except LlmUnavailable as exc:
            log.warning("text_flags_fallback", reason=exc.reason)
            found = keyword_flags(text)
            severity = _cap(severity_for(found), "medium")
            evidence: dict[str, object] = {
                "method": "keyword_rules",
                "fallback_reason": exc.reason,
                "flags": _flag_evidence(text, found),
            }
            return self._result(severity, evidence, found, RULES_NOTE)

        found, unverified = _verified(text, llm)
        evidence = {
            "method": "external_ai_api",
            "provider": "Anthropic",
            "model": llm.model,
            "prompt_version": llm.prompt_version,
            "cached": llm.cached,
            "flags": _flag_evidence(text, found),
            "unverified_quotes_dropped": unverified,
        }
        return self._result(severity_for(found), evidence, found, AI_NOTE)

    def _result(
        self, severity: Severity, evidence: dict[str, object], found: list[FoundFlag], note: str
    ) -> SignalResult:
        return SignalResult(
            name=self.name,
            version=self.version,
            status="ok",
            severity=severity,
            evidence=evidence,
            explanation=_explanation(found, note),
            next_step=_next_step(found),
        )


def _verified(text: str, llm: LlmFlags) -> tuple[list[FoundFlag], int]:
    found: list[FoundFlag] = []
    seen: set[str] = set()
    unverified = 0
    for item in llm.flags:
        if item["flag"] in seen:
            continue
        span = locate_quote(text, item["quote"])
        if span is None:
            unverified += 1
            continue
        seen.add(item["flag"])
        found.append(FoundFlag(item["flag"], snippet(text, *span), *span))
    if unverified:
        log.warning("text_flags_unverified_quotes", count=unverified)
    return found, unverified


def _flag_evidence(text: str, found: list[FoundFlag]) -> list[dict[str, object]]:
    return [
        {
            "flag": f.name,
            "label": BY_NAME[f.name].label,
            "strength": BY_NAME[f.name].strength,
            "quote": snippet(text, f.start, f.end),
            "start": f.start,
            "end": f.end,
        }
        for f in found
    ]


def _cap(severity: Severity, ceiling: Severity) -> Severity:
    order: list[Severity] = ["none", "low", "medium", "high"]
    return severity if order.index(severity) <= order.index(ceiling) else ceiling


def _explanation(found: list[FoundFlag], note: str) -> str:
    if not found:
        return f"{note} None of the warning signs we look for were found in the text."
    lines = "; ".join(f'{BY_NAME[f.name].label} ("{f.quote}")' for f in found)
    return f"{note} Found: {lines}."


def _next_step(found: list[FoundFlag]) -> str:
    names = {f.name for f in found}
    if not names:
        return "Nothing to do about the text. Still see the place in person before paying."
    if names & {"deposit_before_viewing", "off_platform_payment", "keys_by_mail"}:
        return (
            "Don't send any money, gift cards or crypto before you have seen the place in person, "
            "met the landlord and signed a lease. Pay only by traceable methods."
        )
    if names & {"owner_unavailable_abroad", "refuses_viewing"}:
        return (
            "Insist on an in-person viewing with the landlord or their agent before paying "
            "anything, and check they actually own or manage the property."
        )
    return "Take your time: don't let a deadline push you into paying before you've seen it."
