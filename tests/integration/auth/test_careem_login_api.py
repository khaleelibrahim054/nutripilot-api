"""Careem login for the NutriPilot mobile app.

Flow: app -> POST /auth/careem/start -> Careem login page -> deep link with code/state ->
app -> POST /auth/careem/callback -> backend exchanges the code (PKCE + client secret), validates
the ID token, finds or creates the NutriPilot user, and returns the ID token for reuse as bearer.
"""

from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import TokenCipher
from app.main import app
from tests.conftest import TEST_CAREEM_ENV
from tests.support.careem_tokens import (
    CLIENT_ID,
    ISSUER,
    OMIT,
    SAMPLE_SUBJECT,
    s256,
    sha256_hex,
)
from tests.support.fake_careem import (
    ROGUE_KEY,
    FakeCareem,
    MobileApp,
    complete_login,
    login,
    start_login,
)

LOGIN_RESPONSE_KEYS = {"id_token", "token_type", "expires_in", "user_id", "is_new_user"}


def query_params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def error_code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


async def count_rows(db: AsyncSession, sql: str, **params: Any) -> int:
    return (await db.execute(text(sql), params)).scalar_one()


async def identities_for(db: AsyncSession, subject: str) -> int:
    return await count_rows(
        db, "SELECT count(*) FROM auth_identities WHERE subject = :s", s=subject
    )


async def assert_nobody_logged_in(db: AsyncSession, subject: str = SAMPLE_SUBJECT) -> None:
    assert await identities_for(db, subject) == 0
    assert await count_rows(db, "SELECT count(*) FROM users") == 0
    assert await count_rows(db, "SELECT count(*) FROM careem_credentials") == 0


# --- start ---


async def test_start_returns_careem_authorization_url(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    response = await start_login(client, MobileApp())

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"authorization_url", "expires_in"}
    assert body["expires_in"] == 600
    parts = urlsplit(body["authorization_url"])
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://identity.careem.com/authorize"
    params = query_params(body["authorization_url"])
    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == TEST_CAREEM_ENV["CAREEM_REDIRECT_URI"]
    assert params["scope"] == "openid"
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) >= 43
    assert len(params["nonce"]) >= 43
    assert len(params["code_challenge"]) == 43
    assert "no-store" in response.headers["cache-control"]


async def test_careem_pkce_challenge_is_the_backends_not_the_apps(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    mobile = MobileApp()

    response = await start_login(client, mobile)

    assert query_params(response.json()["authorization_url"])["code_challenge"] != (
        mobile.client_challenge
    )


async def test_each_login_attempt_gets_fresh_state_nonce_and_pkce(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    urls = [(await start_login(client, MobileApp())).json()["authorization_url"] for _ in range(3)]

    for name in ("state", "nonce", "code_challenge"):
        assert len({query_params(url)[name] for url in urls}) == 3


async def test_login_transaction_secrets_are_hashed_or_encrypted_at_rest(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    mobile = MobileApp()
    params = query_params((await start_login(client, mobile)).json()["authorization_url"])

    row = (
        await db_session.execute(
            text(
                "SELECT state_hash, nonce_hash, pkce_verifier_encrypted, client_challenge, "
                "redirect_uri, requested_scopes, expires_at - created_at AS ttl, consumed_at "
                "FROM oauth_transactions"
            )
        )
    ).one()
    assert row.state_hash == sha256_hex(params["state"])
    assert row.nonce_hash == sha256_hex(params["nonce"])
    verifier = TokenCipher(TEST_CAREEM_ENV["AUTH_ENCRYPTION_KEY"]).decrypt(
        row.pkce_verifier_encrypted
    )
    assert s256(verifier) == params["code_challenge"]
    assert verifier not in row.pkce_verifier_encrypted
    assert row.client_challenge == mobile.client_challenge
    assert row.redirect_uri == TEST_CAREEM_ENV["CAREEM_REDIRECT_URI"]
    assert row.requested_scopes == "openid"
    assert row.ttl == timedelta(seconds=600)
    assert row.consumed_at is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"client_challenge": "too-short"},
        {"client_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", "user_id": "x"},
        {
            "client_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "redirect_uri": "https://evil.example.com/steal",
        },
    ],
)
async def test_start_rejects_invalid_requests(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession, body: dict
) -> None:
    response = await client.post("/api/v1/auth/careem/start", json=body)

    assert response.status_code == 422
    assert error_code(response) == "validation_error"
    assert await count_rows(db_session, "SELECT count(*) FROM oauth_transactions") == 0


