"""Loads every module's ORM models so Base.metadata is complete for Alembic and tests.

Models are discovered, not listed: any `models.py` under app/modules (including nested packages
such as integrations/careem) is imported. A module that adds models.py is picked up by
`alembic revision --autogenerate` and `alembic check` with no registry edit to forget.
"""

import importlib
import pkgutil

from app.core.database import Base

MODULES_PACKAGE = "app.modules"
MODELS_FILE = "models"


def import_module_models(package_name: str = MODULES_PACKAGE) -> list[str]:
    """Import every `<package>.**.models` submodule; return their dotted names, sorted."""
    package = importlib.import_module(package_name)
    found = sorted(
        info.name
        for info in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}.")
        if info.name.rsplit(".", 1)[-1] == MODELS_FILE
    )
    for name in found:
        importlib.import_module(name)
    return found


import_module_models()

__all__ = ["Base", "import_module_models"]
