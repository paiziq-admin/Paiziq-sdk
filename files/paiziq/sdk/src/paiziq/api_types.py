"""Generated API client types for the Paiziq ingest wire contract.

GENERATED FILE — do not edit by hand. Regenerate with `make openapi`
(runs services/ingest/scripts/export_openapi.py).

Source: Paiziq Ingest API v1.0 (OpenAPI 3.1.0).
"""

from __future__ import annotations

from typing import Any, TypedDict

__all__ = ['AgentPatch', 'AgentRegister', 'DecisionCreate', 'DraftUpdate', 'EnvironmentCreate', 'HTTPValidationError', 'KeyCreate', 'KeyRotate', 'NotificationIn', 'OrgCreate', 'PaymentCreate', 'PolicyCreate', 'RollbackRequest', 'SpanIn', 'TraceBatch', 'TransitionIn', 'ValidationError']


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


class DraftUpdate(TypedDict):
    document: dict[str, Any]


class EnvironmentCreate(TypedDict):
    name: str
    kind: str


class HTTPValidationError(TypedDict, total=False):
    detail: list[ValidationError]


class KeyCreate(TypedDict):
    env_id: str
    name: str
    scope: str


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


class RollbackRequest(TypedDict):
    version: int


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
