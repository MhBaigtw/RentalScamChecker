"""The scoring rule (SPEC 5, ED-027). DETERMINISTIC: points per signal, summed, mapped to a level.

Two levels are computed for every check:
- `independent_level`: from the check's own evidence only, leaving out every result that depends
  on other checks' risk (`evidence.depends_on_other_checks`). Signals that look at a linked
  check read THIS level, never `level`, so risk travels at most one hop through the graph.
- `level`: all results, except that graph-dependent ones may raise it at most `max_steps_up`
  levels above `independent_level`. A fake high-risk listing that uses a real landlord's phone
  number can make the landlord's clean listings "some", never "high".

Then "incomplete": a non-optional signal errored or never finished, and the level isn't already
"high" (missing evidence could only add risk, so a "high" stands).
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from rentalscamchecker.scoring.config import ScoringConfig

Level = Literal["low", "some", "high"]
StoredLevel = Literal["low", "some", "high", "incomplete"]
_ORDER: tuple[Level, ...] = ("low", "some", "high")


@dataclass(frozen=True)
class SignalOutcome:
    """What the scorer needs from one stored signal result."""

    name: str
    status: str  # "ok" | "skipped" | "error"
    severity: str | None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def depends_on_other_checks(self) -> bool:
        return self.evidence.get("depends_on_other_checks") is True


@dataclass(frozen=True)
class Score:
    level: StoredLevel
    independent_level: StoredLevel
    # Signals that added points, strongest first. Names only: points and weights stay internal.
    contributing: list[str]
    # Graph-dependent results were held back by graph.max_steps_up.
    graph_capped: bool
    # Signals (not optional ones) that errored or never finished.
    missing: list[str]


def score(outcomes: list[SignalOutcome], config: ScoringConfig) -> Score:
    ok = [o for o in outcomes if o.status == "ok" and o.severity is not None]
    own_level = _level(_points([o for o in ok if not o.depends_on_other_checks], config), config)
    full_level = _level(_points(ok, config), config)
    ceiling = _ORDER[min(_ORDER.index(own_level) + config.max_steps_up, len(_ORDER) - 1)]
    capped = _ORDER.index(full_level) > _ORDER.index(ceiling)
    level: Level = ceiling if capped else full_level

    missing = sorted(
        o.name for o in outcomes if o.status == "error" and o.name not in config.optional_signals
    )
    contributing = [
        o.name for o in sorted(ok, key=lambda o: -_points([o], config)) if _points([o], config) > 0
    ]
    return Score(
        level=_incomplete_unless_high(level, missing),
        independent_level=_incomplete_unless_high(own_level, missing),
        contributing=contributing,
        graph_capped=capped,
        missing=missing,
    )


def _points(outcomes: list[SignalOutcome], config: ScoringConfig) -> float:
    return sum(
        config.severity_points[o.severity or "none"] * config.weight(o.name) for o in outcomes
    )


def _level(points: float, config: ScoringConfig) -> Level:
    if points >= config.high_at:
        return "high"
    if points >= config.some_at:
        return "some"
    return "low"


def _incomplete_unless_high(level: Level, missing: list[str]) -> StoredLevel:
    return "incomplete" if missing and level != "high" else level
