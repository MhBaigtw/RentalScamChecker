"""Evaluate text_red_flags: per-flag precision and recall against hand-labeled listings.

Run from apps/api (the venv has the app installed):
    .venv/Scripts/python.exe ../../eval/text_flags.py --mode keyword
    .venv/Scripts/python.exe ../../eval/text_flags.py --mode llm [--model claude-haiku-5-5]
    .venv/Scripts/python.exe ../../eval/text_flags.py --validate-only

Data: eval/private/text_labels.jsonl (gitignored: real listing text belongs to third parties).
Format and labeling rules: docs/labeling-guide.md. The file is validated first; every problem is
reported with its line number and nothing is scored until the file is clean.
Until that file exists, the harness runs on eval/fixtures/text_labels_synthetic.jsonl and says so:
those examples were written for testing the harness and their scores mean nothing about real
listings.

Both modes first redact phone numbers and emails, exactly as POST /checks does, so the eval sees
what production sees and no contact details are sent to the API.
--mode llm goes through the same code as the signal (ClaudeFlagExtractor + quote verification):
it uses the database cache and counts toward LLM_DAILY_CALL_CAP. If the API is unavailable for an
example, the keyword fallback answers, as in production; the report counts how often. Latency,
tokens and estimated cost come from the llm_calls rows this run created.
Reports go to eval/private/reports/ (gitignored).
"""

import argparse
import json
import statistics
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
PRIVATE = EVAL_DIR / "private" / "text_labels.jsonl"
SYNTHETIC = EVAL_DIR / "fixtures" / "text_labels_synthetic.jsonl"
REPORTS = EVAL_DIR / "private" / "reports"

REQUIRED_KEYS = {"id", "text", "flags"}
OPTIONAL_KEYS = {"source", "notes"}
# USD per million tokens (input, output): Anthropic list prices as of 2026-10. Haiku 5.5's price is
# for prompts up to 100K tokens. Used only for cost ESTIMATES in reports.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


@dataclass(frozen=True)
class Example:
    id: str
    text: str
    flags: frozenset[str]


class LabelFileError(ValueError):
    def __init__(self, path: Path, problems: list[str]) -> None:
        super().__init__(f"{path}: {len(problems)} problem(s)")
        self.path = path
        self.problems = problems


@dataclass(frozen=True)
class FlagScore:
    flag: str
    tp: int
    fp: int
    fn: int

    @property
    def support(self) -> int:
        return self.tp + self.fn

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None


@dataclass
class CallStats:
    """llm_calls rows created during one run."""

    calls: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    latencies_ms: list[int] = field(default_factory=list)
    input_tokens: list[int] = field(default_factory=list)
    output_tokens: list[int] = field(default_factory=list)

    def estimated_cost_usd(self, model: str) -> float | None:
        price = PRICES_PER_MTOK.get(model)
        if price is None:
            return None
        return (sum(self.input_tokens) * price[0] + sum(self.output_tokens) * price[1]) / 1e6


def validate_lines(
    lines: list[str], known_flags: tuple[str, ...]
) -> tuple[list[Example], list[str]]:
    """Parse and check every line. Returns the examples and a list of 'line N: problem' strings;
    any problem means the file must not be scored."""
    examples: list[Example] = []
    problems: list[str] = []
    first_seen: dict[str, int] = {}
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue  # blank lines are allowed
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"line {line_no}: not valid JSON ({exc.msg} at column {exc.colno})")
            continue
        if not isinstance(row, dict):
            problems.append(f"line {line_no}: must be a JSON object {{...}}")
            continue
        line_problems: list[str] = []
        missing = REQUIRED_KEYS - row.keys()
        if missing:
            line_problems.append(f"missing key(s) {sorted(missing)}")
        unknown_keys = row.keys() - REQUIRED_KEYS - OPTIONAL_KEYS
        if unknown_keys:
            line_problems.append(
                f"unknown key(s) {sorted(unknown_keys)}; allowed: id, text, flags, source, notes"
            )
        example_id, text, flags = row.get("id"), row.get("text"), row.get("flags")
        if "id" in row and (not isinstance(example_id, str) or not example_id.strip()):
            line_problems.append('"id" must be a non-empty string')
        elif isinstance(example_id, str) and example_id in first_seen:
            line_problems.append(
                f'duplicate id "{example_id}" (first used on line {first_seen[example_id]})'
            )
        if "text" in row and (not isinstance(text, str) or not text.strip()):
            line_problems.append('"text" must be a non-empty string')
        if "flags" in row:
            if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
                line_problems.append('"flags" must be a list of flag names (use [] for none)')
            else:
                unknown = sorted(set(flags) - set(known_flags))
                if unknown:
                    line_problems.append(f"unknown flag(s) {unknown}")
                repeated = sorted({f for f in flags if flags.count(f) > 1})
                if repeated:
                    line_problems.append(f"flag(s) listed more than once {repeated}")
        for key in ("source", "notes"):
            if key in row and not isinstance(row[key], str):
                line_problems.append(f'"{key}" must be a string')
        problems += [f"line {line_no}: {p}" for p in line_problems]
        if line_problems:
            continue
        if not (isinstance(example_id, str) and isinstance(text, str) and isinstance(flags, list)):
            continue  # unreachable: line_problems covers these; narrows types for mypy
        first_seen[example_id] = line_no
        examples.append(Example(example_id, text, frozenset(flags)))
    if not examples and not problems:
        problems.append("the file has no examples")
    return examples, problems


