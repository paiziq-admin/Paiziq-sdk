"""Paiziq test payment agent (PZ-044) — everything through PaiziqSDK.

The agent holds no policy logic of its own: verdicts, budgets, audits,
failure handling and telemetry export are all delegated to the SDK.
Runs fully offline: the span exporter ships batches through the retrying
`SyncHTTPTransport` (PZ-033) against an in-memory HTTP endpoint; an
engine outage demonstrates the safe failure modes (PZ-035); duplicate
submissions return the recorded execution, new actions trip the velocity
guard, and a lost gateway response requires receipt reconciliation.

    python3 examples/payment_agent.py
"""

import io
import json
import urllib.error

from paiziq import (
    FailureMode,
    Mandate,
    PaiziqSDK,
    PaymentPolicy,
    PaymentRequest,
    RetryPolicy,
    SyncHTTPTransport,
    sign_webhook_payload,
    verify_webhook_signature,
)
from paiziq.tracing.tracer import HTTPExporter
from paiziq.audit import MockGateway


# ── an in-memory dashboard endpoint (keeps the example offline) ──────────────

class InMemoryDashboard:
    """Fake `urllib` opener: fails once, then accepts — so the run
    genuinely exercises the transport's retry/backoff path."""

    def __init__(self) -> None:
        self.batches: list[dict] = []
        self._failed_once = False

    def __call__(self, request, timeout=None):
        if not self._failed_once:
            self._failed_once = True
            raise urllib.error.URLError("transient network blip")
        self.batches.append(json.loads(request.data.decode()))
        response = io.BytesIO(b"{}")
        response.status = 200
        response.headers = {}
        return response


dashboard = InMemoryDashboard()
transport = SyncHTTPTransport(
    "https://dashboard.paiziq.test",
    api_key="demo-key",
    retry=RetryPolicy(max_attempts=3, base_delay_s=0.01),
    opener=dashboard,
)

# ── all policy lives in the SDK, none in the agent ───────────────────────────

policy = PaymentPolicy(
    review_threshold=100.0,
    hard_limit=1000.0,
    merchant_blocklist={"shady llc"},
    known_merchants={"acme corp", "cloudhost inc"},
    daily_budget=500.0,
)
sdk = PaiziqSDK(
    policy=policy,
    service_name="test-payment-agent",
    failure_mode=FailureMode.REVIEW_REQUIRED,
    exporters=[
        HTTPExporter("https://dashboard.paiziq.test", "demo-key",
                     flush_interval_s=0.05, transport=transport)
    ],
)

mandate = Mandate(
    principal_id="user-42",
    agent_id="test-payment-agent",
    max_amount=200.0,
    allowed_merchants=["acme corp", "cloudhost inc"],
    purpose="cloud + SaaS spend",
)


def propose(merchant: str, amount: float, intent: str) -> PaymentRequest:
    return PaymentRequest(
        agent_id="test-payment-agent",
        principal_id="user-42",
        merchant=merchant,
        amount=amount,
        category="software",
        intent_description=intent,
        mandate=mandate,
    )


# 1. Small known-merchant payment → approved and executed.
request = propose("acme corp", 49.99, "Renew Acme subscription")
decision = sdk.review_payment(request)
result = sdk.execute_payment(request)
print(f"[approved]  verdict={decision.status.value} executed={result.executed}")
assert result.executed

# 2. Over the review threshold → needs_review, blocked until a human approves.
request = propose("cloudhost inc", 180.0, "Annual CloudHost renewal")
decision = sdk.review_payment(request)
blocked = sdk.execute_payment(request)
sdk.approve_review(request.request_id, reviewer_id="cfo-1")
approved = sdk.execute_payment(request)
print(
    f"[review]    verdict={decision.status.value} "
    f"blocked={not blocked.executed} after_approval={approved.executed}"
)
assert not blocked.executed and approved.executed

