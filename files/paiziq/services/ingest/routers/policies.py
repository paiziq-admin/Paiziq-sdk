"""Policy management endpoints (contract §10): drafts, immutable
published versions, and version history."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from audit import AuditLog
from auth import actor_for, require_admin_key, require_read_key
from deps import get_audit_log, get_org_store, get_policy_store
from envelope import ApiError, list_meta, ok
from policy_doc import DocumentError, diff_documents, normalize
from stores.orgs import OrgStore
from stores.policies import PolicyStore

router = APIRouter(tags=["policies"])


class PolicyCreate(BaseModel):
    env_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    document: Optional[dict[str, Any]] = None


def _normalized(document: Optional[dict[str, Any]]) -> dict[str, Any]:
    try:
        return normalize(document)
    except DocumentError as exc:
        raise ApiError(422, "validation_error", f"invalid policy document: {exc}")


def _require_policy(policies: PolicyStore, policy_id: str) -> dict[str, Any]:
    record = policies.get(policy_id)
    if record is None:
        raise ApiError(404, "not_found", f"policy not found: {policy_id}")
    return record


@router.post("/v1/policies")
def create_policy(
    body: PolicyCreate,
    api_key: str = Depends(require_admin_key),
    policies: PolicyStore = Depends(get_policy_store),
    orgs: OrgStore = Depends(get_org_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    if orgs.get_environment(body.env_id) is None:
        raise ApiError(404, "not_found", f"environment not found: {body.env_id}")
    document = _normalized(body.document)
    try:
        record = policies.create(body.env_id, body.name.strip(), document)
    except sqlite3.IntegrityError:
        raise ApiError(409, "conflict", f"policy name already exists in env: {body.name!r}")
    audit.record(
        actor_for(api_key), "policy.create", record["id"],
        {"env_id": body.env_id, "name": record["name"]},
    )
    return ok(record)


@router.get("/v1/policies")
def list_policies(
    api_key: str = Depends(require_read_key),
    env_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    policies: PolicyStore = Depends(get_policy_store),
) -> dict[str, Any]:
    items, total = policies.list(env_id, limit, offset)
    return ok(items, meta=list_meta(total, limit, offset))


@router.get("/v1/policies/{policy_id}")
def get_policy(
    policy_id: str,
    api_key: str = Depends(require_read_key),
    policies: PolicyStore = Depends(get_policy_store),
) -> dict[str, Any]:
    return ok(_require_policy(policies, policy_id))


class DraftUpdate(BaseModel):
    document: dict[str, Any]


@router.put("/v1/policies/{policy_id}/draft")
def update_draft(
    policy_id: str,
    body: DraftUpdate,
    api_key: str = Depends(require_admin_key),
    policies: PolicyStore = Depends(get_policy_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    _require_policy(policies, policy_id)
    record = policies.update_draft(policy_id, _normalized(body.document))
    assert record is not None
    audit.record(actor_for(api_key), "policy.draft_update", policy_id, {})
    return ok(record)


@router.post("/v1/policies/{policy_id}/publish")
def publish_policy(
    policy_id: str,
    api_key: str = Depends(require_admin_key),
    policies: PolicyStore = Depends(get_policy_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    record = _require_policy(policies, policy_id)
    if record["draft_document"] is None:
        raise ApiError(409, "conflict", "policy has no draft document to publish")
    version = policies.publish(policy_id, _normalized(record["draft_document"]))
    audit.record(
        actor_for(api_key), "policy.publish", policy_id,
        {"version": version["version"], "env_id": record["env_id"]},
    )
    return ok(version)


@router.get("/v1/policies/{policy_id}/versions")
def list_versions(
    policy_id: str,
    api_key: str = Depends(require_read_key),
    policies: PolicyStore = Depends(get_policy_store),
) -> dict[str, Any]:
    _require_policy(policies, policy_id)
    return ok(policies.versions(policy_id))


class RollbackRequest(BaseModel):
    version: int = Field(ge=1)


@router.post("/v1/policies/{policy_id}/rollback")
def rollback_policy(
    policy_id: str,
    body: RollbackRequest,
    api_key: str = Depends(require_admin_key),
    policies: PolicyStore = Depends(get_policy_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    record = _require_policy(policies, policy_id)
    target = policies.get_version(policy_id, body.version)
    if target is None:
        raise ApiError(404, "not_found", f"version {body.version} not found for {policy_id}")
    # History is never rewritten: rollback publishes a NEW version whose
    # content copies the target, and re-syncs the draft to match it.
    version = policies.publish(policy_id, target["document"])
    policies.update_draft(policy_id, target["document"])
    audit.record(
        actor_for(api_key), "policy.rollback", policy_id,
        {"to_version": body.version, "published_version": version["version"],
         "env_id": record["env_id"]},
    )
    return ok(version)


def _resolve_compare_ref(
    policies: PolicyStore, record: dict[str, Any], label: str
) -> tuple[Any, dict[str, Any]]:
    """A compare reference is a version number or the literal 'draft'."""
    if label == "draft":
        if record["draft_document"] is None:
            raise ApiError(404, "not_found", "policy has no draft document")
        return "draft", record["draft_document"]
    try:
        number = int(label)
    except ValueError:
        raise ApiError(422, "validation_error", f"invalid version reference: {label!r}")
    version = policies.get_version(record["id"], number)
    if version is None:
        raise ApiError(404, "not_found", f"version {number} not found for {record['id']}")
    return number, version["document"]


@router.get("/v1/policies/{policy_id}/versions/compare")
def compare_versions(
    policy_id: str,
    base: str = Query(min_length=1),
    target: str = Query(min_length=1),
    api_key: str = Depends(require_read_key),
    policies: PolicyStore = Depends(get_policy_store),
) -> dict[str, Any]:
    record = _require_policy(policies, policy_id)
    base_ref, base_doc = _resolve_compare_ref(policies, record, base)
    target_ref, target_doc = _resolve_compare_ref(policies, record, target)
    return ok({
        "policy_id": policy_id,
        "base": base_ref,
        "target": target_ref,
        "changes": diff_documents(base_doc, target_doc),
    })


@router.get("/v1/policies/{policy_id}/versions/{version}")
def get_version(
    policy_id: str,
    version: int,
    api_key: str = Depends(require_read_key),
    policies: PolicyStore = Depends(get_policy_store),
) -> dict[str, Any]:
    _require_policy(policies, policy_id)
    record = policies.get_version(policy_id, version)
    if record is None:
        raise ApiError(404, "not_found", f"version {version} not found for {policy_id}")
    return ok(record)
