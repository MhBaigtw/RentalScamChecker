"""FastAPI application (HTTP entrypoint)."""

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from rentalscamchecker.checks.api import build_router as build_checks_router
from rentalscamchecker.config import Settings, get_settings
from rentalscamchecker.db import database_is_up, make_engine
from rentalscamchecker.errors import error_body, install_error_handlers
from rentalscamchecker.log_config import configure_logging, request_id_middleware
from rentalscamchecker.storage import LocalDiskStorage

__all__ = ["create_app", "error_body"]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url, settings.database_connect_timeout)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    storage = LocalDiskStorage(settings.photo_storage_path)
    log = structlog.get_logger("rentalscamchecker.health")

    app = FastAPI(title="RentalScamChecker API", version="0.1.0")
    app.middleware("http")(request_id_middleware)
    install_error_handlers(app)
    app.include_router(build_checks_router(session_factory, storage, settings))

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
