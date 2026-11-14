---
paths:
  - "tests/**/*.py"
  - "app/**/*.py"
---

# Testing rules

## The prime directive
Tests describe how the system must behave for real users. A failing test means the **code** is
wrong. Never edit, weaken, skip, xfail, delete or loosen a test (e.g. widening an expected status
from 404 to "4xx", removing an assertion, catching the exception) to make it pass. Never add
test-only branches in app code (`if settings.environment == "test"`). The Stop hook locks tests
when the suite is red; escalate via `.claude/state/escalation.md` if a test is genuinely wrong.

## Every task ships its own tests
Each task adds **unit tests and API integration tests** for the functionality it introduces, in the
same change. No feature is done without both layers.

## What to test — derive cases from real-world use, not from the implementation
For every endpoint/feature cover:
1. **Happy path** with realistic data (real-looking names, Dubai/Riyadh coordinates, AED amounts, UTC timestamps).
2. **Validation**: missing fields, wrong types, boundaries (0, negative, max length, max+1), malformed formats → 422.
3. **Business rules**: invalid state transitions (e.g. cancel a completed ride), duplicates → 409, not found → 404.
4. **Security**: unauthenticated → 401; another user's resource → 403/404 (BOLA); mass-assignment fields ignored/rejected; no sensitive fields in responses; no internal details in error bodies.
5. **Edge cases**: empty lists, pagination limits (limit > max is capped/rejected), unicode/emoji input, concurrency-sensitive operations (double booking) where relevant.
6. **Persistence**: after a write, read it back through the API (or DB) and assert the stored state.

## Two layers, mirrored per module
- `tests/unit/<module>/` — services, schemas, pure logic. No DB/network: fake the module's repository (and other modules' services). Fast (< 50 ms each). Marked `unit` automatically.
- `tests/integration/<module>/` — full HTTP stack via the `client` fixture against the real `careem_test` PostgreSQL DB, whose schema is built by running Alembic migrations; each test runs in a rolled-back transaction. Marked `integration` automatically. Use the `db_session` fixture to seed/verify data.
- Give each test folder an `__init__.py` (e.g. `tests/unit/rides/__init__.py`) so same-named test files in different modules don't collide.
- Security case for every authenticated write: sending another user's ID in the body must not change who the action is performed as.
- Architecture rules (module boundaries, UUID PKs, UTC timestamps, no `create_all`, migrations in sync) are checked by `tests/unit/test_architecture.py` and `tests/integration/test_migrations.py` — fix the code if they fail.

## Style
- Name tests as behaviour: `test_rider_cannot_cancel_completed_ride`.
- Arrange / Act / Assert, one behaviour per test; use `pytest.mark.parametrize` for input matrices.
- Assert exact status codes and the meaningful parts of the body, not just "truthy".
- Use factory helpers/fixtures (in conftest) for test data instead of copy-pasted dicts.
- Tests must be deterministic: no reliance on wall-clock time, ordering of unordered results, or external services.
