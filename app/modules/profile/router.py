"""HTTP routes for profile. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/profile", tags=["profile"])
