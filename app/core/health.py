import logging

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.dependencies import SessionDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthRead(BaseModel):
    status: str
    database: str


@router.get("/health", summary="Health check")
async def health(session: SessionDep, response: Response) -> HealthRead:
    """Public liveness + database readiness probe. 503 when PostgreSQL is unreachable."""
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        logger.warning("Health check: database unreachable", exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthRead(status="unavailable", database="unavailable")
    return HealthRead(status="ok", database="ok")