# 3. Blocklisted merchant → rejected, execution refused.
request = propose("shady llc", 20.0, "Totally legitimate purchase")
decision = sdk.review_payment(request)
result = sdk.execute_payment(request)
print(f"[rejected]  verdict={decision.status.value} executed={result.executed}")
assert not result.executed

# 4. Decision engine outage → safe failure mode maps it to needs_review.
class BrokenEngine:
    def evaluate(self, request):
        raise RuntimeError("policy store unavailable")

healthy_engine, sdk.engine = sdk.engine, BrokenEngine()
request = propose("acme corp", 10.0, "Payment during an outage")
decision = sdk.review_payment(request)
print(f"[failure]   verdict={decision.status.value} reason={decision.reasons[0]}")
assert decision.status.value == "needs_review"
assert decision.reasons[0] == "failure_mode:review_required"
sdk.engine = healthy_engine

# 5. Repeat the logical request safely; a new request is subject to velocity.
dup_sdk = PaiziqSDK(
    policy=PaymentPolicy(
        review_threshold=100.0,
        hard_limit=1000.0,
        known_merchants={"acme corp"},
        max_tx_per_hour=1,  # further distinct requests require review
    ),
    service_name="test-payment-agent",
    exporters=[],
)
first = propose("acme corp", 15.0, "Monthly Acme add-on")
first_result = dup_sdk.execute_payment(first)
assert first_result.executed
repeated = dup_sdk.execute_payment(first)
assert repeated.executed and repeated.replayed
assert repeated.gateway_reference == first_result.gateway_reference
assert len(dup_sdk.gateway.charges) == 1
duplicate = propose("acme corp", 15.0, "Monthly Acme add-on")  # a new logical action
decision = dup_sdk.review_payment(duplicate)
print(f"[duplicate] verdict={decision.status.value} flags={[f.value for f in decision.risk_flags]}")
assert decision.status.value == "needs_review"
assert "velocity_anomaly" in [f.value for f in decision.risk_flags]

# 6. A gateway accepts the charge, then its response is lost. Do not retry.
class FlakyGateway(MockGateway):
    name = "flaky-mock"

    def charge_idempotent(self, request: PaymentRequest, idempotency_key: str) -> str:
        super().charge_idempotent(request, idempotency_key)
        raise TimeoutError("card network timeout after provider accepted")


healthy_gateway, sdk.gateway = sdk.gateway, FlakyGateway()
request = propose("acme corp", 25.0, "Payment during gateway outage")
spent_before = sdk.budget_tracker.daily_spend(request.agent_id)
uncertain = sdk.execute_payment(request)
assert not uncertain.executed and uncertain.status == "unknown"
# Unknown exposure remains reserved until the provider confirms its outcome.
assert sdk.budget_tracker.daily_spend(request.agent_id) == spent_before + request.amount
repeated = sdk.execute_payment(request)
assert repeated.status == "unknown" and repeated.replayed
assert len(sdk.gateway.charges) == 1
reconciled = sdk.reconcile_payment(request.request_id)
print(
    f"[gateway]   initial_status={uncertain.status} reconciled={reconciled.status} "
    f"ref={reconciled.gateway_reference}"
)
assert reconciled.executed and len(sdk.gateway.charges) == 1
sdk.gateway = healthy_gateway

# 7. Inbound webhook from Paiziq → verify its signature before trusting it.
secret = "whsec_" + "demo" * 8
payload = json.dumps({"event": "payment.approved", "request_id": request.request_id})
signature = sign_webhook_payload(payload, secret)
print(f"[webhook]   valid={verify_webhook_signature(payload, signature, secret)}")
assert verify_webhook_signature(payload, signature, secret)
assert not verify_webhook_signature(payload + " ", signature, secret)

# 8. Shut down: spans flush through the retrying transport.
sdk.shutdown()
spans = sum(len(batch["spans"]) for batch in dashboard.batches)
print(f"[telemetry] exported {spans} spans in {len(dashboard.batches)} batch(es) after 1 retry")
assert spans > 0

print("payment agent finished: all flows drove through PaiziqSDK")
