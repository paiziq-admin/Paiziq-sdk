"""Paiziq 4-Way Match audit.

Pre-execution verification across four dimensions:

1. Identity Match    — the principal on the request matches the mandate holder.
2. Intent Match      — the transaction stays inside the mandate the principal
                       granted (amount bounds, merchant scope, expiry).
3. Policy Match      — the decision engine verdict permits execution.
4. Transaction Match — the payload about to hit the gateway equals what was
                       reviewed (no post-approval tampering).

A transaction proceeds only if all four dimensions pass.
"""

from __future__ import annotations

import time
from typing import Optional

from ..models import (
    AuditCheck,
    AuditDimension,
    Decision,
    DecisionStatus,
    FourWayAuditResult,
    PaymentRequest,
)


class FourWayAuditor:
    def run(
        self,
        request: PaymentRequest,
        decision: Decision,
        reviewed_snapshot: Optional[dict] = None,
    ) -> FourWayAuditResult:
        checks = [
            self._identity(request),
            self._intent(request),
            self._policy(decision),
            self._transaction(request, decision, reviewed_snapshot),
        ]
        return FourWayAuditResult(checks=checks)

    # 1 ─ Identity
    def _identity(self, request: PaymentRequest) -> AuditCheck:
        if request.mandate is None:
            return AuditCheck(
                AuditDimension.IDENTITY, True,
                "No mandate attached; identity binding not enforced (configure a mandate to enforce)",
            )
        ok = (
            request.mandate.principal_id == request.principal_id
            and request.mandate.agent_id == request.agent_id
        )
        return AuditCheck(
            AuditDimension.IDENTITY, ok,
            "Principal and agent match the mandate" if ok
            else "Request principal/agent does not match the mandate holder",
        )

    # 2 ─ Intent
    def _intent(self, request: PaymentRequest) -> AuditCheck:
        m = request.mandate
        if m is None:
            return AuditCheck(AuditDimension.INTENT, True, "No mandate attached; intent bound only by policy")
        problems: list[str] = []
        if m.expires_at_ms is not None and time.time() * 1000 > m.expires_at_ms:
            problems.append("mandate expired")
        if m.max_amount is not None and request.amount > m.max_amount:
            problems.append(f"amount {request.amount:.2f} exceeds mandate cap {m.max_amount:.2f}")
        if m.currency and request.currency.upper() != m.currency.upper():
            problems.append(f"currency {request.currency} differs from mandate currency {m.currency}")
        if m.allowed_merchants is not None and request.merchant.strip().lower() not in {
            x.strip().lower() for x in m.allowed_merchants
        }:
            problems.append(f"merchant '{request.merchant}' outside mandate scope")
        ok = not problems
        return AuditCheck(
            AuditDimension.INTENT, ok,
            "Transaction is within the principal's mandate" if ok else "; ".join(problems),
        )

    # 3 ─ Policy
    def _policy(self, decision: Decision) -> AuditCheck:
        ok = decision.status is DecisionStatus.APPROVED
        return AuditCheck(
            AuditDimension.POLICY, ok,
            "Decision engine approved the payment" if ok
            else f"Decision engine verdict is '{decision.status.value}'",
        )

    # 4 ─ Transaction
    def _transaction(
        self,
        request: PaymentRequest,
        decision: Decision,
        reviewed_snapshot: Optional[dict],
    ) -> AuditCheck:
        if decision.request_id != request.request_id:
            return AuditCheck(
                AuditDimension.TRANSACTION, False,
                "Decision was issued for a different payment request",
            )
        if reviewed_snapshot is None:
            return AuditCheck(
                AuditDimension.TRANSACTION, True,
                "No reviewed snapshot stored; identifier binding only",
            )
        current = transaction_snapshot(request)
        diffs = [k for k in current if reviewed_snapshot.get(k) != current[k]]
        ok = not diffs
        return AuditCheck(
            AuditDimension.TRANSACTION, ok,
            "Payload matches the reviewed transaction" if ok
            else f"Payload changed after review: {', '.join(diffs)}",
        )


def transaction_snapshot(request: PaymentRequest) -> dict:
    """Canonical fields captured at review time and re-verified at execution."""
    return {
        "merchant": request.merchant.strip().lower(),
        "amount": round(request.amount, 2),
        "currency": request.currency.upper(),
        "category": request.category.strip().lower(),
        "principal_id": request.principal_id,
        "agent_id": request.agent_id,
    }
