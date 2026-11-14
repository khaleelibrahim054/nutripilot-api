---
name: build-feature
description: Build a new API feature or endpoint test-first - derive real-world test cases from the requirement, write unit + integration tests, lock them, implement clean layered code, and loop until every test passes. Use when asked to add/build/implement an endpoint, resource, or feature.
argument-hint: <feature description>
---

# Build feature (spec → tests → lock → implement → green)

Feature request: $ARGUMENTS

Follow these phases in order. Do not skip phases.

## 1. Specify
- Restate the feature as concrete user stories and acceptance criteria (who does what, what the system must guarantee).
- Identify the owning module (`app/modules/<module>/`) and any other module whose **service** you need. List the endpoints (method, path, request/response schema, status codes), tables (and which module owns them), and authorization rules.
- Scope control: list what is explicitly OUT of scope (later tasks). Build nothing beyond this task.
- If a requirement is genuinely ambiguous in a way that changes behaviour (not a detail with an obvious convention), ask the user before writing tests. Otherwise state your assumptions.

## 2. Design test cases from real-world use
Write a test matrix before any code, covering every category in `.claude/rules/testing.md`:
happy path, validation, business rules/state transitions, security (401, BOLA, mass assignment, no data leaks), edge cases, persistence.
Base cases on how real riders/drivers/admins would use the API — not on how you plan to implement it.

For an independent, implementation-agnostic test suite, delegate this phase to the `test-designer` subagent with the spec from phase 1.

## 3. Write the tests
- `tests/unit/<module>/test_<entity>_service.py` for business rules (fake repository, no DB).
- `tests/integration/<module>/test_<entity>_api.py` for the HTTP contract against real PostgreSQL via the `client` / `db_session` fixtures.
- Include the "caller's ID in the body is ignored/rejected" security case for authenticated writes.
- Put shared factories/fixtures in the relevant `conftest.py`.

## 4. Confirm red, then lock
- Run `uv run pytest -q`. New tests must fail for the *right* reason (missing route/module/behaviour) — not because of a typo or broken fixture. Fix test bugs now; this is the last moment tests may change.
- Lock the spec: `uv run .claude/hooks/test_lock.py lock`. From here tests are read-only.

## 5. Implement inside the module (per `.claude/rules/architecture.md`)
`app/modules/<module>/models.py` (UUID PK + timestamp mixins; import it in `app/models_registry.py`) → migration (`uv run alembic revision --autogenerate -m "<module>: ..."`, review and fix it, `uv run alembic upgrade head`) → `repository.py` → `service.py` → `schemas.py` (Pydantic v2) → `router.py` (mount it in `app/api.py`). Cross-module needs go through the other module's service only. Apply `.claude/rules/api-design.md` and `.claude/rules/security.md`.

## 6. Loop until green
Run `uv run pytest -q`. For each failure: read the assertion, find the root cause in app code, fix it, re-run. Repeat until all pass and `uv run ruff check .` is clean. Never touch the tests. (The Stop hook enforces this too.)

## 7. Self-review
- Delegate a review to the `security-reviewer` subagent for the files you changed; fix real findings (add tests for them first if they reveal untested behaviour — ask the user before unlocking).
- Run `uv run pytest --cov=app --cov-report=term-missing` and report coverage for the new modules.

## 8. Report
Summarise: endpoints added, test matrix (counts per category), coverage, assumptions made, and anything escalated.
