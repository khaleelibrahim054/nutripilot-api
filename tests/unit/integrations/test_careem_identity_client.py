"""Wire client for Careem identity: authorization URL, code exchange, JWKS-backed verification.

Careem is never contacted: httpx.MockTransport plays identity.careem.com.
"""

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.config import Settings
from app.integrations.careem_identity.client import CareemIdentityClient
from app.integrations.careem_identity.exceptions import (
    CareemTokenExchangeError,
    CareemUnavailableError,
    IdTokenValidationError,
)
from tests.conftest import TEST_CAREEM_ENV
from tests.support.careem_tokens import (
    SAMPLE_SUBJECT,
    SigningKey,
    id_token_claims,
    jwks_document,
    sign_id_token,
)

KEY = SigningKey.generate(kid="4390a144-906a-49eb-8ffd-614805c2d124")
TOKEN_URL = "https://identity.careem.com/token"
JWKS_URL = "https://identity.careem.com/jwk"
Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler) -> CareemIdentityClient:
    return CareemIdentityClient.from_settings(Settings(), transport=httpx.MockTransport(handler))


def token_response(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "access_token": "careem-access-token-1",
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "careem-refresh-token-1",
        "id_token": sign_id_token(KEY, id_token_claims(datetime.now(UTC))),
        "scope": "openid",
    }
    body.update(overrides)
    return {key: value for key, value in body.items() if value is not None}


def test_authorization_url_carries_every_oidc_and_pkce_parameter() -> None:
    client = make_client(lambda _: httpx.Response(500))

    url = client.build_authorization_url(state="st-1", nonce="nn-1", code_challenge="ch-1")

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://identity.careem.com/authorize"
    assert parse_qs(parts.query) == {
        "response_type": ["code"],
        "client_id": [TEST_CAREEM_ENV["CAREEM_CLIENT_ID"]],
        "redirect_uri": [TEST_CAREEM_ENV["CAREEM_REDIRECT_URI"]],
        "scope": ["openid"],
        "state": ["st-1"],
        "nonce": ["nn-1"],
        "code_challenge": ["ch-1"],
        "code_challenge_method": ["S256"],
    }
    assert TEST_CAREEM_ENV["CAREEM_CLIENT_SECRET"] not in url


async def test_code_exchange_posts_pkce_form_with_client_secret_basic() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=token_response())

    client = make_client(handler)

    tokens = await client.exchange_code(code="auth-code-1", code_verifier="verifier-1")

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == TOKEN_URL
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    credentials = f"{TEST_CAREEM_ENV['CAREEM_CLIENT_ID']}:{TEST_CAREEM_ENV['CAREEM_CLIENT_SECRET']}"
    assert request.headers["authorization"] == (
        "Basic " + base64.b64encode(credentials.encode()).decode()
    )
    assert parse_qs(request.content.decode()) == {
        "grant_type": ["authorization_code"],
        "code": ["auth-code-1"],
        "redirect_uri": [TEST_CAREEM_ENV["CAREEM_REDIRECT_URI"]],
        "code_verifier": ["verifier-1"],
    }
    assert tokens.access_token.get_secret_value() == "careem-access-token-1"
    assert tokens.refresh_token is not None
    assert tokens.refresh_token.get_secret_value() == "careem-refresh-token-1"
    assert tokens.expires_in == 3600
    assert tokens.scope == "openid"


