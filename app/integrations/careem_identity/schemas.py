"""Data returned by the Careem identity client."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class CareemTokens(BaseModel):
    """Token endpoint response. Secrets are masked in repr/logs; unwrap only where needed."""

    model_config = ConfigDict(frozen=True)

    access_token: SecretStr = Field(min_length=1)
    id_token: SecretStr = Field(min_length=1)
    token_type: str
    refresh_token: SecretStr | None = None
    expires_in: int | None = Field(default=None, ge=0)
    scope: str | None = None


class VerifiedIdentity(BaseModel):
    """Claims of an ID token whose signature and claims were fully verified."""

    model_config = ConfigDict(frozen=True)

    issuer: str
    subject: str
    expires_at: datetime
