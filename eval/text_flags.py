"""Evaluate text_red_flags: per-flag precision and recall against hand-labeled listings.

Run from apps/api (the venv has the app installed):
    .venv/Scripts/python.exe ../../eval/text_flags.py --mode keyword
    .venv/Scripts/python.exe ../../eval/text_flags.py --mode llm      # calls the Anthropic API

Data: eval/private/text_labels.jsonl (gitignored: real listing text belongs to third parties).
One JSON object per line: {"id": "...", "text": "...", "flags": ["keys_by_mail", ...]}.
Until that file exists, the harness runs on eval/fixtures/text_labels_synthetic.jsonl and says so:
those examples were written for testing the harness and their scores mean nothing about real
listings.

--mode llm goes through the same code as the signal (ClaudeFlagExtractor + quote verification):
it uses the database cache and counts toward LLM_DAILY_CALL_CAP. If the API is unavailable for an
example, the keyword fallback answers, as in production; the report counts how often.
Reports go to eval/private/reports/ (gitignored).
"""

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
PRIVATE = EVAL_DIR / "private" / "text_labels.jsonl"
SYNTHETIC = EVAL_DIR / "fixtures" / "text_labels_synthetic.jsonl"
REPORTS = EVAL_DIR / "private" / "reports"


@dataclass(frozen=True)
class Example:
    id: str
    text: str
    flags: frozenset[str]


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


def load(path: Path, known_flags: tuple[str, ...]) -> list[Example]:
    examples: list[Example] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        unknown = set(row["flags"]) - set(known_flags)
        if unknown:
            raise ValueError(f"{path}:{line_no}: unknown flags {sorted(unknown)}")
        examples.append(Example(str(row["id"]), row["text"], frozenset(row["flags"])))
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


def report(
    scores: list[FlagScore],
    *,
    n: int,
    mode: str,
    dataset: str,
    synthetic: bool,
    fallbacks: int,
    extra: str = "",
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
    if mode == "llm":
        lines.append(f"- Examples answered by the keyword fallback (API unavailable): {fallbacks}")
    if extra:
        lines.append(f"- {extra}")
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


def main() -> None:
    # Imported here so `score` and `report` stay importable without the app's settings.
    from sqlalchemy.orm import sessionmaker

    from rentalscamchecker.config import get_settings
    from rentalscamchecker.db import make_engine
    from rentalscamchecker.signals.base import CheckInput, SignalContext
    from rentalscamchecker.signals.registry import AVAILABLE
    from rentalscamchecker.text_flags.flags import FLAG_NAMES
    from rentalscamchecker.text_flags.keywords import keyword_flags

    parser = argparse.ArgumentParser(description="Evaluate text_red_flags.")
    parser.add_argument("--mode", choices=["keyword", "llm"], required=True)
    parser.add_argument("--data", type=Path, default=None)
    args = parser.parse_args()

    path = args.data or (PRIVATE if PRIVATE.exists() else SYNTHETIC)
    synthetic = path.resolve() == SYNTHETIC.resolve()
    examples = load(path, FLAG_NAMES)
    if synthetic:
        print("NOTE: eval/private/text_labels.jsonl not found; using the SYNTHETIC fixture.\n")

    predicted: list[frozenset[str]] = []
    fallbacks = 0
    extra = ""
    if args.mode == "keyword":
        predicted = [frozenset(f.name for f in keyword_flags(e.text)) for e in examples]
    else:
        settings = get_settings()
        sessions = sessionmaker(
            make_engine(settings.database_url, settings.database_connect_timeout)
        )
        signal = AVAILABLE["text_red_flags"](settings, sessions)
        ctx = SignalContext(settings=settings)
        cached = 0
        for e in examples:
            check = CheckInput(uuid.uuid4(), e.text, 100000, 1, "-", "-", None)
            result = signal.run(check, ctx)
            flags = result.evidence.get("flags", [])
            if not isinstance(flags, list):
                raise TypeError("evidence.flags is not a list")
            predicted.append(frozenset(str(f["flag"]) for f in flags))
            if result.evidence.get("method") == "keyword_rules":
                fallbacks += 1
            cached += bool(result.evidence.get("cached"))
        extra = f"Model: {settings.anthropic_model}; answers served from cache: {cached}"

    scores = score([e.flags for e in examples], predicted, FLAG_NAMES)
    text = report(
        scores,
        n=len(examples),
        mode=args.mode,
        dataset=str(path.relative_to(EVAL_DIR.parent))
        if path.is_relative_to(EVAL_DIR.parent)
        else str(path),
        synthetic=synthetic,
        fallbacks=fallbacks,
        extra=extra,
    )
    print(text)
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS / f"text_flags_{args.mode}_{'synthetic_' if synthetic else ''}{stamp}.md"
    out.write_text(text, encoding="utf-8")
    print(f"Report written to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
