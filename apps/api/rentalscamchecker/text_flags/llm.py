"""Red-flag extraction through the Anthropic Messages API (ED-024).

This is an EXTERNAL AI API we call (Anthropic's Claude), not a model we built or trained. It
receives only the listing text, which already has phone numbers and emails replaced by
placeholders (ED-016).

Guards, in the order they apply:
1. input longer than LLM_MAX_INPUT_CHARS -> not sent (the caller uses the keyword fallback);
2. cache: same text + prompt version + model -> stored answer, no call, no cost;
3. daily cap: a call is reserved in llm_calls under a Postgres advisory lock, so concurrent
   workers can't overshoot LLM_DAILY_CALL_CAP;
4. the call itself: structured output (JSON schema), one attempt, ANTHROPIC_TIMEOUT_SECONDS.
Every failure raises LlmUnavailable with a short reason; the signal then uses keyword rules.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import anthropic
import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.models import LlmCache, LlmCall
from rentalscamchecker.text_flags.flags import FLAG_NAMES
from rentalscamchecker.text_flags.prompt import (
    OUTPUT_SCHEMA,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    user_message,
)

log = structlog.get_logger("rentalscamchecker.llm")

# Models that accept server-side refusal fallbacks ("default" form). On a refusal the API re-runs
# the request on a fallback model inside the same call.
_SERVER_FALLBACK_MODELS = frozenset(
    {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
)
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Any constant: identifies the "daily cap" advisory lock.
_CAP_LOCK_KEY = 7_402_310


class LlmUnavailable(Exception):
    """The API result can't be used. `reason` is a short code stored in the signal evidence."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True)
class LlmFlags:
    # [{"flag": ..., "quote": ...}] exactly as the model returned them (quotes not yet verified).
    flags: list[dict[str, str]]
    model: str
    prompt_version: str
    cached: bool


def content_hash(listing_text: str) -> bytes:
    return hashlib.sha256(listing_text.encode("utf-8")).digest()


