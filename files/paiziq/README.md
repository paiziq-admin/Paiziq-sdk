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
pip3 install paiziq                 # core
pip3 install paiziq[langchain]      # + LangChain callback handler
pip3 install paiziq[openai]         # + OpenAI tool-call guard
pip3 install paiziq[redis]          # + RedisBudgetStore
pip3 install paiziq[postgres]       # + PostgresAuditStore
```

Local development (all commands are automated — see `make help`):

```bash
make venv install   # python3 -m venv + editable install with dev extras
make test           # SDK test suite
make check          # full quality gate: lint + tests + examples
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

Utilities shipped alongside the facade: `SyncHTTPTransport` /
`AsyncHTTPTransport` (stdlib HTTP with shared `RetryPolicy`
retry/backoff), `FailureMode` (`fail_open` / `fail_closed` /
`review_required` degradation when decisioning infrastructure fails;
default fail-closed), `paiziq.debug()` + `log_event` structured logging
with secret redaction, and `verify_webhook_signature` /
`sign_webhook_payload` (HMAC-SHA256 with replay-window check).

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

## CLI

The package installs a `paiziq` command for working with the backend:

```bash
paiziq init --endpoint http://127.0.0.1:8800
paiziq login --api-key <key>          # verified, then stored (chmod 0600)
paiziq agents list
paiziq keys create --name ci --scope ingest --env <env_id>   # secret shown once
paiziq dashboard deploy && paiziq dashboard serve            # local dashboard
paiziq replay <trace_id>              # pretty-print a trace's span tree
```

## Extensibility

- **Custom rules**: implement the `Rule` protocol and `engine.add_rule(...)`.
- **Budget store**: implement `BudgetStore` (Redis/Postgres) and pass a `BudgetTracker(store)`.
- **Audit store**: implement `AuditStore`; `JSONLAuditStore` ships for durable local trails.
- **Gateways**: implement `PaymentGateway.charge()`; `MockGateway` ships for sandboxes.
- **Notifiers**: implement `Notifier.send()`; `WebhookNotifier` ships for Slack/dashboard fan-out.
- **Transports**: inject a custom `opener`/`RetryPolicy` into `SyncHTTPTransport`/`AsyncHTTPTransport`, or pass `transport=` to `HTTPExporter`.

## Project layout

```
paiziq/
├── Makefile                    # automated commands (make help)
├── CHANGELOG.md                # versioned, per-change history
├── docs/                       # scope, architecture, plan, guide, tracker
├── sdk/
│   ├── pyproject.toml
│   ├── src/paiziq/
│   │   ├── sdk.py              # PaiziqSDK facade
│   │   ├── models.py           # PaymentRequest, Decision, RiskFlag, ...
│   │   ├── engine/             # rules, 4-way audit, policy, budget stores
│   │   ├── tracing/            # tracer, exporters, PII scrub, integrations
│   │   ├── transport.py        # sync/async HTTP with retry/backoff
│   │   ├── logging.py          # structured logs, debug mode, redaction
│   │   ├── webhooks.py         # HMAC webhook signature verification
│   │   ├── notifications/      # harmful-intent + review alerts
│   │   └── audit/              # audit stores + gateway abstraction
│   ├── tests/                  # 165 unit, property-based, and e2e tests
│   └── examples/               # happy path, integrations, payment agent
└── services/ingest/            # FastAPI ingest + control plane (+ 80 tests)
```

## Documentation

| Document | Purpose |
| --- | --- |
| [Product scope](docs/01_PRODUCT_SCOPE.md) | Why Paiziq exists, market, positioning |
| [Architecture](docs/02_ARCHITECTURE.md) | System design, invariants, wire contract |
| [Build & deploy plan](docs/03_BUILD_DEPLOY_PLAN.md) | Phased delivery plan and risks |
| [Developer guide](docs/04_DEVELOPER_GUIDE.md) | Setup, workflow, conventions, releasing |
| [Progress tracker](docs/05_PROGRESS_TRACKER.md) | Human-readable implementation status |
| [API contract](docs/06_API_CONTRACT.md) | Canonical backend HTTP API contract (v1) |
| [Changelog](CHANGELOG.md) | Per-change history with versions |
