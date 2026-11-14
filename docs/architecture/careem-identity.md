# Careem identity: ID token validation

Status: **implemented** (2026-11-14). Verified against live Careem metadata on
**2026-11-14 07:53 UTC**. Re-verify before go-live, because discovery metadata can change without
notice.

## 0. Implemented flow (mobile app)

| Step | Who | What |
|---|---|---|
| 1 | app → `POST /api/v1/auth/careem/start` `{client_challenge}` | The backend stores a single-use transaction (state/nonce hashed, PKCE verifier encrypted, the app's S256 challenge) and returns `{authorization_url, expires_in: 600}`. |
| 2 | app → Careem | The user signs in. Careem redirects to `CAREEM_REDIRECT_URI` (the app's deep link) with `code` + `state`. |
| 3 | app → `POST /api/v1/auth/careem/callback` `{code, state, client_verifier}` | The backend checks state, expiry and `s256(client_verifier)`, atomically consumes the transaction, and exchanges the code with PKCE: as a public client (`client_id` in the form, Careem's default), or with `client_secret_basic` if a secret is configured. It validates the ID token per §3 (including the nonce), finds or creates the user by `(careem, iss, sub)`, and stores Careem's access/refresh tokens Fernet-encrypted. |
| 4 | backend → app | `{id_token, token_type: "bearer", expires_in, user_id, is_new_user}`. Careem access/refresh tokens are never returned. |
| 5 | app → any API, `Authorization: Bearer <id_token>` | `CurrentUserDep` re-verifies the token per §3 (without the nonce) and maps `(iss, sub)` to an active user. `GET /api/v1/auth/session` returns `{user_id, expires_at}`. |

**Decision (2026-11-14): the Careem ID token is reused as the NutriPilot bearer token.** It is
issued for our client ID (`aud`), so accepting it at our own API is sound. The trade-off is that it
expires **600 s after issue**, after which the app must sign in again. A NutriPilot-issued session
or refresh flow is a deferred follow-up. The ID token is still never sent to Careem MCP; MCP uses
the stored Careem `access_token`.

Callback error codes: `invalid_state` (400, unknown, replayed or wrong `client_verifier`),
`login_expired` (400), `authentication_failed` (401, code rejected or ID token invalid),
`account_disabled` (403), `careem_unavailable` (502), `careem_login_unavailable` (503, not
configured).

## 1. Identity mapping

| Field | Value | Notes |
|---|---|---|
| provider | `careem` | constant, stored per external identity |
| issuer | `https://identity.careem.com/` | exact string, **with** trailing slash |
| subject | `sub` claim | Careem user ID; store as `str` (e.g. `"92305709"`), never cast to int |
| stable key | `(issuer, sub)` | unique constraint; never key on `sub` alone, `jti`, email or phone |

`sub` uniqueness is only guaranteed per issuer. Discovery advertises `subject_types_supported:
["public", "pairwise"]`. If our client is registered as **pairwise**, `sub` is unique per client
(and per sector identifier), so the same person gets a different `sub` in another Careem client.
Never compare `sub` values across clients. See [Q3](#5-outstanding-careem-integration-questions).

## 2. Verification results

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | OAuth protected-resource metadata | Verified | `GET https://mcp.careemapis.com/.well-known/oauth-protected-resource` returns `resource: https://mcp.careemapis.com/mcp`, `authorization_servers: ["https://identity.careem.com/"]`, `scopes_supported: ["openid", "xcma", "profile"]`. Sandbox: `mcp.sandbox.careemapis.com`, with authorization server `https://identity.qa.careem-engineering.com/`. Source: https://builders.careem.com/docs/authentication. `POST /mcp` without a token returns 401. |
| 2 | OIDC discovery | Verified | `GET https://identity.careem.com/.well-known/openid-configuration` returns 200 JSON |
| 3 | Issuer | Verified | `issuer = "https://identity.careem.com/"`, which matches the token's `iss` exactly |
| 4 | JWKS URI | Verified | `jwks_uri = "https://identity.careem.com/jwk"`, which returns 200 with 10 RSA keys (`kty=RSA`, `use=sig`, `e=AQAB`, **no `alg` member**). Served with `Cache-Control: no-cache, no-store, max-age=0`, so there is no cache lifetime to honour. |
| 5 | Signing algorithms | Verified, **with risk** | `id_token_signing_alg_values_supported: ["RS256", "none"]`. Careem advertises unsigned ID tokens, so we **must pin RS256** and reject everything else. |
| 6 | Authorization endpoint | Verified | `https://identity.careem.com/authorize` |
| 7 | Token endpoint | Verified | `https://identity.careem.com/token`. Discovery lists only `client_secret_basic`, but Careem's builder docs say MCP client IDs are **public clients: "PKCE enforced, no client secret needed"**, and show `client_id` in the token request body. We support both modes (public when `CAREEM_CLIENT_SECRET` is empty). |
| 8 | Scopes | Verified | Builder docs say to request `openid profile offline_access` (+ `locations` if needed). The MCP metadata also lists `xcma`; add it if MCP rejects tokens without it. `offline_access` yields refresh tokens. |
| 9 | Audience / client ID | Partially verified | The token has single-string `aud = "17ccbccd-fb56-43b9-8b2e-198c08a0a207"`. Validation requires `aud == CAREEM_CLIENT_ID`. **No `CAREEM_CLIENT_ID` is configured in this repo yet**, so the match could not be confirmed. |
| 10 | ID token issued for this client | **Not confirmed** | The token's `kid` `4390a144-906a-49eb-8ffd-614805c2d124` **is present** in the live JWKS, but the raw signed JWT was not available (only the decoded payload), so the signature was not verified. `aud` cannot be matched until the client ID is confirmed. There is no `azp` claim. |
| 11 | Nonce | **Unknown** | The sample payload has **no `nonce` claim**. Either the authorization request sent none, or Careem does not echo it. Needs a test login with a nonce (Q1). |
| 12 | Expiry behaviour | Verified from sample | `iat = 2026-11-14T07:10:22Z`, `exp = 07:20:22Z`, so the **ID token lifetime is 600 s**. `_exp_in_s = 537` is a non-standard remaining-seconds hint computed at the moment of decoding. It is ignored. |

Other discovery facts relevant to the callback task:

- `response_types_supported: ["code", "token"]`. Use **`code` only**. The implicit `token` flow is not allowed.
- `code_challenge_methods_supported: ["plain", "S256"]`. Use **PKCE `S256`**; never `plain`.
- `grant_types_supported` includes `refresh_token`. Refresh tokens are presumably issued with `offline_access`.
- Endpoints: `userinfo_endpoint` `/userinfo`, `revocation_endpoint` `/revoke`, `introspection_endpoint`
  `/introspect`, `end_session_endpoint` `/endsession`.
- `request_parameter_supported: true`, `claims_parameter_supported: false`.

Anomaly: the sample "payload" contains `kid`. `kid` is a **JOSE header** parameter (RFC 7515 §4.1.4).
Its presence suggests that the header and payload were merged when decoding. The validator must read
`kid` and `alg` **only from the protected header**, never from the payload.

## 3. Token validation specification

Input: the raw compact JWS `id_token` from the token endpoint response, plus the `nonce` stored with
the login transaction (if one was sent). Every check is mandatory. Any failure means rejection,
no user is created or logged in, and the response is a generic `401` (no reason details to the client).
Run the checks **in this order**:

1. **Structure**: exactly 3 base64url segments (a compact JWS). Reject JWE (5 segments) and
   anything else. Enforce a max token length (for example 8 KB) before parsing.
2. **Header `alg`**: must be in the allowlist `{"RS256"}` (code constant `ALLOWED_ALGORITHMS`). Reject
   `none`, every `HS*` (this prevents key-confusion attacks that use the RSA public key as an HMAC
   secret), and every other value. The allowlist is ours. It is **never** taken from the token header
   or from discovery.
3. **Header `kid`**: required, non-empty string. Reject the token if it is missing. Do not try every key.
4. **Key selection**: look up the JWK whose `kid` equals the header `kid` in the cached JWKS
   (see §4). The key must have `kty == "RSA"`, and `use` must be absent or equal to `"sig"`. If the key
   carries an `alg`, it must equal the header `alg`. Reject RSA moduli under 2048 bits.
5. **Signature**: verify the RS256 signature with that key. **No claim is trusted before this step
   succeeds.**
6. **`iss`**: must equal `CAREEM_ISSUER` (`https://identity.careem.com/`) byte-for-byte. There is no
   trailing-slash normalisation.
7. **`aud`**: if it is a string, it must equal `CAREEM_CLIENT_ID`. If it is an array, it must contain
   `CAREEM_CLIENT_ID`, and if it holds more than one value, `azp` must be present and equal
   `CAREEM_CLIENT_ID`. If `azp` is present at all, it must equal `CAREEM_CLIENT_ID`.
8. **`exp`**: required numeric date. Reject if `now >= exp + leeway`. `exp` is the only expiry source,
   and `_exp_in_s` is ignored.
9. **`iat`**: required numeric date. Reject if `iat > now + leeway` (issued in the future), and reject
   if `now - iat > CAREEM_ID_TOKEN_MAX_AGE_SECONDS + leeway` (default 600, which matches the observed
   lifetime; the leeway applies symmetrically with `exp`, so a token is never rejected by max-age
   before it would be by `exp`). This bounds replay even if `exp` is set far out. Reject if
   `exp <= iat`. The expiry reported to the app is `min(exp, iat + max_age)`.
10. **`nbf`**: if present, reject if `now < nbf - leeway`.
11. **`nonce`**: if the authorization request sent a nonce, the claim is required and is compared
    with `hmac.compare_digest` against the stored value, which is then consumed (single use). If no
    nonce was sent, a `nonce` claim in the token means rejection. The policy is to **always send a
    nonce** (32+ bytes from `secrets`), subject to Q1.
12. **`sub`**: required, non-empty string, max 255 characters. Stored as a string.

Leeway: `CAREEM_JWT_LEEWAY_SECONDS` (default 60, max 120). Validation receives `now` as an
injectable clock so that tests are deterministic.

Claim handling after validation:

| Claim | Use |
|---|---|
| `sub` + `iss` | the identity key; the only user identifier |
| `aud`, `azp` | validated, not stored |
| `exp`, `iat` | validated. The NutriPilot session lifetime is ours and is **not** tied to `exp`. |
| `jti` | token ID only. It may be logged (it is not a secret) or used for replay detection. It is never a user ID. |
| `kid` | header only, used for key selection |
| `_exp_in_s` | ignored |
| `email`, `phone_number`, … | not present in the sample. If they appear later, they are profile hints only, never identity keys, and are trusted only when the `*_verified` claim is `true`. |

Hard rules:

- Never decode-and-trust (for example `verify_signature=False`) anywhere except in test helpers.
- The **ID token proves login to NutriPilot only.** It is never sent to Careem MCP or APIs. Careem
  API/MCP calls use the separately issued **`access_token`**, which is opaque to us. We do not parse
  or validate it as a JWT, and it is stored encrypted at rest, server-side only.
- Never log raw tokens (ID, access or refresh), `nonce`, `code`, `code_verifier` or the client secret.
- The validator lives in `app/integrations/careem_identity/id_token.py` (Careem-specific wire
  logic). It returns a verified `(issuer, subject, expires_at)` value object, which
  `app/modules/auth` maps to a NutriPilot user.
- Library: `PyJWT[crypto]`, used for signature verification only, with `algorithms=["RS256"]`. All
  claim checks are explicit and use an injectable clock.

## 4. JWKS and key-rotation strategy

Careem's JWKS response has `no-store` and no `ETag`/`max-age`, so we define our own cache policy:

- **Fetch** from `CAREEM_JWKS_URI` with `httpx` (HTTPS enforced by settings validation, timeout
  `CAREEM_HTTP_TIMEOUT_SECONDS` (default 10 s), redirects not followed). The URI is pinned in
  config, not re-read from discovery at runtime, so a tampered discovery response cannot redirect
  key loading.
- **Cache** the parsed key set in-process, keyed by `kid`, with a TTL of
  `CAREEM_JWKS_CACHE_TTL_SECONDS` (default 3600).
- **Unknown `kid`** (rotation): perform **one** forced refresh, then look up the key again. Refreshes
  are rate-limited to at most one per `CAREEM_JWKS_MIN_REFRESH_INTERVAL_SECONDS` (default 60) and
  single-flighted with an `asyncio.Lock`, so a flood of forged `kid`s cannot hammer Careem. If the
  key is still unknown, reject the token.
- **Fetch failure**: keep serving the last good key set until it is 24 h old (stale-if-error); after
  that, logins and bearer checks fail closed with 502 `careem_unavailable`. Never fall back to
  skipping verification.
- **Removed keys** disappear on the next refresh. We do not pin individual keys or keep revoked ones.
- **Startup**: do not block app start on Careem; load lazily on first use. Health checks do not call
  Careem.
- **Multiple workers**: each process holds its own cache. That is acceptable at a 1-hour TTL, so no
  shared cache is needed.
- **Monitoring**: log (without token contents) any unknown-`kid` events, refresh failures and
  algorithm rejections.

## 5. Required environment variables

All of these are read through `app.core.config.Settings` (see `.env.example`). Login returns 503
`careem_login_unavailable` until `CAREEM_CLIENT_ID`, `CAREEM_REDIRECT_URI`
and `AUTH_ENCRYPTION_KEY` are all set.

| Variable | Example / default | Secret | Purpose |
|---|---|---|---|
| `CAREEM_CLIENT_ID` | `17ccbccd-…` *(confirm, Q2)* | no | expected `aud`/`azp`; client auth |
| `CAREEM_CLIENT_SECRET` | empty (public client) | **yes** | only for a confidential client: then sent via `client_secret_basic` |
| `CAREEM_ISSUER` | `https://identity.careem.com/` | no | exact `iss` match |
| `CAREEM_JWKS_URI` | `https://identity.careem.com/jwk` | no | signing keys |
| `CAREEM_AUTHORIZATION_ENDPOINT` | `https://identity.careem.com/authorize` | no | authorization URL |
| `CAREEM_TOKEN_ENDPOINT` | `https://identity.careem.com/token` | no | code exchange |
| `CAREEM_REDIRECT_URI` | `nutripilot://auth/careem/callback` | no | the mobile app's deep link registered with Careem (the app handles the redirect) |
| `CAREEM_SCOPES` | `["openid"]` *(+ others per Q4)* | no | requested scopes |
| `CAREEM_HTTP_TIMEOUT_SECONDS` | `10` | no | Careem request timeout |
| `OAUTH_TRANSACTION_TTL_SECONDS` | `600` | no | login attempt lifetime |
| `AUTH_ENCRYPTION_KEY` | Fernet key | **yes** | encrypts PKCE verifiers and Careem tokens at rest |
| `CAREEM_JWT_LEEWAY_SECONDS` | `60` | no | clock skew |
| `CAREEM_ID_TOKEN_MAX_AGE_SECONDS` | `600` | no | `iat` freshness bound |
| `CAREEM_JWKS_CACHE_TTL_SECONDS` | `3600` | no | JWKS cache |
| `CAREEM_JWKS_MIN_REFRESH_INTERVAL_SECONDS` | `60` | no | rotation refresh rate limit |
| `CAREEM_MCP_URL` | `https://mcp.careemapis.com/mcp` (sandbox `https://mcp.sandbox.careemapis.com/mcp`) | no | MCP server (Streamable HTTP), called with `Authorization: Bearer <access_token>` |

Endpoints are pinned per environment rather than auto-discovered at runtime. A CI or ops check may
compare them against live discovery and alert on drift.

## 6. Security assumptions

1. TLS to `identity.careem.com` is trustworthy (system CA store, no certificate-verification bypass).
2. Careem signs every ID token issued to our client with RS256, even though `none` is advertised. If
   Careem ever sends `alg: none`, logins fail; that is the intended outcome.
3. `(iss, sub)` is stable for the life of a Careem account and is never reassigned to another person.
4. Our client is **confidential** (backend-held secret). The browser/app never sees the client secret,
   tokens or the PKCE verifier; it gets only a NutriPilot session.
5. The login uses the authorization code flow with PKCE S256, and both `state` and `nonce` are bound
   to a server-side, single-use, short-lived (≤ 10 min) login transaction.
6. The ID token is received **directly from the token endpoint over TLS**, which is a second
   authenticity layer. Signature validation is still mandatory.
7. Server clocks are NTP-synced; the 60 s leeway covers the remaining skew.
8. The access token audience/scope is enforced by Careem MCP, not by us. We only forward it.
9. Account linking happens by `(iss, sub)` only, never by matching email or phone, which prevents
   account takeover through an unverified or recycled contact detail.

## 7. Outstanding Careem integration questions

1. **Nonce**: does Careem echo `nonce` in the ID token for the code flow? The sample has none. Test:
   log in with `nonce=<random>` and inspect the token. If it is never echoed, the decision becomes
   whether to rely on `state` + PKCE alone (acceptable for the code flow per OIDC Core §3.1.2.1, but
   a weaker replay defence).
2. **Client ID**: is `17ccbccd-fb56-43b9-8b2e-198c08a0a207` our registered client ID for every
   environment, or is there a separate ID per staging and production?
3. **Subject type**: is our client registered as `public` or `pairwise`? If it is pairwise, what is
   the sector identifier, and will `sub` stay stable if redirect URIs change?
4. **Scopes** (partly answered): the docs say `openid profile offline_access`. Still open: is `xcma`
   (listed in the MCP metadata) required for MCP calls? Do `profile` claims arrive in the ID token
   or only from `/userinfo`?
5. **MCP resource server**: *answered.* `https://mcp.careemapis.com/mcp` (Streamable HTTP,
   stateless, `Authorization: Bearer <access_token>` on every request). It publishes RFC 9728
   metadata. No `resource` parameter is documented. See https://builders.careem.com/docs/connection.
   Still open: does the token response include an `id_token` for public clients? (The docs' sequence
   lists only access and refresh tokens, but `openid` is requested, and our login requires an
   ID token.)
5a. **Redirect URI registration**: our `CAREEM_REDIRECT_URI` must be registered for our client ID via
   https://builders.careem.com/request-access. Register an https App Link / Universal Link, not just
   `nutripilot://`, so another app on the device cannot intercept the code.
6. **Access token**: is it a JWT or opaque? What are its lifetime and the refresh-token lifetime and
   rotation policy? Is it audience-restricted to MCP?
7. **Key rotation**: what is Careem's signing-key rotation cadence, and what overlap window does it
   give? (The JWKS currently publishes 10 keys.)
8. **`alg: none`**: can Careem confirm that our client is configured to *only* receive RS256-signed
   ID tokens?
9. **`kid` in payload**: is `kid` really emitted as a payload claim, or was the sample a merged
   header+payload decode? Supply a raw sample JWT (non-production) so the signature check can be
   verified end to end.
10. **Logout and revocation**: should NutriPilot logout call `/revoke` and/or `/endsession`? Is a
    back-channel logout available?
11. **Discovery stability**: Careem returns no `oauth-authorization-server` metadata (404). Is
    `/.well-known/openid-configuration` the supported contract?
