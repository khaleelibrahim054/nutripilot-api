"""HTTP routes for cooking_preferences. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/profile/cooking", tags=["cooking_preferences"])
