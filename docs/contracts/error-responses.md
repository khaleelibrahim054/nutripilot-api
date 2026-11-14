# Contract: Error Responses

Every non-2xx JSON response from `/api/v1` uses one envelope
(`app/shared/schemas/errors.py`, produced by `app/core/exceptions.py`):

```json
{ "error": { "code": "not_found", "message": "Meal plan not found" } }
```

Validation failures (422) add `details`:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": [
      { "location": ["body", "days"], "message": "Input should be less than or equal to 30", "type": "less_than_equal" }
    ]
  }
}
```

| Status | `code` | Source |
|---|---|---|
| 400 | `bad_request` (or a module-specific code) | `DomainError` |
| 403 | `forbidden` | `PermissionDeniedError` |
| 404 | `not_found` | `NotFoundError`, unknown route |
| 405 | `method_not_allowed` | wrong HTTP method |
| 409 | `conflict` | `ConflictError` |
| 422 | `validation_error` | request body/query/path validation |
| 500 | `internal_error` | any unhandled exception |
| 503 | — | `GET /api/v1/health` returns `{"status": "unavailable", "database": "unavailable"}` |

Guarantees:

- `code` is stable, lowercase snake_case and safe to branch on; `message` is for humans and may
  change.
- No stack traces, SQL, file paths, upstream (AI/Careem) error bodies or rejected input values are
  ever returned. Validation `details` never echo the submitted value.
- Every response, including errors, carries an `X-Request-ID` header; quote it when reporting
  problems. Clients may send their own `X-Request-ID` (`[A-Za-z0-9._-]{1,128}`).

Services raise `DomainError` subclasses, never `HTTPException`. A module needing a new code
subclasses `DomainError` in its own `exceptions.py` with `status_code` and `code`.
