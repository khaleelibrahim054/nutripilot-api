"""HTTP client for the AI/MCP service (a separate application).

Credentials and the service URL stay on this backend; clients never call the AI service directly.
Closing the event iterator early (e.g. the end user disconnected) closes the upstream response,
which aborts the upstream generation request.
"""

from collections.abc import AsyncIterator, Mapping
from types import TracebackType
from typing import Any, Self

import httpx

from app.core.config import Settings
from app.core.logging import request_id_var
from app.integrations.ai_mcp_service.exceptions import (
    AiServiceProtocolError,
    AiServiceResponseError,
    AiServiceUnavailableError,
)
from app.integrations.ai_mcp_service.schemas import ServerSentEvent
from app.integrations.ai_mcp_service.streaming import aiter_sse

EVENT_STREAM_MEDIA_TYPE = "text/event-stream"


class AiMcpServiceClient:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> Self:
        # `read` is the maximum idle gap between stream chunks, not the total stream duration.
        timeout = httpx.Timeout(
            settings.ai_service_timeout_seconds,
            read=settings.ai_service_stream_idle_timeout_seconds,
        )
        http_client = httpx.AsyncClient(
            base_url=str(settings.ai_service_url), timeout=timeout, transport=transport
        )
        return cls(http_client)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def stream_events(
        self, path: str, payload: Mapping[str, Any]
    ) -> AsyncIterator[ServerSentEvent]:
        """POST `payload` to `path` and yield the service's SSE events as they arrive.

        Raises AiServiceUnavailableError (unreachable/timeout), AiServiceResponseError (non-2xx)
        or AiServiceProtocolError (not an SSE stream, or an oversized event).
        """
        _ensure_relative_path(path)
        try:
            async with self._http.stream(
                "POST", path, json=dict(payload), headers=_outgoing_headers()
            ) as response:
                _ensure_event_stream(response)
                async for event in aiter_sse(response.aiter_bytes()):
                    yield event
        except httpx.TimeoutException as exc:
            raise AiServiceUnavailableError("AI service timed out") from exc
        except httpx.TransportError as exc:
            raise AiServiceUnavailableError("AI service is unreachable") from exc


def _ensure_relative_path(path: str) -> None:
    # An absolute URL would make httpx ignore base_url and send the request elsewhere.
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        raise ValueError("path must be relative to the AI service base URL, e.g. '/v1/...'")


def _outgoing_headers() -> dict[str, str]:
    headers = {"Accept": EVENT_STREAM_MEDIA_TYPE}
    request_id = request_id_var.get()
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    return headers


def _ensure_event_stream(response: httpx.Response) -> None:
    if not response.is_success:
        raise AiServiceResponseError(response.status_code)
    content_type = response.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != EVENT_STREAM_MEDIA_TYPE:
        raise AiServiceProtocolError("AI service did not return an event stream")
