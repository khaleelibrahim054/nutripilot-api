---
paths:
  - "app/**/*.py"
  - "alembic/**/*.py"
  - "tests/**/*.py"
---

# Architecture: modular monolith

## Layout
```
app/
  main.py               app factory (middleware, error handlers, router)
  api.py                mounts each module's router under /api/v1
  models_registry.py    auto-discovers every module's models.py (Alembic + tests need full metadata)
  core/                 SHARED infrastructure only: config, database (engine, Base, mixins, session),
                        dependencies, exceptions, security/auth, logging, middleware, health
  shared/               shared kernel (schemas, enums, utils, interfaces); imports nothing from app
  integrations/         clients for separate applications (ai_mcp_service); never imports modules
  modules/<module>/     one business capability (e.g. riders, drivers, rides, payments)
    __init__.py
    router.py           HTTP layer: routes, status codes, auth deps. No SQL, no business rules.
    schemas.py          Pydantic v2 request/response models - the module's public data contract
    service.py          business rules + the module's PUBLIC interface for other modules
    repository.py       all DB access for this module's tables (private)
    models.py           SQLAlchemy models for tables this module owns (private)
    exceptions.py       module-specific DomainError subclasses (only if needed)
```
Start a module with only the files it needs; don't create empty layers.

## Module boundaries (enforced by `tests/unit/test_architecture.py`)
- A module may import another module's **`service` or `schemas` only**. Never its `repository`, `models`, `router`, or tables.
- Cross-module calls go through the other module's service functions/classes, which accept and return schemas or primitives (IDs), never ORM objects.
- `app/core`, `app/shared` and `app/integrations` must never import from `app/modules` (`tests/unit/test_layering.py`). Only the composition roots (`main.py`, `api.py`, `models_registry.py`) wire modules together.
- No circular dependencies between modules. If two modules need each other, the shared concept belongs in one of them (or in core if it is truly infrastructure).
- Prefer keeping logic inside one module; only cross a boundary when the feature genuinely spans capabilities.

## Database and table ownership
- One PostgreSQL database, one SQLAlchemy `Base`/engine/session config (`app/core/database.py`). Modules never create their own engines or sessions.
- Every table is owned by exactly one module and declared only in that module's `models.py`. Only that module's repository reads or writes it.
- Table names: plural snake_case, unambiguous across the app (e.g. `rides`, `ride_events`, `payment_transactions`).
- Cross-module references are UUID foreign-key columns (`rider_id`). No `relationship()` to another module's model; fetch related data through that module's service.
- Models are auto-discovered: any `app/modules/**/models.py` is imported by `app/models_registry.py`.

## Migrations
- Every schema change ships as an Alembic migration: `uv run alembic revision --autogenerate -m "<module>: <change>"`, then **review and edit** the generated file (autogenerate misses renames, server defaults, data migrations, enum changes).
- Never call `create_all()` in app code or startup. Integration tests build the schema by running migrations, and `test_models_and_migrations_are_in_sync` fails if models drift from migrations.
- Migrations must be reversible (`downgrade` implemented) and safe on live data (add nullable → backfill → set NOT NULL; create indexes concurrently for large tables).

## Consistency conventions (enforced where possible by architecture tests)
- **Primary keys**: UUID column named `id` via `UUIDPrimaryKeyMixin`. Never expose sequential IDs.
- **Timestamps**: timezone-aware UTC (`DateTime(timezone=True)`), `created_at`/`updated_at` via `TimestampMixin`. In Python use `datetime.now(UTC)`, never naive datetimes.
- **Naming**: snake_case tables/columns; FK columns `<entity>_id`; booleans `is_`/`has_`; constraint names come from the metadata naming convention (don't hand-name them).
- **Schemas**: `<Entity>Create`, `<Entity>Update`, `<Entity>Read`, list responses `<Entity>Page`.
- **Services/repositories**: `<Entity>Service`, `<Entity>Repository`; methods named by intent (`request_ride`, `cancel_ride`, `get_by_id`).
- **Routes**: plural kebab-case resource paths (`/ride-requests/{ride_request_id}`).
- **Enums**: Python `StrEnum` stored as strings; values lowercase snake_case.

## Scope control
- Implement only what the current task asks for. Do not build features that belong to later tasks, even if "obviously next".
- No speculative abstractions (generic base repositories, plugin systems, event buses) and no new dependencies unless the task genuinely needs them. Three similar lines beat a premature abstraction.
- If the task needs something from a not-yet-built module, implement the minimum in the owning module's service and say so in your summary.
