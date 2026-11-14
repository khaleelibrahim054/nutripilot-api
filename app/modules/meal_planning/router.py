"""HTTP routes for meal_planning. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/meal-plans", tags=["meal_planning"])
