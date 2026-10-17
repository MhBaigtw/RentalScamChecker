"""Load and validate a versioned scoring config (scoring/<version>.toml). DETERMINISTIC."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCORING_VERSION = "v1"
_DIR = Path(__file__).resolve().parent
SEVERITIES = ("none", "low", "medium", "high")


class ScoringConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ScoringConfig:
    version: str
    severity_points: dict[str, int]
    some_at: float
    high_at: float
    weights: dict[str, float]
    max_steps_up: int
    optional_signals: frozenset[str]

    def weight(self, signal: str) -> float:
        return self.weights.get(signal, 1.0)


def load_config(version: str = SCORING_VERSION, directory: Path = _DIR) -> ScoringConfig:
    path = directory / f"{version}.toml"
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ScoringConfigError(f"can't read scoring config {path}: {exc}") from exc
    return parse_config(raw, version)


def parse_config(raw: dict[str, Any], version: str) -> ScoringConfig:
    if raw.get("version") != version:
        raise ScoringConfigError(f"config says version {raw.get('version')!r}, not {version!r}")
    points = raw.get("severity_points", {})
    if set(points) != set(SEVERITIES) or not all(type(v) is int for v in points.values()):
        raise ScoringConfigError(f"severity_points needs an integer for each of {SEVERITIES}")
    if not points["none"] == 0 < points["low"] <= points["medium"] <= points["high"]:
        raise ScoringConfigError("severity_points: none must be 0, and points must not decrease")
    levels = raw.get("levels", {})
    some_at, high_at = levels.get("some"), levels.get("high")
    if not (_is_number(some_at) and _is_number(high_at) and 0 < some_at < high_at):
        raise ScoringConfigError("levels.some and levels.high must be numbers with 0 < some < high")
    weights = raw.get("weights", {})
    if not all(_is_number(w) and w >= 0 for w in weights.values()):
        raise ScoringConfigError("weights must be non-negative numbers")
    steps = raw.get("graph", {}).get("max_steps_up")
    if type(steps) is not int or steps not in (0, 1, 2):
        raise ScoringConfigError("graph.max_steps_up must be 0, 1 or 2")
    optional = raw.get("incomplete", {}).get("optional_signals", [])
    if not isinstance(optional, list) or not all(isinstance(k, str) for k in optional):
        raise ScoringConfigError("incomplete.optional_signals must be a list of signal names")
    return ScoringConfig(
        version=version,
        severity_points=dict(points),
        some_at=float(some_at),
        high_at=float(high_at),
        weights={k: float(v) for k, v in weights.items()},
        max_steps_up=steps,
        optional_signals=frozenset(optional),
    )


def _is_number(value: object) -> bool:
    return type(value) in (int, float)
