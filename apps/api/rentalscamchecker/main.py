"""FastAPI application (HTTP entrypoint)."""

from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rentalscamchecker.config import Settings, get_settings
from rentalscamchecker.db import database_is_up, make_engine
from rentalscamchecker.log_config import configure_logging, request_id_middleware


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """The one error shape used by every endpoint (SPEC.md section 9)."""
    return {"error": {"code": code, "message": message, "details": details or {}}}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url, settings.database_connect_timeout)
    log = structlog.get_logger("rentalscamchecker.health")

    app = FastAPI(title="RentalScamChecker API", version="0.1.0")
    app.middleware("http")(request_id_middleware)

    @app.get("/health")
    def health() -> JSONResponse:
        if not database_is_up(engine):
            log.warning("health_db_unavailable")
            return JSONResponse(
                status_code=503,
                content=error_body(
                    "database_unavailable",
                    "The database is not reachable.",
                    {"database": "unavailable"},
                ),
            )
        return JSONResponse(content={"status": "ok", "database": "ok"})

    return app
