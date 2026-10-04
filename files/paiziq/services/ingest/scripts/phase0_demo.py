"""Seed Phase 0 evidence through the real SDK and HTTP authority.

Only local mock providers are used. The selected backend receives a new
sandbox environment; existing environments and manual demo data are retained.
Print one JSON report for the dashboard service browser lane.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid

from paiziq import PaiziqSDK, PaymentPolicy, PaymentRequest
from paiziq.audit import MockGateway
from paiziq.hosted_execution import HostedExecutionLedger
from paiziq.tracing.tracer import InMemoryExporter
from paiziq.transport import RetryPolicy, SyncHTTPTransport


class UncertainMockProvider:
    name = "phase0-uncertain-mock"

    def __init__(self) -> None:
        self.calls = 0

    def charge(self, request: PaymentRequest) -> str:
        self.calls += 1
        raise TimeoutError("Mock provider received the request; its receipt is unavailable")


def run(endpoint: str, api_key: str) -> dict:
    transport = SyncHTTPTransport(endpoint, api_key=api_key, retry=RetryPolicy(max_attempts=1))

    def data(method, path, body=None):
        response = transport.request(method, path, json_body=body)
        if response.status >= 400:
            raise RuntimeError(f"Phase 0 demo API returned {response.status}: {response.body.decode()}")
        return response.json()["data"]

    org = data("POST", "/v1/orgs", {"name": "phase0-demo-" + uuid.uuid4().hex[:8]})
    env = data("POST", f"/v1/orgs/{org['id']}/environments", {"name": "Phase 0 sandbox", "kind": "sandbox"})
    agent = data("POST", "/v1/agents", {"env_id": env["id"], "name": "phase0-execution-agent"})
    policy = data("POST", "/v1/policies", {"env_id": env["id"], "name": "Phase 0 budget", "document": {
        "review_threshold": 100, "hard_limit": 1000, "daily_budget": 100,
    }})
    data("POST", f"/v1/policies/{policy['id']}/publish")

    def sdk(gateway):
        return PaiziqSDK(
            policy=PaymentPolicy(review_threshold=100, hard_limit=1000),
            execution_ledger=HostedExecutionLedger(transport, org_id=org["id"], env_id=env["id"]),
            org_id=org["id"], env_id=env["id"], gateway=gateway,
            exporters=[InMemoryExporter()],
        )

    def request(amount, merchant):
        return PaymentRequest(
            agent_id=agent["id"], principal_id="phase0-demo-principal", merchant=merchant,
            amount=amount, category="software", intent_description="Phase 0 execution test with a mock provider",
            metadata={"demo": "phase0", "provider_mode": "mock"},
        )

    def report(item, result):
        items = data("GET", f"/v1/payments?env_id={env['id']}&request_id={item.request_id}")
        if len(items) != 1:
            raise RuntimeError("Expected one persisted payment for the demo request")
        return {"payment_id": items[0]["id"], "request_id": item.request_id,
                "executed": result.executed, "state": items[0]["state"], "error": result.error}

    provider = MockGateway()
    confirmed_request = request(40, "Phase 0 confirmed vendor")
    first = sdk(provider)
    confirmed = first.execute_payment(confirmed_request)
    repeated = sdk(provider).execute_payment(confirmed_request)
    if not confirmed.executed or not repeated.executed or len(provider.charges) != 1:
        raise RuntimeError("The repeated request did not retain one confirmed charge")

    uncertain_provider = UncertainMockProvider()
    unknown_request = request(20, "Phase 0 unknown vendor")
    uncertain = sdk(uncertain_provider).execute_payment(unknown_request)
    sdk(uncertain_provider).execute_payment(unknown_request)
    if uncertain.executed or uncertain_provider.calls != 1:
        raise RuntimeError("The unknown request was submitted again")

    blocked_provider = MockGateway()
    blocked_request = request(50, "Phase 0 budget vendor")
    blocked = sdk(blocked_provider).execute_payment(blocked_request)
    if blocked.executed or blocked_provider.charges:
        raise RuntimeError("The hosted budget was exceeded")

    return {
        "org": org, "environment": env, "agent": agent,
        "confirmed": report(confirmed_request, confirmed),
        "unknown": report(unknown_request, uncertain),
        "blocked": report(blocked_request, blocked),
        "provider_calls": {"confirmed": len(provider.charges), "unknown": uncertain_provider.calls},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8800")
    args = parser.parse_args()
    print(json.dumps(run(args.endpoint, os.environ.get("PAIZIQ_API_KEY", "dev-key"))))
