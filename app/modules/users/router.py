"""HTTP routes for users. Endpoints are added with this module's feature task."""

from fastapi import APIRouter

router = APIRouter(prefix="/users", tags=["users"])
