from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A NutriPilot account. It has no credentials of its own: people sign in through an external
    identity provider (Careem), whose identities are linked to users by the auth module."""

    __tablename__ = "users"

    status: Mapped[UserStatus] = mapped_column(
        String(16), default=UserStatus.ACTIVE, server_default=UserStatus.ACTIVE, nullable=False
    )
    # Soft deletion: a deleted account is kept (for audit/links) but can no longer sign in.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
