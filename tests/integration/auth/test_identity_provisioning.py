"""Concurrent first logins for the same Careem identity (e.g. the app retries on a flaky network
while the first request is still running) must yield exactly one NutriPilot user.

Uses real, committed transactions on separate connections (not the rolled-back test session), so
PostgreSQL's unique constraint is genuinely contended. Rows are cleaned up afterwards.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
from app.integrations.careem_identity.client import CareemIdentityClient
from app.modules.auth.service import CareemAuthService
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import get_settings
from tests.support.careem_tokens import ISSUER


@pytest.fixture
async def subject(engine: AsyncEngine) -> AsyncGenerator[str, None]:
    careem_subject = f"race-{uuid.uuid4().hex}"
    yield careem_subject
    async with engine.begin() as conn:
        user_ids = (
            (
                await conn.execute(
                    text("SELECT user_id FROM auth_identities WHERE subject = :s"),
                    {"s": careem_subject},
                )
            )
            .scalars()
            .all()
        )
        await conn.execute(
            text("DELETE FROM auth_identities WHERE subject = :s"), {"s": careem_subject}
        )
        for user_id in user_ids:
            await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


async def count(engine: AsyncEngine, sql: str, subject: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), {"s": subject})).scalar_one()


async def test_concurrent_first_logins_create_exactly_one_user(
    engine: AsyncEngine, careem_identity_client: CareemIdentityClient, subject: str
) -> None:
    users_before = await count(engine, "SELECT count(*) FROM users WHERE :s = :s", subject)

    async def provision() -> tuple[uuid.UUID, bool]:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            service = CareemAuthService(session, get_settings(), careem_identity_client)
            provisioned = await service.get_or_create_user(ISSUER, subject)
            return provisioned.user_id, provisioned.is_new_user

    results = await asyncio.gather(*(provision() for _ in range(5)))

    assert len({user_id for user_id, _ in results}) == 1
    assert sum(is_new for _, is_new in results) == 1
    identities = await count(
        engine, "SELECT count(*) FROM auth_identities WHERE subject = :s", subject
    )
    assert identities == 1
    users_after = await count(engine, "SELECT count(*) FROM users WHERE :s = :s", subject)
    assert users_after - users_before == 1  # losers leave no orphan users behind


async def test_sequential_logins_reuse_the_committed_user(
    engine: AsyncEngine, careem_identity_client: CareemIdentityClient, subject: str
) -> None:
    async def provision() -> uuid.UUID:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            service = CareemAuthService(session, get_settings(), careem_identity_client)
            return (await service.get_or_create_user(ISSUER, subject)).user_id

    first = await provision()
    second = await provision()

    assert first == second
