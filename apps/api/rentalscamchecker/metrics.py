"""GET /internal/metrics: queue depth by status, and each worker process's liveness and counters
(from worker_heartbeats, which the workers write every few seconds). JSON, for operators.

Access: when INTERNAL_METRICS_TOKEN is set, requests need `Authorization: Bearer <token>`. When
it isn't set, the endpoint is open in development/test and disabled (404) in production.
"""

import hmac
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.config import Settings
from rentalscamchecker.errors import ApiError
from rentalscamchecker.models import JOB_STATUSES, Job, WorkerHeartbeat

# A worker that hasn't written a heartbeat for this long is reported as not alive.
STALE_AFTER_SECONDS = 30


def build_router(session_factory: sessionmaker[Session], settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/internal/metrics")
    def internal_metrics(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize(settings, authorization)
        with session_factory() as session:
            return collect(session)

    return router


def _authorize(settings: Settings, authorization: str | None) -> None:
    token = settings.internal_metrics_token
    if token is None:
        if settings.environment == "production":
            raise ApiError(404, "not_found", "Not Found")
        return
    expected = f"Bearer {token.get_secret_value()}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise ApiError(401, "unauthorized", "A valid metrics token is required.")


def collect(session: Session) -> dict[str, Any]:
    now: datetime = session.scalar(select(func.now()))  # type: ignore[assignment]
    by_status = dict.fromkeys(JOB_STATUSES, 0)
    for status, count in session.execute(select(Job.status, func.count()).group_by(Job.status)):
        by_status[status] = count
    workers: list[dict[str, Any]] = []
    for hb in session.scalars(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(50)
    ):
        seconds_since = (now - hb.last_seen_at).total_seconds()
        workers.append(
            {
                "worker_id": hb.worker_id,
                "alive": hb.stopped_at is None and seconds_since <= STALE_AFTER_SECONDS,
                "started_at": hb.started_at.isoformat(),
                "last_seen_seconds_ago": round(seconds_since, 1),
                "stopped_at": hb.stopped_at.isoformat() if hb.stopped_at else None,
                "concurrency": hb.concurrency,
                "jobs_done_total": hb.jobs_done_total,
                "jobs_failed_total": hb.jobs_failed_total,
                "jobs_timed_out_total": hb.jobs_timed_out_total,
                "abandoned_threads_alive": hb.abandoned_threads_alive,
            }
        )
    alive = [w for w in workers if w["alive"]]
    return {
        "jobs_by_status": by_status,
        "workers_alive": len(alive),
        "abandoned_threads_alive": sum(int(w["abandoned_threads_alive"]) for w in alive),
        "jobs_timed_out_total": sum(int(w["jobs_timed_out_total"]) for w in workers),
        "workers": workers,
    }
