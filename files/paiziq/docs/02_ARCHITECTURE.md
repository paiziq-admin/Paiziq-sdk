# Paiziq Agent Audit Tracer — Product Architecture

**Version:** 1.1 · **Status:** Accepted core; hosted identity/subscription addendum proposed · **Scope:** Backend (SDK + ingest/control plane)

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

### Proposed ADR-004 — Separate human identity from machine identity

Hosted dashboard users authenticate through OAuth 2.0/OpenID Connect
Authorization Code flow with PKCE. Paiziq maps the stable `(issuer,
subject)` pair to an internal user, then issues its own opaque,
server-managed browser session. Environment-bound API keys remain the
identity for SDKs, agents, CI, CLI automation, and webhook clients. A
human membership role and an API-key role are never interchangeable.

This ADR is proposed by the account/subscription plan and is not yet
implemented. It follows the OAuth security BCP's PKCE, exact redirect,
state/nonce, and issuer-validation guidance. Browser credentials must not
be stored in local or session storage.

### Proposed ADR-005 — Internal subscriptions and entitlements authorize access

The billing provider records collection facts; it does not authorize a
Paiziq request. Verified provider events are reduced into Paiziq's
versioned internal subscription state, compiled entitlements, and usage
counters. Access checks read only those internal records. During a
provider outage, previously verified access remains valid until an
explicit internal lifecycle deadline is reached.

### Proposed ADR-006 — Isolate SaaS billing from protected payments

The existing `payments` aggregate represents AI-agent payment proposals
that Paiziq protects. Paiziq's own commercial charges live in a separate
billing bounded context with names such as `billing_invoices`,
`billing_payments`, and `billing_refunds`. The planned SDK
`StripeGateway` also belongs to the agent-payment execution boundary and
must not be reused as the subscription-billing adapter.

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

**Control-plane review flow.** A `needs_review` decision opens a
deadline-bearing review row. A later `needs_review` re-evaluation creates
a new immutable decision but reuses the canonical open review and points
it at the new decision. The dashboard reads `/v1/reviews` and
`/v1/reviews/identity`; database-managed keys are bound to their key
name, role, and environment. Reviewer/admin actions enforce optimistic
ownership and nonblank notes. Generic payment transitions and a
re-evaluation whose verdict becomes terminal both reject attempts to
bypass an open review. Approval or decline verifies that the review is
open and the payment is still `needs_review`, then updates the review,
payment, and append-only payment transition in one SQLite transaction.
The audit entry and signed `review.*` webhook fan-out follow the commit.
That atomic boundary intentionally covers only review/payment/transition
state. Audit insertion and webhook enqueue are subsequent transactions;
guaranteed all-or-nothing side effects require a future outbox. The
generic-payment bypass check is also a route-level check before the
payment transition rather than a database constraint, leaving a narrow
concurrent-review-creation race in the current SQLite implementation.

**Execution flow.** `execute_payment` retrieves (or creates) the review, applies the human-approval override for `needs_review` if `approve_review` was recorded, then runs the 4-Way Match: identity vs mandate, intent vs mandate bounds, policy verdict, and the tamper check comparing the live payload to the reviewed snapshot. Only a fully passing audit reaches `PaymentGateway.charge()`. Spend commits to the budget ledger only after successful execution, so concurrent reviews can't double-reserve.

**Trace flow.** Spans queue to a bounded buffer; a daemon thread batches (size or interval), POSTs to `{endpoint}/v1/traces` with exponential-backoff retries, and drops with a warning under sustained backpressure. The invariant throughout the SDK: observability and notification failures are logged, never raised.

**Failure flow.** If the decision engine itself raises unexpectedly, `PaiziqSDK` never propagates the exception to the agent: the configured `FailureMode` maps the outage to a deterministic verdict (fail-open → approved, fail-closed → rejected — the default, review-required → needs_review) with a machine-readable `failure_mode:*` reason and an audit entry.

## 6. Data Contracts (wire-level, language-neutral)

`POST /v1/traces` body: `{"spans": [{name, trace_id, span_id, parent_span_id, start_ms, end_ms, status, attributes, events}]}` — decision payloads ride in span events (`decision`, `four_way_audit`). Notification webhook body: `{severity, title, message, request_id, risk_flags, created_at_ms}`. Both are stable JSON so a TypeScript SDK can target the same ingest plane. Dashboard query surfaces perform payment filtering/sorting, payment/review webhook correlation, and metrics aggregation server-side before pagination. The full backend API contract (envelope, auth scopes, tenancy, payments/decisions/reviews/policies/audit logs, per-endpoint status) lives in `06_API_CONTRACT.md`.

