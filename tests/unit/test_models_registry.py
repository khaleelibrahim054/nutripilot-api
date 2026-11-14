"""Alembic sees a module's models as soon as the module has a models.py - no manual registry."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.models_registry import import_module_models

FAKE_PACKAGE = "nutripilot_fake_modules"


@pytest.fixture
def fake_modules_package(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / FAKE_PACKAGE
    for package in ("", "meal_planning", "integrations", "integrations/careem", "onboarding"):
        (root / package).mkdir(parents=True, exist_ok=True)
        (root / package / "__init__.py").write_text("")
    (root / "meal_planning" / "models.py").write_text("LOADED = True\n")
    (root / "integrations" / "careem" / "models.py").write_text("LOADED = True\n")
    (root / "onboarding" / "service.py").write_text("raise RuntimeError('must not import')\n")
    sys.path.insert(0, str(tmp_path))
    yield root
    sys.path.remove(str(tmp_path))
    for name in [m for m in sys.modules if m.startswith(FAKE_PACKAGE)]:
        del sys.modules[name]


@pytest.mark.usefixtures("fake_modules_package")
def test_discovers_models_in_top_level_and_nested_modules_only() -> None:
    found = import_module_models(FAKE_PACKAGE)

    assert found == [
        f"{FAKE_PACKAGE}.integrations.careem.models",
        f"{FAKE_PACKAGE}.meal_planning.models",
    ]
    assert all(sys.modules[name].LOADED for name in found)
