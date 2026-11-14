"""Careem signing-key cache (docs/architecture/careem-identity.md §4).

Careem serves its JWKS with `Cache-Control: no-store`, so this cache defines the policy:
- keys are cached by `kid` for `ttl_seconds`;
- an unknown `kid` (key rotation) triggers one refresh, rate-limited to one per
  `min_refresh_interval_seconds` and single-flighted, so forged `kid`s cannot hammer Careem;
- if Careem is down, the last good key set keeps being served for up to `max_stale_seconds`
  (stale-if-error); after that verification fails closed.

The cache holds only Careem's public keys, never user state.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import jwt

from app.integrations.careem_identity.exceptions import CareemIdentityError, CareemUnavailableError

logger = logging.getLogger(__name__)

MIN_RSA_KEY_BITS = 2048
DEFAULT_MAX_STALE_SECONDS = 86_400

JwksFetcher = Callable[[], Awaitable[Any]]


class JwksCache:
    def __init__(
        self,
        fetch: JwksFetcher,
        *,
        ttl_seconds: float,
        min_refresh_interval_seconds: float,
        max_stale_seconds: float = DEFAULT_MAX_STALE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._min_refresh_interval = min_refresh_interval_seconds
        self._max_stale = max_stale_seconds
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] | None = None
        self._fetched_at = 0.0
        self._last_attempt: float | None = None
        self._lock = asyncio.Lock()

    async def get_key(self, kid: str) -> jwt.PyJWK | None:
        """Signing key for `kid`, or None if Careem does not publish it.

        Raises CareemUnavailableError when no sufficiently fresh key set can be obtained.
        """
        keys = await self._current_keys()
        key = keys.get(kid)
        if key is None and (self._lock.locked() or self._refresh_allowed()):
            await self._refresh()
            key = (self._keys or {}).get(kid)
        return key

    async def _current_keys(self) -> dict[str, jwt.PyJWK]:
        # Rate limiting applies even before the first successful load, so a failing Careem is not
        # re-fetched on every request during a cold start.
        stale = self._keys is None or self._age() > self._ttl
        if stale and (self._lock.locked() or self._refresh_allowed()):
            await self._refresh()
        if self._keys is None or self._age() > self._max_stale:
            raise CareemUnavailableError("No usable Careem signing keys")
        return self._keys

    def _age(self) -> float:
        return self._clock() - self._fetched_at

    def _refresh_allowed(self) -> bool:
        if self._last_attempt is None:
            return True
        return self._clock() - self._last_attempt >= self._min_refresh_interval

    async def _refresh(self) -> None:
        if self._lock.locked():
            async with self._lock:
                return  # single flight: wait for the refresh already in progress
        async with self._lock:
            self._last_attempt = self._clock()
            try:
                document = await self._fetch()
            except (CareemIdentityError, OSError):
                logger.warning("Careem JWKS refresh failed; keeping cached keys", exc_info=True)
                return
            keys = parse_jwks(document)
            if not keys and self._keys:
                # A broken response must not wipe out a known-good key set (stale-if-error).
                logger.warning("Careem JWKS had no usable keys; keeping cached keys")
                return
            self._keys = keys
            self._fetched_at = self._last_attempt
            logger.info("Careem JWKS refreshed", extra={"jwks_key_count": len(keys)})


def parse_jwks(document: Any) -> dict[str, jwt.PyJWK]:
    """Usable RSA signing keys by kid. Malformed, non-RSA, encryption and weak keys are skipped."""
    if not isinstance(document, Mapping) or not isinstance(document.get("keys"), list):
        return {}
    keys: dict[str, jwt.PyJWK] = {}
    for entry in document["keys"]:
        key = _signing_key(entry)
        if key is not None:
            keys[entry["kid"]] = key
    return keys


def _signing_key(entry: Any) -> jwt.PyJWK | None:
    if not isinstance(entry, dict):
        return None
    kid = entry.get("kid")
    if not isinstance(kid, str) or not kid:
        return None
    if entry.get("kty") != "RSA" or entry.get("use", "sig") != "sig":
        return None
    try:
        key = jwt.PyJWK(entry)
    except (jwt.PyJWTError, ValueError, TypeError):
        return None
    if key.key.key_size < MIN_RSA_KEY_BITS:
        return None
    return key
