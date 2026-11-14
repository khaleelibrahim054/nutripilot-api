"""Composition root for HTTP: mounts every module's router under the versioned API prefix."""

from fastapi import APIRouter

from app.core import health
from app.modules.auth.router import router as auth_router
from app.modules.body_metrics.router import router as body_metrics_router
from app.modules.budget.router import router as budget_router
from app.modules.cooking_preferences.router import router as cooking_preferences_router
from app.modules.dietary_restrictions.router import router as dietary_restrictions_router
from app.modules.food_preferences.router import router as food_preferences_router
from app.modules.grocery_management.router import router as grocery_management_router
from app.modules.meal_planning.router import router as meal_planning_router
from app.modules.nutrition_goals.router import router as nutrition_goals_router
from app.modules.onboarding.router import router as onboarding_router
from app.modules.profile.router import router as profile_router
from app.modules.purchases.router import router as purchases_router
from app.modules.routine.router import router as routine_router
from app.modules.users.router import router as users_router

API_PREFIX = "/api/v1"

# Order matters where prefixes nest: the /profile/<section> routers are mounted before the
# aggregate /profile router so a future /profile/{param} route can never shadow them.
# integrations.careem has no router: it is internal and reached only through its service.
MODULE_ROUTERS = (
    auth_router,
    users_router,
    body_metrics_router,
    nutrition_goals_router,
    food_preferences_router,
    dietary_restrictions_router,
    routine_router,
    budget_router,
    cooking_preferences_router,
    onboarding_router,
    profile_router,
    meal_planning_router,
    grocery_management_router,
    purchases_router,
)

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(health.router)
for module_router in MODULE_ROUTERS:
    api_router.include_router(module_router)
