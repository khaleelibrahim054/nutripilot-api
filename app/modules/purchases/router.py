"""HTTP routes for purchases. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/purchases", tags=["purchases"])
