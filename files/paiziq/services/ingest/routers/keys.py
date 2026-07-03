"""API key lifecycle endpoints (contract §6). Admin scope only.

The plaintext secret appears exactly once, in the create/rotate
response (`data.secret`); every other read returns the prefix only.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from audit import AuditLog
from auth import actor_for, require_admin_key, require_read_key
from deps import get_audit_log, get_key_store, get_org_store
from envelope import ApiError, list_meta, ok
from stores.keys import KeyStore
from stores.orgs import OrgStore

router = APIRouter(tags=["api-keys"])

MAX_GRACE_SECONDS = 7 * 24 * 3600


class KeyCreate(BaseModel):
    env_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    scope: Literal["ingest", "read", "admin"]


class KeyRotate(BaseModel):
    grace_seconds: int = Field(default=0, ge=0, le=MAX_GRACE_SECONDS)


@router.post("/v1/api-keys")
def create_key(
    body: KeyCreate,
    api_key: str = Depends(require_admin_key),
    keys: KeyStore = Depends(get_key_store),
    orgs: OrgStore = Depends(get_org_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    env = orgs.get_environment(body.env_id)
    if env is None:
        raise ApiError(404, "not_found", f"environment not found: {body.env_id}")
    record, secret = keys.create(body.env_id, env["kind"], body.name.strip(), body.scope)
    audit.record(
        actor_for(api_key), "api_key.create", record["id"],
        {"env_id": body.env_id, "scope": body.scope, "prefix": record["secret_prefix"]},
    )
    return ok({**record, "secret": secret})


@router.get("/v1/api-keys")
def list_keys(
    api_key: str = Depends(require_read_key),
    env_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    keys: KeyStore = Depends(get_key_store),
) -> dict[str, Any]:
    items, total = keys.list(env_id, limit, offset)
    return ok(items, meta=list_meta(total, limit, offset))


@router.post("/v1/api-keys/{key_id}/rotate")
def rotate_key(
    key_id: str,
    body: KeyRotate,
    api_key: str = Depends(require_admin_key),
    keys: KeyStore = Depends(get_key_store),
    orgs: OrgStore = Depends(get_org_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    record = keys.get(key_id)
    if record is None:
        raise ApiError(404, "not_found", f"api key not found: {key_id}")
    if record["revoked_at_ms"] is not None:
        raise ApiError(409, "conflict", "cannot rotate a revoked key")
    env = orgs.get_environment(record["env_id"])
    assert env is not None  # FK guarantees the environment exists
    rotated = keys.rotate(key_id, env["kind"], body.grace_seconds)
    assert rotated is not None
    new_record, secret = rotated
    audit.record(
        actor_for(api_key), "api_key.rotate", key_id,
        {"grace_seconds": body.grace_seconds, "prefix": new_record["secret_prefix"]},
    )
    return ok({**new_record, "secret": secret})


@router.delete("/v1/api-keys/{key_id}")
def revoke_key(
    key_id: str,
    api_key: str = Depends(require_admin_key),
    keys: KeyStore = Depends(get_key_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    if keys.get(key_id) is None:
        raise ApiError(404, "not_found", f"api key not found: {key_id}")
    record = keys.revoke(key_id)
    if record is None:
        raise ApiError(409, "conflict", "key already revoked")
    audit.record(actor_for(api_key), "api_key.revoke", key_id, {})
    return ok(record)
