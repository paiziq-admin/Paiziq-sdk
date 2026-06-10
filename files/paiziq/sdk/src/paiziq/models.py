"""Paiziq core domain models.

Framework-agnostic dataclasses shared by the decision engine, tracer,
audit store, and exporters. No third-party dependencies.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class DecisionStatus(str, Enum):
    """Terminal verdicts produced by the decision engine."""

    APPROVED = "approved"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class RiskFlag(str, Enum):
    """Machine-readable risk flags attached to decisions and traces."""

    OVER_HARD_LIMIT = "over_hard_limit"
    OVER_REVIEW_THRESHOLD = "over_review_threshold"
    MERCHANT_BLOCKED = "merchant_blocked"
    MERCHANT_NOT_ALLOWLISTED = "merchant_not_allowlisted"
    UNKNOWN_MERCHANT = "unknown_merchant"
    BUDGET_EXCEEDED = "budget_exceeded"
    BUDGET_NEAR_LIMIT = "budget_near_limit"
    CATEGORY_REVIEW_REQUIRED = "category_review_required"
    CURRENCY_NOT_PERMITTED = "currency_not_permitted"
    VELOCITY_ANOMALY = "velocity_anomaly"
    HARMFUL_INTENT_SUSPECTED = "harmful_intent_suspected"
    IDENTITY_MISMATCH = "identity_mismatch"
    INTENT_MISMATCH = "intent_mismatch"
    POLICY_MISMATCH = "policy_mismatch"
    TRANSACTION_MISMATCH = "transaction_mismatch"


class AuditDimension(str, Enum):
    """The four dimensions of the Paiziq 4-Way Match audit policy."""

    IDENTITY = "identity_match"
    INTENT = "intent_match"
    POLICY = "policy_match"
    TRANSACTION = "transaction_match"


@dataclass
class Mandate:
    """What the human principal authorized the agent to do.

    This is the 'source of truth' the 4-way audit compares against.
    """

    principal_id: str
    agent_id: str
    max_amount: Optional[float] = None
    currency: str = "USD"
    allowed_merchants: Optional[list[str]] = None
    purpose: str = ""
    expires_at_ms: Optional[int] = None
    mandate_id: str = field(default_factory=lambda: _new_id("mnd"))


@dataclass
class PaymentRequest:
    """A payment the agent intends to execute."""

    agent_id: str
    principal_id: str
    merchant: str
    amount: float
    currency: str = "USD"
    category: str = "general"
    intent_description: str = ""
    mandate: Optional[Mandate] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: _new_id("pay"))
    created_at_ms: int = field(default_factory=_now_ms)


@dataclass
class RuleResult:
    """Outcome of a single decision rule."""

    rule_name: str
    status: DecisionStatus
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[RiskFlag] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditCheck:
    """Outcome of one dimension of the 4-way match."""

    dimension: AuditDimension
    passed: bool
    detail: str = ""


@dataclass
class FourWayAuditResult:
    checks: list[AuditCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_dimensions(self) -> list[AuditDimension]:
        return [c.dimension for c in self.checks if not c.passed]


@dataclass
class Decision:
    """Aggregate verdict for a payment request."""

    request_id: str
    status: DecisionStatus
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[RiskFlag] = field(default_factory=list)
    rule_results: list[RuleResult] = field(default_factory=list)
    four_way_audit: Optional[FourWayAuditResult] = None
    decision_id: str = field(default_factory=lambda: _new_id("dec"))
    decided_at_ms: int = field(default_factory=_now_ms)

    @property
    def approved(self) -> bool:
        return self.status is DecisionStatus.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "request_id": self.request_id,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "risk_flags": [f.value for f in self.risk_flags],
            "rule_results": [
                {
                    "rule": r.rule_name,
                    "status": r.status.value,
                    "reasons": r.reasons,
                    "risk_flags": [f.value for f in r.risk_flags],
                    "details": r.details,
                }
                for r in self.rule_results
            ],
            "four_way_audit": (
                {
                    "passed": self.four_way_audit.passed,
                    "checks": [
                        {"dimension": c.dimension.value, "passed": c.passed, "detail": c.detail}
                        for c in self.four_way_audit.checks
                    ],
                }
                if self.four_way_audit
                else None
            ),
            "decided_at_ms": self.decided_at_ms,
        }


@dataclass
class ExecutionResult:
    """Result of pushing an approved payment through a gateway."""

    request_id: str
    decision_id: str
    executed: bool
    gateway: str = "mock"
    gateway_reference: Optional[str] = None
    error: Optional[str] = None
    executed_at_ms: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "decision_id": self.decision_id,
            "executed": self.executed,
            "gateway": self.gateway,
            "gateway_reference": self.gateway_reference,
            "error": self.error,
            "executed_at_ms": self.executed_at_ms,
        }


@dataclass
class AuditRecord:
    """Immutable audit-trail entry. One per significant event."""

    event_type: str  # review | execution | notification | override
    request_id: str
    payload: dict[str, Any]
    trace_id: Optional[str] = None
    record_id: str = field(default_factory=lambda: _new_id("aud"))
    recorded_at_ms: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "event_type": self.event_type,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "payload": self.payload,
            "recorded_at_ms": self.recorded_at_ms,
        }
