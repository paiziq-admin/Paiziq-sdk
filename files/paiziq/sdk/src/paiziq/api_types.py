"""Generated API client types for the Paiziq ingest wire contract.

GENERATED FILE — do not edit by hand. Regenerate with `make openapi`
(runs services/ingest/scripts/export_openapi.py).

Source: Paiziq Ingest API v1.0 (OpenAPI 3.1.0).
"""

from __future__ import annotations

from typing import Any, TypedDict

__all__ = ['AgentPatch', 'AgentRegister', 'DecisionCreate', 'DraftUpdate', 'EndpointCreate', 'EndpointPatch', 'EnvironmentCreate', 'HTTPValidationError', 'KeyCreate', 'KeyRotate', 'NotificationIn', 'OrgCreate', 'PaymentCreate', 'PolicyCreate', 'ReviewAction', 'ReviewAssignment', 'ReviewEscalation', 'ReviewRelease', 'RollbackRequest', 'SimulatePayment', 'SimulateRequest', 'SpanIn', 'TraceBatch', 'TransitionIn', 'ValidationError']


class AgentPatch(TypedDict, total=False):
    name: str | None
    status: str | None
    metadata: dict[str, Any] | None


class _AgentRegisterRequired(TypedDict):
    env_id: str
    name: str


class AgentRegister(_AgentRegisterRequired, total=False):
    framework: str | None
    metadata: dict[str, Any]


class DecisionCreate(TypedDict):
    payment_id: str


class _DraftUpdateRequired(TypedDict):
    document: dict[str, Any]


class DraftUpdate(_DraftUpdateRequired, total=False):
    reason: str | None


class _EndpointCreateRequired(TypedDict):
    env_id: str
    url: str


class EndpointCreate(_EndpointCreateRequired, total=False):
    events: list[str]


class EndpointPatch(TypedDict, total=False):
    url: str | None
    events: list[str] | None
    status: str | None


class EnvironmentCreate(TypedDict):
    name: str
    kind: str


class HTTPValidationError(TypedDict, total=False):
    detail: list[ValidationError]


class _KeyCreateRequired(TypedDict):
    env_id: str
    name: str
    scope: str


class KeyCreate(_KeyCreateRequired, total=False):
    role: str | None


class KeyRotate(TypedDict, total=False):
    grace_seconds: int


class _NotificationInRequired(TypedDict):
    severity: str
    title: str


class NotificationIn(_NotificationInRequired, total=False):
    message: str
    request_id: str | None
    risk_flags: list[str]
    created_at_ms: int | None


class OrgCreate(TypedDict):
    name: str


class _PaymentCreateRequired(TypedDict):
    env_id: str
    agent_id: str
    principal_id: str
    merchant: str
    amount: float


class PaymentCreate(_PaymentCreateRequired, total=False):
    currency: str
    intent_description: str
    request_id: str | None


class _PolicyCreateRequired(TypedDict):
    env_id: str
    name: str


class PolicyCreate(_PolicyCreateRequired, total=False):
    document: dict[str, Any] | None


class ReviewAction(TypedDict):
    reviewer_id: str
    note: str


class ReviewAssignment(TypedDict):
    reviewer_id: str


class _ReviewEscalationRequired(TypedDict):
    reviewer_id: str
    note: str


class ReviewEscalation(_ReviewEscalationRequired, total=False):
    priority: str


class _ReviewReleaseRequired(TypedDict):
    reviewer_id: str


class ReviewRelease(_ReviewReleaseRequired, total=False):
    note: str | None


class RollbackRequest(TypedDict):
    version: int


class _SimulatePaymentRequired(TypedDict):
    merchant: str
    amount: float


class SimulatePayment(_SimulatePaymentRequired, total=False):
    currency: str
    intent_description: str
    agent_id: str
    principal_id: str


class _SimulateRequestRequired(TypedDict):
    payment: SimulatePayment


class SimulateRequest(_SimulateRequestRequired, total=False):
    document: dict[str, Any] | None
    policy_id: str | None
    version: int | None
    use_draft: bool
    env_id: str | None


class _SpanInRequired(TypedDict):
    name: str
    trace_id: str
    span_id: str


class SpanIn(_SpanInRequired, total=False):
    parent_span_id: str | None
    start_ms: int | None
    end_ms: int | None
    duration_ms: int | None
    status: str
    attributes: dict[str, Any]
    events: list[dict[str, Any]]


class TraceBatch(TypedDict):
    spans: list[SpanIn]


class _TransitionInRequired(TypedDict):
    to: str


class TransitionIn(_TransitionInRequired, total=False):
    reason: str | None


class _ValidationErrorRequired(TypedDict):
    loc: list[str | int]
    msg: str
    type: str


class ValidationError(_ValidationErrorRequired, total=False):
    input: Any
    ctx: dict[str, Any]
