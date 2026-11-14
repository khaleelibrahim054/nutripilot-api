import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import ConflictError, NotFoundError, register_exception_handlers


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("Ride 42 not found")

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError("Driver already assigned")

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("password=hunter2 at /srv/app/db.py line 10")

    return TestClient(app, raise_server_exceptions=False)


def test_domain_error_maps_to_status_and_stable_error_shape(client: TestClient) -> None:
    response = client.get("/missing")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Ride 42 not found"}}


def test_conflict_error_maps_to_409(client: TestClient) -> None:
    assert client.get("/conflict").status_code == 409


def test_unhandled_error_does_not_leak_internal_details(client: TestClient) -> None:
    response = client.get("/boom")

    assert response.status_code == 500
    assert "hunter2" not in response.text
    assert "/srv/app" not in response.text
    assert response.json()["error"]["code"] == "internal_error"
