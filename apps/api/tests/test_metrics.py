"""GET /internal/metrics: worker heartbeats and queue depth, and who may read them."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.jobs import queue
from rentalscamchecker.jobs.worker import Worker, WorkerConfig
from rentalscamchecker.main import create_app
from tests.helpers import UNREACHABLE_DB_URL, make_settings


def test_metrics_require_the_token_when_one_is_set() -> None:
    settings = make_settings(UNREACHABLE_DB_URL, internal_metrics_token=SecretStr("s3cret"))
    client = TestClient(create_app(settings))

    missing = client.get("/internal/metrics")
    wrong = client.get("/internal/metrics", headers={"Authorization": "Bearer nope"})

    assert (missing.status_code, missing.json()["error"]["code"]) == (401, "unauthorized")
    assert wrong.status_code == 401


def test_metrics_are_disabled_in_production_without_a_token() -> None:
    client = TestClient(create_app(make_settings(UNREACHABLE_DB_URL, environment="production")))

    response = client.get("/internal/metrics")

    assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found")


@pytest.mark.integration
def test_metrics_report_worker_counters_and_queue_depth(
    session_factory: sessionmaker[Session], photo_dir: Path
) -> None:
    with session_factory.begin() as s:
        queue.enqueue(s, "test", {}, "metrics-1")
        queue.enqueue(s, "test", {}, "metrics-2", delay_seconds=3600)
    worker = Worker(session_factory, {}, WorkerConfig(concurrency=3, job_timeout_seconds=20))
    worker.write_heartbeat()
    settings = make_settings(photo_dir=photo_dir, internal_metrics_token=SecretStr("s3cret"))
    client = TestClient(create_app(settings))

    body = client.get("/internal/metrics", headers={"Authorization": "Bearer s3cret"}).json()

    assert body["jobs_by_status"] == {"queued": 2, "running": 0, "done": 0, "failed": 0, "dead": 0}
    assert body["workers_alive"] == 1
    assert body["abandoned_threads_alive"] == 0
    assert body["jobs_timed_out_total"] == 0
    (row,) = body["workers"]
    assert row["worker_id"] == worker.worker_id
    assert (row["alive"], row["concurrency"]) == (True, 3)

    worker.write_heartbeat(stopped=True)
    body = client.get("/internal/metrics", headers={"Authorization": "Bearer s3cret"}).json()
    assert body["workers_alive"] == 0
    assert body["workers"][0]["stopped_at"] is not None
