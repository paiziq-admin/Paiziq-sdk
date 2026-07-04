"""Bearer API-key authentication with RBAC roles (PZ-073)."""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import Header, HTTPException

from config import configure_logging, load_settings
from deps import get_key_store

settings = load_settings()
configure_logging(settings)

ROLE_SCOPES: dict[str, frozenset[str]] = {
    "admin": frozenset({"ingest", "read", "admin", "review"}),
    "developer": frozenset({"ingest", "read"}),
    "reviewer": frozenset({"read", "review"}),
    "read_only": frozenset({"read"}),
}
SCOPE_TO_ROLE = {"admin": "admin", "read": "read_only", "ingest": "developer"}


def _scopes_for_record(record: dict) -> frozenset[str]:
    role = record.get("role")
    if role:
        return ROLE_SCOPES.get(role, frozenset({record["scope"]}))
    return ROLE_SCOPES.get(SCOPE_TO_ROLE.get(record["scope"], record["scope"]), frozenset({record["scope"]}))


def _resolve(authorization: Optional[str]) -> tuple[str, frozenset[str]]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing API key")
    secret = authorization.split(" ", 1)[1].strip()
    if secret in settings.api_keys:
        return secret, ROLE_SCOPES["admin"]
    record = get_key_store().verify(secret)
    if record is None:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return secret, _scopes_for_record(record)


def _scoped(allowed: frozenset[str]) -> Callable[..., str]:
    def dependency(authorization: Optional[str] = Header(default=None)) -> str:
        secret, scopes = _resolve(authorization)
        if not scopes.intersection(allowed):
            raise HTTPException(status_code=403, detail="Insufficient key scope")
        return secret

    return dependency


require_api_key = _scoped(frozenset({"ingest", "read", "admin", "review"}))
require_ingest_key = _scoped(frozenset({"ingest", "admin"}))
require_read_key = _scoped(frozenset({"read", "admin", "review"}))
require_admin_key = _scoped(frozenset({"admin"}))
require_audit_read = _scoped(frozenset({"read", "admin", "review"}))


def actor_for(api_key: str) -> str:
    return f"key:{api_key[:8]}"