## 7. Security Architecture

Authentication is per-customer Bearer API keys (env `PAIZIQ_API_KEY`), transported over TLS only. The SDK holds no payment credentials — gateways are injected by the host app. Audit stores are append-only by contract; records carry trace correlation IDs for forensic reconstruction. The harmful-intent path is fail-safe: detection escalates and notifies rather than silently passing. Production hardening (Phase 1): payload PII scrubbing hooks before export, key rotation, and HMAC request signing for the ingest API.

## 8. Deployment & Operations

**SDK distribution:** built with `python -m build`, published to a private index (AWS CodeArtifact) then PyPI at GA; semantic versioning with the wire contract frozen at v1. **Ingest plane (Phase 1):** FastAPI on ECS Fargate behind an ALB, SQS buffering between ingest and processing, RDS PostgreSQL for traces/audit/policies, ElastiCache Redis for the shared `BudgetStore`, Secrets Manager for keys, GitHub Actions for CI/CD (ruff + mypy + pytest gate, build, deploy). The current SQLite service applies numbered migrations exactly once, in order, and transactionally at database open; migration `0008` extends existing review rows in place, backfills `updated_at_ms`, and adds review queue indexes. **Observability of the observer:** exporter delivery metrics, queue-drop counters, and ingest 5xx alarms in CloudWatch.

## 9. What Becomes Easier / Harder

Easier: adding rules (protocol + one test file), adding frameworks (one adapter file), adding exporters (one class), and swapping stores/gateways (protocols everywhere). Harder: we own tracer code instead of reusing OTel (mitigated by its small size), and budget consistency across processes requires the Redis store. Revisit at GA: OTLP exporter bridge, SD-JWT mandate signing, and the SDK callback bridge from a signed control-plane review result into local `approve_review` state.

### Security extensions (PZ-073–084)

- **RBAC:** API keys carry a `role` column (`admin`, `developer`, `reviewer`,
  `read_only`) mapped to allowed scopes in `auth.py`.
- **Webhook engine:** `event_router.py` enqueues signed deliveries;
  `webhook_worker.py` polls the retry queue on the FastAPI lifespan task.
- **Secrets at rest:** webhook signing secrets are optionally Fernet-encrypted
  via `PAIZIQ_SECRETS_KEY` (`field_secrets.py`; API key hashes remain SHA-256).
- **Rate limiting:** in-memory token bucket per key prefix (`rate_limit.py`).
- **Retention:** configurable purge of spans, notifications, and audit rows
  (`retention.py`), with audit minimum 365 days when enabled.
- **Human review:** reviewer/admin mutations are isolated behind the
  `review` capability. Database-managed identities are key-name,
  environment, and role bound; optimistic assignment conflicts return
  `409`; and final decisions require a non-blank operator note.

## 10. Proposed Hosted Identity and Subscription Architecture

This section defines the implementation target for the draft commercial
model in `01_PRODUCT_SCOPE.md`, section 10. It is deliberately
provider-adaptable and does not add implemented API contracts.

### 10.1 P0 isolation and deployability prerequisites

The current service must not be exposed to unrelated paying
organizations until these gaps are closed:

- `AuthContext` carries an environment for database-managed API keys, but
  most route dependencies return only the secret and discard that
  boundary. Only the human-review routes currently apply consistent
  environment checks.
- Organization, agent, payment, policy, metrics, API-key, webhook, and
  audit list/detail/mutation paths need deny-by-default organization and
  environment scoping plus cross-tenant tests.
- `spans`, `notifications`, and `audit_log` currently lack organization
  and environment ownership. They require a backfilled ownership model
  before safe customer queries, entitlements, retention, or billing
  metrics.
- Production bootstrap keys must become vault-controlled break-glass
  credentials, never browser credentials. Normal production access uses
  user sessions or scoped machine keys.
- The current ingest Dockerfile does not copy all imported service
  modules or install the SDK used by decision routes. A reproducible
  production image, managed database, queue/worker, backups, readiness
  checks, and image smoke test are prerequisites for paid production.
- Audit retention cannot be implemented by deleting from the current
  append-only table. Archive/partition/WORM retention must preserve the
  append-only invariant.

