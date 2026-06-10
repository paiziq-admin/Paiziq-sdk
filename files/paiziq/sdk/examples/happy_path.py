"""Paiziq happy-path example — runs offline with the mock gateway.

    python examples/happy_path.py
"""

from paiziq import Mandate, PaiziqSDK, PaymentPolicy, PaymentRequest

# 1. Configure policy — the rules the decision engine enforces.
policy = PaymentPolicy(
    review_threshold=100.0,
    hard_limit=1000.0,
    merchant_blocklist={"shady llc"},
    known_merchants={"acme corp", "cloudhost inc"},
    daily_budget=500.0,
    review_categories={"gift_cards"},
)

# 2. Initialize the SDK. Set PAIZIQ_API_KEY / PAIZIQ_ENDPOINT env vars to
#    stream traces to the Paiziq admin dashboard; otherwise spans log locally.
sdk = PaiziqSDK(policy=policy, service_name="demo-procurement-agent")

# 3. The agent proposes a payment, bound to the human's mandate.
mandate = Mandate(
    principal_id="user-42",
    agent_id="procurement-agent",
    max_amount=200.0,
    allowed_merchants=["acme corp", "cloudhost inc"],
    purpose="monthly SaaS renewals",
)
request = PaymentRequest(
    agent_id="procurement-agent",
    principal_id="user-42",
    merchant="acme corp",
    amount=49.99,
    category="software",
    intent_description="Renew the team's monthly Acme subscription",
    mandate=mandate,
)

# 4. Review → approved / needs_review / rejected, with reasons + risk flags.
decision = sdk.review_payment(request)
print(f"verdict:    {decision.status.value}")
print(f"reasons:    {decision.reasons}")
print(f"risk flags: {[f.value for f in decision.risk_flags]}")

# 5. Execute → 4-way audit, then gateway charge.
result = sdk.execute_payment(request)
print(f"executed:   {result.executed} (ref={result.gateway_reference})")
print(f"4-way:      {[ (c.dimension.value, c.passed) for c in decision.four_way_audit.checks ]}")

# 6. Audit trail — everything is on the record.
for event in sdk.get_audit_trail(request.request_id):
    print(f"audit:      {event['event_type']} @ {event['recorded_at_ms']}")

sdk.shutdown()
