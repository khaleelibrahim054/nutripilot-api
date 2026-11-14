---
paths:
  - "app/**/*.py"
---

# API design rules

## Endpoints
- Every route lives in its module's `router.py` and is mounted under `/api/v1` in `app/api.py`.
- Plural kebab-case nouns for resources (`/rides`, `/rides/{ride_id}`); verbs only for actions that aren't CRUD (`POST /rides/{ride_id}/cancel`).
- Correct status codes: 201 + body on create, 204 on delete, 404 missing, 409 conflict/duplicate, 422 validation, 401 unauthenticated, 403 unauthorized.
- Always declare `response_model` (or a typed return) so responses are filtered — never return ORM objects or dicts with unknown keys.
- Errors use the single shape `{"error": {"code": str, "message": str}}` produced by `app/core/exceptions.py` (422s add `details`). Services raise `NotFoundError`/`ConflictError`/`PermissionDeniedError` (or module `DomainError` subclasses), never `HTTPException`.

## Validation with Pydantic v2 (at the API boundary)
- Every request body, query and path parameter is validated by a Pydantic v2 schema or typed/`Annotated` parameter before reaching the service. Services may assume validated input; they still enforce business rules.
- Separate schemas per direction: `<Entity>Create`, `<Entity>Update` (all fields optional), `<Entity>Read`. Never reuse ORM models as schemas, never return a request schema as a response.
- Input schemas: `model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)`; constrain every field (`Field(min_length=, max_length=, ge=, le=, pattern=)`, `EmailStr`, enums, `Decimal` with `max_digits`/`decimal_places`).
- Output schemas: `model_config = ConfigDict(from_attributes=True)`; include only fields the client is allowed to see.
- Use v2 APIs only: `model_validate`, `model_dump`, `field_validator`, `model_validator`, `ConfigDict`. Never v1 (`.dict()`, `.parse_obj()`, `orm_mode`, `@validator`, `class Config`).
- IDs are `uuid.UUID`, timestamps are timezone-aware `datetime` (serialised ISO-8601 UTC).

## Scalability
- Everything async end-to-end: `async def` routes, `AsyncSession`, no blocking I/O (no `requests`, `time.sleep`, sync file/DB calls) inside the event loop.
- Every list endpoint is paginated (`limit` with a hard max like 100, plus `offset` or a cursor). No unbounded queries.
- Avoid N+1: use `selectinload`/`joinedload` explicitly; select only needed columns for large lists.
- Add DB indexes for every column used in filters, joins, ordering or uniqueness; declare unique constraints in the DB, not just in code.
- Keep the app stateless (no in-process caches/sessions holding user state) so it scales horizontally.
- One transaction per request/use-case, committed in the service layer; keep transactions short.
- Idempotency for retry-prone writes (payments, bookings): accept an `Idempotency-Key` header or use natural unique keys.

## Dependency injection
- DB sessions via `SessionDep` (`app/core/dependencies.py`); build services/repositories via `Depends`, so tests can override them.
- Config via `get_settings()`; never read `os.environ` directly in app code.