async def test_public_client_sends_client_id_in_form_without_basic_auth() -> None:
    """Careem MCP clients are public: PKCE replaces the secret (builders.careem.com/docs)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=token_response())

    client = CareemIdentityClient.from_settings(
        Settings(careem_client_secret=""), transport=httpx.MockTransport(handler)
    )

    await client.exchange_code(code="auth-code-1", code_verifier="verifier-1")

    request = seen[0]
    assert "authorization" not in request.headers
    assert parse_qs(request.content.decode()) == {
        "grant_type": ["authorization_code"],
        "code": ["auth-code-1"],
        "redirect_uri": [TEST_CAREEM_ENV["CAREEM_REDIRECT_URI"]],
        "code_verifier": ["verifier-1"],
        "client_id": [TEST_CAREEM_ENV["CAREEM_CLIENT_ID"]],
    }


async def test_confidential_client_never_puts_the_secret_in_the_form() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=token_response())

    client = make_client(handler)

    await client.exchange_code(code="auth-code-1", code_verifier="verifier-1")

    assert TEST_CAREEM_ENV["CAREEM_CLIENT_SECRET"] not in seen[0].content.decode()


async def test_token_values_are_masked_in_repr() -> None:
    client = make_client(lambda _: httpx.Response(200, json=token_response()))

    tokens = await client.exchange_code(code="auth-code-1", code_verifier="verifier-1")

    assert "careem-access-token-1" not in repr(tokens)
    assert "careem-refresh-token-1" not in repr(tokens)


async def test_refresh_token_is_optional() -> None:
    client = make_client(lambda _: httpx.Response(200, json=token_response(refresh_token=None)))

    tokens = await client.exchange_code(code="auth-code-1", code_verifier="verifier-1")

    assert tokens.refresh_token is None


@pytest.mark.parametrize("status_code", [400, 401, 403])
async def test_rejected_code_exchange_raises_exchange_error(status_code: int) -> None:
    client = make_client(
        lambda _: httpx.Response(status_code, json={"error": "invalid_grant"}),
    )

    with pytest.raises(CareemTokenExchangeError):
        await client.exchange_code(code="used-code", code_verifier="verifier-1")


@pytest.mark.parametrize(
    "body",
    [
        b"<html>not json</html>",
        json.dumps({"token_type": "Bearer", "access_token": "a"}).encode(),  # no id_token
        json.dumps({"token_type": "Bearer", "id_token": "x"}).encode(),  # no access_token
        json.dumps({**token_response(), "token_type": "mac"}).encode(),
        json.dumps(["unexpected"]).encode(),
    ],
)
async def test_malformed_token_response_raises_exchange_error(body: bytes) -> None:
    client = make_client(lambda _: httpx.Response(200, content=body))

    with pytest.raises(CareemTokenExchangeError):
        await client.exchange_code(code="auth-code-1", code_verifier="verifier-1")


@pytest.mark.parametrize("status_code", [500, 502, 503])
async def test_careem_server_errors_raise_unavailable(status_code: int) -> None:
    client = make_client(lambda _: httpx.Response(status_code))

    with pytest.raises(CareemUnavailableError):
        await client.exchange_code(code="auth-code-1", code_verifier="verifier-1")


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
async def test_network_failures_raise_unavailable(error: Exception) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise error

    client = make_client(handler)

    with pytest.raises(CareemUnavailableError):
        await client.exchange_code(code="auth-code-1", code_verifier="verifier-1")


async def test_id_token_is_verified_against_careem_jwks() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json=jwks_document(KEY))

    client = make_client(handler)
    now = datetime.now(UTC)
    raw = sign_id_token(KEY, id_token_claims(now))

    identity = await client.verify_id_token(raw, expected_nonce_hash=None, now=now)

    assert identity.subject == SAMPLE_SUBJECT
    assert requested == [JWKS_URL]


async def test_id_token_signed_by_unknown_key_is_rejected() -> None:
    client = make_client(lambda _: httpx.Response(200, json=jwks_document(KEY)))
    now = datetime.now(UTC)
    raw = sign_id_token(SigningKey.generate(kid=KEY.kid), id_token_claims(now))

    with pytest.raises(IdTokenValidationError):
        await client.verify_id_token(raw, expected_nonce_hash=None, now=now)


async def test_jwks_outage_on_first_use_raises_unavailable() -> None:
    client = make_client(lambda _: httpx.Response(503))
    now = datetime.now(UTC)
    raw = sign_id_token(KEY, id_token_claims(now))

    with pytest.raises(CareemUnavailableError):
        await client.verify_id_token(raw, expected_nonce_hash=None, now=now)
