"""HTTP routes for nutrition_goals. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/profile/goals", tags=["nutrition_goals"])
