import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

SHA256_HEX_LENGTH = 64


class AuthProvider(StrEnum):
    CAREEM = "careem"


class AuthIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Links an external identity, keyed by (provider, issuer, subject), to a NutriPilot user.
    Email or phone never identify a person here."""

    __tablename__ = "auth_identities"
    __table_args__ = (UniqueConstraint("provider", "issuer", "subject"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    provider: Mapped[AuthProvider] = mapped_column(String(32), nullable=False)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    # Opaque provider user ID, stored verbatim as a string (never assumed numeric).
    subject: Mapped[str] = mapped_column(String(255), nullable=False)


class CareemCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Careem tokens for calling Careem APIs/MCP on the user's behalf, encrypted at rest."""

    __tablename__ = "careem_credentials"

    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_identities.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthTransaction(UUIDPrimaryKeyMixin, Base):
    """One login attempt between /careem/start and /careem/callback; single use, short-lived."""

    __tablename__ = "oauth_transactions"

    state_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), unique=True, nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), nullable=False)
    pkce_verifier_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # S256 challenge from the mobile app; binds the callback to the app that started the login.
    client_challenge: Mapped[str] = mapped_column(String(43), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    requested_scopes: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Indexed for the purge of old attempts.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
