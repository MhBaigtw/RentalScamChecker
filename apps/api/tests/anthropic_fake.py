"""A fake Anthropic client for tests: records every request, returns canned responses or raises
the SDK's real exception types. No network. Response shapes mirror the fields our code reads
(content[].type/.text, stop_reason, usage, model, _request_id); they are hand-written."""

import json
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def response(
    flags: list[dict[str, str]] | None = None,
    *,
    text: str | None = None,
    stop_reason: str = "end_turn",
    input_tokens: int = 900,
    output_tokens: int = 150,
    model: str = "claude-opus-5-5",
) -> SimpleNamespace:
    body = text if text is not None else json.dumps({"flags": flags or []})
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=body),
        ],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        model=model,
        _request_id="req_fake",
    )


def timeout_error() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(request=_REQUEST)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=_REQUEST)


def status_error(status: int) -> anthropic.APIStatusError:
    resp = httpx2.Response(status, request=_REQUEST)
    if status == 429:
        return anthropic.RateLimitError("rate limited", response=resp, body=None)
    if status >= 500:
        return anthropic.InternalServerError("server error", response=resp, body=None)
    return anthropic.APIStatusError("api error", response=resp, body=None)


@dataclass
class FakeAnthropic:
    """`outcomes` are consumed one per call: a response object or an exception to raise. The
    last outcome repeats. `by_text` overrides by a substring of the user message."""

    outcomes: list[Any] = field(default_factory=lambda: [response([])])
    by_text: dict[str, Any] = field(default_factory=dict)
    delay: float = 0.0
    calls: list[dict[str, Any]] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self.messages = SimpleNamespace(create=lambda **kw: self._create("messages", kw))
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kw: self._create("beta.messages", kw))
        )

    def _create(self, endpoint: str, kwargs: dict[str, Any]) -> Any:
        with self._lock:
            self.calls.append(kwargs)
            self.endpoints.append(endpoint)
            index = len(self.calls) - 1
        if self.delay:
            time.sleep(self.delay)
        user_text = kwargs["messages"][0]["content"]
        outcome = next(
            (o for needle, o in self.by_text.items() if needle in user_text),
            self.outcomes[min(index, len(self.outcomes) - 1)],
        )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
