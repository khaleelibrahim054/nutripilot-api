"""HTTP routes for routine. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/profile/routine", tags=["routine"])
