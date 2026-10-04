"""Payment review and execution with an explicit authoritative ledger.

A dashboard endpoint configures telemetry only. Inject a shared SQLite ledger
or HostedExecutionLedger to coordinate executions across SDK instances.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
import time
from typing import Any, Callable, Optional, TypedDict, cast
from contextlib import contextmanager
from dataclasses import asdict

from .audit import AuditStore, InMemoryAuditStore, MockGateway, PaymentGateway
from .engine.audit4 import FourWayAuditor, transaction_snapshot
from .engine.engine import DecisionEngine
from .engine.policy import BudgetTracker, PaymentPolicy
from .execution import (
    ExecutionConflict,
    ExecutionLedger,
    ExecutionRecord,
    GatewayDeclined,
    GatewayOutcome,
    LedgerBudgetStore,
    LedgerBudgetTracker,
    SQLiteExecutionLedger,
    evidence_digest,
    policy_digest,
    request_digest,
)
from .logging import log_event
from .models import AuditRecord, Decision, DecisionStatus, ExecutionResult, FailureMode, PaymentRequest, RiskFlag, RuleResult
from .notifications import NotificationRouter, Notifier
from .tracing.tracer import ConsoleExporter, Exporter, HTTPExporter, Tracer

logger = logging.getLogger("paiziq.sdk")


class _ExecutionScope(TypedDict):
    org_id: str
    env_id: str


class PaiziqSDK:
    """Entry point for payment verification, execution, and reconciliation."""

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
        failure_mode: FailureMode = FailureMode.FAIL_CLOSED,
        execution_ledger: Optional[ExecutionLedger] = None,
        org_id: str = "local",
        env_id: str = "local",
        policy_version: str = "local",
        clock: Optional[Callable[[], int]] = None,
        review_ttl_ms: int = 900_000,
        evidence_ledger: Optional[SQLiteExecutionLedger] = None,
    ) -> None:
        if not org_id.strip() or not env_id.strip():
            raise ValueError("Organization and environment must be non-empty")
        if review_ttl_ms <= 0:
            raise ValueError("review_ttl_ms must be positive")
        api_key = api_key or os.getenv("PAIZIQ_API_KEY")
        dashboard_endpoint = dashboard_endpoint or os.getenv("PAIZIQ_ENDPOINT")
        if exporters is None:
            exporters = [HTTPExporter(dashboard_endpoint, api_key or "")] if dashboard_endpoint else [ConsoleExporter()]
        self.org_id, self.env_id = org_id, env_id
        self.policy_version = policy_version
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.review_ttl_ms = review_ttl_ms
        self._policy = policy or PaymentPolicy()
        self._lock = threading.RLock()
        self._evaluation_now: Optional[int] = None
        # Reuse an explicitly configured ledger adapter instead of counting its
        # history again as legacy spend.
        legacy_tracker = budget_tracker
        if budget_tracker and isinstance(budget_tracker.store, LedgerBudgetStore):
            store = budget_tracker.store
            if (store.org_id, store.env_id) != (org_id, env_id):
                raise ValueError("Budget store scope differs from execution scope")
            if execution_ledger is not None and execution_ledger is not store.ledger:
                raise ValueError("Budget store and execution ledger must share one authority")
            execution_ledger, legacy_tracker = store.ledger, None
        if legacy_tracker and self._policy.allowed_currencies != {"USD"}:
            raise ValueError("Legacy BudgetTracker has no currency scope; configure USD only or use LedgerBudgetStore")
        self.execution_ledger = execution_ledger or SQLiteExecutionLedger()
        self._legacy_tracker = legacy_tracker
        self._evidence: SQLiteExecutionLedger = evidence_ledger or (
            cast(SQLiteExecutionLedger, self.execution_ledger) if hasattr(self.execution_ledger, "record_review") else SQLiteExecutionLedger()
        )
        if hasattr(self.execution_ledger, "spend_since"):
            self.budget_tracker: BudgetTracker = LedgerBudgetTracker(
                cast(SQLiteExecutionLedger, self.execution_ledger), org_id=org_id, env_id=env_id, legacy=legacy_tracker,
                clock=lambda: self._evaluation_now if self._evaluation_now is not None else self.clock(),
            )
        else:
            # Hosted claim is the final authority for cumulative checks. This
            # local engine applies only additional local restrictions.
            self.budget_tracker = budget_tracker or BudgetTracker()
        self.engine = DecisionEngine(policy=self._policy, budget_tracker=self.budget_tracker)
        self.auditor = FourWayAuditor()
        self.tracer = Tracer(exporters=exporters, service_name=service_name)
        self.gateway = gateway or MockGateway()
        self.audit_store = audit_store or InMemoryAuditStore()
        self.notifications = NotificationRouter(notifiers=notifiers)
        self.require_review_approval = require_review_approval
        self.failure_mode = FailureMode(failure_mode)

    @contextmanager
    def _evaluation(self):
        previous = self._evaluation_now
        self._evaluation_now = self.clock()
        try:
            yield
        finally:
            self._evaluation_now = previous

    @property
    def _scope(self) -> _ExecutionScope:
        return {"org_id": self.org_id, "env_id": self.env_id}

    def _policy_digest(self) -> str:
        return policy_digest(getattr(self.engine, "policy", self._policy), version=self.policy_version, rules=getattr(self.engine, "rules", None))

    def _evaluate(self, request: PaymentRequest) -> Decision:
        try:
            return self.engine.evaluate(request)
        except Exception as exc:
            return self._failure_decision(request, exc)

    def review_payment(self, request: PaymentRequest) -> Decision:
        """Persist a bounded review of one exact request and policy revision.

        This does not reserve or spend money. Execution rechecks current history
        and atomically reserves capacity before calling the provider.
        """
        with self._lock, self._evaluation(), self.tracer.span("paiziq.review_payment", {
            "paiziq.request_id": request.request_id, "payment.merchant": request.merchant,
            "payment.amount": request.amount, "payment.currency": request.currency, "agent.id": request.agent_id,
        }) as span:
            assert self._evaluation_now is not None
            digest = request_digest(request)
            policy_hash = self._policy_digest()
            decision = self._evaluate(request)
            context = {
                "evaluated_at_ms": self._evaluation_now, "expires_at_ms": self._evaluation_now + self.review_ttl_ms,
                "policy_version": self.policy_version, "policy_digest": policy_hash,
                "policy": asdict(getattr(self.engine, "policy", self._policy)),
                "org_id": self.org_id, "env_id": self.env_id,
            }
            if hasattr(self.execution_ledger, "list_events"):
                events = self.execution_ledger.list_events(limit=1, **self._scope)
                context["ledger_sequence"] = events[-1]["sequence"] if events else 0
            payload = {"decision": decision.to_dict(), "snapshot": transaction_snapshot(request), "context": context, "context_digest": evidence_digest(context)}
            self._evidence.record_review(request.request_id, decision.decision_id, digest, policy_hash, payload, **self._scope)
            log_event(logger, "decision", level=logging.DEBUG, request_id=request.request_id, status=decision.status.value, merchant=request.merchant, amount=request.amount, currency=request.currency)
            span.set_attribute("paiziq.decision", decision.status.value)
            span.set_attribute("paiziq.risk_flags", [f.value for f in decision.risk_flags])
            span.add_event("decision", decision.to_dict())
            self._record("review", request, decision.to_dict() | {"intent": request.intent_description, "request_digest": digest, "policy_digest": policy_hash, "context": context})
            self.notifications.notify_decision(request, decision)
            return decision

    def approve_review(self, request_id: str, reviewer_id: str) -> None:
        """Approve only the latest unexpired needs-review decision revision."""
        with self._lock:
            stored = self._evidence.latest_review(request_id, **self._scope)
            if not stored or stored["payload"]["decision"]["status"] != DecisionStatus.NEEDS_REVIEW.value:
                raise ExecutionConflict("Only a current needs_review decision can be approved")
            if stored["policy_digest"] != self._policy_digest() or stored["payload"]["context"]["expires_at_ms"] <= self.clock():
                raise ExecutionConflict("Review expired or policy changed; review the request again")
            self._evidence.approve_review(request_id, stored["decision_id"], reviewer_id, **self._scope)
            self._record_raw("override", request_id, {"action": "review_approved", "reviewer_id": reviewer_id, "decision_id": stored["decision_id"], "request_digest": stored["request_digest"], "policy_digest": stored["policy_digest"]})

    @staticmethod
    def _decision(data: dict) -> Decision:
        return Decision(
            request_id=data["request_id"], status=DecisionStatus(data["status"]), reasons=data["reasons"],
            risk_flags=[RiskFlag(flag) for flag in data["risk_flags"]],
            rule_results=[RuleResult(rule["rule"], DecisionStatus(rule["status"]), rule["reasons"], [RiskFlag(flag) for flag in rule["risk_flags"]], rule["details"]) for rule in data["rule_results"]],
            decision_id=data["decision_id"], decided_at_ms=data["decided_at_ms"],
        )

    def _result(self, record: ExecutionRecord, *, replayed: bool = False) -> ExecutionResult:
        return ExecutionResult(
            request_id=record.request_id, decision_id=record.decision_id, executed=record.status == "confirmed",
            gateway=record.context.get("gateway", self.gateway.name), gateway_reference=record.gateway_reference,
            error=record.error or ("Execution requires reconciliation; no new charge was sent" if record.status in {"reserved", "submitted", "unknown"} else None),
            executed_at_ms=record.updated_at_ms, status=record.status, execution_id=record.execution_id,
            provider_idempotency_key=record.provider_idempotency_key, replayed=replayed,
        )

    def _blocked(self, request: PaymentRequest, error: str, decision_id: str = "") -> ExecutionResult:
        result = ExecutionResult(request_id=request.request_id, decision_id=decision_id, executed=False, gateway=self.gateway.name, error=error)
        self._record("execution", request, result.to_dict(), best_effort=True)
        return result

    def execute_payment(self, request: PaymentRequest) -> ExecutionResult:
        """Revalidate, claim capacity, and send at most one provider invocation.

        Repeated calls return the original execution. An ambiguous provider
        exception leaves exposure reserved; call ``reconcile_payment``. A
        different logical action ID represents a new authorized attempt.
        """
        with self._lock, self._evaluation(), self.tracer.span("paiziq.execute_payment", {"paiziq.request_id": request.request_id}) as span:
            # Freeze the complete payload before any external code can mutate
            # the caller's object while it is being authorized or charged.
            frozen_request = copy.deepcopy(request)
            try:
                digest = request_digest(frozen_request)
                previous = self.execution_ledger.get(request.request_id, **self._scope)
                if previous:
                    if previous.request_digest != digest:
                        return self._blocked(request, "request_digest_conflict: logical action has a different payload", previous.decision_id)
                    return self._result(previous, replayed=True)
                stored = self._evidence.latest_review(request.request_id, **self._scope)
                if stored is None:
                    self.review_payment(frozen_request)
                    stored = self._evidence.latest_review(request.request_id, **self._scope)
                assert stored is not None
                decision = self._decision(stored["payload"]["decision"])
                if stored["request_digest"] != digest:
                    return self._blocked(request, "4-way audit failed: transaction_match; request changed after review", decision.decision_id)
                if stored["policy_digest"] != self._policy_digest():
                    return self._blocked(request, "policy_revision_changed: review the request again", decision.decision_id)
                if stored["payload"]["context"]["expires_at_ms"] <= self.clock():
                    return self._blocked(request, "review_expired: review the request again", decision.decision_id)
                # Do not let a mutable Decision returned to a caller change the
                # stored verdict. Re-evaluate current rules and shared history.
                effective = self._evaluate(frozen_request)
                override = self._evidence.review_approved(decision.decision_id, **self._scope)
                if effective.status is DecisionStatus.NEEDS_REVIEW and (override or not self.require_review_approval):
                    # An old approval cannot clear a newly discovered reason.
                    if (not self.require_review_approval) or (
                        set(effective.risk_flags).issubset(decision.risk_flags)
                        and set(effective.reasons).issubset(decision.reasons)
                    ):
                        effective.status = DecisionStatus.APPROVED
                        effective.reasons.append("Human reviewer approved this request and policy revision")
                # A rejected recorded review remains rejected until re-reviewed.
                if decision.status is DecisionStatus.REJECTED:
                    effective = decision
                audit = self.auditor.run(frozen_request, effective, reviewed_snapshot=stored["payload"]["snapshot"], now_ms=self.clock())
                span.set_attribute("paiziq.four_way_passed", audit.passed)
                span.add_event("four_way_audit", {"checks": [{"dim": check.dimension.value, "passed": check.passed, "detail": check.detail} for check in audit.checks]})
                if not audit.passed:
                    return self._blocked(request, "4-way audit failed: " + ", ".join(dim.value for dim in audit.failed_dimensions), decision.decision_id)
                context = copy.deepcopy(stored["payload"]["context"])
                context["execution_evaluated_at_ms"] = self._evaluation_now
                context["gateway"] = self.gateway.name
                context["execution_decision"] = effective.to_dict()
                context["review_decision_id"] = decision.decision_id
                context["four_way_audit"] = [
                    {"dimension": check.dimension.value, "passed": check.passed, "detail": check.detail}
                    for check in audit.checks
                ]
                if self._legacy_tracker:
                    context["legacy_budget_history"] = {
                        "daily_spend": self._legacy_tracker.daily_spend(request.agent_id),
                        "monthly_spend": self._legacy_tracker.monthly_spend(request.agent_id),
                        "hourly_tx_count": self._legacy_tracker.hourly_tx_count(request.agent_id),
                    }
                policy = getattr(self.engine, "policy", self._policy)
                context["budget_warning_ratio"] = policy.budget_warning_ratio
                context["ignore_budget_warnings"] = not self.require_review_approval
                if override and RiskFlag.BUDGET_NEAR_LIMIT in effective.risk_flags:
                    details = next((result.details for result in effective.rule_results if result.rule_name == "budget_check"), {})
                    context["approved_budget_exposure"] = {
                        label: str(details[label + "_projected"]) for label in ("daily", "monthly")
                        if label + "_projected" in details
                    }
                claim = self.execution_ledger.claim(
                    frozen_request, request_digest=digest, policy_digest=stored["policy_digest"], decision_id=decision.decision_id,
                    context=context, daily_budget=policy.daily_budget, monthly_budget=policy.monthly_budget,
                    max_tx_per_hour=policy.max_tx_per_hour, now_ms=self._evaluation_now, **self._scope,
                )
                if not claim.acquired:
                    return self._result(claim.record, replayed=True) if claim.record else self._blocked(request, claim.reason or "execution_not_authorized", decision.decision_id)
                assert claim.record is not None
                submission_audit = self.auditor.run(
                    frozen_request, effective, reviewed_snapshot=stored["payload"]["snapshot"], now_ms=self.clock(),
                )
                if (self._policy_digest() != stored["policy_digest"]
                        or stored["payload"]["context"]["expires_at_ms"] <= self.clock()
                        or not submission_audit.passed):
                    self.execution_ledger.transition(
                        request.request_id, "failed", error="Authorization changed or expired before provider submission",
                        evidence={"source": "not_submitted", "reason": "authorization_stale"}, now_ms=self.clock(), **self._scope,
                    )
                    return self._blocked(request, "authorization_stale_before_submission", decision.decision_id)
                record = self.execution_ledger.transition(request.request_id, "submitted", now_ms=self.clock(), **self._scope)
            except Exception as exc:
                logger.error("Execution authorization failed: %s", exc)
                # Persistence and authority outages never inherit FAIL_OPEN.
                return self._blocked(request, f"execution_authority_unavailable: {exc}")

            try:
                if hasattr(self.gateway, "charge_idempotent"):
                    reference = self.gateway.charge_idempotent(frozen_request, record.provider_idempotency_key)
                else:
                    reference = self.gateway.charge(frozen_request)
                if not isinstance(reference, str) or not reference:
                    raise RuntimeError("Provider returned no execution reference")
            except Exception as exc:
                status = "failed" if isinstance(exc, GatewayDeclined) else "unknown"
                try:
                    record = self.execution_ledger.transition(request.request_id, status, error=str(exc), evidence={"source": "provider_decline" if status == "failed" else "provider_exception"}, now_ms=self.clock(), **self._scope)
                    result = self._result(record)
                except Exception as ledger_exc:
                    result = self._result(record)
                    result.status = "unknown"
                    result.error = f"Provider result unknown: {exc}; recording pending: {ledger_exc}"
                    result.accounting_pending = True
            else:
                try:
                    record = self.execution_ledger.transition(request.request_id, "confirmed", gateway_reference=reference, evidence={"source": "provider_response"}, now_ms=self.clock(), **self._scope)
                    result = self._result(record)
                except Exception as exc:
                    # A confirmed provider response is never turned into a
                    # failed charge by an accounting/exporter exception.
                    result = self._result(record)
                    result.executed = True
                    result.status = "confirmed"
                    result.gateway_reference = reference
                    result.error = f"Provider confirmed; accounting pending: {exc}"
                    result.accounting_pending = True
            span.set_attribute("paiziq.executed", result.executed)
            span.set_attribute("paiziq.execution_status", result.status)
            self._record("execution", request, result.to_dict(), best_effort=True)
            return result

    def reconcile_payment(self, request_id: str, *, outcome: Optional[GatewayOutcome] = None) -> ExecutionResult:
        """Resolve an uncertain execution from trusted provider evidence.

        An injected outcome is an explicit operator/adapter assertion. Without
        it, the configured gateway must implement lookup(idempotency_key).
        This method never calls charge or releases an unresolved reservation.
        """
        with self._lock:
            record = self.execution_ledger.get(request_id, **self._scope)
            if record is None:
                raise KeyError(request_id)
            if record.status in {"confirmed", "failed"}:
                return self._result(record, replayed=True)
            if outcome is None:
                if not hasattr(self.gateway, "lookup"):
                    return self._result(record, replayed=True)
                try:
                    outcome = self.gateway.lookup(record.provider_idempotency_key)
                except Exception:
                    logger.exception("Provider receipt lookup failed")
                    return self._result(record, replayed=True)
            if not isinstance(outcome, GatewayOutcome):
                raise TypeError("Reconciliation requires a GatewayOutcome")
            if record.status == "reserved" and outcome.status != "failed":
                # No submitted transition exists. Only trusted proof that the
                # call did not happen can release this interrupted claim.
                return self._result(record, replayed=True)
            record = self.execution_ledger.transition(request_id, outcome.status, gateway_reference=outcome.gateway_reference, error=outcome.error, evidence={"source": "provider_lookup", "provider_idempotency_key": record.provider_idempotency_key, "gateway": self.gateway.name}, now_ms=self.clock(), **self._scope)
            result = self._result(record)
            self._record_raw("reconciliation", request_id, result.to_dict(), best_effort=True)
            return result

    def get_audit_trail(self, request_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.audit_store.query(request_id=request_id, limit=limit)]

    def get_execution_events(self, request_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        """Read durable business evidence independently of trace exporters."""
        return self._evidence.list_events(request_id, limit=limit, **self._scope)

    def shutdown(self) -> None:
        self.tracer.shutdown()

    _FAILURE_VERDICTS = {
        FailureMode.FAIL_OPEN: DecisionStatus.APPROVED,
        FailureMode.FAIL_CLOSED: DecisionStatus.REJECTED,
        FailureMode.REVIEW_REQUIRED: DecisionStatus.NEEDS_REVIEW,
    }

    def _failure_decision(self, request: PaymentRequest, exc: Exception) -> Decision:
        status = self._FAILURE_VERDICTS[self.failure_mode]
        logger.error("paiziq decisioning failed (%s); applying %s -> %s", exc, self.failure_mode.value, status.value)
        decision = Decision(request_id=request.request_id, status=status, reasons=[f"failure_mode:{self.failure_mode.value}", f"Decision engine failed unexpectedly: {type(exc).__name__}: {exc}"])
        self._record("review", request, {"failure_mode": self.failure_mode.value, "error": f"{type(exc).__name__}: {exc}", "verdict": status.value})
        return decision

    def _record(self, event_type: str, request: PaymentRequest, payload: dict[str, Any], *, best_effort: bool = False) -> None:
        self._record_raw(event_type, request.request_id, payload, best_effort=best_effort)

    def _record_raw(self, event_type: str, request_id: str, payload: dict[str, Any], *, best_effort: bool = False) -> None:
        try:
            self._evidence.append_event("audit_" + event_type, request_id, payload, **self._scope)
        except Exception:
            if not best_effort:
                raise
            logger.exception("Durable audit projection pending; execution claim remains authoritative")
        try:
            self.audit_store.append(AuditRecord(event_type=event_type, request_id=request_id, payload=copy.deepcopy(payload), trace_id=self.tracer.current_trace_id()))
        except Exception:
            logger.exception("Optional audit-store projection failed; durable evidence remains in execution ledger")
