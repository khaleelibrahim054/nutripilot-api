"""Executable architecture rules: module boundaries, shared core, and schema conventions."""

import ast
from pathlib import Path

import pytest
from sqlalchemy import DateTime, Uuid

import app
from app.models_registry import Base

APP_DIR = Path(app.__file__).parent
MODULES_DIR = APP_DIR / "modules"
# The only parts of a module other modules may import.
PUBLIC_MODULE_FILES = {"service", "schemas"}


def imported_names(source: str) -> list[str]:
    """Fully-qualified names imported by the source (``from a.b import c`` -> ``a.b.c``)."""
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def boundary_violations(owner: str | None, source: str) -> list[str]:
    """Imports in a file owned by module ``owner`` (None = shared core) that break boundaries."""
    violations = []
    for name in imported_names(source):
        parts = name.split(".")
        if parts[:2] != ["app", "modules"] or len(parts) < 3:
            continue
        target = parts[2]
        if owner is None:
            violations.append(f"shared code imports module internals: {name}")
        elif target != owner and (len(parts) < 4 or parts[3] not in PUBLIC_MODULE_FILES):
            violations.append(f"module '{owner}' reaches into '{target}' internals: {name}")
    return violations


def owner_of(path: Path) -> str | None:
    rel = path.relative_to(APP_DIR)
    return rel.parts[1] if rel.parts[0] == "modules" and len(rel.parts) > 2 else None


# --- the checker itself must work, otherwise the real checks below are vacuous ---


@pytest.mark.parametrize(
    ("owner", "source", "expected"),
    [
        ("rides", "from app.modules.payments.service import PaymentService", 0),
        ("rides", "from app.modules.payments.schemas import PaymentRead", 0),
        ("rides", "from app.modules.payments.repository import PaymentRepository", 1),
        ("rides", "from app.modules.payments.models import Payment", 1),
        ("rides", "import app.modules.payments.repository", 1),
        ("rides", "from app.modules.payments import repository", 1),
        ("rides", "from app.modules.rides.repository import RideRepository", 0),
        (None, "from app.modules.rides.service import RideService", 1),
        ("rides", "from app.core.db import Base", 0),
    ],
)
def test_boundary_checker(owner: str | None, source: str, expected: int) -> None:
    assert len(boundary_violations(owner, source)) == expected


# --- the real rules ---


def test_modules_only_use_each_others_public_service_or_schemas() -> None:
    violations = []
    for path in APP_DIR.rglob("*.py"):
        owner = owner_of(path)
        rel = path.relative_to(APP_DIR)
        if owner is None and rel.parts[0] == "modules":
            continue  # app/modules/__init__.py
        if owner is None and rel.parts[0] != "core":
            continue  # composition roots (main.py, api.py, models_registry.py) may wire modules
        violations += [f"{rel}: {v}" for v in boundary_violations(owner, path.read_text())]
    assert violations == []


def test_application_never_creates_schema_outside_migrations() -> None:
    offenders = [
        str(p.relative_to(APP_DIR)) for p in APP_DIR.rglob("*.py") if "create_all" in p.read_text()
    ]
    assert offenders == [], "Use Alembic migrations, not metadata.create_all()"


def _is_association_table(table) -> bool:
    return all(col.foreign_keys for col in table.primary_key.columns)


def test_every_table_has_a_uuid_primary_key_named_id() -> None:
    bad = [
        table.name
        for table in Base.metadata.sorted_tables
        if not _is_association_table(table)
        and (
            [c.name for c in table.primary_key.columns] != ["id"]
            or not isinstance(table.c.id.type, Uuid)
        )
    ]
    assert bad == []


def test_every_timestamp_column_is_timezone_aware() -> None:
    naive = [
        f"{table.name}.{col.name}"
        for table in Base.metadata.sorted_tables
        for col in table.columns
        if isinstance(col.type, DateTime) and not col.type.timezone
    ]
    assert naive == []


def test_table_and_column_names_are_snake_case() -> None:
    bad = [
        f"{table.name}.{col.name}"
        for table in Base.metadata.sorted_tables
        for col in table.columns
        if not (table.name.islower() and col.name.islower())
    ]
    assert bad == []
