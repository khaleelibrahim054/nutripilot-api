"""Careem ID token validation (docs/architecture/careem-identity.md §3).

A decoded payload is never trusted: every token must carry a valid RS256 signature from a Careem
JWKS key selected by the header `kid`, the exact issuer, our client ID as audience, a valid
lifetime, and (at login) the nonce we sent.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.integrations.careem_identity.exceptions import IdTokenValidationError
from app.integrations.careem_identity.id_token import IdTokenValidator
from app.integrations.careem_identity.jwks import JwksCache
from app.integrations.careem_identity.schemas import VerifiedIdentity

from tests.support.careem_tokens import (
    CLIENT_ID,
    ISSUER,
    OMIT,
    SAMPLE_SUBJECT,
    SigningKey,
    hs256_token_with_public_key,
    id_token_claims,
    jwks_document,
    sha256_hex,
    sign_id_token,
    unsigned_token,
)

NOW = datetime(2026, 11, 14, 7, 12, tzinfo=UTC)
CAREEM_KEY = SigningKey.generate(kid="4390a144-906a-49eb-8ffd-614805c2d124")
ROGUE_KEY = SigningKey.generate(kid="4390a144-906a-49eb-8ffd-614805c2d124")
LEEWAY = 60
MAX_AGE = 600


def make_validator(jwks: dict[str, Any] | None = None) -> IdTokenValidator:
    document = jwks or jwks_document(CAREEM_KEY)

    async def fetch() -> dict[str, Any]:
        return document

    cache = JwksCache(fetch, ttl_seconds=3600, min_refresh_interval_seconds=60)
    return IdTokenValidator(
        jwks=cache,
        issuer=ISSUER,
        client_id=CLIENT_ID,
        leeway_seconds=LEEWAY,
        max_age_seconds=MAX_AGE,
    )


def ts(moment: datetime) -> int:
    return int(moment.timestamp())


def claims(**overrides: Any) -> dict[str, Any]:
    return id_token_claims(NOW, **overrides)


async def validate(token: str, nonce_hash: str | None = None, **kwargs: Any) -> VerifiedIdentity:
    return await make_validator(**kwargs).validate(token, expected_nonce_hash=nonce_hash, now=NOW)


async def assert_rejected(token: str, nonce_hash: str | None = None, **kwargs: Any) -> None:
    with pytest.raises(IdTokenValidationError):
        await validate(token, nonce_hash, **kwargs)


# --- happy path ---


async def test_valid_careem_id_token_yields_issuer_and_subject() -> None:
    token = sign_id_token(CAREEM_KEY, claims())

    identity = await validate(token)

    assert identity.issuer == "https://identity.careem.com/"
    assert identity.subject == SAMPLE_SUBJECT
    assert identity.expires_at == datetime.fromtimestamp(claims()["exp"], UTC)


async def test_numeric_looking_subject_stays_a_string() -> None:
    token = sign_id_token(CAREEM_KEY, claims(sub="000123"))

    identity = await validate(token)

    assert identity.subject == "000123"
    assert isinstance(identity.subject, str)


async def test_audience_array_containing_only_our_client_is_accepted() -> None:
    token = sign_id_token(CAREEM_KEY, claims(aud=[CLIENT_ID]))

    assert (await validate(token)).subject == SAMPLE_SUBJECT


async def test_multiple_audiences_are_accepted_when_azp_is_our_client() -> None:
    token = sign_id_token(CAREEM_KEY, claims(aud=[CLIENT_ID, "other-client"], azp=CLIENT_ID))

    assert (await validate(token)).subject == SAMPLE_SUBJECT


# --- algorithm and key selection ---


async def test_unsigned_alg_none_token_is_rejected() -> None:
    await assert_rejected(unsigned_token(claims(), kid=CAREEM_KEY.kid))


async def test_hmac_token_signed_with_the_public_key_is_rejected() -> None:
    await assert_rejected(hs256_token_with_public_key(CAREEM_KEY, claims()))


async def test_algorithms_outside_the_rs256_allowlist_are_rejected() -> None:
    import jwt

    token = jwt.encode(
        claims(), CAREEM_KEY.private_pem, algorithm="RS512", headers={"kid": CAREEM_KEY.kid}
    )

    await assert_rejected(token)


async def test_token_without_header_kid_is_rejected() -> None:
    await assert_rejected(sign_id_token(CAREEM_KEY, claims(), kid=None))


async def test_kid_in_payload_instead_of_header_is_rejected() -> None:
    token = sign_id_token(CAREEM_KEY, claims(kid=CAREEM_KEY.kid), kid=None)

    await assert_rejected(token)


async def test_token_with_unknown_kid_is_rejected() -> None:
    await assert_rejected(sign_id_token(CAREEM_KEY, claims(), kid="not-a-careem-key"))


async def test_token_signed_by_a_different_key_with_a_careem_kid_is_rejected() -> None:
    await assert_rejected(sign_id_token(ROGUE_KEY, claims()))


async def test_token_with_tampered_payload_is_rejected() -> None:
    header, _, signature = sign_id_token(CAREEM_KEY, claims()).split(".")
    forged_payload = sign_id_token(CAREEM_KEY, claims(sub="1")).split(".")[1]

    await assert_rejected(f"{header}.{forged_payload}.{signature}")


async def test_jwks_key_declaring_another_algorithm_is_not_used() -> None:
    jwks = {"keys": [CAREEM_KEY.public_jwk(alg="RS512")]}

    await assert_rejected(sign_id_token(CAREEM_KEY, claims()), jwks=jwks)


# --- issuer and audience ---


@pytest.mark.parametrize(
    "issuer",
    [
        "https://identity.careem.com",  # missing trailing slash: exact match only
        "https://evil.example.com/",
        "http://identity.careem.com/",
        OMIT,
    ],
)
async def test_wrong_or_missing_issuer_is_rejected(issuer: object) -> None:
    await assert_rejected(sign_id_token(CAREEM_KEY, claims(iss=issuer)))


@pytest.mark.parametrize("audience", ["another-client-id", ["another-client-id"], OMIT, ""])
async def test_token_issued_for_another_client_is_rejected(audience: object) -> None:
    await assert_rejected(sign_id_token(CAREEM_KEY, claims(aud=audience)))


async def test_multiple_audiences_without_azp_are_rejected() -> None:
    await assert_rejected(sign_id_token(CAREEM_KEY, claims(aud=[CLIENT_ID, "other-client"])))


async def test_authorized_party_for_another_client_is_rejected() -> None:
    await assert_rejected(sign_id_token(CAREEM_KEY, claims(azp="other-client")))


# --- lifetime ---


async def test_expired_token_is_rejected() -> None:
    expired = NOW - timedelta(seconds=LEEWAY + 60)
    token = sign_id_token(CAREEM_KEY, claims(exp=ts(expired), iat=ts(expired) - 600))

    await assert_rejected(token)


async def test_token_expired_within_clock_leeway_is_accepted() -> None:
    just_expired = NOW - timedelta(seconds=30)
    token = sign_id_token(CAREEM_KEY, claims(exp=ts(just_expired), iat=ts(just_expired) - 600))

    assert (await validate(token)).subject == SAMPLE_SUBJECT


async def test_token_issued_in_the_future_is_rejected() -> None:
    future = NOW + timedelta(seconds=LEEWAY + 60)
    token = sign_id_token(CAREEM_KEY, claims(iat=ts(future), exp=ts(future) + 600))

    await assert_rejected(token)


async def test_token_older_than_max_age_is_rejected_even_if_exp_is_far_away() -> None:
    issued = NOW - timedelta(hours=1)
    token = sign_id_token(CAREEM_KEY, claims(iat=ts(issued), exp=ts(NOW + timedelta(hours=1))))

    await assert_rejected(token)


async def test_token_whose_expiry_is_not_after_issue_is_rejected() -> None:
    issued = NOW - timedelta(seconds=10)
    token = sign_id_token(CAREEM_KEY, claims(iat=ts(issued), exp=ts(issued)))

    await assert_rejected(token)


async def test_token_not_yet_valid_is_rejected() -> None:
    token = sign_id_token(CAREEM_KEY, claims(nbf=ts(NOW + timedelta(minutes=5))))

    await assert_rejected(token)


@pytest.mark.parametrize("missing", ["exp", "iat"])
async def test_token_without_lifetime_claims_is_rejected(missing: str) -> None:
    await assert_rejected(sign_id_token(CAREEM_KEY, claims(**{missing: OMIT})))


async def test_non_numeric_expiry_is_rejected() -> None:
    await assert_rejected(sign_id_token(CAREEM_KEY, claims(exp="tomorrow")))


async def test_non_standard_exp_in_s_never_extends_an_expired_token() -> None:
    expired = NOW - timedelta(minutes=10)
    token = sign_id_token(
        CAREEM_KEY, claims(exp=ts(expired), iat=ts(expired) - 600, _exp_in_s=100_000)
    )

    await assert_rejected(token)


async def test_non_standard_exp_in_s_never_shortens_a_valid_token() -> None:
    token = sign_id_token(CAREEM_KEY, claims(_exp_in_s=-1))

    assert (await validate(token)).subject == SAMPLE_SUBJECT


# --- nonce ---


async def test_matching_nonce_is_accepted() -> None:
    token = sign_id_token(CAREEM_KEY, claims(nonce="login-nonce-1"))

    identity = await validate(token, nonce_hash=sha256_hex("login-nonce-1"))

    assert identity.subject == SAMPLE_SUBJECT


async def test_mismatched_nonce_is_rejected() -> None:
    token = sign_id_token(CAREEM_KEY, claims(nonce="attacker-nonce"))

    await assert_rejected(token, nonce_hash=sha256_hex("login-nonce-1"))


async def test_missing_nonce_is_rejected_when_one_was_sent() -> None:
    await assert_rejected(sign_id_token(CAREEM_KEY, claims()), nonce_hash=sha256_hex("n"))


async def test_nonce_is_not_checked_when_none_is_expected() -> None:
    token = sign_id_token(CAREEM_KEY, claims(nonce="login-nonce-1"))

    assert (await validate(token, nonce_hash=None)).subject == SAMPLE_SUBJECT


# --- subject ---


@pytest.mark.parametrize("subject", [OMIT, "", 92305709, None, "x" * 256])
async def test_missing_or_malformed_subject_is_rejected(subject: object) -> None:
    await assert_rejected(sign_id_token(CAREEM_KEY, claims(sub=subject)))


# --- malformed input ---


@pytest.mark.parametrize(
    "token",
    ["", "not-a-jwt", "a.b", "a.b.c.d.e", "eyJhbGciOiJSUzI1NiJ9.e30.!!!"],
)
async def test_malformed_tokens_are_rejected(token: str) -> None:
    await assert_rejected(token)


async def test_oversized_token_is_rejected_before_parsing() -> None:
    await assert_rejected(sign_id_token(CAREEM_KEY, claims(padding="x" * 9000)))
