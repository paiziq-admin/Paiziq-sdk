"""Organization and environment management endpoints (contract §4)."""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from audit import AuditLog
from auth import actor_for, require_api_key
from deps import get_audit_log, get_org_store
from envelope import ApiError, list_meta, ok
from stores.orgs import OrgStore

router = APIRouter(tags=["organizations"])


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["sandbox", "production"]


@router.post("/v1/orgs")
def create_org(
    body: OrgCreate,
    api_key: str = Depends(require_api_key),
    store: OrgStore = Depends(get_org_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    try:
        org = store.create_org(body.name.strip())
    except sqlite3.IntegrityError:
        raise ApiError(409, "conflict", f"organization name already exists: {body.name!r}")
    audit.record(actor_for(api_key), "org.create", org["id"], {"name": org["name"]})
    return ok(org)


@router.get("/v1/orgs")
def list_orgs(
    api_key: str = Depends(require_api_key),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    store: OrgStore = Depends(get_org_store),
) -> dict[str, Any]:
    orgs, total = store.list_orgs(limit, offset)
    return ok(orgs, meta=list_meta(total, limit, offset))


@router.get("/v1/orgs/{org_id}")
def get_org(
    org_id: str,
    api_key: str = Depends(require_api_key),
    store: OrgStore = Depends(get_org_store),
) -> dict[str, Any]:
    org = store.get_org(org_id)
    if org is None:
        raise ApiError(404, "not_found", f"organization not found: {org_id}")
    return ok(org)


@router.post("/v1/orgs/{org_id}/environments")
def create_environment(
    org_id: str,
    body: EnvironmentCreate,
    api_key: str = Depends(require_api_key),
    store: OrgStore = Depends(get_org_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    if store.get_org(org_id) is None:
        raise ApiError(404, "not_found", f"organization not found: {org_id}")
    try:
        env = store.create_environment(org_id, body.name.strip(), body.kind)
    except sqlite3.IntegrityError:
        raise ApiError(
            409, "conflict", f"environment name already exists in org: {body.name!r}"
        )
    audit.record(
        actor_for(api_key), "environment.create", env["id"],
        {"org_id": org_id, "name": env["name"], "kind": env["kind"]},
    )
    return ok(env)


@router.get("/v1/orgs/{org_id}/environments")
def list_environments(
    org_id: str,
    api_key: str = Depends(require_api_key),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    store: OrgStore = Depends(get_org_store),
) -> dict[str, Any]:
    if store.get_org(org_id) is None:
        raise ApiError(404, "not_found", f"organization not found: {org_id}")
    envs, total = store.list_environments(org_id, limit, offset)
    return ok(envs, meta=list_meta(total, limit, offset))
