"""Clients for SEPARATE applications this API calls over the network (not in-process modules).

Business modules use these clients from their services. Clients here never import app.modules,
own no tables and contain no business rules: they speak the wire protocol and map transport
failures to typed exceptions.
"""
