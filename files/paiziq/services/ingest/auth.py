"""Bearer API-key authentication with scopes (contract §1.2).

Keys resolve from two sources: the PAIZIQ_INGEST_KEYS env var
(bootstrap keys, full admin scope) and the database-backed key store
(PZ-013, scoped ingest/read/admin). Scope rules:

    ingest  → POST /v1/traces, POST /v1/notifications
    read    → all GET endpoints
    admin   → everything, including key lifecycle

Owns the process-wide Settings singleton.
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import Header, HTTPException

from config import configure_logging, load_settings
from deps import get_key_store

settings = load_settings()
configure_logging(settings)


def _resolve(authorization: Optional[str]) -> tuple[str, str]:
    """Return (presented secret, scope); raise 401/403 otherwise."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing API key")
    secret = authorization.split(" ", 1)[1].strip()
    if secret in settings.api_keys:
        return secret, "admin"
    record = get_key_store().verify(secret)
    if record is None:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return secret, record["scope"]


def _scoped(allowed: frozenset[str]) -> Callable[..., str]:
    def dependency(authorization: Optional[str] = Header(default=None)) -> str:
        secret, scope = _resolve(authorization)
        if scope not in allowed:
            raise HTTPException(status_code=403, detail="Insufficient key scope")
        return secret

    return dependency


require_api_key = _scoped(frozenset({"ingest", "read", "admin"}))
require_ingest_key = _scoped(frozenset({"ingest", "admin"}))
require_read_key = _scoped(frozenset({"read", "admin"}))
require_admin_key = _scoped(frozenset({"admin"}))


def actor_for(api_key: str) -> str:
    """Audit-log actor label for a request key (never the full secret)."""
    return f"key:{api_key[:8]}"
