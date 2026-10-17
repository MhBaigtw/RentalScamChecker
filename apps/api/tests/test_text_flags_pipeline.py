"""text_red_flags in the app: startup checks, the timeout budget, and the full journey (API ->
worker -> report) on the AI path and, with a simulated API failure, on the keyword fallback."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.jobs.worker import Worker, WorkerConfig
from rentalscamchecker.main import create_app
from rentalscamchecker.pipeline import build_handlers
from rentalscamchecker.signals.text_red_flags import TextRedFlagsSignal
from rentalscamchecker.text_flags.llm import ClaudeFlagExtractor
from tests.anthropic_fake import FakeAnthropic, response, status_error, timeout_error
from tests.helpers import UNREACHABLE_DB_URL, image_bytes, make_settings

LISTING = (
    "Bright 2 bedroom near Sheridan. I am currently working overseas so I can't show it. "
    "Send the deposit to hold it and I'll mail the keys. Call [phone]."
)


@pytest.mark.parametrize("key", [None, "", "  "])
def test_api_refuses_to_start_with_text_red_flags_but_no_api_key(key: str | None) -> None:
    settings = make_settings(
        UNREACHABLE_DB_URL,
        enabled_signals="text_red_flags",
        anthropic_api_key=SecretStr(key) if key is not None else None,
    )

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY is not set"):
        create_app(settings)


def test_anthropic_worst_case_must_fit_inside_the_job_timeout() -> None:
    # cap lock 5 + timeout 25 x (1 retry + 1) = 55 s > 45 s job timeout.
    with pytest.raises(ValueError, match="Anthropic worst case"):
        make_settings(UNREACHABLE_DB_URL, anthropic_max_retries=1)


def test_default_anthropic_budget_fits() -> None:
    s = make_settings(UNREACHABLE_DB_URL)

    worst = s.llm_cap_lock_wait_seconds + s.anthropic_timeout_seconds * (
        s.anthropic_max_retries + 1
    )
    assert worst == 30 < s.job_timeout_seconds
    assert (s.llm_daily_call_cap, s.anthropic_max_retries) == (200, 0)


def run_journey(sf: sessionmaker[Session], photo_dir: Path, fake: FakeAnthropic) -> dict[str, Any]:
    """POST a listing to the API, run the worker until idle, GET the report. The API is started
    with text_red_flags enabled and a dummy key: it only enqueues jobs and never calls Anthropic.
    The worker runs the same signal backed by the fake client."""
    settings = make_settings(
        photo_dir=photo_dir,
        enabled_signals="text_red_flags",
        anthropic_api_key=SecretStr("test-key-never-used"),
    )
    client = TestClient(create_app(settings))
    extractor = ClaudeFlagExtractor(
        sf,
        fake,  # type: ignore[arg-type]
        model="claude-opus-5-5",
        effort="medium",
        max_tokens=8000,
        daily_cap=200,
        max_input_chars=8000,
        cap_lock_wait_seconds=5,
    )
    worker = Worker(
        sf,
        build_handlers(sf, [TextRedFlagsSignal(extractor)], settings),
        WorkerConfig(concurrency=1, lease_seconds=30, job_timeout_seconds=20),
    )
    created = client.post(
        "/checks",
        data={
            "listing_text": LISTING.replace("[phone]", "416-555-0123"),
            "monthly_rent": "1500",
            "bedrooms": "2",
            "address_text": "1430 Trafalgar Rd",
            "city": "Oakville",
        },
        files=[("photos", ("p.jpg", image_bytes(), "image/jpeg"))],
    )
    check_id = uuid.UUID(created.json()["check_id"])
    while worker.run_once():
        pass
    body: dict[str, Any] = client.get(f"/checks/{check_id}").json()
    return body


@pytest.mark.integration
def test_journey_on_the_ai_path(session_factory: sessionmaker[Session], photo_dir: Path) -> None:
    fake = FakeAnthropic(
        [
            response(
                [
                    {
                        "flag": "owner_unavailable_abroad",
                        "quote": "I am currently working overseas",
                    },
                    {"flag": "keys_by_mail", "quote": "I'll mail the keys"},
                    {"flag": "deposit_before_viewing", "quote": "Send the deposit to hold it"},
                ]
            )
        ]
    )

    body = run_journey(session_factory, photo_dir, fake)

    assert body["status"] == "complete"
    (signal,) = body["signals"]
    assert (signal["name"], signal["status"], signal["severity"]) == (
        "text_red_flags",
        "ok",
        "high",
    )
    assert signal["evidence"]["method"] == "external_ai_api"
    assert [f["flag"] for f in signal["evidence"]["flags"]] == [
        "owner_unavailable_abroad",
        "keys_by_mail",
        "deposit_before_viewing",
    ]
    # The API received the redacted text: no phone number left the system.
    sent = fake.calls[0]["messages"][0]["content"]
    assert "[phone]" in sent and "416" not in sent


@pytest.mark.integration
@pytest.mark.parametrize("failure", [timeout_error(), status_error(503)])
def test_journey_falls_back_to_keyword_rules_when_the_api_fails(
    session_factory: sessionmaker[Session], photo_dir: Path, failure: Exception
) -> None:
    fake = FakeAnthropic([failure])

    body = run_journey(session_factory, photo_dir, fake)

    assert body["status"] == "complete"
    (signal,) = body["signals"]
    assert signal["status"] == "ok"
    assert signal["severity"] == "medium"  # keyword path is capped at medium
    evidence = signal["evidence"]
    assert evidence["method"] == "keyword_rules"
    assert evidence["fallback_reason"] in {"timeout", "api_error_503"}
    assert {"owner_unavailable_abroad", "keys_by_mail", "deposit_before_viewing"} <= {
        f["flag"] for f in evidence["flags"]
    }
    assert body["risk_level"] is None  # the check completed normally, not "incomplete"
