"""HTTP routes for body_metrics. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/profile/body", tags=["body_metrics"])
