"""Job queue and worker behaviour against a real Postgres (the M2 "Done when" tests)."""

import threading
import time
from collections import Counter
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.jobs import queue
from rentalscamchecker.jobs.queue import ClaimedJob
from rentalscamchecker.jobs.worker import PermanentJobError, Worker, WorkerConfig

pytestmark = pytest.mark.integration

KIND = "test"
FAST = WorkerConfig(
    concurrency=1,
    poll_interval_seconds=0.05,
    lease_seconds=30,
    backoff_base_seconds=0,
    backoff_max_seconds=0,
)


class RecordingHandler:
    """Records every run; optionally sleeps, and fails the first `fail_times` runs."""

    def __init__(
        self, fail_times: int = 0, sleep: float = 0.0, error: Exception | None = None
    ) -> None:
        self.fail_times = fail_times
        self.sleep = sleep
        self.error = error or RuntimeError("boom")
        self.calls: list[int] = []
        self.dead: list[tuple[int, str]] = []
        self.started = threading.Event()
        self._lock = threading.Lock()

    def run(self, job: ClaimedJob) -> None:
        with self._lock:
            self.calls.append(job.id)
            failing = len(self.calls) <= self.fail_times
        self.started.set()
        time.sleep(self.sleep)
        if failing:
            raise self.error

    def on_dead(self, job: ClaimedJob, error: str) -> None:
        with self._lock:
            self.dead.append((job.id, error))


def add_jobs(sf: sessionmaker[Session], count: int = 1, **kwargs: Any) -> None:
    with sf.begin() as s:
        for i in range(count):
            queue.enqueue(s, KIND, {"n": i}, f"{KIND}:{i}", **kwargs)


def job(sf: sessionmaker[Session], key: str = f"{KIND}:0") -> sa.Row[Any]:
    with sf() as s:
        return s.execute(
            sa.text(
                "SELECT id, status, attempts, last_error, run_after, updated_at, locked_until "
                "FROM jobs WHERE idempotency_key = :key"
            ),
            {"key": key},
        ).one()


def statuses(sf: sessionmaker[Session]) -> Counter[str]:
    with sf() as s:
        return Counter(s.execute(sa.text("SELECT status FROM jobs")).scalars())


def make_worker(
    sf: sessionmaker[Session], handler: RecordingHandler, config: WorkerConfig = FAST
) -> Worker:
    return Worker(sf, {KIND: handler}, config)


def drain(worker: Worker) -> None:
    while worker.run_once():
        pass


# --- enqueue / claim ---


