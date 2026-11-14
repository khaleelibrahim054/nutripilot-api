"""Public interface of the users module."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserRead


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)

    async def create_user(self) -> UserRead:
        """Create an active user inside the caller's transaction (no commit), so the caller can
        create the user and whatever links to it atomically."""
        return UserRead.model_validate(await self._users.add())

    async def get_user(self, user_id: uuid.UUID) -> UserRead | None:
        user = await self._users.get_by_id(user_id)
        return UserRead.model_validate(user) if user is not None else None
