"""Environment-based settings. Every runtime value is read here; app code never reads os.environ."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AnyUrl, Field, HttpUrl, SecretStr, UrlConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

# Careem endpoints carry secrets and signing keys, so they must be HTTPS.
HttpsUrl = Annotated[HttpUrl, UrlConstraints(allowed_schemes=["https"])]

# Verified against https://identity.careem.com/.well-known/openid-configuration on 2026-11-14
# (docs/architecture/careem-identity.md). Endpoints are pinned, not discovered at runtime.
CAREEM_IDENTITY_BASE = "https://identity.careem.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "NutriPilot API"
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str = "postgresql+asyncpg://localhost:5432/careem"
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=20, ge=0)

    cors_origins: list[str] = []

    # The AI/MCP service is a separate application reached over HTTP + SSE.
    ai_service_url: HttpUrl = HttpUrl("http://localhost:8100")
    ai_service_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    ai_service_stream_idle_timeout_seconds: float = Field(default=60.0, gt=0, le=600)

    # Careem login (OIDC authorization code + PKCE). See docs/architecture/careem-identity.md.
    careem_client_id: str = ""
    careem_client_secret: SecretStr = SecretStr("")
    careem_issuer: str = f"{CAREEM_IDENTITY_BASE}/"
    careem_authorization_endpoint: HttpsUrl = HttpUrl(f"{CAREEM_IDENTITY_BASE}/authorize")
    careem_token_endpoint: HttpsUrl = HttpUrl(f"{CAREEM_IDENTITY_BASE}/token")
    careem_jwks_uri: HttpsUrl = HttpUrl(f"{CAREEM_IDENTITY_BASE}/jwk")
    # The mobile app's deep link registered with Careem (custom scheme or universal link).
    careem_redirect_uri: AnyUrl | None = None
    # offline_access yields a refresh token for long-lived Careem MCP access.
    careem_scopes: list[str] = ["openid", "profile", "offline_access"]
    # Careem MCP resource server; called with the user's Careem access token (never the ID token).
    # Docs: https://builders.careem.com/docs/connection. Sandbox: https://mcp.sandbox.careemapis.com/mcp
    careem_mcp_url: HttpsUrl = HttpUrl("https://mcp.careemapis.com/mcp")
    careem_http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    careem_jwt_leeway_seconds: int = Field(default=60, ge=0, le=120)
    careem_id_token_max_age_seconds: int = Field(default=600, gt=0, le=3600)
    careem_jwks_cache_ttl_seconds: int = Field(default=3600, gt=0, le=86_400)
    careem_jwks_min_refresh_interval_seconds: int = Field(default=60, ge=1, le=3600)
    oauth_transaction_ttl_seconds: int = Field(default=600, gt=0, le=3600)
    # Fernet key (urlsafe base64, 32 bytes) encrypting PKCE verifiers and Careem tokens at rest.
    auth_encryption_key: SecretStr = SecretStr("")

    @property
    def docs_enabled(self) -> bool:
        return self.environment != "production"

    @property
    def careem_login_configured(self) -> bool:
        # The client secret is optional: Careem issues public clients secured by PKCE.
        return bool(
            self.careem_client_id
            and self.careem_redirect_uri
            and self.auth_encryption_key.get_secret_value()
        )

    @property
    def careem_is_public_client(self) -> bool:
        """No secret configured: authenticate to the token endpoint with client_id + PKCE only."""
        return not self.careem_client_secret.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
