"""HTTP client for identity.careem.com (a separate application).

The client secret, PKCE verifier and Careem tokens stay on this backend. Upstream error bodies are
logged by status/code only and never relayed to callers.
"""

import logging
from datetime import datetime
from typing import Any, Self
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.integrations.careem_identity.exceptions import (
    CareemIdentityError,
    CareemTokenExchangeError,
    CareemUnavailableError,
)
from app.integrations.careem_identity.id_token import IdTokenValidator
from app.integrations.careem_identity.jwks import JwksCache
from app.integrations.careem_identity.schemas import CareemTokens, VerifiedIdentity

logger = logging.getLogger(__name__)

PKCE_METHOD = "S256"
BEARER = "bearer"


class CareemIdentityClient:
    def __init__(self, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http_client
        self._settings = settings
        jwks = JwksCache(
            self._fetch_jwks,
            ttl_seconds=settings.careem_jwks_cache_ttl_seconds,
            min_refresh_interval_seconds=settings.careem_jwks_min_refresh_interval_seconds,
        )
        self._validator = IdTokenValidator(
            jwks=jwks,
            issuer=settings.careem_issuer,
            client_id=settings.careem_client_id,
            leeway_seconds=settings.careem_jwt_leeway_seconds,
            max_age_seconds=settings.careem_id_token_max_age_seconds,
        )

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> Self:
        http_client = httpx.AsyncClient(
            timeout=settings.careem_http_timeout_seconds,
            transport=transport,
            follow_redirects=False,
        )
        return cls(http_client, settings)

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def redirect_uri(self) -> str:
        if self._settings.careem_redirect_uri is None:
            raise CareemIdentityError("CAREEM_REDIRECT_URI is not configured")
        return str(self._settings.careem_redirect_uri)

    @property
    def scope(self) -> str:
        return " ".join(self._settings.careem_scopes)

    def build_authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        """Careem authorization URL for the authorization-code flow with PKCE S256 and a nonce."""
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._settings.careem_client_id,
                "redirect_uri": self.redirect_uri,
                "scope": self.scope,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": PKCE_METHOD,
            }
        )
        return f"{self._settings.careem_authorization_endpoint}?{query}"

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str | None = None
    ) -> CareemTokens:
        """Redeem an authorization code (client_secret_basic + PKCE).

        `redirect_uri` must equal the one in the authorization request; it defaults to the
        configured one. Raises CareemTokenExchangeError if Careem refuses the code or answers with
        an unusable body, and CareemUnavailableError on timeouts, network failures and 5xx.
        """
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri or self.redirect_uri,
            "code_verifier": code_verifier,
        }
        auth: tuple[str, str] | None = None
        if self._settings.careem_is_public_client:
            # Public client (Careem's default for MCP integrations): PKCE replaces the secret.
            form["client_id"] = self._settings.careem_client_id
        else:
            auth = (
                self._settings.careem_client_id,
                self._settings.careem_client_secret.get_secret_value(),
            )
        response = await self._send(
            "POST", str(self._settings.careem_token_endpoint), data=form, auth=auth
        )
        if response.status_code != httpx.codes.OK:
            logger.warning(
                "Careem rejected the authorization code",
                extra={
                    "careem_status": response.status_code,
                    "careem_error": _error_code(response),
                },
            )
            raise CareemTokenExchangeError("Careem rejected the authorization code")
        return _parse_tokens(response)

    async def verify_id_token(
        self, raw: str, *, expected_nonce_hash: str | None, now: datetime
    ) -> VerifiedIdentity:
        """See IdTokenValidator.validate."""
        return await self._validator.validate(raw, expected_nonce_hash=expected_nonce_hash, now=now)

    async def _fetch_jwks(self) -> Any:
        response = await self._send("GET", str(self._settings.careem_jwks_uri))
        if response.status_code != httpx.codes.OK:
            raise CareemUnavailableError(f"Careem JWKS answered {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise CareemUnavailableError("Careem JWKS is not JSON") from exc

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http.request(
                method, url, headers={"Accept": "application/json"}, **kwargs
            )
        except httpx.TimeoutException as exc:
            raise CareemUnavailableError("Careem timed out") from exc
        except httpx.RequestError as exc:  # transport, decoding, redirect failures
            raise CareemUnavailableError("Careem is unreachable") from exc
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise CareemUnavailableError(f"Careem answered {response.status_code}")
        return response


def _parse_tokens(response: httpx.Response) -> CareemTokens:
    try:
        body = response.json()
        tokens = CareemTokens.model_validate(body)
    except (ValueError, ValidationError) as exc:
        raise CareemTokenExchangeError("Unusable Careem token response") from exc
    if tokens.token_type.lower() != BEARER:
        raise CareemTokenExchangeError("Unexpected Careem token type")
    return tokens


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error[:64] if isinstance(error, str) else None


_shared_client: CareemIdentityClient | None = None


async def get_careem_identity_client() -> CareemIdentityClient:
    """Process-wide client, so the JWKS cache and connection pool are shared across requests.

    Async on purpose: FastAPI runs sync dependencies in a threadpool, where concurrent first
    requests could each build (and leak) a client. On the event loop creation is race-free.
    """
    global _shared_client
    if _shared_client is None:
        _shared_client = CareemIdentityClient.from_settings(get_settings())
    return _shared_client


async def close_careem_identity_client() -> None:
    global _shared_client
    if _shared_client is not None:
        await _shared_client.aclose()
        _shared_client = None
