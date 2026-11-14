"""In-process fake of identity.careem.com plus helpers that drive the mobile login flow.

The fake behaves like a real authorization server: authorization codes are single-use, the token
endpoint requires client_secret_basic and a PKCE verifier matching the challenge sent in the
authorization URL, and ID tokens echo the nonce from the authorization request.
"""

import base64
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from tests.conftest import TEST_CAREEM_ENV
from tests.support.careem_tokens import (
    OMIT,
    SAMPLE_SUBJECT,
    SigningKey,
    id_token_claims,
    jwks_document,
    s256,
    sign_id_token,
)

CALLBACK_PATH = "/api/v1/auth/careem/callback"
START_PATH = "/api/v1/auth/careem/start"
SESSION_PATH = "/api/v1/auth/session"

CAREEM_KEY = SigningKey.generate(kid="4390a144-906a-49eb-8ffd-614805c2d124")
ROGUE_KEY = SigningKey.generate(kid=CAREEM_KEY.kid)


@dataclass
class PendingAuthorization:
    nonce: str | None
    code_challenge: str
    claims: dict[str, Any]
    sign_with: SigningKey
    token_error: tuple[int, dict[str, Any]] | None
    transport_error: Exception | None


@dataclass
class IssuedTokens:
    access_token: str
    refresh_token: str
    id_token: str


@dataclass
class FakeCareem:
    key: SigningKey = field(default_factory=lambda: CAREEM_KEY)
    token_requests: list[httpx.Request] = field(default_factory=list)
    issued: list[IssuedTokens] = field(default_factory=list)
    _pending: dict[str, PendingAuthorization] = field(default_factory=dict)

    # --- the "Careem login page" ---

    def authorize(
        self,
        authorization_url: str,
        *,
        sub: str = SAMPLE_SUBJECT,
        claims: dict[str, Any] | None = None,
        sign_with: SigningKey | None = None,
        token_error: tuple[int, dict[str, Any]] | None = None,
        transport_error: Exception | None = None,
    ) -> dict[str, str]:
        """The user signs in at Careem; returns the `code`/`state` Careem redirects back with."""
        params = {k: v[0] for k, v in parse_qs(urlsplit(authorization_url).query).items()}
        assert params["client_id"] == TEST_CAREEM_ENV["CAREEM_CLIENT_ID"]
        assert params["redirect_uri"] == TEST_CAREEM_ENV["CAREEM_REDIRECT_URI"]
        assert params["response_type"] == "code"
        assert params["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(24)
        self._pending[code] = PendingAuthorization(
            nonce=params.get("nonce"),
            code_challenge=params["code_challenge"],
            claims={"sub": sub, **(claims or {})},
            sign_with=sign_with or self.key,
            token_error=token_error,
            transport_error=transport_error,
        )
        return {"code": code, "state": params["state"]}

    # --- HTTP endpoints ---

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and url == TEST_CAREEM_ENV["CAREEM_JWKS_URI"]:
            return httpx.Response(200, json=jwks_document(self.key))
        if request.method == "POST" and url == TEST_CAREEM_ENV["CAREEM_TOKEN_ENDPOINT"]:
            return self._token(request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.token_requests.append(request)
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if not self._client_authenticated(request, form):
            return httpx.Response(401, json={"error": "invalid_client"})
        pending = self._pending.pop(form.get("code", ""), None)  # codes are single-use
        if pending is None or form.get("grant_type") != "authorization_code":
            return httpx.Response(400, json={"error": "invalid_grant"})
        if pending.transport_error is not None:
            raise pending.transport_error
        if pending.token_error is not None:
            return httpx.Response(pending.token_error[0], json=pending.token_error[1])
        if s256(form.get("code_verifier", "")) != pending.code_challenge:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if form.get("redirect_uri") != TEST_CAREEM_ENV["CAREEM_REDIRECT_URI"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json=self._issue(pending))

    def _issue(self, pending: PendingAuthorization) -> dict[str, Any]:
        overrides = dict(pending.claims)
        if pending.nonce is not None:
            overrides.setdefault("nonce", pending.nonce)
        claims = id_token_claims(datetime.now(UTC), **overrides)
        claims = {k: v for k, v in claims.items() if v is not OMIT}
        tokens = IssuedTokens(
            access_token=f"careem-at-{secrets.token_urlsafe(16)}",
            refresh_token=f"careem-rt-{secrets.token_urlsafe(16)}",
            id_token=sign_id_token(pending.sign_with, claims),
        )
        self.issued.append(tokens)
        return {
            "access_token": tokens.access_token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": tokens.refresh_token,
            "id_token": tokens.id_token,
            "scope": "openid",
        }

    @staticmethod
    def _client_authenticated(request: httpx.Request, form: dict[str, str]) -> bool:
        """Confidential clients use client_secret_basic; public clients (Careem's default for
        MCP integrations) send only client_id in the form and rely on PKCE."""
        if "authorization" not in request.headers:
            return form.get("client_id") == TEST_CAREEM_ENV["CAREEM_CLIENT_ID"]
        expected = (
            "Basic "
            + base64.b64encode(
                f"{TEST_CAREEM_ENV['CAREEM_CLIENT_ID']}:"
                f"{TEST_CAREEM_ENV['CAREEM_CLIENT_SECRET']}".encode()
            ).decode()
        )
        return request.headers.get("authorization") == expected


@dataclass
class MobileApp:
    """What the NutriPilot mobile app holds during one login attempt."""

    client_verifier: str = field(default_factory=lambda: secrets.token_urlsafe(48))

    @property
    def client_challenge(self) -> str:
        return s256(self.client_verifier)


async def start_login(client: httpx.AsyncClient, app: MobileApp) -> httpx.Response:
    return await client.post(START_PATH, json={"client_challenge": app.client_challenge})


async def complete_login(
    client: httpx.AsyncClient, redirect: dict[str, str], app: MobileApp
) -> httpx.Response:
    body = {**redirect, "client_verifier": app.client_verifier}
    return await client.post(CALLBACK_PATH, json=body)


async def login(
    client: httpx.AsyncClient, careem: FakeCareem, **authorize_kwargs: Any
) -> httpx.Response:
    """Full happy-path flow as the mobile app performs it; returns the callback response."""
    app = MobileApp()
    started = await start_login(client, app)
    assert started.status_code == 200, started.text
    redirect = careem.authorize(started.json()["authorization_url"], **authorize_kwargs)
    return await complete_login(client, redirect, app)
