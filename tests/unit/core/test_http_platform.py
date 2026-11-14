"""Middleware + exception handlers working together on a minimal app (no database)."""

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI, Query
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.exceptions import register_exception_handlers
from app.core.middleware import REQUEST_ID_HEADER, register_middleware, resolve_request_id


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    register_middleware(app, Settings(cors_origins=["https://app.nutripilot.ae"]))
    register_exception_handlers(app)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("password=hunter2 at /srv/app/db.py line 10")

    @app.get("/search")
    async def search(limit: int = Query(ge=1, le=100)) -> dict[str, int]:
        return {"limit": limit}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_generates_request_id_when_none_is_sent(client: AsyncClient) -> None:
    response = await client.get("/ok")

    assert len(response.headers[REQUEST_ID_HEADER]) == 32


async def test_echoes_a_well_formed_caller_request_id(client: AsyncClient) -> None:
    response = await client.get("/ok", headers={REQUEST_ID_HEADER: "mobile-7f3a.1"})

    assert response.headers[REQUEST_ID_HEADER] == "mobile-7f3a.1"


@pytest.mark.parametrize(
    "incoming",
    ["", "has space", "x" * 129, "line\nbreak", "<script>", "مرحبا"],
)
def test_replaces_unsafe_or_oversized_request_ids(incoming: str) -> None:
    assert resolve_request_id(incoming) != incoming


async def test_unhandled_error_returns_standard_500_without_internals(client: AsyncClient) -> None:
    response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert "hunter2" not in response.text


async def test_500_still_carries_request_id_and_security_headers(client: AsyncClient) -> None:
    response = await client.get("/boom")

    assert REQUEST_ID_HEADER in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


async def test_unknown_route_uses_standard_error_shape(client: AsyncClient) -> None:
    response = await client.get("/nope")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


async def test_wrong_method_uses_standard_error_shape(client: AsyncClient) -> None:
    response = await client.delete("/ok")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


async def test_validation_error_lists_fields_without_echoing_input(client: AsyncClient) -> None:
    response = await client.get("/search", params={"limit": "SECRET-TOKEN-999"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["location"] == ["query", "limit"]
    assert "SECRET-TOKEN-999" not in response.text


async def test_cors_allows_only_configured_origins(client: AsyncClient) -> None:
    allowed = await client.get("/ok", headers={"Origin": "https://app.nutripilot.ae"})
    denied = await client.get("/ok", headers={"Origin": "https://evil.example"})

    assert allowed.headers["access-control-allow-origin"] == "https://app.nutripilot.ae"
    assert "access-control-allow-origin" not in denied.headers
