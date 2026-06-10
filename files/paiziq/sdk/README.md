# Paiziq SDK — Agent Audit Tracer for Payment Agents

Paiziq is a tracer library that sits **on top of** any LLM orchestration framework (LangChain, OpenAI SDK, CrewAI, custom agents) and audits the intent of every payment an AI agent attempts — before money moves.

It gives you:

- **Decision engine** — threshold checks, merchant allowlist/blocklist, unknown-merchant detection, budget validation, and review-required logic. Every verdict is `approved`, `needs_review`, or `rejected` with human-readable reasons and machine-readable risk flags.
- **4-Way Match audit** — pre-execution verification of Identity, Intent, Policy, and Transaction. A payment proceeds only if all four pass.
- **Harmful-intent notification** — suspicious agent reasoning triggers a critical alert to the user.
- **Tracing** — every review, audit, and execution streams to the Paiziq admin dashboard as spans (batched, retried, never blocking the agent).
- **Immutable audit trail** — `get_audit_trail()` returns the full event history per payment.

The core SDK has **zero runtime dependencies** — stdlib only — so it never conflicts with your framework's pins.

## Install

```bash
pip install paiziq                 # core
pip install paiziq[langchain]      # + LangChain callback handler
pip install paiziq[openai]         # + OpenAI tool-call guard
```

Local development:

```bash
cd sdk && pip install -e .[dev] && pytest
```

## Quickstart

```python
from paiziq import PaiziqSDK, PaymentPolicy, PaymentRequest

sdk = PaiziqSDK(
    policy=PaymentPolicy(
        review_threshold=100,            # > $100 → human review
        hard_limit=1000,                 # > $1000 → rejected
        merchant_blocklist={"shady llc"},
        known_merchants={"acme corp"},   # anything else → unknown-merchant flag
        daily_budget=500,
    ),
    api_key="pzq_...",                              # or PAIZIQ_API_KEY
    dashboard_endpoint="https://ingest.paiziq.com", # or PAIZIQ_ENDPOINT
)

request = PaymentRequest(
    agent_id="procurement-agent",
    principal_id="user-42",
    merchant="acme corp",
    amount=49.99,
    intent_description="Renew the team's monthly Acme subscription",
)

decision = sdk.review_payment(request)
# decision.status      -> DecisionStatus.APPROVED | NEEDS_REVIEW | REJECTED
# decision.reasons     -> ["All decision rules passed"]
# decision.risk_flags  -> [RiskFlag....]

result = sdk.execute_payment(request)   # 4-way audit, then gateway charge
trail  = sdk.get_audit_trail(request.request_id)
```

## API

| Method | Purpose |
| --- | --- |
| `review_payment(request) -> Decision` | Evaluate against all rules. No money moves. |
| `execute_payment(request) -> ExecutionResult` | Run the 4-Way Match audit, then charge the configured gateway. `needs_review` payments execute only after `approve_review()`. |
| `approve_review(request_id, reviewer_id)` | Record human approval for a flagged payment. |
| `get_audit_trail(request_id=None, limit=100) -> list[dict]` | Immutable event history. |
| `shutdown()` | Flush trace exporters. |

## Framework integrations

**Any framework** — wrap the payment tool:

```python
from paiziq import instrument_payment_tool, PaymentBlockedError

@instrument_payment_tool(sdk, extract=lambda merchant, amount: PaymentRequest(
    agent_id="a1", principal_id="u1", merchant=merchant, amount=amount))
def pay(merchant: str, amount: float): ...
```

**LangChain** — attach the callback handler:

```python
from paiziq import create_langchain_handler
handler = create_langchain_handler(sdk, payment_tools={"execute_payment"})
agent.invoke(inputs, config={"callbacks": [handler]})
```

**OpenAI SDK** — guard tool calls in your dispatch loop:

```python
from paiziq import guard_tool_call
for call in response.choices[0].message.tool_calls:
    guard_tool_call(sdk, call.function.name, call.function.arguments)
```

Blocked payments raise `PaymentBlockedError` carrying the full `Decision`, so the agent loop can surface the reasons back to the model or the user.

## Extensibility

- **Custom rules**: implement the `Rule` protocol and `engine.add_rule(...)`.
- **Budget store**: implement `BudgetStore` (Redis/Postgres) and pass a `BudgetTracker(store)`.
- **Audit store**: implement `AuditStore`; `JSONLAuditStore` ships for durable local trails.
- **Gateways**: implement `PaymentGateway.charge()`; `MockGateway` ships for sandboxes.
- **Notifiers**: implement `Notifier.send()`; `WebhookNotifier` ships for Slack/dashboard fan-out.

## Project layout

```
sdk/
├── pyproject.toml
├── src/paiziq/
│   ├── sdk.py                  # PaiziqSDK facade
│   ├── models.py               # PaymentRequest, Decision, RiskFlag, ...
│   ├── engine/                 # rules, decision engine, 4-way audit, policy/budget
│   ├── tracing/                # tracer, exporters, framework integrations
│   ├── notifications/          # harmful-intent + review alerts
│   └── audit/                  # audit stores + gateway abstraction
├── tests/                      # 44 unit + e2e tests
└── examples/                   # happy path + framework integrations
```
