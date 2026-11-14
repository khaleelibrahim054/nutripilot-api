"""HTTP routes for dietary_restrictions. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/profile/restrictions", tags=["dietary_restrictions"])
