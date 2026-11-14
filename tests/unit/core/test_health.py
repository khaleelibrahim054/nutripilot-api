from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core import health
from app.core.database import get_session


class UnreachableDatabaseSession:
    async def execute(self, *_: object) -> None:
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("5432 refused"))


def test_health_returns_503_when_database_is_unreachable() -> None:
    app = FastAPI()
    app.include_router(health.router)

    async def _unreachable() -> AsyncGenerator[UnreachableDatabaseSession, None]:
        yield UnreachableDatabaseSession()

    app.dependency_overrides[get_session] = _unreachable

    response = TestClient(app).get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unavailable"}
    assert "5432" not in response.text
