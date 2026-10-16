"""The worker: N threads, each repeatedly reaping expired leases, claiming one job and running it.

Delivery is at-least-once (ED-017): a job can run more than once (crash after the work but before
`complete`, or a lease that expired while the job was still running), so handlers must be
idempotent. In this codebase they write with ON CONFLICT DO NOTHING on natural keys.

Graceful shutdown: `request_stop()` sets an event; each thread finishes the job it is running,
then exits instead of claiming another. A job interrupted by a hard kill is recovered by the
lease reaper.
"""

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.jobs import queue
from rentalscamchecker.jobs.queue import ClaimedJob

log = structlog.get_logger("rentalscamchecker.worker")


class PermanentJobError(Exception):
    """Raise from a handler when retrying cannot help (e.g. the check was deleted): the job goes
    straight to dead-letter instead of using up its attempts."""


class JobHandler(Protocol):
    def run(self, job: ClaimedJob) -> None:
        """Do the work and commit its results. Must be idempotent."""
        ...

    def on_dead(self, job: ClaimedJob, error: str) -> None:
        """Called once when the job is dead-lettered, to record the failure for users."""
        ...


@dataclass(frozen=True)
class WorkerConfig:
    concurrency: int = 2
    poll_interval_seconds: float = 1.0
    lease_seconds: float = 60.0
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 300.0


class Worker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        handlers: Mapping[str, JobHandler],
        config: WorkerConfig,
    ) -> None:
        self._sessions = session_factory
        self._handlers = handlers
        self._config = config
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        """Run `concurrency` threads until request_stop(); return when they have all finished."""
        threads = [
            threading.Thread(target=self._loop, name=f"worker-{i}", daemon=False)
            for i in range(self._config.concurrency)
        ]
        log.info("worker_started", concurrency=self._config.concurrency)
        for thread in threads:
            thread.start()
        # Join with a timeout so the main thread keeps waking up to handle signals (Windows).
        while any(t.is_alive() for t in threads):
            for thread in threads:
                thread.join(timeout=0.5)
        log.info("worker_stopped")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                did_work = self.run_once()
            except Exception:
                # A DB outage must not kill the thread; wait and try again.
                log.exception("worker_loop_error")
                did_work = False
            if not did_work:
                self._stop.wait(self._config.poll_interval_seconds)

    def run_once(self) -> bool:
        """Reap expired leases, then claim and process at most one job. True if a job ran."""
        self._reap()
        with self._sessions.begin() as session:
            job = queue.claim(session, self._config.lease_seconds)
        if job is None:
            return False
        self._process(job)
        return True

    def _reap(self) -> None:
        with self._sessions.begin() as session:
            reaped = queue.reap_expired_leases(session)
        for job, status in reaped:
            log.warning("job_lease_expired", job_id=job.id, kind=job.kind, new_status=status)
            if status == "dead":
                self._run_dead_hook(job, queue.LEASE_EXPIRED_ERROR)

    def _process(self, job: ClaimedJob) -> None:
        structlog.contextvars.bind_contextvars(job_id=job.id, job_kind=job.kind)
        started = time.perf_counter()
        log.info("job_started", attempt=job.attempts, max_attempts=job.max_attempts)
        try:
            handler = self._handlers.get(job.kind)
            if handler is None:
                raise PermanentJobError(f"no handler for job kind {job.kind!r}")
            handler.run(job)
        except PermanentJobError as exc:
            self._fail(job, exc, permanent=True)
        except Exception as exc:
            self._fail(job, exc, permanent=False)
        else:
            with self._sessions.begin() as session:
                owned = queue.complete(session, job)
            if owned:
                log.info("job_done", duration_ms=_ms_since(started))
            else:
                log.warning("job_lease_lost", detail="finished after another worker took over")
        finally:
            structlog.contextvars.unbind_contextvars("job_id", "job_kind")

    def _fail(self, job: ClaimedJob, exc: Exception, *, permanent: bool) -> None:
        error = f"{type(exc).__name__}: {exc}"
        delay = queue.backoff_seconds(
            job.attempts, self._config.backoff_base_seconds, self._config.backoff_max_seconds
        )
        with self._sessions.begin() as session:
            status = queue.fail(session, job, error, retry_delay_seconds=delay, permanent=permanent)
        if status is None:
            log.warning("job_lease_lost", detail="failed after another worker took over")
            return
        log.error(
            "job_failed",
            new_status=status,
            attempt=job.attempts,
            retry_in_seconds=delay if status == "failed" else None,
            exc_info=exc,
        )
        if status == "dead":
            self._run_dead_hook(job, error)

    def _run_dead_hook(self, job: ClaimedJob, error: str) -> None:
        handler = self._handlers.get(job.kind)
        if handler is None:
            return
        try:
            handler.on_dead(job, error)
        except Exception:
            log.exception("job_dead_hook_failed", job_id=job.id)


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
