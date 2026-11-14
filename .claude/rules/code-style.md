---
paths:
  - "**/*.py"
---

# Code style

- Full type hints on every function signature (params + return). Prefer `Annotated[..., Depends()]` aliases for FastAPI deps.
- Small, single-purpose functions (aim < 30 lines). Early returns over nested ifs.
- Descriptive names: `ride_id`, `pickup_location`, `cancel_ride()`. No abbreviations like `r`, `tmp`, `data2`.
- No dead code, commented-out code, or speculative abstractions; build what the feature needs.
- Comments explain *why*, not *what*. Docstrings on public services/repositories describing behaviour and raised errors.
- Constants and enums instead of magic strings/numbers (`RideStatus.COMPLETED`, not `"completed"`).
- Timezone-aware datetimes in UTC (`datetime.now(UTC)`); money as `Decimal` or integer minor units, never float.
- Ruff formats and lints automatically after each edit (PostToolUse hook). Fix reported issues properly; `# noqa` only with a specific code and a reason.
