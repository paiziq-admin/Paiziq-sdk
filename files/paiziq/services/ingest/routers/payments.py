"""Payment proposal endpoints and state transitions (contract §7).

`POST /v1/payments` honors the `Idempotency-Key` header: replays return
the original payment instead of creating a duplicate. Transitions are
validated against the server-side state machine and recorded in the
append-only history returned by `GET /v1/payments/{id}`.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from audit import AuditLog
from auth import actor_for, require_ingest_key, require_read_key
from deps import get_agent_store, get_audit_log, get_org_store, get_payment_store
from envelope import ApiError, list_meta, ok
from stores.agents import AgentStore
from stores.orgs import OrgStore
from stores.payments import InvalidTransition, PaymentStore

router = APIRouter(tags=["payments"])

State = Literal["proposed", "approved", "needs_review", "rejected", "executed", "failed"]


class PaymentCreate(BaseModel):
    env_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1, max_length=200)
    merchant: str = Field(min_length=1, max_length=500)
    amount: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    intent_description: str = Field(default="", max_length=2000)
    request_id: Optional[str] = Field(default=None, max_length=200)


class TransitionIn(BaseModel):
    to: Literal["approved", "needs_review", "rejected", "executed", "failed"]
    reason: Optional[str] = Field(default=None, max_length=2000)


@router.post("/v1/payments")
def create_payment(
    body: PaymentCreate,
    api_key: str = Depends(require_ingest_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    payments: PaymentStore = Depends(get_payment_store),
    orgs: OrgStore = Depends(get_org_store),
    agents: AgentStore = Depends(get_agent_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    if idempotency_key:
        existing = payments.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return ok(existing)
    if orgs.get_environment(body.env_id) is None:
        raise ApiError(404, "not_found", f"environment not found: {body.env_id}")
    agent = agents.get(body.agent_id)
    if agent is None:
        raise ApiError(404, "not_found", f"agent not found: {body.agent_id}")
    if agent["env_id"] != body.env_id:
        raise ApiError(
            422, "validation_error",
            f"agent {body.agent_id} does not belong to environment {body.env_id}",
        )
    payment = payments.create(
        body.env_id, body.agent_id, body.principal_id.strip(), body.merchant.strip(),
        body.amount, body.currency.upper(), body.intent_description,
        body.request_id, idempotency_key,
    )
    audit.record(
        actor_for(api_key), "payment.create", payment["id"],
        {"env_id": body.env_id, "agent_id": body.agent_id, "amount": body.amount,
         "currency": payment["currency"], "merchant": payment["merchant"]},
    )
    return ok(payment)


@router.get("/v1/payments")
def list_payments(
    api_key: str = Depends(require_read_key),
    env_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    state: Optional[State] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    payments: PaymentStore = Depends(get_payment_store),
) -> dict[str, Any]:
    items, total = payments.list(env_id, agent_id, state, limit, offset)
    return ok(items, meta=list_meta(total, limit, offset))


@router.get("/v1/payments/{payment_id}")
def get_payment(
    payment_id: str,
    api_key: str = Depends(require_read_key),
    payments: PaymentStore = Depends(get_payment_store),
) -> dict[str, Any]:
    payment = payments.get(payment_id)
    if payment is None:
        raise ApiError(404, "not_found", f"payment not found: {payment_id}")
    return ok({**payment, "transitions": payments.transitions_for(payment_id)})


@router.post("/v1/payments/{payment_id}/transition")
def transition_payment(
    payment_id: str,
    body: TransitionIn,
    api_key: str = Depends(require_ingest_key),
    payments: PaymentStore = Depends(get_payment_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    if payments.get(payment_id) is None:
        raise ApiError(404, "not_found", f"payment not found: {payment_id}")
    try:
        payment = payments.transition(payment_id, body.to, actor_for(api_key), body.reason)
    except InvalidTransition as exc:
        raise ApiError(
            409, "invalid_state_transition",
            f"cannot transition {exc.from_state} -> {exc.to_state}",
        )
    audit.record(
        actor_for(api_key), "payment.transition", payment_id,
        {"to": body.to, "reason": body.reason},
    )
    return ok(payment)
