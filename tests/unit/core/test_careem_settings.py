"""Careem login settings: verified defaults and fail-closed configuration checks."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_defaults_match_verified_careem_discovery_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "CAREEM_ISSUER",
        "CAREEM_AUTHORIZATION_ENDPOINT",
        "CAREEM_TOKEN_ENDPOINT",
        "CAREEM_JWKS_URI",
        "CAREEM_SCOPES",
        "CAREEM_MCP_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.careem_issuer == "https://identity.careem.com/"
    assert str(settings.careem_authorization_endpoint) == "https://identity.careem.com/authorize"
    assert str(settings.careem_token_endpoint) == "https://identity.careem.com/token"
    assert str(settings.careem_jwks_uri) == "https://identity.careem.com/jwk"
    assert settings.careem_jwt_leeway_seconds == 60
    assert settings.careem_id_token_max_age_seconds == 600
    assert settings.oauth_transaction_ttl_seconds == 600


def test_defaults_match_careem_mcp_builder_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    """https://builders.careem.com/docs/getting-started and /docs/connection (2026-11-14)."""
    monkeypatch.delenv("CAREEM_SCOPES", raising=False)
    monkeypatch.delenv("CAREEM_MCP_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.careem_scopes == ["openid", "profile", "offline_access"]
    assert str(settings.careem_mcp_url) == "https://mcp.careemapis.com/mcp"


def test_test_environment_is_fully_configured() -> None:
    assert Settings().careem_login_configured is True


@pytest.mark.parametrize(
    "missing",
    ["careem_client_id", "careem_redirect_uri", "auth_encryption_key"],
)
def test_login_is_not_configured_without_required_values(missing: str) -> None:
    settings = Settings(**{missing: None if missing == "careem_redirect_uri" else ""})

    assert settings.careem_login_configured is False


def test_public_client_without_secret_is_configured() -> None:
    """Careem issues public clients (PKCE enforced, no client secret)."""
    settings = Settings(careem_client_secret="")

    assert settings.careem_login_configured is True
    assert settings.careem_is_public_client is True


def test_client_with_secret_is_confidential() -> None:
    assert Settings().careem_is_public_client is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("careem_jwt_leeway_seconds", 121),
        ("careem_jwt_leeway_seconds", -1),
        ("careem_id_token_max_age_seconds", 0),
        ("oauth_transaction_ttl_seconds", 0),
        ("oauth_transaction_ttl_seconds", 3601),
        ("careem_jwks_cache_ttl_seconds", 0),
        ("careem_token_endpoint", "not a url"),
        ("careem_jwks_uri", "http://identity.careem.com/jwk"),  # keys must come over HTTPS
        ("careem_token_endpoint", "http://identity.careem.com/token"),
        ("careem_mcp_url", "http://mcp.careemapis.com/mcp"),  # bearer tokens only over HTTPS
        ("careem_mcp_url", "not a url"),
    ],
)
def test_rejects_unsafe_or_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings(**{field: value})


def test_client_secret_and_encryption_key_are_masked() -> None:
    settings = Settings()

    assert "test-careem-client-secret" not in repr(settings)
    assert settings.auth_encryption_key.get_secret_value() not in repr(settings)
