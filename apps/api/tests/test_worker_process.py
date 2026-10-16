"""The real worker process: started as `python -m rentalscamchecker.worker`, stopped with a signal
while a job is running. The job must finish, and the process must exit cleanly without claiming
more work.

Linux (CI, production) gets SIGTERM, the signal a platform sends on shutdown. Windows can't
deliver SIGTERM to another process (Popen.send_signal(SIGTERM) calls TerminateProcess, a hard
kill), so there the test sends CTRL_BREAK, which the worker maps to the same graceful stop.
"""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.models import Check
from rentalscamchecker.pipeline import aggregate_job_key, enqueue_check_jobs, signal_job_key
from rentalscamchecker.signals.stub import StubSignal
from tests.helpers import TEST_PEPPER

pytestmark = pytest.mark.integration

API_DIR = Path(__file__).resolve().parents[1]
IS_WINDOWS = sys.platform == "win32"


def job_status(sf: sessionmaker[Session], key: str) -> str:
    with sf() as s:
        return str(
            s.execute(
                sa.text("SELECT status FROM jobs WHERE idempotency_key = :k"), {"k": key}
            ).scalar_one()
        )


def test_stop_signal_lets_the_running_job_finish(
    session_factory: sessionmaker[Session], test_database: str
) -> None:
    check_id = uuid.uuid4()
    with session_factory.begin() as s:
        s.add(
            Check(
                id=check_id,
                listing_text="x",
                rent_cents=100_000,
                bedrooms=1,
                address_text="1 Main St",
                city="Oakville",
            )
        )
        s.flush()
        enqueue_check_jobs(
            s, check_id, [StubSignal(2.0)], check_timeout_seconds=3600, max_attempts=5
        )

    env = {
        **os.environ,
        "DATABASE_URL": test_database,
        "CONTACT_HASH_PEPPER": TEST_PEPPER,
        "ENABLED_SIGNALS": "stub",
        "STUB_SIGNAL_SLEEP_SECONDS": "2",
        "WORKER_CONCURRENCY": "1",
        "WORKER_POLL_INTERVAL_SECONDS": "0.1",
    }
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
    proc = subprocess.Popen(
        [sys.executable, "-m", "rentalscamchecker.worker"],
        cwd=API_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=flags,
    )
    signal_key = signal_job_key(check_id, "stub", "1")
    try:
        deadline = time.monotonic() + 30
        while job_status(session_factory, signal_key) != "running":
            assert time.monotonic() < deadline, "worker never claimed the job"
            assert proc.poll() is None, proc.stdout.read() if proc.stdout else ""
            time.sleep(0.05)

        proc.send_signal(signal.CTRL_BREAK_EVENT if IS_WINDOWS else signal.SIGTERM)
        output, _ = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()

    events = [json.loads(line)["event"] for line in output.splitlines() if line.startswith("{")]
    assert proc.returncode == 0, output
    assert job_status(session_factory, signal_key) == "done"  # finished despite the signal
    # Its result made the aggregator due, but a stopping worker claims nothing new.
    assert job_status(session_factory, aggregate_job_key(check_id)) == "queued"
    assert events.index("shutdown_requested") < events.index("job_done")
    assert events[-1] == "worker_stopped"