def load(path: Path, known_flags: tuple[str, ...]) -> list[Example]:
    examples, problems = validate_lines(path.read_text(encoding="utf-8").splitlines(), known_flags)
    if problems:
        raise LabelFileError(path, problems)
    return examples


def score(
    gold: list[frozenset[str]], predicted: list[frozenset[str]], flags: tuple[str, ...]
) -> list[FlagScore]:
    """Per-flag counts over examples. Each example counts at most once per flag."""
    if len(gold) != len(predicted):
        raise ValueError("gold and predicted must have the same length")
    scores = []
    for flag in flags:
        tp = sum(1 for g, p in zip(gold, predicted, strict=True) if flag in g and flag in p)
        fp = sum(1 for g, p in zip(gold, predicted, strict=True) if flag not in g and flag in p)
        fn = sum(1 for g, p in zip(gold, predicted, strict=True) if flag in g and flag not in p)
        scores.append(FlagScore(flag, tp, fp, fn))
    return scores


def micro(scores: list[FlagScore]) -> FlagScore:
    return FlagScore(
        "ALL (micro)",
        sum(s.tp for s in scores),
        sum(s.fp for s in scores),
        sum(s.fn for s in scores),
    )


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def call_stats_lines(stats: CallStats, model: str) -> list[str]:
    if not stats.calls:
        return ["- API calls made: 0 (all answers from the cache or the fallback)"]
    cost = stats.estimated_cost_usd(model)
    per_call = "n/a" if cost is None else f"${cost / stats.calls:.4f}"
    total = "n/a" if cost is None else f"${cost:.4f}"
    return [
        f"- API calls made: {stats.calls} (outcomes: {dict(sorted(stats.outcomes.items()))})",
        f"- Latency per call: median {statistics.median(stats.latencies_ms):,.0f} ms, "
        f"max {max(stats.latencies_ms):,} ms",
        f"- Tokens per call: input median {statistics.median(stats.input_tokens):,.0f} "
        f"(max {max(stats.input_tokens):,}), output median "
        f"{statistics.median(stats.output_tokens):,.0f} (max {max(stats.output_tokens):,})"
        if stats.input_tokens
        else "- Tokens: none recorded",
        f"- Estimated cost: {per_call} per call, {total} total (list prices; estimate)",
    ]


