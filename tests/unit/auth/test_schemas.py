"""Input validation for the Careem login API (the mobile app is the only caller)."""

import pytest
from app.modules.auth.schemas import CareemLoginCallback, CareemLoginStart
from pydantic import ValidationError

CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"


def callback(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "code": "a1b2c3-careem-auth-code",
        "state": "Zt0p6v6v3qkq2W8X0h9m1yJt4cN5bR7sE2dF8gH1jK4",
        "client_verifier": VERIFIER,
    }
    body.update(overrides)
    return body


def test_start_accepts_an_s256_client_challenge() -> None:
    assert CareemLoginStart(client_challenge=CHALLENGE).client_challenge == CHALLENGE


@pytest.mark.parametrize(
    "challenge",
    [
        "",
        CHALLENGE[:-1],  # 42 chars
        CHALLENGE + "A",  # 44 chars
        CHALLENGE[:-1] + "=",  # padding is not base64url-nopad
        CHALLENGE[:-1] + "+",  # standard base64 alphabet
        "é" * 43,
    ],
)
def test_start_rejects_malformed_challenges(challenge: str) -> None:
    with pytest.raises(ValidationError):
        CareemLoginStart(client_challenge=challenge)


def test_start_requires_a_challenge() -> None:
    with pytest.raises(ValidationError):
        CareemLoginStart.model_validate({})


@pytest.mark.parametrize("field", ["user_id", "redirect_uri", "scope", "nonce"])
def test_start_rejects_client_supplied_extras(field: str) -> None:
    with pytest.raises(ValidationError):
        CareemLoginStart.model_validate({"client_challenge": CHALLENGE, field: "x"})


def test_callback_accepts_a_realistic_mobile_payload() -> None:
    parsed = CareemLoginCallback.model_validate(callback())

    assert parsed.code == "a1b2c3-careem-auth-code"
    assert parsed.client_verifier == VERIFIER


@pytest.mark.parametrize("missing", ["code", "state", "client_verifier"])
def test_callback_requires_every_field(missing: str) -> None:
    body = callback()
    del body[missing]

    with pytest.raises(ValidationError):
        CareemLoginCallback.model_validate(body)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", ""),
        ("code", "x" * 2049),
        ("code", "has space"),
        ("state", ""),
        ("state", "s" * 513),
        ("state", "bad state!"),
        ("client_verifier", VERIFIER[:42]),  # RFC 7636 minimum is 43
        ("client_verifier", "v" * 129),  # RFC 7636 maximum is 128
        ("client_verifier", VERIFIER[:-1] + "/"),
    ],
)
def test_callback_rejects_out_of_bounds_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        CareemLoginCallback.model_validate(callback(**{field: value}))


def test_callback_accepts_boundary_lengths() -> None:
    parsed = CareemLoginCallback.model_validate(
        callback(code="c" * 2048, state="s" * 512, client_verifier="v" * 128)
    )

    assert len(parsed.code) == 2048


@pytest.mark.parametrize("field", ["user_id", "sub", "id_token", "email"])
def test_callback_rejects_client_supplied_identity_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        CareemLoginCallback.model_validate(callback(**{field: "92305709"}))
