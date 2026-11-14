---
name: fix-until-green
description: Run the full test suite and lint, then iteratively fix application code (never the tests) until everything passes. Use when tests are failing, after a refactor, or when asked to make the build green.
---

# Fix until green

1. Lock the spec so tests can't drift: `uv run .claude/hooks/test_lock.py lock`
2. Run `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`.
3. If everything passes → go to step 6.
4. For each failure, in order:
   - Read the failing assertion and traceback fully. Identify what real-world behaviour the test demands.
   - Find the **root cause in application code** (`app/`, `alembic/`). Fix the cause, not the symptom — no special-casing test values, no swallowing exceptions, no test-environment branches.
   - Re-run just that test (`uv run pytest path::test_name -q`), then the whole suite to catch regressions.
5. Repeat from step 2. If a failure persists after 3 genuinely different fixes, re-read the test as a requirement and question your model of the system. If you conclude the test itself contradicts the real requirement, write the test name, the requirement, and your evidence to `.claude/state/escalation.md` and stop.
6. Report which tests were failing, the root cause of each, and what you changed. The Stop hook releases the lock once the suite is green.
