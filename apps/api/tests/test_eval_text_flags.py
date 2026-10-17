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


def test_valid_file_loads_with_optional_source_and_notes() -> None:
    lines = [
        '{"id": "1", "text": "t", "flags": ["a"], "source": "kijiji, by hand", "notes": "x"}',
        "",
        '{"id": "2", "text": "u", "flags": []}',
    ]

    examples, problems = H.validate_lines(lines, FLAGS)

    assert problems == []
    assert [(e.id, e.flags) for e in examples] == [("1", frozenset({"a"})), ("2", frozenset())]


def test_every_problem_is_reported_with_its_line_number() -> None:
    lines = [
        '{"id": "1", "text": "ok", "flags": []}',
        '{"id": "2", "text": "missing bracket", "flags": ["a"]',
        '["not", "an", "object"]',
        '{"id": "3", "text": "", "flags": "a"}',
        '{"id": "1", "text": "dup", "flags": ["nope", "a", "a"], "url": "x"}',
        '{"text": "no id", "flags": []}',
        '{"id": "6", "text": "t", "flags": [], "source": 5}',
    ]

    examples, problems = H.validate_lines(lines, FLAGS)

    assert [e.id for e in examples] == ["1"]
    assert problems == [
        "line 2: not valid JSON (Expecting ',' delimiter at column 54)",
        "line 3: must be a JSON object {...}",
        'line 4: "text" must be a non-empty string',
        'line 4: "flags" must be a list of flag names (use [] for none)',
        "line 5: unknown key(s) ['url']; allowed: id, text, flags, source, notes",
        'line 5: duplicate id "1" (first used on line 1)',
        "line 5: unknown flag(s) ['nope']",
        "line 5: flag(s) listed more than once ['a']",
        "line 6: missing key(s) ['id']",
        'line 7: "source" must be a string',
    ]


def test_load_raises_with_all_problems(tmp_path: Path) -> None:
    data = tmp_path / "labels.jsonl"
    data.write_text('{"id": "1", "text": "t", "flags": ["nope"]}\nnot json\n', encoding="utf-8")

    with pytest.raises(H.LabelFileError) as info:
        H.load(data, FLAGS)

    assert len(info.value.problems) == 2
    assert info.value.problems[0] == "line 1: unknown flag(s) ['nope']"


def test_empty_file_is_a_problem() -> None:
    assert H.validate_lines(["", "  "], FLAGS)[1] == ["the file has no examples"]


def test_cost_estimate_uses_list_prices() -> None:
    stats = H.CallStats(calls=2, input_tokens=[1000, 1000], output_tokens=[500, 500])

    opus = (2000 * 4 + 1000 * 20) / 1e6
    haiku = (2000 * 0.1 + 1000 * 0.5) / 1e6
    assert stats.estimated_cost_usd("claude-opus-5-5") == pytest.approx(opus)
    assert stats.estimated_cost_usd("claude-haiku-5-5") == pytest.approx(haiku)
    assert stats.estimated_cost_usd("unknown-model") is None


def test_synthetic_fixture_loads_with_the_real_flag_names() -> None:
    from rentalscamchecker.text_flags.flags import FLAG_NAMES

    examples = H.load(H.SYNTHETIC, FLAG_NAMES)

    assert len(examples) == 12
