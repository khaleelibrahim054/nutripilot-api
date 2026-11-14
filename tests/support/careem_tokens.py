"""Mint Careem-style ID tokens and JWKS documents with locally generated RSA keys.

Mirrors what https://identity.careem.com publishes (RS256, `kid` in the JOSE header, JWKS keys
with kty=RSA, use=sig and no `alg`), see docs/architecture/careem-identity.md.
"""

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.conftest import TEST_CAREEM_ENV

ISSUER = TEST_CAREEM_ENV["CAREEM_ISSUER"]
CLIENT_ID = TEST_CAREEM_ENV["CAREEM_CLIENT_ID"]
ID_TOKEN_LIFETIME = timedelta(seconds=600)
# Real Careem subject from the integration test login; numeric-looking but must stay a string.
SAMPLE_SUBJECT = "92305709"

OMIT = object()
"""Sentinel: pass as a claim value to drop that claim."""


@dataclass
class SigningKey:
    kid: str
    private_key: rsa.RSAPrivateKey = field(repr=False)

    @classmethod
    def generate(cls, kid: str | None = None, key_size: int = 2048) -> "SigningKey":
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
        return cls(kid=kid or str(uuid.uuid4()), private_key=private_key)

    @property
    def private_pem(self) -> bytes:
        return self.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    @property
    def public_pem(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    def public_jwk(self, **overrides: Any) -> dict[str, Any]:
        """Public JWK in Careem's shape: kty, e, use, kid, n (no alg)."""
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key()))
        jwk = {"kty": "RSA", "e": jwk["e"], "use": "sig", "kid": self.kid, "n": jwk["n"]}
        jwk.update(overrides)
        return jwk


def jwks_document(*keys: SigningKey) -> dict[str, Any]:
    return {"keys": [key.public_jwk() for key in keys]}


def id_token_claims(now: datetime, **overrides: Any) -> dict[str, Any]:
    """Claims shaped like the real Careem ID token. Pass `name=OMIT` to drop a claim."""
    issued_at = int(now.timestamp()) - 60
    claims: dict[str, Any] = {
        "sub": SAMPLE_SUBJECT,
        "aud": CLIENT_ID,
        "iss": ISSUER,
        "iat": issued_at,
        "exp": issued_at + int(ID_TOKEN_LIFETIME.total_seconds()),
        "jti": str(uuid.uuid4()),
        "_exp_in_s": 537,
    }
    claims.update(overrides)
    return {name: value for name, value in claims.items() if value is not OMIT}


def sign_id_token(
    key: SigningKey,
    claims: dict[str, Any],
    *,
    kid: str | object | None = OMIT,
    extra_headers: dict[str, Any] | None = None,
) -> str:
    """RS256-sign `claims` with `key`. `kid` defaults to key.kid; pass None to omit it."""
    headers: dict[str, Any] = {}
    header_kid = key.kid if kid is OMIT else kid
    if header_kid is not None:
        headers["kid"] = header_kid
    headers.update(extra_headers or {})
    return jwt.encode(claims, key.private_pem, algorithm="RS256", headers=headers)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _segments(header: dict[str, Any], claims: dict[str, Any]) -> str:
    return f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"


def unsigned_token(claims: dict[str, Any], kid: str) -> str:
    """alg=none token (Careem's discovery advertises `none`; we must reject it)."""
    return _segments({"alg": "none", "typ": "JWT", "kid": kid}, claims) + "."


def hs256_token_with_public_key(key: SigningKey, claims: dict[str, Any]) -> str:
    """Key-confusion attack: HMAC-sign with the RSA public key as the shared secret."""
    signing_input = _segments({"alg": "HS256", "typ": "JWT", "kid": key.kid}, claims)
    signature = hmac.new(key.public_pem, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def s256(verifier: str) -> str:
    """RFC 7636 S256 code challenge, computed independently of the app code."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
