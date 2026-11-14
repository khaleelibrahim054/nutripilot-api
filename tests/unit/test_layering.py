"""Dependency direction for the layers outside app/modules:

app.modules -> app.integrations -> app.core -> app.shared
(shared depends on nothing in app; core and integrations never depend on business modules)
"""

import ast
from pathlib import Path

import pytest

import app

APP_DIR = Path(app.__file__).parent

FORBIDDEN_IMPORTS = {
    "shared": ("app.core", "app.integrations", "app.modules"),
    "core": ("app.integrations", "app.modules"),
    "integrations": ("app.modules",),
}


def imported_modules(source: str) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


@pytest.mark.parametrize("layer", sorted(FORBIDDEN_IMPORTS))
def test_layer_does_not_depend_on_higher_layers(layer: str) -> None:
    forbidden = FORBIDDEN_IMPORTS[layer]
    violations = [
        f"{path.relative_to(APP_DIR)} imports {name}"
        for path in (APP_DIR / layer).rglob("*.py")
        for name in imported_modules(path.read_text())
        if name.startswith(forbidden)
    ]
    assert violations == []


def test_no_relative_imports_in_app() -> None:
    """Absolute imports keep the boundary checks above (and in test_architecture) complete."""
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in APP_DIR.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ImportFrom) and node.level > 0
    ]
    assert offenders == []
