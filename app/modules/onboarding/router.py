"""HTTP routes for onboarding. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/profile/onboarding-status", tags=["onboarding"])
