"""ClaudeFlagExtractor against a fake Anthropic client and the real test database: request shape,
cache, daily cap (also under concurrency), error mapping. No network."""

import threading
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.text_flags.llm import ClaudeFlagExtractor, LlmUnavailable
from rentalscamchecker.text_flags.prompt import OUTPUT_SCHEMA, PROMPT_VERSION, SYSTEM_PROMPT
from tests.anthropic_fake import (
    FakeAnthropic,
    connection_error,
    response,
    status_error,
    timeout_error,
)

pytestmark = pytest.mark.integration

TEXT = "Owner abroad. Send the deposit to hold it and I'll mail the keys."
FLAGS = [{"flag": "keys_by_mail", "quote": "I'll mail the keys"}]


def extractor(
    sf: sessionmaker[Session],
    fake: FakeAnthropic,
    *,
    model: str = "claude-opus-5-5",
    cap: int = 200,
    max_input_chars: int = 8000,
) -> ClaudeFlagExtractor:
    return ClaudeFlagExtractor(
        sf,
        fake,  # type: ignore[arg-type]
        model=model,
        effort="medium",
        max_tokens=8000,
        daily_cap=cap,
        max_input_chars=max_input_chars,
        cap_lock_wait_seconds=5,
    )


def calls(sf: sessionmaker[Session]) -> list[tuple[Any, ...]]:
    with sf() as s:
        rows = s.execute(
            sa.text(
                "SELECT outcome, input_tokens, output_tokens, error, model, prompt_version "
                "FROM llm_calls ORDER BY id"
            )
        ).all()
    return [tuple(r) for r in rows]


def test_request_uses_structured_output_effort_and_server_fallback(
    session_factory: sessionmaker[Session],
) -> None:
    fake = FakeAnthropic([response(FLAGS)])

    result = extractor(session_factory, fake).extract(TEXT)

    assert result.flags == FLAGS
    assert (result.model, result.prompt_version, result.cached) == (
        "claude-opus-5-5",
        PROMPT_VERSION,
        False,
    )
    (request,) = fake.calls
    assert fake.endpoints == ["beta.messages"]
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["fallbacks"] == "default"
    assert request["model"] == "claude-opus-5-5"
    assert request["max_tokens"] == 8000
    assert request["system"] == SYSTEM_PROMPT
    assert request["messages"] == [{"role": "user", "content": f"<listing>\n{TEXT}\n</listing>"}]
    assert request["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
    }
    assert calls(session_factory) == [("ok", 900, 150, None, "claude-opus-5-5", PROMPT_VERSION)]


def test_models_without_server_fallback_use_the_plain_endpoint(
    session_factory: sessionmaker[Session],
) -> None:
    fake = FakeAnthropic([response([])])

    extractor(session_factory, fake, model="claude-haiku-5-5").extract(TEXT)

    assert fake.endpoints == ["messages"]
    assert "fallbacks" not in fake.calls[0]


def test_same_text_is_answered_from_the_cache(session_factory: sessionmaker[Session]) -> None:
    fake = FakeAnthropic([response(FLAGS)])
    ex = extractor(session_factory, fake)

    first = ex.extract(TEXT)
    second = ex.extract(TEXT)

    assert (first.cached, second.cached) == (False, True)
    assert second.flags == FLAGS
    assert len(fake.calls) == 1
    assert len(calls(session_factory)) == 1  # cache hits don't count toward the daily cap


def test_cache_is_per_model(session_factory: sessionmaker[Session]) -> None:
    fake = FakeAnthropic([response(FLAGS)])

    extractor(session_factory, fake, model="claude-opus-5-5").extract(TEXT)
    extractor(session_factory, fake, model="claude-haiku-5-5").extract(TEXT)

    assert len(fake.calls) == 2


def test_daily_cap_stops_calls(session_factory: sessionmaker[Session]) -> None:
    fake = FakeAnthropic([response([])])
    ex = extractor(session_factory, fake, cap=2)

    ex.extract("listing one")
    ex.extract("listing two")
    with pytest.raises(LlmUnavailable) as info:
        ex.extract("listing three")

    assert info.value.reason == "daily_cap_reached"
    assert len(fake.calls) == 2
    ex.extract("listing one")  # cached: still allowed, and still free


def test_daily_cap_holds_under_concurrency(session_factory: sessionmaker[Session]) -> None:
    fake = FakeAnthropic([response([])], delay=0.2)
    ex = extractor(session_factory, fake, cap=3)
    reasons: list[str] = []
    lock = threading.Lock()

    def attempt(n: int) -> None:
        try:
            ex.extract(f"distinct listing {n}")
            outcome = "called"
        except LlmUnavailable as exc:
            outcome = exc.reason
        with lock:
            reasons.append(outcome)

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(reasons) == ["called"] * 3 + ["daily_cap_reached"] * 5
    assert len(fake.calls) == 3


def test_long_input_is_not_sent(session_factory: sessionmaker[Session]) -> None:
    fake = FakeAnthropic()

    with pytest.raises(LlmUnavailable) as info:
        extractor(session_factory, fake, max_input_chars=100).extract("x" * 101)

    assert info.value.reason == "input_too_long"
    assert fake.calls == []
    assert calls(session_factory) == []


@pytest.mark.parametrize(
    ("outcome", "reason", "row"),
    [
        (timeout_error(), "timeout", ("error", None, None, "timeout")),
        (connection_error(), "connection_error", ("error", None, None, "connection_error")),
        (status_error(429), "rate_limited", ("error", None, None, "rate_limited")),
        (status_error(500), "api_error_500", ("error", None, None, "api_error_500: server error")),
        (response(stop_reason="refusal"), "refused", ("refused", 900, 150, None)),
        (response(stop_reason="max_tokens"), "truncated", ("truncated", 900, 150, None)),
    ],
)
def test_api_problems_become_a_reason_and_are_logged(
    session_factory: sessionmaker[Session], outcome: Any, reason: str, row: tuple[Any, ...]
) -> None:
    fake = FakeAnthropic([outcome])

    with pytest.raises(LlmUnavailable) as info:
        extractor(session_factory, fake).extract(TEXT)

    assert info.value.reason == reason
    assert [c[:4] for c in calls(session_factory)] == [row]
    with session_factory() as s:  # failures are never cached
        assert s.execute(sa.text("SELECT count(*) FROM llm_cache")).scalar_one() == 0


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        '{"wrong": []}',
        '{"flags": [{"flag": "not_a_flag", "quote": "x"}]}',
        '{"flags": [{"flag": "keys_by_mail"}]}',
    ],
)
def test_malformed_output_is_rejected(session_factory: sessionmaker[Session], body: str) -> None:
    fake = FakeAnthropic([response(text=body)])

    with pytest.raises(LlmUnavailable) as info:
        extractor(session_factory, fake).extract(TEXT)

    assert info.value.reason == "invalid_output"
    assert calls(session_factory)[0][0] == "invalid"
