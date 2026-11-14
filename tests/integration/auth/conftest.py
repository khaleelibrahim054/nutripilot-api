"""Careem login fixtures: the app talks to an in-process fake of identity.careem.com."""

from collections.abc import AsyncGenerator

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.integrations.careem_identity.client import (
    CareemIdentityClient,
    get_careem_identity_client,
)
from app.main import app
from tests.support.fake_careem import FakeCareem


@pytest.fixture
def fake_careem() -> FakeCareem:
    return FakeCareem()


@pytest.fixture
async def careem_identity_client(
    fake_careem: FakeCareem,
) -> AsyncGenerator[CareemIdentityClient, None]:
    identity_client = CareemIdentityClient.from_settings(
        get_settings(), transport=httpx.MockTransport(fake_careem.handler)
    )
    yield identity_client
    await identity_client.aclose()


@pytest.fixture
async def careem(
    client: httpx.AsyncClient,
    fake_careem: FakeCareem,
    careem_identity_client: CareemIdentityClient,
) -> FakeCareem:
    """Route the API's Careem calls to the fake. Depends on `client` so its override reset runs
    after this one is installed."""
    app.dependency_overrides[get_careem_identity_client] = lambda: careem_identity_client
    return fake_careem


@pytest.fixture
async def public_careem(
    client: httpx.AsyncClient, fake_careem: FakeCareem
) -> AsyncGenerator[FakeCareem, None]:
    """Like `careem`, but the API is configured as Careem documents MCP integrations: a public
    client with no CAREEM_CLIENT_SECRET (https://builders.careem.com/docs/getting-started)."""
    public_settings = Settings(careem_client_secret="")
    identity_client = CareemIdentityClient.from_settings(
        public_settings, transport=httpx.MockTransport(fake_careem.handler)
    )
    app.dependency_overrides[get_settings] = lambda: public_settings
    app.dependency_overrides[get_careem_identity_client] = lambda: identity_client
    yield fake_careem
    await identity_client.aclose()