async def test_start_is_unavailable_when_careem_login_is_not_configured(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    # A missing client ID means not configured; a missing secret is a valid public client.
    app.dependency_overrides[get_settings] = lambda: Settings(careem_client_id="")

    response = await start_login(client, MobileApp())

    assert response.status_code == 503
    assert error_code(response) == "careem_login_unavailable"


# --- successful login ---


async def test_first_login_creates_a_nutripilot_user_and_careem_identity(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    response = await login(client, careem)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == LOGIN_RESPONSE_KEYS
    assert body["is_new_user"] is True
    assert body["token_type"] == "bearer"
    assert body["id_token"] == careem.issued[-1].id_token
    assert 0 < body["expires_in"] <= 600
    assert "no-store" in response.headers["cache-control"]

    identity = (
        await db_session.execute(
            text("SELECT user_id, provider, issuer, subject FROM auth_identities")
        )
    ).one()
    assert str(identity.user_id) == body["user_id"]
    assert identity.provider == "careem"
    assert identity.issuer == ISSUER
    assert identity.subject == SAMPLE_SUBJECT
    user = (
        await db_session.execute(
            text("SELECT status, deleted_at FROM users WHERE id = :id"), {"id": identity.user_id}
        )
    ).one()
    assert user.status == "active"
    assert user.deleted_at is None


async def test_login_marks_the_transaction_consumed(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    await login(client, careem)

    consumed = await count_rows(
        db_session, "SELECT count(*) FROM oauth_transactions WHERE consumed_at IS NOT NULL"
    )
    assert consumed == 1


async def test_backend_redeems_the_code_with_its_own_pkce_verifier(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    response = await login(client, careem)

    assert response.status_code == 200
    assert len(careem.token_requests) == 1  # the fake rejects a verifier/challenge mismatch


async def test_returning_user_gets_the_same_nutripilot_account(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    first = (await login(client, careem)).json()

    second = await login(client, careem)

    assert second.status_code == 200
    assert second.json()["user_id"] == first["user_id"]
    assert second.json()["is_new_user"] is False
    assert await identities_for(db_session, SAMPLE_SUBJECT) == 1
    assert await count_rows(db_session, "SELECT count(*) FROM users") == 1


async def test_different_careem_users_get_different_accounts(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    first = (await login(client, careem, sub="92305709")).json()
    second = (await login(client, careem, sub="10000001")).json()

    assert first["user_id"] != second["user_id"]


async def test_accounts_are_never_merged_by_matching_email_or_phone(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    shared = {"email": "layla.haddad@example.ae", "phone_number": "+971501234567"}

    first = (await login(client, careem, sub="92305709", claims=shared)).json()
    second = (await login(client, careem, sub="55500011", claims=shared)).json()

    assert first["user_id"] != second["user_id"]
    assert second["is_new_user"] is True
    assert await count_rows(db_session, "SELECT count(*) FROM users") == 2


async def test_non_numeric_subject_is_stored_verbatim(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    subject = "c4a1f9e2-careem-user"

    response = await login(client, careem, sub=subject)

    assert response.status_code == 200
    assert await identities_for(db_session, subject) == 1


async def test_careem_credentials_are_stored_encrypted(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    await login(client, careem)
    issued = careem.issued[-1]

    row = (
        await db_session.execute(
            text(
                "SELECT c.access_token_encrypted, c.refresh_token_encrypted, c.expires_at "
                "FROM careem_credentials c JOIN auth_identities i ON i.id = c.identity_id "
                "WHERE i.subject = :s"
            ),
            {"s": SAMPLE_SUBJECT},
        )
    ).one()
    cipher = TokenCipher(TEST_CAREEM_ENV["AUTH_ENCRYPTION_KEY"])
    assert issued.access_token not in row.access_token_encrypted
    assert issued.refresh_token not in row.refresh_token_encrypted
    assert cipher.decrypt(row.access_token_encrypted) == issued.access_token
    assert cipher.decrypt(row.refresh_token_encrypted) == issued.refresh_token
    assert row.expires_at is not None


async def test_relogin_replaces_stored_careem_credentials(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    await login(client, careem)
    await login(client, careem)

    stored = (
        await db_session.execute(text("SELECT access_token_encrypted FROM careem_credentials"))
    ).scalar_one()
    cipher = TokenCipher(TEST_CAREEM_ENV["AUTH_ENCRYPTION_KEY"])
    assert cipher.decrypt(stored) == careem.issued[-1].access_token


async def test_careem_access_and_refresh_tokens_never_reach_the_app(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    mobile = MobileApp()
    started = await start_login(client, mobile)
    redirect = careem.authorize(started.json()["authorization_url"])

    response = await complete_login(client, redirect, mobile)

    issued = careem.issued[-1]
    exposed = response.text + str(response.headers) + started.text + str(started.headers)
    for secret in (
        issued.access_token,
        issued.refresh_token,
        redirect["code"],
        TEST_CAREEM_ENV["CAREEM_CLIENT_SECRET"],
    ):
        assert secret not in exposed


async def test_public_client_logs_in_without_a_client_secret(
    client: httpx.AsyncClient, public_careem: FakeCareem, db_session: AsyncSession
) -> None:
    response = await login(client, public_careem)

    assert response.status_code == 200, response.text
    assert response.json()["is_new_user"] is True
    token_request = public_careem.token_requests[-1]
    assert "authorization" not in token_request.headers
    assert query_params("?" + token_request.content.decode())["client_id"] == CLIENT_ID
    assert await identities_for(db_session, SAMPLE_SUBJECT) == 1


async def test_public_client_start_is_available_without_a_client_secret(
    client: httpx.AsyncClient, public_careem: FakeCareem
) -> None:
    response = await start_login(client, MobileApp())

    assert response.status_code == 200


# --- state, transaction and app-binding failures ---


async def test_unknown_state_is_rejected_without_contacting_careem(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    mobile = MobileApp()
    started = await start_login(client, mobile)
    redirect = careem.authorize(started.json()["authorization_url"])

    response = await complete_login(client, {**redirect, "state": "forged-state-value"}, mobile)

    assert response.status_code == 400
    assert error_code(response) == "invalid_state"
    assert careem.token_requests == []
    await assert_nobody_logged_in(db_session)


async def test_wrong_client_verifier_is_rejected_and_the_real_app_can_still_finish(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    mobile = MobileApp()
    started = await start_login(client, mobile)
    redirect = careem.authorize(started.json()["authorization_url"])

    intercepted = await complete_login(client, redirect, MobileApp())  # another app's verifier
    legitimate = await complete_login(client, redirect, mobile)

    assert intercepted.status_code == 400
    assert error_code(intercepted) == "invalid_state"
    assert legitimate.status_code == 200


async def test_expired_login_transaction_is_rejected(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    mobile = MobileApp()
    started = await start_login(client, mobile)
    redirect = careem.authorize(started.json()["authorization_url"])
    await db_session.execute(
        text("UPDATE oauth_transactions SET expires_at = now() - interval '1 second'")
    )

    response = await complete_login(client, redirect, mobile)

    assert response.status_code == 400
    assert error_code(response) == "login_expired"
    assert careem.token_requests == []
    await assert_nobody_logged_in(db_session)


async def test_replayed_callback_is_rejected_and_code_redeemed_once(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    mobile = MobileApp()
    started = await start_login(client, mobile)
    redirect = careem.authorize(started.json()["authorization_url"])
    first = await complete_login(client, redirect, mobile)

    replay = await complete_login(client, redirect, mobile)

    assert first.status_code == 200
    assert replay.status_code == 400
    assert error_code(replay) == "invalid_state"
    assert len(careem.token_requests) == 1


async def test_failed_attempt_cannot_be_retried_with_the_same_transaction(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    mobile = MobileApp()
    started = await start_login(client, mobile)
    redirect = careem.authorize(
        started.json()["authorization_url"], token_error=(400, {"error": "invalid_grant"})
    )
    await complete_login(client, redirect, mobile)

    retry = await complete_login(client, redirect, mobile)

    assert retry.status_code == 400
    assert error_code(retry) == "invalid_state"


# --- ID token failures ---


@pytest.mark.parametrize(
    "authorize_kwargs",
    [
        pytest.param({"sign_with": ROGUE_KEY}, id="invalid-signature"),
        pytest.param({"claims": {"iss": "https://evil.example.com/"}}, id="wrong-issuer"),
        pytest.param({"claims": {"aud": "another-careem-client"}}, id="wrong-audience"),
        pytest.param({"claims": {"exp": 1_700_000_600, "iat": 1_700_000_000}}, id="expired"),
        pytest.param({"claims": {"nonce": "attacker-chosen-nonce"}}, id="wrong-nonce"),
        pytest.param({"claims": {"nonce": OMIT}}, id="missing-nonce"),
        pytest.param({"claims": {"sub": OMIT}}, id="missing-subject"),
    ],
)
async def test_invalid_id_token_is_rejected_and_nobody_is_logged_in(
    client: httpx.AsyncClient,
    careem: FakeCareem,
    db_session: AsyncSession,
    authorize_kwargs: dict[str, Any],
) -> None:
    response = await login(client, careem, **authorize_kwargs)

    assert response.status_code == 401
    assert error_code(response) == "authentication_failed"
    assert "id_token" not in response.json()
    await assert_nobody_logged_in(db_session)


# --- Careem failures ---


async def test_code_rejected_by_careem_fails_authentication(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    response = await login(client, careem, token_error=(400, {"error": "invalid_grant"}))

    assert response.status_code == 401
    assert error_code(response) == "authentication_failed"
    assert "invalid_grant" not in response.text  # upstream error bodies are not relayed
    await assert_nobody_logged_in(db_session)


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param({"token_error": (503, {"error": "temporarily_unavailable"})}, id="5xx"),
        pytest.param({"transport_error": httpx.ConnectError("refused")}, id="unreachable"),
        pytest.param({"transport_error": httpx.ReadTimeout("slow")}, id="timeout"),
    ],
)
async def test_careem_outage_is_reported_as_bad_gateway(
    client: httpx.AsyncClient,
    careem: FakeCareem,
    db_session: AsyncSession,
    failure: dict[str, Any],
) -> None:
    response = await login(client, careem, **failure)

    assert response.status_code == 502
    assert error_code(response) == "careem_unavailable"
    await assert_nobody_logged_in(db_session)


# --- account state ---


async def test_disabled_account_cannot_log_in(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    await login(client, careem)
    await db_session.execute(text("UPDATE users SET status = 'disabled'"))

    response = await login(client, careem)

    assert response.status_code == 403
    assert error_code(response) == "account_disabled"
    assert "id_token" not in response.json()


async def test_deleted_account_is_not_resurrected_or_recreated(
    client: httpx.AsyncClient, careem: FakeCareem, db_session: AsyncSession
) -> None:
    await login(client, careem)
    await db_session.execute(text("UPDATE users SET deleted_at = now()"))

    response = await login(client, careem)

    assert response.status_code == 403
    assert error_code(response) == "account_disabled"
    assert await count_rows(db_session, "SELECT count(*) FROM users") == 1


# --- callback input validation ---


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"code": "abc", "state": "def"},
        {"code": "", "state": "def", "client_verifier": "v" * 43},
        {"code": "abc", "state": "def", "client_verifier": "v" * 43, "user_id": "x"},
    ],
)
async def test_callback_rejects_malformed_requests(
    client: httpx.AsyncClient, careem: FakeCareem, body: dict
) -> None:
    response = await client.post("/api/v1/auth/careem/callback", json=body)

    assert response.status_code == 422
    assert careem.token_requests == []


async def test_callback_does_not_accept_get_with_code_in_the_url(
    client: httpx.AsyncClient, careem: FakeCareem
) -> None:
    response = await client.get(
        "/api/v1/auth/careem/callback", params={"code": "abc", "state": "def"}
    )

    assert response.status_code == 405
