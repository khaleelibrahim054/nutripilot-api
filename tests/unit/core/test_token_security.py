"""Token primitives used by the OAuth login: random secrets, hashing at rest, PKCE, encryption."""

import base64
import re

import pytest

from app.core.security import (
    DecryptionError,
    TokenCipher,
    generate_token,
    hash_token,
    s256_challenge,
)

URL_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")
TEST_KEY = base64.urlsafe_b64encode(b"TestKeyForNutriPilotTestSuite123").decode()
OTHER_KEY = base64.urlsafe_b64encode(b"AnotherKeyForNutriPilotTesting!!").decode()


def test_generated_tokens_are_url_safe_and_carry_at_least_256_bits() -> None:
    token = generate_token()

    assert URL_SAFE.fullmatch(token)
    assert len(token) >= 43  # 32 random bytes, base64url without padding


def test_generated_tokens_are_unique() -> None:
    assert len({generate_token() for _ in range(1000)}) == 1000


def test_hash_token_is_deterministic_sha256_hex() -> None:
    digest = hash_token("state-value")

    assert digest == hash_token("state-value")
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest != hash_token("state-valuf")


def test_s256_challenge_matches_rfc7636_appendix_b() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

    assert s256_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_cipher_round_trips_and_never_stores_plaintext() -> None:
    cipher = TokenCipher(TEST_KEY)
    secret = "careem-access-token-abc123"

    ciphertext = cipher.encrypt(secret)

    assert secret not in ciphertext
    assert cipher.decrypt(ciphertext) == secret


def test_cipher_uses_fresh_randomness_per_encryption() -> None:
    cipher = TokenCipher(TEST_KEY)

    assert cipher.encrypt("same") != cipher.encrypt("same")


def test_ciphertext_from_another_key_is_rejected() -> None:
    ciphertext = TokenCipher(OTHER_KEY).encrypt("careem-access-token")

    with pytest.raises(DecryptionError):
        TokenCipher(TEST_KEY).decrypt(ciphertext)


def test_tampered_ciphertext_is_rejected() -> None:
    cipher = TokenCipher(TEST_KEY)
    ciphertext = cipher.encrypt("careem-access-token")
    tampered = ciphertext[:-2] + ("A" if ciphertext[-2] != "A" else "B") + ciphertext[-1]

    with pytest.raises(DecryptionError):
        cipher.decrypt(tampered)


@pytest.mark.parametrize("bad_key", ["", "not-a-fernet-key", "c2hvcnQ="])
def test_cipher_refuses_malformed_keys(bad_key: str) -> None:
    with pytest.raises(ValueError, match="encryption key"):
        TokenCipher(bad_key)
