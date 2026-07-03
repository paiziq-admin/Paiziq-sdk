# Paiziq Agent Audit Tracer — Product Architecture

**Version:** 1.0 · **Status:** Accepted · **Scope:** Backend (SDK + ingest plane)

---

## 1. Context and Forces

The audit tracer must (a) sit on top of any LLM orchestration framework without dependency conflicts, (b) make payment decisions deterministically and fast on the hot path, (c) never break the host agent through observability failures, and (d) ship every trace to the Paiziq admin dashboard for security audit. These forces drive the two central decisions below.

## 2. Key Architecture Decisions

### ADR-001 — Zero-dependency core SDK (stdlib only)

| Option | Complexity | Compatibility | Verdict |
| --- | --- | --- | --- |
| A. Build on OpenTelemetry SDK | Medium | OTel version pins frequently conflict with LangChain/OpenAI stacks | Rejected for core |
| B. Build on Langfuse SDK | Low | Couples customers to Langfuse versions; no enforcement primitives | Rejected for core |
| C. Stdlib-only tracer + optional extras | Medium | Installs cleanly beside any framework; OTel/Langfuse bridges become exporters later | **Accepted** |

**Consequences:** we own a small tracer (≈250 LOC) and an HTTP batch exporter; in exchange, `pip install paiziq` can never break a customer's resolver, which is the #1 adoption blocker for instrumentation libraries. An OTLP-compatible exporter is a clean later addition because the `Exporter` protocol is the only seam.

### ADR-002 — Deterministic decisioning in the SDK, semantic judging server-side

The hot path (every payment) runs only deterministic rules and regex heuristics locally (< 5 ms). Deep semantic analysis of agent reasoning (LLM-as-judge, Langfuse evaluation pipelines) runs asynchronously in the Paiziq backend against the exported traces. This keeps agent latency flat, keeps the SDK key-less for LLM calls, and centralizes judge prompts where they can be updated without customer redeploys. The `HarmfulIntentRule` in the SDK is the conservative first line; the server judge is the second.

### ADR-003 — Enforce at the tool boundary

Payments are intercepted where the agent dispatches tools (decorator / LangChain callback / OpenAI tool-call guard), not by proxying the LLM API. Tool-boundary enforcement sees the exact transaction payload, works identically across model providers, and lets blocked verdicts flow back into the agent loop as structured errors the model can react to.

## 3. System Architecture

