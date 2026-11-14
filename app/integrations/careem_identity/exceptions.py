"""Errors raised by the Careem identity client. Messages are for logs, never for end users."""


class CareemIdentityError(Exception):
    """Base class for Careem identity failures."""


class CareemUnavailableError(CareemIdentityError):
    """Careem could not be reached, timed out, or answered with a server error."""


class CareemTokenExchangeError(CareemIdentityError):
    """Careem refused the authorization code, or its token response was unusable."""


class IdTokenValidationError(CareemIdentityError):
    """The ID token failed verification and must not be trusted."""
