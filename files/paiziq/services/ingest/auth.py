"""Bearer API-key authentication with RBAC roles (PZ-073)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
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


@dataclass(frozen=True)
class AuthContext:
    """Verified key identity propagated to authorization-sensitive routes."""

    secret: str
    scopes: frozenset[str]
    role: str
    key_id: Optional[str] = None
    env_id: Optional[str] = None
    key_name: Optional[str] = None

    @property
    def is_bootstrap(self) -> bool:
        return self.key_id is None

    @property
    def is_admin(self) -> bool:
        return "admin" in self.scopes

    @property
    def managed_identity(self) -> bool:
        return self.key_id is not None and bool(self.key_name)


def _scopes_for_record(record: dict) -> frozenset[str]:
    role = record.get("role")
    if role:
        return ROLE_SCOPES.get(role, frozenset({record["scope"]}))
    return ROLE_SCOPES.get(SCOPE_TO_ROLE.get(record["scope"], record["scope"]), frozenset({record["scope"]}))


def _resolve(authorization: Optional[str]) -> AuthContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing API key")
    secret = authorization.split(" ", 1)[1].strip()
    if secret in settings.api_keys:
        return AuthContext(
            secret=secret,
            scopes=ROLE_SCOPES["admin"],
            role="admin",
        )
    record = get_key_store().verify(secret)
    if record is None:
        raise HTTPException(status_code=403, detail="Invalid API key")
    role = record.get("role") or SCOPE_TO_ROLE.get(record["scope"], record["scope"])
    return AuthContext(
        secret=secret,
        scopes=_scopes_for_record(record),
        role=role,
        key_id=record["id"],
        env_id=record["env_id"],
        key_name=record["name"],
    )


def _scoped_context(allowed: frozenset[str]) -> Callable[..., AuthContext]:
    def dependency(
        authorization: Optional[str] = Header(default=None),
    ) -> AuthContext:
        context = _resolve(authorization)
        if not context.scopes.intersection(allowed):
            raise HTTPException(status_code=403, detail="Insufficient key scope")
        return context

    return dependency


def _scoped_secret(allowed: frozenset[str]) -> Callable[..., str]:
    def dependency(authorization: Optional[str] = Header(default=None)) -> str:
        context = _resolve(authorization)
        if not context.scopes.intersection(allowed):
            raise HTTPException(status_code=403, detail="Insufficient key scope")
        return context.secret

    return dependency


require_api_key = _scoped_secret(frozenset({"ingest", "read", "admin", "review"}))
require_ingest_key = _scoped_secret(frozenset({"ingest", "admin"}))
require_read_key = _scoped_secret(frozenset({"read", "admin", "review"}))
require_read_context = _scoped_context(frozenset({"read", "admin", "review"}))
require_review_key = _scoped_context(frozenset({"review", "admin"}))
require_admin_key = _scoped_secret(frozenset({"admin"}))
require_audit_read = _scoped_secret(frozenset({"read", "admin", "review"}))


def actor_for(api_key: str | AuthContext) -> str:
    """Return a stable, non-secret, collision-resistant audit identity."""

    if isinstance(api_key, AuthContext):
        if api_key.key_id is not None:
            return f"key:{api_key.key_id}"
        secret = api_key.secret
    else:
        secret = api_key
    fingerprint = hashlib.sha256(secret.encode()).hexdigest()[:16]
    return f"key:bootstrap:{fingerprint}"
