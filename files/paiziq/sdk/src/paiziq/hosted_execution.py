"""Explicit hosted execution authority over the versioned payment API.

The endpoint used by a trace exporter does not select this adapter. Supply it
as ``execution_ledger`` and use the same organization/environment on the SDK.
An unavailable service fails closed. A lost claim response never causes a
second provider call. Recovery of an uncertain outcome needs provider lookup
evidence and a credential permitted to reconcile the existing execution.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, fields
from typing import Any, Optional
from urllib.parse import quote, urlencode

from .execution import ClaimResult, ExecutionConflict, ExecutionRecord
from .models import PaymentRequest
from .transport import SyncHTTPTransport, TransportError


class HostedExecutionLedger:
    """Use one server ledger for SDK execution and dashboard evidence.

    ``transport`` is injected, so standard-library HTTP or a test transport can
    be used. Claim tokens stay in memory and are never put into audit records.
    The active server policy is the final authorization boundary; local SDK
    policy checks can only impose additional restrictions.
    """

    def __init__(self, transport: SyncHTTPTransport, *, org_id: str, env_id: str) -> None:
        if not org_id.strip() or not env_id.strip():
            raise ValueError("Hosted execution requires an organization and environment")
        self.transport = transport
        self.org_id = org_id
        self.env_id = env_id
        self._lock = threading.RLock()
        self._payments: dict[str, str] = {}
        self._tokens: dict[str, str] = {}

    def _scope(self, org_id: str, env_id: str) -> None:
        if (org_id, env_id) != (self.org_id, self.env_id):
            raise ExecutionConflict("SDK scope differs from the hosted execution authority")

    def _data(self, method: str, path: str, body: Optional[dict] = None,
              headers: Optional[dict[str, str]] = None) -> Any:
        response = self.transport.request(method, path, json_body=body, headers=headers)
        try:
            envelope = response.json()
        except (ValueError, UnicodeError) as exc:
            raise TransportError("Hosted execution returned an invalid response", response.status) from exc
        if response.status >= 400 or not isinstance(envelope, dict) or not envelope.get("success"):
            error = envelope.get("error", {}) if isinstance(envelope, dict) else {}
            message = error.get("message", "Hosted execution request failed") if isinstance(error, dict) else "Hosted execution request failed"
            if response.status == 409 and isinstance(error, dict) and error.get("code") in {
                "idempotency_conflict", "request_conflict", "request_digest_conflict", "execution_conflict",
            }:
                raise ExecutionConflict(message)
            raise TransportError(message, response.status)
        return envelope["data"]

    def _payment_id(self, request_id: str) -> Optional[str]:
        with self._lock:
            cached = self._payments.get(request_id)
        if cached:
            return cached
        items = self._data("GET", "/v1/payments?" + urlencode({
            "env_id": self.env_id, "request_id": request_id, "limit": 2,
        }))
        if not isinstance(items, list):
            raise TransportError("Hosted payment lookup returned an invalid list")
        matching = [p for p in items if p.get("request_id") == request_id and p.get("env_id") == self.env_id]
        if len(matching) > 1:
            raise ExecutionConflict("Multiple payment records use the same logical request ID")
        if not matching:
            return None
        payment_id = str(matching[0]["id"])
        with self._lock:
            self._payments[request_id] = payment_id
        return payment_id

    def _record(self, value: Any, request_id: str) -> Optional[ExecutionRecord]:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TransportError("Hosted execution returned an invalid record")
        try:
            record = ExecutionRecord(**{f.name: value[f.name] for f in fields(ExecutionRecord)})
        except (KeyError, TypeError) as exc:
            raise TransportError("Hosted execution record has missing fields") from exc
        if (record.org_id, record.env_id, record.request_id) != (self.org_id, self.env_id, request_id):
            raise ExecutionConflict("Hosted execution returned a record outside the requested scope")
        return record

    @staticmethod
    def _path(payment_id: str, suffix: str = "") -> str:
        return f"/v1/payments/{quote(payment_id, safe='')}/execution{suffix}"

    def get(self, request_id: str, *, org_id: str = "local", env_id: str = "local") -> Optional[ExecutionRecord]:
        self._scope(org_id, env_id)
        payment_id = self._payment_id(request_id)
        if payment_id is None:
            return None
        data = self._data("GET", self._path(payment_id))
        return self._record(data["record"], request_id)

    def claim(self, request: PaymentRequest, *, org_id: str, env_id: str,
              request_digest: str, policy_digest: str, decision_id: str,
              context: Optional[dict] = None, daily_budget: Optional[float] = None,
              monthly_budget: Optional[float] = None, max_tx_per_hour: Optional[int] = None,
              now_ms: Optional[int] = None) -> ClaimResult:
        self._scope(org_id, env_id)
        body = asdict(request)
        body.pop("created_at_ms", None)
        body["env_id"] = env_id
        payment = self._data("POST", "/v1/payments", body, {
            "Idempotency-Key": request.request_id,
            "Idempotency-Mode": "scoped",
        })
        if payment.get("env_id") != env_id or payment.get("request_id") != request.request_id:
            raise ExecutionConflict("Hosted payment registration changed the request scope")
        payment_id = str(payment["id"])
        with self._lock:
            self._payments[request.request_id] = payment_id
        # Never trust client-supplied limits or a local approval at this boundary.
        data = self._data("POST", self._path(payment_id, "/claim"), {
            "request_digest": request_digest,
        })
        record = self._record(data.get("record"), request.request_id)
        acquired = data.get("acquired") is True
        if acquired:
            token = data.get("claim_token")
            if record is None or not isinstance(token, str) or not token:
                raise TransportError("Hosted execution claim has no owner token")
            with self._lock:
                self._tokens[request.request_id] = token
        return ClaimResult(record, acquired, data.get("reason"))

    def transition(self, request_id: str, status: str, *, org_id: str, env_id: str,
                   gateway_reference: Optional[str] = None, error: Optional[str] = None,
                   evidence: Optional[dict] = None, now_ms: Optional[int] = None) -> ExecutionRecord:
        self._scope(org_id, env_id)
        payment_id = self._payment_id(request_id)
        if payment_id is None:
            raise ExecutionConflict("No hosted payment exists for this execution")
        with self._lock:
            token = self._tokens.get(request_id)
        body: dict[str, Any] = {
            "status": status, "gateway_reference": gateway_reference,
            "error": error, "evidence": evidence or {},
        }
        # A trusted provider lookup can resolve an existing unknown execution;
        # it never grants a new charge. The server checks reconciliation rights.
        lookup = (evidence or {}).get("source") == "provider_lookup"
        if lookup:
            body["reason"] = "Provider lookup reconciled the existing execution"
            suffix = "/reconcile"
        else:
            if not token:
                raise ExecutionConflict("This process does not own the hosted execution claim")
            body["claim_token"] = token
            suffix = "/report"
        data = self._data("POST", self._path(payment_id, suffix), body)
        record = self._record(data, request_id)
        if record is None:
            raise TransportError("Hosted execution transition returned no record")
        return record
