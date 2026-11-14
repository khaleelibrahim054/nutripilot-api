"""The single error envelope returned by every endpoint (see docs/contracts/error-responses.md)."""

from pydantic import BaseModel


class FieldError(BaseModel):
    location: list[str | int]
    message: str
    type: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[FieldError] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
