import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self) -> User:
        user = User()
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        # Always re-read: status/deletion may have changed since this session last loaded it.
        return await self._session.get(User, user_id, populate_existing=True)
