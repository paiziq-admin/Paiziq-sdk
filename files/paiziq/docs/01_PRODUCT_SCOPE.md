# Paiziq Agent Audit Tracer — Product Scope & Technical Acceptance Criteria

**Version:** 1.0 · **Date:** June 2026 · **Scope:** Backend SDK (tracer library) only

---

## 1. Problem Statement

When AI agents execute payments autonomously, three questions go unanswered: did the principal authorize the transaction, did the agent follow instructions exactly, and can anyone prove what happened afterward? Enterprises deploying payment agents on LangChain, the OpenAI SDK, and similar frameworks have no standard layer that audits agent intent before money moves. The cost of not solving this is direct financial loss from runaway or manipulated agents, plus an accountability gap that blocks enterprise adoption of agentic commerce.

## 2. Product Definition

The **Paiziq Agent Audit Tracer** is a Python SDK that developers install on top of their existing agent stack. It intercepts payment actions, evaluates them against a declarative policy through a deterministic decision engine, enforces a **4-Way Match audit** (Identity, Intent, Policy, Transaction) before execution, notifies the user on harmful intent, and streams every trace to the **Paiziq admin dashboard** for security audit and review workflows.

It is a tracer-style library in the spirit of Langfuse/OpenTelemetry — but instead of only observing, it **decides and enforces**.

## 3. Goals

1. A developer can instrument an existing payment agent in under 15 minutes with fewer than 10 lines of code.
2. 100% of agent payment attempts produce an explainable verdict (`approved` / `needs_review` / `rejected`) with reasons and risk flags.
3. Zero payments execute without passing the 4-Way Match audit; tampering between review and execution is detected.
4. Harmful or evasive agent intent triggers a user notification within seconds of detection.
5. Every review, audit verdict, and execution is visible in the Paiziq admin dashboard as a trace with a complete, immutable audit trail.

## 4. Non-Goals (v1)

- **Building the dashboard frontend** — the SDK targets the dashboard's ingest API contract; the React dashboard is a separate workstream.
- **Real payment-gateway certification** — v1 ships a gateway abstraction with a mock/sandbox implementation; Stripe/Mastercard Agent Pay connectors are fast-follows.
- **LLM-as-judge semantic intent analysis in the SDK** — the SDK runs fast deterministic/heuristic checks locally; deep semantic judging happens server-side (Langfuse-style pipeline) where latency is acceptable.
- **Cryptographic SD-JWT intent tokens** — the `Mandate` model is designed to carry SD-JWT claims later, but signing/verification is deferred to v2 to keep v1 dependency-free.
- **Non-Python runtimes** — TypeScript SDK is a roadmap item; the wire protocol is language-neutral JSON to enable it.

## 5. Target Users & User Stories

- As an **AI engineer**, I want to wrap my agent's payment tool with one decorator so that every payment is reviewed without rewriting my orchestration code.
- As a **platform/security engineer**, I want declarative policies (thresholds, merchant lists, budgets) so that controls live in configuration, not in prompts.
- As a **finance owner (CFO/controller)**, I want payments above a threshold held for my approval, and I want an audit trail proving what the agent intended versus what it did.
- As an **end user/principal**, I want an immediate notification when my agent shows harmful intent or a payment is blocked, so I stay in control.

## 6. Scope of Work (Workstreams)

### WS-1 · SDK package initialization (`/sdk`)
Python package structure under `src/` layout, `pyproject.toml`, package entrypoint (`paiziq.__init__`), pytest test structure.

**Acceptance criteria**
- [x] SDK can be imported locally (`pip install -e . && python -c "import paiziq"`)
- [x] Project structure is clean and runnable (src layout, typed dataclasses, no circular imports)
- [x] Unit test setup exists (`pytest` configured in pyproject, `tests/` collects and runs)

### WS-2 · Payment decision rules in the SDK
Move all decision logic into the SDK's `DecisionEngine` with independent, composable rules: threshold checks, merchant allowlist/blocklist, unknown-merchant detection, budget validation (daily/monthly, warning ratio), and review-required logic (categories, currency, velocity).

**Acceptance criteria**
- [x] Engine returns exactly one of `approved`, `needs_review`, `rejected` (severity: rejected > needs_review > approved)
- [x] Reasons (human-readable) and risk flags (machine-readable enum) are included on every decision, preserving findings from all rules — not just the deciding one
- [x] Rules pass unit tests (each rule tested in isolation: happy path, boundary, and negative cases; 44 tests green)
- [x] Custom rules can be registered via the `Rule` protocol without forking the SDK

