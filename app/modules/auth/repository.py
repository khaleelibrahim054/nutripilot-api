"""DB access for the auth module's tables. Never commits; the service owns transactions."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import AuthIdentity, AuthProvider, CareemCredential, OAuthTransaction


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_transaction(
        self,
        *,
        state_hash: str,
        nonce_hash: str,
        pkce_verifier_encrypted: str,
        client_challenge: str,
        redirect_uri: str,
        requested_scopes: str,
        ttl: timedelta,
    ) -> None:
        # Expiry from the database clock, consistent with created_at.
        transaction = OAuthTransaction(
            state_hash=state_hash,
            nonce_hash=nonce_hash,
            pkce_verifier_encrypted=pkce_verifier_encrypted,
            client_challenge=client_challenge,
            redirect_uri=redirect_uri,
            requested_scopes=requested_scopes,
            expires_at=func.now() + ttl,
        )
        self._session.add(transaction)
        await self._session.flush()

    async def get_transaction_by_state_hash(self, state_hash: str) -> OAuthTransaction | None:
        query = (
            select(OAuthTransaction)
            .where(OAuthTransaction.state_hash == state_hash)
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(query)).scalar_one_or_none()

    async def consume_transaction(self, transaction_id: uuid.UUID, now: datetime) -> bool:
        """Atomically mark the transaction used; False if already consumed or expired (by the
        database clock, the same clock that set expires_at)."""
        statement = (
            update(OAuthTransaction)
            .where(
                OAuthTransaction.id == transaction_id,
                OAuthTransaction.consumed_at.is_(None),
                OAuthTransaction.expires_at > func.now(),
            )
            .values(consumed_at=now)
            .returning(OAuthTransaction.id)
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def purge_transactions_expired_before(self, cutoff: timedelta, limit: int) -> None:
        """Delete up to `limit` transactions that expired more than `cutoff` ago."""
        expired = (
            select(OAuthTransaction.id)
            .where(OAuthTransaction.expires_at < func.now() - cutoff)
            .limit(limit)
        )
        await self._session.execute(
            delete(OAuthTransaction).where(OAuthTransaction.id.in_(expired))
        )

    async def get_identity(
        self, provider: AuthProvider, issuer: str, subject: str
    ) -> AuthIdentity | None:
        query = select(AuthIdentity).where(
            AuthIdentity.provider == provider,
            AuthIdentity.issuer == issuer,
            AuthIdentity.subject == subject,
        )
        return (await self._session.execute(query)).scalar_one_or_none()

    async def add_identity(
        self, *, user_id: uuid.UUID, provider: AuthProvider, issuer: str, subject: str
    ) -> AuthIdentity:
        identity = AuthIdentity(user_id=user_id, provider=provider, issuer=issuer, subject=subject)
        self._session.add(identity)
        await self._session.flush()
        return identity

    async def save_careem_credential(
        self,
        *,
        identity_id: uuid.UUID,
        access_token_encrypted: str,
        refresh_token_encrypted: str | None,
        scope: str | None,
        expires_at: datetime | None,
    ) -> None:
        """Insert or replace the identity's Careem tokens."""
        values = {
            "access_token_encrypted": access_token_encrypted,
            "refresh_token_encrypted": refresh_token_encrypted,
            "scope": scope,
            "expires_at": expires_at,
        }
        statement = (
            insert(CareemCredential)
            .values(id=uuid.uuid4(), identity_id=identity_id, **values)
            .on_conflict_do_update(
                index_elements=[CareemCredential.identity_id],
                set_={**values, "updated_at": func.now()},
            )
        )
        await self._session.execute(statement)
