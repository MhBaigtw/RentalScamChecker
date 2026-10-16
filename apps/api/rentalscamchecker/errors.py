"""The one error shape (SPEC.md section 9) and the handlers that make every error use it."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

HTTP_ERROR_CODES = {
    401: "unauthorized",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
}


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """The one error shape used by every endpoint (SPEC.md section 9)."""
    return {"error": {"code": code, "message": message, "details": details or {}}}


class ApiError(Exception):
    """Raise from a handler to return the standard error shape with a given status."""

    def __init__(
        self, status: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


class FieldError(ValueError):
    """One invalid input field. Collected into a `validation_error` response."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def validation_error(errors: list[FieldError]) -> ApiError:
    return ApiError(
        422,
        "validation_error",
        "Some fields are missing or invalid.",
        {"fields": [{"field": e.field, "message": e.message} for e in errors]},
    )


def _location_to_field(loc: tuple[Any, ...]) -> str:
    # FastAPI locations look like ("body", "bedrooms") or ("path", "check_id"); drop the source.
    parts = [str(p) for p in loc[1:]] if len(loc) > 1 else [str(p) for p in loc]
    return ".".join(parts)


def install_error_handlers(app: FastAPI) -> None:
    log = structlog.get_logger("rentalscamchecker.errors")

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status, content=error_body(exc.code, exc.message, exc.details)
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [FieldError(_location_to_field(tuple(e["loc"])), e["msg"]) for e in exc.errors()]
        api_error = validation_error(errors)
        return await handle_api_error(_, api_error)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = HTTP_ERROR_CODES.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail)),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", exc_info=exc)
        return JSONResponse(
            status_code=500, content=error_body("internal_error", "Something went wrong.")
        )
