"""Orchestration: signal jobs, aggregator start (all done or timeout), end to end via the API."""

import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.jobs import queue
from rentalscamchecker.jobs.worker import Worker, WorkerConfig
from rentalscamchecker.main import create_app
from rentalscamchecker.models import Check
from rentalscamchecker.pipeline import (
    TIMED_OUT_ERROR,
    aggregate_job_key,
    build_handlers,
    build_worker,
    enqueue_check_jobs,
    signal_job_key,
)
from rentalscamchecker.signals.base import CheckInput, Signal, SignalContext, SignalResult
from tests.helpers import image_bytes, make_settings

pytestmark = pytest.mark.integration

FAST = WorkerConfig(
    concurrency=1,
    poll_interval_seconds=0.05,
    lease_seconds=30,
    job_timeout_seconds=20,
    backoff_base_seconds=0,
)


class FixedSignal:
    def __init__(self, name: str, version: str = "1") -> None:
        self.name = name
        self.version = version
        self.runs = 0

    def run(self, check: CheckInput, ctx: SignalContext) -> SignalResult:
        self.runs += 1
        return SignalResult(self.name, self.version, "ok", "low", {"seen": check.city})


class BrokenSignal(FixedSignal):
    def run(self, check: CheckInput, ctx: SignalContext) -> SignalResult:
        self.runs += 1
        raise ConnectionError("upstream down")


def new_check(sf: sessionmaker[Session]) -> uuid.UUID:
    check_id = uuid.uuid4()
    with sf.begin() as s:
        s.add(
            Check(
                id=check_id,
                listing_text="2 bed near campus",
                rent_cents=180_000,
                bedrooms=2,
                address_text="1430 Trafalgar Rd",
                city="Oakville",
            )
        )
    return check_id


def start_check(
    sf: sessionmaker[Session], signals: list[Signal], timeout: float = 120, max_attempts: int = 5
) -> tuple[uuid.UUID, Worker]:
    check_id = new_check(sf)
    with sf.begin() as s:
        enqueue_check_jobs(
            s, check_id, signals, check_timeout_seconds=timeout, max_attempts=max_attempts
        )
    handlers = build_handlers(sf, signals, make_settings())
    return check_id, Worker(sf, handlers, FAST)


def drain(worker: Worker) -> None:
    while worker.run_once():
        pass


def check_row(sf: sessionmaker[Session], check_id: uuid.UUID) -> tuple[str, str | None]:
    with sf() as s:
        check = s.get(Check, check_id)
        assert check is not None
        return check.status, check.risk_level


def results(sf: sessionmaker[Session], check_id: uuid.UUID) -> dict[str, tuple[Any, ...]]:
    with sf() as s:
        rows = s.execute(
            sa.text(
                "SELECT name, status, severity, error FROM signal_results WHERE check_id = :id"
            ),
            {"id": check_id},
        ).all()
    return {r.name: (r.status, r.severity, r.error) for r in rows}


def job_status(sf: sessionmaker[Session], key: str) -> str:
    with sf() as s:
        return str(
            s.execute(
                sa.text("SELECT status FROM jobs WHERE idempotency_key = :k"), {"k": key}
            ).scalar_one()
        )


def test_aggregator_starts_as_soon_as_all_signals_are_done(
    session_factory: sessionmaker[Session],
) -> None:
    a, b = FixedSignal("a"), FixedSignal("b")
    check_id, worker = start_check(session_factory, [a, b], timeout=3600)

    assert worker.run_once() is True  # signal a
    assert check_row(session_factory, check_id) == ("running", None)
    assert worker.run_once() is True  # signal b: last result moves the aggregator to "now"
    assert worker.run_once() is True  # aggregator, an hour before its timeout
    assert worker.run_once() is False

    assert check_row(session_factory, check_id) == ("complete", None)
    assert results(session_factory, check_id) == {
        "a": ("ok", "low", None),
        "b": ("ok", "low", None),
    }


