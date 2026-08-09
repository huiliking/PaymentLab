"""
business/auth.py
=================
Auth seam for business-layer (commercial) mutations: a `Principal`
abstraction that endpoints depend on, instead of checking a key inline.

This is a *different* admin persona than the tooling ops-admin gate in
routes/fraud.py (_require_admin) — commercial-admin manages merchant
tiers and tool grants; ops-admin manages the tool catalog itself. They
happen to reuse the same shared PAYMENTLAB_ADMIN_KEY for now (no
per-persona credentials exist yet — full IAM is Sprint 6), but the role
distinction is real and the endpoints check role, not the key directly,
so a real IAM/JWT provider can replace current_principal()'s internals
in this one place without touching every route.
"""

from dataclasses import dataclass
import hmac
import os

from flask import request


@dataclass(frozen=True)
class Principal:
    role: str


ANONYMOUS = Principal(role="anonymous")


def current_principal() -> Principal:
    """
    Resolve the caller's principal from the current Flask request.
    Stub: a valid PAYMENTLAB_ADMIN_KEY bearer token resolves to
    commercial_admin. No key, wrong key, or unset env var -> anonymous.
    """
    admin_key = os.environ.get("PAYMENTLAB_ADMIN_KEY")
    auth_header = request.headers.get("Authorization", "")
    if admin_key and auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
        if hmac.compare_digest(token, admin_key):
            return Principal(role="commercial_admin")
    return ANONYMOUS