class ClaudeFlagExtractor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        client: anthropic.Anthropic,
        *,
        model: str,
        effort: str,
        max_tokens: int,
        daily_cap: int,
        max_input_chars: int,
        cap_lock_wait_seconds: float,
    ) -> None:
        self._sessions = session_factory
        self._client = client
        self.model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._daily_cap = daily_cap
        self._max_input_chars = max_input_chars
        self._cap_lock_wait_ms = int(cap_lock_wait_seconds * 1000)

    def extract(self, listing_text: str) -> LlmFlags:
        if len(listing_text) > self._max_input_chars:
            raise LlmUnavailable("input_too_long", f"{len(listing_text)} characters")
        key = content_hash(listing_text)
        cached = self._cached(key)
        if cached is not None:
            log.info("llm_cache_hit", model=self.model, prompt_version=PROMPT_VERSION)
            return LlmFlags(cached, self.model, PROMPT_VERSION, cached=True)
        call_id = self._reserve_call()
        flags = self._call(call_id, listing_text)
        with self._sessions.begin() as session:
            session.execute(
                insert(LlmCache)
                .values(
                    content_hash=key,
                    prompt_version=PROMPT_VERSION,
                    model=self.model,
                    response={"flags": flags},
                )
                .on_conflict_do_nothing()
            )
        return LlmFlags(flags, self.model, PROMPT_VERSION, cached=False)

    def _cached(self, key: bytes) -> list[dict[str, str]] | None:
        with self._sessions() as session:
            response = session.scalar(
                select(LlmCache.response).where(
                    LlmCache.content_hash == key,
                    LlmCache.prompt_version == PROMPT_VERSION,
                    LlmCache.model == self.model,
                )
            )
        if response is None:
            return None
        return list(response["flags"])

    def _reserve_call(self) -> int:
        """Insert this call's llm_calls row, unless today's cap is used up. The advisory lock
        makes count-then-insert atomic across every worker process."""
        with self._sessions.begin() as session:
            session.execute(text(f"SET LOCAL lock_timeout = {self._cap_lock_wait_ms}"))
            session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CAP_LOCK_KEY})
            today = session.scalar(
                select(func.count())
                .select_from(LlmCall)
                .where(LlmCall.called_at >= func.date_trunc("day", func.now(), "UTC"))
            )
            if (today or 0) >= self._daily_cap:
                log.warning("llm_daily_cap_reached", cap=self._daily_cap)
                raise LlmUnavailable("daily_cap_reached", f"{today} calls today")
            call_id = session.scalar(
                insert(LlmCall)
                .values(model=self.model, prompt_version=PROMPT_VERSION)
                .returning(LlmCall.id)
            )
        assert call_id is not None  # noqa: S101
        return int(call_id)

    def _call(self, call_id: int, listing_text: str) -> list[dict[str, str]]:
        started = time.perf_counter()
        try:
            response = self._create(listing_text)
        except anthropic.APITimeoutError as exc:  # subclass of APIConnectionError: check first
            self._finish(call_id, "error", started, error="timeout")
            raise LlmUnavailable("timeout") from exc
        except anthropic.APIConnectionError as exc:
            self._finish(call_id, "error", started, error="connection_error")
            raise LlmUnavailable("connection_error") from exc
        except anthropic.RateLimitError as exc:
            self._finish(call_id, "error", started, error="rate_limited")
            raise LlmUnavailable("rate_limited") from exc
        except anthropic.APIStatusError as exc:
            reason = f"api_error_{exc.status_code}"
            self._finish(call_id, "error", started, error=f"{reason}: {exc.message}"[:500])
            log.error("llm_api_error", status=exc.status_code, request_id=exc.request_id)
            raise LlmUnavailable(reason) from exc

        usage = response.usage
        tokens = {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
        if response.stop_reason == "refusal":
            self._finish(call_id, "refused", started, **tokens)
            raise LlmUnavailable("refused")
        if response.stop_reason == "max_tokens":
            self._finish(call_id, "truncated", started, **tokens)
            raise LlmUnavailable("truncated")
        try:
            flags = _parse(response)
        except ValueError as exc:
            self._finish(call_id, "invalid", started, error=str(exc)[:500], **tokens)
            raise LlmUnavailable("invalid_output", str(exc)) from exc
        latency_ms = self._finish(call_id, "ok", started, **tokens)
        log.info(
            "llm_call_ok",
            model=response.model,
            request_id=response._request_id,
            latency_ms=latency_ms,
            flags=len(flags),
            **tokens,
        )
        return flags

    def _create(self, listing_text: str) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_message(listing_text)}],
            "output_config": {
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
        }
        if self.model in _SERVER_FALLBACK_MODELS:
            return self._client.beta.messages.create(
                betas=[_FALLBACK_BETA], fallbacks="default", **kwargs
            )
        return self._client.messages.create(**kwargs)

    def _finish(
        self,
        call_id: int,
        outcome: str,
        started: float,
        *,
        error: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> int:
        """Record the call's outcome; returns the latency stored, so logs show the same number."""
        latency_ms = _ms(started)
        with self._sessions.begin() as session:
            session.execute(
                update(LlmCall)
                .where(LlmCall.id == call_id)
                .values(
                    outcome=outcome,
                    latency_ms=latency_ms,
                    error=error,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            )
        return latency_ms


def _parse(response: Any) -> list[dict[str, str]]:
    """The JSON text block, checked against the schema's shape (the API guarantees valid JSON
    for the schema, but a broken guarantee must not become a crash)."""
    text_block = next((b for b in response.content if getattr(b, "type", None) == "text"), None)
    if text_block is None:
        raise ValueError("no text block in the response")
    data = json.loads(text_block.text)
    if not isinstance(data, dict) or not isinstance(data.get("flags"), list):
        raise ValueError("response is not {'flags': [...]}")
    flags: list[dict[str, str]] = []
    for item in data["flags"]:
        if not isinstance(item, dict):
            raise ValueError("flag entry is not an object")
        flag, quote = item.get("flag"), item.get("quote")
        if flag not in FLAG_NAMES or not isinstance(quote, str):
            raise ValueError(f"unexpected flag entry: {item!r}"[:200])
        flags.append({"flag": flag, "quote": quote})
    return flags


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
