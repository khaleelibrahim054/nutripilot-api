"""Auth: sign-in with Careem (no NutriPilot passwords) and bearer-token authentication.

Owns external identities (auth_identities), Careem credentials and OAuth login transactions.
User records belong to the users module. Other modules authenticate callers with
`app.modules.auth.service.CurrentUserDep`.
"""
