"""PaiziqSDK — the developer-facing facade.

Three methods cover the whole lifecycle:

    sdk = PaiziqSDK(policy=PaymentPolicy(...))
    decision = sdk.review_payment(request)      # approved | needs_review | rejected
    result   = sdk.execute_payment(request)     # 4-way audit + gateway charge
    trail    = sdk.get_audit_trail(request_id)  # immutable event history

No framework concepts leak into this interface: inputs are plain
dataclasses, outputs are plain dataclasses with `.to_dict()`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .audit import AuditStore, InMemoryAuditStore, MockGateway, PaymentGateway
from .engine.audit4 import FourWayAuditor, transaction_snapshot
from .engine.engine import DecisionEngine
from .engine.policy import BudgetTracker, PaymentPolicy
from .models import (
    AuditRecord,
    Decision,
    DecisionStatus,
    ExecutionResult,
    PaymentRequest,
)
from .notifications import NotificationRouter, Notifier
from .tracing.tracer import ConsoleExporter, Exporter, HTTPExporter, Tracer

logger = logging.getLogger("paiziq.sdk")


class PaiziqSDK:
    """Entry point for instrumenting a payment agent with Paiziq auditing."""

    def __init__(
        self,
        policy: Optional[PaymentPolicy] = None,
        api_key: Optional[str] = None,
        dashboard_endpoint: Optional[str] = None,
        gateway: Optional[PaymentGateway] = None,
        audit_store: Optional[AuditStore] = None,
        notifiers: Optional[list[Notifier]] = None,
        exporters: Optional[list[Exporter]] = None,
        budget_tracker: Optional[BudgetTracker] = None,
        service_name: str = "payment-agent",
        require_review_approval: bool = True,
    ) -> None:
        api_key = api_key or os.getenv("PAIZIQ_API_KEY")
        dashboard_endpoint = dashboard_endpoint or os.getenv("PAIZIQ_ENDPOINT")

        if exporters is None:
            exporters = (
                [HTTPExporter(dashboard_endpoint, api_key or "")]
                if dashboard_endpoint
                else [ConsoleExporter()]
            )

        self.budget_tracker = budget_tracker or BudgetTracker()
        self.engine = DecisionEngine(policy=policy, budget_tracker=self.budget_tracker)
        self.auditor = FourWayAuditor()
        self.tracer = Tracer(exporters=exporters, service_name=service_name)
        self.gateway: PaymentGateway = gateway or MockGateway()
        self.audit_store: AuditStore = audit_store or InMemoryAuditStore()
        self.notifications = NotificationRouter(notifiers=notifiers)
        self.require_review_approval = require_review_approval

        # request_id -> (Decision, reviewed transaction snapshot)
        self._reviewed: dict[str, tuple[Decision, dict[str, Any]]] = {}
        # request_ids a human approved after needs_review
        self._review_overrides: set[str] = set()

    # ── public API ───────────────────────────────────────────────────────

    def review_payment(self, request: PaymentRequest) -> Decision:
        """Evaluate the payment against policy. Side-effect free w.r.t. money.

        Returns a Decision with status approved/needs_review/rejected,
        human-readable reasons, and machine-readable risk flags. The
        verdict, reasons, and flags are traced to the dashboard and an
        audit record is appended.
        """
        with self.tracer.span(
            "paiziq.review_payment",
            {
                "paiziq.request_id": request.request_id,
                "payment.merchant": request.merchant,
                "payment.amount": request.amount,
                "payment.currency": request.currency,
                "agent.id": request.agent_id,
            },
        ) as span:
            decision = self.engine.evaluate(request)
            self._reviewed[request.request_id] = (decision, transaction_snapshot(request))
            span.set_attribute("paiziq.decision", decision.status.value)
            span.set_attribute("paiziq.risk_flags", [f.value for f in decision.risk_flags])
            span.add_event("decision", decision.to_dict())

            self._record(
                "review", request, decision.to_dict() | {"intent": request.intent_description}
            )
            self.notifications.notify_decision(request, decision)
            return decision

    def approve_review(self, request_id: str, reviewer_id: str) -> None:
        """Record a human approval for a payment that was flagged needs_review."""
        self._review_overrides.add(request_id)
        self._record_raw(
            "override",
            request_id,
            {"action": "review_approved", "reviewer_id": reviewer_id},
        )

    def execute_payment(self, request: PaymentRequest) -> ExecutionResult:
        """Run the 4-Way Match audit and, if it passes, charge the gateway.

        Reviews the payment first if `review_payment` was not already
        called for this request. needs_review verdicts execute only
        after `approve_review` (unless require_review_approval=False).
        """
        with self.tracer.span(
            "paiziq.execute_payment", {"paiziq.request_id": request.request_id}
        ) as span:
            stored = self._reviewed.get(request.request_id)
            if stored is None:
                decision = self.review_payment(request)
                stored = self._reviewed[request.request_id]
            decision, snapshot = stored

            effective = decision
            if decision.status is DecisionStatus.NEEDS_REVIEW:
                if request.request_id in self._review_overrides or not self.require_review_approval:
                    effective = Decision(
                        request_id=decision.request_id,
                        status=DecisionStatus.APPROVED,
                        reasons=decision.reasons + ["Human reviewer approved execution"],
                        risk_flags=decision.risk_flags,
                        rule_results=decision.rule_results,
                    )

            audit = self.auditor.run(request, effective, reviewed_snapshot=snapshot)
            effective.four_way_audit = audit
            span.set_attribute("paiziq.four_way_passed", audit.passed)
            span.add_event(
                "four_way_audit",
                {"checks": [{"dim": c.dimension.value, "passed": c.passed, "detail": c.detail} for c in audit.checks]},
            )

            if not audit.passed:
                failed = ", ".join(d.value for d in audit.failed_dimensions)
                result = ExecutionResult(
                    request_id=request.request_id,
                    decision_id=decision.decision_id,
                    executed=False,
                    gateway=self.gateway.name,
                    error=f"4-way audit failed: {failed}",
                )
                self._record("execution", request, result.to_dict() | {"audit_failed": failed})
                span.set_attribute("paiziq.executed", False)
                return result

            try:
                reference = self.gateway.charge(request)
                self.budget_tracker.commit(request.agent_id, request.amount)
                result = ExecutionResult(
                    request_id=request.request_id,
                    decision_id=decision.decision_id,
                    executed=True,
                    gateway=self.gateway.name,
                    gateway_reference=reference,
                )
            except Exception as exc:
                result = ExecutionResult(
                    request_id=request.request_id,
                    decision_id=decision.decision_id,
                    executed=False,
                    gateway=self.gateway.name,
                    error=str(exc),
                )

            span.set_attribute("paiziq.executed", result.executed)
            self._record("execution", request, result.to_dict())
            return result

    def get_audit_trail(self, request_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        """Return the immutable audit trail (newest last) as plain dicts."""
        return [r.to_dict() for r in self.audit_store.query(request_id=request_id, limit=limit)]

    def shutdown(self) -> None:
        """Flush exporters. Call on process exit (also wired via atexit)."""
        self.tracer.shutdown()

    # ── internals ────────────────────────────────────────────────────────

    def _record(self, event_type: str, request: PaymentRequest, payload: dict[str, Any]) -> None:
        self._record_raw(event_type, request.request_id, payload)

    def _record_raw(self, event_type: str, request_id: str, payload: dict[str, Any]) -> None:
        self.audit_store.append(
            AuditRecord(
                event_type=event_type,
                request_id=request_id,
                payload=payload,
                trace_id=self.tracer.current_trace_id(),
            )
        )
