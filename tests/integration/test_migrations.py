from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import run_alembic


async def test_models_and_migrations_are_in_sync(engine: AsyncEngine) -> None:
    """Every model change must ship with an Alembic migration."""
    result = run_alembic("check")

    assert result.returncode == 0, (
        "ORM models differ from migrations - create one with "
        "`uv run alembic revision --autogenerate -m '...'`.\n" + result.stdout + result.stderr
    )
