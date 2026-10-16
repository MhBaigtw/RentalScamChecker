"""The worker: N threads, each repeatedly reaping expired leases, claiming one job and running it.

Delivery is at-least-once (ED-017): a job can run more than once (crash after the work but before
`complete`, or a timed-out attempt that is still running), so handlers must be idempotent. In
this codebase they upsert on natural keys (ED-020).

Per-job timeout: each job runs in its own helper thread; if it hasn't returned after
`job_timeout_seconds` (always below the lease), the attempt is failed as retryable and the worker
thread moves on. Python cannot kill a thread, so the abandoned helper keeps running until its code
returns; its job is no longer owned (fencing), and its writes are idempotent. The timeout bounds
how long a job holds a worker slot and guarantees the job is resolved before its lease expires;
it does not stop the code itself.

Graceful shutdown: `request_stop()` sets an event; each thread finishes the job it is running,
then exits instead of claiming another. A job interrupted by a hard kill is recovered by the
lease reaper.
"""

import contextvars
import os
import socket
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.jobs import queue
from rentalscamchecker.jobs.queue import ClaimedJob
from rentalscamchecker.models import WorkerHeartbeat

log = structlog.get_logger("rentalscamchecker.worker")


class PermanentJobError(Exception):
    """Raise from a handler when retrying cannot help (e.g. the check was deleted): the job goes
    straight to dead-letter instead of using up its attempts."""


class JobTimeout(Exception):
    """The handler didn't return within the per-job timeout. Retryable."""


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
    job_timeout_seconds: float = 45.0
    # How often the worker writes its counters to worker_heartbeats (read by /internal/metrics).
    heartbeat_interval_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not 0 < self.job_timeout_seconds < self.lease_seconds:
            raise ValueError(
                f"job timeout ({self.job_timeout_seconds} s) must be greater than 0 and less "
                f"than the lease ({self.lease_seconds} s), so a timed-out job is failed before "
                "its lease expires and someone else claims it"
            )


@dataclass(frozen=True)
class WorkerStats:
    """Counters since this worker process started."""

    jobs_done_total: int
    jobs_failed_total: int
    jobs_timed_out_total: int
    # Helper threads of timed-out attempts that are still running right now. They don't hold a
    # worker slot, but each holds a thread (and possibly a DB connection) until its code returns.
    abandoned_threads_alive: int


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
        self._abandoned_lock = threading.Lock()
        self._abandoned: set[threading.Thread] = set()
        self._counters_lock = threading.Lock()
        self._done = 0
        self._failed = 0
        self._timed_out = 0
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def stats(self) -> WorkerStats:
        with self._abandoned_lock:
            self._abandoned = {t for t in self._abandoned if t.is_alive()}
            alive = len(self._abandoned)
        with self._counters_lock:
            return WorkerStats(self._done, self._failed, self._timed_out, alive)

    def write_heartbeat(self, *, stopped: bool = False) -> None:
        """Upsert this process's row in worker_heartbeats. Never raises: metrics are best effort."""
        stats = self.stats()
        values = {
            "last_seen_at": func.now(),
            "stopped_at": func.now() if stopped else None,
            "concurrency": self._config.concurrency,
            "jobs_done_total": stats.jobs_done_total,
            "jobs_failed_total": stats.jobs_failed_total,
            "jobs_timed_out_total": stats.jobs_timed_out_total,
            "abandoned_threads_alive": stats.abandoned_threads_alive,
        }
        try:
            with self._sessions.begin() as session:
                stmt = insert(WorkerHeartbeat).values(worker_id=self.worker_id, **values)
                session.execute(
                    stmt.on_conflict_do_update(index_elements=["worker_id"], set_=values)
                )
        except Exception:
            log.warning("heartbeat_failed", exc_info=True)

    def run(self) -> None:
        """Run `concurrency` threads until request_stop(); return when they have all finished."""
        threads = [
            threading.Thread(target=self._loop, name=f"worker-{i}", daemon=False)
            for i in range(self._config.concurrency)
        ]
        log.info("worker_started", concurrency=self._config.concurrency, worker_id=self.worker_id)
        for thread in threads:
            thread.start()
        self.write_heartbeat()
        next_heartbeat = time.monotonic() + self._config.heartbeat_interval_seconds
        # Join with a timeout so the main thread keeps waking up to handle signals (Windows)
        # and to write heartbeats.
        while any(t.is_alive() for t in threads):
            for thread in threads:
                thread.join(timeout=0.5)
            if time.monotonic() >= next_heartbeat:
                self.write_heartbeat()
                next_heartbeat = time.monotonic() + self._config.heartbeat_interval_seconds
        self.write_heartbeat(stopped=True)
        log.info("worker_stopped", **self.stats().__dict__)

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
            self._run_with_timeout(handler, job)
        except PermanentJobError as exc:
            self._fail(job, exc, permanent=True)
        except Exception as exc:
            self._fail(job, exc, permanent=False)
        else:
            with self._sessions.begin() as session:
                owned = queue.complete(session, job)
            if owned:
                with self._counters_lock:
                    self._done += 1
                log.info("job_done", duration_ms=_ms_since(started))
            else:
                log.warning("job_lease_lost", detail="finished after another worker took over")
        finally:
            structlog.contextvars.unbind_contextvars("job_id", "job_kind")

    def _run_with_timeout(self, handler: JobHandler, job: ClaimedJob) -> None:
        """Run handler.run(job) in a helper thread; raise JobTimeout if it overruns."""
        outcome: list[BaseException] = []
        # Copy the context so the handler's log lines keep this job's job_id.
        context = contextvars.copy_context()

        def target() -> None:
            try:
                context.run(handler.run, job)
            except BaseException as exc:  # handed to the worker thread below
                outcome.append(exc)

        helper = threading.Thread(
            target=target, name=f"job-{job.id}-attempt-{job.attempts}", daemon=True
        )
        helper.start()
        helper.join(self._config.job_timeout_seconds)
        if helper.is_alive():
            with self._abandoned_lock:
                self._abandoned = {t for t in self._abandoned if t.is_alive()} | {helper}
                abandoned = len(self._abandoned)
            with self._counters_lock:
                self._timed_out += 1
                timed_out = self._timed_out
            # The worker thread (the slot) moves on now; only the helper keeps running.
            log.error(
                "job_timed_out",
                abandoned_threads_alive=abandoned,
                jobs_timed_out_total=timed_out,
            )
            raise JobTimeout(f"did not finish within {self._config.job_timeout_seconds:g} s")
        if outcome:
            raise outcome[0]

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
        with self._counters_lock:
            self._failed += 1
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
