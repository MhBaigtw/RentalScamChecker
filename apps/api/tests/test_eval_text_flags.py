"""The eval harness's scoring and report (eval/text_flags.py), without the app or the API."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

EVAL_FILE = Path(__file__).resolve().parents[3] / "eval" / "text_flags.py"


def load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("eval_text_flags", EVAL_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_text_flags"] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


H = load_harness()
FLAGS = ("a", "b")


def test_per_flag_counts_precision_and_recall() -> None:
    gold = [frozenset({"a"}), frozenset({"a", "b"}), frozenset(), frozenset({"b"})]
    pred = [frozenset({"a"}), frozenset({"a"}), frozenset({"a"}), frozenset({"b"})]

    a, b = H.score(gold, pred, FLAGS)

    assert (a.tp, a.fp, a.fn, a.support) == (2, 1, 0, 2)
    assert (a.precision, a.recall) == (2 / 3, 1.0)
    assert (b.tp, b.fp, b.fn) == (1, 0, 1)
    assert (b.precision, b.recall) == (1.0, 0.5)
    total = H.micro([a, b])
    assert (total.tp, total.fp, total.fn) == (3, 1, 1)


def test_never_predicted_flag_has_no_precision() -> None:
    (a,) = H.score([frozenset({"a"})], [frozenset()], ("a",))

    assert a.precision is None
    assert a.recall == 0.0


def test_mismatched_lengths_are_an_error() -> None:
    with pytest.raises(ValueError, match="same length"):
        H.score([frozenset()], [], FLAGS)


def test_report_states_set_size_and_synthetic_warning() -> None:
    scores = H.score([frozenset({"a"})], [frozenset({"a"})], FLAGS)

    text = H.report(scores, n=1, mode="keyword", dataset="x", synthetic=True, fallbacks=0)

    assert "**Set size: 1 examples**" in text
    assert "SYNTHETIC FIXTURE" in text
    assert "| b | 0 | 0 | 0 | 0 | n/a | n/a |" in text


def test_loader_rejects_unknown_flags(tmp_path: Path) -> None:
    data = tmp_path / "labels.jsonl"
    data.write_text('{"id": "1", "text": "t", "flags": ["nope"]}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="unknown flags"):
        H.load(data, FLAGS)


def test_synthetic_fixture_loads_with_the_real_flag_names() -> None:
    from rentalscamchecker.text_flags.flags import FLAG_NAMES

    examples = H.load(H.SYNTHETIC, FLAG_NAMES)

    assert len(examples) == 12
