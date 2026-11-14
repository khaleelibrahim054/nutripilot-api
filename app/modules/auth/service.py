"""Careem sign-in for the NutriPilot mobile app, and bearer-token authentication.

Flow (docs/architecture/careem-identity.md):
1. start_login: create a short-lived, single-use login transaction (hashed state and nonce,
   encrypted PKCE verifier, the app's S256 challenge) and return Careem's authorization URL.
2. The app opens Careem and receives `code` + `state` on its deep link.
3. complete_login: check state, the app's verifier and expiry; consume the transaction; exchange
   the code with Careem; verify the ID token; find or create the user by (issuer, subject); store
   Careem's tokens encrypted; return the ID token, which the app reuses as its bearer token.
4. authenticate_bearer: verify that ID token on every request and resolve the user.

Public surface for other modules: CurrentUserDep / AuthenticatedUser.
"""

import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import SessionDep
from app.core.security import (
    DecryptionError,
    TokenCipher,
    generate_token,
    hash_token,
    s256_challenge,
)
from app.integrations.careem_identity import exceptions as careem_errors
from app.integrations.careem_identity.client import (
    CareemIdentityClient,
    get_careem_identity_client,
)
from app.integrations.careem_identity.schemas import CareemTokens, VerifiedIdentity
from app.modules.auth.exceptions import (
    AccountDisabledError,
    AuthenticationFailedError,
    CareemLoginUnavailableError,
    CareemUnavailableError,
    InvalidStateError,
    LoginExpiredError,
    UnauthenticatedError,
)
from app.modules.auth.models import AuthProvider, OAuthTransaction
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import (
    AuthenticatedUser,
    CareemLoginCallback,
    CareemLoginRead,
    CareemLoginStartRead,
    ProvisionedUser,
)
from app.modules.users.service import UserService

logger = logging.getLogger(__name__)

PKCE_VERIFIER_BYTES = 48  # 64 base64url chars, within RFC 7636's 43-128
BEARER_SCHEME = "bearer"
TRANSACTION_RETENTION = timedelta(days=1)  # keep expired attempts briefly for troubleshooting
PURGE_BATCH_SIZE = 100

_LOGIN_FAILED = "Careem sign-in failed"