def test_dead_signal_is_recorded_as_error_and_check_is_incomplete(
    session_factory: sessionmaker[Session],
) -> None:
    ok, broken = FixedSignal("ok"), BrokenSignal("broken")
    check_id, worker = start_check(session_factory, [ok, broken], timeout=3600, max_attempts=2)

    drain(worker)

    assert broken.runs == 2  # retried once (backoff 0 in FAST), then dead-lettered
    assert job_status(session_factory, signal_job_key(check_id, "broken", "1")) == "dead"
    assert results(session_factory, check_id) == {
        "ok": ("ok", "low", None),
        "broken": ("error", None, "ConnectionError: upstream down"),
    }
    assert check_row(session_factory, check_id) == ("complete", "incomplete")


def test_check_timeout_finalizes_without_the_missing_signal(
    session_factory: sessionmaker[Session],
) -> None:
    fast, slow = FixedSignal("fast"), FixedSignal("slow")
    check_id, worker = start_check(session_factory, [fast, slow], timeout=1)
    slow_key = signal_job_key(check_id, "slow", "1")
    with session_factory.begin() as s:  # "slow" won't get a worker for an hour
        s.execute(
            sa.text(
                "UPDATE jobs SET run_after = now() + interval '1 hour' WHERE idempotency_key = :k"
            ),
            {"k": slow_key},
        )

    drain(worker)  # runs "fast" only; the aggregator isn't due yet
    assert check_row(session_factory, check_id) == ("running", None)
    time.sleep(1.2)
    drain(worker)  # timeout passed: the aggregator runs

    assert check_row(session_factory, check_id) == ("complete", "incomplete")
    assert results(session_factory, check_id)["slow"] == ("error", None, TIMED_OUT_ERROR)

    # The slow signal finishing later doesn't change the finalized report.
    with session_factory.begin() as s:
        s.execute(
            sa.text("UPDATE jobs SET run_after = now() WHERE idempotency_key = :k"), {"k": slow_key}
        )
    drain(worker)
    assert slow.runs == 1
    assert results(session_factory, check_id)["slow"] == ("error", None, TIMED_OUT_ERROR)
    assert check_row(session_factory, check_id) == ("complete", "incomplete")


def test_running_a_signal_job_twice_stores_one_result(
    session_factory: sessionmaker[Session],
) -> None:
    signal = FixedSignal("once")
    check_id, worker = start_check(session_factory, [signal])
    handler = build_handlers(session_factory, [signal], make_settings())["signal"]
    with session_factory.begin() as s:
        job = queue.claim(s, lease_seconds=30)
    assert job is not None

    handler.run(job)
    handler.run(job)  # e.g. the worker crashed after the work, before marking the job done

    assert signal.runs == 2
    assert list(results(session_factory, check_id)) == ["once"]


def test_check_with_no_enabled_signals_completes_immediately(
    session_factory: sessionmaker[Session],
) -> None:
    check_id, worker = start_check(session_factory, [], timeout=3600)

    drain(worker)

    assert check_row(session_factory, check_id) == ("complete", None)
    assert job_status(session_factory, aggregate_job_key(check_id)) == "done"


# --- End to end through the HTTP API (the M2 "Done when" journey) ---


