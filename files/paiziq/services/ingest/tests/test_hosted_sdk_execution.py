"""Phase 0: real SDK/HTTP/ledger round trips with no real financial provider."""

from __future__ import annotations

import copy
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app  # noqa: E402
from paiziq import GatewayOutcome, PaiziqSDK, PaymentPolicy, PaymentRequest  # noqa: E402
from paiziq.audit import MockGateway  # noqa: E402
from paiziq.hosted_execution import HostedExecutionLedger  # noqa: E402
from paiziq.tracing.tracer import InMemoryExporter  # noqa: E402
from paiziq.transport import RetryPolicy, SyncHTTPTransport, TransportError  # noqa: E402


@pytest.fixture(scope="module")
def hosted_url():
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            pytest.fail("isolated HTTP service failed to start")
        time.sleep(0.02)
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    server.should_exit = True
    thread.join(timeout=5)


def setup_scope(url, **policy_overrides):
    transport = SyncHTTPTransport(url, api_key="dev-key", retry=RetryPolicy(max_attempts=1))
    def post(path, body=None):
        response = transport.post(path, body)
        assert response.status == 200, response.body
        return response.json()["data"]
    org = post("/v1/orgs", {"name": "phase0-" + uuid.uuid4().hex})
    env = post(f"/v1/orgs/{org['id']}/environments", {"name": "sandbox", "kind": "sandbox"})
    agent = post("/v1/agents", {"env_id": env["id"], "name": "phase0-agent"})
    document = {"review_threshold": 100, "hard_limit": 1000, "daily_budget": 100, **policy_overrides}
    policy = post("/v1/policies", {"env_id": env["id"], "name": "phase0", "document": document})
    post(f"/v1/policies/{policy['id']}/publish")
    return transport, org["id"], env["id"], agent["id"]


def sdk_for(scope, gateway):
    transport, org_id, env_id, _ = scope
    ledger = HostedExecutionLedger(transport, org_id=org_id, env_id=env_id)
    sdk = PaiziqSDK(
        execution_ledger=ledger, org_id=org_id, env_id=env_id,
        policy=PaymentPolicy(review_threshold=100, hard_limit=1000),
        gateway=gateway, exporters=[InMemoryExporter()],
    )
    return sdk, ledger


def payment(scope, amount=60):
    return PaymentRequest(
        agent_id=scope[3], principal_id="phase0-principal", merchant="Example Vendor",
        amount=amount, category="software", metadata={"purpose": "phase0-test"},
    )


def test_hosted_sdk_repeat_and_restart_charge_only_once(hosted_url):
    scope = setup_scope(hosted_url)
    gateway = MockGateway()
    request = payment(scope)
    first, _ = sdk_for(scope, gateway)
    result = first.execute_payment(request)
    assert result.executed, result.error
    assert first.execute_payment(request).executed
    second, ledger = sdk_for(scope, gateway)
    assert second.execute_payment(request).executed
    assert len(gateway.charges) == 1
    record = ledger.get(request.request_id, org_id=scope[1], env_id=scope[2])
    assert record.status == "confirmed"
    assert record.gateway_reference == result.gateway_reference


def test_hosted_sdk_server_budget_survives_fresh_sdk_process_state(hosted_url):
    scope = setup_scope(hosted_url)
    gateway = MockGateway()
    first, _ = sdk_for(scope, gateway)
    second, _ = sdk_for(scope, gateway)
    one, two = payment(scope), payment(scope)
    assert first.review_payment(one).approved
    assert second.review_payment(two).approved
    assert first.execute_payment(one).executed
    blocked = second.execute_payment(two)
    assert not blocked.executed
    assert len(gateway.charges) == 1


def test_hosted_sdk_unknown_outcome_keeps_reservation_and_never_recharges(hosted_url):
    scope = setup_scope(hosted_url)
    class UncertainGateway:
        name = "uncertain-test-provider"
        def __init__(self):
            self.calls = 0
        def charge(self, request):
            self.calls += 1
            raise TimeoutError("receipt unavailable after request was sent")
    provider = UncertainGateway()
    first, ledger = sdk_for(scope, provider)
    request = payment(scope)
    assert not first.execute_payment(request).executed
    restarted, _ = sdk_for(scope, provider)
    assert not restarted.execute_payment(request).executed
    assert provider.calls == 1
    record = ledger.get(request.request_id, org_id=scope[1], env_id=scope[2])
    assert record.status == "unknown"
    another, _ = sdk_for(scope, MockGateway())
    assert not another.execute_payment(payment(scope, 50)).executed


def test_hosted_sdk_concurrent_clients_cannot_oversubscribe(hosted_url):
    scope = setup_scope(hosted_url)
    barrier = threading.Barrier(2)
    gateways = [MockGateway(), MockGateway()]
    results = []
    errors = []
    def run(gateway):
        try:
            sdk, _ = sdk_for(scope, gateway)
            request = payment(scope)
            sdk.review_payment(request)
            barrier.wait(timeout=5)
            results.append(sdk.execute_payment(request))
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=run, args=(gateway,)) for gateway in gateways]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()
    assert not errors, errors
    assert sum(result.executed for result in results) == 1
    assert sum(len(gateway.charges) for gateway in gateways) == 1