class CareemAuthService:
    def __init__(
        self, session: AsyncSession, settings: Settings, careem: CareemIdentityClient
    ) -> None:
        self._session = session
        self._settings = settings
        self._careem = careem
        self._auth = AuthRepository(session)
        self._users = UserService(session)

    async def start_login(self, client_challenge: str) -> CareemLoginStartRead:
        """Begin a login attempt. Raises CareemLoginUnavailableError if Careem is not set up."""
        cipher = self._cipher()
        state, nonce = generate_token(), generate_token()
        pkce_verifier = generate_token(PKCE_VERIFIER_BYTES)
        ttl = self._settings.oauth_transaction_ttl_seconds
        await self._auth.add_transaction(
            state_hash=hash_token(state),
            nonce_hash=hash_token(nonce),
            pkce_verifier_encrypted=cipher.encrypt(pkce_verifier),
            client_challenge=client_challenge,
            redirect_uri=self._careem.redirect_uri,
            requested_scopes=self._careem.scope,
            ttl=timedelta(seconds=ttl),
        )
        # /start is public, so keep the table bounded: each call clears a small batch of old rows.
        await self._auth.purge_transactions_expired_before(
            TRANSACTION_RETENTION, limit=PURGE_BATCH_SIZE
        )
        await self._session.commit()
        authorization_url = self._careem.build_authorization_url(
            state=state, nonce=nonce, code_challenge=s256_challenge(pkce_verifier)
        )
        return CareemLoginStartRead(authorization_url=authorization_url, expires_in=ttl)

    async def complete_login(self, callback: CareemLoginCallback) -> CareemLoginRead:
        """Finish a login attempt and return the verified Careem ID token.

        Raises InvalidStateError, LoginExpiredError, AuthenticationFailedError,
        CareemUnavailableError, AccountDisabledError or CareemLoginUnavailableError.
        """
        cipher = self._cipher()
        transaction = await self._claim_transaction(callback)
        tokens = await self._exchange_code(callback.code, cipher, transaction)
        identity = await self._verify_id_token(tokens, transaction.nonce_hash)
        provisioned = await self.get_or_create_user(identity.issuer, identity.subject)
        await self._ensure_active(provisioned)
        await self._store_credentials(provisioned, tokens, cipher)
        await self._session.commit()
        logger.info(
            "Careem login succeeded",
            extra={"user_id": str(provisioned.user_id), "is_new_user": provisioned.is_new_user},
        )
        remaining = identity.expires_at - datetime.now(UTC)
        return CareemLoginRead(
            id_token=tokens.id_token.get_secret_value(),
            token_type=BEARER_SCHEME,
            expires_in=max(0, int(remaining.total_seconds())),
            user_id=provisioned.user_id,
            is_new_user=provisioned.is_new_user,
        )

    async def get_or_create_user(self, issuer: str, subject: str) -> ProvisionedUser:
        """Return the user linked to this Careem identity, creating both on first login.

        Safe under concurrent first logins: the losing request's user insert is rolled back with
        its savepoint and it returns the winner's user. Commits.
        """
        existing = await self._auth.get_identity(AuthProvider.CAREEM, issuer, subject)
        if existing is not None:
            return ProvisionedUser(
                user_id=existing.user_id, identity_id=existing.id, is_new_user=False
            )
        try:
            async with self._session.begin_nested():
                user = await self._users.create_user()
                identity = await self._auth.add_identity(
                    user_id=user.id, provider=AuthProvider.CAREEM, issuer=issuer, subject=subject
                )
        except IntegrityError:
            winner = await self._auth.get_identity(AuthProvider.CAREEM, issuer, subject)
            if winner is None:
                raise
            provisioned = ProvisionedUser(
                user_id=winner.user_id, identity_id=winner.id, is_new_user=False
            )
        else:
            provisioned = ProvisionedUser(
                user_id=user.id, identity_id=identity.id, is_new_user=True
            )
        await self._session.commit()
        return provisioned

    async def authenticate_bearer(self, token: str) -> AuthenticatedUser:
        """Resolve the caller from a Careem ID token. Raises UnauthenticatedError."""
        if not self._settings.careem_login_configured:
            raise UnauthenticatedError("Authentication required")
        try:
            identity = await self._careem.verify_id_token(
                token, expected_nonce_hash=None, now=datetime.now(UTC)
            )
        except careem_errors.IdTokenValidationError as exc:
            logger.info("Bearer token rejected", extra={"reason": str(exc)})
            raise UnauthenticatedError("Authentication required") from exc
        except careem_errors.CareemUnavailableError as exc:
            raise CareemUnavailableError("Careem is temporarily unavailable") from exc
        linked = await self._auth.get_identity(
            AuthProvider.CAREEM, identity.issuer, identity.subject
        )
        user = await self._users.get_user(linked.user_id) if linked is not None else None
        if user is None or not user.is_active:
            raise UnauthenticatedError("Authentication required")
        return AuthenticatedUser(user_id=user.id, expires_at=identity.expires_at)

    # --- steps ---

    def _cipher(self) -> TokenCipher:
        if not self._settings.careem_login_configured:
            raise CareemLoginUnavailableError("Careem sign-in is not available")
        try:
            return TokenCipher(self._settings.auth_encryption_key.get_secret_value())
        except ValueError as exc:
            logger.error("AUTH_ENCRYPTION_KEY is not a valid Fernet key")
            raise CareemLoginUnavailableError("Careem sign-in is not available") from exc

    async def _claim_transaction(self, callback: CareemLoginCallback) -> OAuthTransaction:
        """Validate and atomically consume the login transaction before Careem is contacted,
        so a code can never be redeemed twice. Commits the consumption."""
        transaction = await self._auth.get_transaction_by_state_hash(hash_token(callback.state))
        if transaction is None or transaction.consumed_at is not None:
            raise InvalidStateError("Invalid or already used sign-in attempt")
        now = datetime.now(UTC)
        if now >= transaction.expires_at:
            raise LoginExpiredError("Sign-in attempt expired; please start again")
        # Constant-time: a wrong verifier (e.g. an intercepted deep link replayed by another app)
        # does not consume the transaction, so the legitimate app can still finish.
        verifier_challenge = s256_challenge(callback.client_verifier)
        if not hmac.compare_digest(verifier_challenge, transaction.client_challenge):
            raise InvalidStateError("Invalid or already used sign-in attempt")
        if not await self._auth.consume_transaction(transaction.id, now):
            raise InvalidStateError("Invalid or already used sign-in attempt")
        await self._session.commit()
        return transaction

    async def _exchange_code(
        self, code: str, cipher: TokenCipher, transaction: OAuthTransaction
    ) -> CareemTokens:
        try:
            pkce_verifier = cipher.decrypt(transaction.pkce_verifier_encrypted)
        except DecryptionError as exc:  # e.g. AUTH_ENCRYPTION_KEY rotated mid-login
            logger.error("Stored PKCE verifier could not be decrypted")
            raise CareemLoginUnavailableError("Careem sign-in is not available") from exc
        try:
            return await self._careem.exchange_code(
                code=code, code_verifier=pkce_verifier, redirect_uri=transaction.redirect_uri
            )
        except careem_errors.CareemTokenExchangeError as exc:
            logger.warning("Careem login rejected", extra={"reason": str(exc)})
            raise AuthenticationFailedError(_LOGIN_FAILED) from exc
        except careem_errors.CareemUnavailableError as exc:
            logger.warning("Careem unavailable during login", extra={"reason": str(exc)})
            raise CareemUnavailableError("Careem is temporarily unavailable") from exc

    async def _verify_id_token(self, tokens: CareemTokens, nonce_hash: str) -> VerifiedIdentity:
        try:
            return await self._careem.verify_id_token(
                tokens.id_token.get_secret_value(),
                expected_nonce_hash=nonce_hash,
                now=datetime.now(UTC),
            )
        except careem_errors.IdTokenValidationError as exc:
            logger.warning("Careem login rejected", extra={"reason": str(exc)})
            raise AuthenticationFailedError(_LOGIN_FAILED) from exc
        except careem_errors.CareemUnavailableError as exc:
            raise CareemUnavailableError("Careem is temporarily unavailable") from exc

    async def _ensure_active(self, provisioned: ProvisionedUser) -> None:
        user = await self._users.get_user(provisioned.user_id)
        if user is None or not user.is_active:
            logger.info(
                "Login refused for inactive account", extra={"user_id": str(provisioned.user_id)}
            )
            raise AccountDisabledError("This account is disabled")

    async def _store_credentials(
        self, provisioned: ProvisionedUser, tokens: CareemTokens, cipher: TokenCipher
    ) -> None:
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=tokens.expires_in)
            if tokens.expires_in is not None
            else None
        )
        refresh = tokens.refresh_token
        await self._auth.save_careem_credential(
            identity_id=provisioned.identity_id,
            access_token_encrypted=cipher.encrypt(tokens.access_token.get_secret_value()),
            refresh_token_encrypted=cipher.encrypt(refresh.get_secret_value()) if refresh else None,
            scope=tokens.scope,
            expires_at=expires_at,
        )


def get_careem_auth_service(
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    careem: Annotated[CareemIdentityClient, Depends(get_careem_identity_client)],
) -> CareemAuthService:
    return CareemAuthService(session, settings, careem)


CareemAuthServiceDep = Annotated[CareemAuthService, Depends(get_careem_auth_service)]

_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="CareemIdToken",
    bearerFormat="JWT",
    description="The `id_token` returned by `POST /api/v1/auth/careem/callback`. "
    "Valid for about 10 minutes after login.",
)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    service: CareemAuthServiceDep,
) -> AuthenticatedUser:
    """The authenticated caller. Use `CurrentUserDep` in routes of any module."""
    token = credentials.credentials.strip() if credentials is not None else ""
    if not token or " " in token:
        raise UnauthenticatedError("Authentication required")
    return await service.authenticate_bearer(token)


CurrentUserDep = Annotated[AuthenticatedUser, Depends(get_current_user)]
