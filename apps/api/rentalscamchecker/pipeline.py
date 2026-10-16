"""Orchestration of a check's background work (ED-018).

`POST /checks` calls `enqueue_check_jobs()` in the transaction that creates the check, so a check
never exists without its jobs. It enqueues:

- one `signal` job per enabled signal, and
- one `aggregate` job, due at the check timeout.

Each time a signal result is recorded (success, or an error result when its job is dead-lettered),
the check row is locked and the results are counted; when every expected signal has a result,
the aggregate job is moved to "due now". So the aggregator runs as soon as all signals are
terminal, or at the timeout if some never finish. The aggregator records an error result for
each signal still missing and marks the check complete.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.config import Settings
from rentalscamchecker.jobs import queue
from rentalscamchecker.jobs.queue import ClaimedJob
from rentalscamchecker.jobs.worker import JobHandler, PermanentJobError, Worker, WorkerConfig
from rentalscamchecker.models import Check, Job
from rentalscamchecker.models import SignalResult as SignalResultRow
from rentalscamchecker.signals.base import CheckInput, Signal, SignalContext, SignalResult
from rentalscamchecker.signals.registry import enabled_signals

SIGNAL_JOB = "signal"
AGGREGATE_JOB = "aggregate"
TIMED_OUT_ERROR = "did not finish before the check timeout"

log = structlog.get_logger("rentalscamchecker.pipeline")

SignalKey = tuple[str, str]  # (name, version)


def signal_job_key(check_id: uuid.UUID, name: str, version: str) -> str:
    return f"signal:{check_id}:{name}:{version}"


def aggregate_job_key(check_id: uuid.UUID) -> str:
    return f"aggregate:{check_id}"


def enqueue_check_jobs(
    session: Session,
    check_id: uuid.UUID,
    signals: list[Signal],
    *,
    check_timeout_seconds: float,
    max_attempts: int,
) -> None:
    """Enqueue the signal jobs and the aggregate job for a new check (no commit)."""
    for signal in signals:
        queue.enqueue(
            session,
            SIGNAL_JOB,
            {"check_id": str(check_id), "signal": signal.name, "version": signal.version},
            signal_job_key(check_id, signal.name, signal.version),
            max_attempts=max_attempts,
        )
    queue.enqueue(
        session,
        AGGREGATE_JOB,
        {"check_id": str(check_id), "signals": [[s.name, s.version] for s in signals]},
        aggregate_job_key(check_id),
        # With no signals there is nothing to wait for.
        delay_seconds=check_timeout_seconds if signals else 0.0,
        max_attempts=max_attempts,
    )


class SignalJobHandler:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        signals: Mapping[str, Signal],
        ctx: SignalContext,
    ) -> None:
        self._sessions = session_factory
        self._signals = signals
        self._ctx = ctx

    def run(self, job: ClaimedJob) -> None:
        check_id = uuid.UUID(job.payload["check_id"])
        signal = self._signals.get(job.payload["signal"])
        if signal is None or signal.version != job.payload["version"]:
            raise PermanentJobError(
                f"signal {job.payload['signal']} v{job.payload['version']} is not enabled"
            )
        started_at = _now()
        with self._sessions.begin() as session:
            check_input = _load_check_input(session, check_id)
            # The first signal to start moves the check from pending to running.
            session.execute(
                update(Check)
                .where(Check.id == check_id, Check.status == "pending")
                .values(status="running")
            )
        # The signal itself runs outside any transaction: it may take seconds (network calls).
        result = signal.run(check_input, self._ctx)
        with self._sessions.begin() as session:
            _record_result(session, check_id, result, started_at=started_at, error=None)
            _start_aggregate_if_all_done(session, check_id)

    def on_dead(self, job: ClaimedJob, error: str) -> None:
        check_id = uuid.UUID(job.payload["check_id"])
        result = SignalResult(
            name=job.payload["signal"],
            version=job.payload["version"],
            status="error",
            severity=None,
            explanation="This check could not be completed.",
        )
        with self._sessions.begin() as session:
            if session.get(Check, check_id) is None:
                return
            _record_result(session, check_id, result, started_at=None, error=error)
            _start_aggregate_if_all_done(session, check_id)


class AggregateJobHandler:
    """Finalizes a check. Scoring (risk levels) arrives in M7; for now the aggregator only marks
    the check complete, and "incomplete" when any expected signal errored or never finished."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def run(self, job: ClaimedJob) -> None:
        check_id = uuid.UUID(job.payload["check_id"])
        expected: list[SignalKey] = [(n, v) for n, v in job.payload["signals"]]
        with self._sessions.begin() as session:
            check = session.get(Check, check_id, with_for_update=True)
            if check is None or check.status == "complete":
                return  # deleted, or already finalized by an earlier run of this job
            present = {(r.name, r.version): r.status for r in check.signal_results}
            for name, version in expected:
                if (name, version) not in present:
                    missing = SignalResult(
                        name=name, version=version, status="error", severity=None
                    )
                    _record_result(
                        session, check_id, missing, started_at=None, error=TIMED_OUT_ERROR
                    )
                    present[(name, version)] = "error"
            any_error = any(present[key] == "error" for key in expected)
            check.status = "complete"
            check.risk_level = "incomplete" if any_error else None
        log.info("check_complete", check_id=str(check_id), incomplete=any_error)

    def on_dead(self, job: ClaimedJob, error: str) -> None:
        # Don't leave the user polling forever: close the check as incomplete.
        check_id = uuid.UUID(job.payload["check_id"])
        with self._sessions.begin() as session:
            check = session.get(Check, check_id, with_for_update=True)
            if check is not None and check.status != "complete":
                check.status = "complete"
                check.risk_level = "incomplete"