### WS-3 · Developer-facing API
`PaiziqSDK` facade exposing `review_payment(...)`, `execute_payment(...)`, `get_audit_trail(...)` (plus `approve_review(...)` for the human-in-the-loop path).

**Acceptance criteria**
- [x] API is simple enough to use from a test agent (plain dataclasses in/out; the happy-path example is ~30 lines)
- [x] No framework-specific concepts leak into the interface (LangChain/OpenAI live only in `tracing/integrations`, imported lazily)
- [x] Happy-path example works end-to-end (`examples/happy_path.py`: review → 4-way audit → execution → audit trail)

### WS-4 · 4-Way Match audit policy
Pre-execution verification: (1) Identity — principal/agent match the mandate; (2) Intent — transaction within mandate bounds (amount, merchant scope, currency, expiry); (3) Policy — decision-engine verdict permits execution; (4) Transaction — payload identical to what was reviewed (tamper detection via canonical snapshot).

**Acceptance criteria**
- [x] A payment executes only if all four dimensions pass
- [x] Each dimension reports pass/fail with a detail string, recorded on the trace and audit trail
- [x] Post-review payload tampering is detected and blocks execution

### WS-5 · Tracing & dashboard integration
Dependency-free tracer (spans, trace context, nesting) with pluggable exporters; batched `HTTPExporter` ships spans to the Paiziq dashboard ingest API (`POST /v1/traces`, Bearer auth) with retry/backoff on a daemon thread.

**Acceptance criteria**
- [x] Every `review_payment`/`execute_payment` call emits spans carrying verdict, risk flags, and 4-way results
- [x] Export is asynchronous and lossy-by-design under backpressure — observability never blocks or crashes the agent
- [x] Exporter failures are logged, never raised into the host application

### WS-6 · Notifications & security audit
Notification router maps decisions to alerts: harmful intent → critical, rejection → warning, needs-review → info. `WebhookNotifier` delivers to the Paiziq notification service (Slack/email fan-out server-side). Append-only audit stores (in-memory, JSONL; Postgres via protocol).

**Acceptance criteria**
- [x] Harmful-intent detection fires a critical notification naming the agent, merchant, amount, and reasons
- [x] Audit trail is append-only and queryable per request, with trace correlation IDs
- [x] Notification delivery failure never interrupts the payment flow

### WS-7 · Framework compatibility
Three integration styles funneling into one SDK: a generic decorator for any framework, a LangChain `BaseCallbackHandler`, and an OpenAI tool-call guard. Extras-based installs (`paiziq[langchain]`, `paiziq[openai]`); core stays zero-dependency.

**Acceptance criteria**
- [x] Core `pip install paiziq` pulls zero third-party packages (no version conflicts with host frameworks)
- [x] Blocked payments raise `PaymentBlockedError` carrying the full `Decision` so agent loops can self-correct
- [x] Integrations import their framework lazily and fail with actionable install guidance

## 7. Success Metrics

**Leading:** time-to-first-trace for a new integration (< 15 min target); % of payment attempts with a verdict (100%); SDK overhead per review (< 5 ms p95, deterministic rules only); trace delivery success rate (> 99.5% with retries).
**Lagging:** blocked-loss value surfaced in the dashboard; design-partner adoption (3 pilot integrations in Q3); reduction in unreviewed agent spend at pilot accounts (> 90%).

## 8. Open Questions

- **(Product/Security)** Should `needs_review` approvals flow back from the dashboard via webhook/polling in v1, or remain SDK-local (`approve_review`)? v1 ships SDK-local; dashboard round-trip is designed but not built.
- **(Engineering)** Budget state in multi-process agent fleets requires the Redis `BudgetStore`; confirm pilot deployment topology.
- **(Legal/Compliance)** Audit-record retention period and PII handling in `intent_description` for SOC 2 scope.

## 9. Timeline & Phasing

- **Phase 0 (done — this scaffold):** package, decision engine, 4-way audit, tracer + exporters, notifications, integrations, tests, examples.
- **Phase 1 (Weeks 1–3):** dashboard ingest API hardening, Redis budget store, Postgres audit store, CI/CD, packaging to a private index.
- **Phase 2 (Weeks 4–6):** dashboard review round-trip, Stripe sandbox gateway, server-side LLM intent judge, pilot integration with first design partner.
- **Phase 3 (Weeks 7–9):** SD-JWT mandate signing, Mastercard Agent Pay sandbox, TypeScript SDK kickoff.
