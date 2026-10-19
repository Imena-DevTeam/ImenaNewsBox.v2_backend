"""Base application exceptions and the handlers that map them to HTTP responses.

Every error response has the same body: `{"error": {"code": ..., "message": ...}}`, plus a
`details` list for request validation errors. Services raise an `AppError` subclass (usually a
feature exception that subclasses one of the common ones below) and never build responses or
raise `HTTPException` themselves.
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, ClassVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

NO_STORE = "private, no-store"


class FieldError(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[FieldError] | None = None


class ErrorResponse(BaseModel):
    """The body of every error response. Routers can list it in `responses=` for OpenAPI."""

    error: ErrorBody


class AppError(Exception):
    """Base for every exception that maps to an HTTP error response.

    Subclasses set `status_code`, `code` (a stable, machine-readable string the frontend can
    branch on) and a default `message`, which can be overridden per raise.
    """

    status_code: ClassVar[int] = HTTPStatus.INTERNAL_SERVER_ERROR
    code: ClassVar[str] = "internal_error"
    default_message: ClassVar[str] = "Something went wrong."
    headers: ClassVar[dict[str, str] | None] = None

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class BadRequest(AppError):
    status_code = HTTPStatus.BAD_REQUEST
    code = "bad_request"
    default_message = "The request is invalid."


class Unauthorized(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "unauthorized"
    default_message = "Authentication is required."
    headers = {"WWW-Authenticate": "Bearer"}  # noqa: RUF012 (read-only class constant)


class Forbidden(AppError):
    status_code = HTTPStatus.FORBIDDEN
    code = "forbidden"
    default_message = "You don't have permission to do this."


class NotFound(AppError):
    status_code = HTTPStatus.NOT_FOUND
    code = "not_found"
    default_message = "The resource was not found."


class Conflict(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "conflict"
    default_message = "The request conflicts with the current state of the resource."


class TooManyRequests(AppError):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    code = "too_many_requests"
    default_message = "Too many requests. Try again later."


# Codes for HTTP errors raised by FastAPI/Starlette themselves (unknown route, wrong method,
# and FastAPI's security helpers), so they share the frontend-facing codes above.
_HTTP_ERROR_CODES = {
    HTTPStatus.BAD_REQUEST: BadRequest.code,
    HTTPStatus.UNAUTHORIZED: Unauthorized.code,
    HTTPStatus.FORBIDDEN: Forbidden.code,
    HTTPStatus.NOT_FOUND: NotFound.code,
    HTTPStatus.METHOD_NOT_ALLOWED: "method_not_allowed",
    HTTPStatus.CONFLICT: Conflict.code,
    HTTPStatus.TOO_MANY_REQUESTS: TooManyRequests.code,
}


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers=headers,
    )


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return _error_response(exc.status_code, exc.code, exc.message, headers=exc.headers)


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Only the location and message: FastAPI's default body also echoes the submitted input,
    # which may contain codes, passwords or emails.
    details = [
        FieldError(field=".".join(str(part) for part in error["loc"]), message=error["msg"])
        for error in exc.errors()
    ]
    return _error_response(
        HTTPStatus.UNPROCESSABLE_CONTENT,
        "validation_error",
        "The request has invalid or missing fields.",
        details=details,
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    status = HTTPStatus(exc.status_code)
    message = exc.detail if isinstance(exc.detail, str) else status.phrase
    return _error_response(
        exc.status_code,
        _HTTP_ERROR_CODES.get(status, "http_error"),
        message,
        headers=exc.headers,
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Starlette re-raises the exception after this handler runs, so uvicorn logs the traceback.
    # Logging it here too would duplicate it. This response is sent from outside the app's
    # middleware, so it sets its own Cache-Control header.
    return _error_response(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        AppError.code,
        AppError.default_message,
        headers={"Cache-Control": NO_STORE},
    )


def register_exception_handlers(app: FastAPI) -> None:
    handlers: dict[type[Exception], Any] = {
        AppError: _handle_app_error,
        RequestValidationError: _handle_validation_error,
        StarletteHTTPException: _handle_http_exception,
        Exception: _handle_unexpected_error,
    }
    for exc_class, handler in handlers.items():
        app.add_exception_handler(exc_class, handler)
