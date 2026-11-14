# Database Conventions

One PostgreSQL database, one SQLAlchemy 2.x (async, asyncpg) configuration in
`app/core/database.py`. Modules never create engines or sessions of their own.

## Sessions and transactions

- Routes receive a session through `SessionDep` (`app/core/dependencies.py`): one `AsyncSession` per
  request, closed when the request ends.
- **Services own the transaction**: one use case = one short transaction, committed (or rolled
  back) in the service. Repositories never commit.
- `expire_on_commit=False`, so returned objects stay readable after commit.
- The engine is disposed on application shutdown (lifespan in `app/main.py`).

## Models

- Every model inherits the shared `Base` and lives in its owning module's `models.py`.
  There is no global models file.
- `app/models_registry.py` **discovers** every `app/modules/**/models.py` automatically, so Alembic
  and the test suite always see the full metadata. Adding a `models.py` is enough.
- Primary key: UUID column named `id` (`UUIDPrimaryKeyMixin`). Never expose sequential IDs.
- Timestamps: `DateTime(timezone=True)`; `created_at` / `updated_at` via `TimestampMixin` (set by the
  database). In Python use `datetime.now(UTC)`.
- Names: plural snake_case tables, unambiguous across the app (`meal_plan_days`, not `days`);
  snake_case columns; FKs `<entity>_id`; booleans `is_` / `has_`.
- Constraint and index names come from the metadata naming convention (`pk_`, `fk_`, `uq_`, `ix_`,
  `ck_`); don't name them by hand.
- Cross-module references are UUID columns without `relationship()`; resolve related data through
  the other module's service. FKs to another module's table are allowed for integrity but the
  data is still only read through that module's service.
- Money: `Numeric` / `Decimal` with an explicit currency column (e.g. `AED`), never float.
- External provider IDs (Careem restaurant, product, order IDs) are stored in their own columns,
  never reused as primary keys.
- Enums: Python `StrEnum` stored as strings, lowercase snake_case values.
- Idempotency keys for retry-prone writes (purchases, orders) get a unique constraint.
- Index every column used for filtering, joining, ordering or uniqueness.

These rules are checked by `tests/unit/test_architecture.py` where possible (UUID `id`,
timezone-aware timestamps, snake_case, no `create_all`).

## Migrations (Alembic)

- The schema is changed **only** by Alembic migrations in `alembic/versions/`. The application
  never creates tables at startup, and integration tests build their database by running
  `alembic upgrade head`.
- `alembic/env.py` reads `DATABASE_URL` from settings and uses `Base.metadata` from
  `app/models_registry.py`.
- Workflow:
  ```bash
  uv run alembic revision --autogenerate -m "meal_planning: add meal_plans"
  # review and edit the generated file: autogenerate misses renames, data migrations,
  # server defaults and enum changes
  uv run alembic upgrade head
  ```
- Message prefix = owning module. Every migration implements `downgrade()`.
- Changes must be safe on live data: add nullable → backfill → set NOT NULL; build indexes on
  large tables with `CREATE INDEX CONCURRENTLY` (in an autocommit block).
- `tests/integration/test_migrations.py` runs `alembic check` and fails when models and migrations
  drift apart.
