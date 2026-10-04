"""Simulated payment agent for the local Paiziq workflow."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

_INGEST = Path(__file__).resolve().parents[2]
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))
_TESTS = Path(__file__).resolve().parents[1]
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from e2e_support.scenario import (  # noqa: E402
    AGENT_NAME,
    ENV_KIND,
    ENV_NAME,
    POLICY_DOCUMENT,
    POLICY_NAME,
    PRINCIPAL_ID,
    TRANSACTIONS,
)
from paiziq import PaiziqSDK, PaymentRequest  # noqa: E402
from paiziq.audit import MockGateway  # noqa: E402
from paiziq.tracing.tracer import HTTPExporter  # noqa: E402
from paiziq.transport import RetryPolicy, SyncHTTPTransport  # noqa: E402
from policy_doc import to_policy  # noqa: E402


class ControlPlaneError(RuntimeError):
    """A control-plane call returned an error status."""


class RecordingTransport(SyncHTTPTransport):
    """Record response evidence, excluding credentials, for the endpoint audit."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.responses: list[tuple[str, str, int, dict]] = []

    def request(self, method, path, json_body=None, headers=None):
        response = super().request(method, path, json_body, headers)
        self.responses.append((method, path, response.status, response.json()))
        return response


class SimulatedPaymentAgent:
    """Registers a sandbox agent and runs the three deterministic payments."""

    def __init__(self, base_url: str, api_key: str = "dev-key") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.transport = RecordingTransport(
            self.base_url,
            api_key=api_key,
            retry=RetryPolicy(max_attempts=2, base_delay_s=0.01),
        )
        self.gateway = MockGateway()
        self.exporter = HTTPExporter(
            self.base_url,
            api_key,
            batch_size=1,
            flush_interval_s=0.05,
            transport=self.transport,
        )
        self.sdk = PaiziqSDK(
            policy=to_policy(POLICY_DOCUMENT),
            exporters=[self.exporter],
            notifiers=[],
            gateway=self.gateway,
            service_name=AGENT_NAME,
        )

    def run(self) -> dict[str, Any]:
        try:
            return self._run()
        finally:
            self.sdk.shutdown()

    def _run(self) -> dict[str, Any]:
        suffix = uuid.uuid4().hex[:8]
        org = self._data("POST", "/v1/orgs", {"name": f"e2e-org-{suffix}"})
        env = self._data(
            "POST",
            f"/v1/orgs/{org['id']}/environments",
            {"name": ENV_NAME, "kind": ENV_KIND},
        )
        agent = self._data(
            "POST",
            "/v1/agents",
            {
                "env_id": env["id"],
                "name": AGENT_NAME,
                "framework": "custom",
                "metadata": {"suite": "payment-agent-workflow"},
            },
        )
        policy = self._data(
            "POST",
            "/v1/policies",
            {"env_id": env["id"], "name": POLICY_NAME, "document": POLICY_DOCUMENT},
        )
        published = self._data("POST", f"/v1/policies/{policy['id']}/publish")
        transactions = [
            self._transact(env["id"], agent["id"], spec) for spec in TRANSACTIONS
        ]
        self.sdk.shutdown()
        self._wait_for_traces(transactions)
        return {
            "base_url": self.base_url,
            "org": {"id": org["id"], "name": org["name"]},
            "environment": {"id": env["id"], "name": env["name"], "kind": env["kind"]},
            "agent": {"id": agent["id"], "name": agent["name"]},
            "policy": {
                "id": policy["id"],
                "name": policy["name"],
                "version": published["version"],
            },
            "transactions": transactions,
        }

    def _transact(self, env_id: str, agent_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        request = PaymentRequest(
            agent_id=agent_id,
            principal_id=PRINCIPAL_ID,
            merchant=spec["merchant"],
            amount=spec["amount"],
            currency="USD",
            category="software",
            intent_description=spec["intent"],
            request_id=f"{spec['request_id']}-{env_id}",
        )
        trace_id = self.sdk.tracer.new_trace()
        local = self.sdk.review_payment(request)
        payment = self._data(
            "POST",
            "/v1/payments",
            {
                "env_id": env_id,
                "agent_id": agent_id,
                "principal_id": PRINCIPAL_ID,
                "merchant": spec["merchant"],
                "amount": spec["amount"],
                "currency": "USD",
                "intent_description": spec["intent"],
                "request_id": request.request_id,
            },
            headers={"Idempotency-Key": request.request_id},
        )
        decision = self._data("POST", "/v1/decisions", {"payment_id": payment["id"]})
        if local.status.value != decision["verdict"]:
            raise ControlPlaneError("Local and published-policy verdicts disagree")
        gateway_reference = None
        if spec["execute"]:
            if decision["verdict"] != "approved":
                raise ControlPlaneError("Control plane did not approve execution")
            executed = self.sdk.execute_payment(request)
            if not executed.executed:
                raise ControlPlaneError(
                    f"SDK refused execution for {spec['request_id']}: {executed.error}"
                )
            gateway_reference = executed.gateway_reference
            self._data(
                "POST",
                f"/v1/payments/{payment['id']}/transition",
                {"to": "executed", "reason": f"mock gateway {gateway_reference}"},
            )
        detail = self._data("GET", f"/v1/payments/{payment['id']}")
        return {
            "key": spec["key"],
            "request_id": request.request_id,
            "merchant": spec["merchant"],
            "amount": spec["amount"],
            "intent": spec["intent"],
            "payment_id": detail["id"],
            "state": detail["state"],
            "decision_id": decision["id"],
            "verdict": decision["verdict"],
            "reasons": decision["reasons"],
            "risk_flags": decision["risk_flags"],
            "policy_version": decision["policy_version"],
            "review_id": decision.get("review_id"),
            "trace_id": trace_id,
            "gateway_reference": gateway_reference,
            "local_verdict": local.status.value,
            "local_reasons": list(local.reasons),
            "local_risk_flags": [flag.value for flag in local.risk_flags],
        }

    def _wait_for_traces(self, transactions: list[dict[str, Any]]) -> None:
        deadline = time.time() + 5
        pending = {item["trace_id"] for item in transactions}
        while pending and time.time() < deadline:
            ready = set()
            for trace_id in pending:
                body = self._raw("GET", f"/v1/traces/{trace_id}")
                if body.get("spans"):
                    ready.add(trace_id)
            pending -= ready
            if pending:
                time.sleep(0.05)
        if pending:
            raise ControlPlaneError(f"traces not ingested: {sorted(pending)}")


    def _data(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload = self._raw(method, path, body, headers)
        if not payload.get("success", False):
            raise ControlPlaneError(f"{method} {path} failed: {payload}")
        data = payload["data"]
        if not isinstance(data, dict):
            raise ControlPlaneError(f"{method} {path} data was not an object")
        return data

    def _raw(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = self.transport.request(method, path, json_body=body, headers=headers)
        if response.status >= 400:
            raise ControlPlaneError(
                f"{method} {path} -> {response.status} {response.body[:400]!r}"
            )
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise ControlPlaneError(f"{method} {path} returned {parsed!r}")
        return parsed


def main() -> None:
    base_url = os.environ.get("PAIZIQ_ENDPOINT", "http://127.0.0.1:8800")
    api_key = os.environ.get("PAIZIQ_API_KEY", "dev-key")
    report = SimulatedPaymentAgent(base_url, api_key).run()
    json.dump(report, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
