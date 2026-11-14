"""Careem JWKS caching and key rotation (docs/architecture/careem-identity.md §4).

Careem serves its JWKS with `no-store`, so we own the cache policy: TTL cache keyed by kid, one
rate-limited and single-flighted refresh for an unknown kid, stale-if-error on outages.
"""

import asyncio
from typing import Any

import pytest
from app.integrations.careem_identity.exceptions import CareemUnavailableError
from app.integrations.careem_identity.jwks import JwksCache

from tests.support.careem_tokens import SigningKey, jwks_document

OLD_KEY = SigningKey.generate(kid="4390a144-906a-49eb-8ffd-614805c2d124")
NEW_KEY = SigningKey.generate(kid="64ca09df-59d4-4e6f-8674-038e9727c7b3")
WEAK_KEY = SigningKey.generate(kid="weak-1024", key_size=1024)
TTL = 3600
MIN_REFRESH = 60
MAX_STALE = 86_400


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeJwksEndpoint:
    """Stands in for GET https://identity.careem.com/jwk."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.calls = 0
        self.failure: Exception | None = None

    async def __call__(self) -> dict[str, Any]:
        self.calls += 1
        await asyncio.sleep(0)  # let concurrent callers interleave like a real request
        if self.failure is not None:
            raise self.failure
        return self.document


def make_cache(endpoint: FakeJwksEndpoint, clock: FakeClock) -> JwksCache:
    return JwksCache(
        endpoint,
        ttl_seconds=TTL,
        min_refresh_interval_seconds=MIN_REFRESH,
        max_stale_seconds=MAX_STALE,
        clock=clock,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def endpoint() -> FakeJwksEndpoint:
    return FakeJwksEndpoint(jwks_document(OLD_KEY))


async def test_key_is_selected_by_kid(endpoint: FakeJwksEndpoint, clock: FakeClock) -> None:
    endpoint.document = jwks_document(OLD_KEY, NEW_KEY)
    cache = make_cache(endpoint, clock)

    key = await cache.get_key(NEW_KEY.kid)

    assert key is not None
    assert key.key_id == NEW_KEY.kid


async def test_keys_are_cached_within_ttl(endpoint: FakeJwksEndpoint, clock: FakeClock) -> None:
    cache = make_cache(endpoint, clock)

    await cache.get_key(OLD_KEY.kid)
    clock.advance(TTL - 1)
    await cache.get_key(OLD_KEY.kid)

    assert endpoint.calls == 1


async def test_keys_are_refetched_after_ttl(endpoint: FakeJwksEndpoint, clock: FakeClock) -> None:
    cache = make_cache(endpoint, clock)

    await cache.get_key(OLD_KEY.kid)
    clock.advance(TTL + 1)
    await cache.get_key(OLD_KEY.kid)

    assert endpoint.calls == 2


async def test_unknown_kid_triggers_one_refresh_and_finds_a_rotated_key(
    endpoint: FakeJwksEndpoint, clock: FakeClock
) -> None:
    cache = make_cache(endpoint, clock)
    await cache.get_key(OLD_KEY.kid)
    clock.advance(MIN_REFRESH + 1)
    endpoint.document = jwks_document(OLD_KEY, NEW_KEY)  # Careem rotates in a new key

    key = await cache.get_key(NEW_KEY.kid)

    assert key is not None
    assert key.key_id == NEW_KEY.kid
    assert endpoint.calls == 2


async def test_unknown_kid_returns_none_after_refresh(
    endpoint: FakeJwksEndpoint, clock: FakeClock
) -> None:
    cache = make_cache(endpoint, clock)

    assert await cache.get_key("forged-kid") is None


async def test_unknown_kid_refreshes_are_rate_limited(
    endpoint: FakeJwksEndpoint, clock: FakeClock
) -> None:
    cache = make_cache(endpoint, clock)
    await cache.get_key(OLD_KEY.kid)

    for attempt in range(20):
        assert await cache.get_key(f"forged-kid-{attempt}") is None

    assert endpoint.calls == 1


async def test_concurrent_unknown_kid_lookups_share_one_refresh(
    endpoint: FakeJwksEndpoint, clock: FakeClock
) -> None:
    cache = make_cache(endpoint, clock)
    await cache.get_key(OLD_KEY.kid)
    clock.advance(MIN_REFRESH + 1)
    endpoint.document = jwks_document(OLD_KEY, NEW_KEY)

    keys = await asyncio.gather(*(cache.get_key(NEW_KEY.kid) for _ in range(10)))

    assert all(key is not None and key.key_id == NEW_KEY.kid for key in keys)
    assert endpoint.calls == 2


async def test_last_good_keys_are_served_while_careem_is_down(
    endpoint: FakeJwksEndpoint, clock: FakeClock
) -> None:
    cache = make_cache(endpoint, clock)
    await cache.get_key(OLD_KEY.kid)
    endpoint.failure = OSError("connection refused")
    clock.advance(TTL + 1)

    key = await cache.get_key(OLD_KEY.kid)

    assert key is not None
    assert key.key_id == OLD_KEY.kid


async def test_keys_older_than_the_stale_limit_are_not_served(
    endpoint: FakeJwksEndpoint, clock: FakeClock
) -> None:
    cache = make_cache(endpoint, clock)
    await cache.get_key(OLD_KEY.kid)
    endpoint.failure = OSError("connection refused")
    clock.advance(MAX_STALE + 1)

    with pytest.raises(CareemUnavailableError):
        await cache.get_key(OLD_KEY.kid)


async def test_unavailable_when_no_keys_were_ever_loaded(
    endpoint: FakeJwksEndpoint, clock: FakeClock
) -> None:
    endpoint.failure = OSError("connection refused")
    cache = make_cache(endpoint, clock)

    with pytest.raises(CareemUnavailableError):
        await cache.get_key(OLD_KEY.kid)


async def test_unusable_keys_are_ignored(endpoint: FakeJwksEndpoint, clock: FakeClock) -> None:
    endpoint.document = {
        "keys": [
            WEAK_KEY.public_jwk(),  # 1024-bit RSA is too weak
            OLD_KEY.public_jwk(use="enc", kid="encryption-only"),
            {"kty": "EC", "kid": "ec-key", "crv": "P-256", "x": "AA", "y": "AA"},
            {"kty": "RSA", "kid": "broken", "n": "!!", "e": "AQAB"},
            OLD_KEY.public_jwk(kid=None),
            NEW_KEY.public_jwk(),
        ]
    }
    cache = make_cache(endpoint, clock)

    assert await cache.get_key(WEAK_KEY.kid) is None
    assert await cache.get_key("encryption-only") is None
    assert await cache.get_key("ec-key") is None
    assert await cache.get_key("broken") is None
    assert (await cache.get_key(NEW_KEY.kid)) is not None


@pytest.mark.parametrize("document", [{}, {"keys": "nope"}, [], {"keys": [1, "x"]}])
async def test_malformed_jwks_document_yields_no_keys(
    endpoint: FakeJwksEndpoint, clock: FakeClock, document: Any
) -> None:
    endpoint.document = document
    cache = make_cache(endpoint, clock)

    assert await cache.get_key(OLD_KEY.kid) is None
