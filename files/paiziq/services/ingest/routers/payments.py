"""Payment proposal endpoints and state transitions (contract §7).

`POST /v1/payments` honors the `Idempotency-Key` header: replays return
the original payment instead of creating a duplicate. Transitions are
validated against the server-side state machine and recorded in the
append-only history returned by `GET /v1/payments/{id}`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field, model_validator
from paiziq import PaymentRequest, Mandate
from paiziq.execution import canonical_json, money_decimal

from audit import AuditLog
from auth import AuthContext, actor_for, require_ingest_context, require_read_context
from deps import (
    get_agent_store,
    get_execution_store,
    get_audit_log,
    get_event_router,
    get_org_store,
    get_payment_store,
    get_review_store,
)
from envelope import ApiError, list_meta, ok
from event_router import EventRouter
from stores.executions import ExecutionStore
from stores.agents import AgentStore
from stores.decisions import ReviewStore
from stores.orgs import OrgStore
from stores.payments import InvalidTransition, PaymentStore

router = APIRouter(tags=["payments"])


def _authorize_env(context: AuthContext, env_id: str) -> None:
    if context.env_id is not None and context.env_id != env_id:
        raise ApiError(403, "forbidden", "API key cannot access that environment")


State = Literal[
    "proposed", "approved", "needs_review", "rejected", "executed", "failed"
]
PaymentSort = Literal[
    "created_desc",
    "created_asc",
    "amount_desc",
    "amount_asc",
    "merchant_asc",
]


class PaymentCreate(BaseModel):
    env_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1, max_length=200)
    merchant: str = Field(min_length=1, max_length=500)
    amount: float = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(default="USD", pattern="^[A-Za-z]{3}$")
    intent_description: str = Field(default="", max_length=2000)
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=200)
    category: str = Field(default="general", min_length=1, max_length=200)
    mandate: Optional[dict[str, Any]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_sdk_request(self):
        try:
            mandate = Mandate(**self.mandate) if self.mandate else None
            self.mandate = asdict(mandate) if mandate else None
            money_decimal(self.amount, self.currency)
            canonical_json(self.metadata)
            PaymentRequest(
                agent_id=self.agent_id,
                principal_id=self.principal_id,
                merchant=self.merchant,
                amount=self.amount,
                currency=self.currency,
                mandate=mandate,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        return self


class TransitionIn(BaseModel):
    to: Literal["approved", "needs_review", "rejected", "executed", "failed"]
    reason: Optional[str] = Field(default=None, max_length=2000)


@router.post("/v1/payments")
def create_payment(
    body: PaymentCreate,
    context: AuthContext = Depends(require_ingest_context),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    idempotency_mode: Literal["legacy", "scoped"] = Header(
        default="legacy", alias="Idempotency-Mode"
    ),
    payments: PaymentStore = Depends(get_payment_store),
    orgs: OrgStore = Depends(get_org_store),
    agents: AgentStore = Depends(get_agent_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    _authorize_env(context, body.env_id)
    if orgs.get_environment(body.env_id) is None:
        raise ApiError(404, "not_found", f"environment not found: {body.env_id}")
    agent = agents.get(body.agent_id)
    if agent is None:
        raise ApiError(404, "not_found", f"agent not found: {body.agent_id}")
    if agent["env_id"] != body.env_id:
        raise ApiError(
            422,
            "validation_error",
            f"agent {body.agent_id} does not belong to environment {body.env_id}",
        )
    try:
        payment = payments.create(
            body.env_id,
            body.agent_id,
            body.principal_id,
            body.merchant,
            body.amount,
            body.currency.upper(),
            body.intent_description,
            body.request_id,
            idempotency_key,
            category=body.category,
            mandate=body.mandate,
            metadata=body.metadata,
            scoped_idempotency=idempotency_mode == "scoped",
        )
    except ValueError as exc:
        raise ApiError(
            409,
            str(exc),
            "Idempotency key is already bound to another payload or environment",
        ) from exc
    audit.record(
        actor_for(context),
        "payment.create",
        payment["id"],
        {
            "env_id": body.env_id,
            "agent_id": body.agent_id,
            "amount": body.amount,
            "currency": payment["currency"],
            "merchant": payment["merchant"],
        },
    )
    return ok(payment)


@router.get("/v1/payments")
def list_payments(
    context: AuthContext = Depends(require_read_context),
    env_id: Optional[str] = Query(default=None),
    request_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    state: Optional[State] = Query(default=None),
    currency: Optional[str] = Query(default=None, min_length=3, max_length=3),
    min_amount: Optional[float] = Query(default=None, ge=0),
    max_amount: Optional[float] = Query(default=None, ge=0),
    q: Optional[str] = Query(default=None, min_length=1, max_length=500),
    from_ms: Optional[int] = Query(default=None, ge=0),
    to_ms: Optional[int] = Query(default=None, ge=0),
    sort: PaymentSort = Query(default="created_desc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    payments: PaymentStore = Depends(get_payment_store),
) -> dict[str, Any]:
    if context.env_id is not None:
        _authorize_env(context, env_id or context.env_id)
        env_id = context.env_id
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise ApiError(
            422,
            "validation_error",
            "min_amount must be less than or equal to max_amount",
        )
    if from_ms is not None and to_ms is not None and from_ms > to_ms:
        raise ApiError(
            422, "validation_error", "from_ms must be less than or equal to to_ms"
        )
    items, total = payments.list(
        env_id,
        agent_id,
        state,
        limit,
        offset,
        currency=currency,
        min_amount=min_amount,
        max_amount=max_amount,
        query=q.strip() if q else None,
        from_ms=from_ms,
        to_ms=to_ms,
        sort=sort,
        request_id=request_id,
    )
    return ok(items, meta=list_meta(total, limit, offset))


@router.get("/v1/payments/{payment_id}")
def get_payment(
    payment_id: str,
    context: AuthContext = Depends(require_read_context),
    payments: PaymentStore = Depends(get_payment_store),
) -> dict[str, Any]:
    payment = payments.get(payment_id)
    if payment is None:
        raise ApiError(404, "not_found", f"payment not found: {payment_id}")
    _authorize_env(context, payment["env_id"])
    return ok({**payment, "transitions": payments.transitions_for(payment_id)})


@router.post("/v1/payments/{payment_id}/transition")
def transition_payment(
    payment_id: str,
    body: TransitionIn,
    context: AuthContext = Depends(require_ingest_context),
    payments: PaymentStore = Depends(get_payment_store),
    reviews: ReviewStore = Depends(get_review_store),
    executions: ExecutionStore = Depends(get_execution_store),
    router_events: EventRouter = Depends(get_event_router),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    current = payments.get(payment_id)
    if current is None:
        raise ApiError(404, "not_found", f"payment not found: {payment_id}")
    _authorize_env(context, current["env_id"])
    if (
        current["state"] == "needs_review"
        and body.to in {"approved", "rejected"}
        and reviews.open_for_payment(payment_id) is not None
    ):
        raise ApiError(
            409,
            "review_resolution_required",
            "payment has an open review and must be resolved through the review API",
        )
    try:
        if body.to in {"executed", "failed"}:
            if current["state"] != "approved":
                raise InvalidTransition(current["state"], body.to)
            payment = executions.legacy_transition(
                current, body.to, actor_for(context), body.reason
            )
        else:
            payment = payments.transition(
                payment_id, body.to, actor_for(context), body.reason
            )
    except InvalidTransition as exc:
        raise ApiError(
            409,
            "invalid_state_transition",
            f"cannot transition {exc.from_state} -> {exc.to_state}",
        )
    audit.record(
        actor_for(context),
        "payment.transition",
        payment_id,
        {"to": body.to, "reason": body.reason},
    )
    router_events.dispatch(
        payment["env_id"],
        "payment.updated",
        {"payment_id": payment_id, "state": payment["state"], "to": body.to},
    )
    return ok(payment)