```
┌──────────────────────────  Customer Environment  ──────────────────────────┐
│                                                                            │
│  Agent app (LangChain / OpenAI SDK / CrewAI / custom)                      │
│        │ tool call: execute_payment(...)                                   │
│        ▼                                                                   │
│  ┌──────────────────────── Paiziq SDK (this library) ──────────────────┐   │
│  │ Integrations: @instrument_payment_tool · LangChain handler ·        │   │
│  │               OpenAI guard_tool_call                                 │   │
│  │        │                                                             │   │
│  │        ▼                                                             │   │
│  │ PaiziqSDK facade ── review_payment / execute_payment /               │   │
│  │                     approve_review / get_audit_trail                 │   │
│  │   │            │                │               │                    │   │
│  │   ▼            ▼                ▼               ▼                    │   │
│  │ DecisionEngine FourWayAuditor  NotificationRtr  AuditStore           │   │
│  │  ├ Threshold    ├ Identity      ├ harmful intent ├ InMemory          │   │
│  │  ├ Merchant     ├ Intent        ├ rejected       ├ JSONL             │   │
│  │  ├ UnknownMerch ├ Policy        └ needs_review   └ (Postgres impl)   │   │
│  │  ├ Budget       └ Transaction                                        │   │
│  │  ├ ReviewReq      (tamper check)                                     │   │
│  │  └ HarmfulIntent                                                     │   │
│  │   │                                                                  │   │
│  │   ▼                                                                  │   │
│  │ Tracer ──► Exporters (Console │ InMemory │ HTTP batch+retry) ────────┼───┼──┐
│  │                                                                      │   │  │
│  │ PaymentGateway protocol ──► MockGateway │ Stripe │ MC Agent Pay      │   │  │
│  └──────────────────────────────────────────────────────────────────────┘   │  │
└─────────────────────────────────────────────────────────────────────────────┘  │
                                                                                  │ HTTPS POST /v1/traces (Bearer)
┌──────────────────────────  Paiziq Cloud (AWS)  ─────────────────────────────┐   │
│  Ingest API (FastAPI on ECS Fargate, ALB) ◄──────────────────────────────────┘
│      │ validate key → enqueue                                                │
│      ▼                                                                       │
│  SQS buffer ──► Trace processor ──► PostgreSQL (RDS: traces, decisions,      │
│                      │              audit records, policies)                 │
│                      ├──► LLM Intent Judge (async, Langfuse pipeline)        │
│                      └──► Notification service ──► Slack / email / dashboard │
│  Admin Dashboard (React) ── trace explorer · review queue · policy mgmt ·    │
│                             security audit reports                           │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 4. Component Design (SDK)

| Module | Responsibility | Key types |
| --- | --- | --- |
| `paiziq.models` | Framework-agnostic domain model | `PaymentRequest`, `Mandate`, `Decision`, `DecisionStatus`, `RiskFlag`, `AuditRecord` |
| `paiziq.engine.policy` | Declarative policy + pluggable budget ledger | `PaymentPolicy`, `BudgetTracker`, `BudgetStore` protocol |
| `paiziq.engine.rules` | Six independent rules + `Rule` protocol for custom rules | `ThresholdRule`, `MerchantListRule`, `UnknownMerchantRule`, `BudgetRule`, `ReviewRequiredRule`, `HarmfulIntentRule` |
| `paiziq.engine.engine` | Severity-ordered aggregation preserving all reasons/flags | `DecisionEngine` |
| `paiziq.engine.audit4` | 4-Way Match with canonical transaction snapshot for tamper detection | `FourWayAuditor`, `transaction_snapshot` |
| `paiziq.tracing` | Spans, thread-local trace context, exporter fan-out | `Tracer`, `Span`, `HTTPExporter` |
| `paiziq.tracing.integrations` | Lazy framework adapters | decorator, LangChain handler, OpenAI guard, `PaymentBlockedError` |
| `paiziq.notifications` | Decision→alert mapping, webhook delivery | `NotificationRouter`, `WebhookNotifier` |
| `paiziq.audit` | Append-only trail + gateway abstraction | `AuditStore`, `JSONLAuditStore`, `PaymentGateway`, `MockGateway` |
| `paiziq.transport` | Sync/async stdlib HTTP with shared retry/backoff policy | `SyncHTTPTransport`, `AsyncHTTPTransport`, `RetryPolicy`, `TransportError` |
| `paiziq.logging` | Structured `key=value` logs, debug toggle, secret redaction | `log_event`, `debug()`, `get_logger` |
| `paiziq.webhooks` | Inbound webhook authenticity (HMAC-SHA256 + replay window) | `verify_webhook_signature`, `sign_webhook_payload` |
| `paiziq.sdk` | Developer facade orchestrating all of the above | `PaiziqSDK`, `FailureMode` |

## 5. Critical Flows

**Review flow.** `review_payment` opens a span, runs all rules, aggregates the verdict (rejected > needs_review > approved), stores `(Decision, transaction_snapshot)` keyed by request, appends an audit record, routes notifications, and annotates the span with verdict + risk flags. No money moves.

**Execution flow.** `execute_payment` retrieves (or creates) the review, applies the human-approval override for `needs_review` if `approve_review` was recorded, then runs the 4-Way Match: identity vs mandate, intent vs mandate bounds, policy verdict, and the tamper check comparing the live payload to the reviewed snapshot. Only a fully passing audit reaches `PaymentGateway.charge()`. Spend commits to the budget ledger only after successful execution, so concurrent reviews can't double-reserve.

**Trace flow.** Spans queue to a bounded buffer; a daemon thread batches (size or interval), POSTs to `{endpoint}/v1/traces` with exponential-backoff retries, and drops with a warning under sustained backpressure. The invariant throughout the SDK: observability and notification failures are logged, never raised.

**Failure flow.** If the decision engine itself raises unexpectedly, `PaiziqSDK` never propagates the exception to the agent: the configured `FailureMode` maps the outage to a deterministic verdict (fail-open → approved, fail-closed → rejected — the default, review-required → needs_review) with a machine-readable `failure_mode:*` reason and an audit entry.

## 6. Data Contracts (wire-level, language-neutral)

`POST /v1/traces` body: `{"spans": [{name, trace_id, span_id, parent_span_id, start_ms, end_ms, status, attributes, events}]}` — decision payloads ride in span events (`decision`, `four_way_audit`). Notification webhook body: `{severity, title, message, request_id, risk_flags, created_at_ms}`. Both are stable JSON so a TypeScript SDK can target the same ingest plane. The full backend API contract (envelope, auth scopes, tenancy, payments/decisions/reviews/policies/audit logs, per-endpoint status) lives in `06_API_CONTRACT.md`.

## 7. Security Architecture

Authentication is per-customer Bearer API keys (env `PAIZIQ_API_KEY`), transported over TLS only. The SDK holds no payment credentials — gateways are injected by the host app. Audit stores are append-only by contract; records carry trace correlation IDs for forensic reconstruction. The harmful-intent path is fail-safe: detection escalates and notifies rather than silently passing. Production hardening (Phase 1): payload PII scrubbing hooks before export, key rotation, and HMAC request signing for the ingest API.

## 8. Deployment & Operations

**SDK distribution:** built with `python -m build`, published to a private index (AWS CodeArtifact) then PyPI at GA; semantic versioning with the wire contract frozen at v1. **Ingest plane (Phase 1):** FastAPI on ECS Fargate behind an ALB, SQS buffering between ingest and processing, RDS PostgreSQL for traces/audit/policies, ElastiCache Redis for the shared `BudgetStore`, Secrets Manager for keys, GitHub Actions for CI/CD (ruff + mypy + pytest gate, build, deploy). **Observability of the observer:** exporter delivery metrics, queue-drop counters, and ingest 5xx alarms in CloudWatch.

## 9. What Becomes Easier / Harder

Easier: adding rules (protocol + one test file), adding frameworks (one adapter file), adding exporters (one class), and swapping stores/gateways (protocols everywhere). Harder: we own tracer code instead of reusing OTel (mitigated by its small size), and budget consistency across processes requires the Redis store. Revisit at GA: OTLP exporter bridge, SD-JWT mandate signing, and dashboard-driven review round-trip.
