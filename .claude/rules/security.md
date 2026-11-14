---
paths:
  - "app/**/*.py"
  - "alembic/**/*.py"
---

# Security rules (OWASP API Top 10 aware)

- **Current user comes from the authenticated session only.** Never accept the acting user's ID (`user_id`, `rider_id`, `driver_id`, `owner_id`, `created_by` …) from the request body, query or headers to identify who is calling. Resolve it from the verified token/session via a shared dependency (e.g. `CurrentUserDep` in `app/core/`) and pass it to the service explicitly. Request schemas must not contain a field for the caller's own identity.
- **Auth utilities live in `app/core/`** (token verification, password hashing, current-user dependency) and are reused by every module; modules never implement their own auth.
- **AuthN/AuthZ on every endpoint** except explicitly public ones (health). Enforce authorization at the object level: always scope queries by the current user/tenant (`WHERE owner_id = :current_user`) — never trust an ID from the path/body alone (BOLA/IDOR).
- **Mass assignment**: input schemas list only client-writable fields. Never accept `id`, `owner_id`, `role`, `is_admin`, `status` transitions, prices, etc. from clients unless the endpoint is specifically for that and authorized. Use `model_config = ConfigDict(extra="forbid")` on input schemas.
- **Input validation**: constrain every field (`Field(min_length, max_length, ge, le, pattern)`, `EmailStr`, enums, `conint`). Bound string lengths and list sizes to prevent resource exhaustion.
- **SQL**: only SQLAlchemy expressions or `text()` with bound parameters. Never f-strings/concatenation into SQL. Never pass user input to `order_by`/column names without an allow-list.
- **Secrets**: from environment/settings only; never hard-code, log, or return them. `.env` is git-ignored.
- **Passwords**: hash with argon2 or bcrypt (e.g. `pwdlib[argon2]`); never store or log plaintext. Constant-time comparison for tokens (`hmac.compare_digest`).
- **Tokens/JWT**: short expiry, verify signature + `exp` + `aud`/`iss`, pin the algorithm (never accept `none`).
- **Errors & logging**: never leak stack traces, SQL, file paths or internal IDs to clients (the global handler does this — don't bypass it). Never log passwords, tokens, card numbers or full PII.
- **Responses**: filter through response models so sensitive columns (password_hash, internal flags) can never leak.
- **Rate limiting** on auth and expensive endpoints; enforce pagination limits.
- **CORS**: explicit origin allow-list from settings; never `*` together with credentials.
- **Dangerous functions banned**: `eval`, `exec`, `pickle` on untrusted data, `yaml.load` (use `safe_load`), `subprocess(shell=True)`, `random` for security values (use `secrets`). Ruff's `S` rules flag these — fix, don't `noqa`.
- **Dependencies**: add via `uv add`, prefer well-maintained packages, pin via `uv.lock`.