def test_check_created_via_api_reaches_complete_through_the_worker(
    session_factory: sessionmaker[Session], photo_dir: Path
) -> None:
    settings = make_settings(
        photo_dir=photo_dir,
        enabled_signals="stub",
        stub_signal_sleep_seconds=0.1,
        worker_poll_interval_seconds=0.05,
    )
    client = TestClient(create_app(settings))
    created = client.post(
        "/checks",
        data={
            "listing_text": "Call 416-555-0123. 2 bed near Sheridan.",
            "monthly_rent": "1850",
            "bedrooms": "2",
            "address_text": "1430 Trafalgar Rd",
            "city": "Oakville",
        },
        files=[("photos", ("p.jpg", image_bytes(), "image/jpeg"))],
    )
    assert created.status_code == 202
    check_id = uuid.UUID(created.json()["check_id"])
    assert client.get(f"/checks/{check_id}").json()["status"] == "pending"

    # The jobs were committed with the check; their payloads carry ids, never contact values.
    with session_factory() as s:
        rows = s.execute(sa.text("SELECT idempotency_key, payload FROM jobs")).all()
    jobs = {key: payload for key, payload in rows}
    assert jobs == {
        signal_job_key(check_id, "stub", "1"): {
            "check_id": str(check_id),
            "signal": "stub",
            "version": "1",
        },
        aggregate_job_key(check_id): {"check_id": str(check_id), "signals": [["stub", "1"]]},
    }

    drain(build_worker(settings, session_factory))

    body = client.get(f"/checks/{check_id}").json()
    assert body["status"] == "complete"
    assert body["risk_level"] is None  # scoring arrives in M7
    assert [(s["name"], s["status"], s["severity"]) for s in body["signals"]] == [
        ("stub", "ok", "none")
    ]
    assert body["signals"][0]["evidence"] == {"stub": True, "slept_seconds": 0.1}


# --- idempotent result writes (ED-020) ---


def result_rows(sf: sessionmaker[Session], check_id: uuid.UUID) -> list[tuple[Any, ...]]:
    with sf() as s:
        rows = s.execute(
            sa.text("SELECT name, status, error FROM signal_results WHERE check_id = :id"),
            {"id": check_id},
        ).all()
    return [tuple(r) for r in rows]


def claimed_signal_job(sf: sessionmaker[Session]) -> queue.ClaimedJob:
    with sf.begin() as s:
        job = queue.claim(s, lease_seconds=30)
    assert job is not None and job.kind == "signal"
    return job


def test_success_replaces_an_earlier_error_result(session_factory: sessionmaker[Session]) -> None:
    signal = FixedSignal("s")
    check_id, _ = start_check(session_factory, [signal])
    handler = build_handlers(session_factory, [signal], make_settings())["signal"]
    job = claimed_signal_job(session_factory)

    handler.on_dead(job, "ConnectionError: upstream down")
    assert result_rows(session_factory, check_id) == [
        ("s", "error", "ConnectionError: upstream down")
    ]
    handler.run(job)  # e.g. an abandoned timed-out attempt that succeeded after all

    assert result_rows(session_factory, check_id) == [("s", "ok", None)]


def test_error_never_replaces_a_result(session_factory: sessionmaker[Session]) -> None:
    signal = FixedSignal("s")
    check_id, _ = start_check(session_factory, [signal])
    handler = build_handlers(session_factory, [signal], make_settings())["signal"]
    job = claimed_signal_job(session_factory)

    handler.run(job)
    handler.on_dead(job, "late failure")

    assert result_rows(session_factory, check_id) == [("s", "ok", None)]


class SlowOnceSignal(FixedSignal):
    """The first run overruns the job timeout; later runs are fast. `runs` counts finished runs."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.started = 0

    def run(self, check: CheckInput, ctx: SignalContext) -> SignalResult:
        self.started += 1
        if self.started == 1:
            time.sleep(1.5)
        return super().run(check, ctx)


def test_signal_timing_out_then_finishing_late_leaves_one_result(
    session_factory: sessionmaker[Session],
) -> None:
    signal = SlowOnceSignal("slow")
    check_id = new_check(session_factory)
    with session_factory.begin() as s:
        enqueue_check_jobs(s, check_id, [signal], check_timeout_seconds=3600, max_attempts=5)
    config = WorkerConfig(
        concurrency=1, lease_seconds=30, job_timeout_seconds=0.5, backoff_base_seconds=0
    )
    worker = Worker(
        session_factory, build_handlers(session_factory, [signal], make_settings()), config
    )

    drain(worker)  # attempt 1 times out, attempt 2 succeeds, aggregator completes the check
    time.sleep(1.5)  # attempt 1's abandoned thread now finishes and tries to write its result

    assert (signal.started, signal.runs) == (2, 2)  # both attempts ran to the end
    assert result_rows(session_factory, check_id) == [("slow", "ok", None)]
    assert check_row(session_factory, check_id) == ("complete", None)
