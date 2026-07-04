"""Decision engine service boundary (contract §8).

`POST /v1/decisions` evaluates a persisted payment with the
deterministic SDK `DecisionEngine`, records an immutable decision,
applies the matching payment state transition, and opens a review row
when the verdict is `needs_review`. Evaluation uses the environment's
active published policy version (PZ-022); when none is published yet,
the default `PaymentPolicy` applies and `policy_version` is null.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from paiziq import PaymentRequest
from paiziq.engine import DecisionEngine
from pydantic import BaseModel, Field

from audit import AuditLog
from auth import actor_for, require_ingest_key, require_read_key, settings
from deps import (
    get_audit_log,
    get_decision_store,
    get_event_router,
    get_payment_store,
    get_policy_store,
    get_review_store,
)
from envelope import ApiError, list_meta, ok
from policy_doc import to_policy
from event_router import EventRouter
from stores.decisions import DecisionStore, ReviewStore
from stores.payments import PaymentStore
from stores.policies import PolicyStore

router = APIRouter(tags=["decisions"])

# Payment states an engine evaluation may start from, and where each
# verdict lands (contract §7/§8).
_EVALUABLE_STATES = frozenset({"proposed", "needs_review"})
_VERDICT_TO_STATE = {
    "approved": "approved",
    "needs_review": "needs_review",
    "rejected": "rejected",
}


class DecisionCreate(BaseModel):
    payment_id: str = Field(min_length=1)


@router.post("/v1/decisions")
def create_decision(
    body: DecisionCreate,
    api_key: str = Depends(require_ingest_key),
    payments: PaymentStore = Depends(get_payment_store),
    decisions: DecisionStore = Depends(get_decision_store),
    reviews: ReviewStore = Depends(get_review_store),
    policies: PolicyStore = Depends(get_policy_store),
    router_events: EventRouter = Depends(get_event_router),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    payment = payments.get(body.payment_id)
    if payment is None:
        raise ApiError(404, "not_found", f"payment not found: {body.payment_id}")
    if payment["state"] not in _EVALUABLE_STATES:
        raise ApiError(
            409, "invalid_state_transition",
            f"payment in state {payment['state']!r} cannot be evaluated",
        )

    active = policies.active_for_env(payment["env_id"])
    policy_version = active["version"] if active else None
    engine = DecisionEngine(policy=to_policy(active["document"]) if active else None)
    verdict = engine.evaluate(
        PaymentRequest(
            agent_id=payment["agent_id"],
            principal_id=payment["principal_id"],
            merchant=payment["merchant"],
            amount=payment["amount"],
            currency=payment["currency"],
            intent_description=payment["intent_description"],
            request_id=payment["id"],
        )
    )

    record = decisions.create(
        payment["id"], policy_version, verdict.status.value,
        list(verdict.reasons), [f.value for f in verdict.risk_flags],
    )

    actor = actor_for(api_key)
    target_state = _VERDICT_TO_STATE[verdict.status.value]
    if payment["state"] != target_state:
        payments.transition(
            payment["id"], target_state, actor, f"decision {record['id']}"
        )
    review: Optional[dict[str, Any]] = None
    if verdict.status.value == "needs_review":
        review = reviews.open(payment["id"], record["id"], settings.review_sla_ms)

    router_events.dispatch(
        payment["env_id"], "decision.created",
        {"decision_id": record["id"], "payment_id": payment["id"],
         "verdict": record["verdict"], "policy_version": policy_version},
    )
    if verdict.status.value == "needs_review":
        router_events.dispatch(
            payment["env_id"], "review.assigned",
            {"review_id": review["id"] if review else None, "payment_id": payment["id"],
             "decision_id": record["id"]},
        )

    audit.record(
        actor, "decision.create", record["id"],
        {"payment_id": payment["id"], "verdict": record["verdict"],
         "review_id": review["id"] if review else None},
    )
    return ok({**record, "review_id": review["id"] if review else None})


@router.get("/v1/decisions")
def list_decisions(
    api_key: str = Depends(require_read_key),
    payment_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    decisions: DecisionStore = Depends(get_decision_store),
) -> dict[str, Any]:
    items, total = decisions.list(payment_id, limit, offset)
    return ok(items, meta=list_meta(total, limit, offset))


@router.get("/v1/decisions/{decision_id}")
def get_decision(
    decision_id: str,
    api_key: str = Depends(require_read_key),
    decisions: DecisionStore = Depends(get_decision_store),
) -> dict[str, Any]:
    record = decisions.get(decision_id)
    if record is None:
        raise ApiError(404, "not_found", f"decision not found: {decision_id}")
    return ok(record)
