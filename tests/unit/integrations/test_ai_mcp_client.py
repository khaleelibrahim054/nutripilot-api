"""AI/MCP service client against a fake upstream (httpx.MockTransport) - no network."""

import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from app.core.config import Settings
from app.core.logging import request_id_var
from app.integrations.ai_mcp_service.client import AiMcpServiceClient
from app.integrations.ai_mcp_service.exceptions import (
    AiServiceProtocolError,
    AiServiceResponseError,
    AiServiceUnavailableError,
)

SSE_HEADERS = {"content-type": "text/event-stream; charset=utf-8"}
MEAL_PLAN_STREAM = (
    b'event: stream.started\ndata: {"request_id":"r1"}\n\n'
    b": heartbeat\n\n"
    b'event: generation.progress\ndata: {"stage":"generating_meals","completed":1,"total":7}\n\n'
    b"data: [DONE]\n\n"
)


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> AiMcpServiceClient:
    settings = Settings(ai_service_url="http://ai-service.internal:8100")
    return AiMcpServiceClient.from_settings(settings, transport=httpx.MockTransport(handler))


async def collect(client: AiMcpServiceClient, path: str = "/v1/meal-plans/generate") -> list:
    async with client:
        return [event async for event in client.stream_events(path, {"days": 7})]


async def test_streams_parsed_events_until_done() -> None:
    client = make_client(
        lambda _: httpx.Response(200, headers=SSE_HEADERS, content=MEAL_PLAN_STREAM)
    )

    events = await collect(client)

    assert [e.event for e in events] == ["stream.started", "generation.progress"]
    assert events[1].json_data()["total"] == 7


async def test_sends_json_post_to_base_url_with_sse_accept_and_request_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers=SSE_HEADERS, content=b"data: [DONE]\n\n")

    token = request_id_var.set("req-dxb-42")
    try:
        await collect(make_client(handler))
    finally:
        request_id_var.reset(token)

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "http://ai-service.internal:8100/v1/meal-plans/generate"
    assert request.headers["accept"] == "text/event-stream"
    assert request.headers["x-request-id"] == "req-dxb-42"
    assert json.loads(request.content) == {"days": 7}


async def test_events_are_yielded_incrementally_from_a_chunked_body() -> None:
    async def chunked() -> AsyncIterator[bytes]:
        for index in range(0, len(MEAL_PLAN_STREAM), 5):
            yield MEAL_PLAN_STREAM[index : index + 5]

    client = make_client(lambda _: httpx.Response(200, headers=SSE_HEADERS, content=chunked()))

    assert len(await collect(client)) == 2


@pytest.mark.parametrize("status_code", [400, 401, 500, 503])
async def test_non_success_status_raises_response_error(status_code: int) -> None:
    client = make_client(lambda _: httpx.Response(status_code, json={"detail": "internal"}))

    with pytest.raises(AiServiceResponseError) as caught:
        await collect(client)

    assert caught.value.status_code == status_code


async def test_non_sse_content_type_raises_protocol_error() -> None:
    client = make_client(lambda _: httpx.Response(200, json={"meals": []}))

    with pytest.raises(AiServiceProtocolError):
        await collect(client)


@pytest.mark.parametrize(
    "transport_error",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("idle"), httpx.RemoteProtocolError("eof")],
)
async def test_transport_failures_raise_unavailable(transport_error: httpx.HTTPError) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise transport_error

    with pytest.raises(AiServiceUnavailableError):
        await collect(make_client(handler))


@pytest.mark.parametrize(
    "path", ["v1/generate", "https://evil.example/steal", "//evil.example/steal"]
)
async def test_rejects_paths_that_could_leave_the_ai_service(path: str) -> None:
    client = make_client(lambda _: httpx.Response(200, headers=SSE_HEADERS, content=b""))

    with pytest.raises(ValueError, match="relative"):
        await collect(client, path)
