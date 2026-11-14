"""Careem ID token verification (docs/architecture/careem-identity.md §3).

Nothing in a token is trusted until its RS256 signature verifies against the Careem JWKS key named
by the header `kid`. The algorithm allowlist is ours: Careem's discovery document also advertises
`none`, which must never be accepted. Time checks use an injected `now` so they are testable.
"""

import hmac
import logging
from datetime import UTC, datetime
from typing import Any

import jwt

from app.core.security import hash_token
from app.integrations.careem_identity.exceptions import IdTokenValidationError
from app.integrations.careem_identity.jwks import JwksCache
from app.integrations.careem_identity.schemas import VerifiedIdentity

logger = logging.getLogger(__name__)

ALLOWED_ALGORITHMS = frozenset({"RS256"})
MAX_TOKEN_BYTES = 8192
MAX_SUBJECT_LENGTH = 255

# Signature only: every claim check is done explicitly below, against the injected clock.
_SIGNATURE_ONLY = {
    "verify_signature": True,
    "verify_exp": False,
    "verify_nbf": False,
    "verify_iat": False,
    "verify_aud": False,
    "verify_iss": False,
    "verify_sub": False,
    "verify_jti": False,
}


def _reject(reason: str) -> IdTokenValidationError:
    return IdTokenValidationError(f"ID token rejected: {reason}")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


class IdTokenValidator:
    def __init__(
        self,
        *,
        jwks: JwksCache,
        issuer: str,
        client_id: str,
        leeway_seconds: int,
        max_age_seconds: int,
    ) -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._client_id = client_id
        self._leeway = leeway_seconds
        self._max_age = max_age_seconds

    async def validate(
        self, raw: str, *, expected_nonce_hash: str | None, now: datetime
    ) -> VerifiedIdentity:
        """Verify `raw` and return its identity.

        `expected_nonce_hash` is the stored hash of the nonce sent in the authorization request
        (login), or None when the nonce is not being checked (bearer reuse after login).
        Raises IdTokenValidationError, or CareemUnavailableError if signing keys are unavailable.
        """
        kid = self._checked_header(raw)
        key = await self._jwks.get_key(kid)
        if key is None:
            # Monitoring signal: a flood of these means forged tokens (or an unseen rotation).
            logger.warning("ID token signed with unknown kid", extra={"jwt_kid": kid[:64]})
            raise _reject("unknown signing key")
        if key.algorithm_name not in ALLOWED_ALGORITHMS:
            raise _reject("signing key is not an RS256 key")
        claims = self._signed_claims(raw, key)
        self._check_issuer_and_audience(claims)
        self._check_lifetime(claims, now.timestamp())
        self._check_nonce(claims, expected_nonce_hash)
        # Report when *we* stop accepting the token: exp, capped by our max-age bound.
        effective_expiry = min(claims["exp"], claims["iat"] + self._max_age)
        return VerifiedIdentity(
            issuer=claims["iss"],
            subject=self._subject(claims),
            expires_at=datetime.fromtimestamp(effective_expiry, UTC),
        )

    @staticmethod
    def _checked_header(raw: str) -> str:
        if not isinstance(raw, str) or len(raw.encode()) > MAX_TOKEN_BYTES:
            raise _reject("missing or oversized")
        if raw.count(".") != 2:
            raise _reject("not a compact JWS")
        try:
            header = jwt.get_unverified_header(raw)
        except jwt.PyJWTError as exc:
            raise _reject("malformed header") from exc
        if header.get("alg") not in ALLOWED_ALGORITHMS:
            raise _reject("algorithm not allowed")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise _reject("missing kid")
        return kid

    @staticmethod
    def _signed_claims(raw: str, key: jwt.PyJWK) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                raw, key.key, algorithms=sorted(ALLOWED_ALGORITHMS), options=_SIGNATURE_ONLY
            )
        except jwt.PyJWTError as exc:
            raise _reject("invalid signature or payload") from exc
        if not isinstance(claims, dict):
            raise _reject("payload is not an object")
        return claims

    def _check_issuer_and_audience(self, claims: dict[str, Any]) -> None:
        if claims.get("iss") != self._issuer:
            raise _reject("wrong issuer")
        audience = claims.get("aud")
        if isinstance(audience, str):
            audiences = [audience]
        elif isinstance(audience, list) and all(isinstance(a, str) for a in audience):
            audiences = audience
        else:
            raise _reject("missing audience")
        if self._client_id not in audiences:
            raise _reject("wrong audience")
        authorized_party = claims.get("azp")
        if len(audiences) > 1 and authorized_party is None:
            raise _reject("multiple audiences without azp")
        if authorized_party is not None and authorized_party != self._client_id:
            raise _reject("wrong authorized party")

    def _check_lifetime(self, claims: dict[str, Any], now: float) -> None:
        expires, issued = claims.get("exp"), claims.get("iat")
        if not _is_number(expires) or not _is_number(issued):
            raise _reject("missing or non-numeric exp/iat")
        if expires <= issued:
            raise _reject("exp not after iat")
        if now >= expires + self._leeway:
            raise _reject("expired")
        if issued > now + self._leeway:
            raise _reject("issued in the future")
        if now - issued > self._max_age + self._leeway:
            raise _reject("too old")
        not_before = claims.get("nbf")
        if not_before is not None and (
            not _is_number(not_before) or now + self._leeway < not_before
        ):
            raise _reject("not yet valid")

    @staticmethod
    def _check_nonce(claims: dict[str, Any], expected_nonce_hash: str | None) -> None:
        if expected_nonce_hash is None:
            return
        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not hmac.compare_digest(
            hash_token(nonce), expected_nonce_hash
        ):
            raise _reject("nonce mismatch")

    @staticmethod
    def _subject(claims: dict[str, Any]) -> str:
        subject = claims.get("sub")
        if not isinstance(subject, str) or not 0 < len(subject) <= MAX_SUBJECT_LENGTH:
            raise _reject("missing or malformed subject")
        return subject
