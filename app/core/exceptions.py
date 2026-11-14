"""Domain error types and the handlers that map every error to the shared ErrorResponse shape."""

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.shared.schemas.errors import ErrorBody, ErrorResponse, FieldError

logger = logging.getLogger(__name__)

INTERNAL_ERROR_CODE = "internal_error"
VALIDATION_ERROR_CODE = "validation_error"


class DomainError(Exception):
    """Base class for expected business errors. Services raise these; never HTTPException."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"
    headers: dict[str, str] | None = None  # e.g. WWW-Authenticate on 401

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class PermissionDeniedError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


def error_response(status_code: int, body: ErrorBody) -> JSONResponse:
    content = ErrorResponse(error=body).model_dump(exclude_none=True)
    return JSONResponse(status_code=status_code, content=content)


def internal_error_response() -> JSONResponse:
    # Never leak internals (stack traces, SQL, paths) to clients.
    body = ErrorBody(code=INTERNAL_ERROR_CODE, message="Internal server error")
    return error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, body)


def _status_code_slug(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase.lower().replace(" ", "_").replace("-", "_")
    except ValueError:
        return "http_error"


def _field_errors(exc: RequestValidationError) -> list[FieldError]:
    # Deliberately omit the rejected `input` value: it may contain secrets or PII.
    return [
        FieldError(location=list(err["loc"]), message=err["msg"], type=err["type"])
        for err in exc.errors()
    ]


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        response = error_response(exc.status_code, ErrorBody(code=exc.code, message=exc.message))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        body = ErrorBody(code=_status_code_slug(exc.status_code), message=str(exc.detail))
        response = error_response(exc.status_code, body)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        body = ErrorBody(
            code=VALIDATION_ERROR_CODE,
            message="Request validation failed",
            details=_field_errors(exc),
        )
        return error_response(status.HTTP_422_UNPROCESSABLE_CONTENT, body)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return internal_error_response()