def build_handlers(
    session_factory: sessionmaker[Session], signals: list[Signal], settings: Settings
) -> dict[str, JobHandler]:
    ctx = SignalContext(settings=settings)
    return {
        SIGNAL_JOB: SignalJobHandler(session_factory, {s.name: s for s in signals}, ctx),
        AGGREGATE_JOB: AggregateJobHandler(session_factory),
    }


def build_worker(settings: Settings, session_factory: sessionmaker[Session]) -> Worker:
    config = WorkerConfig(
        concurrency=settings.worker_concurrency,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        lease_seconds=settings.job_lease_seconds,
        backoff_base_seconds=settings.job_backoff_base_seconds,
        backoff_max_seconds=settings.job_backoff_max_seconds,
        job_timeout_seconds=settings.job_timeout_seconds,
        heartbeat_interval_seconds=settings.worker_heartbeat_interval_seconds,
    )
    handlers = build_handlers(session_factory, enabled_signals(settings, session_factory), settings)
    return Worker(session_factory, handlers, config)


def _now() -> datetime:
    return datetime.now(UTC)


def _load_check_input(session: Session, check_id: uuid.UUID) -> CheckInput:
    check = session.get(Check, check_id)
    if check is None:
        raise PermanentJobError(f"check {check_id} no longer exists")
    return CheckInput(
        check_id=check.id,
        listing_text=check.listing_text,
        rent_cents=check.rent_cents,
        bedrooms=check.bedrooms,
        address_text=check.address_text,
        city=check.city,
        submitted_url=check.submitted_url,
        unit_type=check.unit_type,
    )


def _record_result(
    session: Session,
    check_id: uuid.UUID,
    result: SignalResult,
    *,
    started_at: datetime | None,
    error: str | None,
) -> None:
    """Upsert the result for (check, signal, version), the unique key of signal_results.

    A job can run more than once (at-least-once delivery; a timed-out attempt still running in
    an abandoned thread, ED-017), so this must be safe to call repeatedly. Rule (ED-020):
    - no row yet: insert;
    - existing row is an error and the new result isn't: replace it (a successful retry or a late
      success beats "could not be completed");
    - otherwise keep the existing row (first real result wins; an error never replaces a result);
    - the check is complete: write nothing, the report is final.
    """
    check_status = session.execute(
        select(Check.status).where(Check.id == check_id).with_for_update()
    ).scalar_one_or_none()
    if check_status is None or check_status == "complete":
        return
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "check_id": check_id,
        "name": result.name,
        "version": result.version,
        "status": result.status,
        "severity": result.severity,
        "evidence": result.evidence,
        "explanation": result.explanation,
        "next_step": result.next_step,
        "started_at": started_at,
        "finished_at": _now(),
        "error": error,
    }
    stmt = insert(SignalResultRow).values(**values)
    replaceable = [c for c in values if c not in ("id", "check_id", "name", "version")]
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_signal_results_check_id_name_version",
            set_={column: stmt.excluded[column] for column in replaceable},
            # SignalResultRow.status is the existing row; stmt.excluded.status the new one.
            where=(SignalResultRow.status == "error") & (stmt.excluded.status != "error"),
        )
    )


def _start_aggregate_if_all_done(session: Session, check_id: uuid.UUID) -> None:
    # Lock the check row so concurrent signal completions for one check run this one at a time;
    # otherwise two last-but-one signals could each miss the other's result and neither would
    # start the aggregator (it would still run, but only at the timeout).
    session.execute(select(Check.id).where(Check.id == check_id).with_for_update())
    expected = _expected_signals(session, check_id)
    if expected is None:
        return
    rows = session.execute(
        select(SignalResultRow.name, SignalResultRow.version).where(
            SignalResultRow.check_id == check_id
        )
    ).all()
    recorded = {(name, version) for name, version in rows}
    if set(expected) <= recorded and queue.run_now(session, aggregate_job_key(check_id)):
        log.info("aggregate_started_early", check_id=str(check_id))


def _expected_signals(session: Session, check_id: uuid.UUID) -> list[SignalKey] | None:
    payload = session.execute(
        select(Job.payload).where(Job.idempotency_key == aggregate_job_key(check_id))
    ).scalar_one_or_none()
    if payload is None:
        return None
    return [(n, v) for n, v in payload["signals"]]