Every resource query and mutation must derive its organization/environment
scope from the authenticated principal. A caller-supplied identifier may
narrow that scope but can never widen it. Out-of-tenant identifiers return
the same not-found behavior as nonexistent identifiers.

### 10.2 Identity, session, and organization model

```text
OIDC issuer
  └─ (issuer, subject) ──► oauth_identity ──► user
                                               │
                                      organization_membership
                                               │
                                               ▼
                                         organization
                                          ├─ environments
                                          │   ├─ API keys
                                          │   └─ agents
                                          └─ billing_account
```

Planned records:

- `users`: internal identity, status, display profile, verified contact
  snapshots, and account timestamps.
- `oauth_identities`: provider, immutable issuer, immutable subject, user
  link, and last verified claims. Email is a contact attribute, not the
  identity key.
- `user_sessions`: hashed opaque session secret, user, created/last-seen
  timestamps, absolute and idle expiry, rotation/revocation, and security
  metadata.
- `organization_memberships`: organization, user, role, active/suspended
  state, and lifecycle timestamps.
- `organization_invitations`: normalized recipient, intended role,
  single-use hashed token, inviter, expiry, and acceptance state.
- `billing_accounts`: one-to-one organization link for the initial
  release, billing contact, provider customer reference, currency, tax
  profile reference, and commercial status.

On first successful login, Paiziq creates or links the internal user and
starts onboarding. A personal organization may be created only after the
user explicitly confirms its name/terms. Joining an invited organization
does not create another billable account unless the user chooses to.
Distinct identities are never auto-linked by matching email alone.
Linking another provider requires an active user session, fresh
reauthentication with both identities where possible, and an audited
conflict/recovery path.

Browser authentication uses a backend-for-frontend session:

1. Generate transaction-specific PKCE S256 verifier/challenge, state, and
   OIDC nonce; bind them to a short-lived server record.
2. Validate exact callback URI, issuer, signature, audience, time claims,
   state, nonce, and PKCE before identity linking.
3. Regenerate the Paiziq session after login and privilege changes.
4. Set a `__Host-` prefixed, host-only `HttpOnly`, `Secure`,
   `SameSite=Lax` production cookie; use CSRF protection on state-changing
   requests and `Cache-Control: no-store` on session-bearing responses.
5. Store only a session hash server-side. Do not expose provider refresh
   tokens to the dashboard or persist credentials in browser storage.
6. Logout revokes the Paiziq session, clears site data as appropriate, and
   initiates provider logout when supported.
7. Require recent reauthentication or provider step-up/MFA for identity
   linking, account recovery, ownership transfer, security settings,
   high-risk billing changes, refunds, and elevated internal operations.

All dashboard routes except the documented public surface require an
authenticated session and active organization membership. Production has
no demo-session bypass. Test identities are accepted only by a test
configuration that production startup validation rejects.

### 10.3 Commercial data model

```text
organization
  └─ billing_account
       ├─ subscription ──► plan_version ──► plan_entitlements ──► feature
       │       ├─ subscription_transitions (append-only)
       │       ├─ scheduled_plan_changes
       │       └─ entitlement_grants (approved override/temporary access)
       ├─ checkout_sessions
       ├─ billing_invoices ──► billing_payments ──► billing_refunds
       ├─ billing_disputes / promotions / discounts
       └─ usage_events (append-only) ──► usage_counters

billing provider
  └─ provider_events ──► lifecycle reducer ──► subscription + outbox_events
```

Planned catalog records:

- `features`: stable key, boolean/value type, unit, reset rule, limit
  behavior, and product status.
- `plans` and immutable `plan_versions`: stable plan code plus a dated
  commercial/entitlement version.
- `plan_prices`: interval, currency, amount, environment-specific provider
  product/price reference, and effective dates.
- `plan_entitlements`: enabled flag or numeric/text value, reset policy,
  overage behavior, and rollover rule.

Planned lifecycle and finance records:

- `subscriptions`: billing account, plan/version, internal lifecycle
  status, interval, period/trial/access timestamps, scheduled cancellation,
  provider reference, and monotonic entitlement revision.
- `subscription_transitions`: append-only before/after status, reason,
  actor/event, and timestamp.
- `checkout_sessions`: unique internal checkout reference, immutable plan
  selection, idempotency key, provider reference, status, and expiry.
