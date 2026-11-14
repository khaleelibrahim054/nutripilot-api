"""HTTP routes for budget. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/profile/budget", tags=["budget"])
