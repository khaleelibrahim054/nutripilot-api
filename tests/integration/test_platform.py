"""Cross-cutting behaviour of the real application (real middleware stack, real database)."""

from httpx import AsyncClient

from app.api import API_PREFIX


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/health", headers={"X-Request-ID": "web-req-1"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "web-req-1"


async def test_unknown_route_returns_standard_error_body(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/meal-plans-typo")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}
    assert "X-Request-ID" in response.headers


async def test_routes_are_only_served_under_the_versioned_prefix(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 404