def test_enqueue_is_idempotent_on_key(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        assert queue.enqueue(s, KIND, {"n": 1}, "same-key") is True
        assert queue.enqueue(s, KIND, {"n": 2}, "same-key") is False

    with session_factory() as s:
        rows = s.execute(sa.text("SELECT payload FROM jobs")).scalars().all()
    assert rows == [{"n": 1}]


def test_jobs_are_not_claimed_before_run_after(session_factory: sessionmaker[Session]) -> None:
    add_jobs(session_factory, delay_seconds=60)

    with session_factory.begin() as s:
        assert queue.claim(s, lease_seconds=30) is None


def test_a_row_being_claimed_is_skipped_not_waited_for(
    session_factory: sessionmaker[Session],
) -> None:
    add_jobs(session_factory, count=2)

    with session_factory() as first, session_factory() as second:
        # Fail instead of hanging if SKIP LOCKED were not working.
        second.execute(sa.text("SET lock_timeout = '2s'"))
        a = queue.claim(first, lease_seconds=30)  # row lock held: first hasn't committed
        b = queue.claim(second, lease_seconds=30)
        c = queue.claim(second, lease_seconds=30)  # both rows now locked or claimed
        assert a is not None and b is not None
        assert a.id != b.id
        assert c is None
        first.commit()
        second.commit()


def test_two_workers_never_process_the_same_job(session_factory: sessionmaker[Session]) -> None:
    jobs = 200
    add_jobs(session_factory, count=jobs)
    handler = RecordingHandler(sleep=0.002)  # shared, so it sees every run from both workers
    config = WorkerConfig(concurrency=4, poll_interval_seconds=0.02, lease_seconds=30)
    workers = [make_worker(session_factory, handler, config) for _ in range(2)]
    threads = [threading.Thread(target=w.run) for w in workers]
    for t in threads:
        t.start()

    deadline = time.monotonic() + 60
    while statuses(session_factory)["done"] < jobs and time.monotonic() < deadline:
        time.sleep(0.1)
    for w in workers:
        w.request_stop()
    for t in threads:
        t.join(timeout=10)

    assert statuses(session_factory) == Counter({"done": jobs})
    assert len(handler.calls) == jobs  # no job ran twice...
    assert len(set(handler.calls)) == jobs  # ...and every job ran
    with session_factory() as s:
        assert s.execute(sa.text("SELECT max(attempts) FROM jobs")).scalar_one() == 1


# --- crash recovery (lease expiry) ---


def test_crashed_job_is_reclaimed_after_its_lease_expires(
    session_factory: sessionmaker[Session],
) -> None:
    add_jobs(session_factory)
    # A worker claims the job with a 1 s lease, then "crashes": it never completes or fails it.
    with session_factory.begin() as s:
        crashed = queue.claim(s, lease_seconds=1)
    assert crashed is not None
    handler = RecordingHandler()
    worker = make_worker(session_factory, handler)

    assert worker.run_once() is False  # lease still valid: nobody else may take it
    time.sleep(1.5)
    assert worker.run_once() is True  # reaper releases it, then this worker claims and runs it

    row = job(session_factory)
    assert (row.status, row.attempts) == ("done", 2)
    assert handler.calls == [crashed.id]
    # The crashed worker coming back late can't overwrite the newer attempt (fencing).
    with session_factory.begin() as s:
        assert queue.complete(s, crashed) is False
        assert queue.fail(s, crashed, "late", retry_delay_seconds=0) is None
    assert job(session_factory).status == "done"


def test_job_crashing_on_its_last_attempt_is_dead_lettered(
    session_factory: sessionmaker[Session],
) -> None:
    add_jobs(session_factory, max_attempts=1)
    with session_factory.begin() as s:
        assert queue.claim(s, lease_seconds=0.5) is not None
    handler = RecordingHandler()
    time.sleep(1.0)

    assert make_worker(session_factory, handler).run_once() is False  # reaped, nothing to claim

    row = job(session_factory)
    assert (row.status, row.attempts, row.last_error) == ("dead", 1, queue.LEASE_EXPIRED_ERROR)
    assert handler.calls == []
    assert handler.dead == [(row.id, queue.LEASE_EXPIRED_ERROR)]


# --- retries with backoff ---


def test_failed_job_waits_for_its_backoff_then_succeeds(
    session_factory: sessionmaker[Session],
) -> None:
    add_jobs(session_factory)
    handler = RecordingHandler(fail_times=1)
    config = WorkerConfig(concurrency=1, lease_seconds=30, backoff_base_seconds=1.0)
    worker = make_worker(session_factory, handler, config)

    assert worker.run_once() is True  # attempt 1 fails
    row = job(session_factory)
    assert (row.status, row.attempts, row.last_error) == ("failed", 1, "RuntimeError: boom")
    # run_after and updated_at come from the same now(), so the gap is exactly the backoff.
    assert row.run_after - row.updated_at == timedelta(seconds=1)
    assert worker.run_once() is False  # not due yet

    time.sleep(1.2)
    assert worker.run_once() is True  # attempt 2 succeeds
    assert (job(session_factory).status, job(session_factory).attempts) == ("done", 2)


def test_backoff_doubles_with_each_failed_attempt(session_factory: sessionmaker[Session]) -> None:
    add_jobs(session_factory)
    config = WorkerConfig(concurrency=1, lease_seconds=30, backoff_base_seconds=0.25)
    worker = make_worker(session_factory, RecordingHandler(fail_times=3), config)
    gaps = []
    for _ in range(3):
        row = job(session_factory)
        time.sleep(max(0.0, (row.run_after - row.updated_at).total_seconds()) + 0.1)
        assert worker.run_once() is True
        row = job(session_factory)
        gaps.append(row.run_after - row.updated_at)

    assert gaps == [timedelta(seconds=0.25), timedelta(seconds=0.5), timedelta(seconds=1)]


# --- dead-letter ---


def test_poison_job_goes_to_dead_letter_after_max_attempts(
    session_factory: sessionmaker[Session],
) -> None:
    add_jobs(session_factory, max_attempts=3)
    handler = RecordingHandler(fail_times=100)
    worker = make_worker(session_factory, handler)

    drain(worker)

    row = job(session_factory)
    assert (row.status, row.attempts, row.last_error) == ("dead", 3, "RuntimeError: boom")
    assert row.locked_until is None
    assert len(handler.calls) == 3
    assert handler.dead == [(row.id, "RuntimeError: boom")]
    assert worker.run_once() is False  # stays dead


def test_permanent_error_goes_to_dead_letter_immediately(
    session_factory: sessionmaker[Session],
) -> None:
    add_jobs(session_factory, max_attempts=5)
    handler = RecordingHandler(fail_times=1, error=PermanentJobError("check deleted"))

    drain(make_worker(session_factory, handler))

    row = job(session_factory)
    assert (row.status, row.attempts) == ("dead", 1)
    assert row.last_error == "PermanentJobError: check deleted"


def test_job_without_a_handler_goes_to_dead_letter(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        queue.enqueue(s, "unknown_kind", {}, "orphan")

    drain(make_worker(session_factory, RecordingHandler()))

    row = job(session_factory, "orphan")
    assert row.status == "dead"
    assert row.last_error == "PermanentJobError: no handler for job kind 'unknown_kind'"


# --- graceful stop (in process; the SIGTERM path is in test_worker_process.py) ---


def test_stop_lets_the_running_job_finish_and_claims_no_more(
    session_factory: sessionmaker[Session],
) -> None:
    add_jobs(session_factory, count=2)
    handler = RecordingHandler(sleep=1.0)
    worker = make_worker(session_factory, handler)
    thread = threading.Thread(target=worker.run)
    thread.start()

    assert handler.started.wait(timeout=10)
    worker.request_stop()  # while the first job is mid-run
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert statuses(session_factory) == Counter({"done": 1, "queued": 1})
