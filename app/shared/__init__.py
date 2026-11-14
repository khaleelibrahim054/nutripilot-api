"""Shared kernel: small, stable building blocks used by core and by every module.

Rules: nothing here imports from app.modules or app.integrations, owns a table, or holds
business logic for a single capability. If only one module needs it, it belongs in that module.
"""
