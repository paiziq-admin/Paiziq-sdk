"""Claim, report, reconcile, and inspect one hosted payment execution."""

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends
from paiziq.execution import ExecutionConflict
from pydantic import BaseModel, Field

from auth import (
    AuthContext,
    actor_for,
    require_admin_context,
    require_ingest_context,
    require_read_context,
)
from deps import get_execution_store, get_payment_store
from envelope import ApiError, ok
from routers.payments import _authorize_env
from stores.executions import ExecutionStore
from stores.payments import PaymentStore

router = APIRouter(tags=["executions"])


class ExecutionClaim(BaseModel):
    request_digest: Optional[str] = Field(default=None, min_length=64, max_length=64)


class ExecutionReport(BaseModel):
    claim_token: str = Field(min_length=1, max_length=200)
    status: Literal["submitted", "confirmed", "failed", "unknown"]
    gateway_reference: Optional[str] = Field(default=None, min_length=1, max_length=500)
    error: Optional[str] = Field(default=None, max_length=2000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExecutionReconcile(BaseModel):
    status: Literal["confirmed", "failed", "unknown"]
    gateway_reference: Optional[str] = Field(default=None, min_length=1, max_length=500)
    error: Optional[str] = Field(default=None, max_length=2000)
    evidence: dict[str, Any] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)


def _payment(payment_id, context, payments):
    payment = payments.get(payment_id)
    if payment is None:
        raise ApiError(404, "not_found", "Payment not found")
    _authorize_env(context, payment["env_id"])
    return payment


def _run(operation):
    try:
        return operation()
    except ExecutionConflict as exc:
        raise ApiError(409, "execution_conflict", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc)) from exc


@router.get("/v1/payments/{payment_id}/execution")
def get_execution(
    payment_id: str,
    context: AuthContext = Depends(require_read_context),
    payments: PaymentStore = Depends(get_payment_store),
    executions: ExecutionStore = Depends(get_execution_store),
):
    return ok(executions.evidence(_payment(payment_id, context, payments)))


@router.post("/v1/payments/{payment_id}/execution/claim")
def claim_execution(
    payment_id: str,
    body: ExecutionClaim,
    context: AuthContext = Depends(require_ingest_context),
    payments: PaymentStore = Depends(get_payment_store),
    executions: ExecutionStore = Depends(get_execution_store),
):
    _payment(payment_id, context, payments)
    return ok(
        _run(
            lambda: executions.claim(
                payment_id, actor_for(context), body.request_digest
            )
        )
    )


@router.post("/v1/payments/{payment_id}/execution/report")
def report_execution(
    payment_id: str,
    body: ExecutionReport,
    context: AuthContext = Depends(require_ingest_context),
    payments: PaymentStore = Depends(get_payment_store),
    executions: ExecutionStore = Depends(get_execution_store),
):
    _payment(payment_id, context, payments)
    return ok(
        _run(
            lambda: executions.report(
                payment_id,
                actor_for(context),
                body.status,
                token=body.claim_token,
                reference=body.gateway_reference,
                error=body.error,
                evidence=body.evidence,
            )
        )
    )


@router.post("/v1/payments/{payment_id}/execution/reconcile")
def reconcile_execution(
    payment_id: str,
    body: ExecutionReconcile,
    context: AuthContext = Depends(require_admin_context),
    payments: PaymentStore = Depends(get_payment_store),
    executions: ExecutionStore = Depends(get_execution_store),
):
    _payment(payment_id, context, payments)
    return ok(
        _run(
            lambda: executions.report(
                payment_id,
                actor_for(context),
                body.status,
                reference=body.gateway_reference,
                error=body.error,
                evidence={**body.evidence, "reason": body.reason},
                reconcile=True,
            )
        )
    )
