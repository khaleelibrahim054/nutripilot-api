"""HTTP routes for Careem sign-in (mobile app) and session checks."""

from fastapi import APIRouter, Response

from app.modules.auth.schemas import (
    AuthenticatedUser,
    CareemLoginCallback,
    CareemLoginRead,
    CareemLoginStart,
    CareemLoginStartRead,
)
from app.modules.auth.service import CareemAuthServiceDep, CurrentUserDep
from app.shared.schemas.errors import ErrorResponse

router = APIRouter(prefix="/auth", tags=["auth"])

# Responses carry one-time URLs and tokens: never cache them anywhere.
NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE_HEADERS)


@router.post(
    "/careem/start",
    summary="Start a Careem sign-in",
    responses={
        503: {
            "model": ErrorResponse,
            "description": "`careem_login_unavailable`: Careem sign-in is not configured.",
        }
    },
)
async def start_careem_login(
    body: CareemLoginStart, service: CareemAuthServiceDep, response: Response
) -> CareemLoginStartRead:
    """Create a single-use login attempt (valid 10 minutes) and return the Careem
    `authorization_url` for the app to open. Public; no token needed.

    Call it again for every new login attempt."""
    _no_store(response)
    return await service.start_login(body.client_challenge)


@router.post(
    "/careem/callback",
    summary="Complete a Careem sign-in",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "`invalid_state` (unknown, already used, or wrong `client_verifier`) "
            "or `login_expired` (start again).",
        },
        401: {
            "model": ErrorResponse,
            "description": "`authentication_failed`: Careem rejected the code, or the ID token "
            "failed verification.",
        },
        403: {"model": ErrorResponse, "description": "`account_disabled`."},
        502: {"model": ErrorResponse, "description": "`careem_unavailable`: retry later."},
        503: {"model": ErrorResponse, "description": "`careem_login_unavailable`."},
    },
)
async def complete_careem_login(
    body: CareemLoginCallback, service: CareemAuthServiceDep, response: Response
) -> CareemLoginRead:
    """Exchange the `code` and `state` from Careem's redirect for the user's session token.

    The backend redeems the code with Careem, verifies the ID token, and finds the NutriPilot
    user, or creates one on first login. Users are identified only by their Careem identity, never
    by email or phone. Each login attempt can be completed once. Public; no token needed."""
    _no_store(response)
    return await service.complete_login(body)


@router.get(
    "/session",
    summary="Get the current session",
    responses={
        401: {
            "model": ErrorResponse,
            "description": "`unauthenticated`: missing, invalid or expired token, or the account "
            "is disabled.",
        }
    },
)
async def get_session(current_user: CurrentUserDep, response: Response) -> AuthenticatedUser:
    """Return the signed-in user for the bearer token, and when that token expires.

    Requires `Authorization: Bearer <id_token>`."""
    _no_store(response)
    return current_user
