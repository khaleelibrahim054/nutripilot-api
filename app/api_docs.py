"""OpenAPI (Swagger) metadata for the whole API. Served at /docs outside production."""

API_VERSION = "1.0.0"

API_DESCRIPTION = """
NutriPilot backend API. All endpoints are under `/api/v1`. Errors always use the shape
`{"error": {"code": "...", "message": "..."}}`; 422 responses add `details`.

## Signing in (Careem)

NutriPilot has no passwords. Users sign in with their Careem account from the mobile app:

1. **App:** generate a random `client_verifier` (43-128 chars) and keep it in memory. Send
   `client_challenge = BASE64URL(SHA256(client_verifier))` to **`POST /auth/careem/start`**.
2. **App:** open the returned `authorization_url`. The user signs in at Careem, and Careem redirects
   to the app's registered redirect URI with `code` and `state`.
3. **App:** send `code`, `state` and `client_verifier` to **`POST /auth/careem/callback`**. The
   response contains `id_token`, `user_id` and `is_new_user`. The first login creates the account.
4. **App:** call protected endpoints with `Authorization: Bearer <id_token>`.
   **`GET /auth/session`** checks the token. The token expires about 10 minutes after login; after
   a 401, sign in again.

To try protected endpoints here, complete steps 1-3 (for example with the Postman collection in
`docs/postman/`), then click **Authorize** and paste the `id_token`.
"""

OPENAPI_TAGS = [
    {
        "name": "auth",
        "description": "Sign in with Careem and check the current session.",
    },
    {
        "name": "health",
        "description": "Liveness and database readiness. Public.",
    },
]

SWAGGER_UI_PARAMETERS = {
    "persistAuthorization": True,  # keep the pasted bearer token across page reloads
    "displayRequestDuration": True,
    "tryItOutEnabled": True,
}
