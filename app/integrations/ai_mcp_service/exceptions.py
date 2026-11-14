"""Transport-level failures talking to the AI/MCP service.

These are not DomainErrors: the calling module decides what they mean for the user (for a stream,
typically a `stream.error` event) and must never forward raw upstream details to clients.
"""


class AiServiceError(Exception):
    """Base class for every AI/MCP service client failure."""


class AiServiceUnavailableError(AiServiceError):
    """The service could not be reached, or it timed out (connect, or idle gap in a stream)."""


class AiServiceResponseError(AiServiceError):
    """The service answered with a non-success HTTP status."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"AI service responded with HTTP {status_code}")
        self.status_code = status_code


class AiServiceProtocolError(AiServiceError):
    """The service answered, but not with a valid SSE stream."""