- `billing_invoices`, `billing_payments`, `billing_refunds`, and
  `billing_disputes`: provider references and normalized status/amount
  facts. Paiziq never stores PAN, CVC, or raw payment credentials.
- `provider_events`: unique `(provider, event_id)`, verified raw payload or
  encrypted archival reference, object/type timestamps, processing state
  (`received`, `processing`, `completed`, `failed`, `retrying`,
  `dead_letter`), attempts, error, and correlation IDs.
- `outbox_events`: transactionally committed customer notification,
  entitlement invalidation, audit, and operational work.
- `entitlement_grants`: plan, approved custom, or temporary grant source,
  exact value, start/end, reason, approving operator, and revision.
- `usage_events`: append-only, unique usage idempotency key, organization,
  subscription period, meter key, quantity, resource, and event time.
  `usage_counters` are rebuildable aggregates, not the source of truth.

Provider price IDs and webhook secrets are deployment configuration, not
catalog keys. Test and live provider objects can never share an internal
environment mapping.

### 10.4 Access decision and usage flow

```text
Request
  ↓
Authenticate user session or machine API key
  ↓
Resolve organization/environment and active membership/key binding
  ↓
Check role/scope permission
  ↓
Load internal subscription and entitlement revision
  ↓
Check feature entitlement
  ↓
Atomically reserve/check numerical usage when required
  ↓
Perform action
  ↓
Commit usage event + domain change + outbox/audit evidence
```

Permission denial, missing entitlement, and exhausted limit are distinct,
machine-readable outcomes. The dashboard may hide or disable unavailable
actions for clarity, but the server remains authoritative. Concurrent hard
limits require an atomic reservation or database constraint; a stale cache
must not permit extra members, environments, keys, agents, or billable
operations.

Entitlement snapshots may be cached by `(organization_id,
entitlement_revision)`. Subscription transitions, scheduled changes,
temporary grants, and plan-version migrations increment the revision and
invalidate caches. Access is evaluated against the request time and
internal `access_until`, never the browser clock or checkout redirect.

### 10.5 Checkout and provider-event processing

Checkout is account-first and hosted by the selected payment provider to
minimize PCI scope:

```text
Authenticated organization owner/billing admin selects plan + interval
  ↓
Backend validates eligibility and stores one checkout reference
  ↓
Backend creates/reuses provider checkout with an idempotency key
  ↓
Customer completes hosted payment
  ↓
Return page shows pending and reads internal subscription status
  ↓
Verified provider event enters durable event ledger
  ↓
Worker applies internal lifecycle transition and entitlement revision
  ↓
Outbox sends confirmation/receipt and opens onboarding
```

The event ingress verifies the provider signature against the raw body,
rejects unverified events, stores verified events durably, acknowledges
quickly, and processes asynchronously. Duplicate event IDs return success
without repeating effects. Out-of-order events compare provider event and
object timestamps and, when ambiguous, fetch the provider's current object
before applying a transition.

The subscription transition, invoice/payment normalization, usage reset,
entitlement revision, audit evidence, and outbox enqueue share one local
transaction. Temporary failures retry with bounded backoff; repeated
failures enter an operator-visible exception queue with replay protected by
permissions, required reason, and idempotency. Checkout and all outbound
provider mutations use stable internal idempotency keys.

Nightly reconciliation compares provider customers, subscriptions,
invoices, payments, refunds, disputes, fees, and settlements with internal
records. Exceptions include payment without subscription, active access
without verified payment/trial, duplicates, missing refunds, settlement
mismatch, and unknown references.

### 10.6 Standards and implementation guardrails

- OAuth/OIDC security follows
  [RFC 9700](https://datatracker.ietf.org/doc/html/rfc9700), including
  Authorization Code + PKCE S256 and issuer/redirect protections.
- User identity uses the OIDC
  [`iss` + `sub` stable identifier](https://openid.net/specs/openid-connect-core-1_0-35.html#ClaimStability).
- Browser sessions follow the
  [OWASP Session Management guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html);
  credentials are not stored in Web Storage.
- The reference billing adapter may use Stripe Billing, but the provider
  remains behind an injected protocol. Event handling follows the
  provider's signature, duplicate-event, asynchronous-processing, and
  idempotent-request guidance.
- No proposed route or schema becomes part of `06_API_CONTRACT.md` until
  its exact request, response, authorization, tenancy, error, idempotency,
  and lifecycle semantics are implemented and tested.
