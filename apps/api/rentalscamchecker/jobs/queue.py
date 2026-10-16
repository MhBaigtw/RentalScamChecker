"""Postgres job queue repository (ED-002, ED-017). Plain SQL, one function per state change.

Life of a job:

    queued --claim--> running --complete--> done
                         |
                         +--fail--> failed (waits until run_after, then claimable again)
                         |            or dead, when attempts are used up or the error is permanent
                         +--lease expires (worker crashed/hung)--> reap --> failed or dead

Every function takes a Session and leaves committing to the caller, so a job can be enqueued in
the same transaction as the rows it refers to (POST /checks).

Fencing: `attempts` is incremented on every claim, and complete/fail only apply when the row is
still `running` with the attempt number the worker claimed. A worker whose lease expired (and
whose job was handed to someone else) therefore can't overwrite the newer attempt's outcome.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

DEFAULT_MAX_ATTEMPTS = 5
MAX_ERROR_LENGTH = 2000
LEASE_EXPIRED_ERROR = "lease expired: the worker crashed or the job ran longer than its lease"


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    kind: str
    payload: dict[str, Any]
    # 1 on the first try. Also the fencing token for complete() and fail().
    attempts: int
    max_attempts: int


def backoff_seconds(attempt: int, base: float, cap: float) -> float:
    """Delay before retrying after failed attempt number `attempt` (1-based): base * 2^(n-1),
    capped. No jitter: retries here are per job, not a crowd hitting one service at once."""
    return float(min(cap, base * 2 ** (attempt - 1)))


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    *,
    delay_seconds: float = 0.0,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> bool:
    """Add a job unless one with this idempotency key already exists. True if inserted."""
    inserted = session.execute(
        sa.text(
            """
            INSERT INTO jobs (kind, payload, idempotency_key, run_after, max_attempts)
            VALUES (:kind, CAST(:payload AS jsonb), :key,
                    now() + make_interval(secs => :delay), :max_attempts)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """
        ),
        {
            "kind": kind,
            "payload": json.dumps(payload),
            "key": idempotency_key,
            "delay": delay_seconds,
            "max_attempts": max_attempts,
        },
    ).scalar_one_or_none()
    return inserted is not None


def claim(session: Session, lease_seconds: float) -> ClaimedJob | None:
    """Take the oldest due job, or None. Commit promptly: the row lock taken here is only held
    until commit; after that the lease (locked_until) is what reserves the job.

    SKIP LOCKED makes concurrent claimers pass over rows another transaction is claiming
    instead of waiting for it, so N workers get N different jobs without blocking each other.
    """
    row = session.execute(
        sa.text(
            """
            UPDATE jobs
            SET status = 'running',
                attempts = attempts + 1,
                locked_until = now() + make_interval(secs => :lease),
                updated_at = now()
            WHERE id = (
                SELECT id FROM jobs
                WHERE status IN ('queued', 'failed') AND run_after <= now()
                ORDER BY run_after, id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, kind, payload, attempts, max_attempts
            """
        ),
        {"lease": lease_seconds},
    ).one_or_none()
    if row is None:
        return None
    return ClaimedJob(
        id=row.id,
        kind=row.kind,
        payload=row.payload,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
    )


def complete(session: Session, job: ClaimedJob) -> bool:
    """Mark done. False if this worker no longer owns the job (lease expired and re-claimed)."""
    result = session.execute(
        sa.text(
            """
            UPDATE jobs
            SET status = 'done', locked_until = NULL, updated_at = now()
            WHERE id = :id AND status = 'running' AND attempts = :attempts
            """
        ),
        {"id": job.id, "attempts": job.attempts},
    )
    return _rowcount(result) == 1


def fail(
    session: Session,
    job: ClaimedJob,
    error: str,
    *,
    retry_delay_seconds: float,
    permanent: bool = False,
) -> Literal["failed", "dead"] | None:
    """Record a failed attempt. Returns the new status: "failed" (will be retried after the delay)
    or "dead" (attempts used up, or `permanent`). None if this worker no longer owns the job."""
    status = session.execute(
        sa.text(
            """
            UPDATE jobs
            SET status = CASE WHEN :permanent OR attempts >= max_attempts
                              THEN 'dead' ELSE 'failed' END,
                run_after = now() + make_interval(secs => :delay),
                locked_until = NULL,
                last_error = :error,
                updated_at = now()
            WHERE id = :id AND status = 'running' AND attempts = :attempts
            RETURNING status
            """
        ),
        {
            "id": job.id,
            "attempts": job.attempts,
            "permanent": permanent,
            "delay": retry_delay_seconds,
            "error": error[:MAX_ERROR_LENGTH],
        },
    ).scalar_one_or_none()
    if status is None:
        return None
    assert status in ("failed", "dead")  # noqa: S101 (narrowing for mypy; enforced by the SQL)
    return "failed" if status == "failed" else "dead"


def reap_expired_leases(session: Session) -> list[tuple[ClaimedJob, str]]:
    """Release `running` jobs whose lease has passed: back to `failed` (claimable now) or to
    `dead` if that was the last attempt. Returns each reaped job with its new status, so the
    caller can run dead-letter hooks.

    A crash counts as an attempt (attempts was incremented at claim time). That is what stops a
    job that crashes the worker every time (a "poison" job) from being retried forever.
    """
    rows = session.execute(
        sa.text(
            """
            UPDATE jobs
            SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'failed' END,
                run_after = now(),
                locked_until = NULL,
                last_error = :error,
                updated_at = now()
            WHERE status = 'running' AND locked_until < now()
            RETURNING id, kind, payload, attempts, max_attempts, status
            """
        ),
        {"error": LEASE_EXPIRED_ERROR},
    ).all()
    return [
        (
            ClaimedJob(
                id=r.id,
                kind=r.kind,
                payload=r.payload,
                attempts=r.attempts,
                max_attempts=r.max_attempts,
            ),
            r.status,
        )
        for r in rows
    ]


def run_now(session: Session, idempotency_key: str) -> bool:
    """Make a waiting job due immediately (used to start the aggregator early). True if moved."""
    result = session.execute(
        sa.text(
            """
            UPDATE jobs SET run_after = now(), updated_at = now()
            WHERE idempotency_key = :key
              AND status IN ('queued', 'failed')
              AND run_after > now()
            """
        ),
        {"key": idempotency_key},
    )
    return _rowcount(result) == 1


def _rowcount(result: sa.Result[Any]) -> int:
    # Result.rowcount exists on cursor results (UPDATE/DELETE); typed via CursorResult.
    return int(getattr(result, "rowcount", 0))
