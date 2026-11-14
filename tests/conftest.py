"""Root test config. Points the app at the test database BEFORE any app module is imported."""

import os
from pathlib import Path

import pytest

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://ibrahimkhaleel@localhost:5432/careem_test"
)
os.environ["ENVIRONMENT"] = "test"

# Careem login configuration for tests. Careem itself is never contacted: tests replace the
# identity client's transport with an in-process fake (tests/integration/auth/conftest.py).
TEST_CAREEM_ENV = {
    "CAREEM_CLIENT_ID": "17ccbccd-fb56-43b9-8b2e-198c08a0a207",
    "CAREEM_CLIENT_SECRET": "test-careem-client-secret",
    "CAREEM_ISSUER": "https://identity.careem.com/",
    "CAREEM_AUTHORIZATION_ENDPOINT": "https://identity.careem.com/authorize",
    "CAREEM_TOKEN_ENDPOINT": "https://identity.careem.com/token",
    "CAREEM_JWKS_URI": "https://identity.careem.com/jwk",
    "CAREEM_REDIRECT_URI": "nutripilot://auth/careem/callback",
    "CAREEM_SCOPES": '["openid"]',
    # Fernet key (urlsafe base64 of 32 bytes); test-only value.
    "AUTH_ENCRYPTION_KEY": "VGVzdEtleUZvck51dHJpUGlsb3RUZXN0U3VpdGUxMjM=",
}
for _name, _value in TEST_CAREEM_ENV.items():
    os.environ[_name] = _value

if not os.environ["DATABASE_URL"].rsplit("/", 1)[-1].endswith("_test"):
    raise RuntimeError("Refusing to run tests against a non-test database (name must end in _test)")

_TESTS_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by folder: tests/unit -> unit, tests/integration -> integration."""
    for item in items:
        rel = Path(item.fspath).relative_to(_TESTS_DIR)
        if rel.parts[0] in {"unit", "integration"}:
            item.add_marker(getattr(pytest.mark, rel.parts[0]))
