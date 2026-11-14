"""HTTP routes for grocery_management. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/grocery-lists", tags=["grocery_management"])
