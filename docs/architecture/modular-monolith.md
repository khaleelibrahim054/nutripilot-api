# NutriPilot API — Modular Monolith

NutriPilot's backend is **one deployable FastAPI application** backed by **one PostgreSQL
database**. It is split into business modules with enforced boundaries, so that it stays easy to
change now and a module could be extracted later if there is ever a real reason to. The profile
modules (profile, body metrics, goals, preferences…) are modules, **not** microservices.

The **AI/MCP service is a separate application.** This API calls it over HTTP and consumes its
Server-Sent Events (SSE) stream through `app/integrations/ai_mcp_service`. No AI orchestration
runs inside this process, and the frontend never calls the AI service or Careem directly.

## Layers and dependency direction

```
app/main.py, app/api.py, app/models_registry.py      composition roots: wire everything
        │
app/modules/<module>/                                business capabilities
        │
app/integrations/<client>/                           clients for separate applications
        │
app/core/                                            infrastructure: config, database, logging,
        │                                            middleware, security, exceptions, deps
app/shared/                                          shared kernel: schemas, enums, utils, protocols
```

A layer may import only layers below it. Enforced by `tests/unit/test_layering.py`
(core/shared/integrations never import modules; shared imports nothing from `app`) and
`tests/unit/test_architecture.py` (module-to-module rules). Relative imports are banned in `app/`
so these checks see every import.

| Path | Contains | Must not contain |
|---|---|---|
| `app/core/` | settings, engine/session/`Base`, error handling, logging, middleware, security, health | business rules, anything module-specific |
| `app/shared/` | the error envelope schema; later: cross-module enums, pure helpers, `Protocol`s | I/O, DB access, settings, anything only one module uses |
| `app/integrations/` | wire-protocol clients for other applications (AI/MCP service) | business rules, tables, imports of modules |
| `app/modules/<m>/` | one capability: `router`, `schemas`, `service`, `repository`, `models` | another module's internals |

## Module rules

1. **Public surface = `service.py` + `schemas.py`.** Other modules may import only these. Never
   another module's `repository`, `models` or `router`.
2. **Cross-module calls pass schemas or IDs**, never ORM objects.
3. **Every table has exactly one owning module**; only that module's repository reads or writes it.
   Cross-module references are plain UUID columns (`user_id`); no `relationship()` across modules.
4. **No cycles.** If two modules need each other, the shared concept belongs in one of them.
5. **Start small.** A module gets only the files it needs; there are no empty layers.
6. **Routers** are mounted centrally in `app/api.py` under `/api/v1`. Modules without a public HTTP
   surface (`integrations/careem`) have no router.

## Module catalogue

Table names below come from `docs/api-system-design.md` §14 and are **proposed**; each is
confirmed when its feature task adds the migration.

| Module | Responsibility | Owns (proposed) | Route prefix |
|---|---|---|---|
| `auth` | sign-in with Careem (no passwords), bearer authentication (`CurrentUserDep`) | `auth_identities`, `careem_credentials`, `oauth_transactions` | `/auth` |
| `users` | user accounts; source of truth for identity | `users` | `/users` |
| `profile` | personal info; aggregated profile view | `user_profiles` | `/profile` |
| `body_metrics` | height, weight, body data | body metrics | `/profile/body` |
| `nutrition_goals` | calorie and macro targets | `nutrition_goals` | `/profile/goals` |
| `food_preferences` | cuisines, liked/disliked foods | `food_preferences` | `/profile/preferences` |
| `dietary_restrictions` | dietary rules and allergies (hard constraints) | `dietary_restrictions`, `user_allergies` | `/profile/restrictions` |
| `routine` | daily schedule, meal timing | `user_routines` | `/profile/routine` |
| `budget` | food budget and currency | budget settings | `/profile/budget` |
| `cooking_preferences` | skill, time, equipment | cooking preferences | `/profile/cooking` |
| `onboarding` | onboarding progress, read via other modules' services | — | `/profile/onboarding-status` |
| `meal_planning` | meal plans, versions, days, meals, changes; requests AI generation | `meal_plans`, `meal_plan_versions`, `meal_plan_days`, `meals`, `meal_change_history` | `/meal-plans` |
| `grocery_management` | consolidated grocery lists and delivery batches | `grocery_lists`, `grocery_items`, `grocery_products`, `grocery_delivery_batches` | `/grocery-lists` |
| `purchases` | purchase authorization, limits, external orders | `purchase_authorizations`, `authorization_limits`, `external_orders` | `/purchases` |
| `integrations/careem` | Careem restaurant/grocery/order adapter (internal) | `integration_logs` | — |

`integrations/careem` is its own module (its `owner` for boundary purposes is currently computed
as `integrations`; see *Known follow-ups*). Tables in §14 not listed above (`recipes`,
`food_items`, `restaurant_dishes`, `conversations`, `scheduled_actions`, `notifications`, …) get an
owner when their feature is scheduled.

### Careem capabilities are unverified

The operations in `docs/api-system-design.md` §12 are proposed abstractions, not confirmed MCP
tools. Before implementing each one, verify it against the real MCP server (tool availability,
auth, coverage, pricing, checkout, payment, tracking, cancellation, sandbox) and record it in a
capability matrix. Unavailable operations must fail clearly or fall back to a manual path, never
pretend to succeed.

## Request pipeline

```
SecurityHeadersMiddleware   adds nosniff / DENY / no-referrer / COOP to every response
  CORSMiddleware            explicit allow-list from CORS_ORIGINS (absent when empty)
    RequestContextMiddleware  X-Request-ID (accepted if [A-Za-z0-9._-]{1,128}, else generated),
                              one JSON log line per request, last-resort 500 envelope
      exception handlers    DomainError / HTTPException / validation -> error envelope
        /api/v1 routers
```

All middleware is pure ASGI (not `BaseHTTPMiddleware`), so SSE responses stream unbuffered.

## Logging

JSON lines on stdout (`app/core/logging.py`): `timestamp` (UTC), `level`, `logger`, `message`,
`request_id` (inside a request) and any `extra={...}` fields as top-level keys. The request ID is
forwarded to the AI/MCP service as `X-Request-ID`, so one ID traces a request across both apps.
uvicorn's access log is disabled in favour of the structured request line. Never log secrets,
tokens or full PII.

## Known follow-ups (not done in this task)

- **Nested integration modules vs the boundary checker.** `tests/unit/test_architecture.py`
  treats `app.modules.integrations.careem` as module `integrations`, so the first
  `from app.modules.integrations.careem.service import …` in another module will be reported as a
  violation. Decide whether to teach the checker about nested provider modules or flatten to
  `app/modules/careem_integration/` before the first Careem feature.
- **Response envelope.** `docs/api-system-design.md` §15 proposes `{"success": …, "data": …}`;
  the implemented contract is `docs/contracts/error-responses.md` (no success wrapper). Pick one
  before the first public endpoint ships.
- **Authentication lives in the auth module, not core.** Resolving the caller needs the
  `auth_identities` table, and core may not import modules, so `CurrentUserDep` is exported from
  `app/modules/auth/service.py` (a module's public surface). Generic primitives (token hashing,
  PKCE, encryption) are in `app/core/security.py`; Careem token verification is in
  `app/integrations/careem_identity/`. There are no passwords.
- An `AiMcpServiceClient` dependency/lifespan wiring is added with the first AI feature.