def test_hosted_sdk_unavailable_authority_does_not_charge():
    class UnavailableTransport:
        def request(self, *args, **kwargs):
            raise TransportError("authority is unavailable")
    gateway = MockGateway()
    scope = (UnavailableTransport(), "org-unavailable", "env-unavailable", "agent-unavailable")
    sdk, _ = sdk_for(scope, gateway)
    result = sdk.execute_payment(payment(scope))
    assert not result.executed
    assert not gateway.charges


def test_hosted_sdk_concurrent_same_request_charges_once(hosted_url):
    scope = setup_scope(hosted_url)
    request = payment(scope)
    gateway = MockGateway()
    barrier = threading.Barrier(2)
    results = []

    def run():
        sdk, _ = sdk_for(scope, gateway)
        barrier.wait(timeout=5)
        results.append(sdk.execute_payment(request))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()
    assert len(results) == 2
    assert len(gateway.charges) == 1
    restarted, _ = sdk_for(scope, gateway)
    assert restarted.execute_payment(request).executed


def test_hosted_sdk_changed_payload_cannot_reuse_completed_id(hosted_url):
    scope = setup_scope(hosted_url)
    gateway = MockGateway()
    request = payment(scope, 40)
    first, _ = sdk_for(scope, gateway)
    assert first.execute_payment(request).executed
    changed = copy.deepcopy(request)
    changed.metadata["purpose"] = "different obligation"
    restarted, _ = sdk_for(scope, gateway)
    result = restarted.execute_payment(changed)
    assert not result.executed
    assert "request_digest_conflict" in result.error
    assert len(gateway.charges) == 1


class LoseResponse:
    """Commit one server operation, then lose its HTTP response."""

    def __init__(self, transport, suffix, status=None):
        self.transport = transport
        self.suffix = suffix
        self.status = status
        self.lost = False

    def request(self, method, path, json_body=None, headers=None):
        response = self.transport.request(method, path, json_body=json_body, headers=headers)
        if (not self.lost and method == "POST" and path.endswith(self.suffix)
                and (self.status is None or (json_body or {}).get("status") == self.status)):
            self.lost = True
            raise TransportError("response lost after server commit")
        return response


def test_hosted_sdk_lost_claim_response_holds_until_admin_reconciliation(hosted_url):
    scope = setup_scope(hosted_url)
    transport, org, env, agent = scope
    gateway = MockGateway()
    interrupted, _ = sdk_for((LoseResponse(transport, "/claim"), org, env, agent), gateway)
    request = payment(scope, 60)
    assert not interrupted.execute_payment(request).executed
    restarted, ledger = sdk_for(scope, gateway)
    assert restarted.execute_payment(request).status == "reserved"
    assert not gateway.charges
    assert not restarted.execute_payment(payment(scope, 50)).executed
    # The test operator has checked that this mock provider received no call.
    result = restarted.reconcile_payment(request.request_id, outcome=GatewayOutcome("failed", error="provider confirms no charge"))
    assert result.status == "failed"
    assert ledger.get(request.request_id, org_id=org, env_id=env).status == "failed"
    assert restarted.execute_payment(payment(scope, 50)).executed
    assert len(gateway.charges) == 1


def test_hosted_sdk_lost_confirmation_response_preserves_success(hosted_url):
    scope = setup_scope(hosted_url)
    transport, org, env, agent = scope
    gateway = MockGateway()
    interrupted, _ = sdk_for((LoseResponse(transport, "/report", "confirmed"), org, env, agent), gateway)
    request = payment(scope, 40)
    result = interrupted.execute_payment(request)
    assert result.executed and result.accounting_pending
    assert result.gateway_reference
    restarted, _ = sdk_for(scope, gateway)
    repeated = restarted.execute_payment(request)
    assert repeated.executed and repeated.replayed
    assert repeated.gateway_reference == result.gateway_reference
    assert len(gateway.charges) == 1


def test_hosted_sdk_unknown_can_reconcile_by_provider_lookup_after_restart(hosted_url):
    scope = setup_scope(hosted_url)

    class LostReceiptGateway(MockGateway):
        def charge_idempotent(self, request, idempotency_key):
            super().charge_idempotent(request, idempotency_key)
            raise TimeoutError("provider receipt was lost")

    gateway = LostReceiptGateway()
    first, _ = sdk_for(scope, gateway)
    request = payment(scope)
    assert first.execute_payment(request).status == "unknown"
    restarted, _ = sdk_for(scope, gateway)
    result = restarted.reconcile_payment(request.request_id)
    assert result.status == "confirmed" and result.executed
    assert len(gateway.charges) == 1
    assert restarted.execute_payment(request).gateway_reference == result.gateway_reference
