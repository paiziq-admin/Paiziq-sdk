"""Webhook endpoint management (PZ-076/PZ-077)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from audit import AuditLog
from auth import actor_for, require_admin_key, require_read_key
from deps import get_audit_log, get_org_store, get_webhook_store
from envelope import ApiError, list_meta, ok
from stores.orgs import OrgStore
from stores.webhooks import WebhookStore

router = APIRouter(tags=["webhooks"])


class EndpointCreate(BaseModel):
    env_id: str = Field(min_length=1)
    url: str = Field(min_length=8)
    events: list[str] = Field(default_factory=lambda: ["*"])


class EndpointPatch(BaseModel):
    url: Optional[str] = Field(default=None, min_length=8)
    events: Optional[list[str]] = None
    status: Optional[Literal["active", "disabled"]] = None


@router.post("/v1/webhook-endpoints")
def create_endpoint(
    body: EndpointCreate,
    api_key: str = Depends(require_admin_key),
    webhooks: WebhookStore = Depends(get_webhook_store),
    orgs: OrgStore = Depends(get_org_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    if orgs.get_environment(body.env_id) is None:
        raise ApiError(404, "not_found", f"environment not found: {body.env_id}")
    record, secret = webhooks.create_endpoint(body.env_id, body.url.strip(), body.events)
    audit.record(
        actor_for(api_key), "webhook_endpoint.create", record["id"],
        {"env_id": body.env_id, "url": record["url"], "events": record["events"]},
    )
    return ok({**record, "secret": secret})


@router.get("/v1/webhook-endpoints")
def list_endpoints(
    api_key: str = Depends(require_read_key),
    env_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    webhooks: WebhookStore = Depends(get_webhook_store),
) -> dict[str, Any]:
    items, total = webhooks.list_endpoints(env_id, limit, offset)
    return ok(items, meta=list_meta(total, limit, offset))


@router.patch("/v1/webhook-endpoints/{endpoint_id}")
def patch_endpoint(
    endpoint_id: str,
    body: EndpointPatch,
    api_key: str = Depends(require_admin_key),
    webhooks: WebhookStore = Depends(get_webhook_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    if webhooks.get_endpoint(endpoint_id) is None:
        raise ApiError(404, "not_found", f"webhook endpoint not found: {endpoint_id}")
    record = webhooks.update_endpoint(
        endpoint_id, url=body.url, events=body.events, status=body.status
    )
    assert record is not None
    audit.record(actor_for(api_key), "webhook_endpoint.update", endpoint_id, body.model_dump())
    return ok(record)


@router.get("/v1/webhook-deliveries")
def list_deliveries(
    api_key: str = Depends(require_read_key),
    endpoint_id: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    webhooks: WebhookStore = Depends(get_webhook_store),
) -> dict[str, Any]:
    items, total = webhooks.list_deliveries(endpoint_id, state, limit, offset)
    return ok(items, meta=list_meta(total, limit, offset))


@router.get("/v1/webhook-deliveries/{delivery_id}")
def get_delivery(
    delivery_id: str,
    api_key: str = Depends(require_read_key),
    webhooks: WebhookStore = Depends(get_webhook_store),
) -> dict[str, Any]:
    record = webhooks.get_delivery(delivery_id)
    if record is None:
        raise ApiError(404, "not_found", f"delivery not found: {delivery_id}")
    return ok({**record, "logs": webhooks.delivery_logs(delivery_id)})
