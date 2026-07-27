"""Human-review queue and action endpoints (PZ-101).

Queue reads require read scope. Assignment and action mutations require
review scope (or admin), enforce optimistic ownership, and record every
operator action in the append-only audit log.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from audit import AuditLog
from auth import AuthContext, actor_for, require_read_context, require_review_key
from deps import (
    get_audit_log,
    get_event_router,
    get_payment_store,
    get_review_store,
)
from envelope import ApiError, list_meta, ok
from event_router import EventRouter
from ids import now_ms
from stores.decisions import (
    ReviewAssignmentConflict,
    ReviewNotFound,
    ReviewPaymentConflict,
    ReviewStateConflict,
    ReviewStore,
)
from stores.payments import PaymentStore

router = APIRouter(tags=["reviews"])

ReviewState = Literal["open", "approved", "rejected"]
ReviewPriority = Literal["low", "normal", "high", "urgent"]


class ReviewAssignment(BaseModel):
    reviewer_id: str = Field(min_length=1, max_length=200)


class ReviewRelease(ReviewAssignment):
    note: Optional[str] = Field(default=None, max_length=2000)


class ReviewAction(ReviewAssignment):
    note: str = Field(min_length=1, max_length=2000)


class ReviewEscalation(ReviewAction):
    priority: Literal["high", "urgent"] = "urgent"


def _clean(value: str, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ApiError(422, "validation_error", f"{field} must not be blank")
    return cleaned


def _scoped_env(context: AuthContext, requested_env_id: Optional[str]) -> Optional[str]:
    if context.env_id is None:
        return requested_env_id
    if requested_env_id is not None and requested_env_id != context.env_id:
        raise ApiError(403, "forbidden", "API key cannot access that environment")
    return context.env_id


def _authorize_review(
    review_id: str,
    context: AuthContext,
    reviews: ReviewStore,
    payments: PaymentStore,
) -> dict[str, Any]:
    record = reviews.get(review_id)
    if record is None:
        raise ApiError(404, "not_found", f"review not found: {review_id}")
    payment = payments.get(record["payment_id"])
    if payment is None:
        raise ApiError(409, "conflict", "review payment no longer exists")
    if context.env_id is not None and payment["env_id"] != context.env_id:
        raise ApiError(403, "forbidden", "API key cannot access that environment")
    return record


def _reviewer_identity(context: AuthContext, requested_reviewer_id: str) -> str:
    requested = _clean(requested_reviewer_id, "reviewer_id")
    if context.is_bootstrap:
        return requested
    if not context.key_name:
        raise ApiError(403, "forbidden", "API key has no managed reviewer identity")
    if requested != context.key_name:
        raise ApiError(
            403,
            "forbidden",
            "reviewer_id must match the authenticated API key name",
        )
    return context.key_name


def _context_reviewer(context: AuthContext, current_owner: Optional[str]) -> str:
    if context.is_bootstrap:
        return current_owner or "bootstrap-admin"
    if not context.key_name:
        raise ApiError(403, "forbidden", "API key has no managed reviewer identity")
    return context.key_name


def _run(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return operation()
    except ReviewNotFound as exc:
        raise ApiError(404, "not_found", f"review not found: {exc}") from exc
    except ReviewStateConflict as exc:
        raise ApiError(
            409,
            "review_not_open",
            f"review action requires open state; current state is {exc.state}",
        ) from exc
    except ReviewAssignmentConflict as exc:
        owner = exc.reviewer_id or "unassigned"
        raise ApiError(
            409,
            "review_assignment_conflict",
            f"review is assigned to {owner}",
        ) from exc
    except ReviewPaymentConflict as exc:
        raise ApiError(
            409,
            "invalid_state_transition",
            f"review payment cannot be resolved from state {exc.state}",
        ) from exc


def _enrich(record: dict[str, Any], payments: PaymentStore) -> dict[str, Any]:
    deadline = record.get("sla_deadline_ms")
    remaining = deadline - now_ms() if deadline is not None else None
    return {
        **record,
        "sla_remaining_ms": remaining,
        "sla_breached": record["state"] == "open"
        and remaining is not None
        and remaining <= 0,
        "payment": payments.get(record["payment_id"]),
    }


def _record_action(
    record: dict[str, Any],
    action: str,
    context: AuthContext,
    audit: AuditLog,
    events: EventRouter,
    payments: PaymentStore,
    *,
    actor_reviewer_id: str,
    note: Optional[str] = None,
    previous_reviewer_id: Optional[str] = None,
) -> dict[str, Any]:
    payment = payments.get(record["payment_id"])
    assert payment is not None
    detail = {
        "payment_id": record["payment_id"],
        "reviewer_id": record["reviewer_id"],
        "actor_reviewer_id": actor_reviewer_id,
        "previous_reviewer_id": previous_reviewer_id,
        "note": note,
        "state": record["state"],
        "priority": record["priority"],
        "last_action": record["last_action"],
    }
    audit.record(actor_for(context), f"review.{action}", record["id"], detail)
    events.dispatch(
        payment["env_id"],
        f"review.{action}",
        {"review_id": record["id"], **detail},
    )
    return ok(_enrich(record, payments))


@router.get("/v1/reviews")
def list_reviews(
    context: AuthContext = Depends(require_read_context),
    state: Optional[ReviewState] = Query(default=None),
    env_id: Optional[str] = Query(default=None),
    reviewer_id: Optional[str] = Query(default=None),
    priority: Optional[ReviewPriority] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    reviews: ReviewStore = Depends(get_review_store),
    payments: PaymentStore = Depends(get_payment_store),
) -> dict[str, Any]:
    effective_env_id = _scoped_env(context, env_id)
    items, total = reviews.list(
        state,
        effective_env_id,
        reviewer_id,
        priority,
        limit,
        offset,
    )
    return ok(
        [_enrich(item, payments) for item in items],
        meta=list_meta(total, limit, offset),
    )


@router.get("/v1/reviews/identity")
def get_review_identity(
    context: AuthContext = Depends(require_read_context),
) -> dict[str, Any]:
    return ok(
        {
            "reviewer_id": context.key_name,
            "role": context.role,
            "env_id": context.env_id,
            "managed_identity": context.managed_identity,
        }
    )


@router.get("/v1/reviews/{review_id}")
def get_review(
    review_id: str,
    context: AuthContext = Depends(require_read_context),
    reviews: ReviewStore = Depends(get_review_store),
    payments: PaymentStore = Depends(get_payment_store),
) -> dict[str, Any]:
    record = _authorize_review(review_id, context, reviews, payments)
    return ok(_enrich(record, payments))


@router.post("/v1/reviews/{review_id}/claim")
def claim_review(
    review_id: str,
    body: ReviewAssignment,
    context: AuthContext = Depends(require_review_key),
    reviews: ReviewStore = Depends(get_review_store),
    payments: PaymentStore = Depends(get_payment_store),
    audit: AuditLog = Depends(get_audit_log),
    events: EventRouter = Depends(get_event_router),
) -> dict[str, Any]:
    before = _authorize_review(review_id, context, reviews, payments)
    reviewer_id = _reviewer_identity(context, body.reviewer_id)
    record = _run(lambda: reviews.claim(review_id, reviewer_id))
    return _record_action(
        record,
        "claimed",
        context,
        audit,
        events,
        payments,
        actor_reviewer_id=reviewer_id,
        previous_reviewer_id=before["reviewer_id"],
    )


@router.post("/v1/reviews/{review_id}/release")
def release_review(
    review_id: str,
    body: ReviewRelease,
    context: AuthContext = Depends(require_review_key),
    reviews: ReviewStore = Depends(get_review_store),
    payments: PaymentStore = Depends(get_payment_store),
    audit: AuditLog = Depends(get_audit_log),
    events: EventRouter = Depends(get_event_router),
) -> dict[str, Any]:
    before = _authorize_review(review_id, context, reviews, payments)
    reviewer_id = _reviewer_identity(context, body.reviewer_id)
    note = body.note.strip() if body.note else None
    record = _run(lambda: reviews.release(review_id, reviewer_id, note))
    return _record_action(
        record,
        "released",
        context,
        audit,
        events,
        payments,
        actor_reviewer_id=reviewer_id,
        note=note,
        previous_reviewer_id=before["reviewer_id"],
    )


@router.post("/v1/reviews/{review_id}/reassign")
def reassign_review(
    review_id: str,
    body: ReviewAction,
    context: AuthContext = Depends(require_review_key),
    reviews: ReviewStore = Depends(get_review_store),
    payments: PaymentStore = Depends(get_payment_store),
    audit: AuditLog = Depends(get_audit_log),
    events: EventRouter = Depends(get_event_router),
) -> dict[str, Any]:
    before = _authorize_review(review_id, context, reviews, payments)
    reviewer_id = _clean(body.reviewer_id, "reviewer_id")
    note = _clean(body.note, "note")
    actor_reviewer_id = _context_reviewer(context, before["reviewer_id"])
    record = _run(
        lambda: reviews.reassign(
            review_id,
            actor_reviewer_id,
            reviewer_id,
            note,
            allow_override=context.is_admin,
        )
    )
    return _record_action(
        record,
        "reassigned",
        context,
        audit,
        events,
        payments,
        actor_reviewer_id=actor_reviewer_id,
        note=note,
        previous_reviewer_id=before["reviewer_id"],
    )


@router.post("/v1/reviews/{review_id}/request-more-info")
def request_more_info(
    review_id: str,
    body: ReviewAction,
    context: AuthContext = Depends(require_review_key),
    reviews: ReviewStore = Depends(get_review_store),
    payments: PaymentStore = Depends(get_payment_store),
    audit: AuditLog = Depends(get_audit_log),
    events: EventRouter = Depends(get_event_router),
) -> dict[str, Any]:
    before = _authorize_review(review_id, context, reviews, payments)
    reviewer_id = _reviewer_identity(context, body.reviewer_id)
    note = _clean(body.note, "note")
    record = _run(
        lambda: reviews.annotate(
            review_id,
            reviewer_id,
            note,
            "requested_info",
        )
    )
    return _record_action(
        record,
        "requested_info",
        context,
        audit,
        events,
        payments,
        actor_reviewer_id=reviewer_id,
        note=note,
        previous_reviewer_id=before["reviewer_id"],
    )


@router.post("/v1/reviews/{review_id}/escalate")
def escalate_review(
    review_id: str,
    body: ReviewEscalation,
    context: AuthContext = Depends(require_review_key),
    reviews: ReviewStore = Depends(get_review_store),
    payments: PaymentStore = Depends(get_payment_store),
    audit: AuditLog = Depends(get_audit_log),
    events: EventRouter = Depends(get_event_router),
) -> dict[str, Any]:
    before = _authorize_review(review_id, context, reviews, payments)
    reviewer_id = _reviewer_identity(context, body.reviewer_id)
    note = _clean(body.note, "note")
    record = _run(
        lambda: reviews.annotate(
            review_id,
            reviewer_id,
            note,
            "escalated",
            body.priority,
        )
    )
    return _record_action(
        record,
        "escalated",
        context,
        audit,
        events,
        payments,
        actor_reviewer_id=reviewer_id,
        note=note,
        previous_reviewer_id=before["reviewer_id"],
    )


def _resolve(
    review_id: str,
    body: ReviewAction,
    outcome: Literal["approved", "rejected"],
    context: AuthContext,
    reviews: ReviewStore,
    payments: PaymentStore,
    audit: AuditLog,
    events: EventRouter,
) -> dict[str, Any]:
    before = _authorize_review(review_id, context, reviews, payments)
    reviewer_id = _reviewer_identity(context, body.reviewer_id)
    note = _clean(body.note, "note")
    record = _run(lambda: reviews.resolve(review_id, reviewer_id, note, outcome))
    return _record_action(
        record,
        outcome,
        context,
        audit,
        events,
        payments,
        actor_reviewer_id=reviewer_id,
        note=note,
        previous_reviewer_id=before["reviewer_id"],
    )


@router.post("/v1/reviews/{review_id}/approve")
def approve_review(
    review_id: str,
    body: ReviewAction,
    context: AuthContext = Depends(require_review_key),
    reviews: ReviewStore = Depends(get_review_store),
    payments: PaymentStore = Depends(get_payment_store),
    audit: AuditLog = Depends(get_audit_log),
    events: EventRouter = Depends(get_event_router),
) -> dict[str, Any]:
    return _resolve(
        review_id,
        body,
        "approved",
        context,
        reviews,
        payments,
        audit,
        events,
    )


@router.post("/v1/reviews/{review_id}/decline")
def decline_review(
    review_id: str,
    body: ReviewAction,
    context: AuthContext = Depends(require_review_key),
    reviews: ReviewStore = Depends(get_review_store),
    payments: PaymentStore = Depends(get_payment_store),
    audit: AuditLog = Depends(get_audit_log),
    events: EventRouter = Depends(get_event_router),
) -> dict[str, Any]:
    return _resolve(
        review_id,
        body,
        "rejected",
        context,
        reviews,
        payments,
        audit,
        events,
    )


@router.post("/v1/reviews/{review_id}/reject")
def reject_review(
    review_id: str,
    body: ReviewAction,
    context: AuthContext = Depends(require_review_key),
    reviews: ReviewStore = Depends(get_review_store),
    payments: PaymentStore = Depends(get_payment_store),
    audit: AuditLog = Depends(get_audit_log),
    events: EventRouter = Depends(get_event_router),
) -> dict[str, Any]:
    """Backward-compatible spelling retained from the original contract."""
    return _resolve(
        review_id,
        body,
        "rejected",
        context,
        reviews,
        payments,
        audit,
        events,
    )
