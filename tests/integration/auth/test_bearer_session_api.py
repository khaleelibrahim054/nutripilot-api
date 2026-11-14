"""The mobile app reuses the Careem ID token returned at login as its bearer token.

Every request re-verifies the token (signature, issuer, audience, lifetime) and maps the Careem
identity to the NutriPilot user. GET /auth/session lets the app check its login.
"""

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.careem_tokens import id_token_claims, sign_id_token
from tests.support.fake_careem import ROGUE_KEY, SESSION_PATH, FakeCareem, login


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_unauthenticated(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_id_token_from_login_authenticates_the_user(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    logged_in = (await login(client, careem)).json()

    response = await client.get(SESSION_PATH, headers=bearer(logged_in["id_token"]))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"user_id", "expires_at"}
    assert body["user_id"] == logged_in["user_id"]
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert expires_at.tzinfo is not None
    assert expires_at > datetime.now(UTC)


async def test_each_user_is_resolved_from_their_own_token(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    layla = (await login(client, careem, sub="92305709")).json()
    omar = (await login(client, careem, sub="10000001")).json()

    response = await client.get(SESSION_PATH, headers=bearer(omar["id_token"]))

    assert response.json()["user_id"] == omar["user_id"] != layla["user_id"]


async def test_request_without_token_is_unauthenticated(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    assert_unauthenticated(await client.get(SESSION_PATH))


@pytest.mark.parametrize(
    "header",
    ["Basic dXNlcjpwYXNz", "Bearer", "Bearer ", "bearer-token-without-scheme", "Bearer a b"],
)
async def test_malformed_authorization_header_is_unauthenticated(
    client: httpx.AsyncClient, careem: FakeCareem, header: str
) -> None:
    assert_unauthenticated(await client.get(SESSION_PATH, headers={"Authorization": header}))


async def test_expired_id_token_is_unauthenticated(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    await login(client, careem)
    expired = sign_id_token(careem.key, id_token_claims(datetime.now(UTC), iat=1, exp=601))

    assert_unauthenticated(await client.get(SESSION_PATH, headers=bearer(expired)))


async def test_token_for_another_careem_client_is_unauthenticated(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    await login(client, careem)
    token = sign_id_token(
        careem.key, id_token_claims(datetime.now(UTC), aud="another-careem-client")
    )

    assert_unauthenticated(await client.get(SESSION_PATH, headers=bearer(token)))


async def test_forged_token_is_unauthenticated(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    await login(client, careem)
    forged = sign_id_token(ROGUE_KEY, id_token_claims(datetime.now(UTC)))

    assert_unauthenticated(await client.get(SESSION_PATH, headers=bearer(forged)))


async def test_valid_careem_token_for_someone_who_never_logged_in_is_unauthenticated(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    token = sign_id_token(careem.key, id_token_claims(datetime.now(UTC), sub="77700077"))

    response = await client.get(SESSION_PATH, headers=bearer(token))

    assert_unauthenticated(response)
    count = (await db_session.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert count == 0  # bearer use never provisions accounts


async def test_disabled_user_token_is_unauthenticated(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    logged_in = (await login(client, careem)).json()
    await db_session.execute(text("UPDATE users SET status = 'disabled'"))

    response = await client.get(SESSION_PATH, headers=bearer(logged_in["id_token"]))

    assert_unauthenticated(response)
