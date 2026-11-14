"""HTTP routes for food_preferences. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/profile/preferences", tags=["food_preferences"])