def report(
    scores: list[FlagScore],
    *,
    n: int,
    mode: str,
    dataset: str,
    synthetic: bool,
    fallbacks: int,
    extra: list[str] | None = None,
) -> str:
    lines = [
        f"# text_red_flags evaluation ({mode})",
        "",
        f"- Date: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- Dataset: {dataset}",
        f"- **Set size: {n} examples**",
    ]
    if synthetic:
        lines.append(
            "- **SYNTHETIC FIXTURE: these examples were written to test the harness. The scores "
            "below say nothing about accuracy on real listings.**"
        )
    if mode.startswith("llm"):
        lines.append(f"- Examples answered by the keyword fallback (API unavailable): {fallbacks}")
    lines += [f"- {e}" if not e.startswith("- ") else e for e in (extra or [])]
    lines += [
        "",
        "| flag | support | TP | FP | FN | precision | recall |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in [*scores, micro(scores)]:
        lines.append(
            f"| {s.flag} | {s.support} | {s.tp} | {s.fp} | {s.fn} | {_pct(s.precision)} | "
            f"{_pct(s.recall)} |"
        )
    lines += [
        "",
        "Support = labeled positives. Precision is n/a when the flag was never predicted.",
    ]
    return "\n".join(lines) + "\n"


def collect_call_stats(session: Any, after_id: int) -> CallStats:
    import sqlalchemy as sa

    rows = session.execute(
        sa.text(
            "SELECT outcome, latency_ms, input_tokens, output_tokens FROM llm_calls "
            "WHERE id > :after ORDER BY id"
        ),
        {"after": after_id},
    ).all()
    stats = CallStats()
    for outcome, latency, tokens_in, tokens_out in rows:
        stats.calls += 1
        stats.outcomes[outcome] = stats.outcomes.get(outcome, 0) + 1
        if latency is not None:
            stats.latencies_ms.append(latency)
        if tokens_in is not None and tokens_out is not None:
            stats.input_tokens.append(tokens_in)
            stats.output_tokens.append(tokens_out)
    return stats


def last_call_id(session: Any) -> int:
    import sqlalchemy as sa

    return int(session.execute(sa.text("SELECT coalesce(max(id), 0) FROM llm_calls")).scalar_one())


def main() -> None:
    # Imported here so the pure functions stay importable without the app's settings.
    from sqlalchemy.orm import sessionmaker

    from rentalscamchecker.config import get_settings
    from rentalscamchecker.contacts import extract_contacts
    from rentalscamchecker.db import make_engine
    from rentalscamchecker.signals.base import CheckInput, SignalContext
    from rentalscamchecker.signals.registry import AVAILABLE
    from rentalscamchecker.text_flags.flags import FLAG_NAMES
    from rentalscamchecker.text_flags.keywords import keyword_flags

    parser = argparse.ArgumentParser(description="Evaluate text_red_flags.")
    parser.add_argument("--mode", choices=["keyword", "llm"])
    parser.add_argument("--model", default=None, help="override ANTHROPIC_MODEL for this run")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if not args.validate_only and args.mode is None:
        parser.error("--mode is required unless --validate-only")

    path = args.data or (PRIVATE if PRIVATE.exists() else SYNTHETIC)
    synthetic = path.resolve() == SYNTHETIC.resolve()
    try:
        examples = load(path, FLAG_NAMES)
    except LabelFileError as exc:
        print(
            f"{exc.path} has {len(exc.problems)} problem(s); fix them and run again:",
            file=sys.stderr,
        )
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        raise SystemExit(2) from None
    if args.validate_only:
        print(f"{path}: OK, {len(examples)} examples")
        return
    if synthetic:
        print("NOTE: eval/private/text_labels.jsonl not found; using the SYNTHETIC fixture.\n")
    # Same input as production: phone numbers and emails replaced by [phone] / [email] (ED-016)
    # before any rule or API sees the text. Labels are flag names only, so they are unaffected.
    region = get_settings().contact_default_region
    examples = [Example(e.id, extract_contacts(e.text, region).redacted, e.flags) for e in examples]

    predicted: list[frozenset[str]] = []
    fallbacks = 0
    extra: list[str] = []
    if args.mode == "keyword":
        predicted = [frozenset(f.name for f in keyword_flags(e.text)) for e in examples]
    else:
        settings = get_settings()
        if args.model:
            settings = settings.model_copy(update={"anthropic_model": args.model})
        sessions = sessionmaker(
            make_engine(settings.database_url, settings.database_connect_timeout)
        )
        signal = AVAILABLE["text_red_flags"](settings, sessions)
        ctx = SignalContext(settings=settings)
        with sessions() as s:
            watermark = last_call_id(s)
        cached = dropped = 0
        reasons: dict[str, int] = {}
        for e in examples:
            check = CheckInput(uuid.uuid4(), e.text, 100000, 1, "-", "-", None)
            result = signal.run(check, ctx)
            flags = result.evidence.get("flags", [])
            if not isinstance(flags, list):
                raise TypeError("evidence.flags is not a list")
            predicted.append(frozenset(str(f["flag"]) for f in flags))
            if result.evidence.get("method") == "keyword_rules":
                fallbacks += 1
                reason = str(result.evidence.get("fallback_reason"))
                reasons[reason] = reasons.get(reason, 0) + 1
            cached += bool(result.evidence.get("cached"))
            dropped_here = result.evidence.get("unverified_quotes_dropped", 0)
            dropped += int(dropped_here) if isinstance(dropped_here, int) else 0
        with sessions() as s:
            stats = collect_call_stats(s, watermark)
        extra = [
            f"Model: {settings.anthropic_model}, effort {settings.anthropic_effort}, "
            "prompt flags-v1",
            f"Answers served from cache: {cached}",
            f"Invented/unverifiable quotes dropped: {dropped}",
            f"Fallback reasons: {reasons or 'none'}",
            *call_stats_lines(stats, settings.anthropic_model),
        ]

    scores = score([e.flags for e in examples], predicted, FLAG_NAMES)
    try:
        dataset = str(path.resolve().relative_to(EVAL_DIR.parent))
    except ValueError:
        dataset = str(path)
    text = report(
        scores,
        n=len(examples),
        mode=args.mode if args.mode == "keyword" else f"llm, {settings.anthropic_model}",
        dataset=dataset,
        synthetic=synthetic,
        fallbacks=fallbacks,
        extra=extra,
    )
    print(text)
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    label = args.mode if args.mode == "keyword" else f"llm_{settings.anthropic_model}"
    out = REPORTS / f"text_flags_{label}_{'synthetic_' if synthetic else ''}{stamp}.md"
    out.write_text(text, encoding="utf-8")
    print(f"Report written to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
