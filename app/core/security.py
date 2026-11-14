"""Security primitives shared by every module: HTTP security headers and token handling.

NutriPilot has no passwords: users sign in with Careem (app/modules/auth). The helpers here
generate unguessable one-time values, hash them for storage, derive PKCE challenges and encrypt
provider credentials at rest.
"""

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TOKEN_BYTES = 32  # 256 bits of entropy


class DecryptionError(Exception):
    """Ciphertext was produced with another key or has been tampered with."""


def generate_token(nbytes: int = TOKEN_BYTES) -> str:
    """URL-safe random secret (OAuth state, nonce, PKCE verifier)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(value: str) -> str:
    """SHA-256 hex digest. High-entropy tokens are stored only in this form, so a database leak
    does not reveal usable values; compare digests with `hmac.compare_digest`."""
    return hashlib.sha256(value.encode()).hexdigest()


def s256_challenge(verifier: str) -> str:
    """RFC 7636 S256 code challenge: BASE64URL-NOPAD(SHA256(ASCII(verifier)))."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class TokenCipher:
    """Authenticated symmetric encryption (Fernet: AES-128-CBC + HMAC-SHA256) for secrets that
    must be recovered later, such as PKCE verifiers and Careem access tokens."""

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid encryption key: expected urlsafe base64 of 32 bytes") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            _ensure_canonical_base64(ciphertext)
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode()
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise DecryptionError("Ciphertext could not be decrypted") from exc


def _ensure_canonical_base64(value: str) -> None:
    # The decoder ignores unused trailing bits, so differently spelled ciphertexts would decrypt
    # identically; accept only the exact encoding we produced.
    encoded = value.encode("ascii")
    if base64.urlsafe_b64encode(base64.urlsafe_b64decode(encoded)) != encoded:
        raise ValueError("Non-canonical base64")


SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware:
    """Adds SECURITY_HEADERS to every HTTP response (pure ASGI, so SSE streams are untouched)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers += [
                    (name.lower().encode(), value.encode())
                    for name, value in SECURITY_HEADERS.items()
                    if name.lower().encode() not in present
                ]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
