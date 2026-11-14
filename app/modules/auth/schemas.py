"""Public data contract of the auth module."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# RFC 7636: S256 challenge = 43 base64url chars; verifier = 43-128 unreserved chars.
S256_CHALLENGE_PATTERN = r"^[A-Za-z0-9_-]{43}$"
PKCE_VERIFIER_PATTERN = r"^[A-Za-z0-9._~-]{43,128}$"
# Authorization codes are opaque; allow any visible ASCII, bounded.
AUTHORIZATION_CODE_PATTERN = r"^[\x21-\x7E]{1,2048}$"
STATE_PATTERN = r"^[A-Za-z0-9._~-]{1,512}$"


class CareemLoginStart(BaseModel):
    """Begin a Careem sign-in. The app generates a random `client_verifier` (43-128 chars),
    keeps it in memory, and sends only its S256 hash here."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [{"client_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"}]
        },
    )

    client_challenge: str = Field(
        pattern=S256_CHALLENGE_PATTERN,
        description=(
            "BASE64URL-NOPAD(SHA256(client_verifier)): 43 characters. Binds the callback to the "
            "app that started the login, so an intercepted deep link is useless to another app."
        ),
    )


class CareemLoginStartRead(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "authorization_url": (
                        "https://identity.careem.com/authorize?response_type=code"
                        "&client_id=17ccbccd-fb56-43b9-8b2e-198c08a0a207"
                        "&redirect_uri=nutripilot%3A%2F%2Fauth%2Fcareem%2Fcallback"
                        "&scope=openid+profile+offline_access&state=Zt0p6v6v3qkq2W8X0h9m1yJt4c"
                        "&nonce=Q2W8X0h9m1yJt4cN5bR7sE2dF8g&code_challenge=Ab3dE6"
                        "&code_challenge_method=S256"
                    ),
                    "expires_in": 600,
                }
            ]
        }
    )

    authorization_url: str = Field(
        description="Open in the system browser / Careem app. Careem then redirects to the app's "
        "registered redirect URI with `code` and `state`."
    )
    expires_in: int = Field(description="Seconds until this login attempt expires (single use).")


class CareemLoginCallback(BaseModel):
    """What the mobile app received on its deep link, plus its own verifier."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "code": "a1b2c3-careem-auth-code",
                    "state": "Zt0p6v6v3qkq2W8X0h9m1yJt4cN5bR7sE2dF8gH1jK4",
                    "client_verifier": "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
                }
            ]
        },
    )

    code: str = Field(
        min_length=1,
        max_length=2048,
        pattern=AUTHORIZATION_CODE_PATTERN,
        description="`code` query parameter from Careem's redirect.",
    )
    state: str = Field(
        min_length=1,
        max_length=512,
        pattern=STATE_PATTERN,
        description="`state` query parameter from Careem's redirect, unchanged.",
    )
    client_verifier: str = Field(
        min_length=43,
        max_length=128,
        pattern=PKCE_VERIFIER_PATTERN,
        description="The secret whose S256 hash was sent as `client_challenge` at start.",
    )


class CareemLoginRead(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id_token": (
                        "eyJhbGciOiJSUzI1NiIsImtpZCI6IjQzOTBhMTQ0In0.eyJzdWIiOiI5MjMwNTcwOSJ9.c2ln"
                    ),
                    "token_type": "bearer",
                    "expires_in": 540,
                    "user_id": "3f2b8c1e-7d4a-4e6b-9a0c-5d1e2f3a4b5c",
                    "is_new_user": True,
                }
            ]
        }
    )

    id_token: str = Field(
        description="Careem ID token. Send it as `Authorization: Bearer <id_token>` on API calls. "
        "It expires about 10 minutes after login; sign in again after that."
    )
    token_type: Literal["bearer"]
    expires_in: int = Field(description="Seconds until the ID token is no longer accepted.")
    user_id: uuid.UUID = Field(description="NutriPilot user ID (never the Careem subject).")
    is_new_user: bool = Field(description="True when this login created the NutriPilot account.")


class ProvisionedUser(BaseModel):
    user_id: uuid.UUID
    identity_id: uuid.UUID
    is_new_user: bool


class AuthenticatedUser(BaseModel):
    """The caller, resolved from a verified bearer token."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "user_id": "3f2b8c1e-7d4a-4e6b-9a0c-5d1e2f3a4b5c",
                    "expires_at": "2026-11-14T07:20:22Z",
                }
            ]
        }
    )

    user_id: uuid.UUID
    expires_at: datetime = Field(description="When the bearer token stops being accepted (UTC).")
