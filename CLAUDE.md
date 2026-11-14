# NutriPilot API

FastAPI + async SQLAlchemy 2 + PostgreSQL, managed with **uv** (Python 3.11).

## Commands
- Run: `uv run fastapi dev app/main.py` (docs at /docs, disabled in production)
- All tests: `uv run pytest` · unit only: `uv run pytest tests/unit` · integration: `uv run pytest tests/integration`
- Coverage: `uv run pytest --cov=app --cov-report=term-missing`
- Lint/format: `uv run ruff check . --fix && uv run ruff format .`
- Migrations: `uv run alembic revision --autogenerate -m "msg"` then `uv run alembic upgrade head`
- Add deps: `uv add <pkg>` / `uv add --dev <pkg>` (never pip)

## Architecture: modular monolith
```
app/core/              shared infra: config, database (Base, mixins, session), dependencies, exceptions,
                       logging, middleware, security, health
app/modules/<module>/  one business capability: router.py, schemas.py, service.py, repository.py, models.py
app/api.py             mounts module routers under /api/v1
app/shared/            shared kernel: error schemas, cross-module enums, pure utils, Protocols
app/integrations/      clients for SEPARATE apps (ai_mcp_service: HTTP + SSE); never import modules
app/models_registry.py auto-discovers every app/modules/**/models.py (for Alembic + tests)
alembic/versions/      migrations · docs/architecture/, docs/contracts/ explain the rules
```
- Modules talk to each other only through the other module's `service` / `schemas` — never its repository, models or tables (enforced by `tests/unit/test_architecture.py`).
- One PostgreSQL DB, one SQLAlchemy config; each table owned by exactly one module.
- Schema changes only via Alembic migrations — never `create_all()`.
- UUID primary keys, timezone-aware UTC timestamps, snake_case naming.
- Pydantic v2 schemas validate all input at the API boundary.
- The current user is always derived from the authenticated session, never from the request body.
- **Scope control**: implement only the current task; no features from later tasks, no unnecessary abstractions or dependencies.

Detailed rules live in `.claude/rules/` (architecture, API design, security, testing, code style) and load automatically.

## Definition of done
A task is done only when **ruff is clean and every test passes** — the Stop hook enforces this and
will send you back to fix code if anything is red.

## Non-negotiable testing rule
Tests encode real-world requirements; they are the spec. When a test fails, **fix the application
code**. Never edit, weaken, skip, xfail or delete a test to make it pass, and never special-case
test inputs in app code. When the gate goes red the tests are locked automatically (hook-enforced).
If you are convinced a test contradicts the actual requirement, write the evidence to
`.claude/state/escalation.md` and stop — the user decides.

## Workflows
- New feature/endpoint: `/build-feature <description>` (spec → tests → lock → implement → green)
- Red suite: `/fix-until-green`
- Test lock: `uv run .claude/hooks/test_lock.py status|lock|restore` (unlock is user-only)
