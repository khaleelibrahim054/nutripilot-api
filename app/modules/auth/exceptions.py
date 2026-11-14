"""Auth errors. Messages are generic on purpose: they never say which check failed."""

from fastapi import status

from app.core.exceptions import DomainError


class CareemLoginUnavailableError(DomainError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "careem_login_unavailable"


class InvalidStateError(DomainError):
    code = "invalid_state"


class LoginExpiredError(DomainError):
    code = "login_expired"


class AuthenticationFailedError(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_failed"


class CareemUnavailableError(DomainError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "careem_unavailable"


class AccountDisabledError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "account_disabled"


class UnauthenticatedError(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    headers = {"WWW-Authenticate": "Bearer"}  # noqa: RUF012 - read-only class constant
